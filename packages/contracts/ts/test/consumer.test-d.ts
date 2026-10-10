// Compiles only while the generated types match what clients build against. Imported by
// package name, exactly as apps/web and apps/mobile will import it, so a renamed, removed
// or added Pydantic field fails `tsc` here rather than reaching a client at runtime.
import type {
  Coordinates,
  IncidentUpdated,
  LocationConfidence,
  QueueItem,
  ReportState,
  Tier,
} from "@gridlock/contracts";

// Every field, by name. An object literal refuses unknown keys and needs every required one,
// so a rename on the Python side breaks this in both directions.
const item: QueueItem = {
  report_id: "7b89d4e5-6f1a-4d2b-9e3c-8f1a2b3c4d5e",
  incident_id: null,
  tier: "URGENT",
  reason: "Reporter describes a break-in in progress at the Spar on Vilakazi.",
  corroboration_count: 1,
  grid_cell: "89bcc3cc96bffff",
  location_confidence: "RESOLVED",
  state: "TRIAGED",
  received_at: "2026-09-29T12:00:00Z",
  description: "break-in at the Spar on Vilakazi",
  failure_reason: null,
};

// The nullable fields are nullable and nothing else is.
const stale: QueueItem = {
  ...item,
  tier: null,
  reason: null,
  grid_cell: null,
  location_confidence: "UNKNOWN",
  state: "RECEIVED",
  failure_reason: "not yet triaged",
};
// @ts-expect-error -- description is verbatim text, never null
const noDescription: QueueItem = { ...item, description: null };

// The enums are closed: the console can switch over them exhaustively.
const tiers = {
  MONITOR: 4,
  ADVISORY: 3,
  URGENT: 2,
  CRITICAL_DISPATCH: 1,
} satisfies Record<Tier, number>;
const states = [
  "RECEIVED",
  "TRIAGED",
  "NEEDS_REVIEW",
  "ACKNOWLEDGED",
  "RESOLVED",
] as const satisfies readonly ReportState[];
const confidences = [
  "EXACT",
  "RESOLVED",
  "AMBIGUOUS",
  "UNKNOWN",
] as const satisfies readonly LocationConfidence[];
// @ts-expect-error -- a tier outside the four does not exist
const invented: Tier = "HIGH";

const point: Coordinates = { lat: -26.2361, lon: 27.9068 };
const incident: IncidentUpdated = {
  incident_id: "7b89d4e5-6f1a-4d2b-9e3c-8f1a2b3c4d5e",
  grid_cell: "89bcc3cc96bffff",
  report_count: 3,
  peak_tier: "URGENT",
};

export const used = [
  item,
  stale,
  noDescription,
  tiers,
  states,
  confidences,
  invented,
  point,
  incident,
];
