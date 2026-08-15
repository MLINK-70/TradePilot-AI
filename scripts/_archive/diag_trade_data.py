# -*- coding: utf-8 -*-
"""诊断：8525 2024 年缓存记录，验证 maxRecords=500 触顶假设"""
import json
import sqlite3

conn = sqlite3.connect(r"D:\毕设一\tradepilot.db")
conn.row_factory = sqlite3.Row

rows = conn.execute(
    """SELECT cmd_code, partner_code, period, flow_code, reporter_code, LENGTH(data_json) AS ln
       FROM trade_cache WHERE cmd_code='8525' AND period='2024' ORDER BY id DESC LIMIT 20"""
).fetchall()

print("=== 8525/2024 缓存记录 ===")
for r in rows:
    print(f"  partner={r['partner_code']} flow={r['flow_code']} reporter={r['reporter_code']} 数据长度={r['ln']}")

# 重点：中国从全球进口（partner=0, flow=M, reporter=156/中国）的记录数
print("\n=== 中国从全球进口（8525 2024，flow=M, partner=0）===")
row = conn.execute(
    """SELECT data_json FROM trade_cache
       WHERE cmd_code='8525' AND partner_code='0' AND flow_code='M' AND period='2024'"""
).fetchone()
if row:
    data = json.loads(row["data_json"])
    print(f"记录数: {len(data)}（500 = 触顶截断）")
    partners = {}
    for d in data:
        p = d.get("partnerDesc") or d.get("partnerCode")
        partners[p] = partners.get(p, 0) + 1
    print(f"覆盖国家数: {len(partners)}")
    total = sum(d.get("primaryValue") or 0 for d in data)
    print(f"总进口额: {total/1e8:.2f} 亿美元")
    # 前 10 个国家
    top = sorted(partners.items(), key=lambda x: -x[1])[:10]
    print("国家分布:", top)
else:
    print("无缓存记录（该查询可能失败或未缓存）")
