"""管理端：站内发件箱（SMTP 未配置时可直接查看确认链接）。"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from fastapi import APIRouter, Depends, Request

from .. import mailer
from ..database import get_db
from ..deps import require_admin
from ..models import MailOutbox
from ..templating import redirect, render

router = APIRouter(prefix="/admin/mail", tags=["admin"])


@router.get("")
def mailbox(request: Request, db: Session = Depends(get_db)):
    require_admin(request, db)
    rows = list(db.execute(select(MailOutbox).order_by(MailOutbox.id.desc()).limit(200)).scalars())
    return render(request, db, "admin_mail.html", rows=rows, smtp_ready=mailer.smtp_ready(db))


@router.post("/{mail_id}/resend")
def resend(request: Request, db: Session = Depends(get_db), mail_id: int = 0):
    require_admin(request, db)
    row = db.get(MailOutbox, mail_id)
    if row is None:
        return redirect("/admin/mail", "Email not found", "error")
    ok, error = mailer.send_mail(db, row.to_addr, row.subject, row.body)
    if ok:
        return redirect("/admin/mail", f"Email sent to {row.to_addr}", "ok")
    return redirect("/admin/mail", f"Send failed: {error}", "error")


@router.post("/{mail_id}/delete")
def delete_mail(request: Request, db: Session = Depends(get_db), mail_id: int = 0):
    require_admin(request, db)
    row = db.get(MailOutbox, mail_id)
    if row is None:
        return redirect("/admin/mail", "Email not found", "error")
    db.delete(row)
    db.commit()
    return redirect("/admin/mail", "Email deleted", "ok")
