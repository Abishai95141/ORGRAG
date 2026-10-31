# Postgres 17 with pgvector (vectors) and pg_textsearch (BM25 keyword ranking).
# pg_textsearch must be preloaded; without it Strata falls back to ts_rank_cd.
FROM pgvector/pgvector:pg17

RUN apt-get update \
    && apt-get install -y --no-install-recommends "postgresql-17-pg-textsearch=1.5.1-*" \
    && rm -rf /var/lib/apt/lists/*

CMD ["postgres", "-c", "shared_preload_libraries=pg_textsearch"]
