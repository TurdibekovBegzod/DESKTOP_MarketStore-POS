import json
import os
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.parse import urlencode

from ssl_support import create_ssl_context

DEFAULT_API_URL = "https://drinking-relight-trailside.ngrok-free.dev/api/v1"

# Auth runs over a tunnel that can be cold-starting: give it a real budget
# instead of the shorter timeout used by event-triggered sync requests.
AUTH_TIMEOUT = 25
AUTH_RETRIES = 2
_RETRY_BACKOFF_SECONDS = (1.0, 2.5)

# HTTP codes that mean "the app server is not answering right now" rather than
# "your request was rejected". ngrok answers with 404/502 when the tunnel is
# down, and a restarting backend answers with 502/503/504.
_SERVER_UNAVAILABLE_CODES = {404, 500, 502, 503, 504}


class ApiClientError(Exception):
    """Raised when the online API cannot authenticate or respond."""


class ApiOfflineError(ApiClientError):
    """Raised when the server could not be reached at all (no internet/tunnel)."""


class ApiAuthError(ApiClientError):
    """Raised when the server actively rejected the credentials (wrong e-mail/parol)."""


class ApiVerificationRequiredError(ApiClientError):
    """Raised when the account exists but its e-mail was never confirmed."""


class SyncConflictError(ApiClientError):
    """Raised when another device changed server data since our last sync."""

    def __init__(self, message, server_generation=None, expected_generation=None):
        super().__init__(message)
        self.server_generation = server_generation
        self.expected_generation = expected_generation


class UnsupportedSyncTableError(ApiClientError):
    """The server build refuses one or more of the tables in this batch.

    A desktop that ships a new table before the API is deployed used to lose
    every push: FastAPI validates the whole request body, so one row from an
    unknown table turned the entire batch - sales, products, everything - into
    a 422. The tables are reported here so the caller can send what the server
    does understand instead of nothing at all.
    """

    def __init__(self, message, tables=()):
        super().__init__(message)
        self.tables = {str(name) for name in tables if name}


class RemotePurgeRequiredError(ApiClientError):
    """The server erased this account after the desktop's last sync."""

    def __init__(self, message, purge_generation=None):
        super().__init__(message)
        self.purge_generation = purge_generation


def _unsupported_tables(detail):
    """Table names a validation error singled out as unknown to this server."""
    refused = set()
    if not isinstance(detail, list):
        return refused
    for item in detail:
        if not isinstance(item, dict):
            continue
        loc = item.get("loc") or []
        if "table_name" not in [str(part) for part in loc]:
            continue
        if "unsupported sync table" not in str(item.get("msg") or "").lower():
            continue
        value = item.get("input")
        if isinstance(value, str) and value:
            refused.add(value)
    return refused


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
        if "email verification is required" in lower:
            return (
                "Email tasdiqlanmagan. 'Signup' bo'limidan shu email uchun "
                "kodni qayta olib, tasdiqlashni yakunlang."
            )
        if "can be resent in" in lower:
            return "Kodni qayta yuborish uchun biroz kuting."
        if "temporarily unavailable" in lower:
            return "Email xizmati vaqtincha ishlamayapti. Birozdan keyin urinib ko'ring."
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


def _single_request_json(path, payload=None, token=None, timeout=10, method=None, headers=None):
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
        except (UnicodeDecodeError, json.JSONDecodeError, OSError):
            detail = None
        if exc.code == 409 and isinstance(detail, dict) and detail.get("code") == "sync_conflict":
            raise SyncConflictError(
                "Serverdagi ma'lumot boshqa qurilmada o'zgargan.",
                server_generation=detail.get("server_generation"),
                expected_generation=detail.get("expected_generation"),
            ) from exc
        if exc.code == 409 and isinstance(detail, dict) and detail.get("code") == "remote_purge_required":
            raise RemotePurgeRequiredError(
                "Account ma'lumotlari web boshqaruv panelidan o'chirilgan.",
                purge_generation=detail.get("purge_generation"),
            ) from exc
        detail_text = _format_api_detail(detail)
        if exc.code == 409:
            raise ApiClientError(detail_text or "Bu email allaqachon ro'yxatdan o'tgan. Iltimos, 'Login' orqali kiring.") from exc
        if exc.code == 403 and detail_text and "tasdiqlanmagan" in detail_text.lower():
            raise ApiVerificationRequiredError(detail_text) from exc
        if exc.code in (401, 403):
            raise ApiAuthError(detail_text or "Email yoki parol noto'g'ri.") from exc
        if exc.code == 422:
            refused = _unsupported_tables(detail)
            if refused:
                raise UnsupportedSyncTableError(
                    "Server bu jadvallarni qabul qilmaydi: " + ", ".join(sorted(refused)),
                    tables=refused,
                ) from exc
        if exc.code in (400, 422, 429):
            raise ApiClientError(detail_text or "So'rov qabul qilinmadi. Ma'lumotlarni tekshiring.") from exc
        if exc.code in _SERVER_UNAVAILABLE_CODES:
            # A tunnel/gateway answer, not an application answer: treat it as
            # "server is down" so the caller can retry or fall back offline.
            raise ApiOfflineError(
                f"Server hozir javob bermayapti (HTTP {exc.code}). Birozdan keyin urinib ko'ring."
            ) from exc
        raise ApiClientError(detail_text or f"Server xatosi: HTTP {exc.code}") from exc
    except (URLError, TimeoutError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ApiOfflineError("Internet yoki server bilan aloqa yo'q. Iltimos, internet ulanishingizni tekshiring.") from exc


def _request_json(path, payload=None, token=None, timeout=10, method=None, headers=None, retries=0):
    """Perform one API call, optionally retrying transient transport failures.

    Only :class:`ApiOfflineError` is retried - it means the request never
    reached the application, so replaying it cannot duplicate a side effect.
    Any answer the server actually produced (auth failure, validation error,
    conflict) is raised immediately.
    """
    attempts = max(0, int(retries)) + 1
    last_error = None
    for attempt in range(attempts):
        try:
            return _single_request_json(
                path, payload=payload, token=token, timeout=timeout,
                method=method, headers=headers,
            )
        except ApiOfflineError as exc:
            last_error = exc
            if attempt + 1 >= attempts:
                break
            backoff = _RETRY_BACKOFF_SECONDS[min(attempt, len(_RETRY_BACKOFF_SECONDS) - 1)]
            time.sleep(backoff)
    raise last_error


def normalize_email(value):
    """Client-side mirror of the server's e-mail normalisation."""
    return (value or "").strip().lower()


def login(email, password):
    email = normalize_email(email)
    if not email or "@" not in email:
        raise ApiClientError("Email manzilini to'g'ri kiriting.")
    if not password:
        raise ApiClientError("Parolni kiriting.")
    if len(password) < 6:
        raise ApiAuthError("Parol kamida 6 ta belgidan iborat bo'lishi kerak.")
    token_response = _request_json(
        "/auth/login",
        {"email": email, "password": password},
        timeout=AUTH_TIMEOUT,
        retries=AUTH_RETRIES,
    )
    token = token_response.get("access_token")
    if not token:
        raise ApiClientError("Server token qaytarmadi.")
    user = get_current_user(token)
    if not (user.get("user_uid") or user.get("uid")):
        raise ApiClientError("Server account ma'lumotini to'liq qaytarmadi.")
    return {"token": token, "user": user}


def request_registration_code(email):
    return _request_json(
        "/auth/register",
        {"email": normalize_email(email), "password": "TempInitPassword123!"},
        timeout=AUTH_TIMEOUT,
    )


def register(email, password):
    return _request_json(
        "/auth/register",
        {"email": normalize_email(email), "password": password},
        timeout=AUTH_TIMEOUT,
    )


def confirm_registration(email, code, password=None):
    email = normalize_email(email)
    code = "".join(ch for ch in str(code or "") if ch.isdigit())
    payload = {"email": email, "code": code}
    if password:
        payload["password"] = password
    try:
        token_response = _request_json("/auth/register/confirm", payload, timeout=AUTH_TIMEOUT)
    except ApiClientError as exc:
        if "422" in str(exc) and password:
            token_response = _request_json(
                "/auth/register/confirm", {"email": email, "code": code}, timeout=AUTH_TIMEOUT
            )
        else:
            raise
    token = token_response.get("access_token")
    if not token:
        raise ApiClientError("Server token qaytarmadi.")
    user = get_current_user(token)
    return {"token": token, "user": user}


def resend_registration_code(email):
    return _request_json(
        "/auth/register/resend", {"email": normalize_email(email)}, timeout=AUTH_TIMEOUT
    )


def request_password_reset(email):
    return _request_json(
        "/auth/password-reset/request", {"email": normalize_email(email)}, timeout=AUTH_TIMEOUT
    )


def confirm_password_reset(email, code, new_password):
    return _request_json(
        "/auth/password-reset/confirm",
        {
            "email": normalize_email(email),
            "code": "".join(ch for ch in str(code or "") if ch.isdigit()),
            "new_password": new_password,
        },
        timeout=AUTH_TIMEOUT,
    )


def get_current_user(token):
    return _request_json("/auth/me", token=token, timeout=AUTH_TIMEOUT, retries=AUTH_RETRIES)


def push_sync_records(
    token,
    records,
    device_key=None,
    note=None,
    timeout=30,
    expected_generation=None,
    applied_purge_generation=None,
):
    payload = {
        "device": {"device_key": device_key or "desktop", "name": "MarketStore POS Desktop"},
        "records": records,
        "note": note,
    }
    if expected_generation is not None:
        payload["expected_generation"] = int(expected_generation)
    if applied_purge_generation is not None:
        payload["applied_purge_generation"] = int(applied_purge_generation)
    return _request_json("/sync/push", payload, token=token, timeout=timeout)


def get_sync_state(token, timeout=15):
    """Cheap snapshot of the account's server-side change counter."""
    return _request_json("/sync/state", token=token, timeout=timeout)


def reset_sync_records(token, device_key=None, timeout=60, applied_purge_generation=None):
    """Wipe every server-side record for the account (full re-upload path)."""
    return _request_json(
        "/sync/reset",
        payload={},
        token=token,
        timeout=timeout,
        headers={
            "X-Device-Key": device_key,
            "X-Purge-Generation": (
                str(applied_purge_generation) if applied_purge_generation is not None else None
            ),
        },
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


def pull_sync_records(
    token,
    since=None,
    since_seq=None,
    table_name=None,
    table_names=None,
    include_deleted=True,
    timeout=30,
):
    """Download the account's records, optionally only what is new to us.

    ``since_seq`` is a position in the account's change history and is the only
    safe way to ask for "what is new": it moves in commit order. ``since`` is a
    clock reading and is kept for servers that have not been updated yet -- it
    can silently step over rows written by a push that had not committed when
    the reading was taken.
    """
    offset = 0
    records = []
    server_time = None
    generation = 0
    purge_generation = 0
    purge_requested_at = None
    cursor = 0
    cursor_supported = False
    # Medium batches keep first paint fast without turning a large account into
    # dozens of HTTPS round trips. Responses are gzip-compressed by the API.
    page_size = 500
    while True:
        query = {
            "include_deleted": "true" if include_deleted else "false",
            "limit": page_size,
            "offset": offset,
        }
        if since_seq is not None:
            query["since_seq"] = int(since_seq)
        elif since:
            query["since"] = since
        if table_name:
            query["table_name"] = table_name
        elif table_names:
            query["tables"] = ",".join(dict.fromkeys(str(name) for name in table_names if name))
        result = _request_json(f"/sync/pull?{urlencode(query)}", token=token, timeout=timeout)
        records.extend(result.get("records", []))
        server_time = result.get("server_time") or server_time
        generation = result.get("generation") or generation
        purge_generation = result.get("purge_generation") or purge_generation
        purge_requested_at = result.get("purge_requested_at") or purge_requested_at
        if result.get("cursor_supported"):
            cursor_supported = True
            cursor = max(cursor, int(result.get("cursor") or 0))
        if not result.get("has_more"):
            return {
                "records": records,
                "server_time": server_time,
                "generation": generation,
                "purge_generation": purge_generation,
                "purge_requested_at": purge_requested_at,
                "cursor": cursor,
                "cursor_supported": cursor_supported,
            }
        next_offset = result.get("next_offset")
        if next_offset is None or int(next_offset) <= offset:
            raise ApiClientError("Server sync sahifasini davom ettirib bo'lmadi.")
        offset = int(next_offset)


def get_sync_summary(token):
    return _request_json("/sync/summary", token=token)
