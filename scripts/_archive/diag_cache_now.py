# -*- coding: utf-8 -*-
"""查德国 8525 2024 缓存现状"""
import json
import sqlite3

conn = sqlite3.connect(r"D:\毕设一\tradepilot.db")
conn.row_factory = sqlite3.Row
rows = conn.execute(
    """SELECT period, reporter_code, partner_code, flow_code, data_json, fetched_at
       FROM trade_cache WHERE cmd_code='8525' AND reporter_code='276' ORDER BY id DESC LIMIT 8"""
).fetchall()
for r in rows:
    data = json.loads(r["data_json"])
    total = sum(d.get("primaryValue") or 0 for d in data)
    motvals = [d.get("motCode") for d in data[:6]]
    print(f"period={r['period']} flow={r['flow_code']} partner={r['partner_code']} "
          f"条数={len(data)} 总额={total/1e8:.2f}亿 mot前6={motvals} fetched={r['fetched_at']}")
