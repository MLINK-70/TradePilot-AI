# TradePilot AI 服务器部署镜像（v1.0.2）
# 包含 LibreOffice headless：Linux 下 PDF 导出的跨平台后备（Word COM 仅 Windows）
FROM python:3.12-slim

# 防 tzdata 等交互式安装卡构建（回归修复）
ENV DEBIAN_FRONTEND=noninteractive

# LibreOffice（PDF 转换后备）+ 中文字体（matplotlib 图表）
RUN apt-get update && apt-get install -y --no-install-recommends \
    libreoffice-writer \
    fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# .dockerignore 已排除 .env/venv/build/dist/本地 db（回归修复：原 COPY . .
# 会把真实密钥与 557MB venv 烤进镜像）
COPY . .
# 预生成空数据库：named volume 挂单文件路径时 Docker 会把挂载点已有文件复制进卷
# （copy-up）；镜像内无此文件时卷会变成目录，sqlite 打不开（回归修复）
RUN python -c "import database; database.init_db(); database.enable_wal()"

# 非 root 运行（回归修复：原 root 运行，容器逃逸面大）
RUN useradd --create-home tradepilot && chown -R tradepilot:tradepilot /app
USER tradepilot

# ALLOWED_HOSTS 由 docker-compose 注入（默认 * 见 compose 注释）
ENV PYTHONUNBUFFERED=1

EXPOSE 8000
CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
