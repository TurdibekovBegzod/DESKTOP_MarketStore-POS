"""Persist remote account purge markers for every desktop device.

Revision ID: 0009_sync_remote_purge
Revises: 0008_app_release
Create Date: 2026-08-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0009_sync_remote_purge"
down_revision: Union[str, None] = "0008_app_release"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "sync_meta",
        sa.Column("purge_generation", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "sync_meta",
        sa.Column("purge_requested_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("sync_meta", "purge_requested_at")
    op.drop_column("sync_meta", "purge_generation")
