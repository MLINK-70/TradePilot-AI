# -*- coding: utf-8 -*-
"""2026-08-31 查 bug 批次回归测试：

1. partner2Code（原产国/目的国）进去重键 —— 原键不含它，世界进口查询的
   106 个原产国行会折叠成 1 行（德国 HS8518 实测 200 万 vs 真实 38.73 亿），
   且 API 侧 partner2Code 拆行撞 500 上限导致 TC/份额/定价三功能失效
2. REJECTED 血缘键名 —— get_cache_meta 返回 validation_reason，
   trade.py 日志与 pricing.py 分支原按 reason 取，前者恒空、后者 KeyError
3. IPv4-mapped IPv6 绕过内网检查 —— config._is_local 与
   collectors._is_forbidden_ip 的 IPv6 分支漏查 is_private，
   ::ffff:192.168.1.1 被当公网放行（SSRF 校验绕过）
4. 竞争对手份额虚高防护 —— 任一国家数据失败时分母不完整，
   原实现把缺失份额等比摊给剩余国家（英国剔除后中国 34%→52%）
5. 竞争格局份额防幻觉按口径分组 —— 原实现不同 share_scope 一把相加，
   与提示词"口径不同不得并列计算"矛盾
"""
from unittest import mock

import pytest

import config
import collectors
import database
import market_data
import pricing
import trade
from trade import fetch_year


def _mock_resp(data_rows):
    m = mock.MagicMock()
    m.status_code = 200
    m.json.return_value = {"data": data_rows}
    return m


def _mk(value, mot=0, customs="C00", partner2=0, partner=0):
    """构造 UN Comtrade 明细行（默认模拟世界进口：partner=0，按 partner2 拆原产国）"""
    return {"reporterCode": 276, "partnerCode": partner, "cmdCode": "8518",
            "period": "2024", "motCode": mot, "mosCode": 0, "customsCode": customs,
            "partner2Code": partner2, "primaryValue": value, "refYear": 2024,
            "netWgt": 1000.0}


# ── 1. partner2Code 进去重键 ────────────────────────────────────────────

class TestPartner2CodeDedup:
    def test_partner2_rows_not_collapsed(self, tmp_db):
        """世界进口查询按原产国（partner2）拆行：C00+mot=0 的行各自代表一个
        原产国的进口额，去重键不含 partner2Code 会把它们折叠成 1 行——
        实测德国 2024 进口 HS8518 取到 200 万美元（真实 38.73 亿）"""
        rows = [
            _mk(3.872967442e9, partner2=0),    # 世界合计行
            _mk(5.93859431e8, partner2=156),   # 原产国：中国
            _mk(7.3141362e7, partner2=842),    # 原产国：美国
            _mk(5.93859431e8, partner2=156),   # 中国重复行（应被去重）
        ]
        with mock.patch.object(trade.requests, "get", return_value=_mock_resp(rows)):
            out = fetch_year("8518", "0", "2024", "德国", "M")
        # 修复后：三行各代表一个 partner2，全部保留；中国的重复行被去掉
        assert len(out) == 3
        total = sum(r["primaryValue"] for r in out)
        expected = 3.872967442e9 + 5.93859431e8 + 7.3141362e7
        assert abs(total - expected) / expected < 1e-9

    def test_api_params_carry_partner2_zero(self, tmp_db):
        """API 请求参数必须带 partner2Code=0：否则世界进口查询被拆成
        500+ 行撞上限被拒，TC/份额/定价三功能对德国类市场全灭"""
        captured = {}

        def fake_get(url, params=None, **kw):
            captured.update(params or {})
            return _mock_resp([])

        with mock.patch.object(trade.requests, "get", side_effect=fake_get):
            # 空数据 = 合法空结果（不抛错）；目的只是捕获请求参数
            fetch_year("8518", "276", "2024", "中国", "X")
        assert captured.get("partner2Code") == 0


# ── 2. REJECTED 血缘键名修正 ────────────────────────────────────────────

class TestRejectedMetaKey:
    def test_get_cache_meta_has_validation_reason(self, tmp_db):
        """get_cache_meta 返回 validation_reason（契约锁定：下游按此键取）"""
        database.save_cache("8518", "0", "2024", "M", [], "276", cache_key="formal",
                            quality="rejected",
                            validation_reason="原始数据达到 500 条记录上限")
        meta = database.get_cache_meta("8518", "0", "2024", "M", "276", cache_key="formal")
        assert meta is not None
        assert meta["quality"] == "rejected"
        assert "validation_reason" in meta
        assert meta["validation_reason"] == "原始数据达到 500 条记录上限"

    def test_pricing_rejected_reason_surfaces(self, tmp_db, monkeypatch):
        """定价腿被拒时必须透出具体原因（原 meta['reason'] KeyError 被吞成
        通用文案'定价数据获取失败'，G10 的原因透出失效）"""
        mode = "formal" if trade._use_formal() else "preview"
        # 预置市场进口腿 REJECTED 血缘
        database.save_cache("8518", "0", "2024", "M", [], "276", cache_key=mode,
                            quality="rejected", validation_reason="原始数据达到 500 条上限（测试）")
        monkeypatch.setattr(trade, "hs_lookup", lambda p: "8518")
        monkeypatch.setattr(trade, "partner_lookup", lambda m: "276")
        monkeypatch.setattr(trade, "fetch_year", lambda *a, **kw: [_mk(1e8)])
        res = pricing.suggest_pricing("蓝牙耳机", "德国", year="2024")
        assert res["available"] is False
        assert "500 条上限（测试）" in res["reason"], "具体拒绝原因必须透出，不得降级为通用文案"
        assert res["reason"] != "定价数据获取失败"


# ── 3. IPv4-mapped IPv6 不再绕过内网检查 ────────────────────────────────

class TestIpv4MappedLocalCheck:
    def test_collectors_forbids_mapped_private(self):
        """collectors：::ffff:192.168.1.1 是内网 IPv4 的 mapped 形式，必须拒绝"""
        assert collectors._is_forbidden_ip(__import__("ipaddress").ip_address("::ffff:192.168.1.1"))
        assert collectors._is_forbidden_ip(__import__("ipaddress").ip_address("::ffff:10.0.0.1"))
        # 真公网 IPv6 不得误拒（保持原口径：Teredo/文档段等放行）
        assert not collectors._is_forbidden_ip(__import__("ipaddress").ip_address("2001:4860:4860::8888"))
        # 原有拦截行为不回退
        assert collectors._is_forbidden_ip(__import__("ipaddress").ip_address("::1"))
        assert collectors._is_forbidden_ip(__import__("ipaddress").ip_address("fe80::1"))
        assert collectors._is_forbidden_ip(__import__("ipaddress").ip_address("192.168.1.1"))

    def test_config_rejects_mapped_private_base_url(self):
        """config：AI_BASE_URL 指向 mapped 内网地址必须在保存时拒绝"""
        with pytest.raises(ValueError):
            config.validate_ai_base_url("https://[::ffff:192.168.1.1]:8000")
        with pytest.raises(ValueError):
            config.validate_ai_base_url("https://[::ffff:10.0.0.1]:8000")


# ── 4. 竞争对手份额虚高防护 ─────────────────────────────────────────────

class TestCompetitorShareHonesty:
    def _run(self, monkeypatch, fail_country=None):
        monkeypatch.setattr(trade, "hs_lookup", lambda p: "8518")
        monkeypatch.setattr(trade, "partner_lookup", lambda m: "276")

        rows = [{"primaryValue": 5e8, "netWgt": None}]

        def fake_fetch_year(hs, partner, year, reporter="中国", flow="X"):
            if reporter == fail_country:
                raise ValueError("UN Comtrade 返回 500 条达到记录上限")
            return rows

        monkeypatch.setattr(trade, "fetch_year", fake_fetch_year)
        return trade.get_competitor_comparison(
            "蓝牙耳机", "德国", "2024", competitors=["中国", "美国", "英国"])

    def test_error_rows_block_share_computation(self, tmp_db, monkeypatch):
        """任一国家失败 → 所有 share 置 None（缺国时分母不完整，
        给成功国算占比等于把缺失份额摊给它们，中国 34%→52% 的虚高）"""
        res = self._run(monkeypatch, fail_country="英国")
        comps = {c["country"]: c for c in res["competitors"]}
        assert comps["英国"]["error"] is not None
        assert comps["英国"]["value"] is None
        assert comps["中国"]["share"] is None, "存在失败国时不得给成功国算虚高份额"
        assert comps["美国"]["share"] is None

    def test_all_success_shares_sum_100(self, tmp_db, monkeypatch):
        """全部成功时份额照常计算且合计 100%"""
        res = self._run(monkeypatch, fail_country=None)
        shares = [c["share"] for c in res["competitors"]]
        assert all(s is not None for s in shares)
        assert abs(sum(shares) - 100.0) < 0.3


# ── 5. 竞争格局防幻觉按口径分组 ─────────────────────────────────────────

def _landscape_result(brands):
    return {"product_category": "蓝牙耳机", "top_brands": brands,
            "segment_trends": [], "shift_reasons": [],
            "chain_insight": "", "key_insight": "", "zh_summary": ""}


class TestLandscapeScopeAwareValidation:
    def _run(self, monkeypatch, tmp_db, brands):
        # _chat/_parse_json 是函数内延迟导入（from llm import ...），必须打在 llm 模块上
        monkeypatch.setattr(market_data, "_search_web", lambda *a, **kw: [])
        monkeypatch.setattr("llm._chat", lambda *a, **kw: "{}")
        monkeypatch.setattr("llm._parse_json", lambda s: _landscape_result(brands))
        return market_data.get_competitive_landscape("蓝牙耳机", "德国")

    def test_same_scope_over_100_rejected(self, tmp_db, monkeypatch):
        """同口径份额和 >105% → 判幻觉，top_brands 置空（原行为保持）"""
        brands = [{"name": f"品牌{i}", "share": "40%", "share_scope": "2024年全球"}
                  for i in range(3)]
        res = self._run(monkeypatch, tmp_db, brands)
        assert res["top_brands"] == []

    def test_mixed_scope_under_100_each_kept(self, tmp_db, monkeypatch):
        """混口径（全球 80% + 中国 80%）总和解出 160% 但单口径均 ≤105%：
        原实现会误杀，修复后分口径校验，数据保留"""
        brands = [
            {"name": "A", "share": "80%", "share_scope": "2024年全球"},
            {"name": "B", "share": "80%", "share_scope": "2024年中国市场"},
        ]
        res = self._run(monkeypatch, tmp_db, brands)
        assert len(res["top_brands"]) == 2, "不同口径的份额不得相加后误判幻觉"
