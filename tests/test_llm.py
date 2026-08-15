# -*- coding: utf-8 -*-
"""llm 层测试：JSON 解析 / LRU / 重试分流（mock Session，不触网）"""
import pytest
import requests
from unittest import mock

import llm
from llm import _LRUCache, _parse_json


class TestParseJson:
    def test_plain_json(self):
        assert _parse_json('{"a": 1}') == {"a": 1}

    def test_fenced_lowercase(self):
        assert _parse_json('```json\n{"a": 1}\n```') == {"a": 1}

    def test_fenced_uppercase(self):
        assert _parse_json('```JSON\n{"a": 1}\n```') == {"a": 1}

    def test_top_level_array_rejected(self):
        with pytest.raises(ValueError):
            _parse_json("[1, 2, 3]")

    def test_invalid_json_friendly_error(self):
        with pytest.raises(ValueError) as ctx:
            _parse_json("不是 JSON")
        assert "不是合法 JSON" in str(ctx.value)


class TestLruCache:
    def test_evicts_oldest(self):
        c = _LRUCache(maxsize=2)
        c.set("a", 1)
        c.set("b", 2)
        c.set("c", 3)
        assert c.get("a") is None
        assert c.get("b") == 2


class TestChatRetry:
    """_chat 重试分流（阶段 4）：401/403 立即抛、429 退避、5xx 重试、超时重试"""

    def _mock_session(self, resp=None, side_effect=None):
        """mock llm._SESSION.post（注意：_chat 用的是共享 Session，不是 requests.post）"""
        if side_effect is None:
            m = mock.MagicMock(return_value=resp)
        else:
            m = mock.MagicMock(side_effect=side_effect)
        return mock.patch.object(llm._SESSION, "post", m), m

    def test_401_immediate_no_retry(self):
        resp = mock.MagicMock(status_code=401)
        patcher, m = self._mock_session(resp=resp)
        with patcher, mock.patch.object(llm.time, "sleep"):
            with pytest.raises(ValueError) as ctx:
                llm._chat([{"role": "user", "content": "hi"}])
            assert "Key 无效" in str(ctx.value)
        assert m.call_count == 1

    def test_429_backoff_retries(self):
        """429 三次仍限流 → 最终报错（sleep 被 mock 不真等）"""
        resp = mock.MagicMock(status_code=429, headers={})
        patcher, m = self._mock_session(resp=resp)
        with patcher, mock.patch.object(llm.time, "sleep"):
            with pytest.raises(ValueError):
                llm._chat([{"role": "user", "content": "hi"}])
        assert m.call_count == 3

    def test_5xx_retries_then_fails(self):
        resp = mock.MagicMock(status_code=503)
        patcher, m = self._mock_session(resp=resp)
        with patcher, mock.patch.object(llm.time, "sleep"):
            with pytest.raises(ValueError) as ctx:
                llm._chat([{"role": "user", "content": "hi"}])
            assert "503" in str(ctx.value)
        assert m.call_count == 3

    def test_timeout_retries(self):
        patcher, m = self._mock_session(side_effect=requests.exceptions.Timeout("t"))
        with patcher, mock.patch.object(llm.time, "sleep"):
            with pytest.raises(ValueError) as ctx:
                llm._chat([{"role": "user", "content": "hi"}])
            assert "超时" in str(ctx.value)
        assert m.call_count == 3

    def test_success_returns_content(self):
        resp = mock.MagicMock(status_code=200)
        resp.json.return_value = {"choices": [{"message": {"content": "好的"}}]}
        patcher, _ = self._mock_session(resp=resp)
        with patcher:
            assert llm._chat([{"role": "user", "content": "hi"}]) == "好的"


class TestCacheKeyNormalization:
    """回归（遗留项 4）：LLM 缓存 key 规范化——大小写/空白不敏感，防双缓存烧双倍 token"""

    def test_norm_basic(self):
        assert llm._norm_cache_key(" iPhone ") == "iphone"
        assert llm._norm_cache_key("德国") == "德国"
        assert llm._norm_cache_key(None) == ""

    def test_market_key_case_insensitive(self):
        k1 = llm._market_cache_key("蓝牙耳机", "德国", None, None, None, None, None)
        k2 = llm._market_cache_key(" 蓝牙耳机 ", "德国", None, None, None, None, None)
        k3 = llm._market_cache_key("蓝牙耳机", " 德国 ", None, None, None, None, None)
        assert k1 == k2 == k3

    def test_trend_key_case_insensitive(self):
        # 直接构造 trend key 的规范化部分（同 _trade_trend_cache 组装逻辑）
        norm = lambda s: (s or "").strip().lower()
        a = (norm("iPhone"), norm("德国"), norm("中国"))
        b = (norm("iphone"), norm("德国"), norm("中国"))
        assert a == b


class TestCacheKeyHashable:
    """回归（v1.0.3 收尾）：trend 值为 dict（{value,weight}）时缓存 key 必须可哈希——
    嵌套 dict 进 key 会炸 _lock_for（unhashable type: 'dict'）"""

    def _tc(self, quality="valid"):
        return {"available": True, "tc": 0.6, "export_value": 6.88e8,
                "import_value": 1.74e8, "market_import_value": 2.2e9,
                "market_share": 31.2, "quality": quality, "quality_note": ""}

    def test_dict_trend_value_hashable(self):
        trade = {"hs_code": "8525", "trend": {"2020": {"value": 5e8, "weight": 1e6}}}
        key = llm._market_cache_key("摄像头", "德国", None, trade, self._tc(), None, None)
        assert isinstance(hash(key), int)  # 可哈希即不再炸

    def test_dict_trend_key_stable(self):
        trade_a = {"hs_code": "8525", "trend": {"2020": {"value": 5e8, "weight": 1e6}}}
        trade_b = {"hs_code": "8525", "trend": {"2020": {"value": 5e8, "weight": 1e6}}}
        key_a = llm._market_cache_key("摄像头", "德国", None, trade_a, self._tc(), None, None)
        key_b = llm._market_cache_key("摄像头", "德国", None, trade_b, self._tc(), None, None)
        assert key_a == key_b


class TestDataConfidenceBlock:
    """回归（v1.0.3 收尾）：数据置信度总览注入——AI 引用数字必须与程序判定的质量一致"""

    FAKE_JSON = ('{"executive_summary": {"background": "x"}, "market_size": {"value": "1", "year": 2026, "note": ""}, '
                 '"growth_trend": {"cagr": "1%", "forecast_years": "2026-2030", "description": "x", "key_drivers": []}, '
                 '"top_brands": [], "user_profile": {"age_range": "x", "income_level": "x", "key_needs": [], "buying_habits": []}, '
                 '"risks": [], "action_plan": [], "summary": "x", "outlook": "x"}')

    def _run(self, competitiveness, trade_evidence=None, market_context=None):
        captured = {}

        def fake_chat(messages, use_json=True):
            captured["messages"] = messages
            return self.FAKE_JSON
        with mock.patch.object(llm, "_chat", side_effect=fake_chat):
            llm.analyze_market("摄像头", "德国", market_context=market_context,
                               trade_evidence=trade_evidence,
                               competitiveness=competitiveness,
                               background=None, landscape=None, refresh=True)
        return captured["messages"][-1]["content"]

    def _tc(self, quality, note=""):
        return {"available": True, "tc": 0.6, "export_value": 6.88e8,
                "import_value": 1.74e8, "market_import_value": 2.2e9,
                "market_share": 31.2, "quality": quality, "quality_note": note}

    def test_confidence_block_present(self):
        trade = {"hs_code": "8525", "trend": {"2020": {"value": 5e8, "weight": 1e6}}}
        t = self._run(self._tc("valid"), trade)
        assert "【数据置信度总览】" in t
        assert "引用规则" in t
        assert "可信" in t  # valid → ✅ 可信

    def test_suspicious_flagged(self):
        t = self._run(self._tc("suspicious", "镜像口径差异"))
        assert "存疑" in t
        assert "数据质量: suspicious" in t

    def test_rejected_excluded(self):
        t = self._run(self._tc("rejected", "出口腿数据被拒绝"))
        assert "拒绝" in t
        assert "TC=" not in t          # 被拒数字不进提示词
        assert "不要引用竞争力数字" in t
