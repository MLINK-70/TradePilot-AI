"""trade.py — 贸易数据模块：UN Comtrade 查询层 + 命令行入口

实测确认（2026-08-05）：
- 接口免费、无需 key、直连可用
- 一次最多查 1 个 period，多年份需循环
- 免费版限流严格（429），必须缓存
- 欧盟组代码 97 / 东盟 948，字母代码无效
"""
import logging
import sys
import time

import requests

from countries import ALL_COUNTRIES
from database import get_cached, init_db, log_query, save_cache

BASE_URL = "https://comtradeapi.un.org/public/v1/preview/C/A/HS"

# 常用消费电子 HS 编码映射表（第二版起步用，可扩展）
HS_MAP = {
    # 音频设备（85.18）
    "蓝牙耳机": "8518", "无线耳机": "8518", "耳机": "8518",
    "音箱": "8518", "蓝牙音箱": "8518", "功放": "8518",
    # 手表（91.02）
    "智能手表": "9102", "手表": "9102",
    # 吸尘器（85.08）
    "扫地机器人": "8508", "吸尘器": "8508",
    # 电池（85.07）
    "充电宝": "8507", "移动电源": "8507", "电池": "8507",
    # 手机/通讯（85.17）
    "手机": "8517", "智能手机": "8517", "对讲机": "8517",
    # 充电器/电源（85.04）
    "充电器": "8504", "电源适配器": "8504", "电源": "8504", "逆变器": "8504",
    # 显示/电视（85.28）
    "电视": "8528", "电视机": "8528", "显示器": "8528", "投影仪": "8528",
    # 摄像头（85.25）
    "摄像头": "8525", "相机": "8525", "摄像机": "8525",
    # 电脑（84.71）
    "电脑": "8471", "笔记本电脑": "8471", "笔记本": "8471", "平板": "8471",
    "平板电脑": "8471", "台式机": "8471",
    # 小家电（85.16）
    "电饭煲": "8516", "电热水壶": "8516", "吹风机": "8516", "电熨斗": "8516",
    "微波炉": "8516", "烤箱": "8516", "空气炸锅": "8516",
    # 空净/通风（84.21）
    "空气净化器": "8421", "净化器": "8421",
    # 智能家居（85.36/94.05）
    "智能门锁": "8536", "智能插座": "8536", "智能灯泡": "9405", "智能灯": "9405",
    "LED灯": "9405", "台灯": "9405",
    # 电动工具（84.67）
    "电钻": "8467", "电动工具": "8467",
    # 其他
    "无人机": "8526", "路由器": "8517", "机顶盒": "8528", "电子烟": "8543",
    "电动牙刷": "8509", "按摩仪": "9019",
}

# 国家/组织代码表 = 完整国家清单 + 组织代码（ALL_COUNTRIES 来自 countries.py）
AREA_MAP = {**ALL_COUNTRIES, **{
    "欧盟": "97",
    "东盟": "948",
    "RCEP": "RCEP",
    "全球": "0",
}}

# 欧盟成员国清单（27 国，2020 年脱欧后口径；preview 接口组代码查不出，需聚合）
EU_COUNTRIES = [
    "德国", "法国", "意大利", "荷兰", "比利时", "卢森堡", "爱尔兰", "丹麦",
    "希腊", "西班牙", "葡萄牙", "奥地利", "芬兰", "瑞典", "波兰", "捷克",
    "斯洛伐克", "匈牙利", "斯洛文尼亚", "克罗地亚", "罗马尼亚", "保加利亚",
    "立陶宛", "拉脱维亚", "爱沙尼亚", "塞浦路斯", "马耳他",
]

# 东盟成员国清单（10 国）
ASEAN_COUNTRIES = [
    "印度尼西亚", "马来西亚", "菲律宾", "新加坡", "泰国", "文莱", "越南",
    "老挝", "缅甸", "柬埔寨",
]

# RCEP 成员国清单（15 国：东盟 10 国 + 中、日、韩、澳、新西兰；中国为报告国不查自己）
RCEP_COUNTRIES = [
    "日本", "韩国", "澳大利亚", "新西兰",
    "印度尼西亚", "马来西亚", "菲律宾", "新加坡", "泰国", "文莱",
    "越南", "老挝", "缅甸", "柬埔寨",
]

# 组织 → 成员国映射（preview 接口组代码查不出数据，统一走成员聚合）
GROUP_MEMBERS = {
    "97": EU_COUNTRIES,
    "948": ASEAN_COUNTRIES,
    "RCEP": RCEP_COUNTRIES,
}


def get_latest_year() -> int:
    """探测 UN Comtrade 最新可用年份（从今年往前找第一个有数据的年份）

    探测结果写入缓存表（reporter_code='META'），避免每次查询都探测。
    """
    import datetime
    from database import get_cached, init_db, save_cache

    init_db()  # 确保缓存表存在（首次调用/无 db 文件时）

    meta_key = "LATEST_YEAR"
    cached = get_cached(meta_key, "0", "0", "X", "META")
    if cached:
        return int(cached[0]["year"])

    this_year = datetime.date.today().year
    # 探测范围 6 年（数据更新滞后时也能找到最新可用年份）
    for y in range(this_year, this_year - 6, -1):
        try:
            params = {
                "reporterCode": "156",
                "period": str(y),
                "partnerCode": "0",
                "cmdCode": "8518",
                "flowCode": "X",
                "maxRecords": 1,
            }
            resp = requests.get(
                BASE_URL, params=params,
                headers={"Accept": "application/json"},
                timeout=30,
                proxies={"http": None, "https": None},
            )
            if resp.status_code == 200 and resp.json().get("count", 0) > 0:
                save_cache(meta_key, "0", "0", "X", [{"year": y}], "META")
                logging.info("最新可用年份探测: %d", y)
                return y
        except Exception:
            continue
        time.sleep(1)
    logging.warning("最新年份探测失败，回退 2024")
    return 2024  # 兜底


# AI 辅助 HS 编码解析缓存（产品名 → 编码，避免重复调用）
_HS_AI_CACHE: dict = {}


def _hs_via_ai(product: str) -> str:
    """AI 辅助：产品名 → HS 编码（4-6 位）。失败返回空字符串。

    用 DeepSeek 知识库解析（如"羽毛球拍"→9506），成功后写入 SQLite 持久缓存 + 内置表。
    """
    # 太短的输入无法识别（防 AI 幻觉乱猜编码），直接返回空
    if len(product.strip()) < 2:
        return ""
    if product in _HS_AI_CACHE:
        return _HS_AI_CACHE[product]
    # 先查 SQLite 持久缓存（重启不丢），cache_key 专用字段存产品名
    try:
        from database import get_cached
        cached = get_cached("HSAI", "0", "0", "X", "0", cache_key=product)
        if cached:
            hs = str(cached[0].get("hs", ""))
            if hs.isdigit():
                _HS_AI_CACHE[product] = hs
                HS_MAP[product] = hs
                desc = str(cached[0].get("desc", "")).strip()
                if desc:
                    try:
                        from hs_descriptions import HS_DESCRIPTIONS
                        HS_DESCRIPTIONS[str(hs)] = desc
                        from database import save_cache
                        save_cache("HSDESC", "0", "0", "X", [{"hs": hs, "desc": desc}], "0", cache_key=hs)
                    except Exception:
                        pass
                return hs
    except Exception:
        pass
    try:
        from llm import _chat, _parse_json
        content = _chat([
            {"role": "system", "content": "你是 HS 编码专家。根据产品名，返回对应的 HS 编码（4-6 位数字）和中文品名描述。只输出 JSON：{\"hs_code\": \"9506\", \"description\": \"体育器械：羽毛球拍等\"}"},
            {"role": "user", "content": f"产品: {product}"},
        ], use_json=True)
        data = _parse_json(content)
        hs = str(data.get("hs_code", "")).strip()
        desc = str(data.get("description", "")).strip()
        if hs.isdigit() and 4 <= len(hs) <= 6:
            _HS_AI_CACHE[product] = hs
            HS_MAP[product] = hs  # 写进内置表，下次直接命中
            # 描述持久化：写进 SQLite（cache_key 存产品名）+ HSDESC 反向缓存（按编码精确查）+ 内存表
            try:
                from database import save_cache
                save_cache("HSAI", "0", "0", "X", [{"hs": hs, "desc": desc}], "0", cache_key=product)
                if desc:
                    save_cache("HSDESC", "0", "0", "X", [{"hs": hs, "desc": desc}], "0", cache_key=hs)
            except Exception:
                pass
            if desc:
                try:
                    from hs_descriptions import HS_DESCRIPTIONS
                    HS_DESCRIPTIONS[str(hs)] = desc
                except Exception:
                    pass
            return hs
    except Exception:
        pass
    _HS_AI_CACHE[product] = ""
    return ""


def hs_lookup(product: str) -> str:
    """产品名 → HS 编码；支持直接传 4-6 位数字编码

    优先内置表；匹配不到时 AI 辅助解析（成功后写缓存），再失败返回空（前端提示手输）。
    """
    product = product.strip()
    if product.isdigit() and 4 <= len(product) <= 6:
        return product
    hs = HS_MAP.get(product, "")
    if not hs:
        hs = _hs_via_ai(product)
    return hs


def partner_lookup(name: str) -> str:
    """国家/组织名 → 数字代码"""
    return AREA_MAP.get(name.strip(), "")


def fetch_year(cmd_code: str, partner_code: str, period: str, reporter: str = "中国",
               flow: str = "X") -> list:
    """查单年数据：先查缓存，未命中打 API 并写缓存

    reporter: 出口国（报告国），默认中国
    flow: X=出口 / M=进口（reporter 为报告国时的流向）
    """
    reporter_code = AREA_MAP.get(reporter, "156")
    flow_code = flow
    cached = get_cached(cmd_code, partner_code, period, flow_code, reporter_code)
    if cached is not None:
        return cached

    params = {
        "reporterCode": reporter_code,
        "period": period,
        "partnerCode": partner_code,
        "cmdCode": cmd_code,
        "flowCode": flow_code,
        "maxRecords": 500,
    }
    last_error = None
    for attempt in range(3):
        try:
            resp = requests.get(
                BASE_URL,
                params=params,
                headers={"Accept": "application/json"},
                timeout=60,
                proxies={"http": None, "https": None},  # 强制直连（防梯子劫持）
            )
            if resp.status_code == 429:
                last_error = f"429 限流（第 {attempt + 1} 次）"
                wait = 2 * (attempt + 1)
                print(f"[限流] 429，{wait} 秒后重试...")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            try:
                data = resp.json().get("data", [])
            except ValueError:
                raise ValueError(f"UN Comtrade 返回非 JSON 响应（HTTP {resp.status_code}）")
            if len(data) >= 500:
                # 触顶提示：preview 接口 4 位码返回聚合记录，理论上不会触发；
                # 若触发说明可能有更细粒度数据被截断
                print(f"[警告] HS{cmd_code} {period} 返回 {len(data)} 条，可能达到记录上限")
            if data:
                save_cache(cmd_code, partner_code, period, flow_code, data, reporter_code)
            return data
        except requests.exceptions.RequestException as e:
            last_error = str(e)
            if attempt == 2:
                raise ValueError(f"UN Comtrade 查询失败：{e}")
            print(f"[网络] {e}，2 秒后重试...")
            time.sleep(2)
    # 3 次重试全失败（如持续 429）：抛异常而非静默返回空，
    # 防止单国误报 0 / 组织聚合写入残缺缓存
    raise ValueError(f"UN Comtrade 查询失败：{last_error}（重试 3 次仍失败）")


def fetch_group(cmd_code: str, period: str, group_code: str, reporter: str = "中国") -> list:
    """组织聚合查询（欧盟/东盟/RCEP）：循环查成员国数据并缓存聚合结果

    preview 免费接口对组代码（97/948）不返回数据，统一走成员清单聚合。
    聚合结果按 (cmd_code, group_code, period, reporter) 缓存，避免重复全量循环。
    """
    reporter_code = AREA_MAP.get(reporter, "156")
    cached = get_cached(cmd_code, group_code, period, "X", reporter_code)
    if cached is not None:
        return cached

    members = GROUP_MEMBERS[group_code]
    all_rows = []
    failed = []
    for i, country in enumerate(members):
        code = AREA_MAP.get(country, "")
        if not code:
            print(f"[跳过] {country}: 无代码")
            continue
        try:
            rows = fetch_year(cmd_code, code, period, reporter)
            all_rows.extend(rows)
        except ValueError as e:
            failed.append(country)
            print(f"[跳过] {country}: {e}")
        time.sleep(1)  # 限流避让
        if i % 10 == 9:
            print(f"[进度] 已查 {i + 1}/{len(members)} 国")

    if failed:
        print(f"[警告] {len(failed)} 国查询失败: {', '.join(failed)}")

    # 有失败国时聚合数据残缺，不写缓存（防止残缺结果被永久缓存）
    if all_rows and not failed:
        save_cache(cmd_code, group_code, period, "X", all_rows, reporter_code)
    return all_rows


def query_trade(product: str, target: str, year: str, reporter: str = "中国"):
    """主入口：产品 + 国家/组织 + 年份 + 出口国 → 数据列表"""
    init_db()

    hs = hs_lookup(product)
    if not hs:
        raise ValueError(
            f"暂未收录产品「{product}」的 HS 编码，可手输 4-6 位数字 HS 编码查询"
        )

    target_code = partner_lookup(target)
    if not target_code:
        raise ValueError(f"未找到国家/组织「{target}」的代码")

    if target_code in GROUP_MEMBERS:
        rows = fetch_group(hs, year, target_code, reporter)
    else:
        rows = fetch_year(hs, target_code, year, reporter)

    log_query(product, hs, target)
    return hs, rows


def query_trend(product: str, target: str, years: list, reporter: str = "中国") -> tuple[str, list, dict]:
    """年份范围查询：产品 + 国家/组织 + 年份列表 + 出口国 → (hs_code, 全部行, 逐年汇总)

    years 如 [2018, 2019, 2020, 2021, 2022]，每年代价同单年查询；
    单国每年 1 次请求，组织每年 N 国请求（已有缓存则秒回）。
    """
    init_db()

    hs = hs_lookup(product)
    if not hs:
        raise ValueError(
            f"暂未收录产品「{product}」的 HS 编码，可手输 4-6 位数字 HS 编码查询"
        )

    target_code = partner_lookup(target)
    if not target_code:
        raise ValueError(f"未找到国家/组织「{target}」的代码")

    all_rows = []
    if target_code in GROUP_MEMBERS:
        for year in years:
            all_rows.extend(fetch_group(hs, str(year), target_code, reporter))
    else:
        for year in years:
            all_rows.extend(fetch_year(hs, target_code, str(year), reporter))

    log_query(product, hs, target)
    return hs, all_rows, summarize_trend(all_rows)


def summarize_trend(rows: list) -> dict:
    """逐年汇总：{year: {"value": float, "weight": float}}，按 refYear 聚合"""
    by_year: dict[int, dict] = {}
    for r in rows:
        year = r.get("refYear")
        if not year:
            continue
        entry = by_year.setdefault(year, {"value": 0.0, "weight": 0.0})
        entry["value"] += r.get("primaryValue") or 0
        entry["weight"] += r.get("netWgt") or 0
    return {y: v for y, v in sorted(by_year.items())}


def summarize_stats(trend: dict) -> dict:
    """程序精确计算趋势统计指标（供 AI 解读引用，杜绝 AI 自己算错）

    返回：总量、年均增速、峰值/谷值年份、首末变化、最大单年波动、单价趋势
    """
    years = sorted(trend.keys())
    if not years:
        return {}

    first_y, last_y = years[0], years[-1]
    first_v = trend[first_y]["value"]
    last_v = trend[last_y]["value"]
    total = sum(v["value"] for v in trend.values())

    # 年复合增长率 CAGR = (last/first)^(1/n) - 1
    n = len(years) - 1
    cagr = ((last_v / first_v) ** (1 / n) - 1) * 100 if n > 0 and first_v else None

    # 峰值/谷值
    peak_y = max(trend, key=lambda y: trend[y]["value"])
    trough_y = min(trend, key=lambda y: trend[y]["value"])

    # 最大单年波动（相邻年变化率最大）
    max_chg = 0.0
    max_chg_year = None
    for i in range(1, len(years)):
        prev, cur = trend[years[i - 1]]["value"], trend[years[i]]["value"]
        if prev:
            chg = (cur - prev) / prev * 100
            if abs(chg) > abs(max_chg):
                max_chg = chg
                max_chg_year = years[i]

    # 单价趋势（金额/净重）
    unit_prices = []
    for y in years:
        w = trend[y].get("weight") or 0
        if w > 0:
            unit_prices.append({"year": y, "price": trend[y]["value"] / w})

    return {
        "years": years,
        "total_value": total,
        "first_year": first_y,
        "last_year": last_y,
        "first_value": first_v,
        "last_value": last_v,
        "change_over_period_pct": (last_v - first_v) / first_v * 100 if first_v else None,
        "cagr_pct": round(cagr, 2) if cagr is not None else None,
        "peak_year": peak_y,
        "trough_year": trough_y,
        "max_swing_year": max_chg_year,
        "max_swing_pct": round(max_chg, 2) if max_chg_year else None,
        "unit_prices": unit_prices,
    }


def compute_tc(export_value: float, import_value: float) -> float | None:
    """贸易竞争力指数 TC = (出口-进口)/(出口+进口)

    范围 [-1, 1]：>0 顺差（竞争力强），<0 逆差（竞争力弱）。
    """
    total = export_value + import_value
    if total == 0:
        return None
    return round((export_value - import_value) / total, 4)


def compute_rca(product_export: float, country_export: float,
                product_world_export: float, world_export: float) -> float | None:
    """显性比较优势 RCA = (产品出口/国家总出口) / (全球产品出口/全球总出口)

    RCA > 1：该产品在目标国具有显性比较优势；< 1：劣势。
    """
    if not country_export or not world_export or not product_world_export:
        return None
    share_c = product_export / country_export
    share_w = product_world_export / world_export
    if share_w == 0:
        return None
    return round(share_c / share_w, 4)


def get_competitiveness(product: str, target: str, year: str, reporter: str = "中国") -> dict:
    """竞争力指标：TC（贸易竞争力指数）+ 市场出口份额

    - TC：出口国对该市场该产品的出口 + 进口（flow X + M），TC=(X-M)/(X+M)
    - 市场出口份额：出口国对该市场该产品出口 / 该市场该产品总进口
      （份额 = 出口国占目标市场进口的比重，真实可算、有业务含义）

    说明：标准 RCA（显性比较优势）需要全球总出口数据（reporter=0），
    UN Comtrade preview 免费接口不提供，故用"市场出口份额"替代——
    同样衡量竞争力，且数据严谨可溯源。任一数据缺失返回空 dict，不阻断。
    """
    try:
        hs = hs_lookup(product)
        if not hs:
            return {}
        target_code = partner_lookup(target)
        if not target_code:
            return {}

        # TC：出口 + 进口（出口国对该市场）
        exp_rows = fetch_year(hs, target_code, year, reporter, flow="X")
        imp_rows = fetch_year(hs, target_code, year, reporter, flow="M")
        export_value = sum(r.get("primaryValue") or 0 for r in exp_rows)
        import_value = sum(r.get("primaryValue") or 0 for r in imp_rows)
        tc = compute_tc(export_value, import_value)

        # 市场出口份额：目标市场该产品总进口（flow=M, partner=0 全球）
        market_import_rows = fetch_year(hs, "0", year, target, flow="M")
        market_import_value = sum(r.get("primaryValue") or 0 for r in market_import_rows)
        market_share = round(export_value / market_import_value * 100, 2) if market_import_value else None

        return {
            "tc": tc,
            "export_value": export_value,
            "import_value": import_value,
            "market_import_value": market_import_value,
            "market_share": market_share,  # 出口国占目标市场该产品进口的份额（%）
            "available": True,
        }
    except Exception:
        return {}


def get_competitor_comparison(product: str, target: str, year: str,
                              competitors: list = None,
                              reporter: str = "中国") -> dict:
    """竞争对手出口对比：出口国 vs 同类主要出口国对目标市场的同类产品出口

    competitors 默认 [中国, 日本, 韩国, 越南]（消费电子主要出口国）；
    若出口国不在其中（如德国），自动加入并放在第一位，保证对比包含出口国自身。
    返回 {competitors: [{country, value, share}], available: bool}
    """
    if competitors is None:
        competitors = ["中国", "日本", "韩国", "越南"]
    # 出口国必须是竞争对手之一（否则对比表里没有出口国自身，占比失真）
    if reporter and reporter not in competitors:
        competitors = [reporter] + [c for c in competitors if c != reporter]
    try:
        hs = hs_lookup(product)
        if not hs:
            return {}
        # 结果缓存（HS+目标+年份+出口国 → 对比结果），避免重复轮询多国 UN Comtrade
        # 版本签名 V1：未来改 share 计算/候选人名单时递增，旧缓存自动失效
        cache_k = f"V1|{target}|{reporter}"
        try:
            from database import get_cached
            cached = get_cached("COMPARE", hs, year, "X", "0", cache_key=cache_k)
            if cached and isinstance(cached, list):
                return {"competitors": cached, "available": True}
        except Exception:
            pass
        results = []
        total = 0
        for country in competitors:
            try:
                rows = fetch_year(hs, partner_lookup(target) or "0", year, reporter=country)
                value = sum(r.get("primaryValue") or 0 for r in rows)
                results.append({"country": country, "value": value})
                total += value
            except Exception:
                results.append({"country": country, "value": 0})
        for r in results:
            r["share"] = round(r["value"] / total * 100, 1) if total else 0
        try:
            from database import save_cache
            save_cache("COMPARE", hs, year, "X", results, "0", cache_key=cache_k)
        except Exception:
            pass
        return {"competitors": results, "available": True}
    except Exception:
        return {}


def get_top_exporters(product: str, year: str, top_n: int = 6) -> list:
    """动态识别品类出口大国：候选出口国对该品类全球出口额排名

    UN Comtrade preview 不支持 reporterCode=0 的全球分组查询（返回空），
    改为轮询候选出口大国（消费电子主要出口国名单）对该品类的全球出口，
    按出口额降序取 TOP N。返回 [{country, value}]，失败返回 []。

    结果按 (HS, 年份) 缓存到 SQLite——首次轮询 16 国后，后续查询直接命中，
    避免每次贸易查询都打 16 次 UN Comtrade（免费版 429 限流下会显著拖慢）。
    """
    candidates = [
        "中国", "德国", "日本", "韩国", "越南", "美国",
        "英国", "荷兰", "意大利", "新加坡", "法国", "马来西亚",
        "泰国", "墨西哥", "波兰", "印度",
    ]
    try:
        hs = hs_lookup(product)
        if not hs:
            return []
        # 先查缓存（HS+年份 → TOP 出口国），版本签名 V1：改候选人名单/计算时递增
        try:
            from database import get_cached
            cached = get_cached("TOPEXP", hs, year, "X", "0", cache_key="V1|rank")
            if cached and isinstance(cached, list):
                return cached[:top_n]
        except Exception:
            pass
        results = []
        for country in candidates:
            try:
                rows = fetch_year(hs, "0", year, reporter=country, flow="X")
                value = sum(r.get("primaryValue") or 0 for r in rows)
                if value > 0:
                    results.append({"country": country, "value": value})
            except Exception:
                continue
        results.sort(key=lambda x: x["value"], reverse=True)
        top = results[:top_n]
        # 写缓存（含 fetch_year 已缓存，这里存排名结果）
        try:
            from database import save_cache
            save_cache("TOPEXP", hs, year, "X", top, "0", cache_key="V1|rank")
        except Exception:
            pass
        return top
    except Exception:
        return []


def get_destination_ranking(product: str, target: str, year: str,
                            reporter: str = "中国") -> dict:
    """出口目的地排名：目标市场（如欧盟）内部各国进口该产品排名

    返回 {destinations: [{country, value, share}], available: bool}
    """
    try:
        hs = hs_lookup(product)
        if not hs:
            return {}
        target_code = partner_lookup(target)
        if target_code not in GROUP_MEMBERS:
            return {}  # 仅对组织（欧盟/东盟/RCEP）有效
        members = GROUP_MEMBERS[target_code]
        results = []
        total = 0
        for country in members:
            code = AREA_MAP.get(country, "")
            if not code:
                continue
            try:
                rows = fetch_year(hs, code, year, reporter=reporter)
                value = sum(r.get("primaryValue") or 0 for r in rows)
                results.append({"country": country, "value": value})
                total += value
            except Exception:
                continue
        results.sort(key=lambda x: x["value"], reverse=True)
        for r in results:
            r["share"] = round(r["value"] / total * 100, 1) if total else 0
        return {"destinations": results[:10], "available": True}
    except Exception:
        return {}


def _parse_years(arg: str) -> list:
    """解析年份参数：'2022' / '2020-2022' / '2018,2020,2022'"""
    arg = arg.strip()
    if "-" in arg:
        start, end = arg.split("-")
        return list(range(int(start), int(end) + 1))
    return [int(y) for y in arg.split(",") if y.strip().isdigit()]


if __name__ == "__main__":
    # 用法: python trade.py 蓝牙耳机 德国 2022 | 2020-2022 | 2018,2020,2022
    if len(sys.argv) < 4:
        print("用法: python trade.py <产品名或HS编码> <国家/组织> <年份或范围>")
        sys.exit(1)

    product_arg, target_arg, year_arg = sys.argv[1], sys.argv[2], sys.argv[3]
    years = _parse_years(year_arg)
    if not years:
        print(f"错误: 无法解析年份「{year_arg}」，支持 2022 / 2020-2022 / 2018,2020,2022")
        sys.exit(1)

    try:
        if len(years) == 1:
            hs_code, data = query_trade(product_arg, target_arg, str(years[0]))
            trend = summarize_trend(data)
        else:
            hs_code, data, trend = query_trend(product_arg, target_arg, years)
        total_value = sum(r.get("primaryValue") or 0 for r in data)
        total_wgt = sum(r.get("netWgt") or 0 for r in data)
        print(f"\n=== {product_arg}(HS{hs_code}) 中国出口 {target_arg} {year_arg} ===")
        print(f"记录数: {len(data)}")
        print(f"贸易总额: {total_value:,.0f} 美元")
        print(f"总净重: {total_wgt:,.0f} 公斤")
        print("逐年趋势:")
        for y, v in trend.items():
            print(f"  {y}: {v['value']:,.0f} 美元 | {v['weight']:,.0f} 公斤")
        for r in data[:5]:
            print(f"  {r.get('cmdDesc') or 'N/A'} | {r.get('partnerDesc')} | {r.get('primaryValue') or 0:,.0f} 美元")
    except ValueError as e:
        print(f"错误: {e}")
        sys.exit(1)
