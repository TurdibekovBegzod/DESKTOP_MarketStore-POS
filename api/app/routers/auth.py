from datetime import datetime, timedelta, timezone
import html
import json
import secrets
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import HTMLResponse
from fastapi.security import OAuth2PasswordRequestForm
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.deps import get_current_user
from app.models import GoogleOAuthSession, PasswordResetCode, User
from app.schemas import (
    GoogleLoginStartOut,
    GoogleLoginStatusOut,
    LoginRequest,
    MessageOut,
    PasswordResetConfirm,
    PasswordResetRequest,
    TokenOut,
    UserCreate,
    UserOut,
    normalize_email,
)
from app.security import create_access_token, hash_password, hash_secret, verify_password
from app.tasks import send_password_reset_code_task


router = APIRouter(prefix="/auth", tags=["auth"])

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_LOGIN_EXPIRES_SECONDS = 600


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def register(payload: UserCreate, db: Session = Depends(get_db)):
    user = User(
        email=payload.email,
        password_hash=hash_password(payload.password),
        display_name=payload.display_name,
        role="admin",
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Email already exists") from exc
    db.refresh(user)
    return user


def _issue_token(email: str, password: str, db: Session):
    user = db.scalar(select(User).where(User.email == normalize_email(email)))
    if not user or not verify_password(password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User is disabled")
    return TokenOut(access_token=create_access_token(user.uid))


def _google_settings():
    settings = get_settings()
    client_id = settings.google_client_id or ""
    client_secret = settings.google_client_secret or ""
    if (
        not client_id
        or not client_secret
        or client_id.startswith("your-google-client-id")
        or client_secret.startswith("your-google-client-secret")
    ):
        raise HTTPException(status_code=503, detail="Google login is not configured")
    return settings


def _exchange_google_code(code: str, settings) -> dict:
    payload = urlencode(
        {
            "code": code,
            "client_id": settings.google_client_id,
            "client_secret": settings.google_client_secret,
            "redirect_uri": settings.google_redirect_uri,
            "grant_type": "authorization_code",
        }
    ).encode("utf-8")
    request = Request(
        GOOGLE_TOKEN_URL,
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=15) as response:
            import json

            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        try:
            error_body = json.loads(exc.read().decode("utf-8"))
            error_code = error_body.get("error") or "token_exchange_failed"
            description = error_body.get("error_description") or "Google token exchange failed"
            detail = f"{error_code}: {description}"
        except Exception:
            detail = "Google token exchange failed"
        raise HTTPException(status_code=400, detail=detail) from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise HTTPException(status_code=502, detail="Could not reach Google OAuth service") from exc


def _find_or_create_google_user(db: Session, email: str, display_name: str | None) -> User:
    email = normalize_email(email)
    user = db.scalar(select(User).where(User.email == email))
    if user:
        if display_name and not user.display_name:
            user.display_name = display_name
        return user
    user = User(
        email=email,
        password_hash=hash_password(secrets.token_urlsafe(32)),
        display_name=display_name,
        role="admin",
    )
    db.add(user)
    db.flush()
    return user


@router.post("/token", response_model=TokenOut)
def token(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    return _issue_token(form.username, form.password, db)


@router.post("/login", response_model=TokenOut)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    return _issue_token(payload.email, payload.password, db)


@router.post("/google/start", response_model=GoogleLoginStartOut)
def google_start(db: Session = Depends(get_db)):
    settings = _google_settings()
    state = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=GOOGLE_LOGIN_EXPIRES_SECONDS)
    db.add(GoogleOAuthSession(state=state, expires_at=expires_at))
    db.commit()
    params = {
        "client_id": settings.google_client_id,
        "redirect_uri": settings.google_redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "prompt": "select_account",
        "access_type": "online",
    }
    return GoogleLoginStartOut(
        state=state,
        auth_url=f"{GOOGLE_AUTH_URL}?{urlencode(params)}",
        expires_in=GOOGLE_LOGIN_EXPIRES_SECONDS,
    )


@router.get("/google/callback", response_class=HTMLResponse)
def google_callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    db: Session = Depends(get_db),
):
    if not state:
        return HTMLResponse("<h2>Google login failed</h2><p>State is missing.</p>", status_code=400)
    row = db.scalar(select(GoogleOAuthSession).where(GoogleOAuthSession.state == state))
    if not row:
        return HTMLResponse("<h2>Google login failed</h2><p>Session was not found.</p>", status_code=400)
    if row.expires_at < datetime.now(timezone.utc):
        row.error = "Google login session expired"
        db.commit()
        return HTMLResponse("<h2>Google login expired</h2><p>Please try again from the desktop app.</p>", status_code=400)
    if error:
        row.error = error
        db.commit()
        return HTMLResponse("<h2>Google login cancelled</h2><p>You can close this window.</p>", status_code=400)
    if not code:
        row.error = "Google authorization code is missing"
        db.commit()
        return HTMLResponse("<h2>Google login failed</h2><p>Authorization code is missing.</p>", status_code=400)

    settings = _google_settings()
    try:
        token_response = _exchange_google_code(code, settings)
        id_info = google_id_token.verify_oauth2_token(
            token_response["id_token"],
            google_requests.Request(),
            settings.google_client_id,
            clock_skew_in_seconds=settings.google_token_clock_skew_seconds,
        )
        email = id_info.get("email")
        if not email or not id_info.get("email_verified", False):
            raise HTTPException(status_code=400, detail="Google email is not verified")
        user = _find_or_create_google_user(db, email, id_info.get("name"))
        if not user.is_active:
            raise HTTPException(status_code=403, detail="User is disabled")
        row.user_id = user.id
        row.access_token = create_access_token(user.uid)
        row.error = None
        db.commit()
    except HTTPException as exc:
        row.error = str(exc.detail)
        db.commit()
        return HTMLResponse(f"<h2>Google login failed</h2><p>{row.error}</p>", status_code=exc.status_code)
    except Exception as exc:
        row.error = f"{type(exc).__name__}: {exc}"
        if "Token expired" in str(exc):
            row.error = (
                "Google token expired. Browserdagi eski Google login oynalarini yoping va desktop appdan "
                "Google orqali kirishni qayta boshlang."
            )
        db.commit()
        return HTMLResponse(
            f"<h2>Google login failed</h2><p>{html.escape(row.error)}</p>",
            status_code=400,
        )

    return HTMLResponse(
        "<h2>Google login completed</h2>"
        "<p>You can close this window and return to MarketStore POS.</p>"
    )


@router.get("/google/status/{state}", response_model=GoogleLoginStatusOut)
def google_status(state: str, db: Session = Depends(get_db)):
    row = db.scalar(select(GoogleOAuthSession).where(GoogleOAuthSession.state == state))
    if not row:
        raise HTTPException(status_code=404, detail="Google login session was not found")
    if row.error:
        return GoogleLoginStatusOut(status="failed", detail=row.error)
    if row.expires_at < datetime.now(timezone.utc):
        return GoogleLoginStatusOut(status="expired", detail="Google login session expired")
    if not row.access_token:
        return GoogleLoginStatusOut(status="pending")
    token = row.access_token
    row.consumed_at = datetime.now(timezone.utc)
    db.commit()
    return GoogleLoginStatusOut(status="completed", access_token=token)


@router.post("/password-reset/request", response_model=MessageOut)
def request_password_reset(payload: PasswordResetRequest, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == payload.email, User.is_active == True))
    generic_response = MessageOut(message="If this email exists, a verification code has been sent.")
    if not user:
        return generic_response

    settings = get_settings()
    recent_code = db.scalar(
        select(PasswordResetCode)
        .where(
            PasswordResetCode.user_id == user.id,
            PasswordResetCode.used_at.is_(None),
            PasswordResetCode.created_at > datetime.now(timezone.utc) - timedelta(seconds=settings.password_reset_cooldown_seconds),
        )
        .order_by(PasswordResetCode.created_at.desc())
        .limit(1)
    )
    if recent_code:
        return generic_response

    code = f"{secrets.randbelow(900000) + 100000}"
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=settings.password_reset_code_minutes)
    db.execute(
        update(PasswordResetCode)
        .where(PasswordResetCode.user_id == user.id, PasswordResetCode.used_at.is_(None))
        .values(used_at=datetime.now(timezone.utc))
    )
    db.add(PasswordResetCode(user_id=user.id, code_hash=hash_secret(code), expires_at=expires_at))
    db.commit()

    send_password_reset_code_task.delay(user.email, code)
    return generic_response


@router.post("/password-reset/confirm", response_model=MessageOut)
def confirm_password_reset(payload: PasswordResetConfirm, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == payload.email, User.is_active == True))
    if not user:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid verification code")

    now = datetime.now(timezone.utc)
    code_row = db.scalar(
        select(PasswordResetCode)
        .where(
            PasswordResetCode.user_id == user.id,
            PasswordResetCode.code_hash == hash_secret(payload.code),
            PasswordResetCode.used_at.is_(None),
            PasswordResetCode.expires_at > now,
        )
        .order_by(PasswordResetCode.created_at.desc())
        .limit(1)
    )
    if not code_row:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid verification code")

    user.password_hash = hash_password(payload.new_password)
    code_row.used_at = now
    db.commit()
    return MessageOut(message="Password has been updated.")


@router.get("/me", response_model=UserOut)
def me(current_user: User = Depends(get_current_user)):
    return current_user
