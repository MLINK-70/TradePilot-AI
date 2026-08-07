"""aliexpress.py — 速卖通商品分析模块

AliExpress 联盟开放平台（openapi.taobaol.com）：商品 ID → 商品信息/价格/评分/卖家。

**鉴权说明（重要）**：联盟开放平台用 App Key + App Secret 做 **HmacSHA256 签名**，
不是把 Key 直接当 Bearer token。
签名流程：参数按 ASCII 码排序拼接 → 前加 API 路径 → HMAC-SHA256(secret) → hex。

需要：注册速卖通联盟（https://pub.aliexpress.com）→ 开放平台创建应用 → 拿 App Key + App Secret。
注意：速卖通是阿里系服务，国内直连可用（与 eBay 相反，无需梯子）。
"""
import hashlib
import hmac
import json
import re
import time

import requests

from llm import _chat, _parse_json

ALIEXPRESS_API_BASE = "https://openapi.taobaol.com/router/api"
ALIEXPRESS_API_PATH = "aliexpress.affiliate.productdetail.get"


def _sign(secret: str, api_path: str, params: dict) -> str:
    """按淘宝开放平台规范生成 HmacSHA256 签名（参数 ASCII 排序拼接 → 前加 API 路径）"""
    keys = sorted(k for k in params if k != "sign")
    raw = api_path + "".join(f"{k}{params[k]}" for k in keys)
    return hmac.new(secret.encode("utf-8"), raw.encode("utf-8"), hashlib.sha256).hexdigest()


def request_product_detail(app_key: str, app_secret: str, product_id: str, currency: str = "USD") -> dict:
    """调用联盟商品详情接口，返回原始响应 JSON"""
    params = {
        "method": ALIEXPRESS_API_PATH,
        "sign_method": "hmac-sha256",
        "app_key": app_key,
        "timestamp": str(int(time.time())),
        "partner_id": "tradepilot",
        "product_id": str(product_id),
        "currency": currency,
    }
    params["sign"] = _sign(app_secret, ALIEXPRESS_API_PATH, params)
    resp = requests.post(ALIEXPRESS_API_BASE, data=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def parse_aliexpress_url(url: str) -> str | None:
    """从速卖通链接提取商品 ID

    支持格式：
    - https://www.aliexpress.com/item/1005001234567890.html（标准商品页）
    - https://www.aliexpress.com/item/Product-Name/1005001234567890.html（带标题前缀）
    - https://www.aliexpress.com/i/1005001234567890.html（短格式）
    - https://www.aliexpress.com/item/1005001234567890.html?spm=...（带查询参数）
    注意：速卖通短链接（a.aliexpress.com/xxx）不含商品 ID，无法解析（提示用户用完整链接）。
    """
    m = re.search(r"/(?:item|i)/([^?#]+)", url)
    if m:
        # 路径段中找第一个 10-20 位数字（商品 ID；标题里的年份/数字远短于此）
        mid = re.search(r"\d{10,20}", m.group(1))
        return mid.group(0) if mid else None
    return None


def get_product_info(app_key: str, app_secret: str, product_id: str, currency: str = "USD") -> dict:
    """联盟商品详情 → 结构化商品信息（无 AI）

    从联盟响应（aliexpress_affiliate_productdetail_get_response →
    resp_result → result → products）中提取字段。
    """
    raw = request_product_detail(app_key, app_secret, product_id, currency)

    # 联盟响应按 resp_result.code 判断成败（0 成功）
    resp_result = raw.get("aliexpress_affiliate_productdetail_get_response", {}).get("resp_result", {})
    if resp_result.get("code") not in (None, 0, "0"):
        raise ValueError(f"速卖通 API 返回错误: {resp_result.get('code')} {resp_result.get('msg')}")

    # 商品对象可能在 products / result / 直接挂在 resp_result 下（不同文档版本）
    products = (
        resp_result.get("products") or
        resp_result.get("result", {}).get("products") or
        []
    )
    if not products:
        raise ValueError("速卖通 API 未返回商品数据（商品 ID 可能无效或接口权限不足）")
    p = products[0] if isinstance(products, list) else products

    info = {
        "title": p.get("product_title") or p.get("subject") or p.get("title", ""),
        "price": p.get("sale_price") or p.get("price") or "",
        "currency": p.get("currency") or currency,
        "rating": p.get("evaluation_rate") or p.get("rating", ""),
        "trade_count": p.get("trade_count") or p.get("sales", ""),
        "seller": p.get("seller") or p.get("seller_name") or "",
        "seller_rating": p.get("seller_positive_rate") or "",
        "item_url": p.get("detail_url") or p.get("product_detail_url") or "",
        "image_url": p.get("image") or (p.get("images", []) or [""])[0],
        "orders": p.get("order_count") or "",
    }
    return info


def analyze_product(app_key: str, app_secret: str, product_id: str, currency: str = "USD") -> dict:
    """拉取速卖通商品并生成分析（价格定位/卖家信誉/采购建议）"""
    info = get_product_info(app_key, app_secret, product_id, currency)

    content = _chat([
        {"role": "system", "content": "你是跨境电商选品分析师。根据速卖通商品信息，输出简明的采购/选品分析。输出 JSON：{assessment: 商品评估（2-3句）, price_position: 价格定位（高/中/低性价比）, seller_trust: 卖家可信度评估, suggestion: 建议（1句）, zh_summary: 中文总结}"},
        {"role": "user", "content": json.dumps(info, ensure_ascii=False)},
    ], use_json=True)
    analysis = _parse_json(content)

    return {"item": info, "analysis": analysis}
