"""Add Google OAuth sessions.

Revision ID: 0004_google_oauth_sessions
Revises: 0003_user_roles
Create Date: 2026-08-03
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0004_google_oauth_sessions"
down_revision: Union[str, None] = "0003_user_roles"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "google_oauth_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("state", sa.String(length=120), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("access_token", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_google_oauth_sessions_state", "google_oauth_sessions", ["state"], unique=True)
    op.create_index("ix_google_oauth_sessions_user_id", "google_oauth_sessions", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_google_oauth_sessions_user_id", table_name="google_oauth_sessions")
    op.drop_index("ix_google_oauth_sessions_state", table_name="google_oauth_sessions")
    op.drop_table("google_oauth_sessions")
