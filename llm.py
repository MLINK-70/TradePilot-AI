"""llm.py — DeepSeek API 调用层：请求、JSON 解析、错误处理"""
import json
import logging

import time

import requests

from config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL
from prompts import SYSTEM_PROMPT, build_user_prompt


def _parse_json(content: str) -> dict:
    """把 DeepSeek 返回的文本解析为 JSON 对象。

    优先直接解析；失败则剥离 markdown 围栏（大小写都处理）再试；
    顶层必须是 dict（防止返回合法数组导致渲染端 500）。
    """
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        stripped = content.strip()
        if stripped.startswith("```"):
            stripped = stripped.strip("`").strip()
            if stripped.lower().startswith("json"):
                stripped = stripped[4:].strip()
        try:
            data = json.loads(stripped)
        except json.JSONDecodeError:
            raise ValueError("DeepSeek 返回内容不是合法 JSON，请重试")

    if not isinstance(data, dict):
        raise ValueError("DeepSeek 返回结构异常（非 JSON 对象），请重试")
    return data


def analyze_market(product: str, country: str) -> dict:
    """
    调用 DeepSeek 生成市场分析，返回结构化 JSON 字典。

    失败时抛 ValueError，由 main.py 统一转成 502。
    """
    if not DEEPSEEK_API_KEY:
        raise ValueError("未配置 DEEPSEEK_API_KEY，请检查 .env 文件")

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": build_user_prompt(product, country)},
    ]

    # DeepSeek 是国内 API，强制直连不走代理（否则梯子 TUN 模式
    # 会劫持流量导致并发时 "Response ended prematurely"）
    for attempt in range(2):
        try:
            resp = requests.post(
                f"{DEEPSEEK_BASE_URL}/chat/completions",
                headers={
                    "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                    "Content-Type": "application/json",
                    "Connection": "close",
                },
                json={
                    "model": DEEPSEEK_MODEL,
                    "messages": messages,
                    "temperature": 0.7,
                    "response_format": {"type": "json_object"},  # 强制 JSON 输出
                },
                timeout=60,
                proxies={"http": None, "https": None},  # 强制直连
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            break
        except (requests.exceptions.Timeout, requests.exceptions.RequestException) as e:
            # 超时和网络错误都重试（瞬时抖动概率高），间隔 3 秒跨过限流窗口
            if attempt == 1:
                if isinstance(e, requests.exceptions.Timeout):
                    raise ValueError("DeepSeek API 请求超时（60 秒），请稍后重试")
                raise ValueError(f"DeepSeek API 网络错误（重试后仍失败）：{e}")
            logging.warning("DeepSeek 请求失败，3 秒后自动重试: %s", e)
            time.sleep(3)
        except requests.exceptions.HTTPError:
            raise ValueError(f"DeepSeek API 返回错误：{resp.status_code}，可能是余额不足或 Key 无效")

    return _parse_json(content)
