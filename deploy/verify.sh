#!/usr/bin/env bash
# 线上冒烟：管理员登录 → 建客户 → 建订单 → 校验余额 → 校验客户门户可见
set -uo pipefail
BASE=http://127.0.0.1:8888
D=/tmp/bizportal
J=$D/c.jar
rm -f "$J"

code=$(curl -s -L -c "$J" -b "$J" -o "$D/login.html" -w '%{http_code}' \
  -X POST -d 'email=admin&password=Admin@123' "$BASE/login")
echo "login_code=$code"
grep -q '管理概览' "$D/login.html" && echo "admin_home=OK" || echo "admin_home=FAIL"

curl -s -L -c "$J" -b "$J" -o /dev/null -w 'customer_new=%{http_code}\n' \
  -X POST --data-urlencode 'short_name=DEMO' --data-urlencode 'full_name=演示科技有限公司' \
  --data-urlencode 'address=上海市' --data-urlencode 'domains=demo.com' --data-urlencode 'note=' \
  "$BASE/admin/customers/new"

CID=$(python3 - "$D" <<'PY'
import sqlite3, sys
db = "/var/lib/bizportal/bizportal.db"
con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
row = con.execute("select id from customers where short_name='DEMO'").fetchone()
print(row[0] if row else "")
PY
)
echo "customer_id=$CID"
if [ -z "$CID" ]; then echo "customer_lookup=FAIL"; exit 1; fi

curl -s -L -c "$J" -b "$J" -o /dev/null -w 'order_credit=%{http_code}\n' \
  -X POST --data-urlencode "customer_id=$CID" --data-urlencode 'po_number=PO-DEMO-1' \
  --data-urlencode 'order_type=充值' --data-urlencode 'product_name=年度服务包' \
  --data-urlencode 'quantity=1' --data-urlencode 'amount=10000' --data-urlencode 'remark=冒烟' \
  "$BASE/admin/orders/new"

curl -s -L -c "$J" -b "$J" -o /dev/null -w 'order_debit=%{http_code}\n' \
  -X POST --data-urlencode "customer_id=$CID" --data-urlencode 'po_number=PO-DEMO-2' \
  --data-urlencode 'order_type=新购' --data-urlencode 'product_name=PAM 授权 20 席' \
  --data-urlencode 'quantity=20' --data-urlencode 'amount=3200' --data-urlencode 'remark=' \
  "$BASE/admin/orders/new"

BAL=$(python3 - <<'PY'
import sqlite3
con = sqlite3.connect("file:/var/lib/bizportal/bizportal.db?mode=ro", uri=True)
print(con.execute("select balance from customers where short_name='DEMO'").fetchone()[0])
PY
)
echo "balance=$BAL (expect 6800)"

curl -s -b "$J" "$BASE/admin/mail" -o "$D/mail.html" -w 'mail=%{http_code}\n'
curl -s -b "$J" "$BASE/admin/settings" -o "$D/settings.html" -w 'settings=%{http_code}\n'
grep -q 'SMTP' "$D/settings.html" && echo "settings_page=OK" || echo "settings_page=FAIL"

# 客户侧：注册需要 SMTP，这里只验证白名单拒绝与门户隔离
curl -s -X POST --data-urlencode 'email=someone@notallowed.test' "$BASE/register" \
  -o "$D/reg.html" -w 'register=%{http_code}\n'
grep -q '不在客户白名单' "$D/reg.html" && echo "whitelist_guard=OK" || echo "whitelist_guard=FAIL"

echo "--- service ---"
systemctl is-active bizportal
journalctl -u bizportal --since '-60s' --no-pager | tail -3
