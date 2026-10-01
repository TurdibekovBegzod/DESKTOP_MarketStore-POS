"""Drop the rule embeddings: the agent reads every rule, it never searches them.

The rules go into the system prompt whole (ai.agent.system_prompt_for), so the
vector, the model that made it and the hash that decided when to remake it have
no reader left. The rules themselves - raw_text and priority - are untouched.

The ``vector`` extension is left installed, as 0014's downgrade already does:
dropping it would fail this migration, and with it the api's start, the moment
anything else had come to depend on it.

Revision ID: 0015_drop_rule_embeddings
Revises: 0014_account_rules_vector
Create Date: 2026-10-01
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0015_drop_rule_embeddings"
down_revision: Union[str, None] = "0014_account_rules_vector"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_account_rules_embedding_hnsw")
    op.execute("ALTER TABLE account_rules DROP COLUMN IF EXISTS embedding")
    op.execute("ALTER TABLE account_rules DROP COLUMN IF EXISTS model")
    op.execute("ALTER TABLE account_rules DROP COLUMN IF EXISTS text_hash")


def downgrade() -> None:
    # Restores 0014's shape. Every vector comes back NULL, which is the state the
    # embedder already treats as "not done yet", so it rebuilds them on its own.
    op.add_column("account_rules", sa.Column("text_hash", sa.String(length=64), nullable=True))
    op.execute("UPDATE account_rules SET text_hash = encode(sha256(convert_to(raw_text, 'UTF8')), 'hex')")
    op.alter_column("account_rules", "text_hash", nullable=False)
    op.add_column("account_rules", sa.Column("model", sa.String(length=80), nullable=True))
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("ALTER TABLE account_rules ADD COLUMN embedding vector(384)")
    op.execute(
        """
        CREATE INDEX ix_account_rules_embedding_hnsw
        ON account_rules
        USING hnsw (embedding vector_cosine_ops)
        WHERE embedding IS NOT NULL
        """
    )
