"""Initial API sync schema.

Revision ID: 0001_initial
Revises:
Create Date: 2026-08-02
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0001_initial"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    op.create_table(
        "devices",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("device_key", sa.String(length=120), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "device_key", name="uq_devices_user_device_key"),
    )
    op.create_index("ix_devices_user_id", "devices", ["user_id"])

    op.create_table(
        "sync_batches",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("device_id", sa.Integer(), sa.ForeignKey("devices.id", ondelete="SET NULL"), nullable=True),
        sa.Column("direction", sa.String(length=16), nullable=False),
        sa.Column("records_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_sync_batches_user_id", "sync_batches", ["user_id"])

    op.create_table(
        "user_records",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("table_name", sa.String(length=80), nullable=False),
        sa.Column("local_id", sa.String(length=120), nullable=False),
        sa.Column("data", postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("local_updated_at", sa.String(length=40), nullable=True),
        sa.Column("deleted_at", sa.String(length=40), nullable=True),
        sa.Column("source_device_key", sa.String(length=120), nullable=True),
        sa.Column("sync_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("user_id", "table_name", "local_id", name="uq_user_records_user_table_local"),
    )
    op.create_index("ix_user_records_user_id", "user_records", ["user_id"])
    op.create_index("ix_user_records_table_name", "user_records", ["table_name"])
    op.create_index("ix_user_records_local_updated_at", "user_records", ["local_updated_at"])
    op.create_index("ix_user_records_deleted_at", "user_records", ["deleted_at"])
    op.create_index("ix_user_records_data_gin", "user_records", ["data"], postgresql_using="gin")


def downgrade() -> None:
    op.drop_index("ix_user_records_data_gin", table_name="user_records")
    op.drop_table("user_records")
    op.drop_table("sync_batches")
    op.drop_table("devices")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
