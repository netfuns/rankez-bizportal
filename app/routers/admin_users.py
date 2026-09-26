"""管理端：用户增删改查、批量操作、TOTP / 密码重置。"""
from __future__ import annotations

import secrets
from datetime import timedelta

from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .. import mailer, services, settings_service
from ..database import get_db
from ..deps import find_user_by_email, require_admin
from ..models import EmailToken, User
from ..security import (
    generate_temp_password,
    hash_password,
    is_valid_email,
    normalize_email,
    utcnow,
)
from ..templating import redirect, render

router = APIRouter(prefix="/admin/users", tags=["admin"])


def _mk_user(
    db: Session,
    email: str,
    full_name: str = "",
    phone: str = "",
    customer_id: int | None = None,
    must_change_password: bool = True,
    must_bind_totp: bool = True,
) -> tuple[User, str]:
    """创建用户并返回 (用户, 临时密码)。"""
    temp = generate_temp_password()
    user = User(
        email=normalize_email(email),
        full_name=full_name.strip() or None,
        phone=phone.strip() or None,
        password_hash=hash_password(temp),
        role="user",
        status="pending",
        customer_id=customer_id,
        must_change_password=must_change_password,
        must_bind_totp=must_bind_totp,
        token_version=1,
    )
    db.add(user)
    db.flush()
    return user, temp


def _send_confirm(request: Request, db: Session, user: User, temp_password: str) -> None:
    token = EmailToken(
        user_id=user.id,
        token=secrets.token_urlsafe(32),
        purpose="confirm_email",
        expires_at=utcnow() + timedelta(hours=24),
    )
    db.add(token)
    db.flush()
    base = str(request.base_url).rstrip("/")
    site_name = settings_service.get(db, "site_name")
    mailer.send_mail(
        db,
        user.email,
        f"[{site_name}] 请确认您的账号邮箱",
        mailer.render_confirm_mail(
            site_name,
            user.email,
            temp_password,
            f"{base}/confirm-email?token={token.token}",
        ),
    )


@router.get("")
def list_page(
    request: Request,
    db: Session = Depends(get_db),
    q: str = "",
    status: str = "",
    customer_id: str = "",
):
    require_admin(request, db)
    stmt = select(User).order_by(User.id.desc())
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(User.email.like(like), User.full_name.like(like)))
    if status:
        stmt = stmt.where(User.status == status)
    if customer_id.isdigit():
        stmt = stmt.where(User.customer_id == int(customer_id))
    users = list(db.execute(stmt.limit(500)).scalars().unique())
    customers = services.list_customers(db)
    return render(
        request,
        db,
        "admin_users.html",
        users=users,
        customers=customers,
        q=q,
        status=status,
        customer_id=customer_id,
    )


@router.get("/new")
def new_page(request: Request, db: Session = Depends(get_db)):
    require_admin(request, db)
    return render(request, db, "admin_user_form.html", user=None, customers=services.list_customers(db))


@router.post("/new")
def create_submit(
    request: Request,
    db: Session = Depends(get_db),
    email: str = Form(""),
    full_name: str = Form(""),
    phone: str = Form(""),
    customer_id: str = Form(""),
    must_change_password: str = Form(""),
    must_bind_totp: str = Form(""),
    send_mail: str = Form(""),
    skip_confirm: str = Form(""),
):
    admin = require_admin(request, db)
    email = normalize_email(email)
    if not is_valid_email(email):
        return redirect("/admin/users/new", "Invalid email address", "error")
    if find_user_by_email(db, email) is not None:
        return redirect("/admin/users/new", "This email is already registered", "error")

    cid = int(customer_id) if customer_id.isdigit() else None
    if cid is None:
        matched = services.find_customer_by_domain(db, email)
        cid = matched.id if matched else None
    user, temp = _mk_user(
        db,
        email,
        full_name,
        phone,
        cid,
        must_change_password=must_change_password == "on",
        must_bind_totp=must_bind_totp == "on",
    )
    if skip_confirm == "on":
        user.status = "active"
        user.email_confirmed_at = utcnow()
        note = f"用户 {email} 已创建（无需邮件确认），临时密码：{temp}"
    else:
        _send_confirm(request, db, user, temp)
        note = f"用户 {email} 已创建，确认邮件已发送"
    services.audit(db, admin.email, "user.create", email)
    db.commit()
    return redirect("/admin/users", note, "ok")


@router.post("/bulk")
def bulk_create(
    request: Request,
    db: Session = Depends(get_db),
    batch: str = Form(""),
    must_change_password: str = Form(""),
    must_bind_totp: str = Form(""),
    send_mail: str = Form(""),
    skip_confirm: str = Form(""),
):
    admin = require_admin(request, db)
    lines = [ln.strip() for ln in (batch or "").splitlines() if ln.strip()]
    if not lines:
        return redirect("/admin/users/new", "Batch content is empty", "error")
    created, skipped = 0, []
    for line in lines:
        parts = [p.strip() for p in line.split(",")]
        email = normalize_email(parts[0])
        if not is_valid_email(email) or find_user_by_email(db, email) is not None:
            skipped.append(email)
            continue
        full_name = parts[1] if len(parts) > 1 else ""
        phone = parts[2] if len(parts) > 2 else ""
        matched = services.find_customer_by_domain(db, email)
        user, temp = _mk_user(
            db,
            email,
            full_name,
            phone,
            matched.id if matched else None,
            must_change_password=must_change_password == "on",
            must_bind_totp=must_bind_totp == "on",
        )
        if skip_confirm == "on":
            user.status = "active"
            user.email_confirmed_at = utcnow()
        elif send_mail == "on":
            _send_confirm(request, db, user, temp)
        created += 1
    services.audit(db, admin.email, "user.bulk_create", f"{created} 个用户")
    db.commit()
    msg = f"批量创建完成：成功 {created} 个" + (f"，跳过 {len(skipped)} 个" if skipped else "")
    return redirect("/admin/users", msg, "ok")


@router.post("/bulk-delete")
def bulk_delete(request: Request, db: Session = Depends(get_db), ids: str = Form("")):
    admin = require_admin(request, db)
    id_list = [int(x) for x in ids.split(",") if x.strip().isdigit()]
    removed = 0
    for uid in id_list:
        user = db.get(User, uid)
        if user is None or user.role == "admin":
            continue
        db.delete(user)
        removed += 1
    services.audit(db, admin.email, "user.bulk_delete", f"{removed} 个用户")
    db.commit()
    return redirect("/admin/users", f"Users deleted: {removed}", "ok")


@router.get("/{user_id}/edit")
def edit_page(request: Request, db: Session = Depends(get_db), user_id: int = 0):
    require_admin(request, db)
    user = db.get(User, user_id)
    if user is None:
        return redirect("/admin/users", "User not found", "error")
    return render(
        request, db, "admin_user_form.html", user=user, customers=services.list_customers(db)
    )


@router.post("/{user_id}/edit")
def edit_submit(
    request: Request,
    db: Session = Depends(get_db),
    user_id: int = 0,
    email: str = Form(""),
    full_name: str = Form(""),
    phone: str = Form(""),
    customer_id: str = Form(""),
    role: str = Form("user"),
    status: str = Form("active"),
    must_change_password: str = Form(""),
    must_bind_totp: str = Form(""),
):
    admin = require_admin(request, db)
    user = db.get(User, user_id)
    if user is None:
        return redirect("/admin/users", "User not found", "error")
    email = normalize_email(email)
    if email != user.email:
        if not is_valid_email(email) and user.role != "admin":
            return redirect(f"/admin/users/{user_id}/edit", "Invalid email address", "error")
        other = find_user_by_email(db, email)
        if other is not None and other.id != user.id:
            return redirect(f"/admin/users/{user_id}/edit", "This email is already registered", "error")
        user.email = email
    user.full_name = full_name.strip() or None
    user.phone = phone.strip() or None
    user.customer_id = int(customer_id) if customer_id.isdigit() else None
    user.status = status
    if user.role != "admin" or role == "admin":
        user.role = role if role in {"admin", "user"} else "user"
    user.must_change_password = must_change_password == "on"
    user.must_bind_totp = must_bind_totp == "on"
    services.audit(db, admin.email, "user.update", user.email)
    db.commit()
    return redirect("/admin/users", f"User {user.email} saved", "ok")


@router.post("/{user_id}/delete")
def delete_submit(request: Request, db: Session = Depends(get_db), user_id: int = 0):
    admin = require_admin(request, db)
    user = db.get(User, user_id)
    if user is None:
        return redirect("/admin/users", "User not found", "error")
    if user.role == "admin":
        return redirect("/admin/users", "Cannot delete an admin account", "error")
    email = user.email
    db.delete(user)
    services.audit(db, admin.email, "user.delete", email)
    db.commit()
    return redirect("/admin/users", f"User {email} deleted", "ok")


@router.post("/{user_id}/reset-totp")
def reset_totp(request: Request, db: Session = Depends(get_db), user_id: int = 0):
    admin = require_admin(request, db)
    user = db.get(User, user_id)
    if user is None:
        return redirect("/admin/users", "User not found", "error")
    user.totp_secret = None
    user.totp_bound_at = None
    user.must_bind_totp = True
    user.token_version = int(user.token_version or 1) + 1
    services.audit(db, admin.email, "user.reset_totp", user.email)
    db.commit()
    return redirect("/admin/users", "TOTP force reset done. The user must rebind at next login.", "ok")


@router.post("/{user_id}/reset-password")
def reset_password(request: Request, db: Session = Depends(get_db), user_id: int = 0):
    admin = require_admin(request, db)
    user = db.get(User, user_id)
    if user is None:
        return redirect("/admin/users", "User not found", "error")
    temp = generate_temp_password()
    user.password_hash = hash_password(temp)
    user.must_change_password = True
    user.token_version = int(user.token_version or 1) + 1
    services.audit(db, admin.email, "user.reset_password", user.email)
    db.commit()
    site_name = settings_service.get(db, "site_name")
    mailer.send_mail(
        db,
        user.email,
        f"[{site_name}] 密码已重置",
        f"管理员已重置您的密码。\n\n临时密码：{temp}\n\n登录后系统会要求您修改密码。\n",
    )
    return redirect(
        "/admin/users", f"{user.email} 的密码已重置，临时密码：{temp}（已尝试发送邮件）", "ok"
    )


@router.post("/{user_id}/confirm")
def manual_confirm(request: Request, db: Session = Depends(get_db), user_id: int = 0):
    """SMTP 未配置时的兜底：管理员直接确认邮箱。"""
    admin = require_admin(request, db)
    user = db.get(User, user_id)
    if user is None:
        return redirect("/admin/users", "User not found", "error")
    user.status = "active"
    user.email_confirmed_at = utcnow()
    services.audit(db, admin.email, "user.confirm", user.email)
    db.commit()
    return redirect("/admin/users", "User marked as confirmed", "ok")
