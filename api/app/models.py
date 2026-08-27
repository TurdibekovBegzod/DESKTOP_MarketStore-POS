from datetime import datetime
import uuid

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    uid: Mapped[str] = mapped_column(String(36), unique=True, index=True, nullable=False, default=lambda: str(uuid.uuid4()))
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(120))
    role: Mapped[str] = mapped_column(String(20), default="cashier", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    devices: Mapped[list["Device"]] = relationship(back_populates="user", cascade="all, delete-orphan", foreign_keys="Device.user_id")
    records: Mapped[list["UserRecord"]] = relationship(back_populates="user", cascade="all, delete-orphan", foreign_keys="UserRecord.user_id")
    password_reset_codes: Mapped[list["PasswordResetCode"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    email_verification_codes: Mapped[list["EmailVerificationCode"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )

    @property
    def user_uid(self) -> str:
        return self.uid


class PasswordResetCode(Base):
    __tablename__ = "password_reset_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    code_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    user: Mapped[User] = relationship(back_populates="password_reset_codes")


class EmailVerificationCode(Base):
    __tablename__ = "email_verification_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    code_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    user: Mapped[User] = relationship(back_populates="email_verification_codes")


class GoogleOAuthSession(Base):
    """Legacy OAuth rows kept only so account deletion can remove old secrets."""

    __tablename__ = "google_oauth_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    state: Mapped[str] = mapped_column(String(120), unique=True, index=True, nullable=False)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    access_token: Mapped[str | None] = mapped_column(Text)
    error: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Device(Base):
    __tablename__ = "devices"
    __table_args__ = (
        UniqueConstraint("user_id", "device_key", name="uq_devices_user_device_key"),
        UniqueConstraint("user_uid", "device_key", name="uq_devices_user_uid_device_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    user_uid: Mapped[str] = mapped_column(ForeignKey("users.uid", ondelete="CASCADE"), index=True, nullable=False)
    device_key: Mapped[str] = mapped_column(String(120), nullable=False)
    name: Mapped[str | None] = mapped_column(String(120))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    user: Mapped[User] = relationship(back_populates="devices", foreign_keys=[user_id])


class SyncBatch(Base):
    __tablename__ = "sync_batches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    user_uid: Mapped[str] = mapped_column(ForeignKey("users.uid", ondelete="CASCADE"), index=True, nullable=False)
    device_id: Mapped[int | None] = mapped_column(ForeignKey("devices.id", ondelete="SET NULL"))
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    records_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class UserRecord(Base):
    __tablename__ = "user_records"
    __table_args__ = (
        UniqueConstraint("user_id", "table_name", "local_id", name="uq_user_records_user_table_local"),
        UniqueConstraint("user_uid", "table_name", "local_id", name="uq_user_records_user_uid_table_local"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    user_uid: Mapped[str] = mapped_column(ForeignKey("users.uid", ondelete="CASCADE"), index=True, nullable=False)
    table_name: Mapped[str] = mapped_column(String(80), index=True, nullable=False)
    local_id: Mapped[str] = mapped_column(String(120), nullable=False)
    data: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    local_updated_at: Mapped[str | None] = mapped_column(String(40), index=True)
    deleted_at: Mapped[str | None] = mapped_column(String(40), index=True)
    source_device_key: Mapped[str | None] = mapped_column(String(120))
    sync_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    # Position of this row in the account's change history.
    #
    # Downloads used to ask "what changed after this clock reading", and that is
    # unsound: a push writes its rows with the timestamp of the moment the
    # transaction *opened*, but they only become visible when it *commits*. A
    # download running in between saw nothing, moved its reading position past
    # that timestamp, and those rows were then behind the marker forever. Two
    # devices could sit online next to each other, both certain they were up to
    # date, holding completely different data.
    #
    # The counter below is handed out from the account's generation, which every
    # push takes a lock to advance -- so it is assigned in commit order and never
    # goes backwards. Asking "what is above this number" cannot skip anything.
    change_seq: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    user: Mapped[User] = relationship(back_populates="records", foreign_keys=[user_id])


class SyncMeta(Base):
    __tablename__ = "sync_meta"

    user_uid: Mapped[str] = mapped_column(
        ForeignKey("users.uid", ondelete="CASCADE"), primary_key=True
    )
    generation: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    purge_generation: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    purge_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_change_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_device_key: Mapped[str | None] = mapped_column(String(120))
    last_tables: Mapped[list | None] = mapped_column(JSONB)


class AppRelease(Base):
    """The newest published desktop build, as told to us by the release workflow.

    A single row (id=1). Keeping it in the database rather than process memory
    means it survives restarts, is shared across uvicorn workers, and can be
    served to clients without touching the GitHub API at all.
    """

    __tablename__ = "app_releases"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    tag: Mapped[str] = mapped_column(String(80), nullable=False)
    version: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str | None] = mapped_column(String(200))
    notes: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    assets: Mapped[list | None] = mapped_column(JSONB)
    source: Mapped[str] = mapped_column(String(20), default="ping", nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
