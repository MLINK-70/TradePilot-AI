# -*- coding: utf-8 -*-
"""Blast Radius / 数据串台回归测试（2026-09-02）

背景：真正危险的 Bug 不是"A 功能坏了"，而是"A 功能看起来正常，
却把 B、C、D 的结果一起污染了"。本文件专测**跨查询污染**：

- A → B：查完产品 A 再查产品 B，B 绝不能拿到 A 的数据
- A → B → A：交叉后再查 A，A 必须与首次一致（不被 B 污染）
- 同类保护：COMPARE / TOPEXP / LLM 缓存同样按产品隔离

实证缺陷（已修）：get_competitiveness_matrix 的缓存
cmd/partner/period/reporter 全填 "0"，只靠 cache_key 里的
target+years+reporter 区分 → 同 (市场,年份,出口国) 换产品命中同一份矩阵
（查咖啡机拿到蓝牙耳机的出口大国数据）。修复后 partner_code 存 HS 编码。
"""
from unittest import mock

import pytest

import trade
from trade import get_competitiveness_matrix

YEARS = [2020, 2021, 2022, 2023, 2024, 2025]

# 两个产品的"真实"数据刻意做成不同，便于识别串台
PRODUCTS = {
    "蓝牙耳机": {
        "hs": "8518",
        "top": [{"country": "中国", "value": 1.75e10}, {"country": "美国", "value": 3.5e9}],
        "value": 5.94e8,      # 中国出口额
        "share": 15.33,
    },
    "咖啡机": {
        "hs": "8516",
        "top": [{"country": "德国", "value": 8.1e9}, {"country": "意大利", "value": 6.2e9}],
        "value": 2.13e8,
        "share": 7.41,
    },
}


def _fake_hs_lookup(product, *a, **kw):
    return PRODUCTS.get(product, {}).get("hs", "")


def _fake_top_exporters(product, year, top_n=6, *a, **kw):
    return PRODUCTS.get(product, {}).get("top", [])


def _fake_query_trend(product, target, years, reporter="中国", *a, **kw):
    p = PRODUCTS.get(product, {})
    hs = p.get("hs", "")
    # 每个年份给足 3 个以上数据点（analyze_trade_trend 要求 >=3）
    trend = {str(y): {"value": p.get("value", 0) + y, "weight": 1e6} for y in years}
    return hs, [], trend


def _fake_summarize_stats(trend, *a, **kw):
    vals = [v["value"] for v in trend.values()]
    return {
        "last_value": vals[-1] if vals else 0,
        "cagr_pct": 1.5,
        "unit_prices": [{"year": y, "price": 15.3} for y in sorted(trend.keys())],
    }


def _fake_competitiveness(product, target, year, reporter="中国", *a, **kw):
    p = PRODUCTS.get(product, {})
    return {"available": True, "tc": 0.72, "market_share": p.get("share"),
            "quality": "valid", "quality_note": ""}


@pytest.fixture
def _mocked_matrix(monkeypatch):
    """把 matrix 依赖的外部取数全部替换为确定性假数据（不碰网络）"""
    monkeypatch.setattr(trade, "hs_lookup", _fake_hs_lookup)
    monkeypatch.setattr(trade, "get_top_exporters", _fake_top_exporters)
    monkeypatch.setattr(trade, "query_trend", _fake_query_trend)
    monkeypatch.setattr(trade, "summarize_stats", _fake_summarize_stats)
    monkeypatch.setattr(trade, "get_competitiveness", _fake_competitiveness)


def _matrix(product, target="德国", reporter="中国"):
    return get_competitiveness_matrix(product, target, YEARS, reporter=reporter)


class TestMatrixNoCrossTalk:
    """核心：换个产品查，绝不能拿到上一个产品的数据"""

    def test_matrix_differs_by_product(self, tmp_db, _mocked_matrix):
        """A→B：蓝牙耳机 vs 咖啡机 必须得到不同矩阵（修复前两者完全相同）"""
        m_a = _matrix("蓝牙耳机")
        m_b = _matrix("咖啡机")
        # 防御假阳性：mock 必须真的产生非空结果，否则"空==空"会让测试失真
        assert m_a, "蓝牙耳机矩阵不应为空（mock 未生效？）"
        assert m_b, "咖啡机矩阵不应为空（mock 未生效？）"
        names_a = [m["country"] for m in m_a]
        names_b = [m["country"] for m in m_b]
        assert "中国" in names_a, f"蓝牙耳机应含中国，实际 {names_a}"
        assert "德国" in names_b, f"咖啡机应含德国，实际 {names_b}（疑似串台到蓝牙耳机）"

    def test_matrix_aba_roundtrip(self, tmp_db, _mocked_matrix):
        """A→B→A：中间查过 B 之后，A 仍必须等于首次结果"""
        first = _matrix("蓝牙耳机")
        _matrix("咖啡机")          # 中间插入另一个产品
        again = _matrix("蓝牙耳机")
        assert first == again, "再查 A 结果被 B 污染（缓存未按产品隔离）"

    def test_matrix_share_not_crossed(self, tmp_db, _mocked_matrix):
        """份额字段也必须来自各自产品（15.33 vs 7.41）"""
        m_a = {m["country"]: m for m in _matrix("蓝牙耳机")}
        m_b = {m["country"]: m for m in _matrix("咖啡机")}
        assert m_a["中国"]["market_share"] == 15.33
        assert m_b["德国"]["market_share"] == 7.41

    def test_matrix_cache_row_carries_hs(self, tmp_db, _mocked_matrix):
        """缓存行必须带 HS 编码（串台修复的物理证据）"""
        import sqlite3
        _matrix("蓝牙耳机")
        conn = sqlite3.connect(trade.__name__ and __import__("database").DB_PATH)
        row = conn.execute(
            "SELECT partner_code FROM trade_cache WHERE cmd_code='MATRIX'").fetchone()
        assert row is not None, "MATRIX 缓存未写入（hs_code 为空时跳过，符合预期则调整用例）"
        assert row[0] == "8518", f"MATRIX 缓存 partner_code 应为 HS 编码，实际 {row[0]!r}"


class TestSiblingCachesNoCrossTalk:
    """同类保护：COMPARE / TOPEXP 本来就有 HS 维度，锁死不回退"""

    def test_compare_cache_isolated_by_product(self, tmp_db):
        """COMPARE 缓存：不同产品不串台（已有 HS 维度，回归保护）"""

        def fake_fetch_year(cmd, partner, year, reporter="中国", flow="X"):
            return [{"primaryValue": 1e8 if cmd == "8518" else 2e8, "netWgt": 1e6}]

        def fake_hs(p, *a, **kw):
            return "8518" if p == "蓝牙耳机" else "8516"

        with mock.patch.object(trade, "hs_lookup", fake_hs), \
             mock.patch.object(trade, "partner_lookup", lambda m: "276"), \
             mock.patch.object(trade, "fetch_year", fake_fetch_year):
            a = trade.get_competitor_comparison("蓝牙耳机", "德国", "2024",
                                                competitors=["中国"])
            b = trade.get_competitor_comparison("咖啡机", "德国", "2024",
                                                competitors=["中国"])
        assert a["competitors"][0]["value"] == 1e8
        assert b["competitors"][0]["value"] == 2e8, "COMPARE 串台（产品维度丢失）"

    def test_topexp_cache_isolated_by_product(self, tmp_db):
        """TOPEXP 缓存：不同产品返回不同出口大国排名"""

        def fake_fetch_year(cmd, partner, year, reporter="中国", flow="X"):
            return [{"primaryValue": 1e10 if cmd == "8518" else 5e8}]

        def fake_hs(p, *a, **kw):
            return "8518" if p == "蓝牙耳机" else "8516"

        with mock.patch.object(trade, "hs_lookup", fake_hs), \
             mock.patch.object(trade, "fetch_year", fake_fetch_year):
            a = trade.get_top_exporters("蓝牙耳机", "2024", top_n=2)
            b = trade.get_top_exporters("咖啡机", "2024", top_n=2)
        assert a and b
        assert a[0]["value"] == 1e10
        assert b[0]["value"] == 5e8, "TOPEXP 串台（产品维度丢失）"


class TestLlmCacheNoCrossTalk:
    """LLM 层：换产品/换国家/换模型都必须重新分析，不得复用"""

    def test_market_cache_key_differs_by_product(self):
        """缓存 key 必须因产品 / 国家 / 模型而异"""
        import llm
        base = dict(market_context=None, trade_evidence=None,
                    competitiveness=None, background=None, landscape=None)
        k_headset = llm._market_cache_key("蓝牙耳机", "德国", **base)
        k_coffee = llm._market_cache_key("咖啡机", "德国", **base)
        k_other_country = llm._market_cache_key("蓝牙耳机", "法国", **base)
        assert k_headset != k_coffee, "不同产品用了同一个 LLM 缓存 key"
        assert k_headset != k_other_country, "不同国家用了同一个 LLM 缓存 key"

    def test_market_cache_key_is_case_insensitive(self):
        """大小写/空格归一（防 iPhone/iphone 双缓存烧双倍 token）"""
        import llm
        base = dict(market_context=None, trade_evidence=None,
                    competitiveness=None, background=None, landscape=None)
        assert llm._market_cache_key("iPhone", "德国", **base) == \
               llm._market_cache_key("iphone ", "德国", **base)
