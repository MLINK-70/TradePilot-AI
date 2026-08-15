"""ebay.py — eBay 商品分析模块

eBay Browse API（官方免费）：商品链接 → 商品信息/价格/评分/卖家。

**鉴权说明（重要）**：Browse API 需要 **OAuth 2.0 access token**
（client_credentials 流程），不是把 App ID 直接当 Bearer token。
流程：App ID + Client Secret → POST https://api.ebay.com/identity/v1/oauth2/token
→ 拿 access_token（有效期 2 小时）→ 才能调 Browse API。

需要：用户注册 eBay 开发者账号拿到 App ID + Client Secret（审核通过后）。
注意：eBay 是国外服务，调用需要梯子（与 DeepSeek 相反）。
"""
import json
import re

import requests

from llm import _chat, _parse_json

EBAY_API_BASE = "https://api.ebay.com/buy/browse/v1"
EBAY_OAUTH_URL = "https://api.ebay.com/identity/v1/oauth2/token"


class EbayTokenExpired(Exception):
    """eBay OAuth token 失效（401），调用方应刷新 token 重试一次"""


# token 缓存（回归修复：原每次请求都重新换取；token 有效期 2 小时，
# 提前 5 分钟过期，避免临界窗口 401）
_token_cache = {"token": "", "expires_at": 0.0}


def get_oauth_token(app_id: str, client_secret: str, force: bool = False) -> str:
    """获取 OAuth access token（client_credentials 流程，有效期 2 小时）

    模块级缓存：未过期直接复用（force=True 强制刷新，供 401 重试）。
    """
    import base64
    import time
    now = time.time()
    if not force and _token_cache["token"] and _token_cache["expires_at"] - now > 300:
        return _token_cache["token"]
    auth = base64.b64encode(f"{app_id}:{client_secret}".encode()).decode()
    resp = requests.post(
        EBAY_OAUTH_URL,
        headers={
            "Authorization": f"Basic {auth}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={"grant_type": "client_credentials",
              "scope": "https://api.ebay.com/oauth/api_scope/buy.item.retrieve"},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    token = data.get("access_token")
    if not token:  # 回归修复：响应缺 access_token 时不再 KeyError 裸抛
        raise ValueError("eBay OAuth 响应缺少 access_token")
    _token_cache["token"] = token
    _token_cache["expires_at"] = now + int(data.get("expires_in", 7200))
    return token


def parse_ebay_url(url: str) -> str | None:
    """从 eBay 链接提取 item ID

    支持格式：
    - /itm/123456789012（标准）
    - /itm/Product-Name/123456789012（带标题）
    - /p/1234567890（产品页，ID 可为 9 位——回归修复：原要求至少 10 位）
    - /itm/123456789012?hash=...（带查询参数）
    注意：ebay.us 短链接不含 item ID，无法解析（提示用户用完整链接）。
    """
    # 优先匹配 /itm/ 或 /p/ 后的数字（可能带标题前缀）
    m = re.search(r"/(?:itm|p)/(?:[^/]+/)?(\d{8,13})", url)
    if m:
        return m.group(1)
    # 兼容 itm 在查询参数中的情况（如 ?item=123456789012）
    m2 = re.search(r"[?&]item[=:](\d{8,13})", url)
    return m2.group(1) if m2 else None


def fetch_item(item_id: str, access_token: str) -> dict:
    """调用 eBay Browse API 获取商品信息（access_token 来自 get_oauth_token）

    回归修复：404（下架）/401（token 过期）映射为明确错误而非裸 HTTPError。
    """
    resp = requests.get(
        f"{EBAY_API_BASE}/item/{item_id}",
        headers={
            "X-EBAY-C-MARKETPLACE-ID": "EBAY_US",
            "X-EBAY-C-ENDUSERCTX": "contextualLocation=country%3DUS",
            "Authorization": f"Bearer {access_token}",
        },
        timeout=30,
    )
    if resp.status_code == 404:
        raise ValueError("eBay 商品不存在或已下架（HTTP 404）")
    if resp.status_code == 401:
        raise EbayTokenExpired("eBay token 已过期，正在刷新重试")
    resp.raise_for_status()
    return resp.json()


def analyze_item(item_id: str, access_token: str) -> dict:
    """拉取 eBay 商品并生成分析"""
    item = fetch_item(item_id, access_token)

    # 提取商品信息（防御性取值：price/seller/condition 可能缺失或为 None，
    # 下架/异常商品不会因 AttributeError 整条失败；condition 实际是 dict）
    price = item.get("price") or {}
    seller = item.get("seller") or {}
    cond = item.get("condition") or {}
    info = {
        "title": item.get("title", ""),
        "price": price.get("value", ""),
        "currency": price.get("currency", ""),
        "condition": cond.get("conditionDisplayName", "") if isinstance(cond, dict) else str(cond or ""),
        "seller": seller.get("username", ""),
        "seller_feedback": seller.get("feedbackScore", 0),
        "item_web_url": item.get("itemWebUrl", ""),
    }

    # AI 分析商品（价格定位/卖家信誉/购买建议）
    content = _chat([
        {"role": "system", "content": "你是跨境电商选品分析师。根据 eBay 商品信息，输出简明的购买/选品分析。输出 JSON：{assessment: 商品评估（2-3句）, price_position: 价格定位（高/中/低性价比）, seller_trust: 卖家可信度评估, suggestion: 建议（1句）, zh_summary: 中文总结}"},
        {"role": "user", "content": json.dumps(info, ensure_ascii=False)},
    ], use_json=True)
    analysis = _parse_json(content)

    return {"item": info, "analysis": analysis}
