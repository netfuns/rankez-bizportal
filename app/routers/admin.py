"""管理端入口与概览。"""
from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import require_admin
from ..models import Customer, CustomerDomain, MailOutbox, Order, User
from ..templating import render

router = APIRouter(tags=["admin"])

from . import admin_customers, admin_mail, admin_orders, admin_settings, admin_tickets, admin_users  # noqa: E402

router.include_router(admin_customers.router)
router.include_router(admin_users.router)
router.include_router(admin_orders.router)
router.include_router(admin_tickets.router)
router.include_router(admin_settings.router)
router.include_router(admin_mail.router)


@router.get("/admin")
def admin_home(request: Request, db: Session = Depends(get_db)):
    admin = require_admin(request, db)
    customer_count = db.scalar(select(func.count(Customer.id))) or 0
    user_count = db.scalar(select(func.count(User.id)).where(User.role == "user")) or 0
    pending_count = (
        db.scalar(select(func.count(User.id)).where(User.status == "pending")) or 0
    )
    order_count = db.scalar(select(func.count(Order.id))) or 0
    domain_count = db.scalar(select(func.count(CustomerDomain.id))) or 0
    total_balance = db.scalar(select(func.coalesce(func.sum(Customer.balance), 0))) or Decimal("0")
    recent_orders = list(
        db.execute(select(Order).order_by(Order.id.desc()).limit(10)).scalars().unique()
    )
    pending_mail = db.scalar(
        select(func.count(MailOutbox.id)).where(MailOutbox.status == "failed")
    ) or 0
    return render(
        request,
        db,
        "admin_home.html",
        stats={
            "customers": customer_count,
            "users": user_count,
            "pending": pending_count,
            "orders": order_count,
            "domains": domain_count,
            "balance": total_balance,
            "pending_mail": pending_mail,
        },
        recent_orders=recent_orders,
    )
