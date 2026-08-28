"""Index account page downloads by tenant, table and change position.

Revision ID: 0011_page_pull_index
Revises: 0010_user_record_change_seq
Create Date: 2026-08-28
"""
from typing import Sequence, Union

from alembic import op


revision: str = "0011_page_pull_index"
down_revision: Union[str, None] = "0010_user_record_change_seq"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "ix_user_records_uid_table_change_seq_id",
        "user_records",
        ["user_uid", "table_name", "change_seq", "id"],
    )


def downgrade() -> None:
    op.drop_index("ix_user_records_uid_table_change_seq_id", table_name="user_records")
