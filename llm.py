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


TRADE_TREND_SYSTEM = """你是资深国际贸易数据分析师。根据提供的**已核实统计指标**（程序精确计算，来自 UN Comtrade 数据），输出一份简明的市场解读。

输出要求：
1. 只输出合法 JSON 对象
2. 结构如下：
{
  "overview": "2-3 句话总结整体趋势（升/降/波动）",
  "highlights": ["亮点1（引用给定指标的具体数值）", "亮点2"],
  "risks": ["风险1（如增速放缓、波动加大）", "风险2"],
  "suggestion": "1 句行动建议（针对出口商/卖家）"
}
3. 必须直接引用给定的指标数值，禁止自行计算或编造任何数字
4. 所有内容中文输出"""


def analyze_trade_trend(product: str, target: str, reporter: str, trend: dict, stats: dict | None = None) -> dict:
    """AI 解读贸易趋势：trend 为逐年数据，stats 为程序算好的统计指标

    AI 只负责解读（引用已核实指标），不负责算数——杜绝 AI 算术错误/幻觉。
    """
    data_lines = "\n".join(
        f"{y}: {v['value']:,.0f} 美元 / {v['weight']:,.0f} 公斤" for y, v in trend.items()
    )
    stats_lines = ""
    if stats:
        lines = []
        if stats.get("change_over_period_pct") is not None:
            lines.append(
                f"- 区间: {stats['first_year']}-{stats['last_year']}，"
                f"期末较期初变化 {stats['change_over_period_pct']:.1f}%"
            )
        if stats.get("cagr_pct") is not None:
            lines.append(f"- 年复合增长率: {stats['cagr_pct']}%")
        if stats.get("peak_year"):
            lines.append(f"- 峰值年份: {stats['peak_year']}，谷值年份: {stats['trough_year']}")
        if stats.get("max_swing_year") is not None and stats.get("max_swing_pct") is not None:
            lines.append(f"- 最大单年波动: {stats['max_swing_year']} 年 {stats['max_swing_pct']}%")
        prices = stats.get("unit_prices") or []
        if prices:
            lines.append("- 单价趋势: " + "; ".join(f"{p['year']}年 {p['price']:.2f} 美元/公斤" for p in prices))
        if lines:
            stats_lines = "\n已核实统计指标（程序精确计算）:\n" + "\n".join(lines)
    user_msg = (
        f"产品: {product}\n出口国: {reporter}\n目标市场: {target}\n"
        f"逐年出口数据:\n{data_lines}{stats_lines}\n请输出市场解读（引用指标数值，不自行计算）。"
    )
    content = _chat([
        {"role": "system", "content": TRADE_TREND_SYSTEM},
        {"role": "user", "content": user_msg},
    ], use_json=True)
    return _parse_json(content)
