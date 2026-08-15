# -*- coding: utf-8 -*-
"""诊断：德国 X 流记录 partnerCode 分布（验证 partner=156 过滤是否生效）"""
import json
import sqlite3

conn = sqlite3.connect(r"D:\毕设一\tradepilot.db")
conn.row_factory = sqlite3.Row

row = conn.execute(
    """SELECT data_json FROM trade_cache
       WHERE cmd_code='8525' AND partner_code='156' AND flow_code='X' AND reporter_code='276' AND period='2024'"""
).fetchone()
data = json.loads(row["data_json"])
print(f"记录数: {len(data)}")
print("字段名:", list(data[0].keys()) if data else "空")
from collections import Counter
partners = Counter()
cmds = Counter()
for d in data:
    partners[d.get("partnerCode")] += 1
    cmds[str(d.get("cmdCode"))] += 1
print("partnerCode 分布:", dict(partners))
print("cmdCode 分布:", dict(cmds))
print("\n全部记录:")
for d in data:
    print(f"  partner={d.get('partnerCode')} cmd={d.get('cmdCode')} period={d.get('period')} refYear={d.get('refYear')} value={d.get('primaryValue')/1e8:.2f}亿")
