import gzip
import http.client
import json
import os
import ssl
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, getproxies, urlopen
from urllib.parse import urlencode, urlsplit

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
        if "invalid verification code" in lower:
            return "Tasdiqlash kodi noto'g'ri yoki muddati tugagan."
        if "invalid password" in lower:
            return "Parol noto'g'ri."
        if "invalid token" in lower or "user not found" in lower:
            return "Sessiya muddati tugagan. Dasturdan chiqib, qayta kiring."
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


# How long an idle kept-alive connection is trusted before it is replaced. The
# tunnel drops idle client connections on its own schedule; reusing one it has
# already closed costs a failed attempt, so old ones are not offered at all.
_IDLE_CONNECTION_SECONDS = 50

# Errors that mean a reused connection had already been closed by the other end
# before it read our request: the server never saw it, so sending it once more
# on a fresh connection cannot apply anything twice.
_STALE_CONNECTION_ERRORS = (
    http.client.RemoteDisconnected,
    http.client.CannotSendRequest,
    ConnectionResetError,
    ConnectionAbortedError,
    BrokenPipeError,
    ssl.SSLEOFError,
)

# One open connection per thread and server. Each API call used to open its own
# TCP + TLS connection to the tunnel, which made every request cost several
# round trips before a byte of it was sent. Per thread, because http.client
# connections are not safe to share and the GUI, the sync engine and the page
# loaders all talk to the API at the same time.
_connections = threading.local()


def _decode_body(raw, headers):
    """The response text, inflated when the server gzipped it."""
    if (headers.get("Content-Encoding") or "").lower() == "gzip":
        raw = gzip.decompress(raw)
    return raw.decode("utf-8")


def _drop_connection(key):
    pool = getattr(_connections, "pool", None) or {}
    entry = pool.pop(key, None)
    if entry is not None:
        try:
            entry[0].close()
        except Exception:
            pass


def _connection_for(key, timeout):
    """(connection, reused) for this thread, opening a new one when needed."""
    pool = getattr(_connections, "pool", None)
    if pool is None:
        pool = _connections.pool = {}
    entry = pool.get(key)
    if entry is not None:
        connection, last_used = entry
        if time.monotonic() - last_used < _IDLE_CONNECTION_SECONDS and connection.sock is not None:
            connection.timeout = timeout
            connection.sock.settimeout(timeout)
            return connection, True
        _drop_connection(key)
    scheme, host, port = key
    if scheme == "https":
        connection = http.client.HTTPSConnection(
            host, port, timeout=timeout, context=create_ssl_context()
        )
    else:
        connection = http.client.HTTPConnection(host, port, timeout=timeout)
    pool[key] = (connection, time.monotonic())
    return connection, False


def _send_pooled(method, url, data, headers, timeout):
    parts = urlsplit(url)
    key = (parts.scheme, parts.hostname, parts.port)
    target = parts.path + (f"?{parts.query}" if parts.query else "")
    for attempt in range(2):
        connection, reused = _connection_for(key, timeout)
        try:
            connection.request(method, target, body=data, headers=headers)
            response = connection.getresponse()
            raw = response.read()
        except _STALE_CONNECTION_ERRORS:
            _drop_connection(key)
            if reused and attempt == 0:
                continue
            raise
        except BaseException:
            # A timeout or a half-read answer leaves the connection in an
            # unknown state; the next call must not inherit it.
            _drop_connection(key)
            raise
        if response.will_close:
            _drop_connection(key)
        else:
            _connections.pool[key] = (connection, time.monotonic())
        return response.status, response.headers, raw
    raise http.client.RemoteDisconnected("connection closed")


def _send_via_proxy(method, url, data, headers, timeout):
    """urllib's path, kept for a machine that must reach the API through a proxy.

    http.client knows nothing about the system proxy settings urllib reads, and
    a shop behind a proxy is better served by a slower request than none.
    """
    request = Request(url, data=data, headers=headers, method=method)
    try:
        with urlopen(request, timeout=timeout, context=create_ssl_context()) as response:
            return response.status, response.headers, response.read()
    except HTTPError as exc:
        try:
            raw = exc.read()
        except OSError:
            raw = b""
        return exc.code, exc.headers or {}, raw


def _send(method, url, data, headers, timeout):
    """(status, headers, raw body) for one request; raises OSError if unreachable."""
    if getproxies().get(urlsplit(url).scheme):
        return _send_via_proxy(method, url, data, headers, timeout)
    return _send_pooled(method, url, data, headers, timeout)


def _raise_for_status(code, body):
    try:
        detail = json.loads(body).get("detail") if body else None
    except (json.JSONDecodeError, AttributeError):
        detail = None
    if code == 409 and isinstance(detail, dict) and detail.get("code") == "sync_conflict":
        raise SyncConflictError(
            "Serverdagi ma'lumot boshqa qurilmada o'zgargan.",
            server_generation=detail.get("server_generation"),
            expected_generation=detail.get("expected_generation"),
        )
    if code == 409 and isinstance(detail, dict) and detail.get("code") == "remote_purge_required":
        raise RemotePurgeRequiredError(
            "Account ma'lumotlari web boshqaruv panelidan o'chirilgan.",
            purge_generation=detail.get("purge_generation"),
        )
    detail_text = _format_api_detail(detail)
    if code == 409:
        raise ApiClientError(detail_text or "Bu email allaqachon ro'yxatdan o'tgan. Iltimos, 'Login' orqali kiring.")
    if code == 403 and detail_text and "tasdiqlanmagan" in detail_text.lower():
        raise ApiVerificationRequiredError(detail_text)
    if code in (401, 403):
        raise ApiAuthError(detail_text or "Email yoki parol noto'g'ri.")
    if code == 422:
        refused = _unsupported_tables(detail)
        if refused:
            raise UnsupportedSyncTableError(
                "Server bu jadvallarni qabul qilmaydi: " + ", ".join(sorted(refused)),
                tables=refused,
            )
    if code in (400, 422, 429):
        raise ApiClientError(detail_text or "So'rov qabul qilinmadi. Ma'lumotlarni tekshiring.")
    if code in _SERVER_UNAVAILABLE_CODES:
        # A tunnel/gateway answer, not an application answer: treat it as
        # "server is down" so the caller can retry or fall back offline.
        raise ApiOfflineError(
            f"Server hozir javob bermayapti (HTTP {code}). Birozdan keyin urinib ko'ring."
        )
    raise ApiClientError(detail_text or f"Server xatosi: HTTP {code}")


def _single_request_json(path, payload=None, token=None, timeout=10, method=None, headers=None):
    data = None
    request_headers = _build_headers(token, headers)
    # A full pull is ~3 MB of JSON and ~0.4 MB gzipped. Without this header the
    # API sends it raw, and over the tunnel each 500-row page took seconds.
    request_headers.setdefault("Accept-Encoding", "gzip")
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    http_method = method or ("POST" if payload is not None else "GET")

    try:
        status, response_headers, raw = _send(
            http_method, f"{_api_base_url()}{path}", data, request_headers, timeout
        )
    except (URLError, TimeoutError, OSError, http.client.HTTPException) as exc:
        raise ApiOfflineError("Internet yoki server bilan aloqa yo'q. Iltimos, internet ulanishingizni tekshiring.") from exc
    try:
        body = _decode_body(raw, response_headers)
    except (OSError, UnicodeDecodeError) as exc:
        if status < 400:
            raise ApiOfflineError("Internet yoki server bilan aloqa yo'q. Iltimos, internet ulanishingizni tekshiring.") from exc
        # An unreadable error page still says what the status says.
        body = ""
    if status >= 400:
        _raise_for_status(status, body)
    try:
        return json.loads(body) if body else {}
    except json.JSONDecodeError as exc:
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


def verify_admin_password(token, password):
    """Check the password that opens the main (admin) section.

    Separate from the account password: they start out identical, and from the
    first change onward only this one opens the main section. It is never
    cached on the device, so this needs a live connection.
    """
    if not token:
        raise ApiClientError("Sessiya topilmadi. Qayta kiring.")
    if not password:
        raise ApiClientError("Parolni kiriting.")
    return _request_json(
        "/auth/admin-password/verify",
        {"password": password},
        token=token,
        timeout=AUTH_TIMEOUT,
        retries=AUTH_RETRIES,
    )


def request_admin_password_code(token):
    """Mail a verification code for changing the main section password."""
    if not token:
        raise ApiClientError("Sessiya topilmadi. Qayta kiring.")
    return _request_json(
        "/auth/admin-password/request", {}, token=token, timeout=AUTH_TIMEOUT
    )


def confirm_admin_password(token, code, new_password):
    if not token:
        raise ApiClientError("Sessiya topilmadi. Qayta kiring.")
    return _request_json(
        "/auth/admin-password/confirm",
        {
            "code": "".join(ch for ch in str(code or "") if ch.isdigit()),
            "new_password": new_password,
        },
        token=token,
        timeout=AUTH_TIMEOUT,
    )


def get_current_user(token):
    return _request_json("/auth/me", token=token, timeout=AUTH_TIMEOUT, retries=AUTH_RETRIES)


def check_instagram_account_claim(token, account_id, timeout=15):
    """Ask the server whether this Instagram account is free for this shop.

    Returns ``(available, owner_email)``. ``available`` is False when another
    shop already connected the same Instagram Business Account, in which case
    ``owner_email`` is that owner's masked address (or None when the server did
    not disclose it).
    """
    result = _request_json(
        "/instagram/account/claim-check",
        {"account_id": str(account_id or "").strip()},
        token=token,
        timeout=timeout,
    ) or {}
    return bool(result.get("available")), result.get("owner_email")


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
    # dozens of HTTPS round trips. Each page arrives gzipped (_single_request_json).
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
