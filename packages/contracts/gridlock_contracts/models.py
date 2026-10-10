"""The value types and HTTP payloads of docs/design/domain-model.md sections 3 and 6.

Every model refuses a field it does not declare (extra="forbid"): a misspelt field raises
rather than being dropped, so two services cannot silently disagree about a payload. The
location rules -- an AMBIGUOUS or UNKNOWN location carries no cell and no coordinates --
are checked here as well as in the database, so a bad payload fails where it is built,
not three hops later.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from gridlock_contracts import geo
from gridlock_contracts.enums import LocationConfidence, ReportState, Tier


class ContractModel(BaseModel):
    """Base for every contract: unknown fields and non-finite numbers are refused.

    A field with a default is still always present in what a model sends, so its JSON
    Schema marks it required: a TypeScript client never has to guess it might be missing.
    """

    model_config = ConfigDict(
        extra="forbid", allow_inf_nan=False, json_schema_serialization_defaults_required=True
    )


def _canonical_cell(value: str) -> str:
    if not geo.is_cell(value):
        raise ValueError(f"{value!r} is not a lowercase H3 resolution-9 cell")
    return value


def _not_blank(value: str) -> str:
    if not value.strip():
        raise ValueError("must contain more than whitespace")
    return value  # the original, unstripped: text a person wrote is evidence


GridCell = Annotated[str, AfterValidator(_canonical_cell)]
"""An H3 resolution-9 cell, as gridlock_contracts.geo.cell_for returns it."""

NonBlankText = Annotated[str, AfterValidator(_not_blank)]

UNRESOLVED = frozenset({LocationConfidence.AMBIGUOUS, LocationConfidence.UNKNOWN})
"""Location outcomes that must carry neither a cell nor coordinates (domain-model section 3)."""


def refuse_unresolved_location(confidence: LocationConfidence, **location: object) -> None:
    """Raise if an AMBIGUOUS or UNKNOWN outcome carries any of the named location fields."""
    if confidence in UNRESOLVED:
        present = [name for name, value in location.items() if value is not None]
        if present:
            raise ValueError(
                f"{', '.join(present)} must be null when the location is {confidence.value}: "
                "an unresolved location is never given a place"
            )


def require_location(confidence: LocationConfidence, **location: object) -> None:
    """Raise if an EXACT or RESOLVED outcome is missing any of the named location fields."""
    missing = [name for name, value in location.items() if value is None]
    if missing:
        raise ValueError(
            f"{', '.join(missing)} is required when the location is {confidence.value}"
        )


class Coordinates(ContractModel):
    """A WGS84 point. Off-globe values are refused here: PostGIS would quietly move them."""

    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)


class EvidenceChunk(ContractModel):
    """One retrieved landmark chunk, exactly as the model saw it (rag.md invariant 7)."""

    landmark_id: UUID
    text: NonBlankText
    similarity: float


class RetrievalResult(ContractModel):
    """rag-index's answer to POST /internal/rag/retrieve (rag.md sections 3 and 7)."""

    status: LocationConfidence  # RESOLVED | AMBIGUOUS | UNKNOWN — never EXACT (rag never sees GPS)
    grid_cell: GridCell | None  # non-null only when status == RESOLVED
    resolved_coords: Coordinates | None  # non-null only when status == RESOLVED
    evidence: list[EvidenceChunk]  # [] when UNKNOWN; every competing match when AMBIGUOUS

    @model_validator(mode="after")
    def _follows_the_decision_rule(self) -> RetrievalResult:
        if self.status is LocationConfidence.EXACT:
            raise ValueError("rag-index never returns EXACT: it never sees device GPS")
        location = {"grid_cell": self.grid_cell, "resolved_coords": self.resolved_coords}
        refuse_unresolved_location(self.status, **location)
        if self.status is LocationConfidence.RESOLVED:
            require_location(self.status, **location)
            if not self.evidence:
                raise ValueError("evidence must hold the match that resolved the location")
        if self.status is LocationConfidence.UNKNOWN and self.evidence:
            raise ValueError("evidence must be empty when nothing matched (UNKNOWN)")
        return self


class QueueItem(ContractModel):
    """Exactly what the console renders; no extra fields, no fewer (domain-model section 6)."""

    report_id: UUID
    incident_id: UUID | None  # groups the console's cards; None = a group of one
    tier: Tier | None  # None when not yet triaged, or triage failed
    reason: str | None  # None when not yet triaged, or triage failed
    corroboration_count: int = Field(..., ge=1)  # derived: COUNT of reports sharing incident_id
    grid_cell: GridCell | None
    location_confidence: LocationConfidence  # UNKNOWN when not yet triaged
    state: ReportState
    received_at: AwareDatetime
    description: str  # verbatim
    failure_reason: str | None  # triage failure, or "not yet triaged" for a stale RECEIVED

    @model_validator(mode="after")
    def _never_places_an_unresolved_report(self) -> QueueItem:
        refuse_unresolved_location(self.location_confidence, grid_cell=self.grid_cell)
        return self
