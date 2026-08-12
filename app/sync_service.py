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
    raise SyncError("Online account token topilmadi. Gmail yoki Google orqali qayta kiring.")


def push_local_changes(user):
    token = _token_for_user(user)
    records = db.export_sync_records()
    result = api_client.push_sync_records(
        token,
        records,
        device_key=db.get_sync_device_key(),
        note="desktop snapshot",
    )
    db.mark_sync_pushed()
    return {
        "sent": len(records),
        "saved": result.get("saved", 0),
        "batch_id": result.get("batch_id"),
    }


def pull_server_changes(user):
    token = _token_for_user(user)
    result = api_client.pull_sync_records(token, include_deleted=True)
    records = result.get("records", [])
    imported = db.import_sync_records(records)
    return {
        "received": len(records),
        "imported": imported,
        "server_time": result.get("server_time"),
    }
