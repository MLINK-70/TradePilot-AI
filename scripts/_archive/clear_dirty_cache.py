# -*- coding: utf-8 -*-
"""清除受污染的缓存行"""
import sqlite3

conn = sqlite3.connect(r"D:\毕设一\tradepilot.db")
cur = conn.execute(
    """DELETE FROM trade_cache
       WHERE (cmd_code='8525' AND reporter_code='276' AND flow_code='X')
          OR (cmd_code='MATRIX' AND cache_key LIKE 'V1|%')"""
)
print("已清除脏缓存行数:", cur.rowcount)
conn.commit()
conn.close()
