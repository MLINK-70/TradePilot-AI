# TradePilot AI 服务器部署镜像（v1.0）
# 包含 LibreOffice headless：Linux 下 PDF 导出的跨平台后备（Word COM 仅 Windows）
FROM python:3.12-slim

# LibreOffice（PDF 转换后备）+ 中文字体（matplotlib 图表）
RUN apt-get update && apt-get install -y --no-install-recommends \
    libreoffice-writer \
    fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
# 容器内不开 WAL 之外的额外权限；数据库写 /app（挂载卷持久化）
ENV ALLOWED_HOSTS=127.0.0.1,localhost \
    PYTHONUNBUFFERED=1

EXPOSE 8000
CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
