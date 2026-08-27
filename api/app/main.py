import asyncio
import contextlib
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from pathlib import Path

from prometheus_fastapi_instrumentator import Instrumentator

from app.config import get_settings
from app.database import SessionLocal
from app.events import broker
from app.releases import broadcast_release, fetch_latest_from_github, get_release, store_release
from app.routers import auth, health, metrics, superadmin, sync, updates


settings = get_settings()


def _stored_state() -> tuple[str | None, bool]:
    """Current tag, and whether we already know which files it ships."""
    db = SessionLocal()
    try:
        row = get_release(db)
        if row is None:
            return None, False
        return row.tag, bool(row.assets)
    finally:
        db.close()


def _save_release(tag: str, data: dict | None) -> dict:
    db = SessionLocal()
    try:
        return store_release(db, tag, data, source="poll")
    finally:
        db.close()


async def _release_poll_loop() -> None:
    """Safety net behind the release webhook ping.

    The workflow ping is what makes new releases show up instantly. This catches
    the leftovers: a release published by hand, or one announced while this
    service was restarting. One request per interval for the whole fleet, so the
    GitHub rate limit is never a factor.
    """
    interval = max(60, int(settings.release_poll_seconds))
    while True:
        await asyncio.sleep(interval)
        try:
            data = await fetch_latest_from_github()
            tag = str((data or {}).get("tag_name") or "").strip()
            if not tag:
                continue
            stored_tag, has_assets = await run_in_threadpool(_stored_state)
            if tag == stored_tag and has_assets:
                continue
            event = await run_in_threadpool(_save_release, tag, data)
            if tag != stored_tag:
                # Same tag with assets now filled in is a repair, not news.
                broadcast_release(event)
        except asyncio.CancelledError:
            raise
        except Exception:
            # A poll that fails must never take the API down with it.
            continue


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Sync endpoints run in a threadpool; give the broker a handle on the serving
    # loop so they can publish change events back into the SSE streams.
    broker.bind_loop(asyncio.get_running_loop())
    poller = asyncio.create_task(_release_poll_loop())
    try:
        yield
    finally:
        poller.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await poller


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.resolved_trusted_hosts())
_superadmin_static = Path(__file__).resolve().parent / "static" / "superadmin"
app.mount("/superadmin/assets", StaticFiles(directory=_superadmin_static), name="superadmin-assets")

if settings.metrics_token:
    # Records request count, duration and status per endpoint. The template
    # path is used as the label, so /sync/pull is one series rather than one
    # per query string. The long-lived SSE stream is excluded: it would sit in
    # the "in progress" gauge for hours and say nothing useful.
    Instrumentator(
        excluded_handlers=[f"{settings.api_prefix}/sync/events", "/metrics", "/health"],
        should_group_status_codes=False,
    ).instrument(app)
    app.include_router(metrics.router)

app.include_router(health.router)
app.include_router(superadmin.page_router)
app.include_router(auth.router, prefix=settings.api_prefix)
app.include_router(superadmin.router, prefix=settings.api_prefix)
app.include_router(sync.router, prefix=settings.api_prefix)
app.include_router(updates.router, prefix=settings.api_prefix)


@app.get("/install.sh")
async def root_install_sh():
    return await updates.get_install_sh()


@app.get("/install.ps1")
async def root_install_ps1():
    return await updates.get_install_ps1()
