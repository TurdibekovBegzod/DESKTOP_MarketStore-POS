"""Store the latest published desktop release so clients never poll GitHub.

Revision ID: 0008_app_release
Revises: 0007_sync_meta
Create Date: 2026-08-25
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0008_app_release"
down_revision: Union[str, None] = "0007_sync_meta"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "app_releases",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("tag", sa.String(length=80), nullable=False),
        sa.Column("version", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("assets", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("source", sa.String(length=20), nullable=False, server_default="ping"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("app_releases")
