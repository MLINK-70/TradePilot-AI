"""llm.py — DeepSeek API 调用层：请求、JSON 解析、错误处理"""
import json
import logging
import time
from functools import lru_cache

import requests

from config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL
from prompts import SYSTEM_PROMPT, build_user_prompt


def _chat(messages: list, use_json: bool = True) -> str:
    """通用 DeepSeek 请求：直连 + 重试 + 超时兜底，返回文本内容"""
    if not DEEPSEEK_API_KEY:
        raise ValueError("未配置 DEEPSEEK_API_KEY，请检查 .env 文件")

    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": messages,
        "temperature": 0.7,
    }
    if use_json:
        payload["response_format"] = {"type": "json_object"}

    for attempt in range(2):
        try:
            resp = requests.post(
                f"{DEEPSEEK_BASE_URL}/chat/completions",
                headers={
                    "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                    "Content-Type": "application/json",
                    "Connection": "close",
                },
                json=payload,
                timeout=60,
                proxies={"http": None, "https": None},  # 强制直连
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
        except requests.exceptions.Timeout as e:
            if attempt == 1:
                raise ValueError("DeepSeek API 请求超时（60 秒），请稍后重试")
            logging.warning("DeepSeek 请求超时，3 秒后自动重试: %s", e)
            time.sleep(3)
        except requests.exceptions.HTTPError as e:
            code = e.response.status_code if e.response is not None else "?"
            raise ValueError(f"DeepSeek API 返回错误：{code}，可能是余额不足或 Key 无效")
        except requests.exceptions.RequestException as e:
            if attempt == 1:
                raise ValueError(f"DeepSeek API 网络错误（重试后仍失败）：{e}")
            logging.warning("DeepSeek 请求失败，3 秒后自动重试: %s", e)
            time.sleep(3)


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


@lru_cache(maxsize=64)
def analyze_market(product: str, country: str) -> dict:
    """
    调用 DeepSeek 生成市场分析，返回结构化 JSON 字典。

    失败时抛 ValueError，由 main.py 统一转成 502。
    已加缓存：相同 (产品, 国家) 直接命中，不重复消耗 API token。
    """
    if not DEEPSEEK_API_KEY:
        raise ValueError("未配置 DEEPSEEK_API_KEY，请检查 .env 文件")

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": build_user_prompt(product, country)},
    ]
    content = _chat(messages, use_json=True)
    return _parse_json(content)


TRADE_TREND_SYSTEM = """你是资深国际贸易数据分析师。根据提供的真实出口贸易数据（来自 UN Comtrade），输出一份简明的市场解读。

输出要求：
1. 只输出合法 JSON 对象
2. 结构如下：
{
  "overview": "2-3 句话总结整体趋势（升/降/波动）",
  "highlights": ["亮点1（结合具体年份和数值）", "亮点2"],
  "risks": ["风险1（如增速放缓、波动加大）", "风险2"],
  "suggestion": "1 句行动建议（针对出口商/卖家）"
}
3. 必须基于给定数据说话，禁止编造数据；数据不足时在 overview 中说明
4. 所有内容中文输出"""


def analyze_trade_trend(product: str, target: str, reporter: str, trend: dict) -> dict:
    """AI 解读贸易趋势数据：trend = {year: {"value": 金额, "weight": 净重}}"""
    data_lines = "\n".join(
        f"{y}: {v['value']:,.0f} 美元 / {v['weight']:,.0f} 公斤" for y, v in trend.items()
    )
    user_msg = (
        f"产品: {product}\n出口国: {reporter}\n目标市场: {target}\n"
        f"逐年出口数据:\n{data_lines}\n请输出市场解读。"
    )
    content = _chat([
        {"role": "system", "content": TRADE_TREND_SYSTEM},
        {"role": "user", "content": user_msg},
    ], use_json=True)
    return _parse_json(content)
