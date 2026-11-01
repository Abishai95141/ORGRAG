FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    FASTEMBED_CACHE_PATH=/opt/fastembed \
    DOCLING_ARTIFACTS_PATH=/opt/docling-models \
    PATH="/app/.venv/bin:$PATH"

# LibreOffice turns slides into pages and legacy Office files (.doc, .xls, .ppt) into
# PDFs for Docling; no GUI.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libreoffice-writer-nogui libreoffice-calc-nogui libreoffice-impress-nogui \
        fonts-dejavu-core libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv
WORKDIR /app

# Docling (the `parsing` extra) reads PDFs with OCR and tables, Office files, email and
# images. Its torch comes from PyTorch's CPU index (pyproject.toml).
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --extra local-embeddings --extra parsing --no-install-project

# Bake the models in so the first boot and the first upload need no download: the
# default embedding model, and Docling's layout, table and OCR models.
ARG EMBEDDING_MODEL=BAAI/bge-small-en-v1.5
RUN python -c "from fastembed import TextEmbedding; TextEmbedding(model_name='${EMBEDDING_MODEL}')"
RUN docling-tools models download layout tableformer rapidocr -o "$DOCLING_ARTIFACTS_PATH"

COPY . .

# The app runs as `strata` and writes only uploaded files; the code and the virtualenv
# stay root-owned (a recursive chown would copy the whole venv into another layer).
RUN useradd --create-home strata && mkdir -p /data/files && chown -R strata /data
USER strata

ENV FILE_STORAGE_DIR=/data/files
EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers"]
