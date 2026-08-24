import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.parse import urlencode


DEFAULT_API_URL = "http://169.58.152.33:8000/api/v1"


class ApiClientError(Exception):
    """Raised when the online API cannot authenticate or respond."""


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


def _request_json(path, payload=None, token=None, timeout=10):
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"

    request = Request(f"{_api_base_url()}{path}", data=data, headers=headers, method="POST" if payload is not None else "GET")
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
            return json.loads(body) if body else {}
    except HTTPError as exc:
        try:
            body = exc.read().decode("utf-8")
            detail = json.loads(body).get("detail") if body else None
        except (UnicodeDecodeError, json.JSONDecodeError):
            detail = None
        detail_text = _format_api_detail(detail)
        if exc.code == 409:
            raise ApiClientError(detail_text or "Bu email allaqachon ro'yxatdan o'tgan. Iltimos, 'Login' orqali kiring.") from exc
        if exc.code in (400, 401, 403, 422, 429):
            raise ApiClientError(detail_text or "Email yoki parol noto'g'ri.") from exc
        raise ApiClientError(detail_text or f"Server xatosi: HTTP {exc.code}") from exc
    except (URLError, TimeoutError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ApiClientError("Internet yoki server bilan aloqa yo'q. Iltimos, internet ulanishingizni tekshiring.") from exc


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


def push_sync_records(token, records, device_key=None, note=None, timeout=30):
    payload = {
        "device": {"device_key": device_key or "desktop", "name": "MarketStore POS Desktop"},
        "records": records,
        "note": note,
    }
    return _request_json("/sync/push", payload, token=token, timeout=timeout)


def pull_sync_records(token, since=None, table_name=None, include_deleted=True, timeout=30):
    offset = 0
    records = []
    server_time = None
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
        if not result.get("has_more"):
            return {"records": records, "server_time": server_time}
        next_offset = result.get("next_offset")
        if next_offset is None or int(next_offset) <= offset:
            raise ApiClientError("Server sync sahifasini davom ettirib bo'lmadi.")
        offset = int(next_offset)


def get_sync_summary(token):
    return _request_json("/sync/summary", token=token)
