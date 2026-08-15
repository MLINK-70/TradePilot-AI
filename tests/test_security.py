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
        # DNS rebinding 修复后 _safe_url 返回 (url, pinned_ip)：
        # 域名 → 钉扎公网 IP；IP 字面量 → pin 为 None（连接用的就是它）
        url, pin = _safe_url("https://www.amazon.com/dp/B0XXXX")
        assert url == "https://www.amazon.com/dp/B0XXXX"
        assert isinstance(pin, str) and pin.count(".") == 3  # IPv4 公网地址
        url2, pin2 = _safe_url("https://8.8.8.8/x")
        assert url2 == "https://8.8.8.8/x" and pin2 is None


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
