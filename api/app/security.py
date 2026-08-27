from datetime import datetime, timedelta, timezone
import hashlib
import secrets

from jose import JWTError, jwt

from app.config import get_settings


ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 120000)
    return f"pbkdf2_sha256${salt}${digest.hex()}"


def hash_secret(value: str) -> str:
    settings = get_settings()
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        value.encode("utf-8"),
        settings.secret_key.encode("utf-8"),
        120000,
    )
    return digest.hex()


def verify_password(password: str, password_hash: str) -> bool:
    try:
        algorithm, salt, _digest = password_hash.split("$", 2)
    except ValueError:
        return False
    if algorithm != "pbkdf2_sha256":
        return False
    return secrets.compare_digest(hash_password_with_salt(password, salt), password_hash)


def hash_password_with_salt(password: str, salt: str) -> str:
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 120000)
    return f"pbkdf2_sha256${salt}${digest.hex()}"


def create_access_token(subject: str) -> str:
    settings = get_settings()
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=settings.access_token_expire_minutes)
    payload = {"sub": subject, "exp": expires_at}
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def decode_access_token(token: str) -> str | None:
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
    except JWTError:
        return None
    subject = payload.get("sub")
    return str(subject) if subject else None


def create_superadmin_token(username: str) -> str:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=max(1, settings.superadmin_token_expire_minutes))
    payload = {
        "sub": username,
        "scope": "superadmin",
        "iat": now,
        "exp": expires_at,
    }
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def decode_superadmin_token(token: str) -> str | None:
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
    except JWTError:
        return None
    subject = str(payload.get("sub") or "")
    if payload.get("scope") != "superadmin" or subject != settings.superadmin_username:
        return None
    return subject
