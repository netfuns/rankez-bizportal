# syntax=docker/dockerfile:1
# BizPortal — Alpine 最小镜像
FROM python:3.13-alpine AS build

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY requirements.txt .
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install -r requirements.txt

FROM python:3.13-alpine

LABEL org.opencontainers.image.source="https://github.com/netfuns/rankez-bizportal"
LABEL org.opencontainers.image.title="BizPortal"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    BIZPORTAL_DATA_DIR=/data \
    WEB_CONCURRENCY=4

COPY --from=build /opt/venv /opt/venv
WORKDIR /app
COPY app ./app

RUN mkdir -p /data \
    && addgroup -S bp && adduser -S bp -G bp \
    && chown -R bp:bp /data /app

USER bp
VOLUME ["/data"]
EXPOSE 8888

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys;sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8888/healthz',timeout=3).status==200 else 1)"

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port 8888 --workers ${WEB_CONCURRENCY:-4} --proxy-headers --forwarded-allow-ips='*'"]
