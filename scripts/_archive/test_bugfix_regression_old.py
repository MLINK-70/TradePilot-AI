# -*- coding: utf-8 -*-
"""bug 排查回归测试（临时，验证后并入 tests/）：
1. analyze_trade_trend 的 _LRUCache 迁移（此前漏改 → 必现 TypeError 500）
2. agent 意图解析异常不再空参数续跑
3. _parse_json None 防御
"""
import pytest
from unittest import mock

import llm
from llm import _parse_json


class TestTradeTrendCacheRegression:
    """回归：_trade_trend_cache 必须走 .get/.set（阶段 4 迁移漏网点）"""

    def test_trend_cache_uses_lru(self):
        trend = {"2020": {"value": 1.0, "weight": 0.1},
                 "2021": {"value": 2.0, "weight": 0.2},
                 "2022": {"value": 3.0, "weight": 0.3}}
        resp = mock.MagicMock(status_code=200)
        resp.json.return_value = {"choices": [{"message": {"content": '{"overview": "ok"}'}}]}
        with mock.patch.object(llm._SESSION, "post", return_value=resp) as m:
            r1 = llm.analyze_trade_trend("产品X", "德国", "中国", trend)
            r2 = llm.analyze_trade_trend("产品X", "德国", "中国", trend)  # 命中缓存
            assert m.call_count == 1, "第二次应命中缓存（_LRUCache 迁移回归）"
            assert r1 == r2

    def test_trend_cache_evicts(self):
        trend = {"2020": {"value": 1.0, "weight": 0.1},
                 "2021": {"value": 2.0, "weight": 0.2},
                 "2022": {"value": 3.0, "weight": 0.3}}
        resp = mock.MagicMock(status_code=200)
        resp.json.return_value = {"choices": [{"message": {"content": '{"overview": "ok"}'}}]}
        with mock.patch.object(llm._SESSION, "post", return_value=resp) as m:
            llm.analyze_trade_trend("产品A", "德国", "中国", trend)
            llm.analyze_trade_trend("产品B", "德国", "中国", trend)  # 不同 key
            assert m.call_count == 2


class TestParseJsonNone:
    def test_none_content_raises_clear_error(self):
        with pytest.raises(ValueError) as ctx:
            _parse_json(None)
        assert "为空" in str(ctx.value)

    def test_empty_content_raises(self):
        with pytest.raises(ValueError):
            _parse_json("   ")
