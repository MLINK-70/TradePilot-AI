"""config.py — 读取 .env 配置，全项目唯一配置入口

支持运行时更新（set_key）：设置面板保存后立即生效，无需重启。
"""
import ipaddress
import logging
import os
import secrets
import socket
import urllib.parse

from dotenv import load_dotenv

load_dotenv()  # 从项目根目录的 .env 文件加载密钥

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"
DEEPSEEK_MODEL = "deepseek-chat"

# 可选 AI 提供商（多模型支持）
# GPT:       base=https://api.openai.com/v1  model=gpt-4o-mini
# Claude:    base=https://api.anthropic.com/v1  model=claude-sonnet-4-5
# 自定义:    任何 OpenAI 兼容接口（如通义/GLM/本地 Ollama）
AI_PROVIDER = os.getenv("AI_PROVIDER", "deepseek")  # deepseek / gpt / claude / custom
AI_BASE_URL = os.getenv("AI_BASE_URL", DEEPSEEK_BASE_URL)
AI_MODEL = os.getenv("AI_MODEL", DEEPSEEK_MODEL)
AI_API_KEY = os.getenv("AI_API_KEY", DEEPSEEK_API_KEY)

# eBay 开发者凭证（可选，未配置时 eBay 功能提示配置）
# 获取：https://developer.ebay.com 注册后创建应用，审核通过后拿 App ID + Client Secret
EBAY_APP_ID = os.getenv("EBAY_APP_ID", "")
EBAY_CLIENT_SECRET = os.getenv("EBAY_CLIENT_SECRET", "")

# 速卖通联盟开放平台凭证（可选，未配置时速卖通功能提示配置）
# 获取：https://pub.aliexpress.com 注册联盟账号 → 开放平台创建应用 → App Key + App Secret
# 与 eBay 不同：国内直连可用，无需梯子
ALIEXPRESS_APP_KEY = os.getenv("ALIEXPRESS_APP_KEY", "")
ALIEXPRESS_APP_SECRET = os.getenv("ALIEXPRESS_APP_SECRET", "")

# Tavily 搜索 API（行业动态数据源）
# 获取：https://app.tavily.com 注册后拿 API Key
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")

# UN Comtrade 正式 API（贸易数据，比 preview 数据质量高、无 500 条硬截断）
# 获取：https://comtradeplus.un.org 注册后生成 subscription key
UN_COMTRADE_KEY = os.getenv("UN_COMTRADE_KEY", "")
# 数据源模式：preview（免费，无需 key，有 500 条限制、数据质量较低）/ formal（需 key，推荐）
UN_COMTRADE_MODE = os.getenv("UN_COMTRADE_MODE", "preview")

# 搜索提供商（多引擎支持）
# tavily（推荐·默认）/ serper（Google）/ custom（任意兼容接口）
SEARCH_PROVIDER = os.getenv("SEARCH_PROVIDER", "tavily")
SEARCH_API_KEY = os.getenv("SEARCH_API_KEY", TAVILY_API_KEY)
SEARCH_BASE_URL = os.getenv("SEARCH_BASE_URL", "https://api.tavily.com")

# 运行时可更新的 Key（设置面板写入）
RUNTIME_KEYS = {
    "DEEPSEEK_API_KEY": DEEPSEEK_API_KEY,
    "TAVILY_API_KEY": TAVILY_API_KEY,
    "EBAY_APP_ID": EBAY_APP_ID,
    "EBAY_CLIENT_SECRET": EBAY_CLIENT_SECRET,
    "ALIEXPRESS_APP_KEY": ALIEXPRESS_APP_KEY,
    "ALIEXPRESS_APP_SECRET": ALIEXPRESS_APP_SECRET,
    "AI_PROVIDER": AI_PROVIDER,
    "AI_BASE_URL": AI_BASE_URL,
    "AI_MODEL": AI_MODEL,
    "AI_API_KEY": AI_API_KEY,
    "SEARCH_PROVIDER": SEARCH_PROVIDER,
    "SEARCH_API_KEY": SEARCH_API_KEY,
    "SEARCH_BASE_URL": SEARCH_BASE_URL,
    "UN_COMTRADE_MODE": UN_COMTRADE_MODE,
    "UN_COMTRADE_KEY": UN_COMTRADE_KEY,
}

# 本地模型（Ollama 等 http://localhost）需显式放行才允许非 https/内网地址
ALLOW_LOCAL_AI_BASE_URL = os.getenv("ALLOW_LOCAL_AI_BASE_URL", "") == "1"

# 管理员密码（保护 POST /api/settings 等写 Key 的接口）
# 以 .env 显式配置为准；未配置时生成随机密码并打日志（仅本次运行有效，重启即变）
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "").strip()
if not ADMIN_PASSWORD:
    ADMIN_PASSWORD = secrets.token_urlsafe(12)
    logging.warning(
        "未配置 ADMIN_PASSWORD，已生成临时管理员密码：%s（仅本次运行有效；建议写入 .env 固定）",
        ADMIN_PASSWORD,
    )
ADMIN_SESSION_TTL_DAYS = 7


def validate_ai_base_url(url: str) -> str:
    """校验 AI 服务地址（SSRF/密钥泄露防线第一层）

    规则：必须 http(s)://；IP 字面量或域名解析结果命中内网/环回/链路本地/保留地址一律拒绝；
    公网地址必须 https（防止明文把 API Key 发给中间人）。
    本地模型（http://localhost:11434 等）需 .env 设 ALLOW_LOCAL_AI_BASE_URL=1 显式放行。
    返回去除尾部斜杠的规范化地址；不合法抛 ValueError。
    """
    url = (url or "").strip().rstrip("/")
    if not url:
        raise ValueError("AI 服务地址不能为空")
    u = urllib.parse.urlsplit(url)
    if u.scheme not in ("https", "http"):
        raise ValueError("AI 服务地址必须以 http(s):// 开头")
    host = (u.hostname or "").lower()
    if not host:
        raise ValueError("AI 服务地址缺少主机名")

    def _is_local(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
        # IPv6：只拒绝环回(::1)和链路本地(fe80::)。其余（含 2001::/32 Teredo 隧道段、
        # 2001:db8:: 文档段）都是公网，is_private 会把它们误判为内网导致误拒。
        if ip.version == 6:
            return ip.is_loopback or ip.is_link_local
        return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved

    if not ALLOW_LOCAL_AI_BASE_URL and u.scheme != "https":
        raise ValueError("AI 服务地址必须使用 https（本地模型需在 .env 设 ALLOW_LOCAL_AI_BASE_URL=1）")

    try:
        ip = ipaddress.ip_address(host)  # 纯 IP 字面量
    except ValueError:
        ip = None  # 域名，走下方解析校验
    if ip is not None:
        if _is_local(ip) and not ALLOW_LOCAL_AI_BASE_URL:
            raise ValueError("AI 服务地址不允许指向内网/本机地址")
    else:
        # 域名：解析后逐 IP 校验（防 DNS rebinding 绕过字面量检查）
        try:
            infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
        except socket.gaierror:
            raise ValueError("AI 服务地址域名无法解析")
        for info in infos:
            ip = ipaddress.ip_address(info[4][0])
            if _is_local(ip) and not ALLOW_LOCAL_AI_BASE_URL:
                raise ValueError("AI 服务地址解析到内网地址（已拒绝）")
    return url


def set_key(name: str, value: str) -> bool:
    """运行时更新 Key：写入 RUNTIME_KEYS + 追加到 .env（持久化）

    联动别名：设置面板填 DeepSeek/Tavily key 时，同步到 AI_API_KEY/SEARCH_API_KEY
    （llm 读 AI_API_KEY、market_data 读 SEARCH_API_KEY；默认 provider=deepseek/tavily）。
    安全校验：值含换行/# 直接拒绝（防 .env 配置注入）；AI_BASE_URL 过白名单校验（防 SSRF/密钥泄露）。
    返回 True 表示已生效；写 .env 失败时返回 False（运行时仍生效）。
    """
    if name not in RUNTIME_KEYS:
        return False
    value = (value or "").strip()
    # 配置注入防线：换行可拼出新的配置行（如 \nAI_BASE_URL=https://attacker），# 会截断行
    if any(ch in value for ch in ("\n", "\r", "#")):
        raise ValueError(f"配置项 {name} 的值不能包含换行或 # 字符")
    if name == "AI_BASE_URL":
        value = validate_ai_base_url(value)
    RUNTIME_KEYS[name] = value
    globals()[name] = value  # 让引用处（llm/market_data/ebay）立即读到新值

    # 联动别名：
    # - 设置面板"API Key"输入框 → AI_API_KEY（无论 provider，_chat 统一读 AI_API_KEY）
    # - DeepSeek/Tavily 默认 provider 时，填 DEEPSEEK/TAVILY 同步到 AI_API_KEY/SEARCH_API_KEY
    #   （兼容老面板只填 DeepSeek Key 的用法；provider 已切换时 AI_API_KEY 优先于回退链）
    if name == "DEEPSEEK_API_KEY" and RUNTIME_KEYS.get("AI_PROVIDER", "deepseek") == "deepseek":
        RUNTIME_KEYS["AI_API_KEY"] = value
        globals()["AI_API_KEY"] = value
    elif name == "AI_API_KEY":
        RUNTIME_KEYS["AI_API_KEY"] = value
        globals()["AI_API_KEY"] = value
    # 联动别名：Tavily key -> SEARCH_API_KEY（search_provider=tavily 时跟随）
    elif name == "TAVILY_API_KEY" and RUNTIME_KEYS.get("SEARCH_PROVIDER", "tavily") == "tavily":
        RUNTIME_KEYS["SEARCH_API_KEY"] = value
        globals()["SEARCH_API_KEY"] = value

    # 持久化到 .env（更新或追加）
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    try:
        lines = []
        if os.path.exists(env_path):
            with open(env_path, encoding="utf-8") as f:
                lines = f.readlines()
        found = False
        for i, line in enumerate(lines):
            if line.strip().startswith(f"{name}="):
                lines[i] = f"{name}={value}\n"
                found = True
                break
        if not found:
            lines.append(f"{name}={value}\n")
        with open(env_path, "w", encoding="utf-8") as f:
            f.writelines(lines)
    except OSError:
        # 写 .env 失败（如 exe 目录无写权限）：运行时已生效，但持久化失败要让人知道
        logging.exception("写入 .env 失败（%s），设置仅本次运行生效", name)
        return False
    return True


def get_keys_status() -> dict:
    """返回各 Key 配置状态（是否已配置，不返回值——安全）"""
    return {
        "DEEPSEEK_API_KEY": bool(RUNTIME_KEYS.get("DEEPSEEK_API_KEY")),
        "TAVILY_API_KEY": bool(RUNTIME_KEYS.get("TAVILY_API_KEY")),
        "EBAY_APP_ID": bool(RUNTIME_KEYS.get("EBAY_APP_ID")),
        "EBAY_CLIENT_SECRET": bool(RUNTIME_KEYS.get("EBAY_CLIENT_SECRET")),
        "ALIEXPRESS_APP_KEY": bool(RUNTIME_KEYS.get("ALIEXPRESS_APP_KEY")),
        "ALIEXPRESS_APP_SECRET": bool(RUNTIME_KEYS.get("ALIEXPRESS_APP_SECRET")),
        "AI_PROVIDER": RUNTIME_KEYS.get("AI_PROVIDER", "deepseek"),
        "AI_MODEL": RUNTIME_KEYS.get("AI_MODEL", ""),
        "AI_API_KEY": bool(RUNTIME_KEYS.get("AI_API_KEY")),
        "AI_BASE_URL": bool(RUNTIME_KEYS.get("AI_BASE_URL")),
        "SEARCH_PROVIDER": RUNTIME_KEYS.get("SEARCH_PROVIDER", "tavily"),
        "SEARCH_API_KEY": bool(RUNTIME_KEYS.get("SEARCH_API_KEY")),
        "SEARCH_BASE_URL": bool(RUNTIME_KEYS.get("SEARCH_BASE_URL")),
        "UN_COMTRADE_MODE": RUNTIME_KEYS.get("UN_COMTRADE_MODE", "preview"),
        "UN_COMTRADE_KEY": bool(RUNTIME_KEYS.get("UN_COMTRADE_KEY")),
    }
