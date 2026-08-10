"""ecommerce.py — 跨境电商模块：评论分析引擎

第四版核心：评论文本 → 解析（情感/维度/痛点）→ 聚类（Top 痛点/卖点/建议）
→ 平台风格 Listing 生成。零 API 依赖（用户粘贴 + 演示数据），合规真实。

复用 llm._chat / _parse_json 底座。
"""
import json
import re

from llm import _chat, _parse_json

# 消费电子通用维度
ASPECTS = ["电池续航", "降噪", "佩戴舒适", "蓝牙连接", "音质", "做工质量", "价格", "物流", "售后", "其他"]

REVIEW_PARSE_SYSTEM = """你是跨境电商评论分析师。分析每条用户评论，提取结构化信息。

输出 JSON：
{
  "reviews": [
    {
      "text": "评论原文（原样保留）",
      "sentiment": "positive/negative/neutral",
      "aspect": "维度（电池续航/降噪/佩戴舒适/蓝牙连接/音质/做工质量/价格/物流/售后/其他）",
      "pain_point": "抱怨点（负面评论才填，如：续航太短）",
      "praise_point": "卖点（正面评论才填，如：降噪效果惊艳）"
    }
  ]
}

要求：
- 逐条分析，不遗漏不合并
- text 必须与输入完全一致（原样保留，不翻译不改写）
- sentiment 判断：明确夸奖=positive，明确抱怨=negative，中性描述=neutral
- aspect 从给定维度中选一个最相关的
- 一条评论可能同时有 pain_point 和 praise_point（如"降噪好但续航差"）"""

REVIEW_SUMMARY_SYSTEM = """你是资深产品经理。根据评论分析结果（每条评论的 sentiment/aspect/痛点/卖点），输出产品改进洞察。

输出 JSON：
{
  "top_pains": [
    {"pain": "痛点描述（如：续航虚标严重）", "count": 出现次数, "aspect": "维度", "example": "引用原文（必须来自给定评论，不能编造）"}
  ],
  "top_praises": [
    {"praise": "卖点描述（如：降噪效果出色）", "count": 出现次数, "aspect": "维度", "example": "引用原文"}
  ],
  "overall_sentiment": "一句话总评（正面/负面/混合）",
  "improvement_suggestions": ["改进建议1（具体可执行）", "改进建议2"],
  "zh_summary": "中文总结（面向产品改进方向）"
}

要求：
- top_pains 按出现次数排序取前 5，top_praises 取前 5
- example 必须逐字来自给定评论，禁止编造引用
- 改进建议具体（如"将电池容量标注改为实测值"），不说空话
- 所有数字来自统计，不虚增"""


def _parse_reviews_batch(reviews: list) -> list:
    """分批解析评论（每批 10 条，防超长）"""
    parsed = []
    for i in range(0, len(reviews), 10):
        batch = reviews[i:i + 10]
        content = _chat([
            {"role": "system", "content": REVIEW_PARSE_SYSTEM},
            {"role": "user", "content": "评论列表（每行一条）:\n" + "\n".join(f"{j + 1}. {r}" for j, r in enumerate(batch))},
        ], use_json=True)
        data = _parse_json(content)
        parsed.extend(data.get("reviews", []))
    return parsed


def analyze_reviews(reviews: list) -> dict:
    """评论分析主流程：解析 → 统计 → 聚类"""
    if not reviews:
        raise ValueError("评论列表为空")
    # 防资源滥用：单次最多 100 条（10 批解析，约 10 次 API 调用）
    if len(reviews) > 100:
        raise ValueError(f"评论数量过多（{len(reviews)} 条），单次最多支持 100 条")

    # 1. 解析（逐条提取 sentiment/aspect/痛点/卖点）
    parsed = _parse_reviews_batch(reviews)

    # 2. 程序统计（可溯源，不依赖 AI 算数）
    sentiments = {"positive": 0, "negative": 0, "neutral": 0}
    aspect_counts = {}
    for r in parsed:
        s = r.get("sentiment", "neutral")
        sentiments[s] = sentiments.get(s, 0) + 1
        a = r.get("aspect", "其他")
        aspect_counts[a] = aspect_counts.get(a, 0) + 1

    # 3. AI 聚类（带原文引用的痛点/卖点）
    summary_content = _chat([
        {"role": "system", "content": REVIEW_SUMMARY_SYSTEM},
        {"role": "user", "content": json.dumps(parsed, ensure_ascii=False)},
    ], use_json=True)
    summary = _parse_json(summary_content)

    # 4. 引用真实性校验：宽松匹配（去空白/标点后比对），防幻觉但容忍轻微改写
    def _normalize(s):
        return re.sub(r"[\s，。！？,.!?;；:：'\"“”‘’\-]", "", str(s))

    normalized_reviews = {_normalize(r) for r in reviews}
    for pains in (summary.get("top_pains", []), summary.get("top_praises", [])):
        for item in pains:
            ex = item.get("example", "")
            if ex and _normalize(ex) not in normalized_reviews:
                item["example"] = "(引用校验失败，已移除)"

    return {
        "total": len(reviews),
        "parsed_count": len(parsed),
        "sentiments": sentiments,
        "aspect_counts": aspect_counts,
        "top_pains": summary.get("top_pains", []),
        "top_praises": summary.get("top_praises", []),
        "overall_sentiment": summary.get("overall_sentiment", ""),
        "improvement_suggestions": summary.get("improvement_suggestions", []),
        "zh_summary": summary.get("zh_summary", ""),
        "sample_basis": True,  # 基于提供的评论样本
    }


def clean_pasted_text(raw: str) -> list:
    """清洗粘贴的评论文本 → 逐条评论列表

    处理：去掉编号（1. 2. 3.）、去空行、去平台广告行/星级行、
    按换行拆分、去重。
    """
    lines = raw.split("\n")
    cleaned = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        # 去掉行首编号（1. / 1、 / 1) / [1]）——只匹配"数字+标点"，避免误删"10 很好用"这类评论
        line = re.sub(r"^\[\d+\]\s*", "", line)
        line = re.sub(r"^\d+[.、)]\s*", "", line)
        # 去星级行（如 ★★★★★ 或 ★★★★☆ 或 ★ 4.5）
        if re.match(r"^[★☆]{1,5}\s*$", line):
            continue
        if re.match(r"^★+\s*\d", line):
            continue
        # 去疑似广告行（"查看全部"等）
        if line in ("查看全部", "查看更多", "展开", "回复", "赞"):
            continue
        cleaned.append(line)
    # 去重（保持顺序）
    seen = set()
    result = []
    for c in cleaned:
        if c not in seen:
            seen.add(c)
            result.append(c)
    return result


COMPARE_SYSTEM = """你是跨境电商产品分析师。对比两个产品的评论痛点分析结果，找出差异化。

输出 JSON：
{
  "a_pains": "产品 A 的核心痛点（一句话，引用数据）",
  "b_pains": "产品 B 的核心痛点（一句话，引用数据）",
  "a_strengths": "产品 A 相对 B 的优势",
  "b_strengths": "产品 B 相对 A 的优势",
  "difference": "两者用户抱怨点的核心差异（2-3 句）",
  "strategy": "竞争策略建议（1-2 句，针对卖家）",
  "zh_summary": "中文总结"
}

要求：只基于给定的两组分析结果，不编造。"""


def compare_products(analysis_a: dict, analysis_b: dict) -> dict:
    """竞品对比：两组评论分析 → 差异化洞察"""
    content = _chat([
        {"role": "system", "content": COMPARE_SYSTEM},
        {"role": "user", "content": (
            "产品 A 分析:\n" + json.dumps({
                "top_pains": analysis_a.get("top_pains", []),
                "top_praises": analysis_a.get("top_praises", []),
                "zh_summary": analysis_a.get("zh_summary", ""),
            }, ensure_ascii=False) +
            "\n\n产品 B 分析:\n" + json.dumps({
                "top_pains": analysis_b.get("top_pains", []),
                "top_praises": analysis_b.get("top_praises", []),
                "zh_summary": analysis_b.get("zh_summary", ""),
            }, ensure_ascii=False)
        )},
    ], use_json=True)
    return _parse_json(content)


LISTING_SYSTEM = """你是跨境电商 Listing 文案专家。根据产品痛点分析结果和平台特性，生成英文产品 Listing。

输出 JSON：
{
  "title": "产品标题（按平台风格）",
  "bullets": ["卖点1", "卖点2", "卖点3", "卖点4", "卖点5"],
  "description": "产品描述段落（按平台风格）",
  "keywords": ["关键词1", "关键词2", "关键词3"],
  "zh_notes": ["中文要点"]
}

平台风格：
- 亚马逊：五点式 bullet（每个卖点一句，埋搜索关键词），描述偏功能参数
- 速卖通：短促促销风（价格导向，突出性价比），bullet 简短
- 阿里巴巴国际站：B2B 风（突出规格/认证/起订量/定制能力），面向批量采购

要求：
- 卖点必须来自给定的 top_praises（用户真实认可的），不编造
- 结合改进建议暗示产品在改善（如痛点"续航虚标"→ 卖点写"实测续航"）
- 英文输出，zh_notes 中文解释"""


def generate_listing(product: str, platform: str, analysis: dict) -> dict:
    """基于评论分析结果生成平台风格 Listing"""
    praises = analysis.get("top_praises", []) or []
    suggestions = analysis.get("improvement_suggestions", []) or []
    user_msg = (
        f"产品: {product}\n"
        f"平台: {platform}\n"
        f"用户认可的卖点（来自评论分析）: {json.dumps(praises, ensure_ascii=False)}\n"
        f"改进方向（来自痛点分析）: {json.dumps(suggestions, ensure_ascii=False)}\n"
        f"请生成 {platform} 风格的英文 Listing。"
    )
    content = _chat([
        {"role": "system", "content": LISTING_SYSTEM},
        {"role": "user", "content": user_msg},
    ], use_json=True)
    return _parse_json(content)


PRODUCT_ANALYSIS_SYSTEM = """你是跨境电商选品分析师。根据商品画像数据（采集自商品页面），输出选品评估。

输出 JSON：
{
  "assessment": "商品评估（2-3 句，基于规格/价格/卖点/卖家）",
  "price_position": "价格定位（高/中/低性价比，引用价格与规格对比）",
  "seller_trust": "卖家可信度评估（基于卖家信息，缺失则说明）",
  "selling_point_analysis": ["AI 提炼的核心卖点 1", "卖点 2", "卖点 3"],
  "risk_flags": ["规格信息缺失", "无卖家信息", "价格缺失"],
  "zh_summary": "中文总结（面向选品决策）"
}

要求：
- 只基于给定画像字段，禁止编造不存在的信息
- 缺失的字段（规格/卖家/价格）在 risk_flags 中明确标注
- 所有数字来自画像，不自行计算"""


def analyze_product_profile(item: dict) -> dict:
    """商品画像 → AI 选品分析（输入为采集的 Product schema）"""
    content = _chat([
        {"role": "system", "content": PRODUCT_ANALYSIS_SYSTEM},
        {"role": "user", "content": json.dumps(item, ensure_ascii=False)},
    ], use_json=True)
    return _parse_json(content)
