import asyncio
import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import and_, delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.database import SessionLocal, get_db
from app.deps import get_current_user
from app.events import broker
from app.models import Device, SyncBatch, SyncMeta, User, UserRecord
from app.releases import release_payload
from app.schemas import (
    RejectedRecordOut,
    ALLOWED_SYNC_TABLES,
    DeviceIn,
    PullResponse,
    PushRequest,
    PushResponse,
    RecordIn,
    RecordOut,
    ResetResponse,
    SummaryItem,
    SummaryResponse,
    SyncStateOut,
)
from app.security import decode_access_token


router = APIRouter(prefix="/sync", tags=["sync"])

# How often a streaming client falls back to reading sync_meta directly. The
# in-process broker delivers instantly; this only covers multi-worker setups.
GENERATION_POLL_SECONDS = 2.0
# Keepalive comment interval. ngrok and nginx both drop idle tunnels well before
# this, so the stream must never go quiet for longer.
KEEPALIVE_SECONDS = 20.0


def _touch_device(db: Session, user: User, payload: DeviceIn | None) -> Device | None:
    if payload is None:
        return None
    device = db.scalar(
        select(Device).where(Device.user_uid == user.uid, Device.device_key == payload.device_key)
    )
    if device is None:
        device = Device(user_id=user.id, user_uid=user.uid, device_key=payload.device_key, name=payload.name)
        db.add(device)
    else:
        device.user_id = user.id
        device.user_uid = user.uid
        device.name = payload.name or device.name
    device.last_seen_at = datetime.now(timezone.utc)
    db.flush()
    return device


def _current_generation(db: Session, user_uid: str) -> int:
    value = db.scalar(select(SyncMeta.generation).where(SyncMeta.user_uid == user_uid))
    return int(value or 0)


def _current_purge_generation(db: Session, user_uid: str) -> int:
    value = db.scalar(select(SyncMeta.purge_generation).where(SyncMeta.user_uid == user_uid))
    return int(value or 0)


def _assert_purge_generation(db: Session, user: User, applied: int | None) -> None:
    """Never let an offline snapshot resurrect data erased by superadmin."""
    current = _current_purge_generation(db, user.uid)
    if current and applied != current:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "remote_purge_required",
                "message": "Account data was erased by superadmin",
                "purge_generation": current,
                "applied_purge_generation": applied,
            },
        )


def _reserve_generation(db: Session, user: User) -> int:
    """Take the next number in the account's change history.

    Every writer advances this row, so Postgres serialises them: whoever takes
    the lower number also commits first. That is what makes the number usable as
    a download position -- a reader that can see number N can see everything
    below it too, which a wall clock could never promise.
    """
    stmt = insert(SyncMeta).values(user_uid=user.uid, generation=1)
    stmt = stmt.on_conflict_do_update(
        index_elements=[SyncMeta.user_uid],
        set_={"generation": SyncMeta.generation + 1},
    ).returning(SyncMeta.generation)
    return int(db.execute(stmt).scalar_one())


def _describe_generation(
    db: Session,
    user: User,
    device_key: str | None,
    tables,
    generation: int,
) -> dict:
    """Record who made the change and hand back the event to publish."""
    table_list = sorted({name for name in (tables or []) if name})
    now = datetime.now(timezone.utc)
    db.execute(
        update(SyncMeta)
        .where(SyncMeta.user_uid == user.uid)
        .values(last_change_at=now, last_device_key=device_key, last_tables=table_list)
    )
    row = db.execute(
        select(SyncMeta.purge_generation, SyncMeta.purge_requested_at).where(
            SyncMeta.user_uid == user.uid
        )
    ).one()
    purge_generation, purge_requested_at = row
    return {
        "type": "change",
        "generation": int(generation),
        "purge_generation": int(purge_generation or 0),
        "purge_requested_at": purge_requested_at.isoformat() if purge_requested_at else None,
        "tables": table_list,
        "device_key": device_key,
        "server_time": now.isoformat(),
        # Devices download by this number, not by the clock.
        "cursor": int(generation),
    }


def _bump_generation(db: Session, user: User, device_key: str | None, tables) -> dict:
    """Advance the counter and describe the change, for writers that do both."""
    generation = _reserve_generation(db, user)
    return _describe_generation(db, user, device_key, tables, generation)


def _publish(user_uid: str, payload: dict | None) -> None:
    if payload:
        broker.publish(user_uid, payload)


def _assert_generation(db: Session, user: User, expected: int | None) -> None:
    """Reject a push that was prepared against stale server state (Anki-style)."""
    if expected is None:
        return
    current = _current_generation(db, user.uid)
    if current != expected:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "sync_conflict",
                "message": "Server data changed on another device",
                "server_generation": current,
                "expected_generation": expected,
            },
        )


def _upsert_record(db: Session, user: User, record: RecordIn, change_seq: int) -> bool:
    """Store one row. Returns False when the sender was working from a stale copy.

    A device that sends ``expected_version`` is saying "I am changing the row
    as I last saw it". If the stored row has moved on since, the change is
    refused rather than applied over whatever happened in between -- a product
    edited from a screen that still showed yesterday's stock would otherwise
    undo the sales made in the meantime.

    A row the server has never seen carries no version to disagree with, so it
    is always inserted. That is why a sale can never be rejected: it is a new
    row, not a change to an existing one.
    """
    stmt = insert(UserRecord).values(
        user_id=user.id,
        user_uid=user.uid,
        table_name=record.table_name,
        local_id=record.local_id,
        data=record.data,
        local_updated_at=record.local_updated_at,
        deleted_at=record.deleted_at,
        source_device_key=record.source_device_key,
        sync_version=1,
        change_seq=change_seq,
    )
    updates = {
        "user_id": user.id,
        "user_uid": user.uid,
        "data": stmt.excluded.data,
        "local_updated_at": stmt.excluded.local_updated_at,
        "deleted_at": stmt.excluded.deleted_at,
        "source_device_key": stmt.excluded.source_device_key,
        "sync_version": UserRecord.sync_version + 1,
        "change_seq": change_seq,
        "updated_at": func.now(),
    }
    if record.expected_version is None:
        db.execute(stmt.on_conflict_do_update(
            constraint="uq_user_records_user_uid_table_local",
            set_=updates,
        ))
        return True
    # The condition sits on the DO UPDATE, so a row that has moved on is
    # neither inserted nor updated and RETURNING gives back nothing.
    guarded = stmt.on_conflict_do_update(
        constraint="uq_user_records_user_uid_table_local",
        set_=updates,
        where=UserRecord.sync_version == record.expected_version,
    )
    written = db.execute(guarded.returning(UserRecord.id)).first()
    return written is not None


def _record_version(db: Session, user: User, record: RecordIn) -> int | None:
    return db.scalar(
        select(UserRecord.sync_version).where(
            UserRecord.user_uid == user.uid,
            UserRecord.table_name == record.table_name,
            UserRecord.local_id == record.local_id,
        )
    )


@router.post("/push", response_model=PushResponse)
def push(payload: PushRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _assert_purge_generation(db, current_user, payload.applied_purge_generation)
    _assert_generation(db, current_user, payload.expected_generation)
    device = _touch_device(db, current_user, payload.device)
    # Reserved before anything is written so every row of this push carries the
    # number, and so that concurrent pushes queue up behind each other here
    # rather than interleaving their rows in the history.
    generation = _reserve_generation(db, current_user)
    rejected: list[RejectedRecordOut] = []
    accepted: list[RecordIn] = []
    for record in payload.records:
        if _upsert_record(db, current_user, record, generation):
            accepted.append(record)
            continue
        # Partial success rather than a 409: the rest of the batch is perfectly
        # good, and refusing all of it would turn one stale row into a failed
        # sale.
        rejected.append(RejectedRecordOut(
            table_name=record.table_name,
            local_id=record.local_id,
            expected_version=record.expected_version,
            server_version=_record_version(db, current_user, record),
        ))
    batch = SyncBatch(
        user_id=current_user.id,
        user_uid=current_user.uid,
        device_id=device.id if device else None,
        direction="push",
        records_count=len(accepted),
        note=payload.note,
    )
    db.add(batch)
    device_key = payload.device.device_key if payload.device else None
    event = _describe_generation(
        db,
        current_user,
        device_key,
        {record.table_name for record in accepted},
        generation,
    )
    db.commit()
    db.refresh(batch)
    _publish(current_user.uid, event)
    return PushResponse(
        saved=len(accepted),
        batch_id=batch.id,
        generation=event["generation"],
        rejected=rejected,
    )


@router.put("/tables/{table_name}/rows", response_model=PushResponse)
def upsert_table_rows(
    table_name: str,
    records: list[RecordIn],
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    x_device_key: str | None = Header(default=None, alias="X-Device-Key"),
    x_purge_generation: int | None = Header(default=None, alias="X-Purge-Generation"),
):
    _assert_purge_generation(db, current_user, x_purge_generation)
    if table_name not in ALLOWED_SYNC_TABLES:
        raise HTTPException(status_code=422, detail="Unsupported sync table")
    if len(records) > 1000:
        raise HTTPException(status_code=413, detail="Sync batch is too large")
    normalized = [record.model_copy(update={"table_name": table_name}) for record in records]
    generation = _reserve_generation(db, current_user)
    for record in normalized:
        _upsert_record(db, current_user, record, generation)
    batch = SyncBatch(user_id=current_user.id, user_uid=current_user.uid, direction="push", records_count=len(normalized), note=f"table:{table_name}")
    db.add(batch)
    event = _describe_generation(db, current_user, x_device_key, {table_name}, generation)
    db.commit()
    db.refresh(batch)
    _publish(current_user.uid, event)
    return PushResponse(saved=len(normalized), batch_id=batch.id, generation=event["generation"])


@router.get("/pull", response_model=PullResponse)
def pull(
    since: datetime | None = Query(default=None),
    since_seq: int | None = Query(default=None, ge=0),
    table_name: str | None = Query(default=None),
    include_deleted: bool = True,
    limit: int | None = Query(default=None, ge=1, le=5000),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    meta = db.scalar(select(SyncMeta).where(SyncMeta.user_uid == current_user.uid))
    generation = int(meta.generation or 0) if meta else 0
    purge_generation = int(meta.purge_generation or 0) if meta else 0
    purge_requested_at = meta.purge_requested_at if meta else None
    stmt = select(UserRecord).where(UserRecord.user_uid == current_user.uid)
    # A caller that knows about change numbers never asks by clock again. The
    # timestamp filter stays only so an old desktop build keeps working.
    if since_seq is not None:
        stmt = stmt.where(UserRecord.change_seq > since_seq)
    elif since is not None:
        stmt = stmt.where(UserRecord.updated_at > since)
    if table_name:
        stmt = stmt.where(UserRecord.table_name == table_name)
    if not include_deleted:
        stmt = stmt.where(UserRecord.deleted_at.is_(None))
    ordered = stmt.order_by(UserRecord.change_seq, UserRecord.id).offset(offset)

    def answer(page, has_more: bool) -> PullResponse:
        return PullResponse(
            records=page,
            server_time=datetime.now(timezone.utc),
            generation=generation,
            purge_generation=purge_generation,
            purge_requested_at=purge_requested_at,
            has_more=has_more,
            next_offset=offset + len(page) if has_more else None,
            # Only what was actually handed over. A caller that moved its
            # marker to anything else could step over a row it never received.
            cursor=max((int(row.change_seq or 0) for row in page), default=0),
        )

    if limit is None:
        return answer(db.scalars(ordered).all(), False)
    records = db.scalars(ordered.limit(limit + 1)).all()
    has_more = len(records) > limit
    return answer(records[:limit], has_more)


@router.delete("/tables/{table_name}/rows/{local_id}", response_model=RecordOut)
def mark_deleted(
    table_name: str,
    local_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    x_device_key: str | None = Header(default=None, alias="X-Device-Key"),
    x_purge_generation: int | None = Header(default=None, alias="X-Purge-Generation"),
):
    _assert_purge_generation(db, current_user, x_purge_generation)
    now_text = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    record = db.scalar(
        select(UserRecord).where(
            and_(
                UserRecord.user_uid == current_user.uid,
                UserRecord.table_name == table_name,
                UserRecord.local_id == local_id,
            )
        )
    )
    generation = _reserve_generation(db, current_user)
    if record is None:
        record = UserRecord(
            user_id=current_user.id,
            user_uid=current_user.uid,
            table_name=table_name,
            local_id=local_id,
            data={},
            deleted_at=now_text,
            change_seq=generation,
        )
        db.add(record)
    else:
        record.deleted_at = now_text
        record.sync_version += 1
        record.change_seq = generation
    event = _describe_generation(db, current_user, x_device_key, {table_name}, generation)
    db.commit()
    db.refresh(record)
    _publish(current_user.uid, event)
    return record


@router.post("/reset", response_model=ResetResponse)
def reset(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    x_device_key: str | None = Header(default=None, alias="X-Device-Key"),
    x_purge_generation: int | None = Header(default=None, alias="X-Purge-Generation"),
):
    """Drop every stored record for the account.

    Used by the desktop client's "upload mine, discard the server copy" branch of
    the conflict dialog, right before it streams a full snapshot back up.
    """
    _assert_purge_generation(db, current_user, x_purge_generation)
    removed = db.execute(
        delete(UserRecord).where(UserRecord.user_uid == current_user.uid)
    ).rowcount or 0
    event = _bump_generation(db, current_user, x_device_key, ALLOWED_SYNC_TABLES)
    db.commit()
    _publish(current_user.uid, event)
    return ResetResponse(removed=int(removed), generation=event["generation"])


@router.get("/state", response_model=SyncStateOut)
def state(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    meta = db.scalar(select(SyncMeta).where(SyncMeta.user_uid == current_user.uid))
    total = db.scalar(
        select(func.count(UserRecord.id)).where(UserRecord.user_uid == current_user.uid)
    ) or 0
    cursor = db.scalar(
        select(func.max(UserRecord.change_seq)).where(UserRecord.user_uid == current_user.uid)
    ) or 0
    return SyncStateOut(
        generation=int(meta.generation) if meta else 0,
        purge_generation=int(meta.purge_generation or 0) if meta else 0,
        purge_requested_at=meta.purge_requested_at if meta else None,
        last_change_at=meta.last_change_at if meta else None,
        last_device_key=meta.last_device_key if meta else None,
        last_tables=list(meta.last_tables or []) if meta else [],
        records_count=int(total),
        cursor=int(cursor),
        server_time=datetime.now(timezone.utc),
    )


def _resolve_stream_user(token: str | None) -> User:
    """Authenticate a streaming client without holding a session for the stream."""
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing token")
    subject = decode_access_token(token)
    if not subject:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    db = SessionLocal()
    try:
        if subject.isdigit():
            user = db.scalar(select(User).where(User.id == int(subject), User.is_active == True))  # noqa: E712
        else:
            user = db.scalar(select(User).where(User.uid == subject, User.is_active == True))  # noqa: E712
        if not user:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
        db.expunge(user)
        return user
    finally:
        db.close()


def _read_meta(user_uid: str) -> dict:
    db = SessionLocal()
    try:
        meta = db.scalar(select(SyncMeta).where(SyncMeta.user_uid == user_uid))
        if meta is None:
            return {
                "generation": 0,
                "purge_generation": 0,
                "purge_requested_at": None,
                "tables": [],
                "device_key": None,
                "last_change_at": None,
            }
        return {
            "generation": int(meta.generation or 0),
            "purge_generation": int(meta.purge_generation or 0),
            "purge_requested_at": meta.purge_requested_at.isoformat() if meta.purge_requested_at else None,
            "tables": list(meta.last_tables or []),
            "device_key": meta.last_device_key,
            "last_change_at": meta.last_change_at.isoformat() if meta.last_change_at else None,
        }
    finally:
        db.close()


def _read_release() -> dict | None:
    """Newest desktop build, so a device learns about it the moment it connects."""
    db = SessionLocal()
    try:
        return release_payload(db)
    finally:
        db.close()


def _sse(event_name: str, data: dict) -> bytes:
    return f"event: {event_name}\ndata: {json.dumps(data, default=str)}\n\n".encode("utf-8")


@router.get("/events")
async def events(
    request: Request,
    token: str | None = Query(default=None),
    authorization: str | None = Header(default=None),
    since_generation: int | None = Query(default=None, ge=0),
):
    """Server-Sent Events stream of sync changes for the caller's account.

    Emits ``hello`` once on connect, then a ``change`` event every time any device
    of this account writes data, plus a ``ping`` comment on an interval so proxies
    keep the tunnel open.
    """
    bearer = token
    if not bearer and authorization and authorization.lower().startswith("bearer "):
        bearer = authorization.split(" ", 1)[1].strip()
    user = await run_in_threadpool(_resolve_stream_user, bearer)
    user_uid = user.uid

    broker.bind_loop(asyncio.get_running_loop())
    initial = await run_in_threadpool(_read_meta, user_uid)
    release = await run_in_threadpool(_read_release)
    last_generation = int(initial["generation"])

    async def stream():
        nonlocal last_generation
        queue = broker.subscribe(user_uid)
        loop = asyncio.get_running_loop()
        last_keepalive = loop.time()
        try:
            yield _sse("hello", {
                "generation": last_generation,
                "purge_generation": int(initial.get("purge_generation") or 0),
                "purge_requested_at": initial.get("purge_requested_at"),
                "tables": initial["tables"],
                "device_key": initial["device_key"],
                "server_time": datetime.now(timezone.utc).isoformat(),
                "release": release,
            })
            # A client that reconnects after a dropped tunnel tells us where it
            # left off, so no change is silently lost across a reconnect.
            if since_generation is not None and last_generation > since_generation:
                yield _sse("change", {
                    "generation": last_generation,
                    "purge_generation": int(initial.get("purge_generation") or 0),
                    "purge_requested_at": initial.get("purge_requested_at"),
                    "tables": initial["tables"],
                    "device_key": initial["device_key"],
                    "server_time": datetime.now(timezone.utc).isoformat(),
                    "resumed": True,
                })

            while True:
                if await request.is_disconnected():
                    break
                payload = None
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=GENERATION_POLL_SECONDS)
                except asyncio.TimeoutError:
                    fresh = await run_in_threadpool(_read_meta, user_uid)
                    if int(fresh["generation"]) > last_generation:
                        payload = {
                            "type": "change",
                            "generation": int(fresh["generation"]),
                            "purge_generation": int(fresh.get("purge_generation") or 0),
                            "purge_requested_at": fresh.get("purge_requested_at"),
                            "tables": fresh["tables"],
                            "device_key": fresh["device_key"],
                            "server_time": datetime.now(timezone.utc).isoformat(),
                        }

                if payload and payload.get("type") == "release":
                    # Fleet-wide news rather than account data: pass it straight
                    # through, it has nothing to do with the sync counter.
                    yield _sse("release", {
                        "tag": payload.get("tag"),
                        "latest_version": payload.get("latest_version"),
                        "name": payload.get("name"),
                        "published_at": payload.get("published_at"),
                    })
                    last_keepalive = loop.time()
                    continue

                if payload and int(payload.get("generation", 0)) > last_generation:
                    last_generation = int(payload["generation"])
                    yield _sse("change", {
                        "generation": last_generation,
                        "purge_generation": int(payload.get("purge_generation") or 0),
                        "purge_requested_at": payload.get("purge_requested_at"),
                        "tables": payload.get("tables") or [],
                        "device_key": payload.get("device_key"),
                        "server_time": payload.get("server_time"),
                    })
                    last_keepalive = loop.time()
                    continue

                if loop.time() - last_keepalive >= KEEPALIVE_SECONDS:
                    last_keepalive = loop.time()
                    yield _sse("ping", {"generation": last_generation})
        except asyncio.CancelledError:
            raise
        finally:
            broker.unsubscribe(user_uid, queue)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            # Stops nginx from buffering the stream into uselessness.
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/summary", response_model=SummaryResponse)
def summary(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.execute(
        select(
            UserRecord.table_name,
            func.count(UserRecord.id),
            func.count(UserRecord.deleted_at),
            func.max(UserRecord.updated_at),
        )
        .where(UserRecord.user_uid == current_user.uid)
        .group_by(UserRecord.table_name)
        .order_by(UserRecord.table_name)
    ).all()
    return SummaryResponse(
        user_id=current_user.id,
        user_uid=current_user.uid,
        tables=[
            SummaryItem(table_name=row[0], records_count=row[1], deleted_count=row[2], last_updated_at=row[3])
            for row in rows
        ],
    )
