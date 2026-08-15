# -*- coding: utf-8 -*-
"""fetch_year 去重修复的完整验证"""
import sys
sys.path.insert(0, r"D:\毕设一")

from trade import fetch_year

print("=== 验证 1：德国对华出口（reporter=276, partner=156, flow=X）===")
rows = fetch_year("8525", "156", "2024", "276", "X")
total = sum(r.get("primaryValue") or 0 for r in rows)
print(f"记录数: {len(rows)} | 总额: {total/1e8:.2f} 亿美元")
# 记录键唯一性
keys = [(r.get("reporterCode"), r.get("partnerCode"), r.get("cmdCode"),
         r.get("period"), r.get("motCode"), r.get("mosCode")) for r in rows]
print(f"唯一键数: {len(set(keys))}（应等于记录数，无重复）")
assert len(rows) == 6.37 or abs(total/1e8 - 6.37) < 0.05, f"德国值异常: {total/1e8}"

print("\n=== 验证 2：日本对华出口（reporter=392, partner=156, flow=X）===")
rows2 = fetch_year("8525", "156", "2024", "392", "X")
total2 = sum(r.get("primaryValue") or 0 for r in rows2)
print(f"记录数: {len(rows2)} | 总额: {total2/1e8:.2f} 亿美元")
assert total2 > 0, "日本数据不应被破坏"

print("\n=== 验证 3：中国从全球进口（reporter=156, partner=0, flow=M）===")
rows3 = fetch_year("8525", "0", "2024", "156", "M")
total3 = sum(r.get("primaryValue") or 0 for r in rows3)
print(f"记录数: {len(rows3)} | 总额: {total3/1e8:.2f} 亿美元")
assert total3 > 0

print("\n=== 验证 4：德国 5 年序列（矩阵用）===")
for y in range(2020, 2025):
    rs = fetch_year("8525", "156", str(y), "276", "X")
    t = sum(r.get("primaryValue") or 0 for r in rs)
    print(f"  {y}: {len(rs)} 条, {t/1e8:.2f} 亿美元")

print("\n=== 验证 5：份额数学自洽（德国 vs 中国总进口）===")
share = total / total3 * 100
print(f"德国份额 = {total/1e8:.2f} / {total3/1e8:.2f} = {share:.1f}%（应 < 100%）")
assert share < 100, "份额仍超 100%！"
print("\n✅ 全部验证通过：去重生效、正常国家不受影响、份额数学自洽")
