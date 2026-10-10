"""The three enums of docs/design/domain-model.md section 6, verbatim and in the same order.

Imported by every service, never re-declared. infra/postgres/migrations/0001_init.sql
mirrors them as Postgres enums in this order.
"""

from enum import Enum


class Tier(str, Enum):
    MONITOR = "MONITOR"
    ADVISORY = "ADVISORY"
    URGENT = "URGENT"
    CRITICAL_DISPATCH = "CRITICAL_DISPATCH"


class ReportState(str, Enum):
    RECEIVED = "RECEIVED"
    TRIAGED = "TRIAGED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    RESOLVED = "RESOLVED"


class LocationConfidence(str, Enum):
    EXACT = "EXACT"
    RESOLVED = "RESOLVED"
    AMBIGUOUS = "AMBIGUOUS"
    UNKNOWN = "UNKNOWN"
