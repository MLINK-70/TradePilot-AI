# -*- coding: utf-8 -*-
"""定价建议模块测试（v1.0.2 业务收口）：公式正确性 + 数据红线"""
from unittest import mock

import pricing
import trade


def _mk(value, wgt):
    return {"primaryValue": value, "netWgt": wgt}


class TestUnitPrice:
    def test_basic(self):
        """1000美元/100公斤 = 10美元/公斤"""
        assert abs(pricing._unit_price([_mk(1000.0, 100.0)]) - 10.0) < 1e-9

    def test_no_weight(self):
        assert pricing._unit_price([_mk(1000.0, 0)]) is None

    def test_no_value(self):
        assert pricing._unit_price([_mk(0, 100.0)]) is None

    def test_weight_ok(self):
        assert pricing._weight_ok([_mk(0, 0), _mk(0, 5)]) is True
        assert pricing._weight_ok([_mk(0, 0)]) is False


class TestSuggestPricing:
    def _patch_env(self, exp_rows, imp_rows):
        def fake_fetch(cmd, partner, period, reporter="中国", flow="X"):
            return imp_rows if flow == "M" else exp_rows
        return mock.patch.object(trade, "fetch_year", fake_fetch)

    def test_formula(self, tmp_db):
        """出口 10/kg + 市场 20/kg → 建议 15/20/26"""
        with self._patch_env([_mk(1000.0, 100.0)], [_mk(4000.0, 200.0)]):
            with mock.patch.object(trade, "get_latest_year", return_value=2024):
                with mock.patch.object(trade, "_use_formal", return_value=False):
                    with mock.patch.object(trade, "AREA_MAP", {"中国": "156", "德国": "276"}):
                        r = pricing.suggest_pricing("摄像头", "德国")
        assert r["available"] is True
        assert abs(r["export_unit_price"] - 10.0) < 1e-9
        assert abs(r["market_unit_price"] - 20.0) < 1e-9
        assert abs(r["suggest_low"] - 15.0) < 1e-9
        assert abs(r["suggest_mid"] - 20.0) < 1e-9
        assert abs(r["suggest_high"] - 26.0) < 1e-9
        assert "2024" in r["explain"]
        assert r["_audit"]["legs"]["export"]["quality"] in ("valid", "unknown")

    def test_reject_empty(self, tmp_db):
        """两腿都空 → available=False + 明确原因（宁缺勿错，不算假价格）"""
        with self._patch_env([], []):
            with mock.patch.object(trade, "get_latest_year", return_value=2024):
                with mock.patch.object(trade, "_use_formal", return_value=False):
                    with mock.patch.object(trade, "AREA_MAP", {"中国": "156", "德国": "276"}):
                        r = pricing.suggest_pricing("摄像头", "德国")
        assert r["available"] is False
        assert r.get("reason")

    def test_reject_unknown_hs(self, tmp_db):
        """无法识别 HS → 拒绝"""
        with mock.patch.object(trade, "hs_lookup", return_value=""):
            r = pricing.suggest_pricing("不存在的东西", "德国")
        assert r["available"] is False

    def test_single_leg_no_crash(self, tmp_db):
        """回归修复：只有市场价（出口无净重）时 explain 不崩（None:.2f 曾 TypeError）"""
        def fake_fetch(cmd, partner, period, reporter="中国", flow="X"):
            if flow == "M":
                return [_mk(4000.0, 200.0)]   # 市场均价 20/kg
            return [_mk(1000.0, 0)]           # 出口无净重 → export_up=None
        with mock.patch.object(trade, "fetch_year", fake_fetch):
            with mock.patch.object(trade, "get_latest_year", return_value=2024):
                with mock.patch.object(trade, "_use_formal", return_value=False):
                    with mock.patch.object(trade, "AREA_MAP", {"中国": "156", "德国": "276"}):
                        r = pricing.suggest_pricing("摄像头", "德国")
        assert r["available"] is True
        assert r["export_unit_price"] is None
        assert abs(r["market_unit_price"] - 20.0) < 1e-9
        # 单腿降级：基于市场均价 0.8x–1.2x
        assert abs(r["suggest_low"] - 16.0) < 1e-9
        assert abs(r["suggest_high"] - 24.0) < 1e-9
        assert "—" in r["explain"]  # 缺失值显示占位符而非崩溃

    def test_single_leg_export_only(self, tmp_db):
        """只有出口价（市场无净重）时同样不崩"""
        def fake_fetch(cmd, partner, period, reporter="中国", flow="X"):
            if flow == "M":
                return [_mk(4000.0, 0)]       # 市场无净重 → market_up=None
            return [_mk(1000.0, 100.0)]       # 出口 10/kg
        with mock.patch.object(trade, "fetch_year", fake_fetch):
            with mock.patch.object(trade, "get_latest_year", return_value=2024):
                with mock.patch.object(trade, "_use_formal", return_value=False):
                    with mock.patch.object(trade, "AREA_MAP", {"中国": "156", "德国": "276"}):
                        r = pricing.suggest_pricing("摄像头", "德国")
        assert r["available"] is True
        assert abs(r["export_unit_price"] - 10.0) < 1e-9
        assert r["market_unit_price"] is None
        # 单腿降级：基于出口价 1.5x–2.5x
        assert abs(r["suggest_low"] - 15.0) < 1e-9
        assert abs(r["suggest_high"] - 25.0) < 1e-9
        assert "—" in r["explain"]
