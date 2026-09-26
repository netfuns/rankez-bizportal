"""管理端：站点设置、域名绑定、订单类型、SMTP、审计日志。"""
from __future__ import annotations

import json
import re
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import mailer, settings_service
from ..config import settings
from ..database import get_db
from ..deps import require_admin
from ..main import invalidate_host_cache
from ..models import AuditLog
from ..templating import redirect, render

router = APIRouter(prefix="/admin", tags=["admin"])

_LINE_RE = re.compile(r"^([^:,]+)[:,]?\s*(credit|debit)?$", re.IGNORECASE)


def _parse_order_types(raw: str) -> list[dict]:
    types: list[dict] = []
    for line in (raw or "").splitlines():
        line = line.strip()
        if not line:
            continue
        m = _LINE_RE.match(line)
        name = (m.group(1).strip() if m else line.split(",")[0].strip())
        effect = (m.group(2).lower() if m and m.group(2) else "debit")
        if name:
            types.append({"name": name, "effect": effect})
    return types or json.loads(settings_service.DEFAULTS["order_types"])


@router.get("/settings")
def settings_page(request: Request, db: Session = Depends(get_db)):
    require_admin(request, db)
    values = {
        key: ("" if key in settings_service.ENCRYPTED_KEYS else settings_service.get(db, key))
        for key in settings_service.DEFAULTS
    }
    types = settings_service.get_order_types(db)
    types_text = "\n".join(f"{t['name']},{t['effect']}" for t in types)
    return render(
        request,
        db,
        "admin_settings.html",
        values=values,
        order_types_text=types_text,
        ticket_types=settings_service.get_ticket_types(db),
        smtp_ready=mailer.smtp_ready(db),
        current_host=(request.headers.get("host", "").split(":", 1)[0] or ""),
    )


@router.post("/settings")
def settings_save(
    request: Request,
    db: Session = Depends(get_db),
    site_name: str = Form(""),
    browser_title: str = Form(""),
    allowed_hosts: str = Form(""),
    session_timeout_minutes: str = Form("120"),
    force_totp: str = Form(""),
    registration_enabled: str = Form(""),
    order_types: str = Form(""),
    ticket_types: str = Form(""),
    smtp_host: str = Form(""),
    smtp_port: str = Form("587"),
    smtp_user: str = Form(""),
    smtp_password: str = Form(""),
    smtp_from: str = Form(""),
    smtp_tls: str = Form(""),
    force: str = Form(""),
):
    require_admin(request, db)
    hosts = [h.strip().lower() for h in allowed_hosts.split(",") if h.strip()]
    current_host = (request.headers.get("host", "").split(":", 1)[0] or "").lower()
    if hosts and current_host and current_host not in hosts and force != "1":
        return redirect(
            "/admin/settings",
            f"Current host {current_host} is not in the allowed list. "
            "Check \"Still save\" at the bottom if you are sure.",
            "error",
        )

    if not smtp_password:
        # 密码框留空表示保持原值
        smtp_password = settings_service.get(db, "smtp_password")

    parsed_types = _parse_order_types(order_types)
    parsed_ticket_types = [x.strip() for x in (ticket_types or "").split(",") if x.strip()]
    parsed_ticket_types = parsed_ticket_types or settings_service.get_ticket_types(db)
    timeout = session_timeout_minutes.strip() or "120"
    if not timeout.isdigit() or int(timeout) < 5:
        return redirect(
            "/admin/settings",
            "Session timeout must be an integer of at least 5 (minutes)",
            "error",
        )

    settings_service.set_many(
        db,
        {
            "site_name": site_name.strip() or "BizPortal",
            "browser_title": browser_title.strip() or site_name.strip() or "BizPortal",
            "allowed_hosts": ",".join(hosts),
            "session_timeout_minutes": timeout,
            "force_totp": "true" if force_totp == "on" else "false",
            "registration_enabled": "true" if registration_enabled == "on" else "false",
            "order_types": json.dumps(parsed_types, ensure_ascii=False),
            "ticket_types": json.dumps(parsed_ticket_types, ensure_ascii=False),
            "smtp_host": smtp_host.strip(),
            "smtp_port": smtp_port.strip() or "587",
            "smtp_user": smtp_user.strip(),
            "smtp_password": smtp_password,
            "smtp_from": smtp_from.strip(),
            "smtp_tls": "true" if smtp_tls == "on" else "false",
        },
    )
    invalidate_host_cache()
    return redirect("/admin/settings", "Settings saved", "ok")


@router.post("/settings/logo")
async def upload_logo(
    request: Request,
    db: Session = Depends(get_db),
    logo: UploadFile = File(None),
):
    require_admin(request, db)
    if logo is None or not logo.filename:
        return redirect("/admin/settings", "Please select a logo file", "error")
    ext = Path(logo.filename).suffix.lower()
    if ext not in {".png", ".jpg", ".jpeg", ".svg", ".webp", ".gif"}:
        return redirect("/admin/settings", "Logo supports png/jpg/svg/webp/gif only", "error")
    from secrets import token_hex

    target = settings.upload_dir / f"logo_{token_hex(6)}{ext}"
    target.write_bytes(await logo.read())
    settings_service.set_value(db, "site_logo", target.name)
    return redirect("/admin/settings", "Logo updated", "ok")


@router.post("/settings/logo/clear")
def clear_logo(request: Request, db: Session = Depends(get_db)):
    require_admin(request, db)
    settings_service.set_value(db, "site_logo", "")
    return redirect("/admin/settings", "Logo cleared (using text)", "ok")


@router.post("/settings/smtp-test")
def smtp_test(
    request: Request, db: Session = Depends(get_db), test_to: str = Form("")
):
    require_admin(request, db)
    if not test_to.strip():
        return redirect("/admin/settings", "Please fill in the test recipient", "error")
    ok, error = mailer.send_mail(
        db, test_to.strip(), "[BizPortal] SMTP Test Email", "This is a test email from BizPortal.\n"
    )
    if ok:
        return redirect("/admin/settings", f"Test email sent to {test_to.strip()}", "ok")
    return redirect("/admin/settings", f"Send failed: {error}", "error")


@router.get("/audit-log")
def audit_log(request: Request, db: Session = Depends(get_db)):
    require_admin(request, db)
    rows = list(db.execute(select(AuditLog).order_by(AuditLog.id.desc()).limit(200)).scalars())
    return render(request, db, "admin_audit.html", rows=rows)
