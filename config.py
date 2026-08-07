"""config.py — 读取 .env 配置，全项目唯一配置入口

支持运行时更新（set_key）：设置面板保存后立即生效，无需重启。
"""
import os

from dotenv import load_dotenv

load_dotenv()  # 从项目根目录的 .env 文件加载密钥

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"
DEEPSEEK_MODEL = "deepseek-chat"

# eBay 开发者凭证（可选，未配置时 eBay 功能提示配置）
# 获取：https://developer.ebay.com 注册后创建应用，审核通过后拿 App ID + Client Secret
EBAY_APP_ID = os.getenv("EBAY_APP_ID", "")
EBAY_CLIENT_SECRET = os.getenv("EBAY_CLIENT_SECRET", "")

# Tavily 搜索 API（行业动态数据源）
# 获取：https://app.tavily.com 注册后拿 API Key
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")

# 运行时可更新的 Key（设置面板写入）
RUNTIME_KEYS = {
    "DEEPSEEK_API_KEY": DEEPSEEK_API_KEY,
    "TAVILY_API_KEY": TAVILY_API_KEY,
    "EBAY_APP_ID": EBAY_APP_ID,
    "EBAY_CLIENT_SECRET": EBAY_CLIENT_SECRET,
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
    }
