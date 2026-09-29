# PostgreSQL 17 with PostGIS (the base image) and pgvector (rag-index stores its landmark
# embeddings in it -- rag.md). No published image carries both, so this adds pgvector from
# the PostgreSQL apt repository the base image already trusts. Pinned to the same release
# line as the base; bump the two together.
FROM postgis/postgis:17-3.5

RUN apt-get update \
 && apt-get install -y --no-install-recommends postgresql-17-pgvector \
 && rm -rf /var/lib/apt/lists/*
