# -*- coding: utf-8 -*-
"""清理真实污染缓存行（UN 数据重复行）"""
import json
import sqlite3

conn = sqlite3.connect(r"D:\毕设一\tradepilot.db")
conn.row_factory = sqlite3.Row
rows = conn.execute(
    """SELECT id, cmd_code, partner_code, period, flow_code, reporter_code, data_json
       FROM trade_cache ORDER BY id"""
).fetchall()

dirty_ids = []
for r in rows:
    try:
        data = json.loads(r["data_json"])
    except Exception:
        continue
    if not isinstance(data, list) or not data:
        continue
    if not any(isinstance(d, dict) and "reporterCode" in d for d in data):
        continue
    seen = set()
    dup = 0
    for d in data:
        key = (d.get("reporterCode"), d.get("partnerCode"), d.get("cmdCode"),
               d.get("period"), d.get("motCode"), d.get("mosCode"))
        if key in seen:
            dup += 1
        seen.add(key)
    if dup > 0:
        dirty_ids.append(r["id"])

if dirty_ids:
    placeholders = ",".join("?" * len(dirty_ids))
    cur = conn.execute(f"DELETE FROM trade_cache WHERE id IN ({placeholders})", dirty_ids)
    print(f"已删除 {cur.rowcount} 行污染缓存")
    conn.commit()
else:
    print("无污染缓存")
conn.close()
