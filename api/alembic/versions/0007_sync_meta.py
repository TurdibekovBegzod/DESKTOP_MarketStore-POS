"""Add per-account sync generation metadata for realtime change notifications.

Revision ID: 0007_sync_meta
Revises: 0006_email_verification
Create Date: 2026-08-25
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0007_sync_meta"
down_revision: Union[str, None] = "0006_email_verification"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "sync_meta",
        sa.Column("user_uid", sa.String(length=36), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_change_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_device_key", sa.String(length=120), nullable=True),
        sa.Column("last_tables", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.ForeignKeyConstraint(["user_uid"], ["users.uid"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_uid"),
    )
    # Seed a row for every existing account so the first client poll is cheap.
    op.execute(
        "INSERT INTO sync_meta (user_uid, generation) "
        "SELECT uid, 0 FROM users ON CONFLICT (user_uid) DO NOTHING"
    )


def downgrade() -> None:
    op.drop_table("sync_meta")
