import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.parse import urlencode

from ssl_support import create_ssl_context

DEFAULT_API_URL = "https://drinking-relight-trailside.ngrok-free.dev/api/v1"


class ApiClientError(Exception):
    """Raised when the online API cannot authenticate or respond."""


class ApiOfflineError(ApiClientError):
    """Raised when the server could not be reached at all (no internet/tunnel)."""


class SyncConflictError(ApiClientError):
    """Raised when another device changed server data since our last sync."""

    def __init__(self, message, server_generation=None, expected_generation=None):
        super().__init__(message)
        self.server_generation = server_generation
        self.expected_generation = expected_generation


def _format_api_detail(detail):
    if isinstance(detail, list) and detail:
        messages = []
        for item in detail:
            if not isinstance(item, dict):
                continue
            loc = item.get("loc") or []
            field = loc[-1] if loc else ""
            msg = item.get("msg") or "Ma'lumot noto'g'ri."
            if field == "password" and "at least 6" in msg:
                messages.append("Parol kamida 6 ta belgidan iborat bo'lishi kerak.")
            elif field == "email" or "email" in str(msg).lower():
                messages.append("Email formati noto'g'ri kiritildi.")
            else:
                messages.append(str(msg))
        return "\n".join(messages) if messages else "Ma'lumot noto'g'ri."
    if isinstance(detail, str):
        lower = detail.lower()
        if "already exists" in lower or "mavjud" in lower or "ro'yxatdan o'tgan" in lower:
            return "Bu email allaqachon ro'yxatdan o'tgan. Iltimos, 'Login' orqali kiring."
        if "invalid email or password" in lower or "noto'g'ri" in lower:
            return "Email yoki parol noto'g'ri."
        if "invalid or expired" in lower:
            return "Tasdiqlash kodi noto'g'ri yoki muddati tugagan."
        if "not waiting for verification" in lower:
            return "Akkaunt tasdiqlash kutayotgan holatda emas."
        if "can be resent in" in lower:
            return "Kodni qayta yuborish uchun biroz kuting."
        return detail
    return None


def _api_base_url():
    return os.getenv("MARKETSTORE_API_URL", DEFAULT_API_URL).rstrip("/")


def _build_headers(token=None, extra=None):
    headers = {
        "Accept": "application/json",
        "User-Agent": "MarketStore-POS/1.0",
        "ngrok-skip-browser-warning": "true",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if extra:
        headers.update({key: value for key, value in extra.items() if value is not None})
    return headers


def _request_json(path, payload=None, token=None, timeout=10, method=None, headers=None):
    data = None
    request_headers = _build_headers(token, headers)
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    http_method = method or ("POST" if payload is not None else "GET")

    request = Request(f"{_api_base_url()}{path}", data=data, headers=request_headers, method=http_method)
    try:
        with urlopen(request, timeout=timeout, context=create_ssl_context()) as response:
            body = response.read().decode("utf-8")
            return json.loads(body) if body else {}
    except HTTPError as exc:
        try:
            body = exc.read().decode("utf-8")
            detail = json.loads(body).get("detail") if body else None
        except (UnicodeDecodeError, json.JSONDecodeError):
            detail = None
        if exc.code == 409 and isinstance(detail, dict) and detail.get("code") == "sync_conflict":
            raise SyncConflictError(
                "Serverdagi ma'lumot boshqa qurilmada o'zgargan.",
                server_generation=detail.get("server_generation"),
                expected_generation=detail.get("expected_generation"),
            ) from exc
        detail_text = _format_api_detail(detail)
        if exc.code == 409:
            raise ApiClientError(detail_text or "Bu email allaqachon ro'yxatdan o'tgan. Iltimos, 'Login' orqali kiring.") from exc
        if exc.code in (400, 401, 403, 422, 429):
            raise ApiClientError(detail_text or "Email yoki parol noto'g'ri.") from exc
        raise ApiClientError(detail_text or f"Server xatosi: HTTP {exc.code}") from exc
    except (URLError, TimeoutError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ApiOfflineError("Internet yoki server bilan aloqa yo'q. Iltimos, internet ulanishingizni tekshiring.") from exc


def login(email, password):
    token_response = _request_json("/auth/login", {"email": email, "password": password})
    token = token_response.get("access_token")
    if not token:
        raise ApiClientError("Server token qaytarmadi.")
    user = _request_json("/auth/me", token=token)
    return {"token": token, "user": user}


def request_registration_code(email):
    return _request_json("/auth/register", {"email": email, "password": "TempInitPassword123!"})


def register(email, password):
    return _request_json("/auth/register", {"email": email, "password": password})


def confirm_registration(email, code, password=None):
    payload = {"email": email, "code": code}
    if password:
        payload["password"] = password
    try:
        token_response = _request_json("/auth/register/confirm", payload)
    except ApiClientError as exc:
        if "422" in str(exc) and password:
            token_response = _request_json("/auth/register/confirm", {"email": email, "code": code})
        else:
            raise
    token = token_response.get("access_token")
    if not token:
        raise ApiClientError("Server token qaytarmadi.")
    user = _request_json("/auth/me", token=token)
    return {"token": token, "user": user}


def resend_registration_code(email):
    return _request_json("/auth/register/resend", {"email": email})


def request_password_reset(email):
    return _request_json("/auth/password-reset/request", {"email": email})


def confirm_password_reset(email, code, new_password):
    return _request_json(
        "/auth/password-reset/confirm",
        {"email": email, "code": code, "new_password": new_password},
    )


def get_current_user(token):
    return _request_json("/auth/me", token=token)


def push_sync_records(token, records, device_key=None, note=None, timeout=30, expected_generation=None):
    payload = {
        "device": {"device_key": device_key or "desktop", "name": "MarketStore POS Desktop"},
        "records": records,
        "note": note,
    }
    if expected_generation is not None:
        payload["expected_generation"] = int(expected_generation)
    return _request_json("/sync/push", payload, token=token, timeout=timeout)


def get_sync_state(token, timeout=15):
    """Cheap snapshot of the account's server-side change counter."""
    return _request_json("/sync/state", token=token, timeout=timeout)


def reset_sync_records(token, device_key=None, timeout=60):
    """Wipe every server-side record for the account (full re-upload path)."""
    return _request_json(
        "/sync/reset",
        payload={},
        token=token,
        timeout=timeout,
        headers={"X-Device-Key": device_key},
    )


def open_sync_event_stream(token, since_generation=None, timeout=60):
    """Open the long-lived Server-Sent Events connection.

    Returns the raw response object; feed it to :func:`iter_sse_events`. The
    socket timeout doubles as a dead-tunnel detector: the server sends a ping at
    least every 20s, so a read that stalls past `timeout` means the link is gone.
    """
    query = {}
    if since_generation is not None:
        query["since_generation"] = int(since_generation)
    suffix = f"?{urlencode(query)}" if query else ""
    headers = _build_headers(token, {"Accept": "text/event-stream", "Cache-Control": "no-cache"})
    request = Request(f"{_api_base_url()}/sync/events{suffix}", headers=headers, method="GET")
    try:
        return urlopen(request, timeout=timeout, context=create_ssl_context())
    except HTTPError as exc:
        if exc.code in (401, 403):
            raise ApiClientError("Sessiya muddati tugagan. Qayta kiring.") from exc
        raise ApiOfflineError(f"Realtime ulanish ochilmadi: HTTP {exc.code}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise ApiOfflineError("Realtime ulanish ochilmadi.") from exc


def iter_sse_events(response):
    """Parse a text/event-stream body into ``(event_name, payload_dict)`` pairs."""
    event_name = "message"
    data_lines = []
    while True:
        raw = response.readline()
        if not raw:
            return
        line = raw.decode("utf-8", "replace").rstrip("\r\n")
        if not line:
            if data_lines:
                blob = "\n".join(data_lines)
                data_lines = []
                name, event_name = event_name, "message"
                try:
                    yield name, json.loads(blob)
                except json.JSONDecodeError:
                    yield name, {"raw": blob}
            continue
        if line.startswith(":"):
            continue
        field, _, value = line.partition(":")
        value = value[1:] if value.startswith(" ") else value
        if field == "event":
            event_name = value or "message"
        elif field == "data":
            data_lines.append(value)


def pull_sync_records(token, since=None, table_name=None, include_deleted=True, timeout=30):
    offset = 0
    records = []
    server_time = None
    generation = 0
    while True:
        query = {
            "include_deleted": "true" if include_deleted else "false",
            "limit": 1000,
            "offset": offset,
        }
        if since:
            query["since"] = since
        if table_name:
            query["table_name"] = table_name
        result = _request_json(f"/sync/pull?{urlencode(query)}", token=token, timeout=timeout)
        records.extend(result.get("records", []))
        server_time = result.get("server_time") or server_time
        generation = result.get("generation") or generation
        if not result.get("has_more"):
            return {"records": records, "server_time": server_time, "generation": generation}
        next_offset = result.get("next_offset")
        if next_offset is None or int(next_offset) <= offset:
            raise ApiClientError("Server sync sahifasini davom ettirib bo'lmadi.")
        offset = int(next_offset)


def get_sync_summary(token):
    return _request_json("/sync/summary", token=token)
