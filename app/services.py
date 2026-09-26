"""业务服务层：客户、用户归属、订单与余额。"""
from __future__ import annotations

from decimal import Decimal
from typing import Iterable

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import settings_service
from .models import AuditLog, Customer, CustomerDomain, Order, Ticket, User
from .security import email_domain, money, utcnow


# ------------------------------------------------------------------ 审计


def audit(db: Session, actor: str, action: str, target: str = "", detail: str = "") -> None:
    db.add(AuditLog(actor=actor, action=action, target=target, detail=detail))


# ------------------------------------------------------------------ 客户


def normalize_domain(raw: str) -> str:
    value = raw.strip().lower()
    for prefix in ("http://", "https://"):
        if value.startswith(prefix):
            value = value[len(prefix) :]
    return value.strip().strip("/").split("/", 1)[0]


def find_customer_by_domain(db: Session, email: str) -> Customer | None:
    domain = email_domain(email)
    if not domain:
        return None
    row = db.execute(
        select(CustomerDomain).where(CustomerDomain.domain == domain)
    ).scalar_one_or_none()
    return row.customer if row else None


def domain_owner(db: Session, domain: str) -> Customer | None:
    row = db.execute(
        select(CustomerDomain).where(CustomerDomain.domain == normalize_domain(domain))
    ).scalar_one_or_none()
    return row.customer if row else None


def create_customer(
    db: Session,
    assumed_name: str,
    legal_name: str = "",
    registration_address: str = "",
    telephone: str = "",
    domains: Iterable[str] = (),
    note: str = "",
    org_contact_id: int | None = None,
    tech_contact_id: int | None = None,
) -> Customer:
    customer = Customer(
        assumed_name=assumed_name.strip(),
        legal_name=(legal_name or "").strip() or None,
        registration_address=(registration_address or "").strip() or None,
        telephone=(telephone or "").strip() or None,
        note=(note or "").strip() or None,
        org_contact_id=org_contact_id,
        tech_contact_id=tech_contact_id,
        balance=Decimal("0.00"),
        is_active=True,
    )
    db.add(customer)
    db.flush()
    for raw in domains:
        d = normalize_domain(raw)
        if d:
            db.add(CustomerDomain(customer_id=customer.id, domain=d))
    db.flush()
    return customer


def sync_customer_domains(db: Session, customer: Customer, domains: Iterable[str]) -> None:
    wanted = {normalize_domain(d) for d in domains if normalize_domain(d)}
    current = {d.domain: d for d in customer.domains}
    for domain in list(current):
        if domain not in wanted:
            db.delete(current[domain])
    for domain in wanted:
        if domain not in current:
            db.add(CustomerDomain(customer_id=customer.id, domain=domain))
    db.flush()


def list_customers(db: Session, keyword: str = "") -> list[Customer]:
    stmt = select(Customer).order_by(Customer.assumed_name)
    if keyword:
        like = f"%{keyword.strip()}%"
        stmt = stmt.where(
            Customer.assumed_name.like(like) | Customer.legal_name.like(like)
        )
    return list(db.execute(stmt).scalars().unique())


def customer_balance(db: Session, customer_id: int) -> Decimal:
    value = db.execute(
        select(func.coalesce(func.sum(Order.amount), 0)).where(Order.customer_id == customer_id)
    ).scalar_one()
    return money(value)


def recalc_customer_balance(db: Session, customer: Customer) -> Decimal:
    """按订单流水重算余额（credit 加、debit 减），返回新余额。"""
    rows = db.execute(
        select(Order).where(Order.customer_id == customer.id).order_by(Order.id)
    ).scalars()
    running = Decimal("0.00")
    for order in rows:
        delta = order.amount if order.effect == "credit" else -order.amount
        running = money(running + delta)
        if order.balance_after != running:
            order.balance_after = running
    customer.balance = running
    db.flush()
    return running


# ------------------------------------------------------------------ 订单


def create_order(
    db: Session,
    customer: Customer,
    order_type: str,
    amount: Decimal | float | str,
    po_number: str = "",
    product_name: str = "",
    quantity: Decimal | float | str = 1,
    remark: str = "",
    created_by: str = "",
) -> Order:
    effect = settings_service.effect_of_type(db, order_type)
    amt = money(amount)
    if amt <= 0:
        raise ValueError("金额必须大于 0")
    order = Order(
        customer_id=customer.id,
        po_number=(po_number or "").strip() or None,
        order_type=order_type,
        effect=effect,
        product_name=(product_name or "").strip() or None,
        quantity=money(quantity),
        amount=amt,
        remark=(remark or "").strip() or None,
        created_by=created_by,
        created_at=utcnow(),
    )
    db.add(order)
    db.flush()
    order.balance_after = recalc_customer_balance(db, customer)
    return order


def customer_orders(db: Session, customer_id: int, limit: int = 500) -> list[Order]:
    return list(
        db.execute(
            select(Order)
            .where(Order.customer_id == customer_id)
            .order_by(Order.created_at.desc(), Order.id.desc())
            .limit(limit)
        )
        .scalars()
        .unique()
    )


def list_orders(
    db: Session, customer_id: int | None = None, order_type: str = "", keyword: str = ""
) -> list[Order]:
    stmt = select(Order).order_by(Order.created_at.desc(), Order.id.desc())
    if customer_id:
        stmt = stmt.where(Order.customer_id == customer_id)
    if order_type:
        stmt = stmt.where(Order.order_type == order_type)
    if keyword:
        like = f"%{keyword.strip()}%"
        stmt = stmt.where(
            Order.po_number.like(like)
            | Order.product_name.like(like)
            | Order.remark.like(like)
        )
    return list(db.execute(stmt.limit(500)).scalars().unique())


# ------------------------------------------------------------------ 用户


def domain_candidates(db: Session, domain: str) -> list[User]:
    """同域名下已激活的用户，用于选择「首选联系人」/展示同公司成员。"""
    return list(
        db.execute(
            select(User)
            .where(User.email.like(f"%@{normalize_domain(domain)}"))
            .order_by(User.email)
        )
        .scalars()
    )


def company_users(db: Session, customer: Customer) -> list[User]:
    return list(
        db.execute(
            select(User).where(User.customer_id == customer.id).order_by(User.email)
        ).scalars()
    )


def attach_user_to_customer_by_domain(db: Session, user: User) -> Customer | None:
    customer = find_customer_by_domain(db, user.email)
    if customer is not None:
        user.customer_id = customer.id
    return customer


# ------------------------------------------------------------------ 工单


def next_ticket_number(db: Session) -> str:
    """工单号 TK-YYYYMMDD-NNNN（按天递增）。"""
    today = utcnow().strftime("%Y%m%d")
    prefix = f"TK-{today}-"
    last = db.execute(
        select(Ticket.number).where(Ticket.number.like(prefix + "%")).order_by(Ticket.number.desc())
    ).scalar_one_or_none()
    seq = int(last.rsplit("-", 1)[1]) + 1 if last else 1
    return f"{prefix}{seq:04d}"


def create_ticket(
    db: Session, customer: Customer, domain: str, ticket_type: str, user: User
) -> Ticket:
    ticket = Ticket(
        number=next_ticket_number(db),
        customer_id=customer.id,
        domain=domain,
        ticket_type=ticket_type,
        status="requested",
        requested_by_id=user.id,
        created_at=utcnow(),
    )
    db.add(ticket)
    db.flush()
    return ticket


def list_tickets(
    db: Session,
    keyword: str = "",
    ticket_type: str = "",
    status: str = "",
    date_from: str = "",
    date_to: str = "",
    assumed: str = "",
) -> list[Ticket]:
    stmt = select(Ticket).order_by(Ticket.created_at.desc(), Ticket.id.desc())
    if keyword:
        like = f"%{keyword.strip()}%"
        stmt = stmt.where(Ticket.number.like(like) | Ticket.domain.like(like))
    if ticket_type:
        stmt = stmt.where(Ticket.ticket_type == ticket_type)
    if status:
        stmt = stmt.where(Ticket.status == status)
    if date_from:
        stmt = stmt.where(Ticket.created_at >= f"{date_from} 00:00:00")
    if date_to:
        stmt = stmt.where(Ticket.created_at <= f"{date_to} 23:59:59")
    if assumed:
        like = f"%{assumed.strip()}%"
        stmt = stmt.join(Customer, Ticket.customer_id == Customer.id).where(
            Customer.assumed_name.like(like)
        )
    return list(db.execute(stmt.limit(500)).scalars().unique())


def verify_ticket(db: Session, ticket: Ticket, admin_email: str) -> None:
    ticket.status = "verified"
    ticket.verified_by = admin_email
    ticket.verified_at = utcnow()
    row = db.execute(
        select(CustomerDomain).where(
            CustomerDomain.customer_id == ticket.customer_id,
            CustomerDomain.domain == ticket.domain,
        )
    ).scalar_one_or_none()
    if row is not None:
        row.ov_status = "verified"
        row.ov_verified_at = ticket.verified_at
    db.flush()
