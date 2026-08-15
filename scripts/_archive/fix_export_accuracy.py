# -*- coding: utf-8 -*-
"""export.py 批量数据准确性修复"""
from pathlib import Path

f = Path(r"D:\毕设一\export.py")
t = f.read_text(encoding="utf-8")
orig = t
n = 0

def rep(old, new):
    global t, n
    if old in t:
        t = t.replace(old, new)
        n += 1
    else:
        print("未匹配:", old[:70])

# 1. 饼图调用传原始份额
rep('_add_pie_chart(doc, pie_labels, pie_vals, f"{product} 品牌份额")',
    '_add_pie_chart(doc, pie_labels, pie_vals, f"{product} 品牌份额", raw_values=pie_vals)')
rep('_add_pie_chart(doc, share_labels, share_vals, f"{product} 出口国份额结构")',
    '_add_pie_chart(doc, share_labels, share_vals, f"{product} 出口国份额结构", raw_values=share_vals)')

# 2. CAGR 旧公式（两处相同）→ 年差
old_cagr = "cagr = (pow(last / first, 1 / (len(years) - 1)) - 1) * 100"
new_cagr = ("n_years = int(years[-1]) - int(years[0])\n"
            "                cagr = (pow(last / first, 1 / n_years) - 1) * 100 if n_years > 0 else None")
rep(old_cagr, new_cagr)

# 3. 龙头 max 解析 → 公共 _parse_share
old_top = ('for b in brands:\n'
           '            try:\n'
           '                s = float(str(b.get("share", "").replace("%", "")))\n'
           '                top_share = max(top_share or 0, s)\n'
           '            except (ValueError, TypeError):\n'
           '                continue')
new_top = ('for b in brands:\n'
           '            s = _parse_share(b.get("share"))\n'
           '            if s is not None:\n'
           '                top_share = max(top_share or 0, s)')
rep(old_top, new_top)

# 4. 原始数据表 float 强转
rep('raw_tbl.rows[i].cells[3].text = f"{r.get(\'primaryValue\') or 0:,.0f}"',
    'raw_tbl.rows[i].cells[3].text = f"{float(r.get(\'primaryValue\') or 0):,.0f}"')
rep('raw_tbl.rows[i].cells[4].text = f"{r.get(\'netWgt\') or 0:,.0f}"',
    'raw_tbl.rows[i].cells[4].text = f"{float(r.get(\'netWgt\') or 0):,.0f}"')

# 5. 市场集中度文案：单品牌份额 ≠ 合计
rep("• 龙头品牌份额合计约 {top_share:.0f}%：市场高度集中",
    "• 龙头品牌（最大者）份额约 {top_share:.0f}%：市场集中度较高")
rep("elif top_share >= 15:\n"
    "                _p(f\"• 龙头品牌份额约 {top_share:.0f}%：市场中度集中",
    "elif top_share >= 15:\n"
    "                _p(f\"• 龙头品牌（最大者）份额约 {top_share:.0f}%：市场存在主导者")
rep("• 龙头品牌份额约 {top_share:.0f}%：市场相对分散",
    "• 龙头品牌（最大者）份额约 {top_share:.0f}%：市场相对分散")

# 6. build_trend_chart 排序 + dict/float 兼容
old_tc = "    years = list(trend.keys())\n    values = [trend[y][\"value\"] / 1e8 for y in years]  # 亿美元"
new_tc = ("    # 按年份排序（键可能是 str/int）；兼容 {year: {value, weight}} 与 {year: float} 两种结构\n"
          "    years = sorted(trend.keys(), key=lambda k: int(k))\n"
          "    def _val(y):\n"
          "        v = trend[y]\n"
          "        return v[\"value\"] if isinstance(v, dict) else v\n"
          "    values = [_val(y) / 1e8 for y in years]  # 亿美元")
rep(old_tc, new_tc)

f.write_text(t, encoding="utf-8")
print(f"完成 {n} 处替换")
