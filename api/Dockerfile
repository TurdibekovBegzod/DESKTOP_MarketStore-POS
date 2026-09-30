FROM docker.io/library/python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY ai ./ai
COPY alembic ./alembic
COPY alembic.ini .

CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]


# The embedder's image: the same code, plus torch and the sentence-transformers
# model. A separate stage so the api and worker images stay small - they never
# load a model, and torch would add a few hundred megabytes to every deploy of
# code that does not use it.
FROM base AS embedder

COPY requirements-embedder.txt .
RUN pip install --no-cache-dir -r requirements-embedder.txt

# Baked into the image rather than downloaded on first use. A cold start would
# otherwise reach out to huggingface.co, which makes the container's readiness
# depend on someone else's uptime and leaves rules unsearchable until it answers.
ENV HF_HOME=/opt/huggingface
ENV SENTENCE_TRANSFORMERS_HOME=/opt/huggingface
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('intfloat/multilingual-e5-small', device='cpu')"

# Torch otherwise spreads across every core it can see. This box has memory to
# spare but not cores, and an indexing run that takes them all starves Postgres
# and the DM worker.
ENV EMBEDDING_THREADS=2
ENV OMP_NUM_THREADS=2
ENV MKL_NUM_THREADS=2

CMD ["python", "-m", "app.embedder"]
