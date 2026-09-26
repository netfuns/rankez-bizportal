"""系统设置的读写封装（键值对 + 轻量加密字段）。"""
from __future__ import annotations

import base64
import hashlib
import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .models import SystemSetting

DEFAULTS: dict[str, str] = {
    "site_name": "BizPortal",
    "browser_title": "BizPortal",
    "site_logo": "",
    "allowed_hosts": "",  # 空 = 不限制访问域名
    "session_timeout_minutes": "120",
    "force_totp": "false",
    "registration_enabled": "true",
    "order_types": json.dumps(
        [
            {"name": "充值", "effect": "credit"},
            {"name": "新购", "effect": "debit"},
            {"name": "续费", "effect": "debit"},
        ],
        ensure_ascii=False,
    ),
    "ticket_types": json.dumps(["Company Verification"], ensure_ascii=False),
    "smtp_host": "",
    "smtp_port": "587",
    "smtp_user": "",
    "smtp_password": "",
    "smtp_from": "",
    "smtp_tls": "true",
}

ENCRYPTED_KEYS = {"smtp_password"}


def _keystream(salt: bytes, length: int) -> bytes:
    out = bytearray()
    counter = 0
    seed = settings.secret_key.encode()
    while len(out) < length:
        out += hashlib.sha256(seed + salt + counter.to_bytes(4, "big")).digest()
        counter += 1
    return bytes(out[:length])


def obfuscate(raw: str) -> str:
    """对 SMTP 等敏感配置做可逆混淆，避免明文落库。"""
    if not raw:
        return ""
    salt = hashlib.sha256(settings.secret_key.encode()).digest()[:8]
    data = raw.encode()
    ks = _keystream(salt, len(data))
    enc = bytes(a ^ b for a, b in zip(data, ks))
    return base64.b64encode(salt + enc).decode()


def deobfuscate(token: str) -> str:
    if not token:
        return ""
    try:
        blob = base64.b64decode(token)
    except Exception:  # noqa: BLE001
        return ""
    if len(blob) < 8:
        return ""
    salt, enc = blob[:8], blob[8:]
    ks = _keystream(salt, len(enc))
    try:
        return bytes(a ^ b for a, b in zip(enc, ks)).decode()
    except Exception:  # noqa: BLE001
        return ""


def get_raw(db: Session, key: str) -> str:
    row = db.execute(select(SystemSetting).where(SystemSetting.key == key)).scalar_one_or_none()
    if row is None:
        return DEFAULTS.get(key, "")
    return row.value


def get(db: Session, key: str) -> str:
    value = get_raw(db, key)
    if key in ENCRYPTED_KEYS and value:
        return deobfuscate(value)
    return value


def get_int(db: Session, key: str) -> int:
    try:
        return int(str(get(db, key)).strip())
    except (TypeError, ValueError):
        return int(DEFAULTS.get(key, "0") or 0)


def get_bool(db: Session, key: str) -> bool:
    return str(get(db, key)).strip().lower() in {"1", "true", "yes", "on"}


def set_value(db: Session, key: str, value: str) -> None:
    if key in ENCRYPTED_KEYS and value:
        value = obfuscate(value)
    row = db.execute(select(SystemSetting).where(SystemSetting.key == key)).scalar_one_or_none()
    if row is None:
        db.add(SystemSetting(key=key, value=value))
    else:
        row.value = value
    db.commit()


def set_many(db: Session, pairs: dict[str, str]) -> None:
    for key, value in pairs.items():
        if key in DEFAULTS:
            set_value(db, key, value)


def get_order_types(db: Session) -> list[dict]:
    raw = get(db, "order_types")
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        data = json.loads(DEFAULTS["order_types"])
    result = []
    for item in data if isinstance(data, list) else []:
        name = str(item.get("name", "")).strip()
        if not name:
            continue
        effect = str(item.get("effect", "debit")).strip().lower()
        result.append({"name": name, "effect": "credit" if effect == "credit" else "debit"})
    return result or json.loads(DEFAULTS["order_types"])


def effect_of_type(db: Session, order_type: str) -> str:
    for item in get_order_types(db):
        if item["name"] == order_type:
            return item["effect"]
    return "debit"


def get_ticket_types(db: Session) -> list[str]:
    raw = get(db, "ticket_types")
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        data = json.loads(DEFAULTS["ticket_types"])
    if not isinstance(data, list):
        return json.loads(DEFAULTS["ticket_types"])
    result = [str(x).strip() for x in data if str(x).strip()]
    return result or json.loads(DEFAULTS["ticket_types"])


def ensure_defaults(db: Session) -> None:
    """把缺失的默认项写入数据库（幂等）。"""
    changed = False
    for key, value in DEFAULTS.items():
        exists = db.execute(
            select(SystemSetting.id).where(SystemSetting.key == key)
        ).scalar_one_or_none()
        if exists is None:
            db.add(SystemSetting(key=key, value=value))
            changed = True
    if changed:
        db.commit()
