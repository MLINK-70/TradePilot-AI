# -*- coding: utf-8 -*-
"""① .env 追加 UN_COMTRADE_KEY（无 BOM）② 清空所有旧 trade_cache（错误聚合逻辑的产物）"""
from pathlib import Path
import sqlite3

# 1. .env 追加 key（保留原有内容，无 BOM）
env = Path(r"D:\毕设一\.env")
lines = env.read_text(encoding="utf-8-sig").splitlines()
# 去掉已有的 UN_COMTRADE_KEY 行
lines = [l for l in lines if not l.startswith("UN_COMTRADE_KEY=")]
lines.append("UN_COMTRADE_KEY=390f1b74dc73425281d3928b4e0bf7cd")
env.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
print(".env 已写入 UN_COMTRADE_KEY")

# 2. 清空所有 trade_cache（旧数据是错误聚合的产物）
conn = sqlite3.connect(r"D:\毕设一\tradepilot.db")
cur = conn.execute("DELETE FROM trade_cache")
print(f"已清空 trade_cache {cur.rowcount} 行（错误聚合数据）")
conn.commit()
conn.close()
