"""market_data.py — 多数据源聚合模块

World Bank API（无 Key 免费）：市场环境数据（GDP/人口/人均/互联网普及率）
——补充 UN Comtrade 之外的"市场环境"维度，让分析有理有据。

OEC / NewsAPI 预留（注册 Key 后启用，见 config）。
"""
import logging

import requests

from database import get_cached, init_db, save_cache

WB_BASE = "https://api.worldbank.org/v2/country"

# World Bank 指标（ISO3 国家码 → 指标）
INDICATORS = {
    "gdp": "NY.GDP.MKTP.CD",            # GDP 现价美元
    "population": "SP.POP.TOTL",        # 人口
    "gdp_per_capita": "NY.GDP.PCAP.CD", # 人均 GDP
    "internet": "IT.NET.USER.ZS",       # 互联网普及率 %
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
    """查 World Bank 单指标最新值（year=0 表示取最新；带缓存）"""
    init_db()
    cache_key = f"WB:{iso3}:{indicator}"
    cached = get_cached(cache_key, "0", "0", "X", "META")
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
                save_cache(cache_key, "0", "0", "X", [{"value": value}], "META")
                return value
        except Exception as e2:
            logging.warning("World Bank 查询失败 %s/%s: %s", iso3, indicator, e2)
    return None


def get_market_context(country: str) -> dict:
    """聚合市场环境：GDP/人口/人均/互联网 → dict（失败字段为 None，不阻断）"""
    iso3 = COUNTRY_ISO3.get(country.strip(), "")
    if not iso3:
        return {"country": country, "available": False}

    result = {"country": country, "iso3": iso3, "available": True}
    for name in INDICATORS:
        result[name] = get_worldbank(iso3, name)
    return result


# ===== Tavily 行业动态（注册 Key 后启用）=====
def get_news(product: str, market: str) -> dict:
    """Tavily 搜索：产品+市场 相关新闻（行业动态证据链）

    返回：{headlines: [{title, url}], available: bool}
    """
    from config import TAVILY_API_KEY
    if not TAVILY_API_KEY:
        return {"available": False}

    try:
        resp = requests.post(
            "https://api.tavily.com/search",
            json={
                "api_key": TAVILY_API_KEY,
                "query": f"{product} market {market}",
                "max_results": 5,
                "search_depth": "basic",
            },
            timeout=20,
            proxies={"http": None, "https": None},
        )
        resp.raise_for_status()
        data = resp.json()
        headlines = [
            {"title": r.get("title", ""), "url": r.get("url", "")}
            for r in data.get("results", [])[:5]
        ]
        return {"available": bool(headlines), "headlines": headlines}
    except Exception as e:
        logging.warning("Tavily 搜索失败 %s/%s: %s", product, market, e)
        return {"available": False}
