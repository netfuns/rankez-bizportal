"""管理端：OV 工单管理（筛选 / 搜索 / 确认验证）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from .. import services, settings_service
from ..database import get_db
from ..deps import require_admin
from ..models import Ticket, User
from ..templating import redirect, render

router = APIRouter(prefix="/admin/tickets", tags=["admin"])


@router.get("")
def list_page(
    request: Request,
    db: Session = Depends(get_db),
    q: str = "",
    ticket_type: str = "",
    status: str = "",
    date_from: str = "",
    date_to: str = "",
    assumed: str = "",
):
    require_admin(request, db)
    tickets = services.list_tickets(
        db,
        keyword=q,
        ticket_type=ticket_type,
        status=status,
        date_from=date_from,
        date_to=date_to,
        assumed=assumed,
    )
    types = settings_service.get_ticket_types(db)
    requester_names: dict[int, str] = {}
    for t in tickets:
        if t.requested_by_id and t.requested_by_id not in requester_names:
            u = db.get(User, t.requested_by_id)
            if u is not None:
                requester_names[t.requested_by_id] = u.display_name
    return render(
        request,
        db,
        "admin_tickets.html",
        tickets=tickets,
        q=q,
        ticket_type=ticket_type,
        status=status,
        date_from=date_from,
        date_to=date_to,
        assumed=assumed,
        types=types,
        requester_names=requester_names,
    )


@router.post("/{ticket_id}/verify")
def verify(request: Request, db: Session = Depends(get_db), ticket_id: int = 0):
    admin = require_admin(request, db)
    ticket = db.get(Ticket, ticket_id)
    if ticket is None:
        return redirect("/admin/tickets", "Ticket not found", "error")
    if ticket.status != "verified":
        services.verify_ticket(db, ticket, admin.email)
        services.audit(db, admin.email, "ticket.verify", ticket.domain, ticket.number)
    db.commit()
    return redirect("/admin/tickets", "Ticket verified", "ok")
