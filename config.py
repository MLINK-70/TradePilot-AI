"""config.py — 读取 .env 配置，全项目唯一配置入口"""
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
