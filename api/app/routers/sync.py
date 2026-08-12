from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.models import Device, SyncBatch, User, UserRecord
from app.schemas import DeviceIn, PullResponse, PushRequest, PushResponse, RecordIn, RecordOut, SummaryItem, SummaryResponse


router = APIRouter(prefix="/sync", tags=["sync"])


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


def _upsert_record(db: Session, user: User, record: RecordIn):
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
    )
    stmt = stmt.on_conflict_do_update(
        constraint="uq_user_records_user_uid_table_local",
        set_={
            "user_id": user.id,
            "user_uid": user.uid,
            "data": stmt.excluded.data,
            "local_updated_at": stmt.excluded.local_updated_at,
            "deleted_at": stmt.excluded.deleted_at,
            "source_device_key": stmt.excluded.source_device_key,
            "sync_version": UserRecord.sync_version + 1,
            "updated_at": func.now(),
        },
    )
    db.execute(stmt)


@router.post("/push", response_model=PushResponse)
def push(payload: PushRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    device = _touch_device(db, current_user, payload.device)
    for record in payload.records:
        _upsert_record(db, current_user, record)
    batch = SyncBatch(
        user_id=current_user.id,
        user_uid=current_user.uid,
        device_id=device.id if device else None,
        direction="push",
        records_count=len(payload.records),
        note=payload.note,
    )
    db.add(batch)
    db.commit()
    db.refresh(batch)
    return PushResponse(saved=len(payload.records), batch_id=batch.id)


@router.put("/tables/{table_name}/rows", response_model=PushResponse)
def upsert_table_rows(
    table_name: str,
    records: list[RecordIn],
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    normalized = [record.model_copy(update={"table_name": table_name}) for record in records]
    for record in normalized:
        _upsert_record(db, current_user, record)
    batch = SyncBatch(user_id=current_user.id, user_uid=current_user.uid, direction="push", records_count=len(normalized), note=f"table:{table_name}")
    db.add(batch)
    db.commit()
    db.refresh(batch)
    return PushResponse(saved=len(normalized), batch_id=batch.id)


@router.get("/pull", response_model=PullResponse)
def pull(
    since: datetime | None = Query(default=None),
    table_name: str | None = Query(default=None),
    include_deleted: bool = True,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    stmt = select(UserRecord).where(UserRecord.user_uid == current_user.uid)
    if since is not None:
        stmt = stmt.where(UserRecord.updated_at > since)
    if table_name:
        stmt = stmt.where(UserRecord.table_name == table_name)
    if not include_deleted:
        stmt = stmt.where(UserRecord.deleted_at.is_(None))
    records = db.scalars(stmt.order_by(UserRecord.updated_at, UserRecord.id)).all()
    return PullResponse(records=records, server_time=datetime.now(timezone.utc))


@router.delete("/tables/{table_name}/rows/{local_id}", response_model=RecordOut)
def mark_deleted(
    table_name: str,
    local_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
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
    if record is None:
        record = UserRecord(user_id=current_user.id, user_uid=current_user.uid, table_name=table_name, local_id=local_id, data={}, deleted_at=now_text)
        db.add(record)
    else:
        record.deleted_at = now_text
        record.sync_version += 1
    db.commit()
    db.refresh(record)
    return record


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
