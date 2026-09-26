"""模板渲染助手：统一注入站点信息、当前用户、提示消息、多语言。"""
from __future__ import annotations

from decimal import Decimal

from fastapi import Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from . import i18n, settings_service
from .config import BASE_DIR

templates = Jinja2Templates(directory=str(BASE_DIR / "app" / "templates"))


def fmt_money(value) -> str:
    try:
        return f"{Decimal(str(value)):,.2f}"
    except Exception:  # noqa: BLE001
        return "0.00"


def fmt_dt(value) -> str:
    if not value:
        return ""
    return value.strftime("%Y-%m-%d %H:%M:%S")


templates.env.filters["money"] = fmt_money
templates.env.filters["dt"] = fmt_dt


def site_context(db: Session) -> dict:
    return {
        "site_name": settings_service.get(db, "site_name"),
        "browser_title": settings_service.get(db, "browser_title")
        or settings_service.get(db, "site_name"),
        "site_logo": settings_service.get(db, "site_logo"),
    }


def build_context(request: Request, db: Session, **extra) -> dict:
    ctx: dict = {"request": request}
    ctx.update(site_context(db))
    ctx["current_user"] = getattr(request.state, "user", None)
    ctx["msg"] = request.query_params.get("msg", "")
    ctx["kind"] = request.query_params.get("kind", "info")
    locale = i18n.normalize_locale(request.cookies.get(i18n.LOCALE_COOKIE))
    ctx["locale"] = locale
    ctx["locales"] = i18n.LOCALES
    ctx["t"] = lambda key, **fmt: i18n.translate(locale, key, **fmt)
    if ctx["msg"]:
        ctx["msg"] = i18n.translate(locale, ctx["msg"])
    ctx.update(extra)
    return ctx


def render(request: Request, db: Session, name: str, **extra):
    ctx = build_context(request, db, **extra)
    return templates.TemplateResponse(request=request, name=name, context=ctx)


def redirect(url: str, msg: str = "", kind: str = "info"):
    """带提示消息的重定向。msg 传 i18n key（英文），模板显示时翻译。"""
    from fastapi.responses import RedirectResponse

    if msg:
        sep = "&" if "?" in url else "?"
        from urllib.parse import quote

        url = f"{url}{sep}msg={quote(msg)}&kind={quote(kind)}"
    return RedirectResponse(url, status_code=303)
