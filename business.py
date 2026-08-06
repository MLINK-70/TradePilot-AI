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
- 签名必须用提供的发件人信息；占位符原样保留不编造"""


def generate_outreach_from_idea(idea: str, company: str = "", contact: str = "",
                                email: str = "", customer_company: str = "",
                                customer_contact: str = "", customer_title: str = "") -> dict:
    """核心思路 → 完整开发信（AI 拆解扩写）"""
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
        f"用户核心思路: {idea}\n"
        f"收件人: {recipient}\n"
        f"收件人公司: {customer_company or '（未提供）'}\n"
        f"发件公司名称: {company}\n"
        f"发件联系人: {contact}\n"
        f"发件邮箱: {email}\n"
        f"请拆解思路并扩写成完整英文开发信。"
    )
    content = _chat([
        {"role": "system", "content": IDEA_SYSTEM},
        {"role": "user", "content": user_msg},
    ], use_json=True)
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
