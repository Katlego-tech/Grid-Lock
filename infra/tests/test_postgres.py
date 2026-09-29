"""The database compose brings up: PostgreSQL with PostGIS and pgvector enabled.

T012's contract. Each extension is exercised, not just listed: an extension that is
installed but broken fails here, not later inside a service.
"""

from __future__ import annotations

import psycopg
import pytest


@pytest.mark.parametrize("extension", ["postgis", "vector"])
def test_init_sql_enables_the_extension(db: psycopg.Connection, extension: str) -> None:
    row = db.execute("SELECT 1 FROM pg_extension WHERE extname = %s", (extension,)).fetchone()
    assert row == (1,), f"{extension} is not enabled -- see infra/postgres/init.sql"


def test_postgis_measures_real_distances(db: psycopg.Connection) -> None:
    """Johannesburg CBD to Soweto (Vilakazi St) is about 14 km on the ground."""
    row = db.execute(
        "SELECT ST_Distance("
        " 'SRID=4326;POINT(28.0473 -26.2041)'::geography,"
        " 'SRID=4326;POINT(27.9068 -26.2361)'::geography)"
    ).fetchone()
    assert row is not None
    assert 13_000 < row[0] < 16_000


def test_pgvector_stores_and_compares_rag_sized_embeddings(db: psycopg.Connection) -> None:
    """rag.md: all-MiniLM-L6-v2, 384 dimensions, cosine distance."""
    db.execute("CREATE TEMP TABLE probe (embedding vector(384))")
    same = [1.0] * 384
    opposite = [-1.0] * 384
    db.execute("INSERT INTO probe VALUES (%s::vector), (%s::vector)", (str(same), str(opposite)))
    rows = db.execute(
        "SELECT embedding <=> %s::vector FROM probe ORDER BY 1", (str(same),)
    ).fetchall()
    assert [round(r[0], 6) for r in rows] == [0.0, 2.0]


def test_a_vector_of_the_wrong_size_is_refused(db: psycopg.Connection) -> None:
    db.execute("CREATE TEMP TABLE probe (embedding vector(384))")
    with pytest.raises(psycopg.errors.DataException), db.transaction():
        db.execute("INSERT INTO probe VALUES (%s::vector)", (str([0.5] * 383),))
