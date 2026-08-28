"""Sync orchestration for the desktop client.

Conflict handling follows Anki's model: the server keeps a single change counter
per account (``generation``). Whenever two devices have both moved on from the
same counter value, the user is asked to pick one side wholesale rather than the
app silently merging. Unlike Anki, the losing side is backed up to disk first, so
a wrong click is recoverable.
"""

import functools
import threading
from datetime import datetime, timedelta

import api_client
import database as db


class SyncError(Exception):
    pass


class SyncConflict(Exception):
    """Both the local database and the server changed since the last sync."""

    def __init__(self, info):
        super().__init__("Sync conflict")
        self.info = dict(info or {})


# Two syncs must never run at once. The automatic engine and the sync button
# both reach these functions, and overlapping turns would send the same rows
# twice and race each other over the reading position.
_SYNC_LOCK = threading.RLock()


def _one_at_a_time(function):
    @functools.wraps(function)
    def guarded(*args, **kwargs):
        with _SYNC_LOCK:
            return function(*args, **kwargs)
    return guarded


def _token_for_user(user):
    token = (user or {}).get("api_access_token")
    if token:
        return token
    token = db.get_user_api_token((user or {}).get("id"))
    if token:
        return token
    raise SyncError("Online account token topilmadi. Email va parol orqali qayta kiring.")


def apply_server_control(payload):
    """Apply irreversible server control markers before ordinary sync work."""
    state = dict(payload or {})
    purge_generation = int(state.get("purge_generation") or 0)
    if purge_generation <= db.get_applied_purge_generation():
        return {"purged": False, "purge_generation": purge_generation}
    result = db.apply_remote_purge(
        purge_generation,
        server_generation=state.get("generation"),
    )
    return {
        "purged": bool(result.get("applied")),
        "purge_generation": purge_generation,
        "removed_records": int(result.get("removed_records") or 0),
        "removed_artifacts": int(result.get("removed_artifacts") or 0),
    }


def get_server_state(user):
    token = _token_for_user(user)
    state = api_client.get_sync_state(token)
    generation = int(state.get("generation") or 0)
    purge = apply_server_control(state)
    state["local_purge_applied"] = purge["purged"]
    if not purge["purged"]:
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


def _server_uses_uuid_identity(records):
    """Whether another upgraded device has already converted the account."""
    for record in records or []:
        table_name = record.get("table_name")
        if table_name not in db.UUID_KEYED_TABLES:
            continue
        data = record.get("data") or {}
        row_id = data.get("id") if isinstance(data, dict) else None
        row_id = row_id if row_id is not None else record.get("local_id")
        if db.is_row_uuid(row_id):
            return True
    return False


@_one_at_a_time
def reconcile_after_upgrade(user):
    """Settle this device against the server once, before any ordinary sync.

    A device that has been running a while keeps rows the other devices deleted
    long ago. Deletions travel as tombstones, and a tombstone this device never
    received leaves the row in place -- so an ordinary merge would upload it
    again and the deletion would undo itself.

    So the first sync after the upgrade is one-directional: whatever the server
    holds becomes this device's content, and the previous local copy is kept as
    a backup file rather than merged. If the server has nothing usable -- it is
    empty, or still on the old integer keys -- then this device is the only
    source there is, and it uploads instead.

    Returns None when there was nothing to settle.
    """
    if not db.is_upgrade_reconcile_required():
        return None

    token = _token_for_user(user)
    snapshot = api_client.pull_sync_records(token, include_deleted=True)
    purge = apply_server_control(snapshot)
    if purge["purged"]:
        db.mark_upgrade_reconcile_complete()
        db.mark_identity_reset_complete()
        return {"direction": "purge", "purged": True}

    records = snapshot.get("records", [])
    pending = int(db.get_sync_status().get("pending_change_count") or 0)
    if _server_uses_uuid_identity(records):
        result = force_download(user)
        db.mark_upgrade_reconcile_complete()
        db.mark_identity_reset_complete()
        result["adopted_server"] = True
        result["discarded_pending"] = pending
        return result

    result = force_upload(user)
    db.mark_upgrade_reconcile_complete()
    result["adopted_server"] = False
    return result


@_one_at_a_time
def push_local_changes(user, batch_size=1000, incremental=True, force=False, guard_generation=True):
    if not force:
        settled = reconcile_after_upgrade(user)
        if settled is not None:
            return settled
    if not force and db.is_identity_reset_required():
        token = _token_for_user(user)
        snapshot = api_client.pull_sync_records(token, include_deleted=True)
        purge = apply_server_control(snapshot)
        if purge["purged"]:
            return {
                "sent": 0,
                "saved": 0,
                "batch_id": None,
                "generation": db.get_sync_generation(),
                "purged": True,
            }
        server_records = snapshot.get("records", [])
        if not _server_uses_uuid_identity(server_records):
            # This is the first upgraded device. Replace the legacy integer
            # snapshot once, keeping the intact local data under UUID keys.
            return force_upload(user)

        # Another device already upgraded the account. Merge that UUID snapshot
        # into this device before uploading, otherwise this later device would
        # reset the server and erase work done on the first one.
        db.import_sync_records(server_records)
        db.mark_identity_reset_complete()
        db.mark_server_bootstrap_complete()
        _apply_generation(int(snapshot.get("generation") or 0))
        incremental = False
    token = _token_for_user(user)
    state = get_server_state(user)
    if state.get("local_purge_applied"):
        return {
            "sent": 0,
            "saved": 0,
            "batch_id": None,
            "generation": db.get_sync_generation(),
            "purged": True,
        }
    records, watermark = db.export_sync_records(incremental=incremental, with_watermark=True)
    device_key = db.get_sync_device_key()
    # The account-wide counter asks "did anything at all change since I last
    # looked", which was the right question when a conflict meant choosing one
    # whole database over the other. Every row now carries a UUID and merges on
    # its own, so two devices writing different rows is not a conflict at all.
    # Automatic sync therefore merges per row; the manual replace actions keep
    # the counter, because those really do decide between whole copies.
    expected = None if (force or not guard_generation) else db.get_sync_generation()

    if not records:
        # Nothing to send. Deliberately do NOT adopt the server's counter here:
        # we have not seen its data yet, and pretending otherwise would hide a
        # pending download.
        db.mark_sync_pushed(**watermark)
        db.mark_server_reseed_complete()
        return {"sent": 0, "saved": 0, "batch_id": None, "generation": db.get_sync_generation()}

    total_saved = 0
    last_batch_id = None
    rejected = []
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
                applied_purge_generation=db.get_applied_purge_generation(),
            )
            total_saved += result.get("saved", 0)
            last_batch_id = result.get("batch_id")
            for row in result.get("rejected") or []:
                rejected.append(dict(row))
            # What we sent is no longer what we last saw, so the remembered
            # version is dropped until the next download restores it.
            db.forget_row_versions([
                (record.get("table_name"), record.get("local_id")) for record in chunk
            ])
            returned = result.get("generation")
            if returned:
                seen = int(returned)
            # Our own write moved the counter; the next chunk must expect that.
            guard = seen if guard is not None else None
    except api_client.RemotePurgeRequiredError:
        latest = get_server_state(user)
        if latest.get("local_purge_applied"):
            return {
                "sent": 0,
                "saved": 0,
                "batch_id": None,
                "generation": db.get_sync_generation(),
                "purged": True,
            }
        raise SyncError("Serverdagi o'chirish buyrug'ini lokal bazaga qo'llab bo'lmadi.")
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
        "rejected": rejected,
        "generation": generation if generation is not None else db.get_sync_generation(),
    }


@_one_at_a_time
def auto_sync_turn(user, incremental=True):
    """One round of automatic synchronisation: take first, then give.

    Downloading before uploading is deliberate. A device that sends its work
    before seeing the other side's turns an ordinary edit into a conflict, and
    a conflict is the one thing this whole arrangement exists to avoid.

    Returns what moved, so the open windows can reload exactly what changed
    instead of everything.
    """
    outcome = {
        "pulled": 0,
        "pushed": 0,
        "tables": [],
        "rejected": [],
        "conflict": False,
        "settled": False,
    }
    settled = reconcile_after_upgrade(user)
    if settled is not None:
        outcome["settled"] = True
        outcome["pulled"] = int(settled.get("imported") or 0)
        outcome["tables"] = list(db.SYNC_TABLES)
        return outcome

    pull = pull_server_changes(user, incremental=incremental)
    outcome["pulled"] = int(pull.get("imported") or 0)
    outcome["tables"] = db.get_last_pull_stats().get("tables", [])

    status = db.get_sync_status()
    if int(status.get("pending_change_count") or 0) <= 0:
        return outcome

    try:
        push = push_local_changes(user, guard_generation=False)
    except SyncConflict:
        # The server moved while we were preparing to send. Read it once more
        # and try again; if it still refuses, leave it to the sync button
        # rather than looping.
        pull_server_changes(user, incremental=False)
        try:
            push = push_local_changes(user, guard_generation=False)
        except SyncConflict:
            outcome["conflict"] = True
            return outcome
    outcome["pushed"] = int(push.get("sent") or 0)
    outcome["rejected"] = list(push.get("rejected") or [])
    if outcome["rejected"]:
        # Somebody else changed those rows while we were looking at an older
        # copy. Take their version rather than argue about it, and let the
        # caller say which of them the person can see on screen.
        refresh = pull_server_changes(user, incremental=True)
        outcome["pulled"] += int(refresh.get("imported") or 0)
        for row in outcome["rejected"]:
            table = row.get("table_name")
            if table and table not in outcome["tables"]:
                outcome["tables"].append(table)
    return outcome


# How far back an old server has to be asked to look. Only used when the server
# has no change history yet: a clock reading can miss rows written by a push that
# was still committing, and re-reading a couple of minutes costs nothing because
# applying a row we already have changes nothing.
WATERMARK_OVERLAP_SECONDS = 180


def _overlapped_watermark(value):
    """Move a stored clock reading back a little, so a race cannot skip rows."""
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        # Unreadable marker: ask for everything rather than guess.
        return None
    return (moment - timedelta(seconds=WATERMARK_OVERLAP_SECONDS)).isoformat()


def _remember_cursor(result):
    """Move the reading position, but only if the server keeps a history."""
    if not (result or {}).get("cursor_supported"):
        return
    db.set_pull_cursor((result or {}).get("cursor"))


@_one_at_a_time
def reconcile_full(user):
    """Compare this device against the server in full and heal any difference.

    Ordinary sync moves differences around: the upload queue carries what
    changed here, the cursor carries what changed there. Neither notices if a
    difference was never recorded in the first place -- a queue entry lost to a
    crash, a database restored from a backup, a row written by a version that
    had a bug. Once that happens nothing ever corrects it, and the two devices
    stay apart for good.

    So every so often we stop moving differences and look at the whole picture:
    take everything the server has, then walk the local tables and put anything
    the server has never heard of back in the upload queue. After that the two
    sides genuinely agree, rather than agreeing about the changes they happened
    to exchange.
    """
    if db.is_upgrade_reconcile_required():
        # The one-time settlement after the identity change decides between the
        # two copies wholesale. Healing differences before it has run would only
        # queue up rows it is about to discard.
        return {"received": 0, "imported": 0, "queued": 0, "deferred": True}
    token = _token_for_user(user)
    result = api_client.pull_sync_records(token, include_deleted=True)
    purge = apply_server_control(result)
    if purge["purged"]:
        return {"received": 0, "imported": 0, "queued": 0, "purged": True}
    records = result.get("records", [])
    if db.is_identity_reset_required():
        if records and not _server_uses_uuid_identity(records):
            return {"received": len(records), "imported": 0, "queued": 0, "blocked": True}
        db.mark_identity_reset_complete()
    imported = db.import_sync_records(records)
    db.mark_server_bootstrap_complete()
    _apply_generation(int(result.get("generation") or 0))
    db.set_pull_watermark(result.get("server_time"))
    _remember_cursor(result)
    known = {
        (record.get("table_name"), str(record.get("local_id") or ""))
        for record in records
    }
    queued = db.queue_rows_absent_from_server(known)
    return {
        "received": len(records),
        "imported": imported,
        "queued": queued,
        "generation": result.get("generation"),
    }


@_one_at_a_time
def pull_server_changes(user, table_name=None, incremental=False):
    if table_name is None:
        settled = reconcile_after_upgrade(user)
        if settled is not None:
            return {
                "received": settled.get("received", 0),
                "imported": settled.get("imported", 0),
                "skipped_legacy": 0,
                "rejected": 0,
                "server_time": None,
                "generation": settled.get("generation") or db.get_sync_generation(),
                "adopted_server": settled.get("adopted_server"),
                "backup_path": settled.get("backup_path"),
                "discarded_pending": settled.get("discarded_pending", 0),
            }
    token = _token_for_user(user)
    # Ask for what is new to us rather than for the whole account -- but ask by
    # position in the account's change history, never by clock reading. The old
    # code asked "what changed after this moment", and a push whose transaction
    # had opened but not yet committed was invisible to that question while its
    # rows already carried the earlier timestamp. Those rows fell behind the
    # marker and were never offered again: both devices online, both certain
    # they were current, holding different data.
    #
    # A cursor of 0 means we have never read anything by number, so we take the
    # whole account once. That is also what an older server gives us back, since
    # it does not know the parameter -- degrading to a full copy is safe, while
    # degrading to a clock is not.
    since_seq = None
    since = None
    if incremental and table_name is None:
        if db.is_pull_cursor_initialized():
            since_seq = db.get_pull_cursor()
        else:
            since = _overlapped_watermark(db.get_pull_watermark())
    elif incremental and table_name is not None:
        if db.is_table_pull_cursor_initialized(table_name):
            since_seq = db.get_table_pull_cursor(table_name)
    result = api_client.pull_sync_records(
        token,
        since=since,
        since_seq=since_seq,
        table_name=table_name,
        include_deleted=True,
    )
    purge = apply_server_control(result)
    if purge["purged"]:
        return {
            "received": 0,
            "imported": 0,
            "server_time": result.get("server_time"),
            "generation": result.get("generation"),
            "purged": True,
        }
    records = result.get("records", [])
    if db.is_identity_reset_required():
        # This device converted to UUID keys and is still marked as owing the
        # server a replacement. Whether it really does is decided by what the
        # server just handed back, not by the marker: another device may have
        # replaced it already, and refusing the download in that case would
        # leave this device unable to receive anything at all.
        if records and not _server_uses_uuid_identity(records):
            raise SyncError(
                "Serverdagi ma'lumot eski formatda saqlangan, shuning uchun uni "
                "olib bo'lmaydi. \"Yuborish\" tugmasini bosing — server shu "
                "qurilmadagi ma'lumot bilan almashtiriladi."
            )
        db.mark_identity_reset_complete()
    imported = db.import_sync_records(records)
    stats = db.get_last_pull_stats()
    if table_name is None:
        db.mark_server_bootstrap_complete()
        _apply_generation(int(result.get("generation") or 0))
        # Only move the marker past what we actually kept. A row we dropped
        # would otherwise fall behind it and never be offered again.
        # Anything that could not be applied is set aside and retried, not
        # dropped, so the marker can move on without losing it. Freezing it
        # instead would stop every later download behind one bad row.
        db.set_pull_watermark(result.get("server_time"))
        _remember_cursor(result)
        if not db.is_remote_session_cache() or not incremental:
            # A full durable/local download has really seen every table. A
            # server-mode incremental download has only seen rows changed
            # after the global cursor, so untouched historical rows must still
            # be fetched when their page is opened for the first time.
            db.set_all_table_pull_cursors(int(result.get("generation") or 0))
    else:
        # A page-specific read has seen this table through the reported server
        # generation. Later visits only ask for rows changed above that point.
        db.set_table_pull_cursor(table_name, int(result.get("generation") or 0))
    return {
        "received": len(records),
        "imported": imported,
        "skipped_legacy": stats["skipped_legacy"],
        "rejected": stats["rejected"],
        "server_time": result.get("server_time"),
        "generation": result.get("generation"),
    }


@_one_at_a_time
def force_download(user):
    """Take the server copy and discard local changes (after backing them up)."""
    token = _token_for_user(user)
    state = get_server_state(user)
    if state.get("local_purge_applied"):
        return {
            "direction": "purge",
            "received": 0,
            "imported": 0,
            "backup_path": None,
            "generation": db.get_sync_generation(),
            "purged": True,
        }
    backup_path = db.create_local_backup(tag="before_download")
    result = api_client.pull_sync_records(token, include_deleted=True)
    purge = apply_server_control(result)
    if purge["purged"]:
        return {
            "direction": "purge",
            "received": 0,
            "imported": 0,
            "backup_path": None,
            "generation": db.get_sync_generation(),
            "purged": True,
        }
    records = result.get("records", [])
    imported = db.replace_local_from_records(records)
    db.mark_server_bootstrap_complete()
    db.mark_server_reseed_complete()
    _apply_generation(int(result.get("generation") or 0))
    db.set_pull_watermark(result.get("server_time"))
    _remember_cursor(result)
    db.set_all_table_pull_cursors(int(result.get("generation") or 0))
    return {
        "direction": "download",
        "received": len(records),
        "imported": imported,
        "backup_path": backup_path,
        "generation": result.get("generation"),
    }


@_one_at_a_time
def force_upload(user):
    """Overwrite the server with our copy (after backing the server copy up)."""
    if db.is_remote_session_cache():
        raise SyncError(
            "Server-first rejimda vaqtinchalik kesh bilan server bazasini almashtirib bo'lmaydi."
        )
    token = _token_for_user(user)
    state = get_server_state(user)
    if state.get("local_purge_applied"):
        return {
            "direction": "purge",
            "sent": 0,
            "saved": 0,
            "backup_path": None,
            "server_backup_path": None,
            "generation": db.get_sync_generation(),
            "purged": True,
        }
    device_key = db.get_sync_device_key()
    local_backup = db.create_local_backup(tag="before_upload")

    server_backup = None
    try:
        snapshot = api_client.pull_sync_records(token, include_deleted=True)
        server_backup = db.save_server_snapshot_backup(snapshot.get("records", []))
    except api_client.ApiClientError:
        # A snapshot we cannot read is not a reason to block the user's choice.
        server_backup = None

    try:
        api_client.reset_sync_records(
            token,
            device_key=device_key,
            applied_purge_generation=db.get_applied_purge_generation(),
        )
    except api_client.RemotePurgeRequiredError:
        latest = get_server_state(user)
        if latest.get("local_purge_applied"):
            return {
                "direction": "purge",
                "sent": 0,
                "saved": 0,
                "backup_path": None,
                "server_backup_path": None,
                "generation": db.get_sync_generation(),
                "purged": True,
            }
        raise SyncError("Serverdagi o'chirish buyrug'ini lokal bazaga qo'llab bo'lmadi.")
    db.mark_server_bootstrap_complete()
    db.mark_identity_reset_complete()
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


PAGE_DATA_TABLES = {
    "startup": (
        "users", "currencies", "app_settings", "account_assets", "debtors",
        "activity_logs", "notification_reads",
    ),
    "sales": (
        "users", "currencies", "product_sections", "suppliers", "categories",
        "customers", "product_templates", "product_template_fields", "products",
        "product_attributes",
    ),
    "products": (
        "users", "currencies", "product_sections", "suppliers", "categories",
        "customers", "product_templates", "product_template_fields", "products",
        "product_attributes", "sales", "sale_items", "sale_returns", "stock_movements",
    ),
    "finalize_sales": ("users", "currencies", "products", "sales", "sale_items", "sale_returns"),
    "reports": (
        "users", "currencies", "product_sections", "products", "sales", "sale_items",
        "sale_returns", "expense_categories", "expenses", "finance_manual_movements",
    ),
    "sales_details": (
        "users", "currencies", "products", "sales", "sale_items", "sale_returns",
        "expense_categories", "expenses",
    ),
    "finance": (
        "currencies", "products", "sales", "sale_items", "sale_returns",
        "expense_categories", "expenses", "finance_manual_movements",
    ),
    "supplier_debts": (
        "users", "currencies", "suppliers", "customers", "supplier_debt_movements",
        "debtors", "debtor_debt_movements", "customer_debt_movements",
    ),
    "expenses": ("users", "currencies", "expense_categories", "expenses"),
    "users": ("users",),
    "login_history": ("users", "login_logs"),
    "checking": (
        "product_sections", "product_templates", "products", "inventory_check_sessions",
        "inventory_check_items",
    ),
}


@_one_at_a_time
def refresh_page_data(user, page_key):
    """Fetch one page's server data in one paginated, compressed API stream."""
    requested = PAGE_DATA_TABLES.get(str(page_key), ())
    tables = [name for name in db.SYNC_TABLES if name in requested]
    if not tables:
        return {"received": 0, "imported": 0, "tables": []}
    token = _token_for_user(user)
    cursors = [db.get_table_pull_cursor(name) for name in tables]
    initialized = all(db.is_table_pull_cursor_initialized(name) for name in tables)
    since_seq = min(cursors) if cursors and initialized else None
    result = api_client.pull_sync_records(
        token,
        since_seq=since_seq,
        table_names=tables,
        include_deleted=True,
    )
    purge = apply_server_control(result)
    if purge.get("purged"):
        return {"received": 0, "imported": 0, "tables": tables, "purged": True}
    imported = db.import_sync_records(result.get("records", []))
    generation = int(result.get("generation") or 0)
    for table_name in tables:
        db.set_table_pull_cursor(table_name, generation)
    return {
        "received": len(result.get("records", [])),
        "imported": imported,
        "tables": tables,
        "generation": generation,
    }


def bootstrap_remote_session(user):
    """Load only small global tables before opening the desktop window.

    Historical business tables are intentionally not downloaded here. Their
    own pages request them in one filtered stream when first opened.
    """
    result = refresh_page_data(user, "startup")
    generation = int(result.get("generation") or 0)
    db.mark_server_bootstrap_complete()
    db.set_sync_generation(generation)
    db.set_pull_cursor(generation)
    return result


def synchronize_account_storage(user):
    if db.is_server_reseed_required():
        return {"direction": "push", **push_local_changes(user, force=True)}
    if db.is_server_bootstrap_required():
        if db.is_remote_session_cache():
            return {"direction": "pull", **bootstrap_remote_session(user)}
        return {"direction": "pull", **pull_server_changes(user)}
    return {"direction": "none"}
