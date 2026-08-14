# -*- coding: utf-8 -*-
"""统计指标纯函数测试（核心业务逻辑，无网络）

覆盖：summarize_stats（CAGR 非连续年份/边界）、summarize_trend（聚合）、compute_tc。
"""
import pytest

from trade import compute_tc, summarize_stats, summarize_trend


class TestCAGR:
    def test_non_continuous_years_use_actual_year_diff(self):
        """[2018,2020,2022] 跨 4 年 CAGR 10%；旧算法（点数间隔 n=2）会算成 ~21%"""
        trend = {
            "2018": {"value": 100.0, "weight": 10.0},
            "2020": {"value": 121.0, "weight": 12.0},
            "2022": {"value": 146.41, "weight": 14.0},
        }
        cagr = summarize_stats(trend)["cagr_pct"]
        assert cagr is not None
        assert abs(cagr - 10.0) < 0.5

    def test_continuous_years_unchanged(self):
        trend = {str(y): {"value": 100 * (1.1 ** (y - 2018)), "weight": 10.0}
                 for y in range(2018, 2022)}
        cagr = summarize_stats(trend)["cagr_pct"]
        assert abs(cagr - 10.0) < 0.5

    def test_single_year_no_crash(self):
        stats = summarize_stats({"2020": {"value": 100.0, "weight": 10.0}})
        assert stats["cagr_pct"] is None

    def test_first_value_zero_no_crash(self):
        stats = summarize_stats({
            "2018": {"value": 0.0, "weight": 1.0},
            "2022": {"value": 100.0, "weight": 10.0},
        })
        assert stats["cagr_pct"] is None

    def test_empty_trend(self):
        assert summarize_stats({}) == {}


class TestSummarizeTrend:
    def test_aggregates_by_year_and_sorts(self):
        rows = [
            {"refYear": 2021, "primaryValue": 10, "netWgt": 5},
            {"refYear": 2020, "primaryValue": 7, "netWgt": 3},
            {"refYear": 2021, "primaryValue": 4, "netWgt": 2},
            {"refYear": 2020, "primaryValue": 3, "netWgt": 1},
        ]
        trend = summarize_trend(rows)
        assert list(trend.keys()) == [2020, 2021]
        assert trend[2020] == {"value": 10.0, "weight": 4.0}
        assert trend[2021] == {"value": 14.0, "weight": 7.0}

    def test_skips_rows_without_refyear(self):
        rows = [{"primaryValue": 9}, {"refYear": 2020, "primaryValue": 5, "netWgt": 1}]
        assert summarize_trend(rows) == {2020: {"value": 5.0, "weight": 1.0}}


class TestComputeTc:
    def test_zero_zero_returns_none(self):
        assert compute_tc(0, 0) is None

    def test_positive_sign(self):
        tc = compute_tc(100, 50)
        assert tc is not None and tc > 0

    def test_negative_sign(self):
        tc = compute_tc(50, 100)
        assert tc is not None and tc < 0

    def test_zero_export(self):
        assert compute_tc(0, 100) == -1.0
