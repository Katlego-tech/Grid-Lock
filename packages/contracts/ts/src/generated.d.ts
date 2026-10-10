/**
 * GridLock contracts, generated from packages/contracts/gridlock_contracts. Do not edit.
 *
 * Change the Pydantic model, then regenerate from the repo root:
 *   uv run python -m packages.contracts.scripts.export_schema
 *   npm run build -w packages/contracts/ts
 */

/**
 * Any GridLock contract payload.
 */
export type GridLockContract =
  | Coordinates
  | EvidenceChunk
  | RetrievalResult
  | QueueItem
  | ReportReceived
  | ReportTriaged
  | ReportNeedsReview
  | IncidentUpdated;
export type LocationConfidence = "EXACT" | "RESOLVED" | "AMBIGUOUS" | "UNKNOWN";
export type Tier = "MONITOR" | "ADVISORY" | "URGENT" | "CRITICAL_DISPATCH";
export type ReportState = "RECEIVED" | "TRIAGED" | "NEEDS_REVIEW" | "ACKNOWLEDGED" | "RESOLVED";

/**
 * A WGS84 point. Off-globe values are refused here: PostGIS would quietly move them.
 */
export interface Coordinates {
  lat: number;
  lon: number;
}
/**
 * One retrieved landmark chunk, exactly as the model saw it (rag.md invariant 7).
 */
export interface EvidenceChunk {
  landmark_id: string;
  text: string;
  similarity: number;
}
/**
 * rag-index's answer to POST /internal/rag/retrieve (rag.md sections 3 and 7).
 */
export interface RetrievalResult {
  status: LocationConfidence;
  grid_cell: string | null;
  resolved_coords: Coordinates | null;
  evidence: EvidenceChunk[];
}
/**
 * Exactly what the console renders; no extra fields, no fewer (domain-model section 6).
 */
export interface QueueItem {
  report_id: string;
  incident_id: string | null;
  tier: Tier | null;
  reason: string | null;
  corroboration_count: number;
  grid_cell: string | null;
  location_confidence: LocationConfidence;
  state: ReportState;
  received_at: string;
  description: string;
  failure_reason: string | null;
}
/**
 * ingest-api -> triage-engine, once the report is persisted.
 */
export interface ReportReceived {
  report_id: string;
  description: string;
  reported_coords: Coordinates | null;
  reported_landmark: string | null;
  category_hint: string | null;
  received_at: string;
}
/**
 * triage-engine -> verifier, after a successful triage.
 */
export interface ReportTriaged {
  report_id: string;
  tier: Tier;
  grid_cell: string | null;
  resolved_coords: Coordinates | null;
  location_confidence: LocationConfidence;
  triaged_at: string;
}
/**
 * triage-engine -> nothing in the MVP (the console reads the database).
 */
export interface ReportNeedsReview {
  report_id: string;
  failure_reason: string;
  triaged_at: string;
}
/**
 * verifier -> nothing in the MVP (the console reads the database).
 */
export interface IncidentUpdated {
  incident_id: string;
  grid_cell: string;
  report_count: number;
  peak_tier: Tier;
}
