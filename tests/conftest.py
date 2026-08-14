# -*- coding: utf-8 -*-
"""pytest 共享夹具（阶段 6.1）

- autouse：每个测试前后清空 llm 内存缓存（防缓存串扰）
- tmp_db：把 database.DB_PATH 指向临时库（不碰真实 tradepilot.db）
"""
import sys
from pathlib import Path

import pytest

# 保证项目根可导入（tests/ 与源码同级）
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import llm  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_llm_cache():
    """每个测试前清空 LLM 内存缓存（防不同测试串缓存）"""
    llm._market_cache = llm._LRUCache()
    llm._trade_trend_cache = llm._LRUCache()
    llm._compare_cache = llm._LRUCache()
    yield


@pytest.fixture
def tmp_db(tmp_path, monkeypatch):
    """数据库夹具：指向临时库并初始化"""
    import database
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "test.db"))
    database.init_db()
    return database
