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
import threading
from datetime import datetime, timedelta

# DB 路径锚定（6.6）：开发/服务模式用项目目录（绝对路径，不依赖 CWD）；
# PyInstaller exe 模式用用户数据目录（_MEIPASS 是临时解压目录，只读不可写）
if getattr(sys, "frozen", False):
    _DATA_DIR = os.path.join(os.path.expanduser("~"), ".tradepilot")
    os.makedirs(_DATA_DIR, exist_ok=True)
    DB_PATH = os.path.join(_DATA_DIR, "tradepilot.db")
else:
    DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tradepilot.db")

# 写锁：多线程（阶段 4 并发化）下 SQLite 单写者，写操作排队防 "database is locked"
_write_lock = threading.Lock()
# DDL 锁：并发 init_db（8 指标并发首次拉取）时防止 DROP/建表竞态
_init_lock = threading.Lock()
# 进程内已初始化标记（回归修复：此前每个请求路径都重复执行整套 DDL）
_db_initialized = False


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
    # 并发 init_db（如 World Bank 8 指标并发首查）时串行化 DDL，防建表/DROP 竞态
    global _db_initialized
    if _db_initialized:
        return
    with _init_lock:
        if _db_initialized:
            return
        _init_db_unlocked()
        _db_initialized = True


def _init_db_unlocked():
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
                -- 数据血缘（v1.0.2 数据层收口）：可追溯"这个数字怎么来的"
                source TEXT NOT NULL DEFAULT '',            -- 数据源（UN Comtrade preview/formal …）
                raw_record_count INTEGER NOT NULL DEFAULT 0,  -- 原始返回行数（过滤前）
                clean_record_count INTEGER NOT NULL DEFAULT 0, -- 清洗后行数（C00+mot=0）
                quality TEXT NOT NULL DEFAULT 'valid',      -- valid / suspicious / invalid / rejected
                validation_reason TEXT NOT NULL DEFAULT '', -- 质量判定理由（rejected 时为拒绝原因）
                schema_version INTEGER NOT NULL DEFAULT 1,  -- 结构版本（迁移用）
                UNIQUE(cmd_code, partner_code, period, flow_code, reporter_code, cache_key)
            )
        """)
        # 迁移：旧表缺血缘列时无损重建（新建→复制→换名，不丢数据；
        # SQLite ADD COLUMN 不能加 UNIQUE 约束，故整表重建）
        cols = [r[1] for r in conn.execute("PRAGMA table_info(trade_cache)").fetchall()]
        if "source" not in cols or "quality" not in cols or "schema_version" not in cols:
            conn.execute("ALTER TABLE trade_cache RENAME TO trade_cache_legacy")
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
                    source TEXT NOT NULL DEFAULT '',
                    raw_record_count INTEGER NOT NULL DEFAULT 0,
                    clean_record_count INTEGER NOT NULL DEFAULT 0,
                    quality TEXT NOT NULL DEFAULT 'valid',
                    validation_reason TEXT NOT NULL DEFAULT '',
                    schema_version INTEGER NOT NULL DEFAULT 1,
                    UNIQUE(cmd_code, partner_code, period, flow_code, reporter_code, cache_key)
                )
            """)
            conn.execute(
                """INSERT OR IGNORE INTO trade_cache
                   (cmd_code, partner_code, period, flow_code, reporter_code, cache_key, data_json, fetched_at)
                   SELECT cmd_code, partner_code, period, flow_code, reporter_code, cache_key, data_json, fetched_at
                   FROM trade_cache_legacy""")
            conn.execute("DROP TABLE trade_cache_legacy")
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
        # 线索销售漏斗（v1.0.2 业务收口）：线索从生成到成交的状态管理
        conn.execute("""
            CREATE TABLE IF NOT EXISTS leads_funnel (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product TEXT NOT NULL,
                country TEXT NOT NULL,
                company TEXT NOT NULL,
                business_scope TEXT DEFAULT '',
                size_signal TEXT DEFAULT '',
                match_reason TEXT DEFAULT '',
                source_url TEXT DEFAULT '',
                status TEXT NOT NULL DEFAULT 'new',  -- new/sent/replied/quoted/won/lost
                note TEXT DEFAULT '',                -- 跟进备注
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(product, country, company, source_url)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_leads_funnel_status ON leads_funnel(status)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_leads_funnel_updated ON leads_funnel(updated_at)")
        # 我的市场订阅（v1.0.2 业务收口）：关注 (产品, 市场) 组合，定期看变化
        conn.execute("""
            CREATE TABLE IF NOT EXISTS market_watch (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product TEXT NOT NULL,
                market TEXT NOT NULL,
                reporter TEXT NOT NULL DEFAULT '中国',
                last_value REAL,             -- 上次快照出口额（美元）
                last_year TEXT,              -- 上次快照年份
                last_fetched_at TEXT,        -- 上次刷新时间
                created_at TEXT NOT NULL,
                UNIQUE(product, market, reporter)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_market_watch_updated ON market_watch(last_fetched_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_report_history ON report_history(report_type, product, country)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_access_log ON access_log(result, created_at)")
        conn.commit()


def cleanup_expired_cache(trade_max_days: int = 365, history_max_days: int = 90):
    """启动时清理过期缓存行（保守下限：读取侧还有更严格的动态 TTL）

    回归修复：一并清理 access_log / query_log / admin_sessions（此前只清两张缓存表，
    三张日志/会话表随使用无限增长导致 db 膨胀）。
    """
    with contextlib.closing(get_conn()) as conn:
        cutoff = (datetime.now() - timedelta(days=trade_max_days)).isoformat()
        conn.execute("DELETE FROM trade_cache WHERE fetched_at < ?", (cutoff,))
        cutoff_h = (datetime.now() - timedelta(days=history_max_days)).isoformat()
        conn.execute("DELETE FROM report_history WHERE created_at < ?", (cutoff_h,))
        conn.execute("DELETE FROM query_log WHERE created_at < ?", (cutoff_h,))
        conn.execute("DELETE FROM access_log WHERE created_at < ?", (cutoff_h,))
        # 过期管理员会话（TTL 天数对齐 ADMIN_SESSION_TTL_DAYS）
        import config as _cfg
        cutoff_admin = (datetime.now() - timedelta(days=_cfg.ADMIN_SESSION_TTL_DAYS)).isoformat()
        conn.execute("DELETE FROM admin_sessions WHERE expires_at < ?", (cutoff_admin,))
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


def get_cache_meta(cmd_code: str, partner_code: str, period: str, flow_code: str,
                   reporter_code: str = "156", cache_key: str = "") -> dict | None:
    """查缓存血缘元数据（quality/source/记录数/校验理由）——数据血缘追溯用

    未命中返回 None；命中返回 {source, quality, validation_reason,
    raw_record_count, clean_record_count, fetched_at}。
    """
    with contextlib.closing(get_conn()) as conn:
        row = conn.execute(
            """SELECT source, quality, validation_reason, raw_record_count,
                      clean_record_count, fetched_at
               FROM trade_cache WHERE cmd_code=? AND partner_code=? AND period=?
               AND flow_code=? AND reporter_code=? AND cache_key=?""",
            (cmd_code, partner_code, period, flow_code, reporter_code, cache_key),
        ).fetchone()
    if not row:
        return None
    return {
        "source": row["source"],
        "quality": row["quality"],
        "validation_reason": row["validation_reason"],
        "raw_record_count": row["raw_record_count"],
        "clean_record_count": row["clean_record_count"],
        "fetched_at": row["fetched_at"],
    }


def save_cache(cmd_code: str, partner_code: str, period: str, flow_code: str,
               data: list, reporter_code: str = "156", cache_key: str = "",
               source: str = "", raw_count: int = 0, clean_count: int = 0,
               quality: str = "valid", validation_reason: str = ""):
    """写入缓存（存在则更新）；data 可为空列表（空结果也缓存，避免重复打 API）

    血缘字段（v1.0.2）：source 数据源、raw_count 原始行数、clean_count 清洗后行数、
    quality 四态质量（valid/suspicious/invalid/rejected）、validation_reason 判定理由。
    缺省值向后兼容旧调用。
    """
    with _write_lock:
        with contextlib.closing(get_conn()) as conn:
            conn.execute(
                """INSERT INTO trade_cache (cmd_code, partner_code, period, flow_code, reporter_code, cache_key, data_json, fetched_at, source, raw_record_count, clean_record_count, quality, validation_reason)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(cmd_code, partner_code, period, flow_code, reporter_code, cache_key)
                   DO UPDATE SET data_json=excluded.data_json, fetched_at=excluded.fetched_at,
                                 source=excluded.source, raw_record_count=excluded.raw_record_count,
                                 clean_record_count=excluded.clean_record_count,
                                 quality=excluded.quality, validation_reason=excluded.validation_reason""",
                (cmd_code, partner_code, period, flow_code, reporter_code, cache_key,
                 json.dumps(data, ensure_ascii=False), datetime.now().isoformat(),
                 source, raw_count, clean_count, quality, validation_reason),
            )
            conn.commit()


def log_query(product: str, hs_code: str, target: str):
    """记录查询历史（为第三版报告回看铺垫）"""
    with _write_lock:
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
    with _write_lock:
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
    with _write_lock:
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
    with _write_lock:
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
    with _write_lock:
        with contextlib.closing(get_conn()) as conn:
            conn.execute("DELETE FROM admin_sessions WHERE token=?", (token,))
            conn.commit()


# ═══ 线索销售漏斗（v1.0.2 业务收口）═══
# 状态流转：new（新线索）→ sent（已发开发信）→ replied（已回复）
#           → quoted（已报价）→ won（已成交）/ lost（已放弃）
LEAD_STATUSES = ("new", "sent", "replied", "quoted", "won", "lost")

# 允许的状态流转（防跳级/回退失控；won/lost 为终态）
LEAD_TRANSITIONS = {
    "new": ("sent", "lost"),
    "sent": ("replied", "quoted", "lost"),
    "replied": ("quoted", "sent", "lost"),
    "quoted": ("won", "lost", "replied"),
    "won": (),
    "lost": ("new",),  # 放弃后允许重新激活
}


def save_leads_to_funnel(product: str, country: str, leads: list) -> int:
    """把检索到的线索批量存入漏斗（同 (product,country,company,url) 去重，保留原状态）"""
    init_db()
    added = 0
    with _write_lock:
        with contextlib.closing(get_conn()) as conn:
            now = datetime.now().isoformat()
            for ld in leads:
                if not isinstance(ld, dict):
                    continue
                company = (ld.get("company") or "").strip()[:100]
                if not company:
                    continue
                cur = conn.execute(
                    "SELECT id FROM leads_funnel WHERE product=? AND country=? AND company=? AND source_url=?",
                    (product, country, company, (ld.get("source_url") or "").strip()[:300]),
                ).fetchone()
                if cur:
                    continue  # 已存在：保留原状态（不重置漏斗进度）
                conn.execute(
                    """INSERT INTO leads_funnel
                       (product, country, company, business_scope, size_signal, match_reason, source_url,
                        status, note, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, 'new', '', ?, ?)""",
                    (product, country, company,
                     (ld.get("business_scope") or "").strip()[:200],
                     (ld.get("size_signal") or "").strip()[:100],
                     (ld.get("match_reason") or "").strip()[:200],
                     (ld.get("source_url") or "").strip()[:300],
                     now, now),
                )
                added += 1
            conn.commit()
    return added


def list_funnel_leads(status: str = "", product: str = "", country: str = "",
                      limit: int = 200) -> list:
    """漏斗线索列表（可按状态/产品/市场筛选，updated_at 降序）"""
    init_db()
    sql = "SELECT * FROM leads_funnel WHERE 1=1"
    args = []
    if status:
        sql += " AND status=?"
        args.append(status)
    if product:
        sql += " AND product=?"
        args.append(product)
    if country:
        sql += " AND country=?"
        args.append(country)
    sql += " ORDER BY updated_at DESC, id DESC LIMIT ?"
    args.append(limit)
    with contextlib.closing(get_conn()) as conn:
        rows = conn.execute(sql, args).fetchall()
    return [dict(r) for r in rows]


def funnel_stats() -> dict:
    """漏斗统计：各状态数量（销售管道看板数据）"""
    init_db()
    with contextlib.closing(get_conn()) as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS n FROM leads_funnel GROUP BY status").fetchall()
    stats = {s: 0 for s in LEAD_STATUSES}
    for r in rows:
        if r["status"] in stats:
            stats[r["status"]] = r["n"]
    return stats


def update_lead_status(lead_id: int, new_status: str, note: str = "") -> bool:
    """线索状态流转：校验允许的转移 + 记录更新时间；note 留空则不改备注"""
    if new_status not in LEAD_STATUSES:
        raise ValueError(f"非法状态: {new_status}")
    init_db()
    with _write_lock:
        with contextlib.closing(get_conn()) as conn:
            row = conn.execute("SELECT status FROM leads_funnel WHERE id=?", (lead_id,)).fetchone()
            if not row:
                return False
            cur = row["status"]
            if new_status not in LEAD_TRANSITIONS.get(cur, ()):
                raise ValueError(f"不允许从 {cur} 直接流转到 {new_status}")
            note_sql = ", note=?" if note.strip() else ""
            note_args = (note.strip()[:500],) if note.strip() else ()
            conn.execute(
                f"UPDATE leads_funnel SET status=?, updated_at=?{note_sql} WHERE id=?",
                (new_status, datetime.now().isoformat()) + note_args + (lead_id,),
            )
            conn.commit()
    return True


def delete_lead(lead_id: int) -> bool:
    """删除一条线索"""
    init_db()
    with _write_lock:
        with contextlib.closing(get_conn()) as conn:
            cur = conn.execute("DELETE FROM leads_funnel WHERE id=?", (lead_id,))
            conn.commit()
    return cur.rowcount > 0


# ═══ 我的市场订阅（v1.0.2 业务收口）═══

def add_market_watch(product: str, market: str, reporter: str = "中国") -> bool:
    """添加关注 (产品, 市场)；已存在返回 False"""
    init_db()
    with _write_lock:
        with contextlib.closing(get_conn()) as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO market_watch (product, market, reporter, created_at) VALUES (?, ?, ?, ?)",
                (product.strip()[:100], market.strip()[:50], reporter.strip()[:50],
                 datetime.now().isoformat()),
            )
            conn.commit()
    return cur.rowcount > 0


def list_market_watch() -> list:
    """订阅列表（含上次快照）"""
    init_db()
    with contextlib.closing(get_conn()) as conn:
        rows = conn.execute(
            "SELECT * FROM market_watch ORDER BY last_fetched_at IS NULL DESC, last_fetched_at DESC, id DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def remove_market_watch(watch_id: int) -> bool:
    """删除订阅"""
    init_db()
    with _write_lock:
        with contextlib.closing(get_conn()) as conn:
            cur = conn.execute("DELETE FROM market_watch WHERE id=?", (watch_id,))
            conn.commit()
    return cur.rowcount > 0


def update_watch_snapshot(watch_id: int, value: float, year: str):
    """刷新快照：记录最新出口额 + 年份 + 时间"""
    init_db()
    with _write_lock:
        with contextlib.closing(get_conn()) as conn:
            conn.execute(
                "UPDATE market_watch SET last_value=?, last_year=?, last_fetched_at=? WHERE id=?",
                (value, year, datetime.now().isoformat(), watch_id),
            )
            conn.commit()
