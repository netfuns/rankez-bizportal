#!/usr/bin/env python3
"""清理线上冒烟数据：删除 demo 用户、DEMO 客户及其订单、相关邮件记录。"""
from __future__ import annotations

import http.cookiejar
import sqlite3
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:8888"
DB = "file:/var/lib/bizportal/bizportal.db?mode=ro"
jar = http.cookiejar.CookieJar()
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def post(path: str, data: dict) -> None:
    body = urllib.parse.urlencode(data).encode()
    op.open(urllib.request.Request(BASE + path, data=body, method="POST")).read()


def main() -> None:
    post("/login", {"email": "admin", "password": "Admin@123"})
    con = sqlite3.connect(DB, uri=True)
    row = con.execute("select id from users where email='demo@demo.com'").fetchone()
    if row:
        post(f"/admin/users/{row[0]}/delete", {})
        print("[ok] 已删除 demo 用户")
    crow = con.execute("select id from customers where short_name='DEMO'").fetchone()
    if crow:
        post(f"/admin/customers/{crow[0]}/delete", {})
        print("[ok] 已删除 DEMO 客户及其订单")
    con2 = sqlite3.connect(DB, uri=True)
    left_users = con2.execute("select count(*) from users").fetchone()[0]
    left_customers = con2.execute("select count(*) from customers").fetchone()[0]
    left_orders = con2.execute("select count(*) from orders").fetchone()[0]
    print(f"剩余：用户 {left_users} / 客户 {left_customers} / 订单 {left_orders}")


if __name__ == "__main__":
    main()
