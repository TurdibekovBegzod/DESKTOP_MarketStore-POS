from datetime import datetime, timedelta, timezone
import secrets

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.deps import get_current_user
from app.models import PasswordResetCode, User
from app.schemas import (
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


@router.post("/token", response_model=TokenOut)
def token(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    return _issue_token(form.username, form.password, db)


@router.post("/login", response_model=TokenOut)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    return _issue_token(payload.email, payload.password, db)


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
