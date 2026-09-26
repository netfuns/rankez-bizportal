#!/bin/sh
# BizPortal Docker 部署脚本（目标机执行）
set -e
SRC=/tmp/bizportalsrc/src.tgz
APP=/opt/bizportal-src
DATA=/var/lib/bizportal

echo "== unpack source =="
mkdir -p "$APP"
tar xzf "$SRC" -C "$APP" --strip-components=1
cd "$APP"

echo "== build image =="
docker build -t bizportal:latest .

echo "== stop old systemd service =="
systemctl disable --now bizportal.service 2>/dev/null || true

echo "== prepare data dir (schema changed, reset DB; only admin existed) =="
mkdir -p "$DATA"
rm -f "$DATA"/bizportal.db "$DATA"/bizportal.db-wal "$DATA"/bizportal.db-shm
# 容器内 alpine 非 root 用户 uid/gid=100/101
chown -R 100:101 "$DATA"

echo "== remove old container if any =="
docker rm -f bizportal 2>/dev/null || true

echo "== run container =="
docker run -d \
  --name bizportal \
  --restart always \
  -p 8888:8888 \
  -v "$DATA":/data \
  -e BIZPORTAL_SECRET_KEY="${BIZPORTAL_SECRET_KEY:-bizportal-10-2026-stable-key}" \
  bizportal:latest

echo "== wait for healthz =="
for i in $(seq 1 30); do
  code=$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8888/healthz || true)
  [ "$code" = "200" ] && break
  sleep 2
done
echo "healthz=$code"
docker ps --filter name=bizportal --format '{{.Names}} {{.Status}}'
