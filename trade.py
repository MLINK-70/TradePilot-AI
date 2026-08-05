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

from database import get_cached, init_db, log_query, save_cache

BASE_URL = "https://comtradeapi.un.org/public/v1/preview/C/A/HS"

# 常用消费电子 HS 编码映射表（第二版起步用，可扩展）
HS_MAP = {
    "蓝牙耳机": "8518",
    "无线耳机": "8518",
    "耳机": "8518",
    "智能手表": "9102",
    "手表": "9102",
    "扫地机器人": "8508",
    "吸尘器": "8508",
    "充电宝": "8507",
    "手机": "8517",
    "智能手机": "8517",
    "充电器": "8504",
    "显示器": "8528",
    "摄像头": "8525",
    "音箱": "8518",
    "蓝牙音箱": "8518",
}

# 国家/组织代码表（ISO 数字代码，与 UN Comtrade 一致）
AREA_MAP = {
    "中国": "156",
    "德国": "276",
    "美国": "842",
    "日本": "392",
    "英国": "826",
    "法国": "250",
    "荷兰": "528",
    "意大利": "380",
    "韩国": "410",
    "印度": "699",
    "巴西": "76",
    "俄罗斯": "643",
    "澳大利亚": "36",
    "加拿大": "124",
    "西班牙": "724",
    "墨西哥": "484",
    # 欧盟 27 国
    "奥地利": "40", "比利时": "56", "保加利亚": "100", "克罗地亚": "191",
    "塞浦路斯": "196", "捷克": "203", "丹麦": "208", "爱沙尼亚": "233",
    "芬兰": "246", "希腊": "300", "匈牙利": "348", "爱尔兰": "372",
    "拉脱维亚": "428", "立陶宛": "440", "卢森堡": "442", "马耳他": "470",
    "波兰": "616", "葡萄牙": "620", "罗马尼亚": "642", "斯洛伐克": "703",
    "斯洛文尼亚": "705", "瑞典": "752",
    # 东盟 10 国（除已有）
    "印度尼西亚": "360", "马来西亚": "458", "菲律宾": "608", "新加坡": "702",
    "泰国": "764", "文莱": "96", "越南": "704", "老挝": "418",
    "缅甸": "104", "柬埔寨": "116",
    # RCEP 非东盟成员（日本/韩国/澳大利亚/新西兰已有）
    "新西兰": "554",
    "欧盟": "97",
    "东盟": "948",
    "RCEP": "RCEP",
    "全球": "0",
}

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


def hs_lookup(product: str) -> str:
    """产品名 → HS 编码；支持直接传 4-6 位数字编码"""
    product = product.strip()
    if product.isdigit() and 4 <= len(product) <= 6:
        return product
    return HS_MAP.get(product, "")


def partner_lookup(name: str) -> str:
    """国家/组织名 → 数字代码"""
    return AREA_MAP.get(name.strip(), "")


def fetch_year(cmd_code: str, partner_code: str, period: str) -> list:
    """查单年数据：先查缓存，未命中打 API 并写缓存"""
    flow_code = "X"  # X=出口（中国为报告国时即中国出口）
    cached = get_cached(cmd_code, partner_code, period, flow_code)
    if cached is not None:
        return cached

    params = {
        "reporterCode": "156",  # 中国
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
            if data:
                save_cache(cmd_code, partner_code, period, flow_code, data)
            return data
        except requests.exceptions.RequestException as e:
            if attempt == 2:
                raise ValueError(f"UN Comtrade 查询失败：{e}")
            print(f"[网络] {e}，2 秒后重试...")
            time.sleep(2)
    return []


def fetch_group(cmd_code: str, period: str, group_code: str) -> list:
    """组织聚合查询（欧盟/东盟/RCEP）：循环查成员国数据并缓存聚合结果

    preview 免费接口对组代码（97/948）不返回数据，统一走成员清单聚合。
    聚合结果按 (cmd_code, group_code, period) 缓存，避免重复全量循环。
    """
    cached = get_cached(cmd_code, group_code, period, "X")
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
            rows = fetch_year(cmd_code, code, period)
            all_rows.extend(rows)
        except ValueError as e:
            failed.append(country)
            print(f"[跳过] {country}: {e}")
        time.sleep(1)  # 限流避让
        if i % 10 == 9:
            print(f"[进度] 已查 {i + 1}/{len(members)} 国")

    if failed:
        print(f"[警告] {len(failed)} 国查询失败: {', '.join(failed)}")

    save_cache(cmd_code, group_code, period, "X", all_rows)
    return all_rows


def query_trade(product: str, target: str, year: str):
    """主入口：产品 + 国家/组织 + 年份 → 数据列表"""
    init_db()

    hs = hs_lookup(product)
    if not hs:
        raise ValueError(f"未找到产品「{product}」的 HS 编码，可手输 4-6 位数字编码")

    target_code = partner_lookup(target)
    if not target_code:
        raise ValueError(f"未找到国家/组织「{target}」的代码")

    if target_code in GROUP_MEMBERS:
        rows = fetch_group(hs, year, target_code)
    else:
        rows = fetch_year(hs, target_code, year)

    log_query(product, hs, target)
    return hs, rows


if __name__ == "__main__":
    # 用法：python trade.py 蓝牙耳机 德国 2022
    if len(sys.argv) < 4:
        print("用法: python trade.py <产品名或HS编码> <国家/组织> <年份>")
        sys.exit(1)

    product_arg, target_arg, year_arg = sys.argv[1], sys.argv[2], sys.argv[3]
    try:
        hs_code, data = query_trade(product_arg, target_arg, year_arg)
        total_value = sum(r.get("primaryValue") or 0 for r in data)
        total_wgt = sum(r.get("netWgt") or 0 for r in data)
        print(f"\n=== {product_arg}(HS{hs_code}) 中国出口 {target_arg} {year_arg} ===")
        print(f"记录数: {len(data)}")
        print(f"贸易总额: {total_value:,.0f} 美元")
        print(f"总净重: {total_wgt:,.0f} 公斤")
        for r in data[:5]:
            print(f"  {r.get('cmdDesc') or 'N/A'} | {r.get('partnerDesc')} | {r.get('primaryValue') or 0:,.0f} 美元")
    except ValueError as e:
        print(f"错误: {e}")
        sys.exit(1)
