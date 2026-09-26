"""OV 工单流程与多语言测试。"""
from __future__ import annotations

from sqlalchemy import select

import pyotp

from app.models import Customer, CustomerDomain, MailOutbox, Ticket, User

from .helpers import (
    admin_login,
    confirm_token_for,
    create_customer,
    login_user,
    totp_code,
    secret_from,
)


def _temp_password(db, email: str) -> str:
    mail = db.execute(
        select(MailOutbox).where(MailOutbox.to_addr == email).order_by(MailOutbox.id.desc())
    ).scalars().first()
    assert mail is not None
    return [ln for ln in mail.body.splitlines() if ln.startswith("临时密码：")][0].split("：", 1)[1]


def _first_login(client, email: str, temp: str, new_password: str = "Newpass@2026"):
    client.post("/login", data={"email": email, "password": temp})
    page = client.get("/login/totp")
    secret = secret_from(page.text)
    client.post("/login/totp", data={"code": totp_code(secret), "secret": secret})
    client.post(
        "/change-password",
        data={
            "current_password": temp,
            "new_password": new_password,
            "confirm_password": new_password,
        },
    )


def _relogin(client, db, email: str, password: str):
    """已绑定 TOTP 的用户再次登录：密码 + TOTP。"""
    client.post("/login", data={"email": email, "password": password})
    client.get("/login/totp")
    user = db.execute(select(User).where(User.email == email)).scalar_one()
    client.post("/login/totp", data={"code": pyotp.TOTP(user.totp_secret).now()})
    return client


def _make_customer_with_contact(client, db, assumed="OVCO", domain="ovco.com", email="pen@ovco.com"):
    """管理员建客户 + 手工建用户（跳过确认）+ 设为公司联系人。返回 (cust, user)。"""
    admin_login(client)
    create_customer(client, assumed, domain)
    client.post(
        "/admin/users/new",
        data={
            "email": email,
            "full_name": "Pen",
            "phone": "",
            "customer_id": "",
            "must_change_password": "on",
            "must_bind_totp": "on",
            "send_mail": "on",
        },
    )
    token = confirm_token_for(db, email)
    client.get(f"/confirm-email?token={token}")
    cust = db.execute(select(Customer).where(Customer.assumed_name == assumed)).scalar_one()
    user = db.execute(select(User).where(User.email == email)).scalar_one()
    client.post(
        f"/admin/customers/{cust.id}/edit",
        data={
            "assumed_name": assumed,
            "legal_name": "",
            "registration_address": "",
            "telephone": "",
            "domains": domain,
            "note": "",
            "org_contact_id": str(user.id),
            "tech_contact_id": "",
            "is_active": "on",
        },
    )
    client.get("/logout")

    temp = _temp_password(db, email)
    _first_login(client, email, temp)
    return cust, user


def test_ov_request_full_flow(client, db):
    cust, user = _make_customer_with_contact(client, db)

    # 普通联系人可见 OV Request 按钮
    dash = client.get("/")
    assert "OV Request" in dash.text
    assert "OV In Review" not in dash.text

    # 提交验证申请
    resp = client.post("/company/ov-request", data={"domain": "ovco.com"}, follow_redirects=False)
    assert "kind=ok" in resp.headers["location"]

    db.expire_all()
    domain_row = db.execute(
        select(CustomerDomain).where(CustomerDomain.domain == "ovco.com")
    ).scalar_one()
    assert domain_row.ov_status == "verifying"
    ticket = db.execute(select(Ticket)).scalars().first()
    assert ticket is not None
    assert ticket.status == "requested"
    assert ticket.ticket_type == "Company Verification"
    assert ticket.number.startswith("TK-")

    # 按钮变成蓝色状态标签
    dash = client.get("/")
    assert "OV In Review" in dash.text
    assert "OV Request" not in dash.text

    # 管理端工单列表可见，可筛选
    admin_login(client)
    lst = client.get("/admin/tickets")
    assert ticket.number in lst.text
    lst = client.get("/admin/tickets", params={"assumed": "OVCO"})
    assert ticket.number in lst.text
    lst = client.get("/admin/tickets", params={"assumed": "NOMATCH"})
    assert "No tickets" in lst.text
    lst = client.get("/admin/tickets", params={"status": "verified"})
    assert "No tickets" in lst.text

    # 确认验证
    resp = client.post(f"/admin/tickets/{ticket.id}/verify", follow_redirects=False)
    assert "kind=ok" in resp.headers["location"]
    db.expire_all()
    ticket = db.execute(select(Ticket)).scalars().first()
    assert ticket.status == "verified"
    domain_row = db.execute(
        select(CustomerDomain).where(CustomerDomain.domain == "ovco.com")
    ).scalar_one()
    assert domain_row.ov_status == "verified"

    # 用户端显示绿色已验证标签
    client.get("/logout")
    _relogin(client, db, "pen@ovco.com", "Newpass@2026")
    dash = client.get("/")
    assert "OV Verified" in dash.text


def test_ov_request_requires_contact_role(client, db):
    admin_login(client)
    create_customer(client, "NOCONTACT", "nocontact.com")
    client.post(
        "/admin/users/new",
        data={
            "email": "plain@nocontact.com",
            "full_name": "",
            "phone": "",
            "customer_id": "",
            "must_change_password": "on",
            "must_bind_totp": "on",
            "send_mail": "on",
        },
    )
    token = confirm_token_for(db, "plain@nocontact.com")
    client.get(f"/confirm-email?token={token}")
    client.get("/logout")

    temp = _temp_password(db, "plain@nocontact.com")
    _first_login(client, "plain@nocontact.com", temp)

    dash = client.get("/")
    assert "OV Request" not in dash.text
    resp = client.post("/company/ov-request", data={"domain": "nocontact.com"})
    assert "Only organization or technical contacts" in resp.text or "Only organization or technical contacts" in str(resp.url)
    db.rollback()
    cust = db.execute(select(Customer).where(Customer.assumed_name == "NOCONTACT")).scalar_one()
    assert (
        db.execute(select(Ticket).where(Ticket.customer_id == cust.id)).scalars().first() is None
    )


def test_ticket_types_configurable_in_settings(client, db):
    admin_login(client)
    client.post(
        "/admin/settings",
        data={
            "site_name": "BizPortal",
            "browser_title": "BizPortal",
            "allowed_hosts": "",
            "session_timeout_minutes": "120",
            "order_types": "充值,credit\n新购,debit\n续费,debit",
            "ticket_types": "Company Verification,Trademark Verification",
            "smtp_host": "",
            "smtp_port": "587",
            "smtp_user": "",
            "smtp_password": "",
            "smtp_from": "",
            "registration_enabled": "on",
            "force": "1",
        },
    )
    page = client.get("/admin/settings")
    assert 'data-value="Company Verification,Trademark Verification"' in page.text

    cust, user = _make_customer_with_contact(client, db, assumed="TYPES", domain="types.com", email="t@types.com")
    client.get("/logout")
    client.post("/login", data={"email": "t@types.com", "password": "Newpass@2026"})
    client.post("/company/ov-request", data={"domain": "types.com"})
    db.expire_all()
    ticket = db.execute(select(Ticket).order_by(Ticket.id.desc())).scalars().first()
    assert ticket.ticket_type == "Company Verification"


def test_i18n_switching(client):
    # 默认英文
    page = client.get("/login")
    assert "Log In" in page.text
    assert "概览" not in page.text

    # 切简体
    resp = client.get("/set-locale", params={"lang": "zh-CN", "next": "/login"}, follow_redirects=False)
    assert resp.status_code == 303
    page = client.get("/login")
    assert "登录" in page.text
    assert "Log In" not in page.text

    # 切繁体
    client.get("/set-locale", params={"lang": "zh-TW", "next": "/login"})
    page = client.get("/login")
    assert "登入" in page.text

    # 切回英文
    client.get("/set-locale", params={"lang": "en", "next": "/login"})
    page = client.get("/login")
    assert "Log In" in page.text


def test_admin_page_ticket_nav_and_zh(client, db):
    admin_login(client)
    client.get("/set-locale", params={"lang": "zh-CN", "next": "/admin"})
    page = client.get("/admin")
    assert "工单" in page.text
    # 导航顺序：订单 → 工单 → 发件箱
    idx_orders = page.text.index(">订单</a>")
    idx_tickets = page.text.index(">工单</a>")
    idx_mail = page.text.index(">发件箱</a>")
    assert idx_orders < idx_tickets < idx_mail
    client.get("/set-locale", params={"lang": "en", "next": "/admin"})
