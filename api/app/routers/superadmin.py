from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
import asyncio
import json
import secrets
import threading
import time

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app import log_archive
from app.config import get_settings
from app.database import get_db
from app.events import broker
from app.models import Device, GoogleOAuthSession, SyncBatch, SyncMeta, User, UserRecord
from app.schemas import (
    ALLOWED_SYNC_TABLES,
    LogLineOut,
    LogMonthOut,
    LogMonthsOut,
    LogPageOut,
    SuperadminAccountOut,
    SuperadminAvailabilityOut,
    SuperadminActionOut,
    SuperadminConfirmRequest,
    SuperadminLoginRequest,
    SuperadminOverviewOut,
    SuperadminStatusRequest,
    SuperadminTokenOut,
)
from app.security import create_superadmin_token, decode_superadmin_token


router = APIRouter(prefix="/superadmin", tags=["superadmin"])
page_router = APIRouter(include_in_schema=False)
bearer = HTTPBearer(auto_error=False)

_DISABLED_MESSAGE = (
    "Superadmin panel yoqilmagan: serverdagi .env faylda SUPERADMIN_PASSWORD "
    "qiymati o'rnatilmagan. Uni yozib, konteynerlarni qayta ishga tushiring."
)

_LOGIN_WINDOW_SECONDS = 5 * 60
_LOGIN_MAX_FAILURES = 5
_failed_logins: dict[str, deque[float]] = defaultdict(deque)
_login_lock = threading.Lock()
_PAGE_PATH = Path(__file__).resolve().parents[1] / "static" / "superadmin" / "index.html"


def _client_key(request: Request) -> str:
    forwarded = (request.headers.get("x-forwarded-for") or "").split(",", 1)[0].strip()
    return forwarded or (request.client.host if request.client else "unknown")


def _prune_failures(client_key: str, now: float) -> deque[float]:
    attempts = _failed_logins[client_key]
    while attempts and attempts[0] <= now - _LOGIN_WINDOW_SECONDS:
        attempts.popleft()
    return attempts


def _check_login_limit(client_key: str) -> None:
    now = time.monotonic()
    with _login_lock:
        attempts = _prune_failures(client_key, now)
        if len(attempts) >= _LOGIN_MAX_FAILURES:
            retry_after = max(1, int(_LOGIN_WINDOW_SECONDS - (now - attempts[0])))
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Juda ko'p urinish. Bir necha daqiqadan keyin qayta urinib ko'ring.",
                headers={"Retry-After": str(retry_after)},
            )


def _record_login_failure(client_key: str) -> None:
    now = time.monotonic()
    with _login_lock:
        _prune_failures(client_key, now).append(now)


def _clear_login_failures(client_key: str) -> None:
    with _login_lock:
        _failed_logins.pop(client_key, None)


def get_superadmin(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> str:
    settings = get_settings()
    if not settings.superadmin_password:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_DISABLED_MESSAGE,
        )
    token = credentials.credentials if credentials and credentials.scheme.lower() == "bearer" else ""
    username = decode_superadmin_token(token) if token else None
    if not username:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Avval tizimga kiring.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return username


def _require_account(db: Session, user_uid: str, confirm_email: str) -> User:
    user = db.scalar(select(User).where(User.uid == user_uid).with_for_update())
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")
    if not secrets.compare_digest(user.email.lower(), confirm_email.strip().lower()):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Confirmation email does not match")
    return user


def _account_counts(db: Session, user_uid: str) -> tuple[int, int, int]:
    records = db.scalar(select(func.count(UserRecord.id)).where(UserRecord.user_uid == user_uid)) or 0
    devices = db.scalar(select(func.count(Device.id)).where(Device.user_uid == user_uid)) or 0
    batches = db.scalar(select(func.count(SyncBatch.id)).where(SyncBatch.user_uid == user_uid)) or 0
    return int(records), int(devices), int(batches)


@router.get("/availability", response_model=SuperadminAvailabilityOut)
def availability() -> SuperadminAvailabilityOut:
    """Whether the panel can be used at all.

    Without this the only way to discover that SUPERADMIN_PASSWORD was never
    set on the server is to type a password and be told the login failed --
    which reads like a wrong password rather than a server that was never
    configured. Deliberately unauthenticated: it reveals nothing beyond
    whether the feature is switched on.
    """
    settings = get_settings()
    return SuperadminAvailabilityOut(
        enabled=bool(settings.superadmin_password),
        message="" if settings.superadmin_password else _DISABLED_MESSAGE,
    )


@page_router.get("/superadmin", response_class=HTMLResponse)
def superadmin_page():
    return HTMLResponse(
        _PAGE_PATH.read_text(encoding="utf-8"),
        headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": (
                "default-src 'self'; script-src 'self'; style-src 'self'; "
                "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
            ),
            "Referrer-Policy": "no-referrer",
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
        },
    )


@router.post("/login", response_model=SuperadminTokenOut)
def login(payload: SuperadminLoginRequest, request: Request):
    settings = get_settings()
    if not settings.superadmin_password:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=_DISABLED_MESSAGE,
        )

    client_key = _client_key(request)
    _check_login_limit(client_key)
    valid_username = secrets.compare_digest(payload.username, settings.superadmin_username)
    valid_password = secrets.compare_digest(payload.password, settings.superadmin_password)
    if not (valid_username and valid_password):
        _record_login_failure(client_key)
        time.sleep(0.2)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Login yoki parol noto'g'ri.")

    _clear_login_failures(client_key)
    expires_in = max(1, settings.superadmin_token_expire_minutes) * 60
    return SuperadminTokenOut(
        access_token=create_superadmin_token(settings.superadmin_username),
        expires_in_seconds=expires_in,
    )


@router.get("/accounts", response_model=SuperadminOverviewOut)
def accounts(_admin: str = Depends(get_superadmin), db: Session = Depends(get_db)):
    record_count = (
        select(func.count(UserRecord.id)).where(UserRecord.user_uid == User.uid).correlate(User).scalar_subquery()
    )
    deleted_count = (
        select(func.count(UserRecord.id))
        .where(UserRecord.user_uid == User.uid, UserRecord.deleted_at.is_not(None))
        .correlate(User)
        .scalar_subquery()
    )
    device_count = select(func.count(Device.id)).where(Device.user_uid == User.uid).correlate(User).scalar_subquery()
    batch_count = (
        select(func.count(SyncBatch.id)).where(SyncBatch.user_uid == User.uid).correlate(User).scalar_subquery()
    )
    last_activity = (
        select(func.max(UserRecord.updated_at)).where(UserRecord.user_uid == User.uid).correlate(User).scalar_subquery()
    )
    rows = db.execute(
        select(User, record_count, deleted_count, device_count, batch_count, last_activity)
        .order_by(User.created_at.desc(), User.id.desc())
    ).all()

    items = [
        SuperadminAccountOut(
            user_uid=user.uid,
            email=user.email,
            display_name=user.display_name,
            role=user.role,
            is_active=user.is_active,
            is_verified=user.email_verified_at is not None,
            created_at=user.created_at,
            updated_at=user.updated_at,
            records_count=int(records or 0),
            deleted_records_count=int(deleted or 0),
            devices_count=int(devices or 0),
            sync_batches_count=int(batches or 0),
            last_activity_at=last_seen,
        )
        for user, records, deleted, devices, batches, last_seen in rows
    ]
    return SuperadminOverviewOut(
        accounts=items,
        total_accounts=len(items),
        active_accounts=sum(1 for item in items if item.is_active),
        total_records=sum(item.records_count for item in items),
        total_devices=sum(item.devices_count for item in items),
    )


@router.post("/accounts/{user_uid}/status", response_model=SuperadminActionOut)
def set_account_status(
    user_uid: str,
    payload: SuperadminStatusRequest,
    _admin: str = Depends(get_superadmin),
    db: Session = Depends(get_db),
):
    user = _require_account(db, user_uid, payload.confirm_email)
    user.is_active = payload.is_active
    db.commit()
    state = "activated" if payload.is_active else "deactivated"
    return SuperadminActionOut(message=f"Account {state}")


@router.post("/accounts/{user_uid}/clear-data", response_model=SuperadminActionOut)
def clear_account_data(
    user_uid: str,
    payload: SuperadminConfirmRequest,
    _admin: str = Depends(get_superadmin),
    db: Session = Depends(get_db),
):
    user = _require_account(db, user_uid, payload.confirm_email)
    removed_records, removed_devices, removed_batches = _account_counts(db, user.uid)

    db.execute(delete(SyncBatch).where(SyncBatch.user_uid == user.uid))
    db.execute(delete(Device).where(Device.user_uid == user.uid))
    db.execute(delete(UserRecord).where(UserRecord.user_uid == user.uid))

    now = datetime.now(timezone.utc)
    meta = db.scalar(select(SyncMeta).where(SyncMeta.user_uid == user.uid).with_for_update())
    if meta is None:
        meta = SyncMeta(user_uid=user.uid, generation=1, purge_generation=1)
        db.add(meta)
    else:
        meta.generation = int(meta.generation or 0) + 1
        meta.purge_generation = meta.generation
    meta.purge_requested_at = now
    meta.last_change_at = now
    meta.last_device_key = "superadmin"
    meta.last_tables = sorted(ALLOWED_SYNC_TABLES)
    generation = int(meta.generation)
    db.commit()

    broker.publish(
        user.uid,
        {
            "type": "change",
            "generation": generation,
            "purge_generation": generation,
            "purge_requested_at": now.isoformat(),
            "tables": sorted(ALLOWED_SYNC_TABLES),
            "device_key": "superadmin",
            "server_time": now.isoformat(),
        },
    )
    return SuperadminActionOut(
        message="Server and connected desktop data cleared; the account was preserved",
        removed_records=removed_records,
        removed_devices=removed_devices,
        removed_batches=removed_batches,
    )


@router.post("/accounts/{user_uid}/delete", response_model=SuperadminActionOut)
def delete_account(
    user_uid: str,
    payload: SuperadminConfirmRequest,
    _admin: str = Depends(get_superadmin),
    db: Session = Depends(get_db),
):
    user = _require_account(db, user_uid, payload.confirm_email)
    removed_records, removed_devices, removed_batches = _account_counts(db, user.uid)
    user_id = user.id
    email = user.email
    meta = db.scalar(select(SyncMeta).where(SyncMeta.user_uid == user.uid).with_for_update())
    generation = int(meta.generation or 0) + 1 if meta else 1
    now = datetime.now(timezone.utc)

    db.execute(delete(GoogleOAuthSession).where(GoogleOAuthSession.user_id == user_id))
    db.execute(delete(User).where(User.id == user_id))
    db.commit()
    broker.publish(
        user_uid,
        {
            "type": "change",
            "generation": generation,
            "purge_generation": generation,
            "purge_requested_at": now.isoformat(),
            "tables": sorted(ALLOWED_SYNC_TABLES),
            "device_key": "superadmin",
            "server_time": datetime.now(timezone.utc).isoformat(),
        },
    )
    return SuperadminActionOut(
        message=f"Account {email} permanently deleted",
        removed_records=removed_records,
        removed_devices=removed_devices,
        removed_batches=removed_batches,
    )


# --- container logs -------------------------------------------------------
# Docker keeps only the last few megabytes per container, and reading even that
# means an SSH session. The collector copies every line into a monthly archive;
# these endpoints are how the panel shows it - live at the bottom, and
# scrollable all the way back through the months before it.


def _stream_superadmin(token: str | None, authorization: str | None) -> str:
    """EventSource cannot send an Authorization header, so accept a query token.

    The same shape the device event stream uses. The token is short-lived and
    the panel already holds it, so this is no easier to reach than any other
    superadmin call.
    """
    settings = get_settings()
    if not settings.superadmin_password:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=_DISABLED_MESSAGE)
    bearer_token = token
    if not bearer_token and authorization and authorization.lower().startswith("bearer "):
        bearer_token = authorization.split(" ", 1)[1].strip()
    username = decode_superadmin_token(bearer_token) if bearer_token else None
    if not username:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Avval tizimga kiring.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return username


@router.get("/logs/months", response_model=LogMonthsOut)
def log_months(_superadmin: str = Depends(get_superadmin)) -> LogMonthsOut:
    """Which months are on disk, and which containers were seen recently."""
    return LogMonthsOut(
        months=[LogMonthOut(**item) for item in log_archive.list_months()],
        current=log_archive.month_key(),
        containers=log_archive.known_containers(),
    )


@router.get("/logs", response_model=LogPageOut)
def log_page(
    month: str | None = Query(default=None),
    before: int | None = Query(default=None, ge=0),
    limit: int = Query(default=300, ge=1, le=2000),
    container: str | None = Query(default=None, max_length=120),
    q: str | None = Query(default=None, max_length=200),
    _superadmin: str = Depends(get_superadmin),
) -> LogPageOut:
    """One screenful of a month, oldest first; ``before`` walks further back."""
    key = month or log_archive.month_key()
    if not log_archive.is_month_key(key):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Oy formati: YYYY-MM",
        )
    page = log_archive.read_page(key, limit=limit, before=before, container=container, query=q)
    return LogPageOut(
        month=key,
        lines=[LogLineOut(**line) for line in page["lines"]],
        next_before=page.get("next_before"),
        has_more=bool(page.get("has_more")),
        offset=int(page.get("size") or 0),
    )


@router.get("/logs/stream", include_in_schema=False)
async def log_stream(
    request: Request,
    token: str | None = Query(default=None),
    authorization: str | None = Header(default=None),
    offset: int | None = Query(default=None, ge=0),
    container: str | None = Query(default=None, max_length=120),
):
    """Live tail of the current month, as Server-Sent Events."""
    _stream_superadmin(token, authorization)
    settings = get_settings()
    start = log_archive.current_size() if offset is None else int(offset)
    interval = max(1, int(settings.log_poll_seconds))

    async def stream():
        position = start
        quiet_seconds = 0
        yield "event: hello\ndata: " + json.dumps({"offset": position}) + "\n\n"
        while True:
            if await request.is_disconnected():
                break
            entries, position = await run_in_threadpool(log_archive.read_since, position)
            if container:
                entries = [item for item in entries if item.get("c") == container]
            if entries:
                payload = json.dumps({"offset": position, "lines": entries}, ensure_ascii=False)
                yield "event: lines\ndata: " + payload + "\n\n"
                quiet_seconds = 0
            else:
                quiet_seconds += interval
                if quiet_seconds >= 20:
                    # nginx and ngrok both drop a stream that goes quiet.
                    yield ": ping\n\n"
                    quiet_seconds = 0
            await asyncio.sleep(interval)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-store",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
