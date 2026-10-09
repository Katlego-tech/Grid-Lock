"""AMQP messages on exchange `gridlock`: routing keys and their payloads.

docs/design/domain-model.md section 6. The body on the wire is the payload's JSON and
nothing else -- the routing key says which payload it is, so there is no wrapper around it.
`encode` and `decode` are the only way services put a message on, or take one off, the wire.
"""

from __future__ import annotations

from typing import ClassVar
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from gridlock_contracts.enums import LocationConfidence, Tier
from gridlock_contracts.models import (
    UNRESOLVED,
    ContractModel,
    Coordinates,
    GridCell,
    NonBlankText,
    refuse_unresolved_location,
    require_location,
)

EXCHANGE = "gridlock"


class Message(ContractModel):
    """A payload published on EXCHANGE under its routing_key."""

    routing_key: ClassVar[str]


class ReportReceived(Message):
    """ingest-api -> triage-engine, once the report is persisted."""

    routing_key: ClassVar[str] = "report.received"

    report_id: UUID
    description: NonBlankText = Field(..., min_length=1, max_length=2000)  # verbatim
    reported_coords: Coordinates | None = None
    reported_landmark: str | None = Field(None, max_length=200)
    category_hint: str | None = Field(None, max_length=100)
    received_at: AwareDatetime


class ReportTriaged(Message):
    """triage-engine -> verifier, after a successful triage."""

    routing_key: ClassVar[str] = "report.triaged"

    report_id: UUID
    tier: Tier
    grid_cell: GridCell | None = None
    resolved_coords: Coordinates | None = None
    location_confidence: LocationConfidence
    triaged_at: AwareDatetime

    @model_validator(mode="after")
    def _located_only_by_gps_or_a_landmark(self) -> ReportTriaged:
        # triage.md invariant 3: EXACT takes the device's point, RESOLVED the landmark's;
        # AMBIGUOUS and UNKNOWN are refusals to place the report at all.
        location = {"grid_cell": self.grid_cell, "resolved_coords": self.resolved_coords}
        if self.location_confidence in UNRESOLVED:
            refuse_unresolved_location(self.location_confidence, **location)
        else:
            require_location(self.location_confidence, **location)
        return self


class ReportNeedsReview(Message):
    """triage-engine -> nothing in the MVP (the console reads the database)."""

    routing_key: ClassVar[str] = "report.needs_review"

    report_id: UUID
    failure_reason: NonBlankText
    triaged_at: AwareDatetime


class IncidentUpdated(Message):
    """verifier -> nothing in the MVP (the console reads the database)."""

    routing_key: ClassVar[str] = "incident.updated"

    incident_id: UUID
    grid_cell: GridCell
    report_count: int = Field(..., ge=1)
    peak_tier: Tier


PAYLOADS: dict[str, type[Message]] = {
    model.routing_key: model
    for model in (ReportReceived, ReportTriaged, ReportNeedsReview, IncidentUpdated)
}
"""Routing key -> the payload model published under it."""


def encode(message: Message) -> tuple[str, bytes]:
    """The routing key and body to publish `message` with."""
    return message.routing_key, message.model_dump_json().encode()


def decode(routing_key: str, body: bytes) -> Message:
    """Parse a delivered body as the payload its routing key names.

    Raises KeyError for a routing key with no payload, and pydantic.ValidationError for a
    body that is not exactly that payload.
    """
    try:
        model = PAYLOADS[routing_key]
    except KeyError:
        raise KeyError(f"no payload is published under routing key {routing_key!r}") from None
    return model.model_validate_json(body)
