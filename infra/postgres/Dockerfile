# PostgreSQL 17 with pgvector (the base image; rag-index stores its landmark embeddings in
# it -- rag.md) and PostGIS (coordinates and landmark points -- domain-model.md section 3).
# No published image carries both, so this adds PostGIS from the PostgreSQL apt repository
# (PGDG) the base image already trusts.
#
# Everything is pinned exactly: the base by digest (pgvector 0.8.7, PostgreSQL 17, Debian
# trixie) and PostGIS by package version. Bump them together, deliberately.
#
# Why the archive line: PGDG's main repository carries only current package versions, and
# drops a Debian release once it is out of support. Both broke this build before -- it was
# FROM postgis/postgis:17-3.5 (bullseye) with an unpinned apt install, and when PGDG stopped
# serving bullseye a fresh build could no longer install pgvector at all. The archive keeps
# every version ever published, so the exact pin below stays installable after the main
# repository moves on. Same signing key as the main repository.
FROM pgvector/pgvector:0.8.7-pg17-trixie@sha256:7a7e9f22015b67edb4bef5c59daeebcd7e74bfa570df6ce60ae01237c8648a84

ARG POSTGIS_VERSION=3.6.4+dfsg-2.pgdg13+1

RUN echo "deb [ signed-by=/usr/local/share/keyrings/postgres.gpg.asc ] http://apt-archive.postgresql.org/pub/repos/apt trixie-pgdg-archive main" \
      > /etc/apt/sources.list.d/pgdg-archive.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends \
      "postgresql-17-postgis-3=${POSTGIS_VERSION}" \
      "postgresql-17-postgis-3-scripts=${POSTGIS_VERSION}" \
 && rm -rf /var/lib/apt/lists/*
