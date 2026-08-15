# -*- coding: utf-8 -*-
"""数据聚合回归测试（正确逻辑：去重 + customsCode=C00(总计) + motCode=0(全部运输)）

背景：UN Comtrade 数据按 customsCode(C00=总计=C03+C04+…) 和 motCode(0=全部运输)
拆分多条且有成对重复。正确总额 = C00 且 mot=0 的唯一记录。
此前"sum 所有行"(55亿) 和 "mot=0 优先"(27.5亿) 均错，真实值 6.88 亿（实测验证）。
"""
from unittest import mock

import trade
from trade import fetch_year


def _mock_resp(data_rows):
    m = mock.MagicMock()
    m.status_code = 200
    m.json.return_value = {"data": data_rows}
    return m


def _mk(value, mot=0, customs="C00", reporter=276, partner=156):
    return {"reporterCode": reporter, "partnerCode": partner, "cmdCode": "8525",
            "period": "2024", "motCode": mot, "mosCode": 0, "customsCode": customs,
            "primaryValue": value, "refYear": 2024, "netWgt": 1000.0}


class TestFetchYearAggregation:
    def test_dedup_repeated_rows(self, tmp_db):
        """同一条(C00+mot=0)重复 3 次 → 去重为 1 条"""
        rows = [_mk(1e8)] * 3
        with mock.patch.object(trade.requests, "get", return_value=_mock_resp(rows)):
            out = fetch_year("8525", "156", "2024", "德国", "X")
        assert len(out) == 1
        assert sum(r["primaryValue"] for r in out) == 1e8

    def test_mot_split_takes_total(self, tmp_db):
        """C00 下 mot=0(总计5e8) + mot 拆分行(3e8+2e8) → 只取 mot=0"""
        rows = [_mk(5e8, mot=0), _mk(3e8, mot=1000), _mk(2e8, mot=2100)]
        with mock.patch.object(trade.requests, "get", return_value=_mock_resp(rows)):
            out = fetch_year("8525", "156", "2024", "德国", "X")
        assert len(out) == 1
        assert out[0]["motCode"] == 0
        assert sum(r["primaryValue"] for r in out) == 5e8

    def test_customs_split_takes_total(self, tmp_db):
        """mot=0 下 C00(6.88) + C03(6.37) + C04(0.51) → 只取 C00（不重复计子项）"""
        rows = [_mk(6.88e8, customs="C00"), _mk(6.37e8, customs="C03"), _mk(0.51e8, customs="C04")]
        with mock.patch.object(trade.requests, "get", return_value=_mock_resp(rows)):
            out = fetch_year("8525", "156", "2024", "德国", "X")
        assert len(out) == 1
        assert out[0]["customsCode"] == "C00"
        assert abs(sum(r["primaryValue"] for r in out) - 6.88e8) < 0.01e8

    def test_real_dirty_case_germany(self, tmp_db):
        """真实德国案例：22 条成对重复 + mot/customs 拆分 → 正确聚合 6.88 亿"""
        combos = [
            ("C00", 0, 6.8838e8), ("C00", 1000, 2.6868e8), ("C00", 2100, 4.1964e8),
            ("C03", 0, 6.3713e8), ("C03", 1000, 2.6644e8), ("C03", 2100, 3.7061e8),
            ("C04", 0, 0.5126e8), ("C04", 1000, 0.0223e8), ("C04", 2100, 0.4902e8),
        ]
        rows = []
        for customs, mot, v in combos:
            rows.append(_mk(v, mot=mot, customs=customs))
            rows.append(_mk(v, mot=mot, customs=customs))  # 成对重复
        with mock.patch.object(trade.requests, "get", return_value=_mock_resp(rows)):
            out = fetch_year("8525", "156", "2024", "德国", "X")
        total = sum(r["primaryValue"] for r in out)
        assert abs(total / 1e8 - 6.88) < 0.05, f"德国正确聚合应 6.88 亿，实际 {total/1e8}"

    def test_no_c00_fallback_to_unique(self, tmp_db):
        """无 C00 行时兜底取去重后的全部（不崩溃、不重复）"""
        rows = [_mk(6.37e8, customs="C03"), _mk(0.51e8, customs="C04"), _mk(6.37e8, customs="C03")]
        with mock.patch.object(trade.requests, "get", return_value=_mock_resp(rows)):
            out = fetch_year("8525", "156", "2024", "德国", "X")
        assert len(out) == 2

    def test_clean_single_untouched(self, tmp_db):
        """干净单条（C00+mot=0）原样保留"""
        rows = [_mk(5.94e8)]
        with mock.patch.object(trade.requests, "get", return_value=_mock_resp(rows)):
            out = fetch_year("8525", "156", "2024", "德国", "X")
        assert len(out) == 1
        assert sum(r["primaryValue"] for r in out) == 5.94e8

    def test_share_math_consistency(self, tmp_db):
        """份额数学自洽：出口 6.88 亿 / 总进口 22.11 亿 = 31.1% < 100%"""
        exp = [_mk(6.88e8)] * 4  # 脏数据重复
        imp = [_mk(22.11e8)]
        with mock.patch.object(trade.requests, "get",
                               side_effect=[_mock_resp(exp), _mock_resp(imp)]):
            export_rows = fetch_year("8525", "156", "2024", "德国", "X")
            import_rows = fetch_year("8525", "0", "2024", "中国", "M")
        export_v = sum(r["primaryValue"] for r in export_rows)
        import_v = sum(r["primaryValue"] for r in import_rows)
        share = export_v / import_v * 100
        assert 30 < share < 32, f"德国份额应约 31%，实际 {share:.1f}%"


class TestDataGateLineage:
    """数据血缘 + DataGate 四态质量（v1.0.2 数据层收口）

    建议框架：case 01 正常 C00+MOT0 / case 02 重复 C00 / case 03 只有分项无 C00 /
    case 04 500 条截断 / case 05 200+error body / case 06 成对数据异常 /
    case 07 X/M 口径差异 / case 08 空数据 —— 逐案验证清洗逻辑与质量标记。
    """
    # 缓存键含数据源模式（fetch_year 内部 _use_formal 判定；测试环境 .env 有 key → formal）
    CACHE_KEY = "formal" if trade._use_formal() else "preview"

    def _fetch(self, rows, tmp_db, expect_error=False):
        with mock.patch.object(trade.requests, "get", return_value=_mock_resp(rows)):
            if expect_error:
                import pytest
                with pytest.raises(ValueError):
                    fetch_year("8525", "156", "2024", "德国", "X")
                return None
            return fetch_year("8525", "156", "2024", "德国", "X")

    def _meta(self):
        from database import get_cache_meta
        return get_cache_meta("8525", "156", "2024", "X", "276", cache_key=self.CACHE_KEY)

    def test_case01_clean_c00_mot0(self, tmp_db):
        """case 01：正常 C00+MOT0 → valid，血缘记录 raw=1 clean=1"""
        self._fetch([_mk(6.88e8)], tmp_db)
        meta = self._meta()
        assert meta["quality"] == "valid"
        assert meta["raw_record_count"] == 1
        assert meta["clean_record_count"] == 1
        assert meta["source"].startswith("uncomtrade/")

    def test_case02_duplicate_c00(self, tmp_db):
        """case 02：重复 C00（成对行）→ 去重后 valid，raw=2 clean=1"""
        self._fetch([_mk(6.88e8), _mk(6.88e8)], tmp_db)
        meta = self._meta()
        assert meta["quality"] == "valid"
        assert meta["raw_record_count"] == 2
        assert meta["clean_record_count"] == 1

    def test_case03_no_c00_only_detail(self, tmp_db):
        """case 03：只有分项无 C00 → 兜底取 C00 组→无，取 mot=0 行（不求和翻倍）"""
        rows = [_mk(6.37e8, customs="C03"), _mk(0.51e8, customs="C04")]
        out = self._fetch(rows, tmp_db)
        # 兜底链：无 C00+mot=0 → 无 C00 → 取 mot=0 行（C03/C04 都是 mot=0）
        assert len(out) == 2
        total = sum(r["primaryValue"] for r in out)
        assert abs(total / 1e8 - 6.88) < 0.05, "分项求和≈总额是可接受的兜底，但不得翻倍"

    def test_case04_500_truncated(self, tmp_db):
        """case 04：500 条截断 → REJECTED 持久化 + 报错拒绝（宁缺勿错）"""
        rows = [_mk(1e8) for _ in range(500)]
        self._fetch(rows, tmp_db, expect_error=True)
        meta = self._meta()
        assert meta["quality"] == "rejected"
        assert "上限" in meta["validation_reason"]

    def test_case05_200_error_body(self, tmp_db):
        """case 05：HTTP 200 + 错误响应体（无 data 键）→ 拒绝且不污染缓存"""
        m = mock.MagicMock()
        m.status_code = 200
        m.json.return_value = {"error_response": {"code": "xxx"}}
        with mock.patch.object(trade.requests, "get", return_value=m):
            import pytest
            with pytest.raises(ValueError):
                fetch_year("8525", "156", "2024", "德国", "X")
        assert self._meta() is None, "错误体不得写入缓存"

    def test_case06_paired_anomaly(self, tmp_db):
        """case 06：成对数据异常（同 key 重复值不同）→ 去重取首条，不求和"""
        rows = [_mk(6.88e8), _mk(6.88e8), _mk(3.0e8)]  # 第 3 行同 key 但值不同（异常重复）
        out = self._fetch(rows, tmp_db)
        # 同 key（C00+mot=0+同报告国）重复 → 去重只留 1 条；异常值 3.0e8 不得参与求和
        assert len(out) == 1, "同 key 重复去重为 1 条"
        assert abs(out[0]["primaryValue"] - 6.88e8) < 0.01e8, "异常重复行不得污染结果"

    def test_case07_xm_mirror_difference(self, tmp_db):
        """case 07：X/M 镜像口径差异 → get_competitiveness 标 suspicious 不替换"""
        # 出口 6.88 亿（X 流）vs 市场总进口 1.74 亿（M 流）→ 份额 >100% → suspicious
        exp = [_mk(6.88e8)]
        imp = [_mk(1.74e8)]
        with mock.patch.object(trade.requests, "get",
                               side_effect=[_mock_resp(exp), _mock_resp(imp), _mock_resp(imp)]):
            cmp = trade.get_competitiveness("摄像头", "德国", "2024")
        assert cmp.get("quality") == "suspicious", f"镜像差异应标 suspicious，实际 {cmp.get('quality')}"
        assert "镜像" in (cmp.get("quality_note") or "")

    def test_case08_empty_data(self, tmp_db):
        """case 08：空数据 → 合法空结果写缓存（valid，raw=0 clean=0），不报错"""
        out = self._fetch([], tmp_db)
        assert out == []
        meta = self._meta()
        assert meta is not None
        assert meta["quality"] == "valid"

    def test_datagate_blocks_rejected(self, tmp_db):
        """DataGate：rejected 记录 → allowed=False，调用方不得使用数字"""
        self._fetch([_mk(1e8) for _ in range(500)], tmp_db, expect_error=True)
        gate = trade.check_data_gate("8525", "156", "2024", "X", "276", cache_key=self.CACHE_KEY)
        assert gate["allowed"] is False
        assert gate["quality"] == "rejected"
        # 前端友好版
        report = trade.data_gate_report("8525", "156", "2024", "X", "276", cache_key=self.CACHE_KEY)
        assert report["usable"] is False
        assert "无法用于本次分析" in report["label"]

    def test_datagate_unknown_not_empty(self, tmp_db):
        """DataGate：无缓存记录 → allowed=False + unknown（不能当"无数据"）"""
        gate = trade.check_data_gate("8525", "999", "2024", "X", "276")
        assert gate["allowed"] is False
        assert gate["quality"] == "unknown"


class TestLatestYearBreaker:
    """回归（v1.0.3 收尾）：get_latest_year 429 熔断——
    模块级 _latest_probe_fail_ts 被函数内赋值遮蔽导致 UnboundLocalError"""

    def _patch(self, status_code=404):
        m = mock.MagicMock()
        m.status_code = status_code
        m.json.return_value = {"count": 0}
        return m

    def test_no_unbound_local_error(self, monkeypatch):
        """第一次调用（缓存未命中）不抛 UnboundLocalError，探测失败后写熔断"""
        import trade as trade_mod
        monkeypatch.setattr(trade_mod, "_latest_probe_fail_ts", 0.0)
        monkeypatch.setattr(trade_mod, "_read_latest_year_cache", lambda: None)
        with mock.patch.object(trade_mod.requests, "get", return_value=self._patch(404)):
            y = trade_mod.get_latest_year()
        assert isinstance(y, int)
        assert trade_mod._latest_probe_fail_ts > 0  # 熔断已写

    def test_breaker_skips_probe(self, monkeypatch):
        """熔断生效期内不再发 API（10 分钟内直接返回 fallback）"""
        import trade as trade_mod
        monkeypatch.setattr(trade_mod, "_latest_probe_fail_ts", __import__("time").time())
        monkeypatch.setattr(trade_mod, "_read_latest_year_cache", lambda: None)
        with mock.patch.object(trade_mod.requests, "get",
                               side_effect=AssertionError("熔断期内不应打 API")):
            y = trade_mod.get_latest_year()
        assert y == __import__("datetime").date.today().year - 6
