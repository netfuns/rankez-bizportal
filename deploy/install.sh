#!/usr/bin/env bash
# BizPortal 独立部署脚本（目标机以 root 运行）
# 用法: bash /tmp/bizportal-install.sh [tarball路径]
set -euo pipefail

APP_DIR=/opt/bizportal
DATA_DIR=/var/lib/bizportal
ENV_DIR=/etc/bizportal
PORT=8888
TARBALL="${1:-/tmp/bizportal.tgz}"

echo "[1/8] 创建系统账号与目录"
if ! id bizportal >/dev/null 2>&1; then
  useradd --system --home-dir "$DATA_DIR" --shell /usr/sbin/nologin bizportal
fi
mkdir -p "$APP_DIR" "$DATA_DIR/uploads" "$ENV_DIR"

echo "[2/8] 解压代码到 $APP_DIR"
rm -rf "$APP_DIR/app" "$APP_DIR/deploy" "$APP_DIR/requirements.txt"
tar xzf "$TARBALL" -C "$APP_DIR"

echo "[3/8] 准备虚拟环境"
if [ ! -x "$APP_DIR/.venv/bin/python" ]; then
  python3 -m venv "$APP_DIR/.venv"
fi
"$APP_DIR/.venv/bin/pip" install -q --upgrade pip
"$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"

echo "[4/8] 写入环境配置"
if [ ! -f "$ENV_DIR/bizportal.env" ]; then
  SECRET="$("$APP_DIR/.venv/bin/python" -c 'import secrets;print(secrets.token_urlsafe(48))')"
  cat > "$ENV_DIR/bizportal.env" <<EOF2
BIZPORTAL_SECRET_KEY=$SECRET
BIZPORTAL_DATA_DIR=$DATA_DIR
BIZPORTAL_ADMIN_EMAIL=admin
BIZPORTAL_ADMIN_PASSWORD=Admin@123
EOF2
fi
chown root:bizportal "$ENV_DIR/bizportal.env"
chmod 640 "$ENV_DIR/bizportal.env"

echo "[5/8] 安装 systemd 单元"
cp "$APP_DIR/deploy/bizportal.service" /etc/systemd/system/bizportal.service
sed -i "s/--port 8888/--port $PORT/" /etc/systemd/system/bizportal.service

echo "[6/8] 目录属主"
chown -R bizportal:bizportal "$APP_DIR" "$DATA_DIR"
chmod 750 "$DATA_DIR"

echo "[7/8] 清理旧字节码并重启服务"
find "$APP_DIR/app" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
systemctl daemon-reload
systemctl enable bizportal
systemctl restart bizportal
sleep 5

echo "[8/8] 健康检查"
systemctl is-active bizportal
curl -s -o /dev/null -w 'healthz=%{http_code}\n' "http://127.0.0.1:$PORT/healthz"
journalctl -u bizportal --since '-30s' --no-pager | tail -8
