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
