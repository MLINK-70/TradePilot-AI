"""llm.py — DeepSeek API 调用层：请求、JSON 解析、错误处理"""
import json

import requests

from config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL
from prompts import SYSTEM_PROMPT, build_user_prompt


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

    try:
        resp = requests.post(
            f"{DEEPSEEK_BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": DEEPSEEK_MODEL,
                "messages": messages,
                "temperature": 0.7,
            },
            timeout=60,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
    except requests.exceptions.Timeout:
        raise ValueError("DeepSeek API 请求超时（60 秒），请稍后重试")
    except requests.exceptions.HTTPError:
        raise ValueError(f"DeepSeek API 返回错误：{resp.status_code}，可能是余额不足或 Key 无效")
    except requests.exceptions.RequestException as e:
        raise ValueError(f"DeepSeek API 网络错误：{e}")

    # 解析 JSON：优先直接解析，失败则剥离 ```json 围栏再试一次
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        stripped = content.strip().strip("`").removeprefix("json")
        return json.loads(stripped)
