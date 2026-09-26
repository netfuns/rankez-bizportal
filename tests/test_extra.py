"""补充测试：设置项、客户维护、批量删除、个人资料。"""
from __future__ import annotations

import pyotp
from sqlalchemy import select

from app.models import Customer, Order, User
from app.settings_service import get_order_types

from .helpers import (
    admin_login,
    confirm_token_for,
    create_customer,
    login_user,
)


def test_session_timeout_setting_applies(client, db):
    admin_login(client)
    client.post(
        "/admin/settings",
        data={
            "site_name": "客户门户",
            "browser_title": "客户门户",
            "allowed_hosts": "",
            "session_timeout_minutes": "30",
            "order_types": "充值,credit\n新购,debit\n续费,debit",
            "smtp_host": "",
            "smtp_port": "587",
            "smtp_user": "",
            "smtp_password": "",
            "smtp_from": "",
            "registration_enabled": "on",
            "force": "1",
        },
    )
    client.get("/logout")
    resp = client.post("/login", data={"email": "admin", "password": "Admin@123"})
    assert resp.status_code == 200
    cookie = client.cookies.get("bp_session")
    assert cookie
    import jwt

    from app.config import settings

    payload = jwt.decode(cookie, settings.secret_key, algorithms=["HS256"])
    assert payload["exp"] - payload["iat"] == 30 * 60

    # 恢复默认
    admin_login(client)
    client.post(
        "/admin/settings",
        data={
            "site_name": "BizPortal",
            "browser_title": "BizPortal",
            "allowed_hosts": "",
            "session_timeout_minutes": "120",
            "order_types": "充值,credit\n新购,debit\n续费,debit",
            "smtp_host": "",
            "smtp_port": "587",
            "smtp_user": "",
            "smtp_password": "",
            "smtp_from": "",
            "registration_enabled": "on",
            "force": "1",
        },
    )
    assert len(get_order_types(db)) == 3


def test_customer_domain_edit_affects_registration(client, db):
    admin_login(client)
    create_customer(client, "MASSIVE", "massive.com, massive.net")
    cust = db.execute(select(Customer).where(Customer.assumed_name == "MASSIVE")).scalar_one()
    client.get("/logout")
    client.post("/register", data={"email": "one@massive.net"})
    db.rollback()
    assert db.execute(select(User).where(User.email == "one@massive.net")).scalar_one_or_none()

    admin_login(client)
    client.post(
        f"/admin/customers/{cust.id}/edit",
        data={
            "assumed_name": "MASSIVE",
            "legal_name": "",
            "registration_address": "",
            "domains": "massive.com",
            "note": "",
            "org_contact_id": "",
            "tech_contact_id": "",
            "is_active": "on",
        },
    )
    db.rollback()
    cust = db.execute(select(Customer).where(Customer.assumed_name == "MASSIVE")).scalar_one()
    assert [d.domain for d in cust.domains] == ["massive.com"]

    client.get("/logout")
    resp = client.post("/register", data={"email": "two@massive.net"})
    assert "whitelist" in resp.text


def test_customer_delete_cascades_orders(client, db):
    admin_login(client)
    create_customer(client, "VECTOR", "vector.com")
    cust = db.execute(select(Customer).where(Customer.assumed_name == "VECTOR")).scalar_one()
    client.post(
        "/admin/orders/new",
        data={
            "customer_id": str(cust.id),
            "po_number": "PO-9001",
            "order_type": "充值",
            "product_name": "",
            "quantity": "1",
            "amount": "100",
            "remark": "",
        },
    )
    db.rollback()
    assert db.execute(select(Order).where(Order.customer_id == cust.id)).scalars().first()
    client.post(f"/admin/customers/{cust.id}/delete")
    db.rollback()
    assert db.execute(select(Customer).where(Customer.assumed_name == "VECTOR")).scalar_one_or_none() is None
    assert not db.execute(select(Order).where(Order.po_number == "PO-9001")).scalars().first()


def test_bulk_delete_users(client, db):
    admin_login(client)
    create_customer(client, "PIEDPIPER", "piedpiper.com")
    client.post(
        "/admin/users/bulk",
        data={
            "batch": "u1@piedpiper.com\nu2@piedpiper.com\nu3@piedpiper.com",
            "must_change_password": "on",
            "must_bind_totp": "on",
            "skip_confirm": "on",
        },
    )
    db.rollback()
    ids = [
        u.id
        for u in db.execute(
            select(User).where(User.email.like("%@piedpiper.com"))
        ).scalars().all()
    ]
    assert len(ids) == 3
    client.post("/admin/users/bulk-delete", data={"ids": ",".join(str(i) for i in ids)})
    db.rollback()
    assert (
        db.execute(select(User).where(User.email.like("%@piedpiper.com"))).scalars().first()
        is None
    )


def test_user_updates_own_profile(client, db):
    admin_login(client)
    create_customer(client, "STARK", "stark.com")
    client.post(
        "/admin/users/new",
        data={
            "email": "tony@stark.com",
            "legal_name": "",
            "phone": "",
            "customer_id": "",
            "must_change_password": "on",
            "must_bind_totp": "on",
            "send_mail": "on",
        },
    )
    db.rollback()
    token = confirm_token_for(db, "tony@stark.com")
    client.get(f"/confirm-email?token={token}")
    from app.models import MailOutbox

    mail = db.execute(
        select(MailOutbox).where(MailOutbox.to_addr == "tony@stark.com")
    ).scalars().first()
    temp = [
        ln for ln in mail.body.splitlines() if ln.startswith("临时密码：")
    ][0].split("：", 1)[1]
    client.get("/logout")
    login_user(client, "tony@stark.com", temp)

    client.post("/profile", data={"full_name": "Tony", "phone": "13900000000"})
    db.rollback()
    tony = db.execute(select(User).where(User.email == "tony@stark.com")).scalar_one()
    assert tony.full_name == "Tony"
    assert tony.phone == "13900000000"

    # 客户门户应能看到所属公司
    dash = client.get("/")
    assert "STARK" in dash.text


def test_primary_contact_visible_to_customer_users(client, db):
    admin_login(client)
    create_customer(client, "WAYNE", "wayne.com")
    client.post(
        "/admin/users/new",
        data={
            "email": "bruce@wayne.com",
            "full_name": "Bruce",
            "phone": "",
            "customer_id": "",
            "must_change_password": "on",
            "must_bind_totp": "on",
            "send_mail": "on",
        },
    )
    db.rollback()
    token = confirm_token_for(db, "bruce@wayne.com")
    client.get(f"/confirm-email?token={token}")
    from app.models import MailOutbox

    mail = db.execute(
        select(MailOutbox).where(MailOutbox.to_addr == "bruce@wayne.com")
    ).scalars().first()
    temp = [
        ln for ln in mail.body.splitlines() if ln.startswith("临时密码：")
    ][0].split("：", 1)[1]
    client.get("/logout")
    login_user(client, "bruce@wayne.com", temp)

    admin_login(client)
    cust = db.execute(select(Customer).where(Customer.assumed_name == "WAYNE")).scalar_one()
    bruce = db.execute(select(User).where(User.email == "bruce@wayne.com")).scalar_one()
    client.post(
        f"/admin/customers/{cust.id}/edit",
        data={
            "assumed_name": "WAYNE",
            "legal_name": "Wayne Enterprises",
            "registration_address": "Gotham",
            "domains": "wayne.com",
            "note": "",
            "org_contact_id": str(bruce.id),
            "tech_contact_id": "",
            "is_active": "on",
        },
    )
    client.get("/logout")
    client.post("/login", data={"email": "bruce@wayne.com", "password": "Newpass@2026"})
    db.rollback()
    bruce = db.execute(select(User).where(User.email == "bruce@wayne.com")).scalar_one()
    client.post("/login/totp", data={"code": pyotp.TOTP(bruce.totp_secret).now()})
    dash = client.get("/")
    assert "Bruce" in dash.text
    assert "Wayne Enterprises" in dash.text
