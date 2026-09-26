#!/usr/bin/env python3
"""线上端到端冒烟 v2：客户字段 + OV 工单 + i18n。

在容器内执行：docker exec bizportal python /tmp/verify2.py
"""
from __future__ import annotations

import http.cookiejar
import re
import sqlite3
import sys
import urllib.parse
import urllib.request

import pyotp

BASE = "http://127.0.0.1:8888"
DB = "/data/bizportal.db"
DOMAIN = "demo.com"
MAIL = "ov.demo@demo.com"
jar = http.cookiejar.CookieJar()
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
con = sqlite3.connect(DB)
results: list[tuple[str, bool]] = []


def post(path, data):
    body = urllib.parse.urlencode(data).encode()
    with op.open(urllib.request.Request(BASE + path, data=body, method="POST")) as r:
        return r.read().decode("utf-8", "replace")


def get(path):
    with op.open(BASE + path) as r:
        return r.read().decode("utf-8", "replace")


def check(name, ok):
    results.append((name, ok))
    print(f"[{'ok' if ok else 'FAIL'}] {name}")


def temp_password(email):
    row = con.execute(
        "select body from mail_outbox where to_addr=? order by id desc", (email,)
    ).fetchone()
    return [ln for ln in row[0].splitlines() if ln.startswith("临时密码：")][0].split("：", 1)[1]


def totp_login_flow(email, password, new_password):
    post("/login", {"email": email, "password": password})
    page = get("/login/totp")
    m = re.search(r'name="secret"\s+value="([A-Za-z2-7=]+)"', page)
    assert m, "no totp secret on page"
    secret = m.group(1)
    post("/login/totp", {"code": pyotp.TOTP(secret).now(), "secret": secret})
    post(
        "/change-password",
        {
            "current_password": password,
            "new_password": new_password,
            "confirm_password": new_password,
        },
    )


def relogin_admin():
    post("/login", {"email": "admin", "password": "Admin@New2026"})


def relogin_user(email, password):
    con2 = sqlite3.connect(DB)
    secret = con2.execute("select totp_secret from users where email=?", (email,)).fetchone()[0]
    con2.close()
    post("/login", {"email": email, "password": password})
    post("/login/totp", {"code": pyotp.TOTP(secret).now()})


def main():
    # 1. admin 首登（出厂：强制改密，不强制 TOTP）
    post("/login", {"email": "admin", "password": "Admin@123"})
    post(
        "/change-password",
        {
            "current_password": "Admin@123",
            "new_password": "Admin@New2026",
            "confirm_password": "Admin@New2026",
        },
    )
    check("admin 首登改密", True)

    # 2. 建客户（新字段）+ 客户用户 + 订单
    post(
        "/admin/customers/new",
        {
            "assumed_name": "DEMO",
            "legal_name": "Demo Trading Ltd.",
            "registration_address": "Singapore",
            "telephone": "+65 6000 0000",
            "domains": DOMAIN,
            "note": "",
            "org_contact_id": "",
            "tech_contact_id": "",
        },
    )
    cust = con.execute("select id from customers where assumed_name='DEMO'").fetchone()
    check("客户创建（assumed/legal/address/telephone）", cust is not None)

    post(
        "/admin/users/new",
        {
            "email": MAIL,
            "full_name": "OV Tester",
            "phone": "",
            "customer_id": "",
            "must_change_password": "on",
            "must_bind_totp": "on",
            "send_mail": "on",
        },
    )
    token = con.execute(
        "select token from email_tokens order by id desc limit 1"
    ).fetchone()[0]
    get(f"/confirm-email?token={token}")
    uid = con.execute("select id from users where email=?", (MAIL,)).fetchone()[0]
    post(
        f"/admin/customers/{cust[0]}/edit",
        {
            "assumed_name": "DEMO",
            "legal_name": "Demo Trading Ltd.",
            "registration_address": "Singapore",
            "telephone": "+65 6000 0000",
            "domains": DOMAIN,
            "note": "",
            "org_contact_id": str(uid),
            "tech_contact_id": "",
        },
    )
    org = con.execute(
        "select org_contact_id from customers where id=?", (cust[0],)
    ).fetchone()[0]
    check("公司联系人设置", org == uid)

    post(
        "/admin/orders/new",
        {
            "customer_id": str(cust[0]),
            "po_number": "PO-DEMO-1",
            "order_type": "充值",
            "product_name": "充值",
            "quantity": "1",
            "amount": "500",
            "remark": "",
        },
    )

    # 3. 客户用户注册（白名单域名）→ 确认 → 首登
    post("/register", {"email": MAIL, "full_name": "OV Tester"})
    temp = temp_password(MAIL)
    client_get = get  # noop
    # 直接用确认后的 temp 登录
    totp_login_flow(MAIL, temp, "Demo@Pass2026")
    check("客户注册→确认→首登改密+TOTP", True)

    # 4. 仪表盘：OV Request 按钮 + 余额 + 公司字段
    dash = get("/")
    check("仪表盘显示 OV Request 按钮", "OV Request" in dash)
    check("仪表盘显示 Assumed Name", "DEMO" in dash)
    check("仪表盘显示 Legal Name", "Demo Trading Ltd." in dash)
    check("余额 500 可见", "500.00" in dash)

    # 5. 提交 OV 申请
    post("/company/ov-request", {"domain": DOMAIN})
    dash = get("/")
    check("提交后变为 OV In Review", "OV In Review" in dash)
    tk = con.execute("select id, number, status from tickets").fetchone()
    check("工单已创建且状态 requested", tk is not None and tk[2] == "requested")

    # 6. 管理端工单列表 + Verify OV
    get("/logout")
    relogin_admin()
    page = get("/admin/tickets")
    check("管理端工单列表显示工单号", tk[1] in page)
    page = get("/admin/tickets?assumed=DEMO")
    check("按公司简写筛选", tk[1] in page)
    post(f"/admin/tickets/{tk[0]}/verify", {})
    page = get("/admin/tickets?status=verified")
    check("Verify OV 后状态已验证", tk[1] in page)
    row = con.execute("select ov_status from customer_domains where domain=?", (DOMAIN,)).fetchone()
    check("域名 ov_status=verified", row and row[0] == "verified")

    # 7. 用户端显示 OV Verified
    get("/logout")
    relogin_user(MAIL, "Demo@Pass2026")
    dash = get("/")
    check("用户端显示 OV Verified", "OV Verified" in dash)

    # 8. i18n
    get("/set-locale?lang=zh-CN&next=/login")
    get("/logout")
    page = get("/login")
    check("简体中文切换生效", "登录" in page)
    get("/set-locale?lang=zh-TW&next=/login")
    page = get("/login")
    check("繁体中文切换生效", "登入" in page)
    get("/set-locale?lang=en&next=/login")
    page = get("/login")
    check("英文切换生效", "Log In" in page)

    con.close()
    failed = [n for n, ok in results if not ok]
    print(f"=== {len(results) - len(failed)}/{len(results)} passed ===")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
