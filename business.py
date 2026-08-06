"""business.py — 外贸业务模块：英文开发信生成

第三版核心：产品 + 目标市场 + 客户类型 + 公司信息 → 结构化英文开发信 + 中文要点
"""
from llm import _chat, _parse_json

OUTREACH_SYSTEM = """你是资深外贸业务员。根据产品、目标市场、客户类型、公司信息，写一封专业的英文开发信（Cold Outreach Email）。

输出 JSON：
{
  "subject": "邮件标题（英文，公式：产品+市场+钩子，如 {Product} for {Market} – Free Sample）",
  "greeting": "称呼（有收件人用 Dear Mr./Ms. [姓]，只有公司用 Dear [Company] Team，都没用 Dear Purchasing Manager,）",
  "body": "正文（英文，3 段式：①Who we are 1-2句 → ②Value+卖点1-2条+钩子 → ③单一低门槛CTA）",
  "closing": "结尾（Best regards 等）",
  "signature": "签名（用提供的发件人信息，不编造）",
  "zh_notes": ["中文要点 1", "中文要点 2", "中文要点 3"]（解释英文逻辑，帮助用户理解）
}

要求：
- 语气专业但不生硬，B2B 风格
- 正文总长度 100-150 词（越短回复率越高）
- 结合目标市场特点做本地化表达（如德国重品质、日本重细节）
- 信任背书放第②段（出口年限/认证/现有客户市场，如 "CE/FCC certified, supplying EU for 8 years"）
- 钩子用提供的（如免费样品/目录/报价）
- CTA 具体且低门槛（如 "Can I send you our catalog?"），不用 "Let's cooperate"
- 签名必须用提供的发件人信息；若信息是 [Your Company Name] 这类占位符，原样保留不替换、不编造"""


def generate_outreach_email(product: str, market: str, customer_type: str,
                            company: str = "", contact: str = "", email: str = "",
                            selling_points: str = "",
                            customer_company: str = "", customer_contact: str = "",
                            customer_title: str = "",
                            hook: str = "免费样品", credentials: str = "") -> dict:
    """生成英文开发信 + 中文要点（发件/收件信息均可选，缺失用占位符不编造）"""
    # 发件人信息缺失时用占位符，禁止 AI 编造
    company = company or "[Your Company Name]"
    contact = contact or "[Your Name]"
    email = email or "[your.email@company.com]"
    # 收件人信息（可选，缺失则用通用称呼）
    recipient = "（未提供）"
    if customer_contact and customer_title:
        recipient = f"{customer_contact}（{customer_title}）"
    elif customer_contact:
        recipient = customer_contact
    elif customer_company:
        recipient = customer_company
    # 钩子映射
    hook_map = {
        "免费样品": "Free sample for your testing",
        "产品目录": "Send you our full product catalog",
        "报价单": "Send you our competitive quotation",
    }
    hook_en = hook_map.get(hook, hook)
    user_msg = (
        f"产品: {product}\n"
        f"目标市场: {market}\n"
        f"客户类型: {customer_type}\n"
        f"收件人: {recipient}\n"
        f"收件人公司: {customer_company or '（未提供）'}\n"
        f"发件公司名称: {company}\n"
        f"发件联系人: {contact}\n"
        f"发件邮箱: {email}\n"
        f"钩子: {hook_en}\n"
        f"信任背书: {credentials or '（未提供，按产品常识写通用背书）'}\n"
        f"产品卖点: {selling_points or '（未提供，按产品常识生成）'}\n"
        f"请生成英文开发信（100-150 词，3 段式）。"
    )
    content = _chat([
        {"role": "system", "content": OUTREACH_SYSTEM},
        {"role": "user", "content": user_msg},
    ], use_json=True)
    return _parse_json(content)


FOLLOWUP_SYSTEM = """你是资深外贸业务员。基于已经发送的开发信，写一封**跟进邮件**（Follow-up Email）——开发信发出后 3-7 天未获回复时的第二封。

输出 JSON：
{
  "subject": "邮件标题（用 Re: 开头带原主题，如 Re: {原主题} – One More Thought）",
  "greeting": "称呼（同首封：有收件人用姓名，否则 Dear Purchasing Manager,）",
  "body": "正文（英文，60-90 词，3 段：①提及首封被错过 ②给一个新信息点 ③轻量 CTA）",
  "closing": "结尾（Best regards 等）",
  "signature": "签名（同首封发件人信息）",
  "zh_notes": ["中文要点 1", "中文要点 2", "中文要点 3"]
}

要求：
- 开头标准话术：\"I'm writing again in case my previous email was missed.\"（实战高频）
- 中间**给新信息**（新卖点/新背书/新案例），不重复首封内容
- CTA 轻量：如 \"Can I send you our catalog?\" 或 \"Would a sample help?\"（低门槛）
- 总长度 60-90 词（跟进比首封更短）
- 签名必须用提供的发件人信息，占位符原样保留不编造"""


IDEA_SYSTEM = """你是资深外贸业务员。用户给出**核心思路**（一句话，可能有产品/市场/客户类型/卖点/钩子/背书等碎片信息），你负责扩写成一封完整的英文开发信（Cold Outreach Email）。

先拆解用户的思路：
- 提取产品、目标市场、客户类型（经销商/零售商/品牌商）、卖点、钩子、信任背书
- 缺失的信息按行业常识合理补全（不编造具体事实，用通用表达）

输出 JSON：
{
  "subject": "邮件标题（英文，公式：产品+市场+钩子）",
  "greeting": "称呼（有收件人用 Dear Mr./Ms. [姓]，否则 Dear Purchasing Manager,）",
  "body": "正文（英文 3 段式，100-150 词：①Who we are ②Value+卖点+钩子 ③单一低门槛 CTA）",
  "closing": "结尾（Best regards 等）",
  "signature": "签名（用提供的发件人信息，不编造）",
  "zh_notes": ["中文要点 1", "中文要点 2", "中文要点 3"]（解释扩写逻辑）
}

要求：
- 扩写忠实于用户思路，不偏离核心意图
- 信任背书写进第②段（出口年限/认证等，来自思路或常识）
- CTA 具体低门槛（如 "Can I send you our catalog?"），不用 "Let's cooperate"
- **若提供了真实贸易数据，在第②段自然引用 1 句**（如 "our product matches the growing demand for {产品} in {市场}"），增强说服力
- 签名必须用提供的发件人信息；占位符原样保留不编造"""


IDEA_PARSE_SYSTEM = """你是外贸业务信息解析器。从用户的核心思路中提取结构化信息。

输出 JSON：
{
  "product": "产品名（如：降噪耳机）",
  "market": "目标市场（如：德国）",
  "customer_type": "客户类型（经销商/零售商/品牌商/电商卖家）",
  "selling_points": ["卖点1", "卖点2"],
  "hook": "钩子（免费样品/产品目录/报价单）",
  "credentials": "信任背书（认证/出口年限，没有则空字符串）"
}

要求：提取思路中明确提到的；缺失的用常识合理推断（客户类型默认经销商，钩子默认免费样品）。"""


def generate_outreach_from_idea(idea: str, company: str = "", contact: str = "",
                                email: str = "", customer_company: str = "",
                                customer_contact: str = "", customer_title: str = "") -> dict:
    """核心思路 → 完整开发信（AI 拆解 + 真实贸易数据支撑 + 扩写）"""
    company = company or "[Your Company Name]"
    contact = contact or "[Your Name]"
    email = email or "[your.email@company.com]"
    recipient = "（未提供）"
    if customer_contact and customer_title:
        recipient = f"{customer_contact}（{customer_title}）"
    elif customer_contact:
        recipient = customer_contact
    elif customer_company:
        recipient = customer_company

    # 第一步：AI 拆解思路 → 结构化（产品/市场等）
    parse_content = _chat([
        {"role": "system", "content": IDEA_PARSE_SYSTEM},
        {"role": "user", "content": f"核心思路: {idea}"},
    ], use_json=True)
    parsed = _parse_json(parse_content)
    product = str(parsed.get("product", "")).strip()
    market = str(parsed.get("market", "")).strip()

    # 第二步：查真实贸易数据（产品→HS编码→目标市场趋势）
    trade_line = ""
    try:
        from trade import hs_lookup, partner_lookup, query_trend, summarize_trend
        hs = hs_lookup(product)
        if hs:
            pc = partner_lookup(market)
            if pc:
                from trade import get_latest_year
                latest = get_latest_year()
                _, rows, trend = query_trend(product, market, list(range(latest - 2, latest + 1)))
                if trend:
                    years = sorted(trend.keys())
                    first, last = years[0], years[-1]
                    v1, v2 = trend[first]["value"], trend[last]["value"]
                    trade_line = (
                        f"\n真实贸易数据（UN Comtrade，供引用）:\n"
                        f"- {product}(HS{hs}) 出口至 {market}，{first}-{last} 年出口额从 "
                        f"{v1 / 1e8:.1f} 亿美元到 {v2 / 1e8:.1f} 亿美元\n"
                        f"- 开发信中可引用：\"我们的产品正好匹配贵市场{market}对{product}的持续需求\""
                    )
    except Exception:
        trade_line = ""  # 数据查询失败不阻断开发信生成

    # 第三步：扩写开发信（含真实数据）
    idea_detail = (
        f"拆解结果: 产品={product}, 市场={market}, 客户类型={parsed.get('customer_type', '经销商')}, "
        f"卖点={parsed.get('selling_points', [])}, 钩子={parsed.get('hook', '免费样品')}, "
        f"背书={parsed.get('credentials', '')}"
    )
    user_msg = (
        f"用户核心思路: {idea}\n"
        f"{idea_detail}"
        f"{trade_line}\n"
        f"收件人: {recipient}\n"
        f"收件人公司: {customer_company or '（未提供）'}\n"
        f"发件公司名称: {company}\n"
        f"发件联系人: {contact}\n"
        f"发件邮箱: {email}\n"
        f"请扩写成完整英文开发信；若提供了真实贸易数据，在正文中自然引用（增强说服力）。"
    )
    content = _chat([
        {"role": "system", "content": IDEA_SYSTEM},
        {"role": "user", "content": user_msg},
    ], use_json=True)
    result = _parse_json(content)
    result["_trade_data"] = trade_line.strip()  # 附带数据供前端展示来源
    return result


PRODUCT_INTRO_SYSTEM = """你是资深外贸产品经理。根据用户提供的核心思路（产品信息），生成一份英文**产品介绍**（Product Introduction），用于客户回询时发送/附件/官网展示。

输出 JSON：
{
  "product_name": "产品英文名",
  "overview": "2-3 句产品概述（定位+核心价值）",
  "key_features": ["卖点1（英文，具体）", "卖点2", "卖点3", "卖点4"],
  "specs": [{"item": "规格项", "value": "规格值"}],  （4-6 条：如电池/Battery, 续航/Battery Life, 防水/Waterproof, 材质/Material, 认证/Certification）
  "applications": ["应用场景1", "应用场景2", "应用场景3"],
  "advantages": "2-3 句差异化优势（对比竞品/性价比）",
  "zh_summary": "中文一句话总结"
}

要求：
- 规格如思路未提供，用行业通用参数合理推断（标注 "TBD" 表示待确认，不编造精确值）
- 英文输出为主，zh_summary 中文
- 内容客观，不夸大"""


FAQ_SYSTEM = """你是资深外贸业务员。根据核心思路（产品信息），生成英文 **FAQ**（客户常见问题应答），供客户回询时参考。

输出 JSON：
{
  "faqs": [
    {"q": "英文问题", "a": "英文回答（1-2 句）", "zh": "中文要点"}
  ]
}

要求：
- 覆盖 5-6 个外贸高频问题：认证（Certification）、交期（Lead Time）、起订量（MOQ）、付款（Payment Terms）、样品（Samples）、售后（After-sales）
- 思路中提供了的信息用真实值；未提供的用合理通用表达（如 "Lead time: typically 25-30 days" 标注为参考）
- 回答简洁专业"""


def generate_product_intro(idea: str, product_hint: str = "") -> dict:
    """核心思路 → 产品介绍 + FAQ"""
    user_msg = f"核心思路: {idea}\n请生成产品介绍。"
    intro_content = _chat([
        {"role": "system", "content": PRODUCT_INTRO_SYSTEM},
        {"role": "user", "content": user_msg},
    ], use_json=True)
    intro = _parse_json(intro_content)

    faq_content = _chat([
        {"role": "system", "content": FAQ_SYSTEM},
        {"role": "user", "content": f"核心思路: {idea}\n请生成 FAQ。"},
    ], use_json=True)
    faq = _parse_json(faq_content)

    return {"intro": intro, "faqs": faq.get("faqs", [])}


SIMULATE_SYSTEM = """你是 {market} 的 {customer_type}（采购商），正在与一家 {product} 供应商沟通。扮演一个真实、专业的海外采购商。

**人设**（基于市场特点）：
- {market} 采购商：{market_trait}（如德国重品质认证、美国重价格交期、日本重细节服务）
- 你收到供应商的开发信/回复，正在评估是否合作

**行为要求**：
1. 回复要真实：可能询价、问认证、讨价还价、要求样品，也可能礼貌拒绝或拖延
2. 语气专业，符合该市场采购商的风格（德国正式、美国直接、日本客气）
3. 每次回复 2-4 句，聚焦一个关注点（不要一次问完所有问题）
4. 关注点从市场特质中选：认证/价格/交期/MOQ/样品/售后

输出 JSON（字段）：
- reply: 英文回复（采购商口吻，2-4 句）
- zh_translation: 中文翻译
- concern: 本次关注的要点（如：价格/认证/交期）
- coach: 中文教练点评（用户这轮表现：说得好/该改进，下轮怎么应对）"""


def simulate_customer(product: str, market: str, customer_type: str,
                      user_message: str, history: list = None) -> dict:
    """AI 扮演采购商回复（模拟客户沟通练习）"""
    market_traits = {
        "德国": "德国采购商重视品质与认证（CE/RoHS），对价格敏感度中等，谈判直接但礼貌",
        "美国": "美国采购商重视价格和交期，回复直接，喜欢快速推进",
        "日本": "日本采购商重视细节和长期关系，回复客气但要求严格",
        "英国": "英国采购商重视专业和合规，沟通正式",
        "法国": "法国采购商重视品质和品牌，对细节要求高",
        "东南亚": "东南亚采购商重视价格和样品，决策链短",
        "中东": "中东采购商重视关系和报价，喜欢讨价还价",
    }
    trait = market_traits.get(market, "重视品质与价格平衡")

    system = SIMULATE_SYSTEM.format(
        market=market, customer_type=customer_type,
        product=product, market_trait=trait,
    )

    messages = [{"role": "system", "content": system}]
    # 历史对话（用户+AI 交替）
    for h in (history or [])[-6:]:
        messages.append({"role": h["role"], "content": h["content"]})
    messages.append({"role": "user", "content": user_message})

    content = _chat(messages, use_json=True)
    return _parse_json(content)


def generate_followup_email(product: str, market: str, customer_type: str,
                            original_subject: str,
                            company: str = "", contact: str = "", email: str = "",
                            customer_contact: str = "", customer_title: str = "",
                            customer_company: str = "") -> dict:
    """生成跟进邮件（基于开发信上下文）"""
    company = company or "[Your Company Name]"
    contact = contact or "[Your Name]"
    email = email or "[your.email@company.com]"
    recipient = "（未提供）"
    if customer_contact and customer_title:
        recipient = f"{customer_contact}（{customer_title}）"
    elif customer_contact:
        recipient = customer_contact
    elif customer_company:
        recipient = customer_company
    user_msg = (
        f"产品: {product}\n"
        f"目标市场: {market}\n"
        f"客户类型: {customer_type}\n"
        f"原开发信主题: {original_subject or '（未提供）'}\n"
        f"收件人: {recipient}\n"
        f"收件人公司: {customer_company or '（未提供）'}\n"
        f"发件公司名称: {company}\n"
        f"发件联系人: {contact}\n"
        f"发件邮箱: {email}\n"
        f"请生成跟进邮件（60-90 词）。"
    )
    content = _chat([
        {"role": "system", "content": FOLLOWUP_SYSTEM},
        {"role": "user", "content": user_msg},
    ], use_json=True)
    return _parse_json(content)
