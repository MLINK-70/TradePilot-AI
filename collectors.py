"""collectors.py — 商品数据采集层（混合方案：能抓则抓 + 粘贴兜底）

策略：
1. 有 pasted_text → 直接 AI 提取（粘贴兜底，全平台通用）
2. 亚马逊 URL → 程序抓取公开页（唯一纯 HTTP 可爬的主流平台）
3. 其他/失败 → JSON-LD Product 块 + og: 标签提取
4. 仍无数据 → AI 提取页面文本

原则：只采公开页面公开信息，不做反爬对抗（不破验证码/登录墙/隐藏接口）。
SSRF 防护：仅 https + 拒绝内网 IP。
"""
import ipaddress
import json
import logging
import re
import time
from urllib.parse import urlparse

import requests

from llm import _chat, _parse_json

UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}


class CollectorError(Exception):
    """采集失败（网络/平台不支持/页面结构变化）"""


def _safe_url(url: str) -> str:
    """SSRF 防护：仅 https、拒内网/保留地址"""
    u = urlparse(url)
    if u.scheme != "https":
        raise CollectorError("仅支持 https 链接")
    host = u.hostname or ""
    try:
        ip = ipaddress.ip_address(host)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise CollectorError("不支持内网地址")
    except ValueError:
        pass  # 域名，交由 DNS 解析层（requests 默认不解析内网重定向）
    return url


def _fetch_html(url: str, timeout: int = 15) -> str:
    """抓取页面 HTML（强制直连，不重试，大小上限 2MB）"""
    url = _safe_url(url)
    resp = requests.get(url, headers=UA, timeout=timeout,
                        proxies={"http": None, "https": None})
    if resp.status_code != 200:
        raise CollectorError(f"页面访问失败（HTTP {resp.status_code}）")
    html = resp.text
    if len(html) > 2 * 1024 * 1024:
        raise CollectorError("页面过大")
    return html


def detect_platform(url: str) -> str:
    """识别平台：amazon/aliexpress/ebay/generic"""
    host = (urlparse(url).hostname or "").lower()
    if "amazon" in host:
        return "amazon"
    if "aliexpress" in host:
        return "aliexpress"
    if "ebay" in host:
        return "ebay"
    return "generic"


def _json_ld_products(html: str) -> list:
    """提取 JSON-LD 中的 Product 块"""
    products = []
    for m in re.finditer(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
                         html, re.S):
        raw = m.group(1).strip()
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue
        # 兼容单对象/数组/@graph
        items = data if isinstance(data, list) else [data]
        for d in items:
            if isinstance(d, dict) and d.get("@type") in ("Product", "IndividualProduct"):
                products.append(d)
    return products


def _extract_og(html: str) -> dict:
    """og: 标签兜底提取"""
    out = {}
    for prop in ("title", "image", "description"):
        m = re.search(rf'<meta[^>]*property="og:{prop}"[^>]*content="([^"]*)"', html)
        if not m:
            m = re.search(rf'<meta[^>]*content="([^"]*)"[^>]*property="og:{prop}"', html)
        if m:
            out[prop] = m.group(1)
    return out


def _normalize_product(raw: dict, url: str, platform: str, source: str) -> dict:
    """补全 Product schema"""
    return {
        "platform": platform,
        "source": source,
        "product_id": raw.get("product_id", ""),
        "url": url,
        "title": raw.get("title", ""),
        "brand": raw.get("brand", ""),
        "category": raw.get("category", ""),
        "price": raw.get("price", ""),
        "currency": raw.get("currency", ""),
        "specifications": raw.get("specifications", []),
        "description": raw.get("description", ""),
        "selling_points": raw.get("selling_points", []),
        "images": raw.get("images", []),
        "seller": raw.get("seller", ""),
        "collected_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }


def amazon_collect(url: str) -> dict:
    """亚马逊采集：/dp/{ASIN} 或 /gp/product/{ASIN}"""
    m = re.search(r"/(?:dp|gp/product)/([A-Z0-9]{10})", url)
    if not m:
        raise CollectorError("无法从链接提取亚马逊商品 ID（ASIN）")
    asin = m.group(1)
    html = _fetch_html(url)

    raw = {
        "product_id": asin, "url": url, "platform": "amazon", "source": "html",
        "title": "", "brand": "", "category": "", "price": "", "currency": "",
        "specifications": [], "description": "", "selling_points": [],
        "images": [], "seller": "",
    }
    # JSON-LD（亚马逊商品页含 Product 块）
    for p in _json_ld_products(html):
        raw["title"] = p.get("name", "")
        offers = p.get("offers") or {}
        price = offers.get("price", "") if isinstance(offers, dict) else ""
        raw["price"] = str(price)
        raw["currency"] = offers.get("priceCurrency", "") if isinstance(offers, dict) else ""
        brand = p.get("brand") or {}
        raw["brand"] = brand.get("name", "") if isinstance(brand, dict) else str(brand)
        raw["seller"] = (p.get("seller") or {}).get("name", "") if isinstance(p.get("seller"), dict) else ""
        imgs = p.get("image", "")
        raw["images"] = [imgs] if isinstance(imgs, str) and imgs else (imgs if isinstance(imgs, list) else [])
        break
    # HTML 兜底：标题/价格
    if not raw["title"]:
        t = re.search(r'<span id="productTitle"[^>]*>\s*([^<]{5,300}?)\s*</span>', html)
        if t:
            raw["title"] = t.group(1).strip()
    if not raw["price"]:
        p = re.search(r'<span class="a-offscreen">\s*([$€£¥][\d.,]+)', html)
        if p:
            raw["price"] = p.group(1).strip()
    # 规格表（tech spec）
    specs = []
    for name, val in re.findall(r'<th[^>]*class="a-color-secondary"[^>]*>\s*([^<]{2,60}?)\s*</th>\s*<td[^>]*>\s*([^<]{2,200}?)\s*</td>', html):
        specs.append({"name": name.strip(), "value": val.strip()})
    raw["specifications"] = specs[:15]

    if not raw["title"]:
        raise CollectorError("亚马逊页面未找到商品信息（可能被风控或改版）")
    return _normalize_product(raw, url, "amazon", "html")


def generic_collect(url: str, platform: str) -> dict:
    """通用采集：JSON-LD + og 标签"""
    html = _fetch_html(url)
    raw = {
        "url": url, "platform": platform, "source": "html",
        "title": "", "brand": "", "category": "", "price": "", "currency": "",
        "specifications": [], "description": "", "selling_points": [],
        "images": [], "seller": "", "product_id": "",
    }
    for p in _json_ld_products(html):
        raw["title"] = p.get("name", "")
        offers = p.get("offers") or {}
        if isinstance(offers, dict):
            raw["price"] = str(offers.get("price", ""))
            raw["currency"] = offers.get("priceCurrency", "")
        elif isinstance(offers, list) and offers:
            raw["price"] = str(offers[0].get("price", ""))
            raw["currency"] = offers[0].get("priceCurrency", "")
        brand = p.get("brand") or {}
        raw["brand"] = brand.get("name", "") if isinstance(brand, dict) else ""
        raw["seller"] = (p.get("seller") or {}).get("name", "") if isinstance(p.get("seller"), dict) else ""
        desc = p.get("description", "")
        raw["description"] = desc if isinstance(desc, str) else ""
        imgs = p.get("image", "")
        raw["images"] = [imgs] if isinstance(imgs, str) and imgs else (imgs if isinstance(imgs, list) else [])
        break
    # og 兜底
    og = _extract_og(html)
    if not raw["title"]:
        raw["title"] = og.get("title", "")
    if not raw["images"] and og.get("image"):
        raw["images"] = [og["image"]]
    if not raw["description"]:
        raw["description"] = og.get("description", "")

    if not raw["title"]:
        raise CollectorError("页面未找到商品结构化数据")
    return _normalize_product(raw, url, platform, "html")


PRODUCT_EXTRACT_SYSTEM = """你是电商商品信息提取器。从用户粘贴的商品页面文本中提取结构化信息。

输出 JSON（严格按以下结构）：
{
  "title": "商品标题",
  "brand": "品牌",
  "category": "品类",
  "price": "价格（保留原文，如 'US $29.99'）",
  "currency": "币种（USD/CNY/EUR 等）",
  "specifications": [{"name": "规格名", "value": "规格值"}],
  "description": "商品描述（2-3 句概括）",
  "selling_points": ["卖点1", "卖点2", "卖点3"],
  "images": [],
  "seller": "卖家/店铺名",
  "product_id": "商品 ID（文本中出现才填）"
}

要求：
- 只提取文本中明确出现的信息，缺失字段填空字符串/空列表，禁止编造
- price 保留原文格式，不换算
- specifications 最多 15 条
- 全部字段用中文或原文（不翻译）"""


def ai_extract(text: str, url: str = "", platform: str = "generic") -> dict:
    """AI 提取：粘贴文本/页面文本 → Product schema（兜底）"""
    text = text.strip()
    if not text:
        raise CollectorError("没有可提取的文本内容")
    content = _chat([
        {"role": "system", "content": PRODUCT_EXTRACT_SYSTEM},
        {"role": "user", "content": text[:6000]},
    ], use_json=True)
    data = _parse_json(content)
    raw = {
        "product_id": str(data.get("product_id", "")),
        "url": url,
        "platform": platform,
        "source": "ai_extract",
        "title": str(data.get("title", "")),
        "brand": str(data.get("brand", "")),
        "category": str(data.get("category", "")),
        "price": str(data.get("price", "")),
        "currency": str(data.get("currency", "")),
        "specifications": data.get("specifications", []) or [],
        "description": str(data.get("description", "")),
        "selling_points": data.get("selling_points", []) or [],
        "images": data.get("images", []) or [],
        "seller": str(data.get("seller", "")),
    }
    if not raw["title"]:
        raise CollectorError("AI 提取失败：文本中未找到商品信息")
    return _normalize_product(raw, url, platform, "ai_extract")


def collect_product(url: str = "", pasted_text: str = "") -> dict:
    """统一入口：URL + 可选粘贴文本 → Product schema

    优先级：粘贴文本 → AI 提取；亚马逊 → 程序抓取；其他 → JSON-LD；
    全部失败 → AI 提取页面文本。
    """
    url = (url or "").strip()
    pasted_text = (pasted_text or "").strip()
    if not url and not pasted_text:
        raise CollectorError("请粘贴商品链接或商品页面内容")

    # 1. 粘贴兜底：用户直接粘贴页面内容
    if pasted_text:
        try:
            return ai_extract(pasted_text, url, detect_platform(url) if url else "generic")
        except CollectorError:
            raise

    # 2. 平台采集
    platform = detect_platform(url)
    errors = []
    try:
        if platform == "amazon":
            return amazon_collect(url)
        # 其他平台也试通用 JSON-LD（有些页面有结构化数据）
        return generic_collect(url, platform)
    except CollectorError as e:
        errors.append(str(e))

    # 3. 兜底：抓页面文本 → AI 提取
    try:
        html = _fetch_html(url)
        # 去脚本/样式后的纯文本
        text = re.sub(r"<script.*?</script>|<style.*?</style>", " ", html, flags=re.S)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return ai_extract(text[:6000], url, platform)
    except CollectorError as e:
        errors.append(str(e))
        raise CollectorError("；".join(errors) + "（可尝试粘贴商品页面内容后重试）")
