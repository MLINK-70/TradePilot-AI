"""config.py — 读取 .env 配置，全项目唯一配置入口"""
import os

from dotenv import load_dotenv

load_dotenv()  # 从项目根目录的 .env 文件加载密钥

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"
DEEPSEEK_MODEL = "deepseek-chat"
