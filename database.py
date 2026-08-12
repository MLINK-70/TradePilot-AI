"""database.py — SQLite 缓存层：贸易数据查询缓存

免费版 UN Comtrade 限流严格（429），查询必须缓存。
粒度：HS 编码 + 国家/组织代码 + 年份 + 流向。
"""
import json
import sqlite3
from datetime import datetime

DB_PATH = "tradepilot.db"


def get_conn():
    # timeout=10：并发写时等待锁最多 10 秒，避免立即抛 "database is locked"
    conn = sqlite3.connect(DB_PATH, timeout=10)
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
            cache_key TEXT NOT NULL DEFAULT '',
            data_json TEXT NOT NULL,
            fetched_at TEXT NOT NULL,
            UNIQUE(cmd_code, partner_code, period, flow_code, reporter_code, cache_key)
        )
    """)
    # 迁移：旧表无 cache_key 列时重建（缓存数据易失，直接重建避免索引冲突）
    cols = [r[1] for r in conn.execute("PRAGMA table_info(trade_cache)").fetchall()]
    if "cache_key" not in cols:
        conn.execute("DROP TABLE trade_cache")
        conn.execute("""
            CREATE TABLE trade_cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cmd_code TEXT NOT NULL,
                partner_code TEXT NOT NULL,
                period TEXT NOT NULL,
                flow_code TEXT NOT NULL,
                reporter_code TEXT NOT NULL DEFAULT '156',
                cache_key TEXT NOT NULL DEFAULT '',
                data_json TEXT NOT NULL,
                fetched_at TEXT NOT NULL,
                UNIQUE(cmd_code, partner_code, period, flow_code, reporter_code, cache_key)
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
    conn.execute("""
        CREATE TABLE IF NOT EXISTS report_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            report_type TEXT NOT NULL,      -- market / trade
            product TEXT NOT NULL,
            country TEXT NOT NULL,
            params TEXT DEFAULT '',         -- 额外参数（年份区间/出口国/格式等，JSON）
            result_json TEXT NOT NULL,      -- 完整查询结果（含证据链/AI 解读）
            created_at TEXT NOT NULL,
            UNIQUE(report_type, product, country, params)
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_report_history ON report_history(report_type, product, country)")
    conn.commit()
    conn.close()


def get_cached(cmd_code: str, partner_code: str, period: str, flow_code: str, reporter_code: str = "156", cache_key: str = "") -> list | None:
    """查缓存；命中返回数据列表，未命中返回 None"""
    conn = get_conn()
    row = conn.execute(
        "SELECT data_json FROM trade_cache WHERE cmd_code=? AND partner_code=? AND period=? AND flow_code=? AND reporter_code=? AND cache_key=?",
        (cmd_code, partner_code, period, flow_code, reporter_code, cache_key),
    ).fetchone()
    conn.close()
    return json.loads(row["data_json"]) if row else None


def save_cache(cmd_code: str, partner_code: str, period: str, flow_code: str, data: list, reporter_code: str = "156", cache_key: str = ""):
    """写入缓存（存在则更新）"""
    conn = get_conn()
    conn.execute(
        """INSERT INTO trade_cache (cmd_code, partner_code, period, flow_code, reporter_code, cache_key, data_json, fetched_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(cmd_code, partner_code, period, flow_code, reporter_code, cache_key)
           DO UPDATE SET data_json=excluded.data_json, fetched_at=excluded.fetched_at""",
        (cmd_code, partner_code, period, flow_code, reporter_code, cache_key, json.dumps(data, ensure_ascii=False), datetime.now().isoformat()),
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


def save_report_history(report_type: str, product: str, country: str,
                        result: dict, params: str = "") -> int:
    """保存报告历史：市场/贸易查询完整结果 → report_history 表

    同 (type, product, country, params) 存在则更新（覆盖旧结果），返回 id。
    """
    init_db()
    conn = get_conn()
    conn.execute(
        """INSERT INTO report_history (report_type, product, country, params, result_json, created_at)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(report_type, product, country, params)
           DO UPDATE SET result_json=excluded.result_json, created_at=excluded.created_at""",
        (report_type, product, country, params,
         json.dumps(result, ensure_ascii=False), datetime.now().isoformat()),
    )
    row = conn.execute(
        "SELECT id FROM report_history WHERE report_type=? AND product=? AND country=? AND params=?",
        (report_type, product, country, params),
    ).fetchone()
    conn.commit()
    conn.close()
    return row["id"] if row else 0


def get_report_history(report_type: str, product: str, country: str,
                       params: str = "") -> dict | None:
    """查报告历史：同参数命中返回结果 dict，未命中返回 None"""
    init_db()
    conn = get_conn()
    row = conn.execute(
        "SELECT result_json FROM report_history WHERE report_type=? AND product=? AND country=? AND params=?",
        (report_type, product, country, params),
    ).fetchone()
    conn.close()
    return json.loads(row["result_json"]) if row else None


def list_report_history(report_type: str = "", limit: int = 50) -> list:
    """列出历史记录（倒序），可选按类型过滤"""
    init_db()
    conn = get_conn()
    if report_type:
        rows = conn.execute(
            "SELECT id, report_type, product, country, params, created_at FROM report_history "
            "WHERE report_type=? ORDER BY id DESC LIMIT ?",
            (report_type, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT id, report_type, product, country, params, created_at FROM report_history "
            "ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
