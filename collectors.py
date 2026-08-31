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
import socket
import ssl
import time
from urllib.parse import urljoin, urlparse

import requests
from urllib3.connection import HTTPSConnection
from urllib3.connectionpool import HTTPSConnectionPool
from urllib3.poolmanager import PoolManager
from requests.adapters import HTTPAdapter

from llm import _chat, _parse_json

UA = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
}


class CollectorError(Exception):
    """采集失败（网络/平台不支持/页面结构变化）"""


def _is_forbidden_ip(ip) -> bool:
    """内网/保留地址判定（回归修复：补 is_multicast/is_unspecified/CGNAT；
    0.0.0.0 在旧版 Python is_private=False 属版本相关，一并显式拦截）"""
    # IPv4-mapped IPv6（::ffff:192.168.1.1 等）先解包按 IPv4 规则判：
    # IPv6 分支只查 loopback/link-local/unspecified/multicast，
    # 会把内网 IPv4 的 mapped 形式当公网放行（SSRF 校验绕过）。
    if ip.version == 6:
        mapped = ip.ipv4_mapped
        if mapped is not None:
            ip = mapped
        else:
            # IPv6：拒绝环回/链路本地/未指定；其余（含 2001::/32 等）是公网
            return ip.is_loopback or ip.is_link_local or ip.is_unspecified or ip.is_multicast
    return (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
            or ip.is_multicast or ip.is_unspecified or ip == ipaddress.ip_address("0.0.0.0")
            or ip in ipaddress.ip_network("100.64.0.0/10"))  # CGNAT 共享地址段


def _safe_url(url: str):
    """SSRF 防护：仅 https、拒绝内网/保留地址，返回 (url, pinned_ip)

    域名解析后逐 IP 校验（DNS rebinding 防护见 _fetch_html：解析出的 IP
    钉扎到连接，不再二次解析）；纯 IP 字面量直接校验。
    返回 pinned_ip 供调用方固定建连；None 表示无需钉扎（本轮无域名）。
    """
    u = urlparse(url)
    if u.scheme != "https":
        raise CollectorError("仅支持 https 链接")
    try:
        host = (u.hostname or "").lower()
    except ValueError:
        raise CollectorError("链接格式非法")
    if not host:
        raise CollectorError("链接缺少主机名")
    try:
        ip = ipaddress.ip_address(host)  # 纯 IP 字面量
    except ValueError:
        ip = None  # 域名，走解析校验
    if ip is not None:
        if _is_forbidden_ip(ip):
            raise CollectorError("不支持内网地址")
        return url, None  # IP 字面量无需钉扎（连接用的就是它）
    else:
        try:
            infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
        except (socket.gaierror, UnicodeError, OSError, ValueError):
            raise CollectorError("链接域名无法解析")
        public = []
        for info in infos:
            ip = ipaddress.ip_address(info[4][0])
            if _is_forbidden_ip(ip):
                raise CollectorError("链接解析到内网地址（已拒绝）")
            public.append(ip)
        if not public:
            raise CollectorError("链接域名解析结果为空")
        # 钉扎第一个公网解析结果（多 IP 场景取首选；DNS rebinding 窗口
        # 由此关闭——连接不再重新解析域名）
        return url, str(public[0])


class PinIpAdapter(HTTPAdapter):
    """把 HTTPS 连接钉扎到固定 IP 的 requests 适配器（DNS rebinding 防护）

    构造时传入解析校验过的 IP；连接时直接连该 IP，同时保留原始 Host
    头与 server_hostname（TLS SNI + 证书校验仍按域名进行，防中间人）。
    """

    def __init__(self, ip: str, *args, **kwargs):
        self._pin_ip = ip
        super().__init__(*args, **kwargs)

    def init_poolmanager(self, *args, **kwargs):
        kwargs["ssl_context"] = ssl.create_default_context()
        pin_ip = self._pin_ip

        class PinnedHTTPSConnection(HTTPSConnection):
            _pin_ip = pin_ip  # 类属性：闭包内 self 是新连接实例，不能引用外层 self

            def _new_conn(self):
                # 直接连钉扎 IP（不走 getaddrinfo/系统解析）
                sock = socket.create_connection((self._pin_ip, self.port), self.timeout)
                return sock

        class PinnedHTTPSConnectionPool(HTTPSConnectionPool):
            ConnectionCls = PinnedHTTPSConnection

        class PinnedPoolManager(PoolManager):
            def _new_pool(self, scheme, host, port, request_context=None):
                if scheme == "https":
                    return PinnedHTTPSConnectionPool(
                        host, port, **self.connection_pool_kw,
                    )
                return super()._new_pool(scheme, host, port, request_context)

        self.poolmanager = PinnedPoolManager(**kwargs)


def _request_pinned(url: str, pinned_ip, timeout: int = 15) -> requests.Response:
    """按钉扎 IP 发起 GET（无 IP 时普通请求）；关闭代理；不跟随重定向"""
    if pinned_ip:
        s = requests.Session()
        s.mount("https://", PinIpAdapter(pinned_ip))
        try:
            return s.get(url, headers=UA, timeout=timeout, allow_redirects=False,
                         stream=True, proxies={"http": None, "https": None})
        finally:
            s.close()
    return requests.get(url, headers=UA, timeout=timeout, allow_redirects=False,
                        stream=True, proxies={"http": None, "https": None})


def _fetch_html(url: str, timeout: int = 15) -> str:
    """抓取页面 HTML（强制直连，不重试，大小上限 2MB）

    手动跟随重定向（最多 5 跳）：每一跳都重新过 _safe_url 校验，
    防止 https 页面 302 跳到内网/非 https 地址绕过 SSRF 防线。
    DNS rebinding 防护（修复 TOCTOU 窗口）：_safe_url 返回钉扎 IP，
    连接经 PinIpAdapter 直接连到该 IP（带 Host 头 + SNI 校验），
    不再让 requests 二次解析域名——校验与建连使用同一解析结果。
    回归修复：requests 异常（连接失败/超时/DNS）统一包装为 CollectorError，
    让 collect_product 的 AI 兜底链真正生效（此前 requests 异常原样冒泡）。
    """
    current = url
    for _ in range(5):
        current, pinned_ip = _safe_url(current)
        try:
            resp = _request_pinned(current, pinned_ip, timeout=timeout)
        except requests.exceptions.RequestException as e:
            raise CollectorError(f"页面访问失败：{e}")
        if resp.status_code in (301, 302, 303, 307, 308):
            nxt = resp.headers.get("Location")
            if not nxt:
                raise CollectorError("重定向缺少目标地址")
            current = urljoin(current, nxt)  # 相对重定向也要基于当前 URL 解析
            continue
        if resp.status_code != 200:
            raise CollectorError(f"页面访问失败（HTTP {resp.status_code}）")
        # 回归修复：流式读取限量（原先把整页解码进内存后才检查 2MB）
        try:
            content_length = int(resp.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            content_length = 0
        if content_length > 2 * 1024 * 1024:
            resp.close()
            raise CollectorError("页面过大")
        chunks = []
        total = 0
        try:
            for chunk in resp.iter_content(chunk_size=65536):
                total += len(chunk)
                if total > 2 * 1024 * 1024:
                    resp.close()
                    raise CollectorError("页面过大")
                chunks.append(chunk)
        except requests.exceptions.RequestException as e:
            raise CollectorError(f"页面读取失败：{e}")
        finally:
            resp.close()
        raw = b"".join(chunks)
        # 编码兜底：显式 charset 头按头解码；无 charset 头（GBK 等中文页常见）
        # 用内容探测修正，避免乱码污染 AI 兜底文本（回归修复）
        html = None
        if "charset=" in resp.headers.get("Content-Type", "").lower():
            try:
                html = raw.decode(resp.encoding or "utf-8", errors="replace")
            except LookupError:
                html = None
        if html is None:
            try:
                from charset_normalizer import from_bytes
                best = from_bytes(raw).best()
                html = raw.decode(best.encoding if best else "utf-8", errors="replace")
            except Exception:
                html = raw.decode("utf-8", errors="replace")
        return html
    raise CollectorError("重定向次数过多")


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
    """提取 JSON-LD 中的 Product 块（回归修复：支持 @type 列表与 @graph 嵌套）"""
    products = []
    for m in re.finditer(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>',
                         html, re.S | re.I):
        raw = m.group(1).strip()
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue
        # 兼容单对象/数组/@graph
        items = data if isinstance(data, list) else [data]
        if isinstance(data, dict) and isinstance(data.get("@graph"), list):
            items = data["@graph"]
        for d in items:
            if not isinstance(d, dict):
                continue
            types = d.get("@type") or []
            if not isinstance(types, list):
                types = [types]
            if any(t in ("Product", "IndividualProduct") for t in types):
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
    """亚马逊采集：/dp/{ASIN} 或 /gp/product/{ASIN}（回归修复：小写 asin 也识别）"""
    m = re.search(r"/(?:dp|gp/product)/([A-Za-z0-9]{10})", url)
    if not m:
        raise CollectorError("无法从链接提取亚马逊商品 ID（ASIN）")
    asin = m.group(1).upper()
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
        except ValueError as e:
            # 回归修复：AI 不可用（无 Key/限流）统一包装，前端拿到一致错误
            raise CollectorError(f"AI 提取不可用：{e}")

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
    except (CollectorError, ValueError) as e:
        # 回归修复：requests/ValueError 统一进 errors，AI 兜底失败给一致错误文案
        errors.append(str(e))
        raise CollectorError("；".join(errors) + "（可尝试粘贴商品页面内容后重试）")
