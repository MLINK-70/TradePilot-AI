"""config.py — 读取 .env 配置，全项目唯一配置入口

支持运行时更新（set_key）：设置面板保存后立即生效，无需重启。
"""
import os

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
}


def set_key(name: str, value: str) -> bool:
    """运行时更新 Key：写入 RUNTIME_KEYS + 追加到 .env（持久化）"""
    if name not in RUNTIME_KEYS:
        return False
    value = (value or "").strip()
    RUNTIME_KEYS[name] = value
    globals()[name] = value  # 让引用处（llm/market_data/ebay）立即读到新值

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
        pass  # 写 .env 失败不影响运行时生效
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
        "SEARCH_PROVIDER": RUNTIME_KEYS.get("SEARCH_PROVIDER", "tavily"),
    }
