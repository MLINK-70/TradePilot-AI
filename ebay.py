"""ebay.py — eBay 商品分析模块

eBay Browse API（官方免费）：商品链接 → 商品信息/价格/评分/卖家。
需要 eBay 开发者 App ID（用户注册后填入 config）。

注意：eBay 是国外服务，调用需要梯子（与 DeepSeek 相反）。
"""
import json
import re

import requests

from llm import _chat, _parse_json

EBAY_API_BASE = "https://api.ebay.com/buy/browse/v1"


def parse_ebay_url(url: str) -> str | None:
    """从 eBay 链接提取 item ID（支持 /itm/ 和 /p/ 格式）"""
    m = re.search(r"/(?:itm|p)/(?:[^/]+/)?(\d{10,13})", url)
    return m.group(1) if m else None


def fetch_item(item_id: str, app_id: str) -> dict:
    """调用 eBay Browse API 获取商品信息"""
    resp = requests.get(
        f"{EBAY_API_BASE}/item/{item_id}",
        headers={
            "X-EBAY-C-MARKETPLACE-ID": "EBAY_US",
            "X-EBAY-C-ENDUSERCTX": "contextualLocation=country%3DUS",
            "Authorization": f"Bearer {app_id}",
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def analyze_item(item_id: str, app_id: str) -> dict:
    """拉取 eBay 商品并生成分析"""
    item = fetch_item(item_id, app_id)

    # 提取商品信息
    info = {
        "title": item.get("title", ""),
        "price": item.get("price", {}).get("value", ""),
        "currency": item.get("price", {}).get("currency", ""),
        "condition": item.get("condition", ""),
        "seller": item.get("seller", {}).get("username", ""),
        "seller_feedback": item.get("seller", {}).get("feedbackScore", 0),
        "item_web_url": item.get("itemWebUrl", ""),
    }

    # AI 分析商品（价格定位/卖家信誉/购买建议）
    content = _chat([
        {"role": "system", "content": "你是跨境电商选品分析师。根据 eBay 商品信息，输出简明的购买/选品分析。输出 JSON：{assessment: 商品评估（2-3句）, price_position: 价格定位（高/中/低性价比）, seller_trust: 卖家可信度评估, suggestion: 建议（1句）, zh_summary: 中文总结}"},
        {"role": "user", "content": json.dumps(info, ensure_ascii=False)},
    ], use_json=True)
    analysis = _parse_json(content)

    return {"item": info, "analysis": analysis}
