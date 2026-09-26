"""客户自助端：余额 / 消费记录 / 个人资料 / 公司信息与 OV 工单。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from .. import i18n, services
from ..database import get_db
from ..deps import live_user
from ..models import Customer, User
from ..security import validate_password, verify_password, hash_password
from ..templating import redirect, render

router = APIRouter(tags=["portal"])


@router.get("/")
def dashboard(request: Request, db: Session = Depends(get_db)):
    user: User | None = getattr(request.state, "user", None)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    if user.role == "admin":
        return RedirectResponse("/admin", status_code=303)

    customer: Customer | None = user.customer
    orders = services.customer_orders(db, customer.id) if customer else []
    members = services.company_users(db, customer) if customer else []
    can_request_ov = bool(
        customer and user.id in {customer.org_contact_id, customer.tech_contact_id}
    )
    contact_names: dict[int, str] = {}
    if customer:
        for m in members:
            contact_names[m.id] = f"{m.display_name}（{m.email}）"
    return render(
        request,
        db,
        "portal_dashboard.html",
        customer=customer,
        orders=orders,
        members=members,
        balance=customer.balance if customer else 0,
        can_request_ov=can_request_ov,
        contact_names=contact_names,
    )


@router.post("/company/ov-request")
def ov_request(
    request: Request,
    db: Session = Depends(get_db),
    domain: str = Form(""),
):
    """提交「验证公司」申请：创建工单并把域名置为验证中。"""
    user: User | None = live_user(request, db)
    if user is None:
        return redirect("/login", "Please log in first", "error")
    customer: Customer | None = user.customer
    if customer is None:
        return redirect("/", "Customer not found", "error")
    if user.id not in {customer.org_contact_id, customer.tech_contact_id}:
        return redirect("/", "Only organization or technical contacts can submit verification requests", "error")
    domain = services.normalize_domain(domain)
    target = next((d for d in customer.domains if d.domain == domain), None)
    if target is None:
        return redirect("/", "Domain not found in your company", "error")
    if target.ov_status == "verified":
        return redirect("/", "Already verified", "error")
    if target.ov_status == "verifying":
        return redirect("/", "A verification request is already in progress for this domain", "error")

    types = services.settings_service.get_ticket_types(db) if hasattr(services, "settings_service") else []
    from .. import settings_service

    types = settings_service.get_ticket_types(db)
    services.create_ticket(db, customer, domain, types[0], user)
    target.ov_status = "verifying"
    services.audit(db, user.email, "ticket.create", domain, "OV request")
    db.commit()
    return redirect("/", "Verification request submitted", "ok")


@router.get("/profile")
def profile_page(request: Request, db: Session = Depends(get_db)):
    user: User | None = getattr(request.state, "user", None)
    if user is None:
        return RedirectResponse("/login", status_code=303)
    return render(request, db, "portal_profile.html")


@router.post("/profile")
def profile_update(
    request: Request,
    db: Session = Depends(get_db),
    full_name: str = Form(""),
    phone: str = Form(""),
):
    user: User | None = live_user(request, db)
    if user is None:
        return redirect("/login", "Please log in first", "error")
    user.full_name = full_name.strip() or None
    user.phone = phone.strip() or None
    db.commit()
    return redirect("/profile", "Profile updated", "ok")


@router.post("/profile/password")
def profile_password(
    request: Request,
    db: Session = Depends(get_db),
    current_password: str = Form(""),
    new_password: str = Form(""),
    confirm_password: str = Form(""),
):
    user: User | None = live_user(request, db)
    if user is None:
        return redirect("/login", "Please log in first", "error")
    if not verify_password(current_password, user.password_hash):
        return redirect("/profile", "Current password is incorrect", "error")
    if new_password != confirm_password:
        return redirect("/profile", "The two passwords do not match", "error")
    problem = validate_password(new_password)
    if problem:
        return redirect("/profile", "New password does not meet the requirements", "error")
    user.password_hash = hash_password(new_password)
    user.must_change_password = False
    db.commit()
    return redirect("/profile", "Password updated", "ok")


@router.get("/consumption")
def consumption(request: Request, db: Session = Depends(get_db)):
    """消费记录（与仪表盘同源，提供独立分页视图）。"""
    return dashboard(request, db)
