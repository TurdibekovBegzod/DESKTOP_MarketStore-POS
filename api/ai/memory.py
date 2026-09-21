"""What the bot remembers of a customer, keyed by their Instagram id.

Redis rather than Postgres: this is context, not an archive. It answers "what
did *this* customer just say", never "what are all customers asking", and a key
that nobody touches for two weeks should simply stop existing. Redis does that
expiry itself; in Postgres it would be a cron job and a growing table.

The api and worker containers are separate processes, so an in-process dict
would be written by one and read by the other as empty.
"""

import json
import logging

import redis

from app.config import get_settings


logger = logging.getLogger(__name__)

# One week, as a returning customer's context. Past it the key is gone and
# the conversation starts clean - which is the intended behaviour, not a loss.
TTL_SECONDS = 7 * 24 * 3600

# Turns kept per conversation. Every turn is resent as input tokens on the next
# reply, so an uncapped history makes a long chat quietly more expensive with
# each message.
MAX_TURNS = 10

KEY_PREFIX = "conv:"


_client: redis.Redis | None = None


def get_client() -> redis.Redis | None:
    """The shared client, or None when no Redis URL is configured."""
    global _client
    if _client is not None:
        return _client

    url = (get_settings().conversation_redis_url or "").strip()
    if not url:
        return None

    _client = redis.Redis.from_url(url, decode_responses=True)
    return _client


def load(instagram_id: str) -> list[dict]:
    """The stored turns for this customer, oldest first.

    A missing key, expired key or unreachable Redis all mean the same thing to
    the caller: no context. Answering without history is worse than answering
    with it, but far better than not answering at all, so this never raises.
    """
    client = get_client()
    if client is None or not instagram_id:
        return []

    try:
        raw = client.get(f"{KEY_PREFIX}{instagram_id}")
    except redis.RedisError:
        logger.exception("conversation history unavailable for %s", instagram_id)
        return []

    if not raw:
        return []

    try:
        turns = json.loads(raw)
    except ValueError:
        # Corrupt value: drop it rather than fail every future reply.
        logger.warning("discarding unreadable history for %s", instagram_id)
        return []

    return turns if isinstance(turns, list) else []


def save(instagram_id: str, turns: list[dict]) -> None:
    """Replace this customer's history and restart the fourteen-day clock.

    Writing on every reply means the TTL measures silence, not age: a customer
    who writes weekly keeps their context indefinitely, which is what makes it
    feel like the shop remembers them.
    """
    client = get_client()
    if client is None or not instagram_id:
        return

    try:
        client.setex(
            f"{KEY_PREFIX}{instagram_id}",
            TTL_SECONDS,
            json.dumps(turns[-MAX_TURNS:], ensure_ascii=False),
        )
    except redis.RedisError:
        # The reply itself already went out; losing its history is not worth
        # failing the task and having Celery send the answer twice.
        logger.exception("could not store history for %s", instagram_id)


def clear(instagram_id: str) -> None:
    """Forget this customer now, without waiting for the TTL."""
    client = get_client()
    if client is None or not instagram_id:
        return

    try:
        client.delete(f"{KEY_PREFIX}{instagram_id}")
    except redis.RedisError:
        logger.exception("could not clear history for %s", instagram_id)
