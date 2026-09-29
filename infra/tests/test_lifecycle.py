"""Report and incident lifecycles: the state diagrams in domain-model.md section 5.

"Transitions not drawn here must be made impossible in code, not merely unimplemented."
Every (from, to) pair is tried. The allowed set is read from the document's diagram, so
the test and the document cannot disagree.
"""

from __future__ import annotations

import contextlib
from collections import deque
from itertools import product
from uuid import UUID

import psycopg
import pytest
from psycopg import errors, sql

import domain_model
import factories as f

STATES = domain_model.python_enums()["ReportState"]
DRAWN = domain_model.report_transitions()


def _path_to(target: str) -> list[str]:
    """The states to step through from RECEIVED to reach target, along drawn edges."""
    queue: deque[list[str]] = deque([["RECEIVED"]])
    while queue:
        path = queue.popleft()
        if path[-1] == target:
            return path[1:]
        queue.extend([*path, b] for a, b in DRAWN if a == path[-1] and b not in path)
    raise AssertionError(f"{target} is unreachable from RECEIVED in the diagram")


def _record_outcome(db: psycopg.Connection, rid: UUID, state: str) -> None:
    """Write the triage outcome a state claims, as the triage engine does before moving it.

    TRIAGED needs a validated tier on record, NEEDS_REVIEW a failure. A manual re-run
    (NEEDS_REVIEW -> TRIAGED) replaces the failure with a tier.
    """
    if state == "TRIAGED":
        f.record_outcome(db, rid, tier="URGENT")
    elif state == "NEEDS_REVIEW":
        f.record_outcome(db, rid, failure_reason="model exceeded the 3s budget")


def _move(db: psycopg.Connection, rid: UUID, state: str) -> None:
    db.execute("UPDATE reports SET state = %s WHERE id = %s", (state, rid))


def _report_in(db: psycopg.Connection, state: str) -> UUID:
    rid = f.report(db)
    for step in _path_to(state):
        _record_outcome(db, rid, step)
        _move(db, rid, step)
    return rid


def test_the_diagram_only_names_real_states() -> None:
    named = {s for pair in DRAWN for s in pair}
    assert named <= set(STATES), f"diagram names states the enum lacks: {named - set(STATES)}"


@pytest.mark.parametrize(("src", "dst"), [(a, b) for a, b in product(STATES, STATES) if a != b])
def test_every_transition_is_allowed_exactly_when_drawn(
    db: psycopg.Connection, src: str, dst: str
) -> None:
    rid = _report_in(db, src)
    if (src, dst) in DRAWN:
        _record_outcome(db, rid, dst)
        _move(db, rid, dst)
        row = db.execute("SELECT state FROM reports WHERE id = %s", (rid,)).fetchone()
        assert row == (dst,)
    else:
        # Put the outcome dst claims on record where the database allows it, so the
        # refusal below is the transition itself, not a missing triage result. (Turning a
        # TRIAGED report's result into a failure is refused on its own -- tested below.)
        with contextlib.suppress(errors.CheckViolation), db.transaction():
            _record_outcome(db, rid, dst)
        # Refused by the transition rule itself, not by a neighbouring check that happens
        # to fire for the same pair.
        with pytest.raises(errors.CheckViolation, match="cannot move from"), db.transaction():
            _move(db, rid, dst)
        row = db.execute("SELECT state FROM reports WHERE id = %s", (rid,)).fetchone()
        assert row == (src,)


def test_resolved_is_final(db: psycopg.Connection) -> None:
    rid = _report_in(db, "RESOLVED")
    for dst in STATES:
        if dst != "RESOLVED":
            with f.refused(db, errors.CheckViolation):
                db.execute("UPDATE reports SET state = %s WHERE id = %s", (dst, rid))


def test_a_responder_can_act_on_a_report_the_model_could_not_rank(db: psycopg.Connection) -> None:
    rid = _report_in(db, "NEEDS_REVIEW")
    db.execute("UPDATE reports SET state = 'ACKNOWLEDGED' WHERE id = %s", (rid,))


def test_triaged_needs_a_triage_result(db: psycopg.Connection) -> None:
    rid = f.report(db)
    with f.refused(db, errors.CheckViolation):
        _move(db, rid, "TRIAGED")


def test_a_failed_triage_cannot_be_called_triaged(db: psycopg.Connection) -> None:
    """QueueItem.tier is None only when the state is NEEDS_REVIEW -- so never TRIAGED."""
    rid = f.report(db)
    f.record_outcome(db, rid, failure_reason="model returned 'SEVERE'")
    with f.refused(db, errors.CheckViolation):
        _move(db, rid, "TRIAGED")


def test_needs_review_needs_a_recorded_failure(db: psycopg.Connection) -> None:
    rid = f.report(db)
    f.record_outcome(db, rid, tier="MONITOR")
    with f.refused(db, errors.CheckViolation):
        _move(db, rid, "NEEDS_REVIEW")


@pytest.mark.parametrize("state", [s for s in STATES if s != "RECEIVED"])
def test_a_report_is_created_received(db: psycopg.Connection, state: str) -> None:
    with f.refused(db, errors.CheckViolation):
        f.report(db, state=state)


# --- the audit trail --------------------------------------------------------------------


def test_a_report_cannot_be_deleted(db: psycopg.Connection) -> None:
    rid = _report_in(db, "RESOLVED")
    with f.refused(db, errors.RestrictViolation):
        db.execute("DELETE FROM reports WHERE id = %s", (rid,))


@pytest.mark.parametrize("table", ["reports", "triage_results"])
def test_the_audit_trail_cannot_be_truncated(db: psycopg.Connection, table: str) -> None:
    f.report(db)
    with f.refused(db, errors.RestrictViolation):
        db.execute(sql.SQL("TRUNCATE {} CASCADE").format(sql.Identifier(table)))


def test_a_triage_result_cannot_be_deleted(db: psycopg.Connection) -> None:
    tid = f.triage(db, f.report(db))
    with f.refused(db, errors.RestrictViolation):
        db.execute("DELETE FROM triage_results WHERE id = %s", (tid,))


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("description", "edited"),
        ("reported_coords", "SRID=4326;POINT(28.0474 -26.2041)"),  # ~10 m east
        ("reported_coords", None),
        ("reported_landmark", "somewhere else"),
        ("category_hint", "noise"),
        ("received_at", "2020-01-01T00:00:00Z"),
        ("source_channel", "cctv"),
    ],
)
def test_what_the_reporter_sent_is_never_rewritten(
    db: psycopg.Connection, column: str, value: str | None
) -> None:
    rid = f.report(db, reported_coords=f.JHB, reported_landmark="Spar", category_hint="crime")
    with f.refused(db, errors.RestrictViolation):
        db.execute(
            sql.SQL("UPDATE reports SET {} = %s WHERE id = %s").format(sql.Identifier(column)),
            (value, rid),
        )


def test_linking_a_report_to_an_open_incident_is_allowed(db: psycopg.Connection) -> None:
    rid = _report_in(db, "TRIAGED")
    iid = f.incident(db)
    db.execute("UPDATE reports SET incident_id = %s WHERE id = %s", (iid, rid))


@pytest.mark.parametrize("end", ["MERGED", "CLOSED"])
def test_a_report_cannot_join_an_ended_incident(db: psycopg.Connection, end: str) -> None:
    """Defence in depth for when MERGED/CLOSED are designed: the link rule does not rely on
    the OPEN-only constraint. The constraint is dropped inside this test's transaction,
    which is rolled back afterwards, like everything else the test does."""
    rid = _report_in(db, "TRIAGED")
    iid = f.incident(db)
    db.execute(
        "ALTER TABLE incidents"
        " DROP CONSTRAINT incidents_open_only_until_close_and_merge_are_designed"
    )
    db.execute("UPDATE incidents SET state = %s WHERE id = %s", (end, iid))
    with f.refused(db, errors.CheckViolation):
        db.execute("UPDATE reports SET incident_id = %s WHERE id = %s", (iid, rid))


@pytest.mark.parametrize("relink", ["another", "none"])
def test_a_resolved_reports_incident_link_is_final(db: psycopg.Connection, relink: str) -> None:
    rid = _report_in(db, "TRIAGED")
    first = f.incident(db)
    db.execute("UPDATE reports SET incident_id = %s WHERE id = %s", (first, rid))
    for step in ("ACKNOWLEDGED", "RESOLVED"):
        _move(db, rid, step)
    target = f.incident(db) if relink == "another" else None
    with f.refused(db, errors.RestrictViolation):
        db.execute("UPDATE reports SET incident_id = %s WHERE id = %s", (target, rid))


# --- incidents -------------------------------------------------------------------------


def test_the_document_reserves_merged_and_closed() -> None:
    assert domain_model.incident_states() == ["OPEN", "MERGED", "CLOSED"]


@pytest.mark.parametrize("state", ["MERGED", "CLOSED"])
def test_no_code_may_write_a_reserved_incident_state(db: psycopg.Connection, state: str) -> None:
    """domain-model sections 5 and 10: no action reaches MERGED or CLOSED yet, "so no code
    may write them" -- neither on insert nor by update."""
    with f.refused(db, errors.CheckViolation):
        f.incident(db, state=state)
    iid = f.incident(db)
    with f.refused(db, errors.CheckViolation):
        db.execute("UPDATE incidents SET state = %s WHERE id = %s", (state, iid))


def test_an_incident_is_opened_open(db: psycopg.Connection) -> None:
    iid = f.incident(db)
    row = db.execute("SELECT state FROM incidents WHERE id = %s", (iid,)).fetchone()
    assert row == ("OPEN",)


@pytest.mark.parametrize("column", ["opened_at", "last_report_at"])
def test_incident_times_are_report_times_never_defaulted(
    db: psycopg.Connection, column: str
) -> None:
    """verification.md invariant 4: report time, not processing time. With no now()
    default, a verifier that forgets to pass the report's received_at fails loudly
    instead of quietly shifting the corroboration window."""
    with f.refused(db, errors.NotNullViolation):
        f.incident(db, **{column: None})


def test_an_incident_opened_by_a_late_triaged_report_is_accepted(db: psycopg.Connection) -> None:
    """A report received an hour ago and triaged only now opens its incident at its own
    received_at -- the database must not second-guess that against the clock."""
    f.incident(db, opened_at="2020-01-01T08:00:00Z", last_report_at="2020-01-01T08:00:00Z")


# --- triage results keep their shape under their report's state ----------------------


def test_a_triaged_reports_result_cannot_lose_its_tier(db: psycopg.Connection) -> None:
    rid = _report_in(db, "TRIAGED")
    with f.refused(db, errors.CheckViolation):
        db.execute(
            "UPDATE triage_results SET tier = NULL, reason = NULL, failure_reason = 'oops'"
            " WHERE report_id = %s",
            (rid,),
        )


def test_a_rerun_can_turn_a_failure_into_a_success(db: psycopg.Connection) -> None:
    """NEEDS_REVIEW -> TRIAGED: the result is replaced first, then the report moves."""
    rid = _report_in(db, "NEEDS_REVIEW")
    f.record_outcome(db, rid, tier="URGENT")
    _move(db, rid, "TRIAGED")


def test_triage_can_finish_after_a_responder_acknowledged(db: psycopg.Connection) -> None:
    """domain-model section 5: the result is still written; the state stays ACKNOWLEDGED."""
    rid = f.report(db)
    _move(db, rid, "ACKNOWLEDGED")
    f.record_outcome(db, rid, tier="MONITOR")
    row = db.execute("SELECT state FROM reports WHERE id = %s", (rid,)).fetchone()
    assert row == ("ACKNOWLEDGED",)
