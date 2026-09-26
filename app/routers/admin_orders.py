"""管理端：订单（充值 / 新购 / 续费）与余额变动。"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy.orm import Session

from .. import services, settings_service
from ..database import get_db
from ..deps import require_admin
from ..models import Customer, Order
from ..security import money
from ..templating import redirect, render

router = APIRouter(prefix="/admin/orders", tags=["admin"])


def _to_decimal(raw: str, default: str = "0") -> Decimal:
    try:
        return money(Decimal((raw or default).strip()))
    except (InvalidOperation, AttributeError, ValueError):
        return money(default)


@router.get("")
def list_page(
    request: Request,
    db: Session = Depends(get_db),
    customer_id: str = "",
    order_type: str = "",
    q: str = "",
):
    require_admin(request, db)
    cid = int(customer_id) if customer_id.isdigit() else None
    orders = services.list_orders(db, cid, order_type, q)
    return render(
        request,
        db,
        "admin_orders.html",
        orders=orders,
        customers=services.list_customers(db),
        order_types=settings_service.get_order_types(db),
        customer_id=customer_id,
        order_type=order_type,
        q=q,
    )


@router.get("/new")
def new_page(request: Request, db: Session = Depends(get_db), customer_id: str = ""):
    require_admin(request, db)
    return render(
        request,
        db,
        "admin_order_form.html",
        order=None,
        customers=services.list_customers(db),
        order_types=settings_service.get_order_types(db),
        customer_id=customer_id,
    )


@router.post("/new")
def create_submit(
    request: Request,
    db: Session = Depends(get_db),
    customer_id: str = Form(""),
    po_number: str = Form(""),
    order_type: str = Form(""),
    product_name: str = Form(""),
    quantity: str = Form("1"),
    amount: str = Form("0"),
    remark: str = Form(""),
):
    admin = require_admin(request, db)
    if not customer_id.isdigit():
        return redirect("/admin/orders/new", "Customer not selected", "error")
    customer = db.get(Customer, int(customer_id))
    if customer is None:
        return redirect("/admin/orders/new", "Customer not found", "error")
    amt = _to_decimal(amount)
    if amt <= 0:
        return redirect("/admin/orders/new", "Amount must be greater than 0", "error")
    try:
        order = services.create_order(
            db,
            customer,
            order_type=order_type,
            amount=amt,
            po_number=po_number,
            product_name=product_name,
            quantity=_to_decimal(quantity, "1"),
            remark=remark,
            created_by=admin.email,
        )
    except ValueError as exc:
        return redirect("/admin/orders/new", str(exc), "error")
    services.audit(
        db, admin.email, "order.create", customer.assumed_name, f"{order.order_type} {amt}"
    )
    db.commit()
    return redirect("/admin/orders", "Order created", "ok")


@router.get("/{order_id}/edit")
def edit_page(request: Request, db: Session = Depends(get_db), order_id: int = 0):
    require_admin(request, db)
    order = db.get(Order, order_id)
    if order is None:
        return redirect("/admin/orders", "Order not found", "error")
    return render(
        request,
        db,
        "admin_order_form.html",
        order=order,
        customers=services.list_customers(db),
        order_types=settings_service.get_order_types(db),
        customer_id=str(order.customer_id),
    )


@router.post("/{order_id}/edit")
def edit_submit(
    request: Request,
    db: Session = Depends(get_db),
    order_id: int = 0,
    customer_id: str = Form(""),
    po_number: str = Form(""),
    order_type: str = Form(""),
    product_name: str = Form(""),
    quantity: str = Form("1"),
    amount: str = Form("0"),
    remark: str = Form(""),
):
    admin = require_admin(request, db)
    order = db.get(Order, order_id)
    if order is None:
        return redirect("/admin/orders", "Order not found", "error")
    old_customer_id = order.customer_id
    customer = db.get(Customer, int(customer_id)) if customer_id.isdigit() else None
    if customer is None:
        return redirect(f"/admin/orders/{order_id}/edit", "Customer not selected", "error")
    amt = _to_decimal(amount)
    if amt <= 0:
        return redirect(f"/admin/orders/{order_id}/edit", "Amount must be greater than 0", "error")

    order.customer_id = customer.id
    order.po_number = po_number.strip() or None
    order.order_type = order_type
    order.effect = settings_service.effect_of_type(db, order_type)
    order.product_name = product_name.strip() or None
    order.quantity = _to_decimal(quantity, "1")
    order.amount = amt
    order.remark = remark.strip() or None
    db.flush()
    services.recalc_customer_balance(db, customer)
    if old_customer_id != customer.id:
        old = db.get(Customer, old_customer_id)
        if old is not None:
            services.recalc_customer_balance(db, old)
    services.audit(db, admin.email, "order.update", f"#{order.id}", f"{order.order_type} {amt}")
    db.commit()
    return redirect("/admin/orders", "Order saved", "ok")


@router.post("/{order_id}/delete")
def delete_submit(request: Request, db: Session = Depends(get_db), order_id: int = 0):
    admin = require_admin(request, db)
    order = db.get(Order, order_id)
    if order is None:
        return redirect("/admin/orders", "Order not found", "error")
    customer_id = order.customer_id
    db.delete(order)
    db.flush()
    customer = db.get(Customer, customer_id)
    if customer is not None:
        services.recalc_customer_balance(db, customer)
    services.audit(db, admin.email, "order.delete", f"#{order_id}")
    db.commit()
    return redirect("/admin/orders", "Order deleted, balance recalculated", "ok")


@router.post("/recalculate")
def recalc_all(request: Request, db: Session = Depends(get_db)):
    """按订单流水重算全部客户余额（排错用）。"""
    admin = require_admin(request, db)
    for customer in services.list_customers(db):
        services.recalc_customer_balance(db, customer)
    services.audit(db, admin.email, "order.recalculate", "all")
    db.commit()
    return redirect("/admin/orders", "All balances recalculated", "ok")
