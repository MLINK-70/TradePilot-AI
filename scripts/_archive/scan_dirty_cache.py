# -*- coding: utf-8 -*-
"""全库脏缓存扫描：找出含重复行 / mot 拆分污染的缓存记录"""
import json
import sqlite3

conn = sqlite3.connect(r"D:\毕设一\tradepilot.db")
conn.row_factory = sqlite3.Row
rows = conn.execute(
    """SELECT id, cmd_code, partner_code, period, flow_code, reporter_code, data_json, fetched_at
       FROM trade_cache ORDER BY id"""
).fetchall()

dirty = []
total_rows = 0
for r in rows:
    try:
        data = json.loads(r["data_json"])
    except Exception:
        continue
    if not isinstance(data, list) or not data:
        continue
    total_rows += len(data)
    # 检查 1：重复记录键（同 reporter+partner+cmd+period+mot+mos 出现多次）
    seen = set()
    dup = 0
    has_mot_split = False
    mot_all_present = False
    for d in data:
        key = (d.get("reporterCode"), d.get("partnerCode"), d.get("cmdCode"),
               d.get("period"), d.get("motCode"), d.get("mosCode"))
        if key in seen:
            dup += 1
        seen.add(key)
        mot = d.get("motCode")
        if mot not in (None, 0, "", "0"):
            has_mot_split = True
        else:
            mot_all_present = True
    if dup > 0 or (has_mot_split and not mot_all_present):
        dirty.append((r["id"], r["cmd_code"], r["partner_code"], r["period"],
                      r["flow_code"], r["reporter_code"], len(data), dup, r["fetched_at"]))

print(f"缓存总行数: {len(rows)}，数据记录总数: {total_rows}")
print(f"污染行数: {len(dirty)}")
print()
for d in dirty:
    print(f"  id={d[0]} HS={d[1]} partner={d[2]} {d[3]} flow={d[4]} reporter={d[5]} "
          f"条数={d[6]} 重复={d[7]} 缓存于={d[8][:16]}")

# 输出可清理的 id 列表
if dirty:
    ids = [str(d[0]) for d in dirty]
    print()
    print("需清理 id:", ",".join(ids))
