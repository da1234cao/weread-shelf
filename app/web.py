"""FastAPI application: dashboard pages, JSON API, manual refresh, scheduler."""

from __future__ import annotations

import json
import logging
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import repository as repo
from .config import get_settings
from .db import init_db, session_scope
from .fetcher import run_daily_pull
from .scheduler import shutdown_scheduler, start_scheduler
from .utils import fmt_duration

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
templates.env.filters["duration"] = fmt_duration

# Guards against overlapping pulls (startup, scheduler, manual refresh).
_pull_lock = threading.Lock()


def _run_pull_locked(kind: str) -> None:
    if not _pull_lock.acquire(blocking=False):
        logger.info("pull already running; skipping %s", kind)
        return
    try:
        run_daily_pull(kind=kind)
    except Exception:
        logger.exception("%s pull failed", kind)
    finally:
        _pull_lock.release()


def _has_any_data() -> bool:
    with session_scope() as session:
        return repo.last_pull(session) is not None


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    settings = get_settings()
    if settings.has_api_key:
        start_scheduler()
        if settings.pull_on_startup and not _has_any_data():
            logger.info("no data yet; kicking off initial pull in background")
            threading.Thread(target=_run_pull_locked, args=("startup",), daemon=True).start()
    else:
        logger.warning("WEREAD_API_KEY not set; scheduler/pull disabled (UI still serves)")
    yield
    shutdown_scheduler()


app = FastAPI(title="weread-shelf", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


# --------------------------------------------------------------------------
# Pages
# --------------------------------------------------------------------------
@app.get("/")
def page_overview(request: Request):
    with session_scope() as session:
        ctx = {
            "overview": repo.overview(session),
            "monthly": repo.latest_snapshot(session, "monthly"),
            "overall": repo.latest_snapshot(session, "overall"),
        }
    return templates.TemplateResponse(request, "overview.html", {"active": "overview", **ctx})


@app.get("/shelf")
def page_shelf(request: Request):
    with session_scope() as session:
        shelf = repo.current_shelf(session)
    return templates.TemplateResponse(request, "shelf.html", {"active": "shelf", "shelf": shelf})


@app.get("/notes")
def page_notes(request: Request):
    with session_scope() as session:
        books = repo.books_with_notes(session)
    return templates.TemplateResponse(request, "notes.html", {"active": "notes", "books": books})


@app.get("/notes/{book_id}")
def page_note_detail(request: Request, book_id: str):
    with session_scope() as session:
        data = repo.book_notes(session, book_id)
    if data["book"] is None and not data["chapters"]:
        raise HTTPException(status_code=404, detail="book not found")
    return templates.TemplateResponse(request, "note_detail.html", {"active": "notes", **data})


@app.get("/discover")
def page_discover(request: Request):
    with session_scope() as session:
        recs = repo.current_recommendations(session)
    return templates.TemplateResponse(request, "discover.html", {"active": "discover", "recs": recs})


# --------------------------------------------------------------------------
# JSON API
# --------------------------------------------------------------------------
@app.get("/api/health")
def api_health():
    return {"status": "ok"}


@app.get("/api/overview")
def api_overview():
    with session_scope() as session:
        ov = repo.overview(session)
        ov.pop("last_pull", None)
        return ov


@app.get("/api/stats/trend")
def api_trend(days: int = 120):
    with session_scope() as session:
        daily = repo.daily_trend(session, days=days)
        monthly = repo.monthly_trend(session)
        overall = repo.latest_snapshot(session, "overall")
        payload = json.loads(overall.payload_json) if overall else {}
    return {
        "daily": daily,
        "monthly": monthly,
        "preferCategory": payload.get("preferCategory", []),
        "preferAuthor": payload.get("preferAuthor", []),
        "preferTime": payload.get("preferTime", []),
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
    return "\n".join(lines)


@app.post("/api/refresh")
def api_refresh():
    if not get_settings().has_api_key:
        raise HTTPException(status_code=400, detail="WEREAD_API_KEY not configured")
    if _pull_lock.locked():
        return JSONResponse({"status": "already_running"}, status_code=202)
    threading.Thread(target=_run_pull_locked, args=("manual",), daemon=True).start()
    return JSONResponse({"status": "started"}, status_code=202)
