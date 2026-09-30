"""The service that turns saved rules into vectors.

Its own process, and deliberately not part of the Celery worker. There is memory
to spare on this server, so the reason is CPU: encoding a few thousand rules
saturates the cores it is given, and a worker doing that is a worker not
answering Instagram DMs. Kept apart, an indexing run and a customer's question
never compete - and this process can be stopped, restarted or starved of CPU
without a single reply going missing.

It polls rather than taking a queue message. The work is idempotent and defined
entirely by the table ("rows whose embedding is NULL"), so a poll recovers by
itself from a crash mid-batch, a rule saved while the service was down, or a
vector cleared by an edit. A queue would have to be drained correctly for the
same guarantee, and a lost message would leave a rule permanently unsearchable.

The model is loaded on the first batch, not at import. Nothing here should keep
the container from starting: a bad download or a missing dependency then shows up
as rules staying unsearchable, which is visible and recoverable, rather than as a
crash loop.
"""

import logging
import os
import signal
import time

from ai import embedding
from app import rules_service


logger = logging.getLogger(__name__)

# Rows per encode call. Batching is most of the model's throughput; the size is
# capped so one pass holds a bounded number of texts in memory and a shutdown
# never has to wait long.
BATCH_SIZE = 32

# How long to wait after finding nothing to do. Rules are written by hand, so
# fresh work arrives in ones and twos - a few seconds of latency is invisible to
# the shop and keeps this process near-idle.
IDLE_SLEEP_SECONDS = float(os.environ.get("EMBEDDER_IDLE_SECONDS", "5"))

# Backoff after a failure, so a database that is down or a model that will not
# load produces one log line every half minute instead of a flood.
ERROR_SLEEP_SECONDS = float(os.environ.get("EMBEDDER_ERROR_SECONDS", "30"))

_stop = False


def _request_stop(signum, _frame):
    """Finish the batch in hand, then exit.

    Docker sends SIGTERM and waits ten seconds. Stopping between batches rather
    than inside one means a restart never leaves half a batch with vectors and
    half without - though even that would be self-correcting, since anything
    still NULL is simply picked up next time.
    """
    global _stop
    logger.info("Embedder received signal %s, stopping after this batch", signum)
    _stop = True


def process_one_batch() -> int:
    """Embed one batch of waiting rules. Returns how many rows were stored.

    Zero means there was nothing to do, which is the normal state.
    """
    pending = rules_service.pending_rules(limit=BATCH_SIZE)
    if not pending:
        return 0

    ids = [row[0] for row in pending]
    texts = [row[1] for row in pending]
    vectors = embedding.embed_passages(texts)
    if len(vectors) != len(ids):
        # Cannot pair a vector with its row, and guessing would attach the wrong
        # meaning to a rule. Left NULL for the next pass.
        logger.error(
            "Embedder got %d vectors for %d rules; skipping this batch",
            len(vectors),
            len(ids),
        )
        return 0

    stored = rules_service.store_embeddings(
        list(zip(ids, vectors)), model_name=embedding.MODEL_NAME
    )
    if stored < len(ids):
        # Ordinary: a rule edited between the read and the write is skipped on
        # purpose, so its new text is not pinned to the vector of the old.
        logger.info("Stored %d of %d rule embeddings; the rest moved on", stored, len(ids))
    return stored


def run() -> None:
    """Poll for unembedded rules until asked to stop."""
    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)

    if not embedding.is_available():
        # Worth saying once and loudly: the service will keep running and keep
        # failing, and this is the line that explains why.
        logger.error(
            "sentence-transformers is not installed; no rule will become searchable in this image"
        )

    logger.info("Embedder started (model %s, batch %d)", embedding.MODEL_NAME, BATCH_SIZE)
    while not _stop:
        try:
            stored = process_one_batch()
        except embedding.EmbeddingUnavailableError as exc:
            logger.error("Embedding unavailable: %s", exc)
            time.sleep(ERROR_SLEEP_SECONDS)
            continue
        except Exception:
            logger.exception("Embedder pass failed")
            time.sleep(ERROR_SLEEP_SECONDS)
            continue

        if stored == 0:
            time.sleep(IDLE_SLEEP_SECONDS)

    logger.info("Embedder stopped")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    run()
