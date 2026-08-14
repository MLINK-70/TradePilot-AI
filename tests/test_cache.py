# -*- coding: utf-8 -*-
"""缓存层测试：TTL / 空结果缓存 / 规范化 / LRU（使用临时库，不碰真实 DB）"""
import sqlite3

import pytest

from llm import _LRUCache


class TestGetCached:
    def test_basic_hit_and_ttl_expiry(self, tmp_db):
        tmp_db.save_cache("T", "0", "2020", "X", [{"v": 1}])
        assert tmp_db.get_cached("T", "0", "2020", "X") == [{"v": 1}]
        # 把 fetched_at 改到 100 天前 → ttl_days=90 应过期
        with sqlite3.connect(tmp_db.DB_PATH) as conn:
            conn.execute("UPDATE trade_cache SET fetched_at=? WHERE cmd_code='T'",
                         ("2020-01-01T00:00:00",))
            conn.commit()
        assert tmp_db.get_cached("T", "0", "2020", "X", ttl_days=90) is None
        # ttl=0 永不过期（兼容旧调用）
        assert tmp_db.get_cached("T", "0", "2020", "X") == [{"v": 1}]

    def test_empty_result_cached(self, tmp_db):
        tmp_db.save_cache("E", "0", "2020", "X", [])
        assert tmp_db.get_cached("E", "0", "2020", "X") == []


class TestReportHistoryNormalization:
    def test_strip_lower_normalize(self, tmp_db):
        tmp_db.save_report_history("market", " iPhone ", "德国", {"ok": 1})
        assert tmp_db.get_report_history("market", "iphone", "德国") == {"ok": 1}
        assert tmp_db.get_report_history("market", " iPhone ", " 德国 ") == {"ok": 1}


class TestLRUCache:
    def test_evicts_oldest(self):
        c = _LRUCache(maxsize=3)
        for i in range(5):
            c.set(f"k{i}", i)
        assert c.get("k0") is None
        assert c.get("k4") == 4
        assert len(c._d) == 3

    def test_get_refreshes_recency(self):
        c = _LRUCache(maxsize=3)
        for i in range(3):
            c.set(f"k{i}", i)
        c.get("k0")  # 访问 k0 → 变最新
        c.set("k3", 3)  # 淘汰最旧的 k1
        assert c.get("k0") == 0
        assert c.get("k1") is None
