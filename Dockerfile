# ===== 构建阶段 =====
FROM python:3.11-slim AS builder

WORKDIR /build

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update && apt-get install -y --no-install-recommends gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ===== 运行阶段 =====
FROM python:3.11-slim

WORKDIR /opt/ai-image-service

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/opt/ai-image-service/src

COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

COPY . .

# 非 root 用户
RUN groupadd -r appuser && useradd -r -g appuser -d /opt/ai-image-service -s /sbin/nologin appuser \
    && chown -R appuser:appuser /opt/ai-image-service

USER appuser

EXPOSE 8030

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8030"]
