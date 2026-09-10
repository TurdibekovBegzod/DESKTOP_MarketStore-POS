"""Separate password for the desktop's main (admin) section.

Until now the main section was unlocked with the account's own e-mail password,
so the two could never differ. They start out identical - a new account gets the
same value in both - but from the moment the owner changes one, the other is
left alone.

``purpose`` splits the verification codes: a code mailed out to recover the main
section password must not also open the account itself.

Revision ID: 0012_admin_password
Revises: 0011_page_pull_index
Create Date: 2026-09-10
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0012_admin_password"
down_revision: Union[str, None] = "0011_page_pull_index"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Nullable on purpose: an account created before this migration has never
    # set a separate password, and NULL is what says "still the account one".
    op.add_column("users", sa.Column("admin_password_hash", sa.String(length=255), nullable=True))
    op.add_column(
        "password_reset_codes",
        sa.Column("purpose", sa.String(length=20), nullable=False, server_default="account"),
    )
    op.create_index(
        "ix_password_reset_codes_user_purpose",
        "password_reset_codes",
        ["user_id", "purpose"],
    )


def downgrade() -> None:
    op.drop_index("ix_password_reset_codes_user_purpose", table_name="password_reset_codes")
    op.drop_column("password_reset_codes", "purpose")
    op.drop_column("users", "admin_password_hash")
