"""llm.py 单元测试：JSON 解析 + 异常分派 + 重试策略 + 缓存"""
import unittest
from unittest import mock

import requests

import llm
from llm import _parse_json


class TestParseJson(unittest.TestCase):
    def test_plain_json(self):
        self.assertEqual(_parse_json('{"a": 1}'), {"a": 1})

    def test_fenced_lowercase(self):
        self.assertEqual(_parse_json('```json\n{"a": 1}\n```'), {"a": 1})

    def test_fenced_uppercase(self):
        self.assertEqual(_parse_json('```JSON\n{"a": 1}\n```'), {"a": 1})

    def test_top_level_array_rejected(self):
        with self.assertRaises(ValueError):
            _parse_json("[1, 2, 3]")

    def test_invalid_json_raises_friendly_error(self):
        with self.assertRaises(ValueError) as ctx:
            _parse_json("不是 JSON")
        self.assertIn("不是合法 JSON", str(ctx.exception))


class TestAnalyzeMarket(unittest.TestCase):
    def _mock_post(self, side_effect=None, content=None):
        m = mock.patch("llm.requests.post")
        patched = m.start()
        if side_effect:
            patched.side_effect = side_effect
        else:
            patched.return_value = mock.MagicMock(
                json=lambda: {"choices": [{"message": {"content": content}}]},
                status_code=200,
            )
        self.addCleanup(m.stop)
        return patched

    def test_http_error_no_retry(self):
        m = self._mock_post(side_effect=requests.exceptions.HTTPError("401"))
        with self.assertRaises(ValueError) as ctx:
            llm.analyze_market("蓝牙耳机-401", "德国")  # 不同参数避免缓存串扰
        self.assertIn("余额不足或 Key 无效", str(ctx.exception))
        self.assertEqual(m.call_count, 1)  # 不重试

    def test_timeout_retries_once(self):
        m = self._mock_post(side_effect=requests.exceptions.Timeout("timeout"))
        with self.assertRaises(ValueError) as ctx:
            llm.analyze_market("蓝牙耳机-超时", "德国")
        self.assertIn("超时", str(ctx.exception))
        self.assertEqual(m.call_count, 2)  # 重试 1 次

    def test_network_error_retries_once(self):
        m = self._mock_post(side_effect=requests.exceptions.ConnectionError("conn"))
        with self.assertRaises(ValueError) as ctx:
            llm.analyze_market("蓝牙耳机-断连", "德国")
        self.assertIn("网络错误", str(ctx.exception))
        self.assertEqual(m.call_count, 2)

    def test_success_parses_dict(self):
        content = '{"market_size": {"value": "x"}, "summary": "ok"}'
        m = self._mock_post(content=content)
        data = llm.analyze_market("蓝牙耳机-成功", "德国")
        self.assertIsInstance(data, dict)
        self.assertIn("market_size", data)

    def test_cache_hits_second_call(self):
        content = '{"summary": "ok"}'
        m = self._mock_post(content=content)
        llm.analyze_market("蓝牙耳机-缓存", "德国")
        llm.analyze_market("蓝牙耳机-缓存", "德国")  # 第二次应命中缓存
        self.assertEqual(m.call_count, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
