"""The invariants from domain-model.md section 3, enforced by the database itself.

Each test asks Postgres to store something the document forbids and expects a refusal.
These are the rules that must hold no matter which service -- or which person in psql --
does the writing.
"""

from __future__ import annotations

import json
import uuid

import psycopg
import pytest
from psycopg import errors

import factories as f

# --- the grounding rule: no location is ever guessed ------------------------------------


@pytest.mark.parametrize("confidence", ["AMBIGUOUS", "UNKNOWN"])
def test_unresolved_location_cannot_carry_a_grid_cell(
    db: psycopg.Connection, confidence: str
) -> None:
    rid = f.report(db)
    with f.refused(db, errors.CheckViolation):
        f.triage(db, rid, location_confidence=confidence, grid_cell=f.CELL_A)


@pytest.mark.parametrize("confidence", ["AMBIGUOUS", "UNKNOWN"])
def test_unresolved_location_is_stored_with_a_null_grid_cell(
    db: psycopg.Connection, confidence: str
) -> None:
    f.triage(db, f.report(db), location_confidence=confidence, grid_cell=None)


@pytest.mark.parametrize("confidence", ["EXACT", "RESOLVED"])
def test_resolved_location_may_carry_a_grid_cell(db: psycopg.Connection, confidence: str) -> None:
    f.triage(db, f.report(db), location_confidence=confidence, grid_cell=f.CELL_A)


def test_a_blank_grid_cell_is_not_a_way_around_the_null_rule(db: psycopg.Connection) -> None:
    with f.refused(db, errors.CheckViolation):
        f.triage(db, f.report(db), location_confidence="EXACT", grid_cell="  ")


def test_updating_a_result_into_an_invented_location_is_refused(db: psycopg.Connection) -> None:
    tid = f.triage(db, f.report(db), location_confidence="UNKNOWN")
    with f.refused(db, errors.CheckViolation):
        db.execute("UPDATE triage_results SET grid_cell = %s WHERE id = %s", (f.CELL_A, tid))


@pytest.mark.parametrize("confidence", ["AMBIGUOUS", "UNKNOWN"])
def test_unresolved_location_cannot_carry_coordinates_either(
    db: psycopg.Connection, confidence: str
) -> None:
    """rag.md invariant 2: a point with no cell is still an invented location."""
    with f.refused(db, errors.CheckViolation):
        f.triage(db, f.report(db), location_confidence=confidence, resolved_coords=f.JHB)


@pytest.mark.parametrize(
    "cell",
    ["Orlando West", "89BCC3CC96BFFFF", "89bcc3cc96bfff", " 89bcc3cc96bffff", "8abcc3cc96bffff"],
)
def test_a_grid_cell_must_be_an_h3_res9_cell(db: psycopg.Connection, cell: str) -> None:
    """Lower-case hex, 15 characters, resolution 9 (starts 89): cells are computed by
    gridlock_contracts.geo.cell_for, never typed, so anything else is a mistake."""
    with f.refused(db, errors.CheckViolation):
        f.triage(db, f.report(db), location_confidence="RESOLVED", grid_cell=cell)


def test_the_constraint_names_which_case_was_violated(db: psycopg.Connection) -> None:
    rid = f.report(db)
    with pytest.raises(errors.CheckViolation) as caught, db.transaction():
        f.triage(db, rid, location_confidence="AMBIGUOUS", grid_cell=f.CELL_A)
    assert caught.value.diag.constraint_name == "triage_ambiguous_has_no_grid_cell"


# --- tiers: four legal values, and never invented ------------------------------------


def test_a_tier_outside_the_four_is_refused(db: psycopg.Connection) -> None:
    with f.refused(db, errors.InvalidTextRepresentation):
        f.triage(db, f.report(db), tier="SEVERE")


def test_a_failed_triage_carries_its_failure_and_no_tier(db: psycopg.Connection) -> None:
    f.triage(db, f.report(db), tier=None, reason=None, failure_reason="model returned 'SEVERE'")


def test_a_failure_cannot_also_carry_a_tier(db: psycopg.Connection) -> None:
    with f.refused(db, errors.CheckViolation):
        f.triage(db, f.report(db), failure_reason="timeout after 3s")


def test_a_result_needs_a_tier_or_a_failure(db: psycopg.Connection) -> None:
    with f.refused(db, errors.CheckViolation):
        f.triage(db, f.report(db), tier=None, reason=None)


def test_a_tier_needs_its_reason(db: psycopg.Connection) -> None:
    with f.refused(db, errors.CheckViolation):
        f.triage(db, f.report(db), reason=None)


@pytest.mark.parametrize("column", ["model_id", "prompt_version"])
def test_every_result_records_its_model_and_prompt(db: psycopg.Connection, column: str) -> None:
    with f.refused(db, errors.NotNullViolation):
        f.triage(db, f.report(db), **{column: None})


def test_a_report_has_at_most_one_result(db: psycopg.Connection) -> None:
    rid = f.report(db)
    f.triage(db, rid)
    with f.refused(db, errors.UniqueViolation):
        f.triage(db, rid)


# --- counts -----------------------------------------------------------------------------


def test_corroboration_count_is_not_stored(db: psycopg.Connection) -> None:
    """Derived at read time, never stored (domain-model section 3): there is no column
    a service could increment and let drift."""
    row = db.execute(
        "SELECT count(*) FROM information_schema.columns WHERE column_name = 'corroboration_count'"
    ).fetchone()
    assert row == (0,)


def test_an_incident_has_at_least_one_report(db: psycopg.Connection) -> None:
    with f.refused(db, errors.CheckViolation):
        f.incident(db, report_count=0)


def test_an_incident_is_located(db: psycopg.Connection) -> None:
    with f.refused(db, errors.NotNullViolation):
        f.incident(db, grid_cell=None)


# --- the report itself --------------------------------------------------------------------


@pytest.mark.parametrize("description", ["", "   ", "\n\t", "x" * 2001])
def test_blank_or_oversize_descriptions_are_refused(
    db: psycopg.Connection, description: str
) -> None:
    with f.refused(db, errors.CheckViolation):
        f.report(db, description=description)


def test_a_description_is_stored_verbatim(db: psycopg.Connection) -> None:
    text = "  2 guys by the SPAR on vilakazi!!  one has a gun?? \n"
    rid = f.report(db, description=text)
    row = db.execute("SELECT description FROM reports WHERE id = %s", (rid,)).fetchone()
    assert row == (text,)


def test_a_new_report_starts_received_on_the_web_channel(db: psycopg.Connection) -> None:
    rid = f.report(db)
    row = db.execute(
        "SELECT state, source_channel, received_at IS NOT NULL, incident_id FROM reports"
        " WHERE id = %s",
        (rid,),
    ).fetchone()
    assert row == ("RECEIVED", "web", True, None)


def test_received_at_is_stored_with_its_timezone(db: psycopg.Connection) -> None:
    rid = f.report(db, received_at="2026-09-27 09:00:00+02:00")
    row = db.execute(
        "SELECT received_at AT TIME ZONE 'UTC' FROM reports WHERE id = %s", (rid,)
    ).fetchone()
    assert row is not None
    assert row[0].isoformat() == "2026-09-27T07:00:00"


def test_postgis_moves_off_globe_coordinates_so_callers_must_reject_them(
    db: psycopg.Connection,
) -> None:
    """A known hole, pinned so nobody assumes the database closes it.

    PostGIS does not refuse a latitude of -95: it silently "coerces" it to -85 and
    stores a real-looking point roughly 1,100 km from anything the reporter said -- an
    invented location. The coercion happens while parsing the value, before any CHECK or
    trigger can see the original, so no database rule can catch it. The guard belongs
    where coordinates enter: the contracts model and ingest-api must reject lat outside
    [-90, 90] and lon outside [-180, 180] with a 422.

    If this test starts failing, PostGIS has started refusing such input -- good news;
    turn this back into a refusal test.
    """
    rid = f.report(db, reported_coords="SRID=4326;POINT(28.0 -95.0)")
    row = db.execute(
        "SELECT ST_Y(reported_coords::geometry) FROM reports WHERE id = %s", (rid,)
    ).fetchone()
    assert row == (-85.0,)


def test_a_report_needs_no_coordinates(db: psycopg.Connection) -> None:
    f.report(db, reported_coords=None, reported_landmark="the Spar on Vilakazi")


# --- evidence ---------------------------------------------------------------------------


def _chunk(**overrides: object) -> dict[str, object]:
    chunk: dict[str, object] = {
        "landmark_id": str(uuid.uuid4()),
        "text": "Spar Vilakazi, Vilakazi St, Orlando West",
        "similarity": 0.91,
    }
    chunk.update(overrides)
    return chunk


def test_evidence_defaults_to_an_empty_list(db: psycopg.Connection) -> None:
    tid = f.triage(db, f.report(db))
    row = db.execute("SELECT evidence FROM triage_results WHERE id = %s", (tid,)).fetchone()
    assert row == ([],)


def test_evidence_keeps_every_candidate_in_order(db: psycopg.Connection) -> None:
    """AMBIGUOUS keeps every competing match, so a responder sees each candidate."""
    chunks = [_chunk(similarity=0.88), _chunk(similarity=0.87)]
    tid = f.triage(db, f.report(db), location_confidence="AMBIGUOUS", evidence=json.dumps(chunks))
    row = db.execute("SELECT evidence FROM triage_results WHERE id = %s", (tid,)).fetchone()
    assert row == (chunks,)


@pytest.mark.parametrize(
    "evidence",
    [
        {"landmark_id": str(uuid.uuid4()), "text": "x", "similarity": 0.9},  # not a list
        [_chunk(landmark_id="the Spar")],  # not a landmark id
        [_chunk(text="  ")],  # blank text
        [_chunk(similarity="high")],  # not a number
        [{k: v for k, v in _chunk().items() if k != "text"}],  # missing a field
        [_chunk(grid_cell="89bcc3cc96bffff")],  # a field EvidenceChunk does not have
        ["Spar Vilakazi"],  # not an object
    ],
)
def test_malformed_evidence_is_refused(db: psycopg.Connection, evidence: object) -> None:
    with f.refused(db, errors.CheckViolation):
        f.triage(db, f.report(db), evidence=json.dumps(evidence))


# --- landmarks ----------------------------------------------------------------------------


def test_a_landmark_keeps_its_aliases(db: psycopg.Connection) -> None:
    lid = f.landmark(db, aliases=["the Spar", "Spar on Vilakazi"])
    row = db.execute("SELECT aliases FROM landmarks WHERE id = %s", (lid,)).fetchone()
    assert row == (["the Spar", "Spar on Vilakazi"],)


def test_a_landmark_alias_cannot_be_null(db: psycopg.Connection) -> None:
    with f.refused(db, errors.CheckViolation):
        f.landmark(db, aliases=["the Spar", None])


def test_a_landmark_is_located_by_a_real_cell(db: psycopg.Connection) -> None:
    with f.refused(db, errors.CheckViolation):
        f.landmark(db, grid_cell="Orlando West")
