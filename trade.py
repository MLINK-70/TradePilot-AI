"""trade.py — 贸易数据模块：UN Comtrade 查询层 + 命令行入口

实测确认（2026-08-05）：
- 接口免费、无需 key、直连可用
- 一次最多查 1 个 period，多年份需循环
- 免费版限流严格（429），必须缓存
- 欧盟组代码 97 / 东盟 948，字母代码无效
"""
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
    for y in range(this_year, this_year - 3, -1):
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
                return y
        except Exception:
            continue
        time.sleep(1)
    return 2024  # 兜底


def hs_lookup(product: str) -> str:
    """产品名 → HS 编码；支持直接传 4-6 位数字编码"""
    product = product.strip()
    if product.isdigit() and 4 <= len(product) <= 6:
        return product
    return HS_MAP.get(product, "")


def partner_lookup(name: str) -> str:
    """国家/组织名 → 数字代码"""
    return AREA_MAP.get(name.strip(), "")


def fetch_year(cmd_code: str, partner_code: str, period: str, reporter: str = "中国") -> list:
    """查单年数据：先查缓存，未命中打 API 并写缓存

    reporter: 出口国（报告国），默认中国
    """
    reporter_code = AREA_MAP.get(reporter, "156")
    flow_code = "X"  # X=出口（reporter 为报告国时即该国的出口）
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
            if attempt == 2:
                raise ValueError(f"UN Comtrade 查询失败：{e}")
            print(f"[网络] {e}，2 秒后重试...")
            time.sleep(2)
    return []


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

    # 空结果不缓存：429 或成员全失败时缓存 []，后续查询永远拿到 0
    if all_rows:
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
