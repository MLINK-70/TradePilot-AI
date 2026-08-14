# -*- coding: utf-8 -*-
"""安全校验测试（SSRF 防线 / base_url 白名单 / 配置注入拦截）"""
import pytest

import config
from collectors import CollectorError, _safe_url


class TestValidateAiBaseUrl:
    def test_reject_http_plain(self):
        with pytest.raises(ValueError):
            config.validate_ai_base_url("http://evil.com")

    def test_reject_private_ip(self):
        with pytest.raises(ValueError):
            config.validate_ai_base_url("https://192.168.1.1")

    def test_reject_loopback(self):
        with pytest.raises(ValueError):
            config.validate_ai_base_url("https://127.0.0.1")

    def test_reject_link_local(self):
        with pytest.raises(ValueError):
            config.validate_ai_base_url("https://169.254.169.254/latest/meta-data")

    def test_reject_bad_scheme(self):
        with pytest.raises(ValueError):
            config.validate_ai_base_url("ftp://deepseek.com")

    def test_accept_public_https(self):
        assert config.validate_ai_base_url("https://api.deepseek.com/v1") == "https://api.deepseek.com/v1"
        assert config.validate_ai_base_url("https://api.openai.com/v1") == "https://api.openai.com/v1"

    def test_reject_empty(self):
        with pytest.raises(ValueError):
            config.validate_ai_base_url("")


class TestSafeUrl:
    def test_reject_http(self):
        with pytest.raises(CollectorError):
            _safe_url("http://a.com")

    def test_reject_loopback(self):
        with pytest.raises(CollectorError):
            _safe_url("https://127.0.0.1/x")

    def test_reject_private(self):
        with pytest.raises(CollectorError):
            _safe_url("https://10.0.0.1/x")

    def test_reject_link_local(self):
        with pytest.raises(CollectorError):
            _safe_url("https://169.254.169.254/x")

    def test_accept_public(self):
        assert _safe_url("https://www.amazon.com/dp/B0XXXX") == "https://www.amazon.com/dp/B0XXXX"


class TestSetKeyInjection:
    def test_reject_newline_injection(self):
        with pytest.raises(ValueError):
            config.set_key("AI_API_KEY", "sk-abc\nAI_BASE_URL=https://evil.com")

    def test_reject_hash(self):
        with pytest.raises(ValueError):
            config.set_key("AI_API_KEY", "sk-abc#comment")

    def test_reject_bad_base_url(self):
        with pytest.raises(ValueError):
            config.set_key("AI_BASE_URL", "http://evil.com")

    def test_unknown_key_returns_false(self):
        assert config.set_key("NOT_A_KEY", "x") is False
