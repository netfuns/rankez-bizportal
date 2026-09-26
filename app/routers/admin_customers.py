"""管理端：客户（公司）与域名白名单。"""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, Form, Request
from sqlalchemy.orm import Session

from .. import services
from ..database import get_db
from ..deps import require_admin
from ..models import Customer, User
from ..templating import redirect, render

router = APIRouter(prefix="/admin/customers", tags=["admin"])

_SPLIT_RE = re.compile(r"[\s,;]+")


def _parse_domains(raw: str) -> list[str]:
    return [d for d in (services.normalize_domain(x) for x in _SPLIT_RE.split(raw or "")) if d]


def _parse_contact(raw: str) -> int | None:
    raw = (raw or "").strip()
    return int(raw) if raw.isdigit() else None


def _contact_names(db: Session, customers: list[Customer]) -> dict[int, tuple[str, str]]:
    names: dict[int, tuple[str, str]] = {}
    for c in customers:
        org = tech = ""
        for field, key in (("org", "org_contact_id"), ("tech", "tech_contact_id")):
            cid = getattr(c, key)
            if cid:
                u = db.get(User, cid)
                if u is not None:
                    label = f"{u.display_name}（{u.email}）" if field == "org" else u.display_name
                    if field == "org":
                        org = label
                    else:
                        tech = label
        names[c.id] = (org, tech)
    return names


@router.get("")
def list_page(request: Request, db: Session = Depends(get_db), q: str = ""):
    require_admin(request, db)
    customers = services.list_customers(db, q)
    counts = {c.id: len(services.company_users(db, c)) for c in customers}
    return render(
        request,
        db,
        "admin_customers.html",
        customers=customers,
        q=q,
        counts=counts,
        contact_names=_contact_names(db, customers),
    )


@router.get("/new")
def new_page(request: Request, db: Session = Depends(get_db)):
    require_admin(request, db)
    return render(request, db, "admin_customer_form.html", customer=None, domains=[], contacts=[])


@router.post("/new")
def create_submit(
    request: Request,
    db: Session = Depends(get_db),
    assumed_name: str = Form(""),
    legal_name: str = Form(""),
    registration_address: str = Form(""),
    telephone: str = Form(""),
    domains: str = Form(""),
    note: str = Form(""),
    org_contact_id: str = Form(""),
    tech_contact_id: str = Form(""),
):
    admin = require_admin(request, db)
    assumed_name = assumed_name.strip()
    if not assumed_name:
        return redirect("/admin/customers/new", "Company assumed name is required", "error")
    exists = db.query(Customer).filter(Customer.assumed_name == assumed_name).one_or_none()
    if exists is not None:
        return redirect("/admin/customers/new", "This assumed name already exists", "error")
    parsed = _parse_domains(domains)
    if not parsed:
        return redirect("/admin/customers/new", "At least one company domain is required", "error")
    for d in parsed:
        if services.domain_owner(db, d) is not None:
            return redirect(
                "/admin/customers/new", f"Domain {d} is owned by another customer", "error"
            )

    customer = services.create_customer(
        db,
        assumed_name=assumed_name,
        legal_name=legal_name,
        registration_address=registration_address,
        telephone=telephone,
        domains=parsed,
        note=note,
        org_contact_id=_parse_contact(org_contact_id),
        tech_contact_id=_parse_contact(tech_contact_id),
    )
    services.audit(db, admin.email, "customer.create", customer.assumed_name, ",".join(parsed))
    db.commit()
    return redirect("/admin/customers", f"Customer {assumed_name} created", "ok")


@router.get("/{customer_id}/edit")
def edit_page(request: Request, db: Session = Depends(get_db), customer_id: int = 0):
    require_admin(request, db)
    customer = db.get(Customer, customer_id)
    if customer is None:
        return redirect("/admin/customers", "Customer not found", "error")
    contacts = services.company_users(db, customer)
    return render(
        request,
        db,
        "admin_customer_form.html",
        customer=customer,
        domains=[d.domain for d in customer.domains],
        contacts=contacts,
    )


@router.post("/{customer_id}/edit")
def edit_submit(
    request: Request,
    db: Session = Depends(get_db),
    customer_id: int = 0,
    assumed_name: str = Form(""),
    legal_name: str = Form(""),
    registration_address: str = Form(""),
    telephone: str = Form(""),
    domains: str = Form(""),
    note: str = Form(""),
    org_contact_id: str = Form(""),
    tech_contact_id: str = Form(""),
    is_active: str = Form("on"),
):
    admin = require_admin(request, db)
    customer = db.get(Customer, customer_id)
    if customer is None:
        return redirect("/admin/customers", "Customer not found", "error")
    assumed_name = assumed_name.strip()
    if not assumed_name:
        return redirect(
            f"/admin/customers/{customer_id}/edit", "Company assumed name is required", "error"
        )
    other = (
        db.query(Customer).filter(Customer.assumed_name == assumed_name).one_or_none()
    )
    if other is not None and other.id != customer.id:
        return redirect(
            f"/admin/customers/{customer_id}/edit", "This assumed name already exists", "error"
        )
    parsed = _parse_domains(domains)
    if not parsed:
        return redirect(
            f"/admin/customers/{customer_id}/edit",
            "At least one company domain is required",
            "error",
        )
    for d in parsed:
        owner = services.domain_owner(db, d)
        if owner is not None and owner.id != customer.id:
            return redirect(
                f"/admin/customers/{customer_id}/edit",
                f"Domain {d} is owned by another customer",
                "error",
            )

    customer.assumed_name = assumed_name
    customer.legal_name = legal_name.strip() or None
    customer.registration_address = registration_address.strip() or None
    customer.telephone = telephone.strip() or None
    customer.note = note.strip() or None
    customer.is_active = is_active in {"on", "1", "true"}
    customer.org_contact_id = _parse_contact(org_contact_id)
    customer.tech_contact_id = _parse_contact(tech_contact_id)
    services.sync_customer_domains(db, customer, parsed)

    db.flush()
    services.recalc_customer_balance(db, customer)
    services.audit(db, admin.email, "customer.update", customer.assumed_name)
    db.commit()
    return redirect("/admin/customers", f"Customer {assumed_name} saved", "ok")


@router.post("/{customer_id}/delete")
def delete_submit(request: Request, db: Session = Depends(get_db), customer_id: int = 0):
    admin = require_admin(request, db)
    customer = db.get(Customer, customer_id)
    if customer is None:
        return redirect("/admin/customers", "Customer not found", "error")
    name = customer.assumed_name
    db.delete(customer)
    services.audit(db, admin.email, "customer.delete", name)
    db.commit()
    return redirect("/admin/customers", f"Customer {name} deleted", "ok")
