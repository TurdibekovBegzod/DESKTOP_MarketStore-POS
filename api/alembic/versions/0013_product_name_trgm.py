"""Trigram index over synced product names, for the DM agent's lookups.

A customer types "airpods pro" or "aripods"; LIKE '%...%' cannot use an index
for either, so the alternative is a sequential scan of every synced row in the
account on every DM. pg_trgm indexes the name inside the JSONB envelope and
answers both the fuzzy match and its ranking.

Partial, because ``user_records`` holds every synced table and only the
products rows are ever searched this way.

Revision ID: 0013_product_name_trgm
Revises: 0012_admin_password
Create Date: 2026-09-21
"""
from typing import Sequence, Union

from alembic import op


revision: str = "0013_product_name_trgm"
down_revision: Union[str, None] = "0012_admin_password"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute(
        """
        CREATE INDEX ix_user_records_product_name_trgm
        ON user_records
        USING gin ((data ->> 'name') gin_trgm_ops)
        WHERE table_name = 'products' AND deleted_at IS NULL
        """
    )
    # The lookup filters by account before it ever reaches the trigram match,
    # so that part of the WHERE clause needs its own index to stay cheap.
    op.execute(
        """
        CREATE INDEX ix_user_records_user_products
        ON user_records (user_id)
        WHERE table_name = 'products' AND deleted_at IS NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_user_records_user_products")
    op.execute("DROP INDEX IF EXISTS ix_user_records_product_name_trgm")
    # The extension is left in place: other work may have started using it.
