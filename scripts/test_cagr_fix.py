# -*- coding: utf-8 -*-
"""CAGR 修复验证：非连续年份用实际年差，不再高估"""
from trade import summarize_stats

# 构造：2018=100, 2020=121, 2022=146.41 → 4 年真实 CAGR 10%（旧算法 n=2 → 21%）
trend = {
    "2018": {"value": 100.0, "weight": 10.0},
    "2020": {"value": 121.0, "weight": 12.0},
    "2022": {"value": 146.41, "weight": 14.0},
}
stats = summarize_stats(trend)
cagr = stats.get("cagr_pct")
print(f"CAGR = {cagr}")
assert cagr is not None, "CAGR 不应为 None"
assert abs(cagr - 10.0) < 0.5, f"CAGR 应为 ~10%，实际 {cagr}"
print("PASS: 非连续年份 [2018,2020,2022] CAGR = 10%（旧算法会算成 ~21%）")

# 连续年份回归：2018-2021 每年 +10% → 3 年 10%
trend2 = {str(y): {"value": 100 * (1.1 ** (y - 2018)), "weight": 10.0} for y in range(2018, 2022)}
stats2 = summarize_stats(trend2)
print(f"连续年份 CAGR = {stats2.get('cagr_pct')}")
assert abs(stats2.get("cagr_pct", 0) - 10.0) < 0.5
print("PASS: 连续年份 [2018..2021] CAGR = 10%（行为不变）")

# 边界：单年 → CAGR None（不崩）
stats3 = summarize_stats({"2020": {"value": 100.0, "weight": 10.0}})
print(f"单年 CAGR = {stats3.get('cagr_pct')}")
assert stats3.get("cagr_pct") is None
print("PASS: 单年不崩溃")

# 边界：首值 0 → None（防除零/complex）
stats4 = summarize_stats({"2018": {"value": 0.0, "weight": 1.0}, "2022": {"value": 100.0, "weight": 10.0}})
print(f"首值0 CAGR = {stats4.get('cagr_pct')}")
assert stats4.get("cagr_pct") is None
print("PASS: 首值 0 不崩溃")
print("=== 全部通过 ===")
