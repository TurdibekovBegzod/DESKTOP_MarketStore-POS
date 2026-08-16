from datetime import datetime, timedelta, timezone
import math
import secrets

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.deps import get_current_user
from app.models import EmailVerificationCode, PasswordResetCode, User
from app.schemas import (
    LoginRequest,
    MessageOut,
    PasswordResetConfirm,
    PasswordResetRequest,
    RegistrationChallengeOut,
    RegistrationResend,
    RegistrationStart,
    RegistrationVerify,
    TokenOut,
    UserCreate,
    UserOut,
    normalize_email,
)
from app.security import create_access_token, hash_password, hash_secret, verify_password
from app.tasks import send_password_reset_code_task, send_signup_verification_code_task


router = APIRouter(prefix="/auth", tags=["auth"])


def _seconds_until(value: datetime, now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    return max(0, math.ceil((value - now).total_seconds()))


def _new_signup_code(db: Session, user: User, now: datetime) -> tuple[str, datetime]:
    settings = get_settings()
    code = f"{secrets.randbelow(900000) + 100000}"
    expires_at = now + timedelta(minutes=settings.signup_verification_code_minutes)
    db.execute(
        update(EmailVerificationCode)
        .where(EmailVerificationCode.user_id == user.id, EmailVerificationCode.used_at.is_(None))
        .values(used_at=now)
    )
    db.add(EmailVerificationCode(user_id=user.id, code_hash=hash_secret(code), expires_at=expires_at))
    return code, expires_at


def _registration_challenge(expires_at: datetime, resend_after_seconds: int, now: datetime) -> RegistrationChallengeOut:
    return RegistrationChallengeOut(
        message="Verification code has been sent.",
        expires_in_seconds=_seconds_until(expires_at, now),
        resend_after_seconds=resend_after_seconds,
    )


@router.post("/register/start", response_model=RegistrationChallengeOut, status_code=status.HTTP_202_ACCEPTED)
def register_start(payload: RegistrationStart, db: Session = Depends(get_db)):
    settings = get_settings()
    now = datetime.now(timezone.utc)
    user = db.scalar(select(User).where(User.email == payload.email).with_for_update())
    if user and user.is_active:
        raise HTTPException(status_code=409, detail="Email already exists")

    if user is None:
        user = User(
            email=payload.email,
            password_hash=hash_password(secrets.token_urlsafe(32)),
            display_name=payload.display_name,
            role="admin",
            is_active=False,
        )
        db.add(user)
        try:
            db.flush()
        except IntegrityError as exc:
            db.rollback()
            raise HTTPException(status_code=409, detail="Email already exists") from exc
    else:
        if payload.display_name:
            user.display_name = payload.display_name

    latest_code = db.scalar(
        select(EmailVerificationCode)
        .where(EmailVerificationCode.user_id == user.id, EmailVerificationCode.used_at.is_(None))
        .order_by(EmailVerificationCode.created_at.desc())
        .limit(1)
    )
    if latest_code and latest_code.expires_at > now:
        db.commit()
        remaining = _seconds_until(latest_code.expires_at, now)
        return _registration_challenge(latest_code.expires_at, remaining, now)

    code, expires_at = _new_signup_code(db, user, now)
    db.commit()
    send_signup_verification_code_task.delay(user.email, code)
    return _registration_challenge(expires_at, settings.signup_verification_resend_seconds, now)


@router.post("/register", response_model=RegistrationChallengeOut, status_code=status.HTTP_202_ACCEPTED)
def register(payload: UserCreate, db: Session = Depends(get_db)):
    settings = get_settings()
    now = datetime.now(timezone.utc)
    user = db.scalar(select(User).where(User.email == payload.email).with_for_update())
    if user and user.is_active:
        raise HTTPException(status_code=409, detail="Email already exists")

    if user is None:
        user = User(
            email=payload.email,
            password_hash=hash_password(payload.password),
            display_name=payload.display_name,
            role="admin",
            is_active=False,
        )
        db.add(user)
        try:
            db.flush()
        except IntegrityError as exc:
            db.rollback()
            raise HTTPException(status_code=409, detail="Email already exists") from exc
    else:
        user.password_hash = hash_password(payload.password)
        user.display_name = payload.display_name

    latest_code = db.scalar(
        select(EmailVerificationCode)
        .where(EmailVerificationCode.user_id == user.id, EmailVerificationCode.used_at.is_(None))
        .order_by(EmailVerificationCode.created_at.desc())
        .limit(1)
    )
    if latest_code and latest_code.expires_at > now:
        db.commit()
        remaining = _seconds_until(latest_code.expires_at, now)
        return _registration_challenge(latest_code.expires_at, remaining, now)

    code, expires_at = _new_signup_code(db, user, now)
    db.commit()
    send_signup_verification_code_task.delay(user.email, code)
    return _registration_challenge(expires_at, settings.signup_verification_resend_seconds, now)


@router.post("/register/resend", response_model=RegistrationChallengeOut)
def resend_registration_code(payload: RegistrationResend, db: Session = Depends(get_db)):
    settings = get_settings()
    now = datetime.now(timezone.utc)
    user = db.scalar(select(User).where(User.email == payload.email).with_for_update())
    if not user or user.is_active:
        raise HTTPException(status_code=400, detail="Account is not waiting for verification")

    latest_code = db.scalar(
        select(EmailVerificationCode)
        .where(EmailVerificationCode.user_id == user.id)
        .order_by(EmailVerificationCode.created_at.desc())
        .limit(1)
    )
    if latest_code:
        resend_at = latest_code.created_at + timedelta(seconds=settings.signup_verification_resend_seconds)
        retry_after = _seconds_until(resend_at, now)
        if retry_after > 0:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Verification code can be resent in {retry_after} seconds",
                headers={"Retry-After": str(retry_after)},
            )

    code, expires_at = _new_signup_code(db, user, now)
    db.commit()
    send_signup_verification_code_task.delay(user.email, code)
    return _registration_challenge(expires_at, settings.signup_verification_resend_seconds, now)


@router.post("/register/confirm", response_model=TokenOut)
def confirm_registration(payload: RegistrationVerify, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == payload.email).with_for_update())
    if not user or user.is_active:
        raise HTTPException(status_code=400, detail="Invalid or expired verification code")

    now = datetime.now(timezone.utc)
    code_row = db.scalar(
        select(EmailVerificationCode)
        .where(
            EmailVerificationCode.user_id == user.id,
            EmailVerificationCode.code_hash == hash_secret(payload.code),
            EmailVerificationCode.used_at.is_(None),
            EmailVerificationCode.expires_at > now,
        )
        .order_by(EmailVerificationCode.created_at.desc())
        .limit(1)
    )
    if not code_row:
        raise HTTPException(status_code=400, detail="Invalid or expired verification code")

    if payload.password:
        user.password_hash = hash_password(payload.password)
    user.is_active = True
    user.email_verified_at = now
    code_row.used_at = now
    db.commit()
    return TokenOut(access_token=create_access_token(user.uid))


def _issue_token(email: str, password: str, db: Session):
    user = db.scalar(select(User).where(User.email == normalize_email(email)))
    if not user or not verify_password(password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
    if not user.is_active:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Email verification is required")
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
