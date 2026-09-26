"""请求上下文依赖：当前用户、管理员校验、模板渲染助手。"""
from __future__ import annotations

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import settings_service
from .database import get_db
from .models import User
from .security import decode_token, utcnow

SESSION_COOKIE = "bp_session"
PREAUTH_COOKIE = "bp_preauth"
MAX_FAILED_LOGINS = 5
LOCK_MINUTES = 15


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    payload = decode_token(token)
    if not payload or payload.get("typ") != "session":
        return None
    try:
        user_id = int(payload.get("sub"))
    except (TypeError, ValueError):
        return None
    user = db.get(User, user_id)
    if user is None or user.status != "active":
        return None
    if int(payload.get("tv", 0)) != int(user.token_version):
        return None
    return user


def live_user(request: Request, db: Session) -> User | None:
    """取回绑定到当前会话的用户对象。

    ⚠️ 中间件里的 `request.state.user` 来自一个已关闭的会话，直接改它的属性
    不会进入任何 flush，写库会静默失败。凡是要修改用户的地方都用这个函数。
    """
    user = getattr(request.state, "user", None)
    if user is None:
        return None
    return db.get(User, user.id)


def require_user(request: Request, db: Session = Depends(get_db)) -> User:
    user = get_current_user(request, db)
    if user is None:
        raise HTTPException(status_code=303, headers={"Location": "/login"})
    return user


def require_admin(request: Request, db: Session = Depends(get_db)) -> User:
    user = require_user(request, db)
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin permission required")
    return user


def is_locked(user: User) -> bool:
    return bool(user.locked_until and user.locked_until > utcnow())


def register_login_failure(db: Session, user: User) -> None:
    user.failed_logins = int(user.failed_logins or 0) + 1
    if user.failed_logins >= MAX_FAILED_LOGINS:
        from datetime import timedelta

        user.locked_until = utcnow() + timedelta(minutes=LOCK_MINUTES)
        user.failed_logins = 0
    db.commit()


def reset_login_failures(db: Session, user: User) -> None:
    user.failed_logins = 0
    user.locked_until = None
    db.commit()


def find_user_by_email(db: Session, email: str) -> User | None:
    return db.execute(select(User).where(User.email == email.strip().lower())).scalar_one_or_none()
