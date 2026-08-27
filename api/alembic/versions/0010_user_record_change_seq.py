"""Give every stored row a place in the account's change history.

Downloads used to ask the server "what changed after this clock reading". That
question cannot be answered safely: a push stamps its rows with the time its
transaction opened, but the rows only appear when it commits. A download that
ran in between saw nothing, moved its marker past that timestamp, and those rows
stayed behind the marker forever -- two devices online side by side, both sure
they were current, holding different data.

``change_seq`` replaces the clock. It is handed out from the account generation,
which every push locks before advancing, so it is assigned in commit order.

Revision ID: 0010_user_record_change_seq
Revises: 0009_sync_remote_purge
Create Date: 2026-08-27
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0010_user_record_change_seq"
down_revision: Union[str, None] = "0009_sync_remote_purge"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "user_records",
        sa.Column("change_seq", sa.BigInteger(), nullable=False, server_default="0"),
    )
    # Everything already stored predates the counter. Numbering it all 0 means
    # the first download after the upgrade asks from 0 and is therefore a full
    # copy -- which is exactly what a device that may have missed rows needs.
    op.create_index(
        "ix_user_records_uid_change_seq",
        "user_records",
        ["user_uid", "change_seq"],
    )
    op.alter_column("user_records", "change_seq", server_default=None)


def downgrade() -> None:
    op.drop_index("ix_user_records_uid_change_seq", table_name="user_records")
    op.drop_column("user_records", "change_seq")
