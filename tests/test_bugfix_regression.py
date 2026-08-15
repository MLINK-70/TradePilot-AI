# -*- coding: utf-8 -*-
"""代码审查修复回归测试（2026-06 审查批次）：

1. leads._normalize_url 端口越界不再崩溃（原为确定性 500）
2. 防幻觉硬约束：非法 URL 归一化为空后不得进入 seen 集合（绕过点）
3. agent 流水线 step_results 每步仅一条最终状态（原双份记录）
4. agent 提前返回的 result 事件结构统一（含 steps 键）
5. agent 正则兜底按关键词识别任务类型（原一律 full）
6. business.simulate_customer 用户输入含 { } 不再崩溃（原 .format 抛异常）
7. ecommerce 评论解读失败降级：程序统计不丢失 + praise 维度不再硬编码"其他"
8. llm.analyze_trade_trend 缓存返回副本（原返回原对象可被调用方污染）
9. main 请求模型输入长度/年份边界校验
"""
import pytest
from unittest import mock

import agent
import business
import ecommerce
import leads
import llm
from llm import _parse_json


# ── 1. leads._normalize_url 健壮性 ──────────────────────────────────────

class TestNormalizeUrlRobust:
    def test_port_out_of_range_no_crash(self):
        """回归：u.port 访问端口越界（:99999）抛 ValueError → 接口 500"""
        assert leads._normalize_url("https://example.com:99999/x") == ""

    def test_javascript_scheme_rejected(self):
        assert leads._normalize_url("javascript:alert(1)") == ""

    def test_normal_url_normalized(self):
        assert leads._normalize_url("https://www.Example.com/path/?q=1") == "https://example.com/path"

    def test_empty_returns_empty(self):
        assert leads._normalize_url("") == ""
        assert leads._normalize_url(None) == ""


class TestLeadsAntiHallucination:
    def test_invalid_url_excluded_from_seen(self):
        """回归：非法 URL 归一化为 '' 后不得进入 seen（否则 '' 可绕过来源校验）"""
        raw = [{"url": "javascript:alert(1)", "title": "x", "content": "y"},
               {"url": "https://good.com/a", "title": "g", "content": "h"}]
        seen = {u for u in (leads._normalize_url(r.get("url", "")) for r in raw if r.get("url")) if u}
        assert "" not in seen
        assert "https://good.com/a" in seen


# ── 2. agent 流水线步骤记录 ─────────────────────────────────────────────

class TestAgentSteps:
    def _run_empty_input(self):
        """parse_intent 走正则兜底且输入为空 → 步骤 0 skipped + 提前返回"""
        with mock.patch.object(agent, "_chat", side_effect=ValueError("no key")):
            events = list(agent.run_agent_pipeline(""))
        return events

    def test_step_results_single_record_per_step(self):
        """回归：emit 曾 append 双份（running + skipped），现每步仅一条"""
        events = self._run_empty_input()
        result = events[-1]
        assert result["type"] == "result"
        steps = [s for s in result["steps"] if s is not None]
        assert len(steps) == 1, f"应只有 1 条步骤记录，实际 {len(steps)}"
        assert steps[0]["status"] == "skipped"

    def test_early_result_includes_steps(self):
        """回归：提前返回的 result 事件缺 steps 键，前端读 undefined"""
        events = self._run_empty_input()
        result = events[-1]
        assert "steps" in result
        assert result["summary"] == "未能识别产品，请补充产品与目标市场"

    def test_regex_fallback_task_detection(self):
        """回归：正则兜底曾一律 full；含"经销商"关键词应判 leads"""
        with mock.patch.object(agent, "_chat", side_effect=ValueError("no key")):
            intent = agent.parse_intent("蓝牙耳机卖到德国找经销商")
        assert intent["task"] == "leads"
        assert intent["product"] == "蓝牙耳机"
        assert intent["country"] == "德国找经销商"


# ── 3. business.simulate_customer 花括号崩溃 ───────────────────────────

class TestSimulateFormat:
    def test_braces_in_user_input_no_crash(self):
        """回归：原 .format() 遇 { } 抛 KeyError/IndexError → 500"""
        resp = {"reply": "Hello", "zh_translation": "你好", "concern": "价格", "coach": "继续"}
        with mock.patch.object(business, "_chat", return_value='{"reply": "Hello", "zh_translation": "你好", "concern": "价格", "coach": "继续"}'):
            out = business.simulate_customer("耳机{Pro}", "德国", "经销商", "你好")
        assert out == resp


# ── 4. ecommerce 降级与维度修复 ────────────────────────────────────────

class TestEcommerceDegrade:
    def _mock_parsed(self, reviews_json):
        """第一次 _chat 返回解析结果，第二次抛异常（解读失败）"""
        calls = [reviews_json, ValueError("AI down")]
        return mock.patch.object(ecommerce, "_chat",
                                 side_effect=[calls[0] if i == 0 else (_ for _ in ()).throw(calls[1])
                                              for i in range(2)])

    def test_summary_failure_keeps_program_stats(self):
        """回归：AI 解读失败曾整体 500，程序统计全丢；现降级保留统计"""
        reviews = ["音质很好但续航太短", "降噪效果惊艳"]
        reviews_json = json_dumps_ci({
            "reviews": [
                {"text": "音质很好但续航太短", "sentiment": "negative",
                 "aspect": "电池续航", "pain_point": "续航太短", "praise_point": ""},
                {"text": "降噪效果惊艳", "sentiment": "positive",
                 "aspect": "降噪", "pain_point": "", "praise_point": "降噪效果惊艳"},
            ]
        })
        # 第一次调用（解析）成功，第二次调用（解读）抛异常
        def side_effect(*a, **k):
            if side_effect.n == 0:
                side_effect.n += 1
                return reviews_json
            raise ValueError("AI down")
        side_effect.n = 0
        with mock.patch.object(ecommerce, "_chat", side_effect=side_effect):
            out = ecommerce.analyze_reviews(reviews)
        assert out["summary_failed"] is True
        assert out["sentiments"]["negative"] == 1 and out["sentiments"]["positive"] == 1
        assert out["top_pains"][0]["pain"] == "续航太短"
        assert out["top_praises"][0]["aspect"] == "降噪", "praise 维度不得硬编码'其他'"

    def test_llm_non_list_reviews_guarded(self):
        """回归：LLM 返回 reviews 非列表时兜底为空，不崩 500"""
        def side_effect(*a, **k):
            # 第 1、2 次（解析批次重试）都返回非列表；第 3 次（解读）抛异常
            side_effect.n += 1
            if side_effect.n <= 2:
                return '{"reviews": {"text": "不是列表"}}'
            raise ValueError("AI down")
        side_effect.n = 0
        with mock.patch.object(ecommerce, "_chat", side_effect=side_effect):
            out = ecommerce.analyze_reviews(["某条评论"])
        assert out["parsed_count"] == 0
        assert out["summary_failed"] is True


def json_dumps_ci(obj):
    import json
    return json.dumps(obj, ensure_ascii=False)


# ── 5. llm 缓存返回副本 ────────────────────────────────────────────────

class TestTradeTrendCacheCopy:
    def _mock_resp(self):
        resp = mock.MagicMock(status_code=200)
        resp.json.return_value = {"choices": [{"message": {"content": '{"overview": "原始"}'}}]}
        return resp

    def test_cache_returns_copy(self):
        """回归：返回缓存原对象，调用方原地修改会污染后续命中"""
        trend = {"2020": {"value": 1.0, "weight": 0.1},
                 "2021": {"value": 2.0, "weight": 0.2},
                 "2022": {"value": 3.0, "weight": 0.3}}
        resp = self._mock_resp()
        with mock.patch.object(llm._SESSION, "post", return_value=resp):
            r1 = llm.analyze_trade_trend("产品X", "德国", "中国", trend)
            r1["overview"] = "被污染"  # 模拟 main.py 挂 _data_range 的同类操作
            r2 = llm.analyze_trade_trend("产品X", "德国", "中国", trend)
            assert r2["overview"] == "原始", "缓存命中应返回副本，不受调用方修改影响"


# ── 6. main 请求模型边界校验 ───────────────────────────────────────────

class TestRequestModelBounds:
    def test_analyze_product_too_long_rejected(self):
        from main import AnalyzeRequest
        with pytest.raises(Exception):
            AnalyzeRequest(product="x" * 101, country="德国")

    def test_trade_start_year_bounds(self):
        from main import TradeQueryRequest
        with pytest.raises(Exception):
            TradeQueryRequest(product="耳机", target="德国", start_year=1)
        with pytest.raises(Exception):
            TradeQueryRequest(product="耳机", target="德国", start_year=2101)

    def test_agent_input_too_long_rejected(self):
        from main import AgentRequest
        with pytest.raises(Exception):
            AgentRequest(input="x" * 501)


# ── 7. trade.fetch_year 数据准确性（截断/错误体/总额行兜底）──────────────

class TestFetchYearDataAccuracy:
    def _mock_resp(self, payload):
        resp = mock.MagicMock(status_code=200)
        resp.json.return_value = payload
        return resp

    def _mode_key(self):
        import trade
        return "formal" if trade._use_formal() else "preview"

    def test_error_body_not_cached_as_empty(self, tmp_db):
        """回归：200 + 缺 data 键的错误响应体不得当"合法空结果"缓存"""
        import trade
        resp = self._mock_resp({"count": 0})  # 无 data 键
        with mock.patch.object(trade.requests, "get", return_value=resp):
            with pytest.raises(ValueError):
                trade.fetch_year("8518", "276", "2022")
        assert tmp_db.get_cached("8518", "276", "2022", "X", "156",
                                 cache_key=self._mode_key()) is None

    def test_truncation_detected_before_filter(self, tmp_db):
        """回归：500 条原始行（过滤后很少）也必须触发截断拒绝"""
        import trade
        raw = []
        for i in range(500):
            raw.append({"reporterCode": "156", "partnerCode": "276", "cmdCode": "8518",
                        "period": "2022", "motCode": "3", "mosCode": "1",
                        "customsCode": "C03", "primaryValue": i})
        resp = self._mock_resp({"count": 500, "data": raw})
        with mock.patch.object(trade.requests, "get", return_value=resp):
            with pytest.raises(ValueError) as ctx:
                trade.fetch_year("8518", "276", "2022")
            assert "达到记录上限" in str(ctx.value)

    def test_no_total_row_rejected_not_doubled(self, tmp_db):
        """回归：无 C00/mot=0 总额行时不得用明细行兜底（求和会翻倍）"""
        import trade
        raw = [{"reporterCode": "156", "partnerCode": "276", "cmdCode": "8518",
                "period": "2022", "motCode": "3", "mosCode": "1",
                "customsCode": "C03", "primaryValue": 100.0}]
        resp = self._mock_resp({"count": 1, "data": raw})
        with mock.patch.object(trade.requests, "get", return_value=resp):
            with pytest.raises(ValueError) as ctx:
                trade.fetch_year("8518", "276", "2022")
            assert "总额行" in str(ctx.value)


# ── 8. ebay / collectors 回归 ─────────────────────────────────────────

class TestEbayFixes:
    def test_parse_9_digit_product_id(self):
        """回归：eBay /p/ 产品页 9 位 ID 原匹配不了"""
        import ebay
        assert ebay.parse_ebay_url("https://www.ebay.com/p/123456789") == "123456789"

    def test_token_cached(self):
        """回归：token 应缓存复用（原每次请求都重新换取）"""
        import ebay
        ebay._token_cache = {"token": "", "expires_at": 0.0}
        resp = mock.MagicMock(status_code=200)
        resp.json.return_value = {"access_token": "tok-1", "expires_in": 7200}
        with mock.patch.object(ebay.requests, "post", return_value=resp) as m:
            assert ebay.get_oauth_token("a", "s") == "tok-1"
            assert ebay.get_oauth_token("a", "s") == "tok-1"  # 命中缓存
            assert m.call_count == 1

    def test_fetch_item_404_friendly(self):
        import ebay
        resp = mock.MagicMock(status_code=404)
        with mock.patch.object(ebay.requests, "get", return_value=resp):
            with pytest.raises(ValueError) as ctx:
                ebay.fetch_item("123", "tok")
            assert "已下架" in str(ctx.value)


class TestCollectorsFixes:
    def test_forbidden_ip_multicast_cgnat(self):
        """回归：组播/CGNAT 地址此前漏网"""
        import collectors
        import ipaddress
        assert collectors._is_forbidden_ip(ipaddress.ip_address("224.0.0.1")) is True
        assert collectors._is_forbidden_ip(ipaddress.ip_address("100.64.0.1")) is True
        assert collectors._is_forbidden_ip(ipaddress.ip_address("8.8.8.8")) is False

    def test_jsonld_type_list_and_graph(self):
        """回归：@type 列表与 @graph 嵌套此前不识别"""
        import collectors
        html = ('<script type="application/ld+json">{"@graph": [{"@type": ["Product", "Thing"], '
                '"name": "X"}]}</script>')
        products = collectors._json_ld_products(html)
        assert len(products) == 1 and products[0]["name"] == "X"


# ── 9. 第二轮审查修复（markdown 渲染 / 脏数据 / 残缺缓存 / desktop）──────

class TestMarkdownReportGuards:
    def test_non_list_fields_no_crash(self):
        """回归：LLM 把数组字段返回成 int/dict/字符串时渲染不得 500"""
        from main import markdown_report
        d = {
            "executive_summary": {"data_points": 5, "key_findings": "串", "challenges": {}},
            "market_size": {"value": "1亿", "year": 2026},
            "growth_trend": {"key_drivers": 3},
            "top_brands": 7,
            "user_profile": {"key_needs": None, "buying_habits": "x"},
            "risks": 9,
            "action_plan": 1,
            "summary": "ok",
        }
        md = markdown_report("耳机", "德国", d)
        assert "## 市场规模" in md  # 正常渲染其余部分


class TestSummarizeTrendDirtyData:
    def test_dirty_rows_skipped_not_crash(self):
        """回归：primaryValue 为 "N/A" 的脏行跳过，不再整体 502"""
        import trade
        rows = [{"refYear": 2022, "primaryValue": "N/A", "netWgt": 1.0},
                {"refYear": 2022, "primaryValue": 100.0, "netWgt": 2.0}]
        trend = trade.summarize_trend(rows)
        assert trend[2022]["value"] == 100.0


class TestTopExportersNoPartialCache:
    def test_partial_failure_not_cached(self, tmp_db):
        """回归：TOP 出口国轮询有失败国时不得写缓存（残缺排名会留错误数周）"""
        import trade
        hs = trade.hs_lookup("蓝牙耳机") or "8518"
        year = "2022"

        # 4 国失败（< 半数 8，不触发整体降级）、其余成功
        FAIL = {"中国", "德国", "日本", "韩国"}

        def fake_fetch_year(cmd, partner, period, reporter="中国", flow="X"):
            if reporter in FAIL:
                raise ValueError("网络失败")
            return [{"primaryValue": 100.0}]

        with mock.patch.object(trade, "fetch_year", side_effect=fake_fetch_year):
            top = trade.get_top_exporters("蓝牙耳机", year)
        # 有失败 → 不写缓存
        cached = tmp_db.get_cached("TOPEXP", hs, year, "X", "0",
                                   cache_key="V1|rank", ttl_days=0)
        assert cached is None
        # 成功国仍在结果中
        assert any(t["country"] not in FAIL for t in top)


class TestRejectedCacheNotReturned:
    """回归 P0-1：REJECTED 缓存不得被读取侧当"合法空结果"返回（拒绝→假 0）"""

    def test_rejected_cache_triggers_refetch(self, tmp_db):
        import trade
        import database
        mode_key = "formal" if trade._use_formal() else "preview"
        # 预置 REJECTED 缓存（截断/C00 缺失拒绝的落库形态：data_json=[]）
        database.save_cache("8518", "276", "2022", "X", [], "156",
                            cache_key=mode_key, source="uncomtrade/" + mode_key,
                            quality="rejected", validation_reason="测试拒绝")

        def boom(*a, **k):
            raise ValueError("REJECTED 缓存被当空结果返回（未重新请求）")

        # 若 rejected 被当合法空返回 → 不发请求 → fetch_year 返回 []（断言失败）；
        # 正确行为是重新请求（mock 抛错 → ValueError 冒泡）
        with mock.patch.object(trade.requests, "get", side_effect=boom):
            with pytest.raises(ValueError):
                trade.fetch_year("8518", "276", "2022")

    def test_valid_empty_cache_still_served(self, tmp_db):
        """对照：合法空结果（valid）仍应命中缓存，不重复请求"""
        import trade
        import database
        mode_key = "formal" if trade._use_formal() else "preview"
        database.save_cache("8518", "276", "2022", "X", [], "156",
                            cache_key=mode_key, source="uncomtrade/" + mode_key,
                            quality="valid", validation_reason="合法空结果")

        def boom(*a, **k):
            raise AssertionError("合法空缓存应命中，不应发请求")

        with mock.patch.object(trade.requests, "get", side_effect=boom):
            assert trade.fetch_year("8518", "276", "2022") == []
    def test_no_free_port_returns_none(self):
        """回归：socket 分配失败时返回 None（原静默回退到必失败的 8000）"""
        import desktop

        class _AlwaysBusy:
            def __enter__(self):
                raise OSError("address in use")
            def __exit__(self, *a):
                return False

        with mock.patch.object(desktop.socket, "socket", return_value=_AlwaysBusy()):
            assert desktop.find_free_port() is None

    def test_os_allocated_port_returned(self):
        """回归：bind(0) 由 OS 分配端口并读回（无 TOCTOU、无范围限制）"""
        import desktop

        class _Sock:
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False
            def bind(self, addr):
                pass
            def getsockname(self):
                return ("127.0.0.1", 8123)

        with mock.patch.object(desktop.socket, "socket", return_value=_Sock()):
            assert desktop.find_free_port() == 8123


# ── 10. v1.0.3 新功能回归（定价/漏斗/订阅）──────────────────────────────

class TestPricingRobust:
    def test_dirty_value_rows_skipped(self):
        """回归：primaryValue 为脏数据（'N/A'）时单价仍可算（原 TypeError 被吞成失败）"""
        from pricing import _unit_price
        rows = [{"primaryValue": "N/A", "netWgt": 10.0},
                {"primaryValue": 100.0, "netWgt": 10.0}]
        assert _unit_price(rows) == 5.0  # 100 / 20

    def test_suggest_range_never_inverted(self):
        """回归：出口单价异常高于市场均价时区间不得反转（150–1.3 之类荒谬输出）"""
        import pricing
        import trade
        import database
        with mock.patch.object(trade, "fetch_year",
                               side_effect=[[{"primaryValue": 10000.0, "netWgt": 1.0}],
                                            [{"primaryValue": 1.0, "netWgt": 1.0}]]), \
             mock.patch.object(trade, "hs_lookup", return_value="8518"), \
             mock.patch.object(trade, "partner_lookup", return_value="276"), \
             mock.patch.object(trade, "get_latest_year", return_value=2022), \
             mock.patch.object(database, "get_cache_meta", return_value=None):
            r = pricing.suggest_pricing("蓝牙耳机", "德国", "2022")
        assert r["available"] is True
        assert r["suggest_low"] <= r["suggest_high"], "区间反转即 bug"

    def test_missing_leg_is_graceful_degrade(self):
        """回归：单腿缺失（市场均价无净重）是正常降级而非失败"""
        import pricing
        import trade
        import database
        with mock.patch.object(trade, "fetch_year",
                               side_effect=[[{"primaryValue": 100.0, "netWgt": 1.0}],
                                            []]), \
             mock.patch.object(trade, "hs_lookup", return_value="8518"), \
             mock.patch.object(trade, "partner_lookup", return_value="276"), \
             mock.patch.object(trade, "get_latest_year", return_value=2022), \
             mock.patch.object(database, "get_cache_meta", return_value=None):
            r = pricing.suggest_pricing("蓝牙耳机", "德国", "2022")
        assert r["available"] is True
        assert r["export_unit_price"] == 100.0
        assert r["market_unit_price"] is None
        assert "—" in r["explain"]  # 单腿缺失文案用 — 而非崩溃
