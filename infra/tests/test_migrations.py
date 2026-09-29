"""infra/postgres/migrate.sh: the one way a schema change reaches a database.

It runs here exactly as compose's db-migrate job runs it: inside the Postgres container
when the suite starts its own, or with the local psql against an external server.
"""

from __future__ import annotations

import psycopg

from conftest import MIGRATIONS, PostgresServer, dbname_of, fresh_database

ALL = sorted(p.stem for p in MIGRATIONS.glob("*.sql"))


def _applied(dsn: str) -> list[str]:
    with psycopg.connect(dsn) as conn:
        return [r[0] for r in conn.execute("SELECT version FROM schema_migrations ORDER BY 1")]


def test_every_migration_is_applied_and_recorded(postgres_server: PostgresServer) -> None:
    with fresh_database(postgres_server) as dsn:
        run = postgres_server.migrate(dbname_of(dsn))
        assert run.code == 0, run.output
        assert _applied(dsn) == ALL


def test_running_it_again_changes_nothing(postgres_server: PostgresServer) -> None:
    """docker compose runs db-migrate on every `up`."""
    with fresh_database(postgres_server) as dsn:
        assert postgres_server.migrate(dbname_of(dsn)).code == 0
        second = postgres_server.migrate(dbname_of(dsn))
        assert second.code == 0, second.output
        assert "already applied" in second.output
        assert "+ " not in second.output
        assert _applied(dsn) == ALL


def test_a_migration_that_fails_halfway_leaves_no_trace(postgres_server: PostgresServer) -> None:
    """One transaction per file: a half-applied schema change is worse than none."""
    broken = {"9999_fails_halfway.sql": "CREATE TABLE half_done (x int);\nSELECT 1 / 0;\n"}
    with fresh_database(postgres_server) as dsn:
        run = postgres_server.migrate(dbname_of(dsn), broken)
        assert run.code != 0, "a failing migration must fail the job"
        assert "division by zero" in run.output
        assert _applied(dsn) == ALL, "the failed migration must not be recorded"
        with psycopg.connect(dsn) as conn:
            row = conn.execute("SELECT to_regclass('public.half_done')").fetchone()
        assert row == (None,), "the failed migration's first statement must be rolled back"
