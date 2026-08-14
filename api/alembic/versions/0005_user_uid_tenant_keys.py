"""Add stable user UID tenant keys.

Revision ID: 0005_user_uid_tenant_keys
Revises: 0004_google_oauth_sessions
Create Date: 2026-08-08
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0005_user_uid_tenant_keys"
down_revision: Union[str, None] = "0004_google_oauth_sessions"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


TENANT_TABLES = ("devices", "sync_batches", "user_records")


def upgrade() -> None:
    conn = op.get_bind()

    op.add_column("users", sa.Column("uid", sa.String(length=36), nullable=True))
    conn.execute(
        sa.text(
            """
            UPDATE users
            SET uid = substr(uid_hash, 1, 8) || '-' || substr(uid_hash, 9, 4) || '-' ||
                      substr(uid_hash, 13, 4) || '-' || substr(uid_hash, 17, 4) || '-' ||
                      substr(uid_hash, 21, 12)
            FROM (
                SELECT id, md5(random()::text || clock_timestamp()::text || id::text) AS uid_hash
                FROM users
                WHERE uid IS NULL
            ) generated
            WHERE users.id = generated.id
            """
        )
    )
    op.alter_column("users", "uid", existing_type=sa.String(length=36), nullable=False)
    op.create_index("ix_users_uid", "users", ["uid"], unique=True)

    for table_name in TENANT_TABLES:
        op.add_column(table_name, sa.Column("user_uid", sa.String(length=36), nullable=True))
        conn.execute(
            sa.text(
                f"""
                UPDATE {table_name}
                SET user_uid = users.uid
                FROM users
                WHERE {table_name}.user_id = users.id
                """
            )
        )
        op.alter_column(table_name, "user_uid", existing_type=sa.String(length=36), nullable=False)
        op.create_index(f"ix_{table_name}_user_uid", table_name, ["user_uid"])
        op.create_foreign_key(
            f"fk_{table_name}_user_uid_users",
            table_name,
            "users",
            ["user_uid"],
            ["uid"],
            ondelete="CASCADE",
        )

    op.create_unique_constraint("uq_devices_user_uid_device_key", "devices", ["user_uid", "device_key"])
    op.create_unique_constraint(
        "uq_user_records_user_uid_table_local",
        "user_records",
        ["user_uid", "table_name", "local_id"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_user_records_user_uid_table_local", "user_records", type_="unique")
    op.drop_constraint("uq_devices_user_uid_device_key", "devices", type_="unique")

    for table_name in reversed(TENANT_TABLES):
        op.drop_constraint(f"fk_{table_name}_user_uid_users", table_name, type_="foreignkey")
        op.drop_index(f"ix_{table_name}_user_uid", table_name=table_name)
        op.drop_column(table_name, "user_uid")

    op.drop_index("ix_users_uid", table_name="users")
    op.drop_column("users", "uid")
