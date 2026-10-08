FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app

WORKDIR /app

# 安裝系統必備工具
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 複製依賴宣告並安裝
COPY requirements.txt pyproject.toml /app/
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir -e .

# 複製專案模組與指令設定檔
COPY src /app/src
COPY config /app/config
COPY README.md /app/README.md

# 曝露埠號 (符合 Cloud Run Port 8080 規範)
EXPOSE 8080

# 啟動 FastAPI 服務
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8080"]
