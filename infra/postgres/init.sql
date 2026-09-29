-- init.sql -- extensions every GridLock database needs, before any migration runs.
--
-- PostGIS: coordinates and landmark points (domain-model.md section 3).
-- pgvector: rag-index's landmark embeddings (rag.md).
--
-- Tables are not created here: they belong to infra/postgres/migrations (T013).

CREATE EXTENSION IF NOT EXISTS postgis;
CREATE EXTENSION IF NOT EXISTS vector;
