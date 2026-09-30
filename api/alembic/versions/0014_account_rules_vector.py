"""Per-account agent rules, searched by meaning rather than by keyword.

Every shop writes its own rules - delivery terms, warranty, payment, whatever it
wants the bot to know - and the agent looks them up before it answers. The set
grows year on year and nobody knows how large it gets, so the rules are embedded
and retrieved by similarity instead of all being pushed into the prompt.

``embedding`` is 384-dimensional because that is what multilingual-e5-small
produces. A different model means a different vector space, so ``model`` is
stored next to every row: mixing two models' vectors does not raise anything, it
just quietly returns the wrong rules.

``raw_text`` and ``text_hash`` are what make re-embedding cheap. The hash says
whether the text behind a vector changed; without it, every sync would have to
re-embed the whole account to find the one rule that was edited.

The index story is the part that is easy to get wrong. HNSW applies the WHERE
clause *after* it has walked the graph, so a shared HNSW index asked for the 5
nearest rules of one account returns however many of its global 40 candidates
happen to belong to that account - often one, sometimes none. A btree on
user_uid is what keeps this correct: for an account holding a small slice of the
table, Postgres scans that slice and ranks it exactly, so "the 5 nearest rules
of this account" really is that. HNSW is added only as the fallback for when one
account grows large enough that an exact scan stops being cheap, and the query
side turns on iterative scans so a filtered search still fills its LIMIT.

Revision ID: 0014_account_rules_vector
Revises: 0013_product_name_trgm
Create Date: 2026-09-30
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0014_account_rules_vector"
down_revision: Union[str, None] = "0013_product_name_trgm"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# multilingual-e5-small's native width. Not truncated: 384 is already small
# enough that shrinking it would cost recall for no saving that matters here.
EMBEDDING_DIM = 384


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "account_rules",
        sa.Column("id", sa.Integer(), primary_key=True),
        # The rule's identity as the desktop app knows it. Updates and deletes
        # address a rule by (account, local_id), which is why it is unique per
        # account rather than globally: two shops may both hold a rule whose
        # local id was generated on their own device.
        sa.Column("local_id", sa.String(length=120), nullable=False),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_uid",
            sa.String(length=36),
            sa.ForeignKey("users.uid", ondelete="CASCADE"),
            nullable=False,
        ),
        # What the shop owner wrote. Kept verbatim: it is both what the agent
        # reads out and what the embedding is checked against.
        sa.Column("raw_text", sa.Text(), nullable=False),
        # sha256 of the embedded text. Compared instead of the text itself so a
        # sync over thousands of rules does not ship or compare long strings.
        sa.Column("text_hash", sa.String(length=64), nullable=False),
        # The vector itself is added by the ALTER below: pgvector's type is not
        # one SQLAlchemy knows without the pgvector package installed, and this
        # image does not carry it - only the embedder's does.
        sa.Column("model", sa.String(length=80), nullable=True),
        # Which rule wins when two of them say opposite things. Similarity has
        # no opinion about that, so the shop states it.
        sa.Column("priority", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("user_uid", "local_id", name="uq_account_rules_uid_local"),
    )

    # Raw DDL, because the column's type comes from the extension rather than
    # from SQLAlchemy: expressing it through sa.Column would mean importing
    # pgvector here, and the api image installs only what it needs to serve.
    #
    # Null until the embedder has run over the row. That is a normal state, not
    # a failure - saving a rule must never wait on a model - and it means a new
    # rule is in the app immediately while being briefly unsearchable.
    op.execute(f"ALTER TABLE account_rules ADD COLUMN embedding vector({EMBEDDING_DIM})")

    # The account filter, and the reason filtered search stays correct - see the
    # module docstring. This is the index the planner is meant to choose while an
    # account's rules are a small slice of the table.
    op.create_index("ix_account_rules_user_uid", "account_rules", ["user_uid"])
    op.create_index("ix_account_rules_user_id", "account_rules", ["user_id"])

    # The fallback for a single account large enough that ranking its whole slice
    # stops being cheap. Partial per-account indexes were considered and
    # rejected: shops are added over time and each one would mean another index
    # to build, store and vacuum.
    #
    # Rows whose vector is still NULL are excluded: they cannot be ranked, and
    # leaving them out keeps the graph to what is actually searchable.
    op.execute(
        """
        CREATE INDEX ix_account_rules_embedding_hnsw
        ON account_rules
        USING hnsw (embedding vector_cosine_ops)
        WHERE embedding IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_account_rules_embedding_hnsw")
    op.drop_index("ix_account_rules_user_id", table_name="account_rules")
    op.drop_index("ix_account_rules_user_uid", table_name="account_rules")
    op.drop_table("account_rules")
    # The extension is left in place: other work may have started using it.
