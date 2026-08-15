# -*- coding: utf-8 -*-
import config
from collectors import _safe_url, CollectorError

def expect_reject(fn, url, why):
    try:
        fn(url)
        print(f"FAIL: {url} 被放行（{why}）")
    except (ValueError, CollectorError) as e:
        print(f"OK  拒绝: {url} -> {e}")

def expect_pass(fn, url):
    try:
        fn(url)
        print(f"OK  放行: {url}")
    except (ValueError, CollectorError) as e:
        print(f"FAIL: {url} 被误拒 -> {e}")

print("== config.validate_ai_base_url ==")
expect_reject(config.validate_ai_base_url, "http://evil.com", "http 明文")
expect_reject(config.validate_ai_base_url, "https://192.168.1.1", "私网 IP")
expect_reject(config.validate_ai_base_url, "https://127.0.0.1", "环回 IP")
expect_reject(config.validate_ai_base_url, "https://169.254.169.254/latest/meta-data", "链路本地")
expect_reject(config.validate_ai_base_url, "ftp://deepseek.com", "非 http(s)")
expect_pass(config.validate_ai_base_url, "https://api.deepseek.com/v1")
expect_pass(config.validate_ai_base_url, "https://api.openai.com/v1")

print("== collectors._safe_url ==")
expect_reject(_safe_url, "http://a.com", "http 明文")
expect_reject(_safe_url, "https://127.0.0.1/x", "环回")
expect_reject(_safe_url, "https://10.0.0.1/x", "私网")
expect_reject(_safe_url, "https://169.254.169.254/x", "链路本地")
expect_pass(_safe_url, "https://www.amazon.com/dp/B0XXXX")
