"""Row builders for the database tests. Each inserts a valid row; tests override one thing."""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from typing import Any, LiteralString
from uuid import UUID

import psycopg
import pytest
from psycopg import sql

# Johannesburg CBD, as a WGS84 point literal.
JHB = "SRID=4326;POINT(28.0473 -26.2041)"

# Real H3 resolution-9 cells, from verification.md section 4.2: Vilakazi Street, Orlando
# West, and one of its neighbours.
CELL_A = "89bcc3cc96bffff"
CELL_B = "89bcc3cc963ffff"


def _insert(db: psycopg.Connection, table: LiteralString, values: dict[str, Any]) -> UUID | None:
    query = sql.SQL("INSERT INTO {} ({}) VALUES ({}) RETURNING *").format(
        sql.Identifier(table),
        sql.SQL(", ").join(sql.Identifier(k) for k in values),
        sql.SQL(", ").join(sql.Placeholder() for _ in values),
    )
    row = db.execute(query, list(values.values())).fetchone()
    assert row is not None
    first = row[0]
    return first if isinstance(first, UUID) else None


def report(db: psycopg.Connection, **overrides: Any) -> UUID:
    values: dict[str, Any] = {"description": "Two men climbing the wall at number 14"}
    values.update(overrides)
    rid = _insert(db, "reports", values)
    assert rid is not None
    return rid


def landmark(db: psycopg.Connection, **overrides: Any) -> UUID:
    values: dict[str, Any] = {
        "name": "Spar Vilakazi",
        "aliases": ["the Spar on Vilakazi"],
        "category": "shop",
        "address": "Vilakazi St",
        "suburb": "Orlando West",
        "coords": JHB,
        "grid_cell": CELL_A,
        "source": "seed",
    }
    values.update(overrides)
    lid = _insert(db, "landmarks", values)
    assert lid is not None
    return lid


def triage(db: psycopg.Connection, report_id: UUID, **overrides: Any) -> UUID:
    values: dict[str, Any] = {
        "report_id": report_id,
        "tier": "URGENT",
        "reason": "Reporter describes an intrusion in progress.",
        "location_confidence": "UNKNOWN",
        "model_id": "test-model",
        "prompt_version": "triage-v1",
    }
    values.update(overrides)
    tid = _insert(db, "triage_results", values)
    assert tid is not None
    return tid


def record_outcome(
    db: psycopg.Connection,
    report_id: UUID,
    *,
    tier: str | None = None,
    failure_reason: str | None = None,
) -> None:
    """Insert or replace a report's triage outcome: a tier (with reason) or a failure."""
    db.execute(
        """
        INSERT INTO triage_results
               (report_id, tier, reason, failure_reason, location_confidence, model_id,
                prompt_version)
        VALUES (%(rid)s, %(tier)s, %(reason)s, %(failure)s, 'UNKNOWN', 'test-model', 'triage-v1')
        ON CONFLICT (report_id) DO UPDATE
           SET tier = EXCLUDED.tier, reason = EXCLUDED.reason,
               failure_reason = EXCLUDED.failure_reason
        """,
        {
            "rid": report_id,
            "tier": tier,
            "reason": "Reporter describes an intrusion." if tier else None,
            "failure": failure_reason,
        },
    )


def incident(db: psycopg.Connection, **overrides: Any) -> UUID:
    values: dict[str, Any] = {
        "grid_cell": CELL_A,
        "peak_tier": "URGENT",
        "report_count": 1,
        "opened_at": "2026-09-29T12:00:00Z",
        "last_report_at": "2026-09-29T12:00:00Z",
    }
    values.update(overrides)
    iid = _insert(db, "incidents", values)
    assert iid is not None
    return iid


@contextlib.contextmanager
def refused(db: psycopg.Connection, error: type[psycopg.Error]) -> Iterator[None]:
    """Assert the database refuses what happens inside, then carry on with the test.

    The statement runs in a savepoint, so the refusal rolls back only itself and the
    test's other rows stay usable.
    """
    with pytest.raises(error), db.transaction():
        yield
