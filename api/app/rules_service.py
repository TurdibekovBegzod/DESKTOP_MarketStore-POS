"""Storing and retrieving one account's agent rules.

A rule is a sentence the shop wants its bot to know - delivery terms, warranty,
payment, anything. Each shop owns its own set, and the agent is given all of it
in its system prompt (``list_rules`` + ``format_all_for_prompt``).

**The account filter comes first, always.** Every statement below is scoped to
one ``user_uid``. This mirrors ai/tools.py, and for the same reason: one shop must
never be answered out of another shop's rules.
"""

from dataclasses import dataclass
import logging

from sqlalchemy import text

from app.database import SessionLocal


logger = logging.getLogger(__name__)


@dataclass
class AccountRule:
    """One rule as the agent sees it."""

    local_id: str
    text: str
    priority: int


def list_rules(user_uid: str) -> list[AccountRule]:
    """Every rule this account has stored, highest priority first.

    This is what the agent's system prompt is built from: the whole set, read
    at reply time, so a rule the shop just wrote applies from the next message.

    Never raises: a failed lookup degrades to "no rules" rather than costing the
    customer a reply.
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
        AccountRule(local_id=row[0], text=row[1], priority=row[2])
        for row in rows
        if (row[1] or "").strip()
    ]


def upsert_rule(user_id: int, user_uid: str, local_id: str, raw_text: str, priority: int = 0) -> bool:
    """Store or replace one rule. Returns whether anything was written."""
    account = str(user_uid or "").strip()
    rule_id = str(local_id or "").strip()
    body = (raw_text or "").strip()
    if not account or not rule_id or not body:
        return False

    try:
        with SessionLocal() as session:
            session.execute(
                text(
                    """
                    INSERT INTO account_rules (local_id, user_id, user_uid, raw_text, priority)
                    VALUES (:local_id, :user_id, :user_uid, :raw_text, :priority)
                    ON CONFLICT (user_uid, local_id) DO UPDATE SET
                        raw_text = EXCLUDED.raw_text,
                        priority = EXCLUDED.priority,
                        updated_at = now()
                    """
                ),
                {
                    "local_id": rule_id,
                    "user_id": user_id,
                    "user_uid": account,
                    "raw_text": body,
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
    handles it; this table holds only rules that still exist, so nobody reading
    it has to wonder which rows are still live.

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


def format_all_for_prompt(rules: list[AccountRule]) -> str:
    """The account's whole rule set as the system prompt's closing block.

    The header says this is the complete set: every rule here is the shop's own
    and binding, and a question none of them covers really has no answer from
    the shop. Order is kept as given - ``list_rules`` already puts higher
    priority first.
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
