"""leads.py — 客户线索模块（v1.0 阶段 2.2）

Tavily 多组查询词检索 → LLM 提炼客户线索画像 → 防幻觉硬约束
（无公司名/无来源 URL / URL 不在搜索结果中 → 一律剔除）
→ 一键生成针对该公司的开发信（画像注入 business 模块，形成闭环）。

数据声明：线索来自公开网页检索，仅供业务开发参考，发送前需人工核实。
"""
import logging

from market_data import _search_web

DISCLAIMER = "线索来自公开网页检索（Tavily），仅供业务开发参考，联系前请人工核实。"

# 多组查询词：覆盖分销/零售/批发/进口/采购等角色
QUERY_TEMPLATES = (
    "{product} {country} distributor",
    "{product} {country} retailer",
    "{product} {country} wholesaler",
    "{product} {country} importer",
    "{product} {country} buyer",
    "{product} {country} procurement",
)

LEADS_SYSTEM = """你是外贸客户线索分析师。从给定的网页搜索结果中，识别潜在的目标客户公司（分销商/零售商/批发商/进口商/采购方）。

输出 JSON：
{
  "leads": [
    {
      "company": "公司名",
      "business_scope": "业务范围（1-2句，如「消费电子分销，覆盖北欧市场」）",
      "size_signal": "规模信号（员工数/年营收/门店数等，只在搜索结果中明确出现时填写，否则留空字符串）",
      "match_reason": "匹配理由（为什么这家公司可能是目标客户，1句）",
      "source_url": "来源 URL（必须来自搜索结果，禁止编造）"
    }
  ]
}

要求：
- 只输出 JSON 对象，不要任何解释文字
- 每条线索必须能在搜索结果中找到依据；source_url 必须是搜索结果中真实存在的 URL
- 没有明确公司名或没有来源 URL 的信息不要列入
- 最多 8 条，按匹配度从高到低排序"""


def _normalize_url(url: str) -> str:
    """URL 归一化：去协议/尾部斜杠/大小写，用于来源校验"""
    u = url.strip().rstrip("/")
    for prefix in ("https://", "http://"):
        if u.startswith(prefix):
            u = u[len(prefix):]
            break
    return u.lower()


def _search_leads_raw(product: str, country: str,
                      max_queries: int = 4, results_per_query: int = 4) -> list:
    """多组查询词检索 + 按 URL 去重"""
    seen = set()
    out = []
    for tpl in QUERY_TEMPLATES[:max_queries]:
        q = tpl.format(product=product, country=country)
        for r in _search_web(q, max_results=results_per_query):
            url = (r.get("url") or "").strip()
            if not url or _normalize_url(url) in seen:
                continue
            seen.add(_normalize_url(url))
            out.append(r)
        if len(out) >= 30:
            break
    return out


def _extract_leads(product: str, country: str, raw_results: list) -> list:
    """LLM 提炼线索 + 防幻觉硬约束（无公司名/来源 URL 不在结果中 → 剔除）"""
    from llm import _chat, _parse_json

    seen_urls = {_normalize_url(r.get("url", "")) for r in raw_results if r.get("url")}
    snippets = []
    for i, r in enumerate(raw_results[:30], 1):
        content = (r.get("content") or "").strip()[:300]
        snippets.append(f"{i}. 标题: {(r.get('title') or '').strip()}\n   URL: {(r.get('url') or '').strip()}\n   摘要: {content}")

    user_msg = (
        f"产品: {product}\n目标市场: {country}\n\n"
        f"搜索结果（共 {len(snippets)} 条）:\n\n" + "\n\n".join(snippets)
    )
    leads = []
    for attempt in range(2):
        content = _chat([
            {"role": "system", "content": LEADS_SYSTEM},
            {"role": "user", "content": user_msg},
        ], use_json=True)
        data = _parse_json(content)
        leads = data.get("leads", []) or []
        if leads or attempt == 1:
            break

    # 防幻觉硬约束（v1.0 验收：客户线索全部带来源 URL，无幻觉）
    clean = []
    for ld in leads:
        if not isinstance(ld, dict):
            continue
        company = str(ld.get("company", "")).strip()
        url = str(ld.get("source_url", "")).strip()
        if not company or not url:
            continue
        if _normalize_url(url) not in seen_urls:
            logging.warning("线索 %s 的 URL 不在搜索结果中，已剔除（防幻觉）", company)
            continue
        clean.append({
            "company": company[:100],
            "business_scope": str(ld.get("business_scope", "")).strip()[:200],
            "size_signal": str(ld.get("size_signal", "")).strip()[:100],
            "match_reason": str(ld.get("match_reason", "")).strip()[:200],
            "source_url": url[:300],
        })
    return clean[:8]


def find_leads(product: str, country: str) -> dict:
    """主入口：产品 + 目标市场 → 客户线索列表（带来源 URL + 免责声明）"""
    product = (product or "").strip()
    country = (country or "").strip()
    if not product or not country:
        raise ValueError("产品和国家不能为空")

    raw = _search_leads_raw(product, country)
    if not raw:
        return {
            "leads": [], "disclaimer": DISCLAIMER,
            "message": "未检索到相关线索（请确认搜索 Key 已配置：Tavily）",
        }
    leads = _extract_leads(product, country, raw)
    return {"leads": leads, "disclaimer": DISCLAIMER}


def build_lead_outreach(lead: dict, product: str, country: str,
                        company: str = "", contact: str = "", email: str = "",
                        hook: str = "免费样品") -> dict:
    """闭环：线索画像 → 针对该公司的开发信（注入 business 模块）"""
    from business import generate_outreach_email

    lead = lead or {}
    scope = str(lead.get("business_scope", "")).strip()
    size = str(lead.get("size_signal", "")).strip()
    # 画像注入：业务范围+规模信号拼进收件人背景（customer_title 位），让 AI 定制内容
    profile = "；".join(filter(None, [scope, f"规模信号：{size}" if size else ""]))[:150]
    return generate_outreach_email(
        product=product,
        market=country,
        customer_type="潜在客户（线索画像：进口商/分销商/零售商）",
        company=company, contact=contact, email=email,
        customer_company=str(lead.get("company", "")).strip(),
        customer_contact="",
        customer_title=profile,
        hook=hook,
        credentials="",
        selling_points="",
    )
