import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


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
            else:
                messages.append(str(msg))
        return "\n".join(messages) if messages else "Ma'lumot noto'g'ri."
    if isinstance(detail, str):
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
        except Exception:
            detail = None
        detail_text = _format_api_detail(detail)
        if exc.code in (400, 401, 403, 409, 422, 429):
            raise ApiClientError(detail_text or "Email yoki parol noto'g'ri.") from exc
        raise ApiClientError(detail_text or f"Server xatosi: HTTP {exc.code}") from exc
    except URLError as exc:
        raise ApiClientError("Online API bilan aloqa yo'q. Server ishlayotganini tekshiring.") from exc
    except TimeoutError as exc:
        raise ApiClientError("Online API javob berishi cho'zilib ketdi.") from exc
    except OSError as exc:
        raise ApiClientError("Online APIga ulanishda xatolik yuz berdi.") from exc


def login(email, password):
    token_response = _request_json("/auth/login", {"email": email, "password": password})
    token = token_response.get("access_token")
    if not token:
        raise ApiClientError("Server token qaytarmadi.")
    user = _request_json("/auth/me", token=token)
    return {"token": token, "user": user}


def register(email, password):
    return _request_json("/auth/register", {"email": email, "password": password})


def confirm_registration(email, code):
    token_response = _request_json("/auth/register/confirm", {"email": email, "code": code})
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
    query = []
    if since:
        query.append(f"since={since}")
    if table_name:
        query.append(f"table_name={table_name}")
    query.append(f"include_deleted={'true' if include_deleted else 'false'}")
    path = "/sync/pull"
    if query:
        path += "?" + "&".join(query)
    return _request_json(path, token=token, timeout=timeout)


def get_sync_summary(token):
    return _request_json("/sync/summary", token=token)
