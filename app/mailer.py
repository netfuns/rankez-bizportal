"""邮件发送：SMTP 已配置则真发，未配置或失败则仅入库（站内发件箱可见）。"""
from __future__ import annotations

import smtplib
from email.header import Header
from email.mime.text import MIMEText
from email.utils import formataddr, parseaddr

from sqlalchemy.orm import Session

from . import settings_service
from .models import MailOutbox


def smtp_ready(db: Session) -> bool:
    host = settings_service.get(db, "smtp_host").strip()
    return bool(host)


def send_mail(db: Session, to_addr: str, subject: str, body: str) -> tuple[bool, str]:
    """返回 (是否发送成功, 错误原因)。无论成败都会写入 mail_outbox。"""
    host = settings_service.get(db, "smtp_host").strip()
    port = settings_service.get_int(db, "smtp_port") or 587
    user = settings_service.get(db, "smtp_user").strip()
    password = settings_service.get(db, "smtp_password")
    sender = settings_service.get(db, "smtp_from").strip() or user or "noreply@localhost"
    use_tls = settings_service.get_bool(db, "smtp_tls")

    ok = False
    error = ""
    if not host:
        error = "SMTP 未配置，邮件仅保存在站内发件箱"
    else:
        try:
            msg = MIMEText(body, "plain", "utf-8")
            msg["Subject"] = Header(subject, "utf-8")
            msg["From"] = formataddr(("BizPortal", parseaddr(sender)[1] or sender))
            msg["To"] = to_addr

            if port == 465:
                server = smtplib.SMTP_SSL(host, port, timeout=15)
            else:
                server = smtplib.SMTP(host, port, timeout=15)
                if use_tls:
                    server.starttls()
            try:
                if user:
                    server.login(user, password)
                server.sendmail(sender, [to_addr], msg.as_string())
                ok = True
            finally:
                try:
                    server.quit()
                except Exception:  # noqa: BLE001 - 退出失败不影响结果
                    pass
        except Exception as exc:  # noqa: BLE001 - 任何异常都降级为入库
            error = f"{type(exc).__name__}: {exc}"

    db.add(
        MailOutbox(
            to_addr=to_addr,
            subject=subject,
            body=body,
            status="sent" if ok else "failed",
            error=error or None,
        )
    )
    db.commit()
    return ok, error


def render_confirm_mail(site_name: str, email: str, temp_password: str, confirm_url: str) -> str:
    return (
        f"您好，\n\n"
        f"您已在 {site_name} 注册账号（{email}）。\n\n"
        f"临时密码：{temp_password}\n\n"
        f"请先点击下面的链接确认邮箱地址，之后才能登录：\n{confirm_url}\n\n"
        f"首次登录时系统会要求您修改密码并绑定 TOTP 动态口令。\n\n"
        f"如果这不是您本人的操作，请忽略此邮件。\n"
    )


def render_totp_reset_mail(site_name: str, email: str) -> str:
    return (
        f"您好，\n\n"
        f"管理员已重置您在 {site_name}（{email}）的 TOTP 动态口令绑定。\n"
        f"下次登录时系统会要求您重新绑定 TOTP。\n"
    )
