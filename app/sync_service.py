"""Sync orchestration for the desktop client.

Conflict handling follows Anki's model: the server keeps a single change counter
per account (``generation``). Whenever two devices have both moved on from the
same counter value, the user is asked to pick one side wholesale rather than the
app silently merging. Unlike Anki, the losing side is backed up to disk first, so
a wrong click is recoverable.
"""

import api_client
import database as db


class SyncError(Exception):
    pass


class SyncConflict(Exception):
    """Both the local database and the server changed since the last sync."""

    def __init__(self, info):
        super().__init__("Sync conflict")
        self.info = dict(info or {})


def _token_for_user(user):
    token = (user or {}).get("api_access_token")
    if token:
        return token
    token = db.get_user_api_token((user or {}).get("id"))
    if token:
        return token
    raise SyncError("Online account token topilmadi. Email va parol orqali qayta kiring.")


def get_server_state(user):
    token = _token_for_user(user)
    state = api_client.get_sync_state(token)
    generation = int(state.get("generation") or 0)
    db.mark_remote_change(
        generation,
        tables=state.get("last_tables") or [],
        device_key=state.get("last_device_key"),
        changed_at=state.get("last_change_at"),
    )
    return state


def describe_sync(user, server_state=None):
    """Compare local and server state without changing either of them."""
    state = server_state if server_state is not None else get_server_state(user)
    status = db.get_sync_status()
    known = db.get_sync_generation()
    server_generation = int(state.get("generation") or 0)
    local_pending = int(status.get("pending_change_count") or 0)
    server_ahead = server_generation > known
    return {
        "conflict": bool(local_pending > 0 and server_ahead),
        "server_ahead": server_ahead,
        "local_pending": local_pending,
        "local_records": db.count_sync_records(),
        "server_records": int(state.get("records_count") or 0),
        "server_generation": server_generation,
        "known_generation": known,
        "server_changed_at": state.get("last_change_at"),
        "server_device_key": state.get("last_device_key"),
        "server_tables": list(state.get("last_tables") or []),
        "own_device_key": db.get_sync_device_key(),
    }


def _apply_generation(generation):
    if generation:
        db.set_sync_generation(generation)
        db.clear_remote_change()


def push_local_changes(user, batch_size=1000, incremental=True, force=False):
    token = _token_for_user(user)
    records, watermark = db.export_sync_records(incremental=incremental, with_watermark=True)
    device_key = db.get_sync_device_key()
    expected = None if force else db.get_sync_generation()

    if not records:
        # Nothing to send. Deliberately do NOT adopt the server's counter here:
        # we have not seen its data yet, and pretending otherwise would hide a
        # pending download.
        db.mark_sync_pushed(**watermark)
        db.mark_server_reseed_complete()
        return {"sent": 0, "saved": 0, "batch_id": None, "generation": db.get_sync_generation()}

    total_saved = 0
    last_batch_id = None
    # `guard` is the value each chunk asserts against; `seen` is the counter the
    # server reported back. They differ once we start forcing.
    guard = expected
    seen = None
    try:
        for i in range(0, len(records), batch_size):
            chunk = records[i:i + batch_size]
            result = api_client.push_sync_records(
                token,
                chunk,
                device_key=device_key,
                note=f"desktop snapshot ({i + len(chunk)}/{len(records)})",
                timeout=60,
                expected_generation=guard,
            )
            total_saved += result.get("saved", 0)
            last_batch_id = result.get("batch_id")
            returned = result.get("generation")
            if returned:
                seen = int(returned)
            # Our own write moved the counter; the next chunk must expect that.
            guard = seen if guard is not None else None
    except api_client.SyncConflictError as exc:
        raise SyncConflict(describe_sync(user)) from exc

    generation = seen
    if generation is None:
        # Older server, or a response without the counter: ask for it, but never
        # fail an otherwise successful upload just because this lookup didn't.
        try:
            generation = int(api_client.get_sync_state(token).get("generation") or 0)
        except api_client.ApiClientError:
            generation = None
    # Clear only what this push carried: anything queued while it was in flight
    # has a higher seq and survives for the next round.
    db.mark_sync_pushed(**watermark)
    db.mark_server_reseed_complete()
    if generation is not None:
        _apply_generation(generation)
    return {
        "sent": len(records),
        "saved": total_saved,
        "batch_id": last_batch_id,
        "generation": generation if generation is not None else db.get_sync_generation(),
    }


def pull_server_changes(user, table_name=None):
    token = _token_for_user(user)
    result = api_client.pull_sync_records(
        token,
        table_name=table_name,
        include_deleted=True,
    )
    records = result.get("records", [])
    imported = db.import_sync_records(records)
    if table_name is None:
        db.mark_server_bootstrap_complete()
        _apply_generation(int(result.get("generation") or 0))
    return {
        "received": len(records),
        "imported": imported,
        "server_time": result.get("server_time"),
        "generation": result.get("generation"),
    }


def force_download(user):
    """Take the server copy and discard local changes (after backing them up)."""
    token = _token_for_user(user)
    backup_path = db.create_local_backup(tag="before_download")
    result = api_client.pull_sync_records(token, include_deleted=True)
    records = result.get("records", [])
    imported = db.replace_local_from_records(records)
    db.mark_server_bootstrap_complete()
    db.mark_server_reseed_complete()
    _apply_generation(int(result.get("generation") or 0))
    return {
        "direction": "download",
        "received": len(records),
        "imported": imported,
        "backup_path": backup_path,
        "generation": result.get("generation"),
    }


def force_upload(user):
    """Overwrite the server with our copy (after backing the server copy up)."""
    token = _token_for_user(user)
    device_key = db.get_sync_device_key()
    local_backup = db.create_local_backup(tag="before_upload")

    server_backup = None
    try:
        snapshot = api_client.pull_sync_records(token, include_deleted=True)
        server_backup = db.save_server_snapshot_backup(snapshot.get("records", []))
    except api_client.ApiClientError:
        # A snapshot we cannot read is not a reason to block the user's choice.
        server_backup = None

    api_client.reset_sync_records(token, device_key=device_key)
    db.mark_server_bootstrap_complete()
    result = push_local_changes(user, incremental=False, force=True)
    return {
        "direction": "upload",
        "sent": result.get("sent", 0),
        "saved": result.get("saved", 0),
        "backup_path": local_backup,
        "server_backup_path": server_backup,
        "generation": result.get("generation"),
    }


def refresh_account_assets(user):
    """Pull just the shared logo/asset table - never conflicts, always applied."""
    if db.has_pending_sync_for_table("account_assets"):
        return {"received": 0, "imported": 0, "skipped": "local_changes_pending"}
    return pull_server_changes(user, table_name="account_assets")


def synchronize_account_storage(user):
    if db.is_server_reseed_required():
        return {"direction": "push", **push_local_changes(user, force=True)}
    if db.is_server_bootstrap_required():
        return {"direction": "pull", **pull_server_changes(user)}
    return {"direction": "none"}
