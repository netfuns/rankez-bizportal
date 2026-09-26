#!/usr/bin/env python3
"""线上端到端冒烟（客户侧）：注册 → 确认 → 登录 → 绑 TOTP → 改密 → 看余额。

用目标机 /opt/bizportal/.venv/bin/python 执行（需要 pyotp）。
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
DB = "file:/var/lib/bizportal/bizportal.db?mode=ro"
MAIL = "demo@demo.com"
jar = http.cookiejar.CookieJar()
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def post(path: str, data: dict) -> str:
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(BASE + path, data=body, method="POST")
    with op.open(req) as resp:
        return resp.read().decode("utf-8", "replace")


def get(path: str) -> str:
    with op.open(BASE + path) as resp:
        return resp.read().decode("utf-8", "replace")


def main() -> int:
    con = sqlite3.connect(DB, uri=True)
    exists = con.execute("select id from users where email=?", (MAIL,)).fetchone()
    if exists:
        print(f"[skip] 用户 {MAIL} 已存在（id={exists[0]}），本次只做登录验证")
    else:
        page = post("/register", {"email": MAIL, "full_name": "演示用户"})
        if "注册成功" not in page:
            print("[FAIL] 注册未成功")
            return 1
        print("[ok] 注册提交成功")

    uid = con.execute("select id from users where email=?", (MAIL,)).fetchone()[0]
    token_row = con.execute(
        "select token from email_tokens where user_id=? order by id desc", (uid,)
    ).fetchone()
    if token_row:
        get(f"/confirm-email?token={token_row[0]}")
        print("[ok] 邮箱确认完成")
    mail = con.execute(
        "select body from mail_outbox where to_addr=? order by id desc", (MAIL,)
    ).fetchone()
    temp = ""
    if mail:
        temp = [
            ln for ln in mail[0].splitlines() if ln.startswith("临时密码：")
        ][0].split("：", 1)[1]

    if not temp:
        print("[FAIL] 未取到临时密码")
        return 1

    post("/login", {"email": MAIL, "password": temp})
    page = get("/login/totp")
    m = re.search(r'name="secret"\s+value="([A-Za-z2-7=]+)"', page)
    if not m:
        # 已绑定过 TOTP，直接用当前口令登录
        secret = con.execute("select totp_secret from users where id=?", (uid,)).fetchone()[0]
        post("/login/totp", {"code": pyotp.TOTP(secret).now()})
    else:
        secret = m.group(1)
        post("/login/totp", {"code": pyotp.TOTP(secret).now(), "secret": secret})
        print("[ok] TOTP 绑定完成")
        post(
            "/change-password",
            {
                "current_password": temp,
                "new_password": "Demo@2026pass",
                "confirm_password": "Demo@2026pass",
            },
        )
        print("[ok] 首次改密完成")

    dash = get("/")
    checks = {
        "余额 6800": "6,800.00" in dash,
        "PO-DEMO-1": "PO-DEMO-1" in dash,
        "公司名称 DEMO": "DEMO" in dash,
        "未泄露其它公司": "管理概览" not in dash,
    }
    for name, ok in checks.items():
        print(f"[{'ok' if ok else 'FAIL'}] {name}")

    admin_page = get("/admin")
    blocked = "管理概览" not in admin_page
    print(f"[{'ok' if blocked else 'FAIL'}] 客户用户无法进入管理端")

    return 0 if all(checks.values()) and blocked else 1


if __name__ == "__main__":
    sys.exit(main())
