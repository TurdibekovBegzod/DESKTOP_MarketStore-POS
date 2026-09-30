"""Storing and retrieving one account's agent rules.

A rule is a sentence the shop wants its bot to know - delivery terms, warranty,
payment, anything. Each shop owns its own set, and the agent is given all of it
in its system prompt (``list_rules`` + ``format_all_for_prompt``). The similarity
search below is kept for when a set outgrows the prompt; nothing calls it today.

Three things in here are load-bearing:

**The account filter comes first, always.** Every statement below is scoped to
one ``user_uid``. This mirrors ai/tools.py, and for the same reason: one shop must
never be answered out of another shop's rules.

**Similarity search with a filter is not the obvious SQL.** An HNSW index applies
WHERE *after* it has walked its graph, so the natural-looking query returns
whichever of its global candidates happen to belong to this account - frequently
fewer than the LIMIT, sometimes none. Two things fix it: a btree on user_uid, so
Postgres can scan one account's slice and rank it exactly; and, when it falls
back to the HNSW index anyway, ``hnsw.iterative_scan``, which keeps pulling
candidates until the LIMIT is filled. Both are set up here - the migration
0014 docstring has the longer explanation.

**There is no distance threshold at all.** Whatever the account has stored, its
best five come back; deciding whether any of them answers the question is the
model's job, and SYSTEM_PROMPT tells it to say it has no information when none
does.

That is also what measurement supports. Over a shop's rules in Uzbek, e5-small
returns everything in a narrow band - on-topic questions at 0.169-0.216 and pure
noise ("salom", "ob-havo qanday") at 0.188-0.244 - so the two ranges overlap and
any cut loses real rules to exclude noise: at 0.20 it drops 2 of 12 genuine
matches and still admits 2 of 10 noise ones. The *ranking* is what holds up: the
right rule came first for 10 of 12 questions and was in the top 3 for all 12. A
threshold would throw that away; the model reads the five and judges them.
"""

from dataclasses import dataclass
import logging

from sqlalchemy import text

from ai import embedding
from app.database import SessionLocal


logger = logging.getLogger(__name__)

# How many rules the agent is given for one question. Enough to cover a question
# that touches two policies, small enough to leave the prompt about the customer.
MAX_RULES = 5

# Candidate list size for the HNSW fallback. The default of 40 is what makes a
# filtered search come back short, so it is raised for these queries only.
EF_SEARCH = 200


@dataclass
class AccountRule:
    """One rule as the agent sees it."""

    local_id: str
    text: str
    priority: int
    distance: float


def _vector_literal(vector: list[float]) -> str:
    """A vector as pgvector's text input form.

    Bound as a string and cast in SQL rather than adapted as a list: that keeps
    the pgvector Python package off the dependency list, and the value still
    travels as a parameter, never as concatenated SQL.
    """
    return "[" + ",".join(repr(float(value)) for value in vector) + "]"


def search_rules(user_uid: str, question: str, limit: int = MAX_RULES) -> list[AccountRule]:
    """The account's ``limit`` rules nearest in meaning to ``question``.

    No relevance filtering: whatever this account has stored, its best few come
    back, and the model decides which of them - if any - actually answers the
    question. Fewer than ``limit`` only means the account holds fewer embedded
    rules than that, and an empty list only means it has none yet.

    Never raises. A rule lookup that fails must not cost the customer a reply; it
    degrades to "no rules", and the agent falls back on what it always knew.
    """
    account = str(user_uid or "").strip()
    message = (question or "").strip()
    if not account or not message:
        return []

    try:
        query_vector = embedding.embed_query(message)
    except embedding.EmbeddingUnavailableError as exc:
        # Expected whenever the model is not in this image - the web worker does
        # not carry it. Not an error: search is simply unavailable here.
        logger.info("Rule search skipped, embedding unavailable: %s", exc)
        return []
    except Exception:
        logger.exception("Rule search failed while embedding the question")
        return []

    try:
        with SessionLocal() as session:
            # Only for this transaction. Set unconditionally: the planner decides
            # between the btree and HNSW on its own, and this is what keeps the
            # HNSW branch from returning a short list.
            session.execute(text("SET LOCAL hnsw.iterative_scan = strict_order"))
            session.execute(text(f"SET LOCAL hnsw.ef_search = {EF_SEARCH}"))
            rows = session.execute(
                text(
                    """
                    SELECT local_id, raw_text, priority,
                           embedding <=> CAST(:query_vector AS vector) AS distance
                    FROM account_rules
                    WHERE user_uid = :user_uid
                      AND embedding IS NOT NULL
                    ORDER BY distance ASC, priority DESC
                    LIMIT :limit
                    """
                ),
                {
                    "user_uid": account,
                    "query_vector": _vector_literal(query_vector),
                    "limit": max(1, int(limit)),
                },
            ).all()
    except Exception:
        logger.exception("Rule search failed for account %s", account)
        return []

    return [
        AccountRule(local_id=row[0], text=row[1], priority=row[2], distance=float(row[3]))
        for row in rows
    ]


def list_rules(user_uid: str) -> list[AccountRule]:
    """Every rule this account has stored, highest priority first.

    This is what the agent's system prompt is built from: the whole set, not a
    search over it. No embedding is needed, so a rule the embedder has not
    reached yet is included all the same - the shop wrote it, so the bot knows it
    from the next message on.

    Never raises, like ``search_rules``: a failed lookup degrades to "no rules"
    rather than costing the customer a reply.
    """
    account = str(user_uid or "").strip()
    if not account:
        return []

    try:
        with SessionLocal() as session:
            rows = session.execute(
                text(
                    """
                    SELECT local_id, raw_text, priority
                    FROM account_rules
                    WHERE user_uid = :user_uid
                    ORDER BY priority DESC, created_at ASC, id ASC
                    """
                ),
                {"user_uid": account},
            ).all()
    except Exception:
        logger.exception("Rule listing failed for account %s", account)
        return []

    return [
        AccountRule(local_id=row[0], text=row[1], priority=row[2], distance=0.0)
        for row in rows
        if (row[1] or "").strip()
    ]


def upsert_rule(user_id: int, user_uid: str, local_id: str, raw_text: str, priority: int = 0) -> bool:
    """Store or replace one rule, leaving it queued for embedding.

    The vector is cleared whenever the text changes, so the row is briefly
    unsearchable rather than searchable under its old meaning - the safer of the
    two, since a stale vector would retrieve the rule for the wrong questions.
    Text that has not changed keeps its vector and costs no re-embedding.

    Returns whether anything was written.
    """
    account = str(user_uid or "").strip()
    rule_id = str(local_id or "").strip()
    body = (raw_text or "").strip()
    if not account or not rule_id or not body:
        return False

    new_hash = embedding.text_hash(body)
    try:
        with SessionLocal() as session:
            session.execute(
                text(
                    """
                    INSERT INTO account_rules
                        (local_id, user_id, user_uid, raw_text, text_hash, priority, embedding, model)
                    VALUES
                        (:local_id, :user_id, :user_uid, :raw_text, :text_hash, :priority, NULL, NULL)
                    ON CONFLICT (user_uid, local_id) DO UPDATE SET
                        raw_text = EXCLUDED.raw_text,
                        priority = EXCLUDED.priority,
                        text_hash = EXCLUDED.text_hash,
                        -- Only the changed rows lose their vector. Comparing the
                        -- hash rather than the text keeps this cheap on a sync
                        -- that resends an account's whole rule set unchanged.
                        embedding = CASE
                            WHEN account_rules.text_hash = EXCLUDED.text_hash THEN account_rules.embedding
                            ELSE NULL
                        END,
                        model = CASE
                            WHEN account_rules.text_hash = EXCLUDED.text_hash THEN account_rules.model
                            ELSE NULL
                        END,
                        updated_at = now()
                    """
                ),
                {
                    "local_id": rule_id,
                    "user_id": user_id,
                    "user_uid": account,
                    "raw_text": body,
                    "text_hash": new_hash,
                    "priority": int(priority or 0),
                },
            )
            session.commit()
            return True
    except Exception:
        logger.exception("Failed to store rule %s for account %s", rule_id, account)
        return False


def delete_rule(user_uid: str, local_id: str) -> bool:
    """Remove one rule from this account for good.

    A real DELETE, not a flag. The tombstone that carries the deletion to the
    shop's other devices lives in ``user_records`` where the rest of sync already
    handles it; this table holds only rules that still exist, so nothing here has
    to be filtered out of a search and nobody reading it has to wonder which rows
    are still live.

    Scoped to the account as well as the id: ``local_id`` comes from a device, and
    one shop's id must never be able to address another shop's rule.
    """
    account = str(user_uid or "").strip()
    rule_id = str(local_id or "").strip()
    if not account or not rule_id:
        return False

    try:
        with SessionLocal() as session:
            result = session.execute(
                text(
                    """
                    DELETE FROM account_rules
                    WHERE user_uid = :user_uid AND local_id = :local_id
                    """
                ),
                {"user_uid": account, "local_id": rule_id},
            )
            session.commit()
            return result.rowcount > 0
    except Exception:
        logger.exception("Failed to delete rule %s for account %s", rule_id, account)
        return False


def table_is_missing(exc: Exception) -> bool:
    """Whether this failure is "account_rules does not exist yet".

    The table is created by the alembic run in the api container's CMD, so on a
    release that adds a migration there is a window where the embedder is up and
    the table is not. That is a normal part of a deploy, not a fault, and it is
    worth one quiet line rather than a traceback every few seconds.
    """
    return "UndefinedTable" in type(exc).__name__ or "account_rules" in str(exc) and "does not exist" in str(exc)


def pending_rules(limit: int = 200) -> list[tuple[int, str]]:
    """Rules waiting for a vector, as (id, raw_text).

    Ordered oldest first so a rule cannot be starved by newer ones arriving. The
    limit keeps one pass bounded: the embedder loops until this returns nothing.
    """
    try:
        with SessionLocal() as session:
            rows = session.execute(
                text(
                    """
                    SELECT id, raw_text
                    FROM account_rules
                    WHERE embedding IS NULL
                    ORDER BY updated_at ASC
                    LIMIT :limit
                    """
                ),
                {"limit": max(1, int(limit))},
            ).all()
    except Exception as exc:
        if table_is_missing(exc):
            # Mid-deploy: the api container has not run alembic yet. The next
            # pass picks the work up, so this needs a line, not a traceback.
            logger.info("account_rules is not there yet; waiting for the migration")
        else:
            logger.exception("Failed to list rules awaiting embedding")
        return []
    return [(row[0], row[1]) for row in rows]


def store_embeddings(items: list[tuple[int, list[float]]], model_name: str) -> int:
    """Attach computed vectors to their rows. Returns how many were stored.

    The text hash is re-checked in the UPDATE: between reading a row and encoding
    it, the shop may have edited that rule. Writing the vector anyway would pin
    the new text to the old meaning, so a row that moved on is skipped and picked
    up again on the next pass.
    """
    if not items:
        return 0

    stored = 0
    try:
        with SessionLocal() as session:
            for rule_id, vector in items:
                if len(vector) != embedding.DIMENSIONS:
                    logger.error(
                        "Refusing to store a %d-dimensional vector for rule %s; the column is vector(%d)",
                        len(vector),
                        rule_id,
                        embedding.DIMENSIONS,
                    )
                    continue
                result = session.execute(
                    text(
                        """
                        UPDATE account_rules
                        SET embedding = CAST(:vector AS vector),
                            model = :model
                        WHERE id = :id AND embedding IS NULL
                        """
                    ),
                    {"id": rule_id, "vector": _vector_literal(vector), "model": model_name},
                )
                stored += result.rowcount
            session.commit()
    except Exception:
        logger.exception("Failed to store computed rule embeddings")
        return 0
    return stored


def format_for_prompt(rules: list[AccountRule]) -> str:
    """The rules as a block for the system prompt, or "" when there are none.

    Higher priority first, because that is the order the model should read a
    contradiction in. Distances are left out: they would invite the model to
    reason about its own retrieval instead of about the rules.
    """
    if not rules:
        return ""
    ordered = sorted(rules, key=lambda rule: (-rule.priority, rule.distance))
    lines = "\n".join(f"- {rule.text}" for rule in ordered)
    # Deliberately not "rules for this question": nothing was filtered, so some
    # of these will not bear on it at all. Saying otherwise would push the model
    # to apply a rule that merely ranked nearby - see the module docstring.
    return (
        "DO'KON QOIDALARI (bazadagi eng o'xshash qoidalar, saralanmagan - "
        f"faqat savolga javob beradiganini ishlat):\n{lines}"
    )


def format_all_for_prompt(rules: list[AccountRule]) -> str:
    """The account's whole rule set as the system prompt's closing block.

    Unlike ``format_for_prompt`` this is the complete set, so the header says
    so: every rule here is the shop's own and binding, and a question none of
    them covers really has no answer from the shop. Order is kept as given -
    ``list_rules`` already puts higher priority first.
    """
    lines = "\n".join(f"- {rule.text.strip()}" for rule in rules if (rule.text or "").strip())
    if not lines:
        return (
            "DO'KON QOIDALARI: do'kon hali birorta ham qoida yozmagan. Do'kon "
            "sharti so'ralsa, \"bu bo'yicha ma'lumotim yo'q\" de va operatorga "
            "yo'naltir."
        )
    return (
        "DO'KON QOIDALARI (do'kon egasi yozgan barcha qoidalar, muhimi "
        f"yuqorida):\n{lines}"
    )
