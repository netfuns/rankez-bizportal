"""认证路由：登录 / 注册 / 邮箱确认 / TOTP / 改密。"""
from __future__ import annotations

import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from .. import mailer, services, settings_service
from ..config import settings
from ..database import get_db
from ..deps import (
    PREAUTH_COOKIE,
    SESSION_COOKIE,
    find_user_by_email,
    is_locked,
    live_user,
    register_login_failure,
    reset_login_failures,
)
from ..models import EmailToken, User
from ..security import (
    create_preauth_token,
    create_session_token,
    decode_token,
    email_domain,
    generate_temp_password,
    hash_password,
    is_valid_email,
    new_totp_secret,
    normalize_email,
    totp_provisioning_uri,
    totp_qr_data_uri,
    utcnow,
    validate_password,
    verify_password,
    verify_totp,
)
from ..templating import redirect, render

router = APIRouter(tags=["auth"])


def _preauth_user(request: Request, db: Session) -> User | None:
    token = request.cookies.get(PREAUTH_COOKIE)
    if not token:
        return None
    payload = decode_token(token)
    if not payload or payload.get("typ") != "preauth":
        return None
    user = db.get(User, int(payload["sub"]))
    return user


def _needs_binding(user: User, db: Session) -> bool:
    """是否需要（重新）绑定 TOTP：显式标记 > 已绑定 > 全局强制开关。"""
    if user.must_bind_totp:
        return True
    if user.totp_secret:
        return False
    return settings_service.get_bool(db, "force_totp")


def _issue_session(response: RedirectResponse, db: Session, user: User) -> RedirectResponse:
    ttl = settings_service.get_int(db, "session_timeout_minutes") or 120
    token = create_session_token(user.id, user.role, user.token_version, ttl)
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=ttl * 60,
        httponly=True,
        samesite="lax",
        path="/",
    )
    response.delete_cookie(PREAUTH_COOKIE, path="/")
    return response


# ------------------------------------------------------------------ 登录


@router.get("/login")
def login_page(request: Request, db: Session = Depends(get_db)):
    if getattr(request.state, "user", None):
        return RedirectResponse("/", status_code=303)
    return render(
        request,
        db,
        "login.html",
        registration_enabled=settings_service.get_bool(db, "registration_enabled"),
    )


@router.post("/login")
def login_submit(
    request: Request,
    db: Session = Depends(get_db),
    email: str = Form(""),
    password: str = Form(""),
):
    email = normalize_email(email)
    user = find_user_by_email(db, email)
    if user is None or not verify_password(password, user.password_hash):
        if user is not None:
            register_login_failure(db, user)
            if is_locked(user):
                return redirect("/login", "Too many attempts, please try again later", "error")
        return redirect("/login", "Email or password is incorrect", "error")

    if user.status == "disabled":
        return redirect("/login", "Account is disabled", "error")
    if user.status == "pending":
        return redirect(
            "/login", "Please complete email confirmation first", "error"
        )
    if is_locked(user):
        return redirect("/login", "Account is locked, try again later", "error")

    reset_login_failures(db, user)
    user.last_login_at = utcnow()
    db.commit()

    need_totp = _needs_binding(user, db) or bool(user.totp_secret)
    if not need_totp:
        dest = "/admin" if user.role == "admin" else "/"
        return _issue_session(RedirectResponse(dest, status_code=303), db, user)

    response = RedirectResponse("/login/totp", status_code=303)
    response.set_cookie(
        PREAUTH_COOKIE, create_preauth_token(user.id), max_age=600, httponly=True, samesite="lax"
    )
    return response


@router.get("/login/totp")
def login_totp_page(request: Request, db: Session = Depends(get_db)):
    user = _preauth_user(request, db)
    if user is None:
        return redirect("/login", "Please log in first", "error")
    binding = _needs_binding(user, db)
    ctx: dict = {"binding": binding, "email": user.email}
    if binding:
        secret = new_totp_secret()
        ctx["secret"] = secret
        ctx["qr"] = totp_qr_data_uri(
            totp_provisioning_uri(secret, user.email, settings_service.get(db, "site_name"))
        )
    return render(request, db, "login_totp.html", **ctx)


@router.post("/login/totp")
def login_totp_submit(
    request: Request,
    db: Session = Depends(get_db),
    code: str = Form(""),
    secret: str = Form(""),
):
    user = _preauth_user(request, db)
    if user is None:
        return redirect("/login", "Please log in first", "error")
    binding = _needs_binding(user, db)
    target_secret = secret if binding else (user.totp_secret or "")
    if not verify_totp(target_secret, code):
        return redirect("/login/totp", "TOTP code is incorrect", "error")

    if binding:
        user.totp_secret = target_secret
        user.totp_bound_at = utcnow()
        user.must_bind_totp = False
        db.commit()

    dest = "/change-password" if user.must_change_password else "/"
    response = _issue_session(RedirectResponse(dest, status_code=303), db, user)
    return response


# ------------------------------------------------------------------ 注册


@router.get("/register")
def register_page(request: Request, db: Session = Depends(get_db)):
    if not settings_service.get_bool(db, "registration_enabled"):
        return redirect("/login", "Self-service registration is disabled", "error")
    return render(request, db, "register.html")


@router.post("/register")
def register_submit(
    request: Request,
    db: Session = Depends(get_db),
    email: str = Form(""),
    full_name: str = Form(""),
    phone: str = Form(""),
):
    if not settings_service.get_bool(db, "registration_enabled"):
        return redirect("/register", "Self-service registration is disabled", "error")
    email = normalize_email(email)
    if not is_valid_email(email):
        return redirect("/register", "Invalid email address", "error")
    if find_user_by_email(db, email) is not None:
        return redirect("/register", "This email is already registered", "error")

    customer = services.find_customer_by_domain(db, email)
    if customer is None:
        return redirect(
            "/register",
            "This email domain is not in the whitelist",
            "error",
        )

    temp_password = generate_temp_password()
    user = User(
        email=email,
        full_name=full_name.strip() or None,
        phone=phone.strip() or None,
        password_hash=hash_password(temp_password),
        role="user",
        status="pending",
        customer_id=customer.id,
        must_change_password=True,
        must_bind_totp=True,
        token_version=1,
    )
    db.add(user)
    db.flush()

    token = EmailToken(
        user_id=user.id,
        token=secrets.token_urlsafe(32),
        purpose="confirm_email",
        expires_at=utcnow() + timedelta(hours=24),
    )
    db.add(token)
    db.flush()

    base = str(request.base_url).rstrip("/")

    confirm_url = f"{base}/confirm-email?token={token.token}"
    site_name = settings_service.get(db, "site_name")
    mailer.send_mail(
        db,
        email,
        f"[{site_name}] 请确认您的注册邮箱",
        mailer.render_confirm_mail(site_name, email, temp_password, confirm_url),
    )
    db.commit()
    return redirect(
        "/login",
        "Registration received. Please check the confirmation email.",
        "ok",
    )


@router.get("/confirm-email")
def confirm_email(request: Request, db: Session = Depends(get_db), token: str = ""):
    row = db.query(EmailToken).filter(EmailToken.token == token).one_or_none()
    if row is None or row.used_at is not None or row.expires_at < utcnow():
        return redirect("/login", "Invalid or expired confirmation link", "error")
    user = db.get(User, row.user_id)
    if user is None:
        return redirect("/login", "Invalid or expired confirmation link", "error")
    row.used_at = utcnow()
    if user.status == "pending":
        user.status = "active"
    user.email_confirmed_at = utcnow()
    db.commit()
    return redirect("/login", "Email confirmed. Please log in with the temporary password from the email.", "ok")


# ------------------------------------------------------------------ 改密


@router.get("/change-password")
def change_password_page(request: Request, db: Session = Depends(get_db)):
    user = getattr(request.state, "user", None)
    if user is None:
        return redirect("/login", "Please log in first", "error")
    return render(request, db, "change_password.html", forced=user.must_change_password)


@router.post("/change-password")
def change_password_submit(
    request: Request,
    db: Session = Depends(get_db),
    current_password: str = Form(""),
    new_password: str = Form(""),
    confirm_password: str = Form(""),
):
    user = live_user(request, db)
    if user is None:
        return redirect("/login", "Please log in first", "error")
    if not verify_password(current_password, user.password_hash):
        return redirect("/change-password", "Current password is incorrect", "error")
    if new_password != confirm_password:
        return redirect("/change-password", "The two passwords do not match", "error")
    problem = validate_password(new_password)
    if problem:
        return redirect("/change-password", "New password does not meet the requirements", "error")
    if verify_password(new_password, user.password_hash):
        return redirect("/change-password", "New password must differ from the current password", "error")

    user.password_hash = hash_password(new_password)
    user.must_change_password = False
    user.token_version = int(user.token_version or 1) + 1
    db.commit()

    ttl = settings_service.get_int(db, "session_timeout_minutes") or 120
    response = RedirectResponse("/", status_code=303)
    response.set_cookie(
        SESSION_COOKIE,
        create_session_token(user.id, user.role, user.token_version, ttl),
        max_age=ttl * 60,
        httponly=True,
        samesite="lax",
    )
    return response


# ------------------------------------------------------------------ TOTP 自助绑定 / 重置


@router.get("/setup-totp")
def setup_totp_page(request: Request, db: Session = Depends(get_db)):
    user = getattr(request.state, "user", None)
    if user is None:
        return redirect("/login", "Please log in first", "error")
    secret = request.query_params.get("secret") or new_totp_secret()
    uri = totp_provisioning_uri(secret, user.email, settings_service.get(db, "site_name"))
    return render(
        request,
        db,
        "setup_totp.html",
        secret=secret,
        qr=totp_qr_data_uri(uri),
        has_totp=bool(user.totp_secret),
    )


@router.post("/setup-totp")
def setup_totp_submit(
    request: Request,
    db: Session = Depends(get_db),
    secret: str = Form(""),
    code: str = Form(""),
    current_code: str = Form(""),
):
    user = live_user(request, db)
    if user is None:
        return redirect("/login", "Please log in first", "error")
    if user.totp_secret and user.must_bind_totp is False:
        # 已绑定状态下重新绑定，需要先验证当前 TOTP
        if not verify_totp(user.totp_secret or "", current_code):
            return redirect("/setup-totp", "TOTP code is incorrect", "error")
    if not secret:
        return redirect("/setup-totp", "Missing secret, please refresh and retry", "error")
    if not verify_totp(secret, code):
        return redirect(
            f"/setup-totp?secret={secret}", "TOTP code is incorrect", "error"
        )
    user.totp_secret = secret
    user.totp_bound_at = utcnow()
    user.must_bind_totp = False
    db.commit()
    return redirect("/profile", "TOTP bound", "ok")


@router.post("/profile/totp/reset")
def reset_totp(request: Request, db: Session = Depends(get_db), code: str = Form("")):
    user = live_user(request, db)
    if user is None:
        return redirect("/login", "Please log in first", "error")
    if not user.totp_secret:
        return redirect("/setup-totp", "TOTP is not bound yet", "error")
    if not verify_totp(user.totp_secret, code):
        return redirect("/profile", "TOTP code is incorrect", "error")
    user.totp_secret = None
    user.totp_bound_at = None
    user.must_bind_totp = True
    db.commit()
    return redirect("/setup-totp", "Previous binding removed. Please bind a new authenticator.", "ok")


# ------------------------------------------------------------------ 退出


@router.get("/logout")
def logout():
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(PREAUTH_COOKIE, path="/")
    return response


