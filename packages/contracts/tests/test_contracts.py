"""gridlock_contracts against docs/design/domain-model.md section 6 (and rag.md section 7).

Every payload round-trips through JSON unchanged, an unknown or misspelt field is refused
rather than dropped, and the location rules that keep an invented place out of the system
hold in the models themselves -- not only in the database behind them.
"""

from __future__ import annotations

import json
import math
import re
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from gridlock_contracts import geo, messages
from gridlock_contracts.enums import LocationConfidence, ReportState, Tier
from gridlock_contracts.models import Coordinates, EvidenceChunk, QueueItem, RetrievalResult

# The issue's reference point: Vilakazi Street, Orlando West.
LAT, LON = -26.2361, 27.9068
CELL = "89bcc3cc96bffff"
RECEIVED_AT = datetime(2026, 9, 29, 12, 0, tzinfo=UTC)
TRIAGED_AT = datetime(2026, 9, 29, 12, 0, 3, tzinfo=UTC)


def _chunk(**overrides: Any) -> dict[str, Any]:
    chunk: dict[str, Any] = {
        "landmark_id": str(uuid.uuid4()),
        "text": "Spar Vilakazi (Vilakazi Spar) — supermarket, Vilakazi Street, Orlando West",
        "similarity": 0.81,
    }
    chunk.update(overrides)
    return chunk


def _queue_item(**overrides: Any) -> dict[str, Any]:
    item: dict[str, Any] = {
        "report_id": str(uuid.uuid4()),
        "incident_id": None,
        "tier": "URGENT",
        "reason": "Reporter describes a break-in in progress at the Spar on Vilakazi.",
        "corroboration_count": 1,
        "grid_cell": CELL,
        "location_confidence": "RESOLVED",
        "state": "TRIAGED",
        "received_at": "2026-09-29T12:00:00Z",
        "description": "break-in at the Spar on Vilakazi",
        "failure_reason": None,
    }
    item.update(overrides)
    return item


# Each payload in section 6, as a JSON-shaped dict that is valid.
VALID: dict[type[BaseModel], dict[str, Any]] = {
    Coordinates: {"lat": LAT, "lon": LON},
    EvidenceChunk: _chunk(),
    RetrievalResult: {
        "status": "RESOLVED",
        "grid_cell": CELL,
        "resolved_coords": {"lat": LAT, "lon": LON},
        "evidence": [_chunk()],
    },
    QueueItem: _queue_item(),
    messages.ReportReceived: {
        "report_id": str(uuid.uuid4()),
        "description": "break-in at the Spar on Vilakazi",
        "reported_coords": {"lat": LAT, "lon": LON},
        "reported_landmark": None,
        "category_hint": None,
        "received_at": "2026-09-29T12:00:00.000000Z",
    },
    messages.ReportTriaged: {
        "report_id": str(uuid.uuid4()),
        "tier": "URGENT",
        "grid_cell": CELL,
        "resolved_coords": {"lat": LAT, "lon": LON},
        "location_confidence": "RESOLVED",
        "triaged_at": "2026-09-29T12:00:03.000000Z",
    },
    messages.ReportNeedsReview: {
        "report_id": str(uuid.uuid4()),
        "failure_reason": "invalid model output: HIGH",
        "triaged_at": "2026-09-29T12:00:03.000000Z",
    },
    messages.IncidentUpdated: {
        "incident_id": str(uuid.uuid4()),
        "grid_cell": CELL,
        "report_count": 3,
        "peak_tier": "URGENT",
    },
}
MODELS = list(VALID)


# --- enums --------------------------------------------------------------------------------


def test_the_enums_are_exactly_section_6_in_order() -> None:
    assert [t.value for t in Tier] == ["MONITOR", "ADVISORY", "URGENT", "CRITICAL_DISPATCH"]
    assert [s.value for s in ReportState] == [
        "RECEIVED",
        "TRIAGED",
        "NEEDS_REVIEW",
        "ACKNOWLEDGED",
        "RESOLVED",
    ]
    assert [c.value for c in LocationConfidence] == ["EXACT", "RESOLVED", "AMBIGUOUS", "UNKNOWN"]


@pytest.mark.parametrize("raw", ["HIGH", "URGENT!", "urgent", "", "CRITICAL"])
def test_a_tier_outside_the_four_is_refused_never_coerced(raw: str) -> None:
    with pytest.raises(ValueError):
        Tier(raw)
    with pytest.raises(ValidationError):
        QueueItem.model_validate(_queue_item(tier=raw))


# --- every payload ------------------------------------------------------------------------


@pytest.mark.parametrize("model", MODELS, ids=lambda m: m.__name__)
def test_each_payload_round_trips_through_json_unchanged(model: type[BaseModel]) -> None:
    parsed = model.model_validate(VALID[model])
    again = model.model_validate_json(parsed.model_dump_json())
    assert again == parsed
    assert json.loads(again.model_dump_json()) == json.loads(parsed.model_dump_json())


@pytest.mark.parametrize("model", MODELS, ids=lambda m: m.__name__)
def test_an_unknown_field_is_refused_not_dropped(model: type[BaseModel]) -> None:
    with pytest.raises(ValidationError, match=r"extra_forbidden|Extra inputs"):
        model.model_validate({**VALID[model], "priority": "high"})


@pytest.mark.parametrize("model", MODELS, ids=lambda m: m.__name__)
def test_a_misspelt_field_is_refused_not_dropped(model: type[BaseModel]) -> None:
    valid = VALID[model]
    first = next(iter(valid))
    typo = {(f"{k}s" if k == first else k): v for k, v in valid.items()}
    with pytest.raises(ValidationError):
        model.model_validate(typo)


# --- coordinates and cells ----------------------------------------------------------------


def test_cell_for_is_h3_resolution_9_as_lowercase_hex() -> None:
    assert geo.cell_for(LAT, LON) == CELL


@pytest.mark.parametrize(
    ("lat", "lon"),
    [(91.0, 0.0), (-95.0, 28.0), (0.0, 181.0), (0.0, -180.5), (math.nan, 0.0), (0.0, math.inf)],
)
def test_cell_for_refuses_a_point_that_is_not_on_the_globe(lat: float, lon: float) -> None:
    """h3 itself returns a real-looking cell for lat 91 or -95 -- an invented location."""
    with pytest.raises(ValueError):
        geo.cell_for(lat, lon)


@pytest.mark.parametrize(
    "coords",
    [
        {"lat": 91, "lon": 0},
        {"lat": -90.0001, "lon": 0},
        {"lat": 0, "lon": 180.5},
        {"lat": "NaN", "lon": 0},
        {"lat": 0, "lon": "Infinity"},
        {"lat": LAT},
    ],
)
def test_coordinates_off_the_globe_are_refused(coords: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        Coordinates.model_validate(coords)


def test_coordinates_on_the_edges_of_the_globe_are_accepted() -> None:
    Coordinates(lat=90, lon=180)
    Coordinates(lat=-90, lon=-180)


@pytest.mark.parametrize(
    "cell",
    [
        "89BCC3CC96BFFFF",  # h3 accepts upper case; the column it is grouped by must not
        "8abcc3cc96bffff",  # a real cell, wrong resolution
        "89bcc3cc96bfff",  # truncated
        " 89bcc3cc96bffff",
        "Orlando West",
        "",
    ],
)
def test_a_grid_cell_must_be_a_lowercase_h3_res9_cell(cell: str) -> None:
    assert not geo.is_cell(cell)
    with pytest.raises(ValidationError):
        messages.IncidentUpdated.model_validate(
            {**VALID[messages.IncidentUpdated], "grid_cell": cell}
        )


# --- evidence -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "chunk",
    [
        _chunk(text=""),
        _chunk(text="   "),
        _chunk(landmark_id="spar-vilakazi"),
        _chunk(similarity="NaN"),
    ],
)
def test_malformed_evidence_is_refused(chunk: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        EvidenceChunk.model_validate(chunk)


# --- RetrievalResult: rag.md invariants 1-3 -----------------------------------------------


def test_retrieval_never_reports_exact() -> None:
    """rag-index never sees GPS; EXACT is decided by triage-engine."""
    with pytest.raises(ValidationError, match="EXACT"):
        RetrievalResult.model_validate({**VALID[RetrievalResult], "status": "EXACT"})


@pytest.mark.parametrize("status", ["AMBIGUOUS", "UNKNOWN"])
@pytest.mark.parametrize("field", ["grid_cell", "resolved_coords"])
def test_an_unresolved_retrieval_carries_no_location(status: str, field: str) -> None:
    payload: dict[str, Any] = {
        "status": status,
        "grid_cell": None,
        "resolved_coords": None,
        "evidence": [_chunk(), _chunk()] if status == "AMBIGUOUS" else [],
    }
    RetrievalResult.model_validate(payload)
    located = {**payload, field: VALID[RetrievalResult][field]}
    with pytest.raises(ValidationError, match=field):
        RetrievalResult.model_validate(located)


@pytest.mark.parametrize("field", ["grid_cell", "resolved_coords"])
def test_a_resolved_retrieval_carries_its_location(field: str) -> None:
    with pytest.raises(ValidationError, match=field):
        RetrievalResult.model_validate({**VALID[RetrievalResult], field: None})


def test_a_resolved_retrieval_shows_the_match_that_placed_it() -> None:
    with pytest.raises(ValidationError, match="evidence"):
        RetrievalResult.model_validate({**VALID[RetrievalResult], "evidence": []})


def test_an_unknown_retrieval_has_no_evidence() -> None:
    with pytest.raises(ValidationError, match="evidence"):
        RetrievalResult.model_validate(
            {
                "status": "UNKNOWN",
                "grid_cell": None,
                "resolved_coords": None,
                "evidence": [_chunk()],
            }
        )


def test_an_ambiguous_retrieval_keeps_every_candidate_in_order() -> None:
    first, second = _chunk(similarity=0.78), _chunk(similarity=0.76)
    result = RetrievalResult.model_validate(
        {
            "status": "AMBIGUOUS",
            "grid_cell": None,
            "resolved_coords": None,
            "evidence": [first, second],
        }
    )
    assert [str(c.landmark_id) for c in result.evidence] == [
        first["landmark_id"],
        second["landmark_id"],
    ]


# --- QueueItem ----------------------------------------------------------------------------


def test_queue_item_has_exactly_the_section_6_fields() -> None:
    assert list(QueueItem.model_fields) == [
        "report_id",
        "incident_id",
        "tier",
        "reason",
        "corroboration_count",
        "grid_cell",
        "location_confidence",
        "state",
        "received_at",
        "description",
        "failure_reason",
    ]


@pytest.mark.parametrize("count", [0, -1])
def test_corroboration_count_is_at_least_one(count: int) -> None:
    with pytest.raises(ValidationError, match="corroboration_count"):
        QueueItem.model_validate(_queue_item(corroboration_count=count))


@pytest.mark.parametrize("confidence", ["AMBIGUOUS", "UNKNOWN"])
def test_a_queue_item_with_an_unresolved_location_carries_no_cell(confidence: str) -> None:
    with pytest.raises(ValidationError, match="grid_cell"):
        QueueItem.model_validate(_queue_item(location_confidence=confidence))


def test_a_stale_received_report_is_a_valid_queue_item() -> None:
    item = QueueItem.model_validate(
        _queue_item(
            tier=None,
            reason=None,
            grid_cell=None,
            location_confidence="UNKNOWN",
            state="RECEIVED",
            failure_reason="not yet triaged",
        )
    )
    assert item.tier is None and item.state is ReportState.RECEIVED


def test_the_description_is_kept_verbatim() -> None:
    raw = "  Two men  jumped the wall at no. 12\n— white Polo, no plates  "
    assert QueueItem.model_validate(_queue_item(description=raw)).description == raw


def test_a_timestamp_without_a_timezone_is_refused() -> None:
    with pytest.raises(ValidationError, match="received_at"):
        QueueItem.model_validate(_queue_item(received_at="2026-09-29T12:00:00"))


# --- messages: routing and the report.triaged location rule --------------------------------


def test_each_routing_key_names_its_payload() -> None:
    assert messages.EXCHANGE == "gridlock"
    assert {key: model.__name__ for key, model in messages.PAYLOADS.items()} == {
        "report.received": "ReportReceived",
        "report.triaged": "ReportTriaged",
        "report.needs_review": "ReportNeedsReview",
        "incident.updated": "IncidentUpdated",
    }


@pytest.mark.parametrize(
    "model",
    [m for m in MODELS if m in messages.PAYLOADS.values()],
    ids=lambda m: m.__name__,
)
def test_encode_then_decode_returns_the_same_message(model: type[messages.Message]) -> None:
    message = model.model_validate(VALID[model])
    routing_key, body = messages.encode(message)
    assert routing_key == model.routing_key
    assert messages.decode(routing_key, body) == message


def test_the_wire_body_is_the_payload_itself_with_no_wrapper() -> None:
    message = messages.ReportNeedsReview.model_validate(VALID[messages.ReportNeedsReview])
    _key, body = messages.encode(message)
    assert set(json.loads(body)) == {"report_id", "failure_reason", "triaged_at"}


def test_decode_refuses_an_unknown_routing_key() -> None:
    with pytest.raises(KeyError, match=re.escape("report.recieved")):
        messages.decode("report.recieved", b"{}")


def test_decode_refuses_a_body_sent_under_the_wrong_key() -> None:
    _key, body = messages.encode(
        messages.ReportNeedsReview.model_validate(VALID[messages.ReportNeedsReview])
    )
    with pytest.raises(ValidationError):
        messages.decode("report.triaged", body)


@pytest.mark.parametrize("confidence", ["AMBIGUOUS", "UNKNOWN"])
@pytest.mark.parametrize("field", ["grid_cell", "resolved_coords"])
def test_a_triaged_message_never_invents_a_location(confidence: str, field: str) -> None:
    payload = {
        **VALID[messages.ReportTriaged],
        "location_confidence": confidence,
        "grid_cell": None,
        "resolved_coords": None,
    }
    messages.ReportTriaged.model_validate(payload)
    with pytest.raises(ValidationError, match=field):
        messages.ReportTriaged.model_validate(
            {**payload, field: VALID[messages.ReportTriaged][field]}
        )


@pytest.mark.parametrize("confidence", ["EXACT", "RESOLVED"])
@pytest.mark.parametrize("field", ["grid_cell", "resolved_coords"])
def test_a_located_triaged_message_carries_its_location(confidence: str, field: str) -> None:
    payload = {**VALID[messages.ReportTriaged], "location_confidence": confidence, field: None}
    with pytest.raises(ValidationError, match=field):
        messages.ReportTriaged.model_validate(payload)


def test_a_received_message_without_coordinates_is_valid() -> None:
    message = messages.ReportReceived.model_validate(
        {**VALID[messages.ReportReceived], "reported_coords": None}
    )
    assert message.reported_coords is None
    assert json.loads(message.model_dump_json())["reported_coords"] is None


@pytest.mark.parametrize("description", ["", "   ", "x" * 2001])
def test_a_received_message_carries_a_real_description(description: str) -> None:
    with pytest.raises(ValidationError, match="description"):
        messages.ReportReceived.model_validate(
            {**VALID[messages.ReportReceived], "description": description}
        )


def test_timestamps_survive_the_wire_as_utc() -> None:
    message = messages.ReportTriaged.model_validate(VALID[messages.ReportTriaged])
    assert message.triaged_at == TRIAGED_AT
    received = messages.ReportReceived.model_validate(VALID[messages.ReportReceived])
    assert received.received_at == RECEIVED_AT
