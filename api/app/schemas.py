from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator


def normalize_email(value: str) -> str:
    normalized = (value or "").strip().lower()
    if "@" not in normalized or "." not in normalized.rsplit("@", 1)[-1]:
        raise ValueError("Valid email is required")
    return normalized


class UserCreate(BaseModel):
    email: str = Field(min_length=5, max_length=255)
    password: str = Field(min_length=6, max_length=128)
    display_name: str | None = Field(default=None, max_length=120)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return normalize_email(value)


class LoginRequest(BaseModel):
    email: str = Field(min_length=5, max_length=255)
    password: str = Field(min_length=6, max_length=128)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return normalize_email(value)


class PasswordResetRequest(BaseModel):
    email: str = Field(min_length=5, max_length=255)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return normalize_email(value)


class PasswordResetConfirm(BaseModel):
    email: str = Field(min_length=5, max_length=255)
    code: str = Field(min_length=6, max_length=12)
    new_password: str = Field(min_length=6, max_length=128)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return normalize_email(value)

    @field_validator("code")
    @classmethod
    def normalize_code(cls, value: str) -> str:
        code = "".join(ch for ch in value.strip() if ch.isdigit())
        if len(code) != 6:
            raise ValueError("Verification code must be 6 digits")
        return code


class MessageOut(BaseModel):
    message: str


class UserOut(BaseModel):
    id: int
    user_uid: str
    email: str
    display_name: str | None = None
    role: str
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


class DeviceIn(BaseModel):
    device_key: str = Field(min_length=1, max_length=120)
    name: str | None = Field(default=None, max_length=120)


class RecordIn(BaseModel):
    table_name: str = Field(min_length=1, max_length=80)
    local_id: str = Field(min_length=1, max_length=120)
    data: dict[str, Any] = Field(default_factory=dict)
    local_updated_at: str | None = Field(default=None, max_length=40)
    deleted_at: str | None = Field(default=None, max_length=40)
    source_device_key: str | None = Field(default=None, max_length=120)


class RecordOut(RecordIn):
    id: int
    user_uid: str
    sync_version: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class PushRequest(BaseModel):
    device: DeviceIn | None = None
    records: list[RecordIn] = Field(default_factory=list)
    note: str | None = None


class PushResponse(BaseModel):
    saved: int
    batch_id: int


class PullResponse(BaseModel):
    records: list[RecordOut]
    server_time: datetime


class SummaryItem(BaseModel):
    table_name: str
    records_count: int
    deleted_count: int
    last_updated_at: datetime | None = None


class SummaryResponse(BaseModel):
    user_id: int
    user_uid: str
    tables: list[SummaryItem]
