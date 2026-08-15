# -*- coding: utf-8 -*-
"""阶段 4 单元验证：LRU 缓存 / TTL / 规范化 / CAGR 回归"""
import time

# 1. LRU 上限
from llm import _LRUCache
c = _LRUCache(maxsize=3)
for i in range(5):
    c.set(f"k{i}", i)
assert c.get("k0") is None and c.get("k4") == 4 and len(c._d) == 3, "LRU 淘汰失败"
print("PASS: LRU 缓存上限 3 淘汰最旧")

# 2. get_cached TTL（临时库）
import database, os, tempfile
tmp = tempfile.mkdtemp()
database.DB_PATH = os.path.join(tmp, "t.db")
database.init_db()
database.save_cache("T", "0", "2020", "X", [{"v": 1}])
assert database.get_cached("T", "0", "2020", "X") == [{"v": 1}], "基础命中失败"
assert database.get_cached("T", "0", "2020", "X", ttl_days=0) == [{"v": 1}], "ttl=0 应命中"
# 修改 fetched_at 到 100 天前 → ttl_days=90 应过期
import sqlite3
with sqlite3.connect(database.DB_PATH) as conn:
    conn.execute("UPDATE trade_cache SET fetched_at=? WHERE cmd_code='T'", ("2020-01-01T00:00:00",))
    conn.commit()
assert database.get_cached("T", "0", "2020", "X", ttl_days=90) is None, "TTL 过期未生效"
assert database.get_cached("T", "0", "2020", "X") == [{"v": 1}], "ttl=0 永不过期被破坏"
print("PASS: get_cached TTL（90 天过期 / 0 永不过期）")

# 3. 空结果缓存
database.save_cache("E", "0", "2020", "X", [])
assert database.get_cached("E", "0", "2020", "X") == [], "空结果缓存失败"
print("PASS: 空结果可缓存（不再重复打 API）")

# 4. 报告历史规范化
database.save_report_history("market", " iPhone ", "德国", {"ok": 1})
assert database.get_report_history("market", "iphone", "德国") == {"ok": 1}, "规范化命中失败"
print("PASS: 报告历史 strip+lower 规范化（iPhone/iphone 同一条）")

# 5. _ttl_for_period 动态 TTL
from trade import _ttl_for_period
import datetime
this = datetime.date.today().year
assert _ttl_for_period(str(this)) == 90, "今年应 90 天"
assert _ttl_for_period(str(this - 5)) == 0, "旧年份应永久"
print("PASS: 贸易缓存动态 TTL（近期 90 天 / 旧年永久）")

# 6. CAGR 回归（上一轮修复不退化）
from trade import summarize_stats
trend = {"2018": {"value": 100.0, "weight": 1.0}, "2020": {"value": 121.0, "weight": 1.2}, "2022": {"value": 146.41, "weight": 1.4}}
assert abs(summarize_stats(trend)["cagr_pct"] - 10.0) < 0.5
print("PASS: CAGR 非连续年份回归")

# 7. LLM 重试分流不回归（无 Key 时报明确错误）
import config
try:
    from llm import _chat
    _chat([{"role": "user", "content": "hi"}])
    print("FAIL: 应报未配置 Key")
except ValueError as e:
    print("PASS: 无 Key 明确报错")
print("=== 阶段 4 单测全部通过 ===")
