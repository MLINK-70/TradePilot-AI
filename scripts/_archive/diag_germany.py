# -*- coding: utf-8 -*-
"""诊断：德国对华出口（reporter=276, partner=156, flow=X）记录明细"""
import json
import sqlite3

conn = sqlite3.connect(r"D:\毕设一\tradepilot.db")
conn.row_factory = sqlite3.Row

# 1. 德国对华出口（矩阵里 55.07 亿的来源）
print("=== 德国对华出口 8525（reporter=276 partner=156 flow=X）各年记录 ===")
rows = conn.execute(
    """SELECT period, data_json FROM trade_cache
       WHERE cmd_code='8525' AND partner_code='156' AND flow_code='X' AND reporter_code='276'"""
).fetchall()
for r in rows:
    data = json.loads(r["data_json"])
    total = sum(d.get("primaryValue") or 0 for d in data)
    print(f"  年 {r['period']}: {len(data)} 条, 总额 {total/1e8:.2f} 亿美元, partnerDesc={data[0].get('partnerDesc') if data else '空'}, cmdDesc={data[0].get('cmdDesc') if data else '空'}")

# 2. 中国从全球进口聚合记录（partner=0 flow=M）
print("\n=== 中国从全球进口 8525（partner=0 flow=M reporter=156）===")
row = conn.execute(
    """SELECT period, data_json FROM trade_cache
       WHERE cmd_code='8525' AND partner_code='0' AND flow_code='M' AND reporter_code='156'"""
).fetchone()
if row:
    data = json.loads(row["data_json"])
    for d in data[:3]:
        print(f"  partnerDesc={d.get('partnerDesc')} primaryValue={d.get('primaryValue')} cmdDesc={d.get('cmdDesc')} period={d.get('period')} refYear={d.get('refYear')}")

# 3. 德国对华出口 2024 年原始记录（矩阵用的最新年）
print("\n=== 德国对华出口 8525 2024 明细（前 5 条）===")
row = conn.execute(
    """SELECT data_json FROM trade_cache
       WHERE cmd_code='8525' AND partner_code='156' AND flow_code='X' AND reporter_code='276' AND period='2024'"""
).fetchone()
if row:
    data = json.loads(row["data_json"])
    for d in data[:5]:
        print(f"  partnerDesc={d.get('partnerDesc')} primaryValue={d.get('primaryValue')} cmdDesc={d.get('cmdDesc')}")
