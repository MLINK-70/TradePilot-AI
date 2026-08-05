"""database.py — SQLite 缓存层：贸易数据查询缓存

免费版 UN Comtrade 限流严格（429），查询必须缓存。
粒度：HS 编码 + 国家/组织代码 + 年份 + 流向。
"""
import json
import sqlite3
from datetime import datetime

DB_PATH = "tradepilot.db"


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trade_cache (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            cmd_code TEXT NOT NULL,
            partner_code TEXT NOT NULL,
            period TEXT NOT NULL,
            flow_code TEXT NOT NULL,
            reporter_code TEXT NOT NULL DEFAULT '156',
            data_json TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            UNIQUE(cmd_code, partner_code, period, flow_code, reporter_code)
        )
    """)
    # 迁移：旧表无 reporter_code 列时重建（缓存数据易失，直接重建避免索引冲突）
    cols = [r[1] for r in conn.execute("PRAGMA table_info(trade_cache)").fetchall()]
    if "reporter_code" not in cols:
        conn.execute("DROP TABLE trade_cache")
        conn.execute("""
            CREATE TABLE trade_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cmd_code TEXT NOT NULL,
                partner_code TEXT NOT NULL,
                period TEXT NOT NULL,
                flow_code TEXT NOT NULL,
                reporter_code TEXT NOT NULL DEFAULT '156',
                data_json TEXT NOT NULL,
                fetched_at TEXT NOT NULL,
                UNIQUE(cmd_code, partner_code, period, flow_code, reporter_code)
            )
        """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS query_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product TEXT,
            hs_code TEXT,
            country_or_group TEXT,
            created_at TEXT
        )
    """)
    conn.commit()
    conn.close()


def get_cached(cmd_code: str, partner_code: str, period: str, flow_code: str, reporter_code: str = "156") -> list | None:
    """查缓存；命中返回数据列表，未命中返回 None"""
    conn = get_conn()
    row = conn.execute(
        "SELECT data_json FROM trade_cache WHERE cmd_code=? AND partner_code=? AND period=? AND flow_code=? AND reporter_code=?",
        (cmd_code, partner_code, period, flow_code, reporter_code),
    ).fetchone()
    conn.close()
    return json.loads(row["data_json"]) if row else None


def save_cache(cmd_code: str, partner_code: str, period: str, flow_code: str, data: list, reporter_code: str = "156"):
    """写入缓存（存在则更新）"""
    conn = get_conn()
    conn.execute(
        """INSERT INTO trade_cache (cmd_code, partner_code, period, flow_code, reporter_code, data_json, fetched_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(cmd_code, partner_code, period, flow_code, reporter_code)
           DO UPDATE SET data_json=excluded.data_json, fetched_at=excluded.fetched_at""",
        (cmd_code, partner_code, period, flow_code, reporter_code, json.dumps(data, ensure_ascii=False), datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()


def log_query(product: str, hs_code: str, target: str):
    """记录查询历史（为第三版报告回看铺垫）"""
    conn = get_conn()
    conn.execute(
        "INSERT INTO query_log (product, hs_code, country_or_group, created_at) VALUES (?, ?, ?, ?)",
        (product, hs_code, target, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()
