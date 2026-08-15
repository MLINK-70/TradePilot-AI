"""market_data.py — 多数据源聚合模块

World Bank API（无 Key 免费）：市场环境数据（GDP/人口/人均/互联网普及率）
——补充 UN Comtrade 之外的"市场环境"维度，让分析有理有据。

OEC / NewsAPI 预留（注册 Key 后启用，见 config）。
"""
import logging
import re
import time
from datetime import datetime, timedelta

import requests

import config as cfg
from database import get_cached, init_db, save_cache

WB_BASE = "https://api.worldbank.org/v2/country"

# World Bank 指标（ISO3 国家码 → 指标）
INDICATORS = {
    "gdp": "NY.GDP.MKTP.CD",            # GDP 现价美元
    "population": "SP.POP.TOTL",        # 人口
    "gdp_per_capita": "NY.GDP.PCAP.CD", # 人均 GDP
    "internet": "IT.NET.USER.ZS",       # 互联网普及率 %
    "cpi": "FP.CPI.TOTL.ZG",            # 通胀率 CPI（年变化 %）
    "exchange_rate": "PA.NUS.FCRF",     # 官方汇率（本币/美元）
    "high_tech_exports": "TX.VAL.TECH.MF.ZS",  # 高科技出口占制成品出口 %
    "mobile": "IT.CEL.SETS.P2",         # 每百人手机订阅
}

# 国家名 → ISO3 码（World Bank 用 ISO3；内置主要 50 国）
COUNTRY_ISO3 = {
    "中国": "CHN", "德国": "DEU", "美国": "USA", "日本": "JPN", "英国": "GBR",
    "法国": "FRA", "荷兰": "NLD", "意大利": "ITA", "韩国": "KOR", "印度": "IND",
    "巴西": "BRA", "俄罗斯": "RUS", "澳大利亚": "AUS", "加拿大": "CAN",
    "西班牙": "ESP", "墨西哥": "MEX", "奥地利": "AUT", "比利时": "BEL",
    "波兰": "POL", "瑞典": "SWE", "瑞士": "CHE", "葡萄牙": "PRT", "希腊": "GRC",
    "土耳其": "TUR", "越南": "VNM", "泰国": "THA", "马来西亚": "MYS",
    "新加坡": "SGP", "印度尼西亚": "IDN", "菲律宾": "PHL", "以色列": "ISR",
    "阿联酋": "ARE", "沙特阿拉伯": "SAU", "埃及": "EGY", "南非": "ZAF",
    "尼日利亚": "NGA", "阿根廷": "ARG", "智利": "CHL", "哥伦比亚": "COL",
    "挪威": "NOR", "丹麦": "DNK", "芬兰": "FIN", "爱尔兰": "IRL",
    "新西兰": "NZL", "捷克": "CZE", "匈牙利": "HUN", "罗马尼亚": "ROU",
    "乌克兰": "UKR", "哈萨克斯坦": "KAZ", "孟加拉国": "BGD",
}


def get_worldbank(iso3: str, indicator: str, year: int = 0) -> float | None:
    """查 World Bank 单指标最新值（year=0 表示取最新；带缓存，90 天 TTL）

    CPI/高科技出口/手机订阅等每年更新的指标不能永久缓存（去年查过的
    国家今年还是旧值）。90 天过期后重新拉取。
    """
    init_db()
    # 缓存 key 含年份（year=0 取最新用 "latest" 占位）：不同年份查询不再串缓存（B 类审查 #2）
    cache_key = f"WB:{iso3}:{indicator}:{year if year else 'latest'}"
    # TTL 直接交给 get_cached（90 天），删掉手工 fetched_at 查询与手开连接（回归修复）
    cached = get_cached(cache_key, "0", "0", "X", "META", ttl_days=90)
    if cached:
        return cached[0].get("value")

    url = f"{WB_BASE}/{iso3}/indicator/{INDICATORS[indicator]}"
    params = {"format": "json", "per_page": 1}
    if year:
        params["date"] = str(year)
    try:
        # World Bank 国内直连（不走梯子代理，否则 ProxyError）
        resp = requests.get(url, params=params, timeout=15,
                            proxies={"http": None, "https": None})
        resp.raise_for_status()
        data = resp.json()
        if len(data) > 1 and data[1]:
            value = data[1][0].get("value")
            if value is not None:
                # 回归修复：value=None（WB 临时缺值）不写缓存，否则 90 天不重试
                save_cache(cache_key, "0", "0", "X", [{"value": value}], "META")
                return value
    except Exception as e:
        # 接口偶发超时，重试一次
        try:
            resp = requests.get(url, params=params, timeout=15,
                                proxies={"http": None, "https": None})
            resp.raise_for_status()
            data = resp.json()
            if len(data) > 1 and data[1]:
                value = data[1][0].get("value")
                if value is not None:
                    save_cache(cache_key, "0", "0", "X", [{"value": value}], "META")
                    return value
        except Exception as e2:
            logging.warning("World Bank 查询失败 %s/%s: %s", iso3, indicator, e2)
    return None


def get_worldbank_series(iso3: str, indicator: str, years: list) -> dict:
    """查 World Bank 指标多年序列（供报告趋势图；带缓存）

    一次请求拉取区间数据（per_page=100），避免逐年请求。
    返回 {year: value}，失败返回 {}（不阻断）。
    """
    if not years:
        return {}
    init_db()
    # 回归修复：缓存键用完整年份序列（原 min-max 区间会让 [2018,2022] 与 [2019,2020]
    # 等不同查询串缓存），且读取侧不过滤年份
    cache_key = f"WB_SER:{iso3}:{indicator}:{','.join(str(y) for y in sorted(set(years)))}"
    # 数据准确性：序列缓存 90 天 TTL（年度指标需刷新最新年份，防永久旧数据）
    cached = get_cached(cache_key, "0", "0", "X", "META", ttl_days=90)
    if cached:
        # 读取侧按请求年份过滤（回归修复：防御性双保险，防旧缓存多带年份）
        return {int(k): v for k, v in (cached[0].get("data") or {}).items()
                if int(k) in years}

    url = f"{WB_BASE}/{iso3}/indicator/{INDICATORS[indicator]}"
    params = {
        "format": "json",
        "per_page": 100,
        "date": f"{min(years)}:{max(years)}",
    }
    try:
        resp = requests.get(url, params=params, timeout=20,
                            proxies={"http": None, "https": None})
        resp.raise_for_status()
        data = resp.json()
        series = {}
        if len(data) > 1 and data[1]:
            for row in data[1]:
                try:
                    y = int(row.get("date"))
                    if row.get("value") is not None and y in years:
                        series[y] = row["value"]
                except (ValueError, TypeError):
                    continue
        if series:
            save_cache(cache_key, "0", "0", "X",
                       [{"data": series}], "META")
        return series
    except Exception as e:
        logging.warning("World Bank 序列查询失败 %s/%s: %s", iso3, indicator, e)
        return {}


def get_market_context(country: str) -> dict:
    """聚合市场环境：GDP/人口/人均/互联网 → dict（失败字段为 None，不阻断）

    阶段 4：8 个指标并发拉取（World Bank 免费接口无并发限制），
    原来串行最长 8×15s ≈ 2 分钟 → 一轮 ~15s。
    """
    iso3 = COUNTRY_ISO3.get(country.strip(), "")
    if not iso3:
        return {"country": country, "available": False}

    result = {"country": country, "iso3": iso3, "available": True}
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {name: pool.submit(get_worldbank, iso3, name) for name in INDICATORS}
        for name, fut in futures.items():
            try:
                result[name] = fut.result()
            except Exception as e:
                logging.warning("指标 %s 获取失败（不阻断）: %s", name, e)
                result[name] = None
    return result


# ===== 宏观背景（WTO 贸易展望，30 天增量刷新）=====
BACKGROUND_TTL_DAYS = 30  # 缓存有效期：30 天
BACKGROUND_KEY = "TRADE_BACKGROUND"
BACKGROUND_REFRESH_SYSTEM = """你是全球经济分析师。根据搜索结果，提炼全球贸易宏观背景（当前最新信息）。

输出 JSON：
{
  "global_trade_growth": "全球贸易增长率预测（如：2026年1.9%）",
  "key_drivers": ["推动因素1（如AI投资）", "推动因素2"],
  "key_risks": ["风险1（如地缘冲突）", "风险2"],
  "trends": ["趋势1（如区域化/近岸外包）", "趋势2"],
  "summary": "2-3 句话宏观背景总结（面向出口商）"
}

要求：基于搜索结果，标注时间（如"2026年3月 WTO 报告"），不编造。"""


def _search_web(query: str, max_results: int = 5, depth: str = "basic") -> list:
    """统一网页搜索封装（多提供商）

    支持：tavily（推荐·默认）/ serper（Google）/ custom（任意兼容接口）。
    返回 [{title, url, content}]，失败返回 []。
    """
    import config as cfg
    provider = cfg.SEARCH_PROVIDER
    api_key = cfg.SEARCH_API_KEY or cfg.TAVILY_API_KEY  # 回退：未单独配 SEARCH_API_KEY 时用 Tavily key
    if not api_key:
        return []
    try:
        if provider == "serper":
            resp = requests.post(
                "https://google.serper.dev/search",
                headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
                json={"q": query, "num": max_results},
                timeout=20,
                proxies={"http": None, "https": None},
            )
            resp.raise_for_status()
            data = resp.json()
            results = []
            for r in data.get("organic", [])[:max_results]:
                results.append({
                    "title": r.get("title", ""),
                    "url": r.get("link", ""),
                    "content": r.get("snippet", ""),
                })
            return results
        elif provider == "custom":
            resp = requests.post(
                cfg.SEARCH_BASE_URL.rstrip("/") + "/search",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={"query": query, "max_results": max_results},
                timeout=20,
                proxies={"http": None, "https": None},
            )
            resp.raise_for_status()
            data = resp.json()
            results = []
            for r in (data.get("results", []) or data.get("data", []))[:max_results]:
                results.append({
                    "title": r.get("title", ""),
                    "url": r.get("url", "") or r.get("link", ""),
                    "content": r.get("content", "") or r.get("snippet", ""),
                })
            return results
        else:  # tavily（默认）
            resp = requests.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": api_key,
                    "query": query,
                    "max_results": max_results,
                    "search_depth": depth,
                },
                timeout=20,
                proxies={"http": None, "https": None},
            )
            resp.raise_for_status()
            data = resp.json()
            return [
                {"title": r.get("title", ""), "url": r.get("url", ""), "content": r.get("content", "")}
                for r in data.get("results", [])[:max_results]
            ]
    except Exception as e:
        logging.warning("搜索失败 %s: %s", provider, e)
        return []


def get_trade_background(force_refresh: bool = False) -> dict:
    """宏观背景：30 天增量刷新（有更新才抓取）

    - 缓存未过期 → 直接返回（不重复搜索，省 Tavily 额度）
    - 缓存过期或缺失 → Tavily 搜索最新 WTO 贸易展望 → 更新缓存
    """
    from llm import _chat, _parse_json
    init_db()

    # 检查缓存（含抓取时间）
    cached = get_cached(BACKGROUND_KEY, "0", "0", "X", "META")
    if cached and not force_refresh:
        try:
            fetched = datetime.fromisoformat(cached[0].get("fetched_at", ""))
            if datetime.now() - fetched < timedelta(days=BACKGROUND_TTL_DAYS):
                return cached[0].get("data", {})
        except (ValueError, KeyError, IndexError):
            pass

    # 缓存过期/缺失 → 重新抓取
    if not (cfg.SEARCH_API_KEY or cfg.TAVILY_API_KEY):
        return {}
    try:
        snippets = [
            r.get("content", "")
            for r in _search_web("WTO global trade outlook latest forecast merchandise trade growth",
                                 max_results=8, depth="advanced")
        ][:6]

        content = _chat([
            {"role": "system", "content": BACKGROUND_REFRESH_SYSTEM},
            {"role": "user", "content": "搜索到的内容:\n" + "\n---\n".join(snippets)},
        ], use_json=True)
        result = _parse_json(content)
        result["_updated"] = datetime.now().isoformat()
        result["_source"] = "WTO Global Trade Outlook（Tavily 检索）"

        save_cache(BACKGROUND_KEY, "0", "0", "X",
                   [{"fetched_at": datetime.now().isoformat(), "data": result}], "META")
        return result
    except Exception as e:
        logging.warning("宏观背景刷新失败: %s", e)
        # 有旧缓存则降级返回
        if cached:
            return cached[0].get("data", {})
        return {}
# ===== 竞争格局（龙头品牌/份额，30 天增量刷新）=====
LANDSCAPE_TTL_DAYS = 30
LANDSCAPE_REFRESH_SYSTEM = """你是行业竞争分析师。根据搜索结果，提炼【产品类别】的竞争格局（龙头品牌、市场份额、细分趋势、格局变动原因）。

输出 JSON：
{
  "product_category": "产品类别（优先输出市场主流细分类目，如「微单/数码相机」而非泛泛的「相机」）",
  "top_brands": [
    {"name": "品牌名", "share": "市场份额（如 46.5%；数据缺失填「—」并注明）", "position": "地位描述（如：全球第一，单反主导）"}
  ],
  "segment_trends": ["细分趋势1（如：微单出货量是单反10倍）", "细分趋势2"],
  "shift_reasons": ["格局变动原因1（上下游/技术/需求驱动，如：国产CMOS传感器突破降低准入门槛）", "原因2", "原因3"],
  "chain_insight": "产业链洞察（上游核心组件谁主导、对格局的影响，1-2 句）",
  "key_insight": "2-3 句竞争格局核心洞察（面向出口商：谁主导、为什么在变、机会在哪）",
  "zh_summary": "中文总结"
}

要求：
- 基于搜索结果的具体数据（品牌名+份额+年份），不编造；标注数据年份（如"2024年 BCN 数据"）
- **品类细分纪律（最关键）**：若产品词是宽泛品类（如「相机」），必须聚焦该品类**市场主流细分**（如相机 → 微单/数码相机/单反）的品牌份额；
  运动相机/全景相机等**边缘细分**的品牌（如 GoPro、影石）**不得混入整体品类份额**——它们只能在 segment_trends 中作为细分提及。
- **主流品牌覆盖**：优先提炼该品类公认的头部品牌（如相机行业的佳能/索尼/尼康/富士、手机行业的三星/苹果/华为）；
  若搜索结果未覆盖这些品牌，在 key_insight 中注明「主流品牌份额数据未检索到」并说明原因（如检索结果偏向细分品类）。
- shift_reasons 要结合产业逻辑（上游传感器/芯片、技术路线转移、需求变化）
- 面向"出口商了解市场格局与变动趋势"的视角"""


def get_competitive_landscape(product: str, market: str, force_refresh: bool = False) -> dict:
    """竞争格局：产品类别的龙头品牌/份额（30 天增量刷新）"""
    from llm import _chat, _parse_json
    init_db()

    # 提示词版本签名：LANDSCAPE_REFRESH_SYSTEM 变更（如品类细分纪律 v2）时旧缓存失效
    LANDSCAPE_PROMPT_VER = "v2"
    cache_key = f"LANDSCAPE:{product}:{market}:{LANDSCAPE_PROMPT_VER}"
    cached = get_cached(cache_key, "0", "0", "X", "META")
    if cached and not force_refresh:
        try:
            fetched = datetime.fromisoformat(cached[0].get("fetched_at", ""))
            if datetime.now() - fetched < timedelta(days=LANDSCAPE_TTL_DAYS):
                return cached[0].get("data", {})
        except (ValueError, KeyError, IndexError):
            pass

    if not (cfg.SEARCH_API_KEY or cfg.TAVILY_API_KEY):
        return {}
    try:
        # 三轮检索：①主流品牌份额（含细分品类聚焦）②产业逻辑 ③主流品牌销量数据
        snippets = []
        for query in [
            f"{product} {market} 市场份额 排名 龙头 主要品牌 市占率",
            f"{product} 行业 产业链 上下游 传感器 芯片 格局 变动 原因 分析",
            f"{product} {market} 主流品牌 销量 排名 数据 报告",
        ]:
            snippets += [
                r.get("content", "")
                for r in _search_web(query, max_results=5, depth="advanced")
            ][:5]

        content = _chat([
            {"role": "system", "content": LANDSCAPE_REFRESH_SYSTEM},
            {"role": "user", "content": f"产品: {product}，市场: {market}\n搜索到的内容:\n" + "\n---\n".join(snippets[:10])},
        ], use_json=True)
        result = _parse_json(content)
        # 数据准确性：校验份额和 ≤105%（防 LLM 幻觉，如三家各 40%）
        total_share = 0.0
        for b in result.get("top_brands", []):
            s = str(b.get("share", "")).replace("％", "%").replace(",", "")
            m = re.search(r"(\d+(?:\.\d+)?)", s)
            if m:
                total_share += float(m.group(1))
        if result.get("top_brands") and total_share > 105:
            logging.error("[数据准确性] 竞争格局份额和 %.1f%% > 105%%，疑似幻觉，top_brands 置空降级",
                          total_share)
            result["top_brands"] = []
            result["key_insight"] = "竞争格局份额数据异常（份额和超过 100%），已降级，请人工核实。"
        result["_updated"] = datetime.now().isoformat()
        result["_source"] = "Tavily 行业检索"

        save_cache(cache_key, "0", "0", "X",
                   [{"fetched_at": datetime.now().isoformat(), "data": result}], "META")
        return result
    except Exception as e:
        logging.warning("竞争格局刷新失败 %s/%s: %s", product, market, e)
        if cached:
            return cached[0].get("data", {})
        return {}


def get_news(product: str, market: str) -> dict:
    """Tavily 搜索：产品+市场 相关新闻（行业动态证据链）

    返回：{headlines: [{title, url}], available: bool}
    """
    if not (cfg.SEARCH_API_KEY or cfg.TAVILY_API_KEY):
        return {"available": False}

    try:
        results = _search_web(f"{product} market {market}", max_results=5, depth="basic")
        headlines = [
            {"title": r.get("title", ""), "url": r.get("url", "")}
            for r in results[:5]
        ]
        return {"available": bool(headlines), "headlines": headlines}
    except Exception as e:
        logging.warning("Tavily 搜索失败 %s/%s: %s", product, market, e)
        return {"available": False}
