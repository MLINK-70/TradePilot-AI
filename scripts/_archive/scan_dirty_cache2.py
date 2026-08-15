# -*- coding: utf-8 -*-
"""修正版扫描：只对 UN Comtrade 原始行（含 reporterCode 字段）判重"""
import json
import sqlite3

conn = sqlite3.connect(r"D:\毕设一\tradepilot.db")
conn.row_factory = sqlite3.Row
rows = conn.execute(
    """SELECT id, cmd_code, partner_code, period, flow_code, reporter_code, data_json
       FROM trade_cache ORDER BY id"""
).fetchall()

dirty = []
for r in rows:
    try:
        data = json.loads(r["data_json"])
    except Exception:
        continue
    if not isinstance(data, list) or not data:
        continue
    # 只处理 UN 原始数据行（有 reporterCode 字段）
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
        dirty.append((r["id"], r["cmd_code"], r["partner_code"], r["period"],
                      r["flow_code"], r["reporter_code"], len(data), dup))

print(f"真实污染行数（UN 数据重复）: {len(dirty)}")
for d in dirty:
    print(f"  id={d[0]} HS={d[1]} partner={d[2]} {d[3]} flow={d[4]} reporter={d[5]} "
          f"条数={d[6]} 重复={d[7]}")

ids = [str(d[0]) for d in dirty]
print()
print("清理 id:", ",".join(ids))
