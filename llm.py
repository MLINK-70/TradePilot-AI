"""llm.py — DeepSeek API 调用层：请求、JSON 解析、错误处理"""
import json
import logging
import time
from functools import lru_cache

import requests

import config as cfg  # 模块引用：set_key 后运行时读新值
from prompts import SYSTEM_PROMPT, build_user_prompt


def _chat(messages: list, use_json: bool = True) -> str:
    """多提供商 LLM 请求：直连 + 重试 + 超时兜底，返回文本内容

    支持：deepseek（默认）/ gpt（OpenAI 兼容）/ claude / custom（任意 OpenAI 兼容接口）。
    提供商由 config 的 AI_PROVIDER 决定，.env 可配 AI_BASE_URL / AI_MODEL / AI_API_KEY。
    """
    provider = cfg.AI_PROVIDER
    api_key = cfg.AI_API_KEY
    base_url = cfg.AI_BASE_URL.rstrip("/")
    model = cfg.AI_MODEL
    if not api_key:
        raise ValueError("未配置 AI_API_KEY（或 DEEPSEEK_API_KEY），请检查 .env 文件")

    headers = {
        "Content-Type": "application/json",
        "Connection": "close",
    }

    if provider == "claude":
        # Anthropic 格式：Authorization: Bearer + anthropic-version 头，system 单独传
        headers["x-api-key"] = api_key
        headers["anthropic-version"] = "2023-06-01"
        payload = {
            "model": model,
            "max_tokens": 4096,
            "messages": [m for m in messages if m["role"] != "system"],
        }
        system_text = "\n".join(m["content"] for m in messages if m["role"] == "system")
        if system_text:
            payload["system"] = system_text
        url = f"{base_url}/messages"
    else:
        # OpenAI 兼容（deepseek/gpt/custom）
        headers["Authorization"] = f"Bearer {api_key}"
        payload = {
            "model": model,
            "messages": messages,
            "temperature": 0.7,
        }
        if use_json:
            payload["response_format"] = {"type": "json_object"}
        url = f"{base_url}/chat/completions"

    for attempt in range(2):
        try:
            resp = requests.post(
                url,
                headers=headers,
                json=payload,
                timeout=60,
                proxies={"http": None, "https": None},  # 强制直连
            )
            resp.raise_for_status()
            data = resp.json()
            if provider == "claude":
                return "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
            return data["choices"][0]["message"]["content"]
        except requests.exceptions.Timeout as e:
            if attempt == 1:
                raise ValueError("AI 请求超时（60 秒），请稍后重试")
            logging.warning("AI 请求超时，3 秒后自动重试: %s", e)
            time.sleep(3)
        except requests.exceptions.HTTPError as e:
            code = e.response.status_code if e.response is not None else "?"
            raise ValueError(f"AI API 返回错误：{code}，可能是余额不足或 Key 无效")
        except requests.exceptions.RequestException as e:
            if attempt == 1:
                raise ValueError(f"AI API 网络错误（重试后仍失败）：{e}")
            logging.warning("AI 请求失败，3 秒后自动重试: %s", e)
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


# 手动缓存（market_context 是 dict 不可哈希，lru_cache 无法直接用）
_market_cache: dict = {}


def _market_cache_key(product: str, country: str,
                      market_context: dict | None,
                      trade_evidence: dict | None,
                      competitiveness: dict | None,
                      background: dict | None,
                      landscape: dict | None = None) -> tuple:
    """缓存 key：产品+国家+证据链签名（证据链变化时缓存失效重算）"""
    def _sig(d):
        if not d:
            return None
        if "trend" in d:  # trade_evidence
            return ("trade", tuple(sorted(d.get("trend", {}).items())))
        if "tc" in d:     # competitiveness
            return ("tc", d.get("tc"))
        if "summary" in d:  # background
            return ("bg", str(d.get("summary", ""))[:80])
        if "top_brands" in d:  # landscape
            return ("land", tuple(sorted((b.get("name", ""), b.get("share", "")) for b in d.get("top_brands", []))))
        if "gdp" in d:    # market_context
            return ("ctx", d.get("gdp"), d.get("gdp_per_capita"),
                    d.get("population"), d.get("internet"))
        return None
    # AI 提供商/模型签名：切换提供商或模型时旧缓存自动失效
    ai_sig = (cfg.AI_PROVIDER, cfg.AI_MODEL)
    return (product, country, ai_sig, _sig(market_context), _sig(trade_evidence),
            _sig(competitiveness), _sig(background), _sig(landscape))


def analyze_market(product: str, country: str, market_context: dict | None = None,
                   trade_evidence: dict | None = None,
                   competitiveness: dict | None = None,
                   background: dict | None = None,
                   landscape: dict | None = None) -> dict:
    """
    调用 DeepSeek 生成市场分析，返回结构化 JSON 字典。

    失败时抛 ValueError，由 main.py 统一转成 502。
    手动缓存：相同 (产品, 国家) + 证据链签名直接命中，不重复消耗 API token。
    market_context: World Bank 市场环境数据（可选）
    trade_evidence: 真实贸易数据（UN Comtrade，可选）
    competitiveness: 竞争力指标 TC（可选）
    background: 全球宏观背景（WTO 展望，可选）
    landscape: 竞争格局（龙头品牌/份额，可选）
    """
    if not cfg.RUNTIME_KEYS.get("AI_API_KEY"):
        raise ValueError("未配置 AI_API_KEY（或 DEEPSEEK_API_KEY），请检查 .env 文件")

    cache_key = _market_cache_key(product, country, market_context, trade_evidence,
                                  competitiveness, background)
    if cache_key in _market_cache:
        return _market_cache[cache_key]

    user_prompt = build_user_prompt(product, country)
    evidence_lines = []

    # 竞争格局（龙头品牌/份额/变动原因）
    if landscape and landscape.get("top_brands"):
        brands_str = "、".join(
            f"{b.get('name', '')}（{b.get('share', '')}）" for b in landscape.get("top_brands", [])[:5]
        )
        shift_str = "；".join(landscape.get("shift_reasons", [])[:3])
        chain_str = landscape.get("chain_insight", "")
        landscape_line = (
            f"【竞争格局（{landscape.get('_source', '行业检索')}）】龙头品牌: {brands_str}"
        )
        if shift_str:
            landscape_line += f"；格局变动原因: {shift_str}"
        if chain_str:
            landscape_line += f"；产业链: {chain_str}"
        evidence_lines.append(landscape_line)

    # 宏观背景（WTO 全球贸易展望）
    if background and background.get("summary"):
        evidence_lines.append(
            f"【全球宏观背景（{background.get('_source', 'WTO')}）】"
            f"全球贸易增长预测 {background.get('global_trade_growth', '')}；"
            f"驱动因素：{'、'.join(background.get('key_drivers', [])[:2])}；"
            f"风险：{'、'.join(background.get('key_risks', [])[:2])}；"
            f"趋势：{'、'.join(background.get('trends', [])[:2])}"
        )

    # 贸易数据（UN Comtrade 真实出口额）
    if trade_evidence and trade_evidence.get("trend"):
        trend = trade_evidence["trend"]
        years = sorted(trend.keys())
        trend_str = "、".join(f"{y}年 {trend[y]} 亿美元" for y in years)
        evidence_lines.append(f"【真实贸易数据（UN Comtrade）】{product} 出口至 {country}：{trend_str}")

    # 市场环境（World Bank）
    if market_context and market_context.get("available"):
        env = []
        if market_context.get("gdp"):
            env.append(f"GDP {market_context['gdp'] / 1e12:.2f} 万亿美元")
        if market_context.get("population"):
            env.append(f"人口 {market_context['population'] / 1e8:.2f} 亿")
        if market_context.get("gdp_per_capita"):
            env.append(f"人均 GDP {market_context['gdp_per_capita']:,.0f} 美元")
        if env:
            evidence_lines.append("【市场环境（World Bank）】" + "，".join(env))

    # 竞争力指标
    if competitiveness and competitiveness.get("available") and competitiveness.get("tc") is not None:
        evidence_lines.append(
            f"【竞争力指标】贸易竞争力指数 TC={competitiveness['tc']}（出口 "
            f"{competitiveness.get('export_value', 0) / 1e8:.2f} 亿 vs 进口 "
            f"{competitiveness.get('import_value', 0) / 1e8:.2f} 亿美元）"
        )

    if evidence_lines:
        user_prompt += "\n\n【以下为真实数据，请基于这些数据生成分析，引用具体数值支撑结论】\n" + "\n".join(evidence_lines)

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]
    content = _chat(messages, use_json=True)
    result = _parse_json(content)
    _market_cache[cache_key] = result  # 缓存结果（首次含市场环境，后续同参数复用）
    return result


TRADE_TREND_SYSTEM = """你是资深国际贸易分析师兼出海品牌策略顾问。根据提供的**已核实统计指标**（程序精确计算，来自 UN Comtrade 数据）和**竞争格局**（龙头品牌/份额/变动原因/产业链），输出一份有洞察的市场解读和**可落地的入局策略**。

输出要求：
1. 只输出合法 JSON 对象
2. 结构如下：
{
  "overview": "2-3 句话总结整体趋势（升/降/波动），引用数据时标注年份区间，如「2020-2022 年间，出口额从 X 增至 Y」",
  "highlights": ["亮点1（引用具体数值+年份，如「2021 年出口额达 7.97 亿美元，为区间峰值」）", "亮点2"],
  "risks": ["风险1（引用具体年份+数值）", "风险2"],
  "suggestion": "1 句可执行的行动建议",
  "entry_strategy": {
    "positioning": "目标定位：建议切入哪个细分市场/客群（如「避开苹果主导的高端，切 50-100 美元的中端通勤降噪」），基于竞争格局给出理由",
    "differentiation": "差异化方向：与现有龙头品牌如何差异化（功能/价格/渠道/认证/服务，2-3 句），引用竞争格局中的品牌",
    "pricing": "定价与渠道策略：建议价位段和渠道打法（线上/线下/区域分销），1-2 句",
    "opportunity": "市场机会点：结合变动原因和产业链，指出空白机会（如「国产芯片降本后中低端性价比空间」）",
    "actions": ["行动1（具体可执行，如「先以 OEM 切入德国中端市场，再推自有品牌」）", "行动2", "行动3"]
  },
  "advantage_categories": [
    {"category": "优势品类（如「高端监听耳机」）", "strength": "优势点（如「声学调校/工艺精度/工程师文化」）", "brands": "代表品牌（如「森海塞尔/拜亚动力」）", "relevance": "对目标市场的机会（如「中国高端 HiFi 人群增长」）"}
  ]
}
3. 必须直接引用给定的指标数值，禁止自行计算或编造任何数字
4. **建议必须具体可执行**：结合数据给出明确方向（如"针对 2022 年出口额下降 16.8%，建议优化 X 产品线或开拓 Y 市场"），禁止"提升产品附加值""加强市场开拓"这类空话
5. 若提供了【市场环境数据】（GDP/人口/人均），在 overview 或 highlights 中引用 1 句（如"该国人均 GDP 6 万美元，消费力支撑中高端产品"），增强结论可信度
6. 若提供了【竞争格局】（龙头品牌/份额/变动原因/产业链），在 risks、suggestion 或 entry_strategy 中引用（如"龙头品牌苹果占 25% 份额，国产芯片突破降本是入局机会"），让解读有产业逻辑
7. **entry_strategy 的视角必须跟随出口国（reporter）**：站在「出口国品牌」的角度，分析如何进入目标市场（target）：
   - 若出口国是中国：以中国品牌视角（如小米/华为/中国供应链降本优势）
   - 若出口国是德国：以德国品牌视角（如森海塞尔/拜亚动力/声学工艺优势）
   - 若出口国是日本：以日本品牌视角（如索尼/松下/精密制造优势）
   - 渠道和策略要基于**目标市场**（如进入中国用天猫/京东/线下体验店，进入德国用 Amazon.de/MediaMarkt），**结合出口国品牌的自身优势**（声学底蕴/工艺/本土渠道/文化认同）做差异化，不套模板
8. **advantage_categories 必须列出「出口国」在该品类的 3-4 个优势品类**：每个品类说明优势点（工艺/技术/品牌传统/供应链）、代表品牌（基于竞争格局或行业常识，标注明来源）、以及该品类在**目标市场**的机会点
9. 所有内容中文输出"""


# 手动缓存（trend/stats 是 dict 不可哈希，lru_cache 无法直接用）
_trade_trend_cache: dict = {}


def analyze_trade_trend(product: str, target: str, reporter: str, trend: dict, stats: dict | None = None,
                        market_context: dict | None = None, landscape: dict | None = None) -> dict:
    """AI 解读贸易趋势：trend 为逐年数据，stats 为程序算好的统计指标

    AI 只负责解读（引用已核实指标），不负责算数——杜绝 AI 算术错误/幻觉。
    手动缓存：相同查询（产品/目标/出口国/数据区间）不重复消耗 token。
    market_context: World Bank 市场环境（可选），双证据链支撑结论。
    landscape: 竞争格局（龙头品牌/变动原因/产业链，可选），深化解读。
    """
    # 缓存 key 含提示词版本签名：TRADE_TREND_SYSTEM 变更时旧缓存自动失效
    PROMPT_VER = "v2-entry-strategy"  # 提示词结构版本（改提示词需递增）
    cache_key = (product, target, reporter, tuple(trend.keys()), PROMPT_VER)
    if cache_key in _trade_trend_cache:
        return _trade_trend_cache[cache_key]

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
    # 市场环境（World Bank）注入：双证据链
    market_lines = ""
    if market_context and market_context.get("available"):
        env = []
        if market_context.get("gdp"):
            env.append(f"GDP {market_context['gdp'] / 1e12:.2f} 万亿美元")
        if market_context.get("population"):
            env.append(f"人口 {market_context['population'] / 1e8:.2f} 亿")
        if market_context.get("gdp_per_capita"):
            env.append(f"人均 GDP {market_context['gdp_per_capita']:,.0f} 美元")
        if env:
            market_lines = "\n市场环境（World Bank 官方）: " + "，".join(env)

    # 竞争格局注入：龙头品牌/份额/变动原因/产业链
    landscape_lines = ""
    if landscape:
        parts = []
        brands = landscape.get("top_brands") or []
        if brands:
            parts.append("龙头品牌: " + "；".join(
                f"{b.get('name', '')}（{b.get('share', '')}）" for b in brands[:5]))
        shifts = landscape.get("shift_reasons") or []
        if shifts:
            parts.append("格局变动原因: " + "；".join(shifts[:3]))
        if landscape.get("chain_insight"):
            parts.append("产业链洞察: " + landscape["chain_insight"])
        if landscape.get("key_insight"):
            parts.append("核心洞察: " + landscape["key_insight"])
        if parts:
            landscape_lines = "\n竞争格局（Tavily 行业检索）:\n" + "\n".join(parts)

    user_msg = (
        f"产品: {product}\n出口国: {reporter}\n目标市场: {target}\n"
        f"逐年出口数据:\n{data_lines}{stats_lines}{market_lines}{landscape_lines}\n"
        f"请输出市场解读（引用指标数值、市场环境和竞争格局数据支撑结论，不自行计算）。"
    )
    content = _chat([
        {"role": "system", "content": TRADE_TREND_SYSTEM},
        {"role": "user", "content": user_msg},
    ], use_json=True)
    result = _parse_json(content)
    _trade_trend_cache[cache_key] = result  # 缓存结果，避免重复烧 token
    return result
