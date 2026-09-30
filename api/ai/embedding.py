"""Local sentence embeddings, on the CPU, from multilingual-e5-small.

Local rather than an embedding API: the vectors are only ever compared with each
other, so there is nothing to gain from a hosted model and a great deal to lose -
a shop's rules would stop being searchable whenever someone else's quota ran out.

The model is loaded once per process and held. It is ~470MB of weights and a
couple of seconds of startup, so loading it per call would dominate every
request; holding it costs memory for as long as the process lives, which is why
this belongs in the embedder service rather than in the web worker that has to
answer DMs quickly.

e5 was trained with prefixes and it is not optional: a stored rule is embedded as
``passage: ...`` and a customer's question as ``query: ...``. Getting this wrong
raises nothing at all - it just quietly retrieves worse rules - so the prefixes
are applied in here, where a caller cannot forget them.
"""

import hashlib
import logging
import os
import threading


logger = logging.getLogger(__name__)

MODEL_NAME = "intfloat/multilingual-e5-small"

# What the model produces. Written down because the database column is declared
# vector(384) and a mismatch has to be caught here, in Python, rather than as an
# opaque insert error one layer down.
DIMENSIONS = 384

# e5's two prefixes. Documents and queries live in the same space only when each
# side is marked for what it is.
PASSAGE_PREFIX = "passage: "
QUERY_PREFIX = "query: "

# The model's own limit is 512 tokens. Characters are not tokens, but a rule far
# longer than this is being truncated by the tokenizer anyway, and cutting here
# keeps one pathological paste from stalling a whole batch.
MAX_CHARS = 2000

_model = None
# Two Celery workers in one process would otherwise each start a load, and for a
# few seconds the container would hold two copies of the weights.
_model_lock = threading.Lock()


class EmbeddingUnavailableError(RuntimeError):
    """The model could not be loaded or run.

    Raised rather than returning an empty vector: a zero vector is a *point* in
    the space and would silently rank as some fixed distance from everything,
    which looks like a working search returning nonsense. Callers treat this as
    "try again later" and leave the row's vector NULL.
    """


def is_available() -> bool:
    """Whether embedding can run at all in this process.

    Lets a caller skip work up front instead of discovering a missing dependency
    once per row. Does not load the model.
    """
    try:
        import sentence_transformers  # noqa: F401
    except Exception:
        return False
    return True


def _load():
    """The loaded model, loading it on first use.

    Thread count is pinned before torch is imported. Left alone, torch spreads
    across every core it can see, and on this server that means an indexing run
    starves Postgres and the DM worker of CPU on a box that has plenty of memory
    but no spare cores.
    """
    global _model
    if _model is not None:
        return _model

    with _model_lock:
        if _model is not None:
            return _model
        threads = os.environ.get("EMBEDDING_THREADS", "2")
        os.environ.setdefault("OMP_NUM_THREADS", threads)
        os.environ.setdefault("MKL_NUM_THREADS", threads)
        try:
            import torch
            from sentence_transformers import SentenceTransformer

            torch.set_num_threads(int(threads))
            logger.info("Loading embedding model %s on CPU (%s threads)", MODEL_NAME, threads)
            # device is explicit: a CPU-only image has no CUDA, and letting
            # sentence-transformers guess makes the failure look like a model
            # problem rather than a missing GPU.
            _model = SentenceTransformer(MODEL_NAME, device="cpu")
        except Exception as exc:
            raise EmbeddingUnavailableError(f"could not load {MODEL_NAME}: {exc}") from exc
    return _model


def text_hash(text: str) -> str:
    """Fingerprint of the text a vector was built from.

    The embedder compares these to decide what to re-embed, so it hashes exactly
    what gets embedded - prefix and truncation included. Hashing the raw text
    instead would miss a rule whose stored form changed only after truncation.
    """
    return hashlib.sha256(_prepare(text, PASSAGE_PREFIX).encode("utf-8")).hexdigest()


def _prepare(text: str, prefix: str) -> str:
    return prefix + (text or "").strip()[:MAX_CHARS]


def embed_passages(texts: list[str]) -> list[list[float]]:
    """Vectors for text being stored, in the order given.

    Batched in one call: the model is far more efficient per row this way, which
    is what makes a first indexing run over a large catalogue finish in seconds
    rather than minutes.
    """
    if not texts:
        return []
    model = _load()
    prepared = [_prepare(text, PASSAGE_PREFIX) for text in texts]
    try:
        # Normalised, so cosine distance is what the database's <=> computes and
        # the two sides cannot drift apart.
        vectors = model.encode(prepared, normalize_embeddings=True, show_progress_bar=False)
    except Exception as exc:
        raise EmbeddingUnavailableError(f"encode failed: {exc}") from exc
    return [[float(value) for value in vector] for vector in vectors]


def embed_query(text: str) -> list[float]:
    """Vector for something being searched for.

    Separate from embed_passages only to apply the other prefix - the whole
    reason e5 can tell a question from the thing that answers it.
    """
    message = (text or "").strip()
    if not message:
        raise EmbeddingUnavailableError("nothing to embed")
    model = _load()
    try:
        vector = model.encode(
            _prepare(message, QUERY_PREFIX), normalize_embeddings=True, show_progress_bar=False
        )
    except Exception as exc:
        raise EmbeddingUnavailableError(f"encode failed: {exc}") from exc
    return [float(value) for value in vector]
