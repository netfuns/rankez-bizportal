"""端到端主流程测试。"""
from __future__ import annotations

import pyotp
from sqlalchemy import select

from app.models import Customer, MailOutbox, Order, User
from app.security import verify_password

from .helpers import (
    admin_login,
    confirm_token_for,
    create_customer,
    login_user,
    secret_from,
    totp_code,
)


def test_admin_default_credentials(client):
    admin_login(client)
    resp = client.get("/admin")
    assert resp.status_code == 200
    assert "Admin Overview" in resp.text


def test_admin_can_create_customer_with_domains(client):
    admin_login(client)
    create_customer(client, "ACME", "acme.com, acme.io", "ACME 科技有限公司")
    resp = client.get("/admin/customers")
    assert "ACME" in resp.text
    assert "acme.com" in resp.text
    assert "acme.io" in resp.text


def test_registration_rejected_for_non_whitelist_domain(client):
    admin_login(client)
    create_customer(client, "WHITE", "white.com")
    client.get("/logout")
    resp = client.post("/register", data={"email": "someone@notallowed.com"})
    assert "whitelist" in resp.text or "whitelist" in str(resp.url)


def test_register_confirm_login_and_see_balance(client, db):
    admin_login(client)
    create_customer(client, "GLOBEX", "globex.com")
    # 充值 500，新购 120 → 余额 380
    cust = db.execute(select(Customer).where(Customer.assumed_name == "GLOBEX")).scalar_one()
    client.post(
        "/admin/orders/new",
        data={
            "customer_id": str(cust.id),
            "po_number": "PO-1001",
            "order_type": "充值",
            "product_name": "账户充值",
            "quantity": "1",
            "amount": "500",
            "remark": "",
        },
    )
    client.post(
        "/admin/orders/new",
        data={
            "customer_id": str(cust.id),
            "po_number": "PO-1002",
            "order_type": "新购",
            "product_name": "PAM 授权 10 席",
            "quantity": "10",
            "amount": "120",
            "remark": "首单",
        },
    )
    db.expire_all()
    cust = db.execute(select(Customer).where(Customer.assumed_name == "GLOBEX")).scalar_one()
    assert float(cust.balance) == 380.0
    client.get("/logout")

    # 自助注册
    resp = client.post("/register", data={"email": "alice@globex.com", "full_name": "爱丽丝"})
    assert "Registration received" in resp.text or "Registration received" in str(resp.url)
    db.expire_all()
    pending = db.execute(select(User).where(User.email == "alice@globex.com")).scalar_one()
    assert pending.status == "pending"
    assert pending.customer_id == cust.id
    assert pending.must_change_password is True
    assert pending.must_bind_totp is True

    # 确认邮件入库（未配置 SMTP）
    token = confirm_token_for(db, "alice@globex.com")
    mails = db.execute(
        select(MailOutbox).where(MailOutbox.to_addr == "alice@globex.com")
    ).scalars().all()
    assert mails and mails[0].status == "failed"
    temp = [
        ln for ln in mails[0].body.splitlines() if ln.startswith("临时密码：")
    ][0].split("：", 1)[1]

    # 未确认不能登录（即使密码正确）
    resp = client.post("/login", data={"email": "alice@globex.com", "password": temp})
    assert "Please complete email confirmation first" in resp.text

    client.get(f"/confirm-email?token={token}")
    db.expire_all()
    user = db.execute(select(User).where(User.email == "alice@globex.com")).scalar_one()
    assert user.status == "active"
    assert verify_password(temp, user.password_hash)

    final = login_user(client, "alice@globex.com", temp)
    assert final.status_code == 200
    db.expire_all()
    user = db.execute(select(User).where(User.email == "alice@globex.com")).scalar_one()
    assert user.totp_secret
    assert user.must_change_password is False
    assert verify_password("Newpass@2026", user.password_hash)

    # 首页能看到余额与消费记录
    dash = client.get("/")
    assert "380.00" in dash.text
    assert "PO-1001" in dash.text
    assert "PAM 授权 10 席" in dash.text


def test_user_cannot_see_other_company_and_admin_pages(client, db):
    admin_login(client)
    create_customer(client, "INITECH", "initech.com")
    cust = db.execute(select(Customer).where(Customer.assumed_name == "INITECH")).scalar_one()
    client.post(
        "/admin/orders/new",
        data={
            "customer_id": str(cust.id),
            "po_number": "PO-2001",
            "order_type": "充值",
            "product_name": "",
            "quantity": "1",
            "amount": "999",
            "remark": "",
        },
    )
    client.get("/logout")

    client.post("/register", data={"email": "bob@initech.com"})
    token = confirm_token_for(db, "bob@initech.com")
    client.get(f"/confirm-email?token={token}")
    mails = db.execute(
        select(MailOutbox).where(MailOutbox.to_addr == "bob@initech.com")
    ).scalars().all()
    temp = [ln for ln in mails[0].body.splitlines() if ln.startswith("临时密码：")][0].split("：", 1)[1]
    login_user(client, "bob@initech.com", temp)

    dash = client.get("/")
    assert "PO-2001" in dash.text
    assert "GLOBEX" not in dash.text

    admin_page = client.get("/admin")
    assert "Admin Overview" not in admin_page.text


def test_password_policy_enforced(client):
    admin_login(client)
    resp = client.post(
        "/change-password",
        data={
            "current_password": "Admin@123",
            "new_password": "short1!",
            "confirm_password": "short1!",
        },
    )
    assert resp.status_code == 200
    assert "New password does not meet the requirements" in resp.text or "requirements" in str(resp.url)


def test_admin_manual_user_and_bulk_create(client, db):
    admin_login(client)
    create_customer(client, "UMBRELLA", "umbrella.com")
    resp = client.post(
        "/admin/users/new",
        data={
            "email": "carol@umbrella.com",
            "full_name": "卡罗尔",
            "phone": "13000000000",
            "customer_id": "",
            "must_change_password": "on",
            "must_bind_totp": "on",
            "skip_confirm": "on",
        },
    )
    assert resp.status_code == 200
    db.expire_all()
    carol = db.execute(select(User).where(User.email == "carol@umbrella.com")).scalar_one()
    assert carol.status == "active"
    cust = db.execute(select(Customer).where(Customer.assumed_name == "UMBRELLA")).scalar_one()
    assert carol.customer_id == cust.id

    resp = client.post(
        "/admin/users/bulk",
        data={
            "batch": "dave@umbrella.com,戴夫,13100000000\neve@umbrella.com\nfrank@nodomain.com",
            "must_change_password": "on",
            "must_bind_totp": "on",
            "skip_confirm": "on",
        },
    )
    assert resp.status_code == 200
    db.expire_all()
    assert db.execute(select(User).where(User.email == "dave@umbrella.com")).scalar_one()
    assert db.execute(select(User).where(User.email == "eve@umbrella.com")).scalar_one()
    # 管理员手工加入不受域名白名单限制，但未匹配到公司时没有归属
    db.rollback()
    frank = db.execute(
        select(User).where(User.email == "frank@nodomain.com")
    ).scalar_one_or_none()
    assert frank is not None
    assert frank.customer_id is None


def test_admin_force_reset_totp_requires_rebind(client, db):
    admin_login(client)
    create_customer(client, "SOYLENT", "soylent.com")
    client.post(
        "/admin/users/new",
        data={
            "email": "greg@soylent.com",
            "full_name": "",
            "phone": "",
            "customer_id": "",
            "must_change_password": "on",
            "must_bind_totp": "on",
            "send_mail": "on",
        },
    )
    db.rollback()
    token = confirm_token_for(db, "greg@soylent.com")
    client.get(f"/confirm-email?token={token}")
    mails = db.execute(
        select(MailOutbox).where(MailOutbox.to_addr == "greg@soylent.com")
    ).scalars().all()
    temp = [ln for ln in mails[0].body.splitlines() if ln.startswith("临时密码：")][0].split("：", 1)[1]
    client.get("/logout")
    login_user(client, "greg@soylent.com", temp)
    client.get("/logout")

    admin_login(client)
    greg = db.execute(select(User).where(User.email == "greg@soylent.com")).scalar_one()
    old_secret = greg.totp_secret
    client.post(f"/admin/users/{greg.id}/reset-totp")
    db.expire_all()
    greg = db.execute(select(User).where(User.email == "greg@soylent.com")).scalar_one()
    assert greg.totp_secret is None
    assert greg.must_bind_totp is True
    assert old_secret
    client.get("/logout")

    # 重新登录必须重新绑定
    client.post("/login", data={"email": "greg@soylent.com", "password": "Newpass@2026"})
    page = client.get("/login/totp")
    new_secret = secret_from(page.text)
    assert new_secret and new_secret != old_secret
    resp = client.post("/login/totp", data={"code": totp_code(new_secret), "secret": new_secret})
    assert resp.status_code == 200
    db.expire_all()
    greg = db.execute(select(User).where(User.email == "greg@soylent.com")).scalar_one()
    assert greg.totp_secret == new_secret
    assert greg.must_bind_totp is False


def test_self_service_totp_reset_needs_current_code(client, db):
    admin_login(client)
    create_customer(client, "HOOLI", "hooli.com")
    client.post(
        "/admin/users/new",
        data={
            "email": "hank@hooli.com",
            "full_name": "",
            "phone": "",
            "customer_id": "",
            "must_change_password": "on",
            "must_bind_totp": "on",
            "send_mail": "on",
        },
    )
    db.rollback()
    token = confirm_token_for(db, "hank@hooli.com")
    client.get(f"/confirm-email?token={token}")
    mails = db.execute(
        select(MailOutbox).where(MailOutbox.to_addr == "hank@hooli.com")
    ).scalars().all()
    temp = [ln for ln in mails[0].body.splitlines() if ln.startswith("临时密码：")][0].split("：", 1)[1]
    client.get("/logout")
    login_user(client, "hank@hooli.com", temp)

    # 错误的当前口令 → 拒绝
    resp = client.post("/profile/totp/reset", data={"code": "000000"})
    assert "TOTP code is incorrect" in resp.text or "TOTP code is incorrect" in str(resp.url)

    db.expire_all()
    hank = db.execute(select(User).where(User.email == "hank@hooli.com")).scalar_one()
    resp = client.post("/profile/totp/reset", data={"code": pyotp.TOTP(hank.totp_secret).now()})
    assert resp.status_code == 200
    db.expire_all()
    hank = db.execute(select(User).where(User.email == "hank@hooli.com")).scalar_one()
    assert hank.totp_secret is None

    page = client.get("/setup-totp")
    secret = secret_from(page.text)
    client.post("/setup-totp", data={"secret": secret, "code": totp_code(secret), "current_code": ""})
    db.expire_all()
    hank = db.execute(select(User).where(User.email == "hank@hooli.com")).scalar_one()
    assert hank.totp_secret == secret


def test_custom_order_type_changes_balance_direction(client, db):
    admin_login(client)
    create_customer(client, "CYBERDYNE", "cyberdyne.com")
    # 自定义订单类型：返点 = credit
    client.post(
        "/admin/settings",
        data={
            "site_name": "BizPortal",
            "browser_title": "BizPortal",
            "allowed_hosts": "",
            "session_timeout_minutes": "120",
            "order_types": "充值,credit\n新购,debit\n续费,debit\n返点,credit",
            "smtp_host": "",
            "smtp_port": "587",
            "smtp_user": "",
            "smtp_password": "",
            "smtp_from": "",
            "registration_enabled": "on",
            "force": "1",
        },
    )
    cust = db.execute(select(Customer).where(Customer.assumed_name == "CYBERDYNE")).scalar_one()
    client.post(
        "/admin/orders/new",
        data={
            "customer_id": str(cust.id),
            "po_number": "PO-3001",
            "order_type": "返点",
            "product_name": "年度返点",
            "quantity": "1",
            "amount": "88",
            "remark": "",
        },
    )
    db.expire_all()
    cust = db.execute(select(Customer).where(Customer.assumed_name == "CYBERDYNE")).scalar_one()
    assert float(cust.balance) == 88.0
    order = db.execute(
        select(Order).where(Order.customer_id == cust.id)
    ).scalars().first()
    assert order.effect == "credit"

    # 续费扣减
    client.post(
        "/admin/orders/new",
        data={
            "customer_id": str(cust.id),
            "po_number": "PO-3002",
            "order_type": "续费",
            "product_name": "年度续费",
            "quantity": "1",
            "amount": "30",
            "remark": "",
        },
    )
    db.expire_all()
    cust = db.execute(select(Customer).where(Customer.assumed_name == "CYBERDYNE")).scalar_one()
    assert float(cust.balance) == 58.0


def test_order_delete_recalculates_balance(client, db):
    admin_login(client)
    create_customer(client, "TYRELL", "tyrell.com")
    cust = db.execute(select(Customer).where(Customer.assumed_name == "TYRELL")).scalar_one()
    client.post(
        "/admin/orders/new",
        data={
            "customer_id": str(cust.id),
            "po_number": "PO-4001",
            "order_type": "充值",
            "product_name": "",
            "quantity": "1",
            "amount": "1000",
            "remark": "",
        },
    )
    client.post(
        "/admin/orders/new",
        data={
            "customer_id": str(cust.id),
            "po_number": "PO-4002",
            "order_type": "新购",
            "product_name": "",
            "quantity": "1",
            "amount": "250",
            "remark": "",
        },
    )
    db.expire_all()
    cust = db.execute(select(Customer).where(Customer.assumed_name == "TYRELL")).scalar_one()
    assert float(cust.balance) == 750.0

    order = db.execute(select(Order).where(Order.po_number == "PO-4002")).scalar_one()
    client.post(f"/admin/orders/{order.id}/delete")
    db.expire_all()
    cust = db.execute(select(Customer).where(Customer.assumed_name == "TYRELL")).scalar_one()
    assert float(cust.balance) == 1000.0


def test_allowed_hosts_guard(client):
    admin_login(client)
    client.post(
        "/admin/settings",
        data={
            "site_name": "BizPortal",
            "browser_title": "BizPortal",
            "allowed_hosts": "portal.example.com",
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
    resp = client.get("/admin")
    assert resp.status_code == 403

    # 恢复（需要 force 之外的路径：直接用正确的 Host 请求）
    resp = client.get("/admin", headers={"Host": "portal.example.com"})
    assert resp.status_code == 200
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
        headers={"Host": "portal.example.com"},
    )
    assert client.get("/admin").status_code == 200
