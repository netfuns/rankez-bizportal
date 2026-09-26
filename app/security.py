"""密码哈希 / 口令策略 / 会话令牌 / TOTP。"""
from __future__ import annotations

import base64
import hashlib
import hmac
import io
import re
import secrets
import string
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import jwt
import pyotp
import qrcode

from .config import settings

_PBKDF2_ITER = 200_000
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")
_SPECIAL_RE = re.compile(r"[^A-Za-z0-9]")
_TEMP_ALPHABET = string.ascii_letters + string.digits + "!@#$%^&*"


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------- 密码


def hash_password(raw: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", raw.encode("utf-8"), salt, _PBKDF2_ITER)
    return "pbkdf2_sha256$%d$%s$%s" % (
        _PBKDF2_ITER,
        base64.b64encode(salt).decode(),
        base64.b64encode(dk).decode(),
    )


def verify_password(raw: str, hashed: str) -> bool:
    try:
        algo, iter_s, salt_b64, dk_b64 = hashed.split("$")
        if algo != "pbkdf2_sha256":
            return False
        salt = base64.b64decode(salt_b64)
        expect = base64.b64decode(dk_b64)
    except Exception:  # noqa: BLE001 - 任何解析失败都视为不匹配
        return False
    dk = hashlib.pbkdf2_hmac("sha256", raw.encode("utf-8"), salt, int(iter_s))
    return hmac.compare_digest(dk, expect)


def password_problems(raw: str) -> list[str]:
    """返回密码不满足策略的原因列表；空列表表示通过。"""
    problems: list[str] = []
    if len(raw) < 10:
        problems.append("长度至少 10 位")
    if not re.search(r"[A-Z]", raw):
        problems.append("需包含大写英文字母")
    if not re.search(r"[a-z]", raw):
        problems.append("需包含小写英文字母")
    if not re.search(r"[0-9]", raw):
        problems.append("需包含数字")
    if not _SPECIAL_RE.search(raw):
        problems.append("需包含特殊字符")
    return problems


def validate_password(raw: str) -> str | None:
    problems = password_problems(raw)
    return "；".join(problems) if problems else None


def generate_temp_password(length: int = 14) -> str:
    """生成一定满足口令策略的临时密码。"""
    while True:
        pwd = "".join(secrets.choice(_TEMP_ALPHABET) for _ in range(length))
        if not password_problems(pwd):
            return pwd


# ---------------------------------------------------------------- 邮箱


def normalize_email(value: str) -> str:
    return value.strip().lower()


def is_valid_email(value: str) -> bool:
    return bool(_EMAIL_RE.match(value.strip()))


def email_domain(value: str) -> str:
    return normalize_email(value).split("@", 1)[-1]


# ---------------------------------------------------------------- 会话令牌


def _sign(payload: dict, ttl_minutes: int) -> str:
    now = utcnow()
    body = dict(payload)
    body["iat"] = now
    body["exp"] = now + timedelta(minutes=ttl_minutes)
    return jwt.encode(body, settings.secret_key, algorithm="HS256")


def create_session_token(user_id: int, role: str, token_version: int, ttl_minutes: int) -> str:
    return _sign(
        {"sub": str(user_id), "typ": "session", "role": role, "tv": token_version},
        ttl_minutes,
    )


def create_preauth_token(user_id: int) -> str:
    """阶段一登录通过后的短时效令牌，用于 TOTP 挑战 / 绑定。"""
    return _sign({"sub": str(user_id), "typ": "preauth"}, 10)


def decode_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, settings.secret_key, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None


# ---------------------------------------------------------------- TOTP


def new_totp_secret() -> str:
    return pyotp.random_base32()


def totp_provisioning_uri(secret: str, account: str, issuer: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=account, issuer_name=issuer)


def totp_qr_data_uri(uri: str) -> str:
    """SVG 二维码 data URI（无需 Pillow，减小镜像体积）。"""
    import qrcode.image.svg

    factory = qrcode.image.svg.SvgPathImage
    img = qrcode.make(uri, image_factory=factory, box_size=12, border=2)
    buf = io.BytesIO()
    img.save(buf)
    return "data:image/svg+xml;base64," + base64.b64encode(buf.getvalue()).decode()


def verify_totp(secret: str, code: str) -> bool:
    code = (code or "").strip().replace(" ", "")
    if not secret or not code or not code.isdigit():
        return False
    return pyotp.TOTP(secret).verify(code, valid_window=1)


# ---------------------------------------------------------------- 其它


def money(value: Decimal | float | int | str) -> Decimal:
    return Decimal(str(value or "0")).quantize(Decimal("0.01"))
