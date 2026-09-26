"""首次启动初始化：写入默认设置与默认管理员账号。"""
from __future__ import annotations

from sqlalchemy.orm import Session

from . import settings_service
from .config import settings
from .models import User
from .security import hash_password, utcnow


def ensure_admin(db: Session) -> User:
    email = settings.bootstrap_admin_email.strip().lower()
    user = db.query(User).filter(User.email == email).one_or_none()
    if user is not None:
        return user
    user = User(
        email=email,
        full_name="系统管理员",
        password_hash=hash_password(settings.bootstrap_admin_password),
        role="admin",
        status="active",
        must_change_password=False,
        must_bind_totp=False,
        email_confirmed_at=utcnow(),
        token_version=1,
    )
    db.add(user)
    db.commit()
    return user


def bootstrap(db: Session) -> None:
    settings_service.ensure_defaults(db)
    ensure_admin(db)
