"""database.py — SQLite 缓存层：贸易数据查询缓存

免费版 UN Comtrade 限流严格（429），查询必须缓存。
粒度：HS 编码 + 国家/组织代码 + 年份 + 流向。

v1.0 阶段 4 改进：
- 所有连接用 contextlib.closing 管理（异常路径不泄漏）
- get_cached 支持 ttl_days（0 = 永不过期，兼容旧调用）
- WAL 模式（读写不互斥）+ 启动时清理过期行
- 报告历史保存/查询前 product/country 规范化（strip + lower，防表膨胀）
"""
import contextlib
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta

# DB 路径锚定（6.6）：开发/服务模式用项目目录（绝对路径，不依赖 CWD）；
# PyInstaller exe 模式用用户数据目录（_MEIPASS 是临时解压目录，只读不可写）
if getattr(sys, "frozen", False):
    _DATA_DIR = os.path.join(os.path.expanduser("~"), ".tradepilot")
    os.makedirs(_DATA_DIR, exist_ok=True)
    DB_PATH = os.path.join(_DATA_DIR, "tradepilot.db")
else:
    DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tradepilot.db")


def get_conn():
    # timeout=10：并发写时等待锁最多 10 秒，避免立即抛 "database is locked"
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def enable_wal():
    """开启 WAL 模式 + 降同步级别（并发读写不互斥，性能提升）"""
    with contextlib.closing(get_conn()) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.commit()


def init_db():
    with contextlib.closing(get_conn()) as conn:
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
        conn.execute("""
            CREATE TABLE IF NOT EXISTS access_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ip TEXT,                        -- 来源 IP
                path TEXT,                      -- 请求路径
                action TEXT,                    -- 动作（Host校验/限流/管理员登录/保存设置…）
                result TEXT,                    -- ok / blocked
                created_at TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS admin_sessions (
                token TEXT PRIMARY KEY,
                expires_at TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_report_history ON report_history(report_type, product, country)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_access_log ON access_log(result, created_at)")
        conn.commit()


def cleanup_expired_cache(trade_max_days: int = 365, history_max_days: int = 90):
    """启动时清理过期缓存行（保守下限：读取侧还有更严格的动态 TTL）"""
    with contextlib.closing(get_conn()) as conn:
        cutoff = (datetime.now() - timedelta(days=trade_max_days)).isoformat()
        conn.execute("DELETE FROM trade_cache WHERE fetched_at < ?", (cutoff,))
        cutoff_h = (datetime.now() - timedelta(days=history_max_days)).isoformat()
        conn.execute("DELETE FROM report_history WHERE created_at < ?", (cutoff_h,))
        conn.commit()


def get_cached(cmd_code: str, partner_code: str, period: str, flow_code: str,
               reporter_code: str = "156", cache_key: str = "", ttl_days: int = 0) -> list | None:
    """查缓存；命中返回数据列表，未命中/已过期返回 None

    ttl_days>0：fetched_at 超过该天数视为过期（0 = 永不过期，兼容旧调用）。
    """
    with contextlib.closing(get_conn()) as conn:
        row = conn.execute(
            "SELECT data_json, fetched_at FROM trade_cache WHERE cmd_code=? AND partner_code=? AND period=? AND flow_code=? AND reporter_code=? AND cache_key=?",
            (cmd_code, partner_code, period, flow_code, reporter_code, cache_key),
        ).fetchone()
    if not row:
        return None
    if ttl_days and ttl_days > 0:
        try:
            fetched = datetime.fromisoformat(row["fetched_at"])
            if datetime.now() - fetched > timedelta(days=ttl_days):
                return None
        except (ValueError, TypeError):
            pass
    return json.loads(row["data_json"])


def save_cache(cmd_code: str, partner_code: str, period: str, flow_code: str,
               data: list, reporter_code: str = "156", cache_key: str = ""):
    """写入缓存（存在则更新）；data 可为空列表（空结果也缓存，避免重复打 API）"""
    with contextlib.closing(get_conn()) as conn:
        conn.execute(
            """INSERT INTO trade_cache (cmd_code, partner_code, period, flow_code, reporter_code, cache_key, data_json, fetched_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(cmd_code, partner_code, period, flow_code, reporter_code, cache_key)
               DO UPDATE SET data_json=excluded.data_json, fetched_at=excluded.fetched_at""",
            (cmd_code, partner_code, period, flow_code, reporter_code, cache_key,
             json.dumps(data, ensure_ascii=False), datetime.now().isoformat()),
        )
        conn.commit()


def log_query(product: str, hs_code: str, target: str):
    """记录查询历史（为第三版报告回看铺垫）"""
    with contextlib.closing(get_conn()) as conn:
        conn.execute(
            "INSERT INTO query_log (product, hs_code, country_or_group, created_at) VALUES (?, ?, ?, ?)",
            (product, hs_code, target, datetime.now().isoformat()),
        )
        conn.commit()


def _normalize(text: str) -> str:
    """参数规范化：strip + lower（防 iPhone/iphone 生成不同缓存行导致表膨胀）"""
    return (text or "").strip().lower()


def save_report_history(report_type: str, product: str, country: str,
                        result: dict, params: str = "") -> int:
    """保存报告历史：市场/贸易查询完整结果 → report_history 表

    同 (type, product, country, params) 存在则更新（覆盖旧结果），返回 id。
    保存前规范化 product/country。
    """
    init_db()
    product, country = _normalize(product), _normalize(country)
    with contextlib.closing(get_conn()) as conn:
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
        return row["id"] if row else 0


def get_report_history(report_type: str, product: str, country: str,
                       params: str = "", ttl_days: int = 7) -> dict | None:
    """查报告历史：同参数命中且未过期返回结果 dict，未命中/过期返回 None

    TTL：竞争格局等依赖 30 天增量缓存，历史结果超过 ttl_days 视为过期
    （避免把一个月前的旧格局/旧经济数据当新鲜返回）。
    """
    init_db()
    product, country = _normalize(product), _normalize(country)
    with contextlib.closing(get_conn()) as conn:
        row = conn.execute(
            "SELECT result_json, created_at FROM report_history WHERE report_type=? AND product=? AND country=? AND params=?",
            (report_type, product, country, params),
        ).fetchone()
    if not row:
        return None
    # TTL 过期检查
    try:
        created = datetime.fromisoformat(row["created_at"])
        if datetime.now() - created > timedelta(days=ttl_days):
            return None
    except (ValueError, TypeError):
        pass
    return json.loads(row["result_json"])


def list_report_history(report_type: str = "", limit: int = 50) -> list:
    """列出历史记录（倒序），可选按类型过滤"""
    init_db()
    with contextlib.closing(get_conn()) as conn:
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
    return [dict(r) for r in rows]


# ── 访问日志（v1.0 阶段 1.3：可展示的拒绝）────────────────────────────────

def log_access(ip: str, path: str, action: str, result: str):
    """记录一条访问日志（拦截/放行），管理面板据此展示安全事件"""
    init_db()
    with contextlib.closing(get_conn()) as conn:
        conn.execute(
            "INSERT INTO access_log (ip, path, action, result, created_at) VALUES (?, ?, ?, ?, ?)",
            (ip or "?", (path or "")[:200], action, result, datetime.now().isoformat()),
        )
        conn.commit()


def count_access(result: str = "blocked") -> int:
    """统计某结果（默认 blocked）的日志条数"""
    init_db()
    with contextlib.closing(get_conn()) as conn:
        row = conn.execute("SELECT COUNT(*) AS c FROM access_log WHERE result=?", (result,)).fetchone()
    return row["c"] if row else 0


def list_access(limit: int = 50) -> list:
    """最近访问日志（倒序）"""
    init_db()
    with contextlib.closing(get_conn()) as conn:
        rows = conn.execute(
            "SELECT id, ip, path, action, result, created_at FROM access_log ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


# ── 管理员会话（v1.0 阶段 1.1）───────────────────────────────────────────

def create_admin_session(token: str, expires_at: str):
    """创建管理员会话（登录成功时调用）"""
    init_db()
    with contextlib.closing(get_conn()) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO admin_sessions (token, expires_at, created_at) VALUES (?, ?, ?)",
            (token, expires_at, datetime.now().isoformat()),
        )
        conn.commit()


def check_admin_session(token: str) -> bool:
    """校验管理员会话：存在且未过期"""
    if not token:
        return False
    init_db()
    with contextlib.closing(get_conn()) as conn:
        row = conn.execute("SELECT expires_at FROM admin_sessions WHERE token=?", (token,)).fetchone()
    if not row:
        return False
    try:
        exp = datetime.fromisoformat(row["expires_at"])
        return datetime.now() < exp
    except (ValueError, TypeError):
        return False


def delete_admin_session(token: str):
    """注销管理员会话（登出时调用）"""
    init_db()
    with contextlib.closing(get_conn()) as conn:
        conn.execute("DELETE FROM admin_sessions WHERE token=?", (token,))
        conn.commit()
