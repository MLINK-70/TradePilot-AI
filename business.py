"""business.py — 外贸业务模块：英文开发信生成

第三版核心：产品 + 目标市场 + 客户类型 + 公司信息 → 结构化英文开发信 + 中文要点
"""
from llm import _chat, _parse_json

OUTREACH_SYSTEM = """你是资深外贸业务员。根据产品、目标市场、客户类型、公司信息，写一封专业的英文开发信（Cold Outreach Email）。

输出 JSON：
{
  "subject": "邮件标题（英文，吸引点击，突出核心卖点）",
  "greeting": "称呼（如 Dear Purchasing Manager,）",
  "body": "正文（英文，3-4 段：开场自我介绍→产品价值→合作提议→行动号召）",
  "closing": "结尾（Best regards 等）",
  "signature": "签名（用提供的公司信息，不要编造）",
  "zh_notes": ["中文要点 1", "中文要点 2", "中文要点 3"]（解释英文逻辑，帮助用户理解）
}

要求：
- 语气专业但不生硬，B2B 风格
- 结合目标市场特点做本地化表达（如德国重品质、日本重细节）
- 突出产品卖点（基于提供的信息）
- 长度适中（正文 150-200 词）
- 签名必须用提供的公司名称/联系人/邮箱，不编造"""


def generate_outreach_email(product: str, market: str, customer_type: str,
                            company: str, contact: str, email: str,
                            selling_points: str = "") -> dict:
    """生成英文开发信 + 中文要点"""
    user_msg = (
        f"产品: {product}\n"
        f"目标市场: {market}\n"
        f"客户类型: {customer_type}\n"
        f"公司名称: {company}\n"
        f"联系人: {contact}\n"
        f"邮箱: {email}\n"
        f"产品卖点: {selling_points or '（未提供，按产品常识生成）'}\n"
        f"请生成英文开发信。"
    )
    content = _chat([
        {"role": "system", "content": OUTREACH_SYSTEM},
        {"role": "user", "content": user_msg},
    ], use_json=True)
    return _parse_json(content)
