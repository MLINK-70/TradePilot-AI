# -*- coding: utf-8 -*-
"""验证：份额解析 / SEC 单位 / A 股排序"""
import sys
import types

sys.path.insert(0, r"D:\毕设一")

from export import _parse_share  # noqa: E402

# 1. _parse_share 健壮性
assert _parse_share("46.5%（2026年Q1）") == 46.5, "括号"
assert _parse_share("1,234.5%") == 1234.5, "千分位"
assert _parse_share("18.2％") == 18.2, "全角"
assert _parse_share("18.2% (2026)") == 18.2, "ASCII括号"
assert _parse_share("3-5%") == 4.0, "范围中点"
assert _parse_share("约18.2%") == 18.2, "前缀"
assert _parse_share(None) is None and _parse_share("无数据") is None, "None/无数据"
print("_parse_share 7 项全过")

# 2. SEC 单位换算
from financials import _annual_series  # noqa: E402
data = {"units": {"USD": [
    {"form": "10-K", "fp": "FY", "end": "2025-09-27", "filed": "2025-11-03", "val": 416161},
    {"form": "10-K", "fp": "FY", "end": "2024-09-28", "filed": "2024-11-01", "val": 391035},
]}}
s = _annual_series(data)
assert s[0]["year"] == "2024" and s[1]["year"] == "2025", "升序"
assert abs(s[1]["value"] - 416161e6) < 1, "SEC 百万美元换算错误"
print(f"SEC 换算: 416161 百万美元 -> {s[1]['value']/1e8:.1f} 亿美元 OK（修复前 0.00）")

# 3. A 股排序
import financials  # noqa: E402
def fake_get(url, **kw):
    r = types.SimpleNamespace(status_code=200,
                              raise_for_status=lambda: None,
                              json=lambda: {"result": {"data": [
        {"REPORTDATE": "2024-12-31 00:00:00", "TOTAL_OPERATE_INCOME": 100},
        {"REPORTDATE": "2021-12-31 00:00:00", "TOTAL_OPERATE_INCOME": 70},
        {"REPORTDATE": "2023-12-31 00:00:00", "TOTAL_OPERATE_INCOME": 90},
        {"REPORTDATE": "2022-12-31 00:00:00", "TOTAL_OPERATE_INCOME": 80},
        {"REPORTDATE": "2020-12-31 00:00:00", "TOTAL_OPERATE_INCOME": 60},
    ]}})
    return r
orig_get = financials.requests.get
financials.requests.get = fake_get
try:
    fin = financials.get_a_share_financials("漫步者")
finally:
    financials.requests.get = orig_get
rev = fin["metrics"]["revenue"]
years = [r["year"] for r in rev]
assert years == sorted(years), f"A股应升序: {years}"
assert rev[-1]["year"] == "2024" and rev[-1]["value"] == 100, "最新应为 2024"
print(f"A股排序: {years}，最新 {rev[-1]} OK（修复前取 [-1] 错位到 2021）")

print("\n全部验证通过")
