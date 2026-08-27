import base64
import binascii
import hashlib
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


ALLOWED_SYNC_TABLES = frozenset({
    "users", "categories", "currencies", "app_settings", "account_assets", "product_sections",
    "product_templates", "product_template_fields", "products", "product_attributes",
    "customers", "suppliers", "supplier_debt_movements", "debtors",
    "debtor_debt_movements", "expense_categories", "expenses", "sales", "sale_items",
    "stock_movements", "inventory_check_sessions", "inventory_check_items",
    "finance_manual_movements",
})


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


class RegistrationStart(BaseModel):
    email: str = Field(min_length=5, max_length=255)
    display_name: str | None = Field(default=None, max_length=120)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return normalize_email(value)


class RegistrationVerify(BaseModel):
    email: str = Field(min_length=5, max_length=255)
    code: str = Field(min_length=6, max_length=12)
    password: str | None = Field(default=None, min_length=6, max_length=128)

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


class RegistrationResend(BaseModel):
    email: str = Field(min_length=5, max_length=255)

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return normalize_email(value)


class MessageOut(BaseModel):
    message: str


class RegistrationChallengeOut(BaseModel):
    message: str
    expires_in_seconds: int
    resend_after_seconds: int


class UserOut(BaseModel):
    id: int
    user_uid: str
    email: str
    display_name: str | None = None
    role: str
    is_active: bool
    email_verified_at: datetime | None = None
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
    # "I am changing the row as I last saw it." Left unset for a row the sender
    # believes is new, which is why a sale can never be refused.
    expected_version: int | None = Field(default=None, ge=0)

    @field_validator("table_name")
    @classmethod
    def validate_table_name(cls, value: str) -> str:
        if value not in ALLOWED_SYNC_TABLES:
            raise ValueError("Unsupported sync table")
        return value

    @model_validator(mode="after")
    def validate_account_asset(self):
        if self.table_name != "account_assets" or (self.deleted_at and not self.data):
            return self
        content = self.data.get("content_base64")
        if not isinstance(content, str) or len(content) > 700_000:
            raise ValueError("Account asset payload is invalid or too large")
        try:
            raw = base64.b64decode(content, validate=True)
        except (ValueError, binascii.Error):
            raise ValueError("Account asset must contain valid base64") from None
        if not raw or len(raw) > 512 * 1024:
            raise ValueError("Account asset must be between 1 byte and 512 KB")
        if self.data.get("media_type") != "image/png" or not raw.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("Only PNG account assets are supported")
        if len(raw) < 24 or raw[12:16] != b"IHDR":
            raise ValueError("Account asset PNG header is invalid")
        width = int.from_bytes(raw[16:20], "big")
        height = int.from_bytes(raw[20:24], "big")
        if width < 1 or height < 1 or width > 1024 or height > 1024:
            raise ValueError("Account asset dimensions must be between 1 and 1024 pixels")
        expected_digest = hashlib.sha256(raw).hexdigest()
        if self.data.get("sha256") != expected_digest:
            raise ValueError("Account asset checksum does not match")
        if str(self.data.get("id") or "") != self.local_id:
            raise ValueError("Account asset id does not match local_id")
        return self


class RecordOut(RecordIn):
    id: int
    user_uid: str
    sync_version: int
    # Where this row sits in the account's change history. Devices download by
    # this number instead of by a clock reading.
    change_seq: int = 0
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class PushRequest(BaseModel):
    device: DeviceIn | None = None
    records: list[RecordIn] = Field(default_factory=list, max_length=1000)
    note: str | None = Field(default=None, max_length=500)
    # When set, the server refuses the push with 409 if another device has
    # written since the caller last synced. Omit it to force the write through.
    expected_generation: int | None = Field(default=None, ge=0)
    # A server-side purge always wins over an old desktop snapshot. Clients
    # must acknowledge the newest purge before they can write anything back.
    applied_purge_generation: int | None = Field(default=None, ge=0)


class RejectedRecordOut(BaseModel):
    """One row the server would not overwrite, and what it holds instead."""

    table_name: str
    local_id: str
    expected_version: int | None = None
    server_version: int | None = None


class PushResponse(BaseModel):
    saved: int
    batch_id: int
    generation: int = 0
    rejected: list[RejectedRecordOut] = Field(default_factory=list)


class PullResponse(BaseModel):
    records: list[RecordOut]
    server_time: datetime
    generation: int = 0
    purge_generation: int = 0
    purge_requested_at: datetime | None = None
    has_more: bool = False
    next_offset: int | None = None
    # Highest change number in this response, and the flag that tells an
    # upgraded client it may stop downloading by clock reading altogether.
    cursor: int = 0
    cursor_supported: bool = True


class ResetResponse(BaseModel):
    removed: int
    generation: int


class SyncStateOut(BaseModel):
    generation: int
    purge_generation: int = 0
    purge_requested_at: datetime | None = None
    last_change_at: datetime | None = None
    last_device_key: str | None = None
    last_tables: list[str] = Field(default_factory=list)
    records_count: int = 0
    # Highest change number stored for the account. A device whose marker is
    # below this still has something to collect.
    cursor: int = 0
    cursor_supported: bool = True
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


class SuperadminLoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=1, max_length=256)


class SuperadminTokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in_seconds: int


class SuperadminAvailabilityOut(BaseModel):
    """Whether the control panel is configured on this server."""

    enabled: bool
    message: str = ""


class SuperadminConfirmRequest(BaseModel):
    confirm_email: str = Field(min_length=5, max_length=255)


class SuperadminStatusRequest(SuperadminConfirmRequest):
    is_active: bool


class SuperadminAccountOut(BaseModel):
    user_uid: str
    email: str
    display_name: str | None = None
    role: str
    is_active: bool
    is_verified: bool
    created_at: datetime
    updated_at: datetime
    records_count: int = 0
    deleted_records_count: int = 0
    devices_count: int = 0
    sync_batches_count: int = 0
    last_activity_at: datetime | None = None


class SuperadminOverviewOut(BaseModel):
    accounts: list[SuperadminAccountOut]
    total_accounts: int
    active_accounts: int
    total_records: int
    total_devices: int


class SuperadminActionOut(BaseModel):
    message: str
    removed_records: int = 0
    removed_devices: int = 0
    removed_batches: int = 0
