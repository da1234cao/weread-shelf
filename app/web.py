"""FastAPI application: dashboard pages, JSON API, admin page, auth, scheduler."""

from __future__ import annotations

import json
import logging
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import auth
from . import fetcher
from . import repository as repo
from . import settings_store
from . import stats
from .client import WeReadClient
from .db import init_db, session_scope
from .models import AppUser
from .scheduler import reschedule, shutdown_scheduler, start_scheduler
from .utils import fmt_duration, fmt_local

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
templates.env.filters["duration"] = fmt_duration
templates.env.filters["localtime"] = fmt_local

# Curated timezone choices for the admin dropdown; the user's current value is
# always added on top of this so a custom zone is never lost.
COMMON_TIMEZONES = [
    "UTC",
    "Asia/Shanghai", "Asia/Hong_Kong", "Asia/Taipei", "Asia/Tokyo", "Asia/Seoul",
    "Asia/Singapore", "Asia/Bangkok", "Asia/Kolkata", "Asia/Dubai",
    "Europe/London", "Europe/Paris", "Europe/Berlin", "Europe/Moscow",
    "America/New_York", "America/Chicago", "America/Denver", "America/Los_Angeles",
    "Australia/Sydney", "Pacific/Auckland",
]

# Snapshot retention presets for the admin dropdown: (days, label). 0 = keep forever.
RETENTION_CHOICES = [
    (0, "不限制"), (90, "90 天"), (180, "180 天"), (365, "1 年"),
    (730, "2 年"), (1825, "5 年"), (2920, "8 年"), (3650, "10 年"),
]

# Paths reachable without authentication even when "require login" is on.
_PUBLIC_PREFIXES = ("/login", "/logout", "/static", "/api/health", "/favicon.ico")


def _has_any_data() -> bool:
    with session_scope() as session:
        return repo.last_pull(session) is not None


def _bootstrap() -> None:
    """Ensure the settings row and a default admin account exist."""
    settings_store.get()  # seeds the row + session secret
    with session_scope() as session:
        if not repo.has_admin(session):
            repo.create_user(
                session, "admin", auth.hash_password("admin"), is_admin=True, must_change=True
            )
            logger.info("seeded default admin account (admin/admin) — change it on first login")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    _bootstrap()
    start_scheduler()
    if settings_store.get().api_key and not _has_any_data():
        logger.info("no data yet; kicking off initial pull in background")
        threading.Thread(target=fetcher.run_pull_locked, args=("startup",), daemon=True).start()
    yield
    shutdown_scheduler()


app = FastAPI(title="weread-shelf", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


@app.get("/favicon.ico")
def favicon():
    """Serve the bundled icon for browsers' implicit /favicon.ico request."""
    return FileResponse(BASE_DIR / "static" / "favicon.png", media_type="image/png")


# --------------------------------------------------------------------------
# Access control
# --------------------------------------------------------------------------
def _is_public_path(path: str) -> bool:
    return path.startswith(_PUBLIC_PREFIXES)


@app.middleware("http")
async def access_control(request: Request, call_next):
    path = request.url.path
    user = auth.current_user(request)
    request.state.user = user  # reused by handlers/_render this request

    if path == "/admin" or path.startswith("/admin/"):
        if not (user and user.is_admin):
            return RedirectResponse(f"/login?next={path}", status_code=303)
        # Until default credentials are changed, only the dashboard + account form work.
        if user.must_change and path not in ("/admin", "/admin/account"):
            return RedirectResponse("/admin", status_code=303)
    elif settings_store.get().require_user_auth and not _is_public_path(path):
        if user is None:
            return RedirectResponse(f"/login?next={path}", status_code=303)

    return await call_next(request)


def _current(request: Request) -> AppUser | None:
    return getattr(request.state, "user", None)


def _render(request: Request, name: str, **ctx):
    s = settings_store.get()
    base = {
        "nav": {"show_overview": s.show_overview, "show_discover": s.show_discover},
        "show_footer_credit": s.show_footer_credit,
        "cur_user": _current(request),
    }
    return templates.TemplateResponse(request, name, {**base, **ctx})


def _require_admin(request: Request) -> AppUser:
    user = _current(request)
    if not (user and user.is_admin):
        raise HTTPException(status_code=403, detail="admin only")
    return user


def _require_admin_ready(request: Request) -> AppUser:
    user = _require_admin(request)
    if user.must_change:
        raise HTTPException(status_code=403, detail="change the default credentials first")
    return user


# --------------------------------------------------------------------------
# Pages
# --------------------------------------------------------------------------
@app.get("/")
def page_overview(request: Request):
    if not settings_store.get().show_overview:
        return RedirectResponse("/shelf", status_code=303)
    # All data is loaded client-side from /api/stats (a pure DB read).
    return _render(request, "overview.html", active="overview")


@app.get("/shelf")
def page_shelf(request: Request):
    with session_scope() as session:
        shelf = repo.current_shelf(session)
    return _render(request, "shelf.html", active="shelf", shelf=shelf)


@app.get("/notes")
def page_notes(request: Request):
    with session_scope() as session:
        books = repo.books_with_notes(session)
    return _render(request, "notes.html", active="notes", books=books)


@app.get("/book/{book_id}")
def page_book(request: Request, book_id: str):
    # Book metadata is enriched during the pull (see fetcher), so this is a pure read.
    with session_scope() as session:
        data = repo.book_notes(session, book_id)
        toc = repo.book_chapters(session, book_id)
    if data["book"] is None and not data["chapters"]:
        raise HTTPException(status_code=404, detail="book not found")
    return _render(request, "book_detail.html", active="", toc=toc, **data)


@app.get("/discover")
def page_discover(request: Request):
    if not settings_store.get().show_discover:
        return RedirectResponse("/", status_code=303)
    with session_scope() as session:
        recs = repo.current_recommendations(session)
    return _render(request, "discover.html", active="discover", recs=recs)


# --------------------------------------------------------------------------
# Auth pages
# --------------------------------------------------------------------------
@app.get("/login")
def login_form(request: Request, next: str = "/"):
    return _render(request, "login.html", next=next, error=None)


@app.post("/login")
def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next: str = Form("/"),
):
    user = auth.authenticate(username, password)
    if user is None:
        return _render(request, "login.html", next=next, error="用户名或密码错误")
    # Only allow same-site redirect targets (guard against open redirect).
    if not next.startswith("/") or next.startswith("//"):
        next = "/"
    target = "/admin" if user.is_admin and user.must_change else next
    resp = RedirectResponse(target, status_code=303)
    resp.set_cookie(auth.COOKIE, auth.make_token(user.username), httponly=True, samesite="lax")
    return resp


@app.get("/logout")
def logout():
    resp = RedirectResponse("/", status_code=303)
    resp.delete_cookie(auth.COOKIE)
    return resp


# --------------------------------------------------------------------------
# Admin page
# --------------------------------------------------------------------------
@app.get("/admin")
def admin_home(request: Request):
    user = _require_admin(request)
    s = settings_store.get()
    with session_scope() as session:
        users = repo.list_users(session)
        last = repo.last_pull(session)
    upgrade = bool(s.suggested_skill_version and s.suggested_skill_version != s.skill_version)
    timezones = COMMON_TIMEZONES if s.timezone in COMMON_TIMEZONES else [s.timezone, *COMMON_TIMEZONES]
    return _render(
        request, "admin.html",
        s=s, users=users, last_pull=last, upgrade_available=upgrade,
        must_change=user.must_change, saved=request.query_params.get("saved"),
        has_key=bool(s.api_key), timezones=timezones, retention_choices=RETENTION_CHOICES,
    )


@app.post("/admin/settings")
def admin_settings(
    request: Request,
    show_overview: bool = Form(False),
    show_discover: bool = Form(False),
    show_footer_credit: bool = Form(False),
    require_user_auth: bool = Form(False),
    pull_interval_hours: int = Form(24),
    retention_days: int = Form(0),
    gateway_interval: float = Form(0.2),
    timezone: str = Form("Asia/Shanghai"),
    skill_version: str = Form("1.0.3"),
):
    _require_admin_ready(request)
    try:
        ZoneInfo(timezone)
    except Exception:
        timezone = settings_store.get().timezone
    if pull_interval_hours not in (6, 12, 24):
        pull_interval_hours = 24
    if retention_days not in {days for days, _ in RETENTION_CHOICES}:
        retention_days = 0
    gateway_interval = min(max(gateway_interval, 0.05), 5.0)
    settings_store.save(
        show_overview=show_overview, show_discover=show_discover,
        show_footer_credit=show_footer_credit,
        require_user_auth=require_user_auth, pull_interval_hours=pull_interval_hours,
        retention_days=retention_days, gateway_interval=gateway_interval,
        timezone=timezone, skill_version=(skill_version or "").strip() or "1.0.3",
    )
    reschedule()
    return RedirectResponse("/admin?saved=settings", status_code=303)


@app.post("/admin/apikey")
def admin_apikey(request: Request, api_key: str = Form(...)):
    _require_admin_ready(request)
    api_key = api_key.strip()
    if not api_key.startswith("wrk-"):
        return RedirectResponse("/admin?saved=apikey_bad", status_code=303)
    try:
        with WeReadClient(api_key=api_key) as client:
            client.shelf_sync()
    except Exception as exc:
        logger.warning("API key test call failed: %s", exc)
        return RedirectResponse("/admin?saved=apikey_fail", status_code=303)
    settings_store.save(api_key=api_key)
    # With a key now configured and no data yet, kick off the first pull in the
    # background (mirrors lifespan's initial pull) so the dashboard fills in on its
    # own; the page's poller surfaces progress. Steady-state key replacement, where
    # data already exists, leaves it to the scheduler / "refresh" button.
    if not _has_any_data():
        threading.Thread(target=fetcher.run_pull_locked, args=("startup",), daemon=True).start()
    return RedirectResponse("/admin?saved=apikey", status_code=303)


@app.post("/admin/account")
def admin_account(request: Request, username: str = Form(...), password: str = Form(...)):
    user = _require_admin(request)  # allowed even while must_change
    username = username.strip()
    if not username or not password:
        return RedirectResponse("/admin?saved=account_bad", status_code=303)
    with session_scope() as session:
        if username != user.username:
            if repo.get_user(session, username) is not None:
                return RedirectResponse("/admin?saved=account_taken", status_code=303)
            repo.rename_user(session, user.username, username)
        repo.set_password(session, username, auth.hash_password(password), must_change=False)
    resp = RedirectResponse("/admin?saved=account", status_code=303)
    resp.set_cookie(auth.COOKIE, auth.make_token(username), httponly=True, samesite="lax")
    return resp


@app.post("/admin/users")
def admin_users(
    request: Request,
    action: str = Form(...),
    username: str = Form(...),
    password: str = Form(""),
):
    _require_admin_ready(request)
    username = username.strip()
    with session_scope() as session:
        existing = repo.get_user(session, username)
        if action == "delete":
            if existing is not None and not existing.is_admin:
                repo.delete_user(session, username)
        elif action == "create":
            if not username or not password:
                return RedirectResponse("/admin?saved=user_bad", status_code=303)
            if existing is not None:
                return RedirectResponse("/admin?saved=user_taken", status_code=303)
            repo.create_user(session, username, auth.hash_password(password))
        elif action == "password":
            if existing is not None and not existing.is_admin and password:
                repo.set_password(session, username, auth.hash_password(password))
    return RedirectResponse("/admin?saved=users", status_code=303)


# --------------------------------------------------------------------------
# JSON API
# --------------------------------------------------------------------------
@app.get("/api/health")
def api_health():
    return {"status": "ok"}


@app.get("/api/stats")
def api_stats(mode: str = "monthly", offset: int = 0):
    """One period's display stats (pure DB read; the pull populates period_stat).

    `period_label` / `has_prev` / `has_next` are time-relative, so they're computed
    here rather than stored. A period not yet fetched returns ``{empty: true}``.
    """
    if mode not in stats.MODES:
        raise HTTPException(status_code=400, detail="unknown mode")
    offset = min(offset, 0)  # no future periods
    base_time = stats.base_time_for(mode, offset)
    with session_scope() as session:
        payload = repo.get_period_stat(session, mode, base_time)
        if payload is None:
            return {"empty": True, "mode": mode, "offset": offset}
        has_prev = mode != "overall" and repo.has_period_stat(
            session, mode, stats.base_time_for(mode, offset - 1)
        )
    return {
        **payload,
        "mode": mode,
        "offset": offset,
        "period_label": stats.period_label(mode, offset, base_time),
        "has_prev": has_prev,
        "has_next": offset < 0,
    }


@app.get("/api/shelf")
def api_shelf():
    with session_scope() as session:
        return repo.current_shelf(session)


@app.get("/api/notes")
def api_notes():
    with session_scope() as session:
        return repo.books_with_notes(session)


@app.get("/api/discover")
def api_discover():
    with session_scope() as session:
        return [r.model_dump() for r in repo.current_recommendations(session)]


@app.get("/export/notes/{book_id}.md", response_class=PlainTextResponse)
def export_notes_md(book_id: str):
    with session_scope() as session:
        data = repo.book_notes(session, book_id)
    book = data["book"]
    if book is None and not data["chapters"]:
        raise HTTPException(status_code=404, detail="book not found")
    title = book.title if book else book_id
    author = book.author if book else ""
    lines = [f"# {title}", f"> {author}" if author else "", ""]
    for ch in data["chapters"]:
        lines.append(f"## {ch['title'] or '未命名章节'}")
        for bm in ch["bookmarks"]:
            lines.append(f"> {bm.mark_text}")
            lines.append("")
        for rv in ch["reviews"]:
            if rv.abstract:
                lines.append(f"> {rv.abstract}")
            lines.append(f"💭 {rv.content}")
            lines.append("")
    if data["book_reviews"]:
        lines.append("## 书评")
        lines.append("")
        for rv in data["book_reviews"]:
            lines.append(rv.content)
            lines.append("")
    return "\n".join(lines)


@app.post("/api/refresh")
def api_refresh():
    if not settings_store.get().api_key:
        raise HTTPException(status_code=400, detail="API key not configured")
    if fetcher.pull_running():
        return JSONResponse({"status": "already_running"}, status_code=202)
    threading.Thread(target=fetcher.run_pull_locked, args=("manual",), daemon=True).start()
    return JSONResponse({"status": "started"}, status_code=202)


@app.get("/api/refresh/status")
def api_refresh_status():
    """Report whether a pull is in progress and the latest run's outcome.

    The frontend polls this after starting a refresh — and on page load — so the
    user can tell when a pull (manual, scheduled, or the startup one) has actually
    finished and whether it succeeded. ``has_data`` reflects whether any pull has
    ever succeeded, so a first-time sync can auto-reload while later ones don't.
    """
    with session_scope() as session:
        run = repo.latest_pull(session)
        has_data = repo.last_pull(session) is not None
        last = None
        if run is not None:
            last = {
                "id": run.id,
                "kind": run.kind,
                "ok": run.ok,
                "finished_at": run.finished_at.isoformat() if run.finished_at else None,
                "error": run.error,
                "counts": json.loads(run.counts_json or "{}"),
            }
    return JSONResponse({"running": fetcher.pull_running(), "has_data": has_data, "last": last})
