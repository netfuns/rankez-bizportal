"""测试用例辅助函数。"""
from __future__ import annotations

import re

import pyotp
from sqlalchemy import select

from app.models import EmailToken, User

_SECRET_RE = re.compile(r'name="secret"\s+value="([A-Za-z2-7=]+)"')


def admin_login(client) -> None:
    resp = client.post("/login", data={"email": "admin", "password": "Admin@123"})
    assert resp.status_code == 200, resp.text


def create_customer(client, assumed_name: str, domains: str, legal_name: str = "") -> None:
    resp = client.post(
        "/admin/customers/new",
        data={
            "assumed_name": assumed_name,
            "legal_name": legal_name,
            "registration_address": "",
            "telephone": "",
            "domains": domains,
            "note": "",
            "org_contact_id": "",
            "tech_contact_id": "",
        },
    )
    assert resp.status_code == 200, resp.text
    assert "kind=error" not in str(resp.url), str(resp.url)


def secret_from(html: str) -> str:
    m = _SECRET_RE.search(html)
    assert m, "页面中没有找到 TOTP 密钥"
    return m.group(1)


def totp_code(secret: str) -> str:
    return pyotp.TOTP(secret).now()


def confirm_token_for(db, email: str) -> str:
    user = db.execute(select(User).where(User.email == email)).scalar_one()
    row = (
        db.execute(
            select(EmailToken)
            .where(EmailToken.user_id == user.id, EmailToken.used_at.is_(None))
            .order_by(EmailToken.id.desc())
        )
        .scalars()
        .first()
    )
    assert row is not None, "没有生成确认令牌"
    return row.token


def login_user(client, email: str, password: str, new_password: str = "Newpass@2026"):
    """完整走一遍：密码 → 绑定 TOTP → 强制改密，返回最终落地页面。"""
    client.post("/login", data={"email": email, "password": password})
    page = client.get("/login/totp")
    secret = secret_from(page.text)
    resp = client.post("/login/totp", data={"code": totp_code(secret), "secret": secret})
    assert resp.status_code == 200
    return client.post(
        "/change-password",
        data={
            "current_password": password,
            "new_password": new_password,
            "confirm_password": new_password,
        },
    )
