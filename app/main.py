"""应用入口。"""
from __future__ import annotations

import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

from . import settings_service
from .bootstrap import bootstrap
from .config import BASE_DIR, settings
from .database import SessionLocal, init_db
from .deps import SESSION_COOKIE, get_current_user
from .templating import templates

_ALLOWED_HOSTS_TTL = 60.0
_host_cache: dict[str, tuple[float, list[str]]] = {"ts": 0.0, "hosts": []}

# 强制改密 / 强制绑 TOTP 期间仍允许访问的路径
_FREE_PATHS = {"/change-password", "/setup-totp", "/logout", "/login"}


def _allowed_hosts() -> list[str]:
    now = time.time()
    if now - _host_cache["ts"] < _ALLOWED_HOSTS_TTL:
        return _host_cache["hosts"]
    db: Session = SessionLocal()
    try:
        raw = settings_service.get(db, "allowed_hosts")
        hosts = [h.strip().lower() for h in raw.split(",") if h.strip()]
        _host_cache["ts"] = now
        _host_cache["hosts"] = hosts
        return hosts
    finally:
        db.close()


def invalidate_host_cache() -> None:
    _host_cache["ts"] = 0.0


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    db = SessionLocal()
    try:
        bootstrap(db)
    finally:
        db.close()
    yield


app = FastAPI(title=settings.app_name, lifespan=lifespan, docs_url=None, redoc_url=None)

app.mount(
    "/static",
    StaticFiles(directory=str(BASE_DIR / "app" / "static")),
    name="static",
)
app.mount(
    "/uploads",
    StaticFiles(directory=str(settings.upload_dir)),
    name="uploads",
)


@app.middleware("http")
async def context_middleware(request: Request, call_next):
    host = (request.headers.get("host", "").split(":", 1)[0] or "").lower()
    allowed = _allowed_hosts()
    if allowed and host not in allowed:
        return HTMLResponse(
            "<h2>403 · Access via this address is not allowed</h2>"
            "<p>Please configure allowed domains in system settings.</p>",
            status_code=403,
        )
    db: Session = SessionLocal()
    try:
        user = get_current_user(request, db)
        request.state.user = user
        if user is not None:
            path = request.url.path
            static = path.startswith("/static") or path.startswith("/uploads")
            if user.must_change_password and not static and path not in _FREE_PATHS:
                return RedirectResponse("/change-password", status_code=303)
            if (
                user.must_bind_totp
                and not static
                and path not in _FREE_PATHS
                and path != "/change-password"
            ):
                return RedirectResponse("/setup-totp", status_code=303)
    finally:
        db.close()
    return await call_next(request)


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    location = (exc.headers or {}).get("Location") if exc.headers else None
    if exc.status_code == 303 and location:
        return RedirectResponse(location, status_code=303)
    if exc.status_code in {401, 403}:
        return RedirectResponse(f"/login?msg={exc.detail}&kind=error", status_code=303)
    return templates.TemplateResponse(
        request=request,
        name="error.html",
        context={"status_code": exc.status_code, "detail": exc.detail},
        status_code=exc.status_code,
    )


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/set-locale")
def set_locale(request: Request, lang: str = "en", next: str = "/"):
    from . import i18n
    from urllib.parse import quote

    locale = i18n.normalize_locale(lang)
    nxt = next if next.startswith("/") and not next.startswith("//") else "/"
    sep = "&" if "?" in nxt else "?"
    response = RedirectResponse(f"{nxt}{sep}_=1", status_code=303)
    response.set_cookie(
        i18n.LOCALE_COOKIE, locale, max_age=365 * 86400, httponly=True, samesite="lax"
    )
    return response


from .routers import admin, auth, portal  # noqa: E402  # 路由在 app 创建后注册

app.include_router(auth.router)
app.include_router(portal.router)
app.include_router(admin.router)
