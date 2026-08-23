import api_client
import database as db


class SyncError(Exception):
    pass


def _token_for_user(user):
    token = (user or {}).get("api_access_token")
    if token:
        return token
    token = db.get_user_api_token((user or {}).get("id"))
    if token:
        return token
    raise SyncError("Online account token topilmadi. Email va parol orqali qayta kiring.")


def push_local_changes(user, batch_size=1000, incremental=True):
    token = _token_for_user(user)
    records = db.export_sync_records(incremental=incremental)
    if not records:
        db.mark_sync_pushed()
        db.mark_server_reseed_complete()
        return {"sent": 0, "saved": 0, "batch_id": None}
    device_key = db.get_sync_device_key()
    total_saved = 0
    last_batch_id = None
    for i in range(0, len(records), batch_size):
        chunk = records[i:i + batch_size]
        result = api_client.push_sync_records(
            token,
            chunk,
            device_key=device_key,
            note=f"desktop snapshot ({i + len(chunk)}/{len(records)})",
            timeout=60,
        )
        total_saved += result.get("saved", 0)
        last_batch_id = result.get("batch_id")
    db.mark_sync_pushed()
    db.mark_server_reseed_complete()
    return {
        "sent": len(records),
        "saved": total_saved,
        "batch_id": last_batch_id,
    }


def pull_server_changes(user):
    token = _token_for_user(user)
    result = api_client.pull_sync_records(token, include_deleted=True)
    records = result.get("records", [])
    imported = db.import_sync_records(records)
    db.mark_server_bootstrap_complete()
    return {
        "received": len(records),
        "imported": imported,
        "server_time": result.get("server_time"),
    }


def synchronize_account_storage(user):
    if db.is_server_reseed_required():
        return {"direction": "push", **push_local_changes(user)}
    if db.is_server_bootstrap_required():
        return {"direction": "pull", **pull_server_changes(user)}
    return {"direction": "none"}
