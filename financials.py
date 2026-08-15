"""financials.py — 上市公司财报解析（财务画像）

三层覆盖：
1. 美股（SEC EDGAR XBRL）：苹果/索尼等，免费、无 Key、结构化 JSON
2. A 股（预留）：巨潮资讯，后续实现
3. 非上市（Tavily 兜底）：华为等公开报道数据

所有财务数字由程序解析（非 AI），AI 只解读引用——守住"AI 不参与算术"底线。
"""
import logging
import re
import time

import requests

SEC_BASE = "https://data.sec.gov/api/xbrl/companyconcept"

# 公司 → (CIK, 名称, 上市地)
# CIK 需 10 位补零
COMPANIES = {
    "苹果": ("0000320193", "Apple Inc.", "US"),
    "索尼": ("0000313838", "Sony Group Corporation", "US"),
    "特斯拉": ("0001318605", "Tesla, Inc.", "US"),
    "戴尔": ("0000826083", "Dell Technologies Inc.", "US"),
    "三星": ("", "Samsung Electronics", "KR"),  # 韩国上市，SEC 无
}

# A 股公司 → 股票代码（消费电子/家电/电池等，可扩展）
A_SHARES = {
    "歌尔股份": "002241", "立讯精密": "002475", "漫步者": "002351",
    "石头科技": "688169", "科沃斯": "603486", "美的集团": "000333",
    "苏泊尔": "002032", "九阳股份": "002242", "宁德时代": "300750",
    "亿纬锂能": "300014", "京东方A": "000725", "TCL科技": "000100",
}

# 非上市/无 SEC 但有公开报道财务数据的公司（仅名单内走 Tavily 兜底，防未知公司幻觉）
PRIVATE_COMPANIES = {
    "华为", "OPPO", "vivo", "大疆", "传音", "字节跳动", "小米", "荣耀",
    "三星",  # 韩国上市，SEC 无 CIK，走公开报道（B6 修复：否则被路由漏掉报"未收录"）
}


def get_a_share_financials(company: str) -> dict:
    """A 股财报：总营收/净利/毛利率/ROE（东方财富 datacenter 免费接口）

    接口返回按报告期排序的财务摘要（含 TOTAL_OPERATE_INCOME 总营收、
    PARENT_NETPROFIT 归母净利、XSMLL 销售毛利率、WEIGHTAVG_ROE 加权ROE）。
    注意：A 股财报为累计值（一季报=Q1，中报=H1，三季报=Q3，年报=FY），
    取年报（DATAYEAR 年 + 报告期 12-31）做年度序列，避免重复累计。
    """
    code = A_SHARES.get(company)
    if not code:
        return {"available": False, "reason": f"{company} 未收录 A 股代码"}
    try:
        url = ("https://datacenter-web.eastmoney.com/api/data/v1/get"
               f"?reportName=RPT_LICO_FN_CPD&columns=ALL&filter=(SECURITY_CODE%3D%22{code}%22)"
               "&pageNumber=1&pageSize=30&sortColumns=REPORTDATE&sortTypes=-1")
        resp = requests.get(url, headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://data.eastmoney.com/",
        }, timeout=30, proxies={"http": None, "https": None})
        resp.raise_for_status()
        # 容错：result 为 null 时不再抛 AttributeError（B 类审查 #7）
        rows = (resp.json().get("result") or {}).get("data") or []
        if not rows:
            return {"available": False, "reason": "A 股财报拉取失败"}

        # 年报序列（报告期 12-31）：营收/净利/毛利率/ROE
        # REPORTDATE 形如 "2025-12-31 00:00:00"，用日期部分判断（不再依赖 20 开头 hack）
        annual = [r for r in rows if re.match(r"^\d{4}-", str(r.get("REPORTDATE", ""))) and str(r.get("REPORTDATE", ""))[5:10] == "12-31"]
        annual.sort(key=lambda r: r.get("REPORTDATE", ""), reverse=True)
        annual = annual[:5]

        def _series(key, scale=1.0):
            s = [{"year": r["REPORTDATE"][:4], "value": round((r.get(key) or 0) * scale, 2)} for r in annual]
            # 统一升序（旧→新）：export.py 取 [-1] 为最新年报，且"近 N 年变化"方向正确
            # （数据准确性修复：此前降序导致最新营收错位、扩张显示为收缩）
            s.sort(key=lambda x: str(x["year"]))
            return s

        metrics = {
            "revenue": _series("TOTAL_OPERATE_INCOME"),
            "net_income": _series("PARENT_NETPROFIT"),
            "gross_margin_pct": _series("XSMLL"),
            "roe_pct": _series("WEIGHTAVG_ROE"),
        }
        return {
            "company": company + "（" + code + "）",
            "source": "东方财富财报接口（A 股年报）",
            "available": True,
            "unit": "CNY",
            "metrics": metrics,
        }
    except Exception as e:
        logging.warning("A 股财报失败 %s: %s", company, e)
        return {"available": False, "reason": "A 股财报拉取失败"}


def get_company_financials(company: str) -> dict:
    """统一入口：公司名 → 财务画像（自动路由）

    - COMPANIES 里有 CIK → SEC EDGAR 官方（美股）
    - A_SHARES 里有代码 → 东方财富 A 股年报
    - PRIVATE_COMPANIES 名单内 → Tavily 公开报道兜底（华为等非上市）
    - 其他 → 未收录（防未知公司走 Tavily 产生幻觉数据）
    返回统一结构 {company, source, available, metrics}
    """
    info = COMPANIES.get(company, "")
    if info and info[0]:
        return get_sec_financials(company)
    if company in A_SHARES:
        return get_a_share_financials(company)
    if company in PRIVATE_COMPANIES:
        return get_private_company_financials(company)
    return {"available": False, "reason": f"未收录公司「{company}」，暂不支持财报查询"}

# us-gaap tag → 指标名（SEC 不同公司 tag 可能不同，按候选列表取）
TAG_CANDIDATES = {
    "revenue": [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
        "Revenue",
    ],
    "net_income": [
        "NetIncomeLoss",
        "ProfitLoss",
    ],
    "gross_profit": [
        "GrossProfit",
        "GrossProfitFromContinuingOperations",
    ],
    "rd_expense": [
        "ResearchAndDevelopmentExpense",
        "ResearchAndDevelopmentExpenseExcludingAcquiredInProcessResearchAndDevelopmentCosts",
    ],
}


def _fetch_concept(cik: str, tag: str) -> dict | None:
    """拉取 SEC XBRL 概念数据（公司 + 指标 tag），429 限流重试"""
    url = f"{SEC_BASE}/CIK{cik}/us-gaap/{tag}.json"
    last_error = None
    for attempt in range(3):
        try:
            resp = requests.get(url, headers={"User-Agent": "TradePilotAI contact@example.com"}, timeout=30)
            if resp.status_code == 404:
                return None
            if resp.status_code == 429:
                last_error = f"429 限流（第 {attempt + 1} 次）"
                wait = 2 * (attempt + 1)
                logging.warning("SEC 429，%d 秒后重试...", wait)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.RequestException as e:
            last_error = str(e)
            if attempt == 2:
                logging.warning("SEC 拉取失败 %s/%s: %s", cik, tag, last_error)
                return None
            time.sleep(2)
    return None


# SEC XBRL 申报单位：companyconcept 的 val 是申报文件原始值，
# 白名单内大部分美股公司（苹果/特斯拉/戴尔）以"百万美元"申报，
# 统一换算为美元（×1e6），否则报告显示"0.00 亿美元"（数据准确性红线）。
# 回归修复：索尼是外国私人发行人，年报申报为 Form 20-F（非 10-K），
# 且以"百万日元"列示——原硬编码 (10-K, USD, 1e6) 导致索尼永远取不到数据。
# 按公司维护申报档案（form 集合, 币种, 换算倍数）。
SEC_PROFILES = {
    "苹果": (("10-K",), "USD", 1e6),
    "索尼": (("20-F", "10-K"), "JPY", 1e6),  # 20-F 年报 + 百万日元
    "特斯拉": (("10-K",), "USD", 1e6),
    "戴尔": (("10-K",), "USD", 1e6),
}


def _annual_series(data: dict, n: int = 5, forms=("10-K",), currency: str = "USD",
                   scale: float = 1e6) -> list:
    """从 XBRL 响应提取最近 N 个财年的年度值（允许 form 集合 + 币种）

    数据准确性：val 按申报币种换算为基准货币（乘以申报单位倍数 scale）；
    财年 end 提取年份。旧版注释"以百万美元申报"仅对 10-K 美股成立。
    """
    if not data:
        return []
    units = data.get("units", {}).get(currency, [])
    if not units:
        return []
    annual = [u for u in units if u.get("form") in forms and u.get("fp") == "FY" and u.get("end")]
    # 去重（同 end 保留最新 filed）
    seen = {}
    for u in annual:
        end = u["end"]
        if end not in seen or u["filed"] > seen[end]["filed"]:
            seen[end] = u
    recent = sorted(seen.values(), key=lambda u: u["end"])[-n:]
    # 升序（旧→新），与 A 股序列约定一致
    recent.sort(key=lambda u: u["end"])
    return [{"year": u["end"][:4], "value": u["val"] * scale} for u in recent]


def get_sec_financials(company: str) -> dict:
    """美股财报：营收/净利/毛利率/研发 5 年序列（SEC EDGAR 官方）"""
    info = COMPANIES.get(company, "")
    if not info or not info[0]:
        return {"available": False, "reason": f"{company} 无 SEC 财报（非美股上市）"}
    cik = info[0]
    forms, currency, scale = SEC_PROFILES.get(company, (("10-K",), "USD", 1e6))

    result = {"company": info[1], "source": "SEC EDGAR (官方 XBRL)", "available": True,
              "unit": currency, "metrics": {}}
    for metric, tags in TAG_CANDIDATES.items():
        for tag in tags:
            try:
                data = _fetch_concept(cik, tag)
                series = _annual_series(data, forms=forms, currency=currency, scale=scale)
                if series:
                    result["metrics"][metric] = series
                    break
            except Exception as e:
                logging.warning("SEC 拉取失败 %s/%s: %s", company, tag, e)
            time.sleep(0.2)  # SEC 限流友好

    # 毛利率 = 毛利 / 营收（对齐年份）
    rev = {r["year"]: r["value"] for r in result["metrics"].get("revenue", [])}
    gp = {r["year"]: r["value"] for r in result["metrics"].get("gross_profit", [])}
    margins = []
    for y in sorted(set(rev) & set(gp)):
        if rev[y]:
            margins.append({"year": y, "value": round(gp[y] / rev[y] * 100, 2)})
    if margins:
        result["metrics"]["gross_margin_pct"] = margins

    if not result["metrics"]:
        return {"available": False, "reason": "SEC 数据拉取失败"}
    return result


def get_private_company_financials(company: str) -> dict:
    """非上市公司（华为等）：Tavily 检索公开报道的营收/研发数据

    无官方财报，用公开报道（标注来源年份），数字尽量来自同一财年。
    返回 {metrics: {revenue: [{year, value}], rd_expense: [...]}, source: '公开报道'}
    """
    from market_data import _search_web
    try:
        # 检索营收 + 研发
        snippets = []
        for q in [f"{company} 营收 2024 2025 财报 公布", f"{company} 研发投入 2024 2025 亿元"]:
            snippets += [r.get("content", "") for r in _search_web(q, max_results=3)]
        text = "\n".join(snippets)
        if not text:
            return {"available": False, "reason": "未检索到公开财务数据"}

        # 用 LLM 从检索文本提炼（数字提取靠 LLM 但标注"公开报道"，非官方）
        from llm import _chat, _parse_json
        content = _chat([
            {"role": "system", "content": "从给定的新闻报道片段中，提炼公司的财务数据。只输出 JSON：{\"revenue\": [{\"year\": 2024, \"value_billion\": 8621}], \"rd_expense\": [{\"year\": 2024, \"value_billion\": 1647}]}。value_billion 单位：亿元人民币。没有的数据就不填。禁止编造，只提炼文中出现的数字。"},
            {"role": "user", "content": text[:4000]},
        ], use_json=True)
        data = _parse_json(content)

        # 数据准确性：value_billion 可能是 "8621亿" / "8,621" / 0 等，统一正则抽纯数字；
        # year 可能是 "2024年" 字符串，强制 int；非法值跳过不整条失败
        def _clean_series(items):
            out = []
            for r in items:
                if not isinstance(r, dict):
                    continue
                s = str(r.get("value_billion") or "")
                m = re.search(r"(\d[\d,.]*)", s)
                if not m:
                    continue
                try:
                    val = float(m.group(1).replace(",", ""))
                except ValueError:
                    continue
                if val <= 0:
                    continue
                y = str(r.get("year") or "")
                ym = re.search(r"(\d{4})", y)
                if not ym:
                    continue
                out.append({"year": int(ym.group(1)), "value": val * 1e8})
            return out

        metrics = {}
        if data.get("revenue"):
            metrics["revenue"] = _clean_series(data["revenue"])
        if data.get("rd_expense"):
            metrics["rd_expense"] = _clean_series(data["rd_expense"])
        if not metrics:
            return {"available": False, "reason": "公开报道中未找到财务数据"}
        return {"company": company, "source": "公开报道（Tavily 检索，非官方财报）",
                "available": True, "unit": "CNY", "metrics": metrics}
    except Exception as e:
        logging.warning("非上市财报检索失败 %s: %s", company, e)
        return {"available": False, "reason": "检索失败"}
