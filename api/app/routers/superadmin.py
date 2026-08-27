from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path
import secrets
import threading
import time

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.events import broker
from app.models import Device, GoogleOAuthSession, SyncBatch, SyncMeta, User, UserRecord
from app.schemas import (
    ALLOWED_SYNC_TABLES,
    SuperadminAccountOut,
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
                detail="Too many login attempts. Try again later.",
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
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Superadmin is disabled")
    token = credentials.credentials if credentials and credentials.scheme.lower() == "bearer" else ""
    username = decode_superadmin_token(token) if token else None
    if not username:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Superadmin authentication is required",
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
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Superadmin is disabled")

    client_key = _client_key(request)
    _check_login_limit(client_key)
    valid_username = secrets.compare_digest(payload.username, settings.superadmin_username)
    valid_password = secrets.compare_digest(payload.password, settings.superadmin_password)
    if not (valid_username and valid_password):
        _record_login_failure(client_key)
        time.sleep(0.2)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid username or password")

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
