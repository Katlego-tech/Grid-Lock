-- 0001_init.sql -- the GridLock schema: docs/design/domain-model.md section 3.
--
-- Every table and column maps to a field of that document's class diagram, and the infra
-- test suite reads the diagram and fails if the two drift apart in either direction. A
-- column that is not in the document does not belong here -- change the document first.
--
-- Applied by infra/postgres/migrate.sh, which wraps each file in one transaction and
-- records it in schema_migrations. So no BEGIN/COMMIT here.
--
-- The invariants are enforced by the database, not by the services that write to it: a
-- rule in one service's code does nothing to stop another service, or a hand-typed psql
-- session, from breaking it.
--
--   * AMBIGUOUS / UNKNOWN location -> grid_cell and resolved_coords are NULL
--     (the place a hallucinated location would enter the system)
--   * a triage result is a success (tier + reason) or a failure (failure_reason), never both
--   * Report.state moves only along the section 5 diagram; a report enters TRIAGED only with
--     a result carrying a tier and NEEDS_REVIEW only with a recorded failure, and a TRIAGED
--     report's result cannot lose its tier
--   * reports and triage results are never deleted; what the reporter sent is never rewritten
--   * incidents are OPEN only: MERGED and CLOSED have no designed trigger (section 10)
--
-- corroboration_count is not a column: it is derived at read time (section 3).
-- Coordinates are WGS84 geography points; timestamps are timestamptz.

-- ------------------------------------------------------------------ enums ---
-- Mirrors packages/contracts/gridlock_contracts/enums.py, in the same order. A value
-- outside these sets cannot be stored: an out-of-set tier is refused here, never rounded.

CREATE TYPE tier AS ENUM ('MONITOR', 'ADVISORY', 'URGENT', 'CRITICAL_DISPATCH');

CREATE TYPE report_state AS ENUM (
    'RECEIVED', 'TRIAGED', 'NEEDS_REVIEW', 'ACKNOWLEDGED', 'RESOLVED'
);

CREATE TYPE location_confidence AS ENUM ('EXACT', 'RESOLVED', 'AMBIGUOUS', 'UNKNOWN');

CREATE TYPE incident_state AS ENUM ('OPEN', 'MERGED', 'CLOSED');

-- ------------------------------------------------------------- grid cells ---
-- "grid_cell is an H3 resolution-9 cell as a 15-character lowercase hex string"
-- (section 3). Every res-9 cell starts 89. This does not prove a cell exists -- only
-- gridlock_contracts.geo.cell_for makes cells -- but it keeps a suburb name, a padded
-- string or an upper-cased copy out of a column that incidents are grouped by.

CREATE DOMAIN h3_cell AS text CHECK (VALUE ~ '^89[0-9a-f]{13}$');

-- -------------------------------------------------------------- landmarks ---
-- rag-index's source of truth. Its vector rows (rag.md LandmarkIndexRow) are rag-index's
-- own table, built from these.

CREATE TABLE landmarks (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name       text NOT NULL CHECK (name ~ '[^[:space:]]'),
    aliases    text[] NOT NULL DEFAULT '{}',
    category   text NOT NULL CHECK (category ~ '[^[:space:]]'),
    address    text NOT NULL CHECK (address ~ '[^[:space:]]'),
    suburb     text NOT NULL CHECK (suburb ~ '[^[:space:]]'),
    coords     geography(Point, 4326) NOT NULL,
    grid_cell  h3_cell NOT NULL,
    source     text NOT NULL CHECK (source ~ '[^[:space:]]'),
    CONSTRAINT landmarks_aliases_have_no_nulls CHECK (array_position(aliases, NULL) IS NULL)
);

CREATE INDEX landmarks_coords_gix ON landmarks USING gist (coords);
CREATE INDEX landmarks_grid_cell_ix ON landmarks (grid_cell);

-- -------------------------------------------------------------- incidents ---
-- Owned by the verifier (verification.md). report_count is kept equal to the COUNT(*) of
-- linked reports in the same transaction as each link; it feeds incident.updated only.
-- opened_at and last_report_at are REPORT times (received_at), never processing times
-- (verification.md invariant 4), so neither has a now() default to fall back on.

CREATE TABLE incidents (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    grid_cell       h3_cell NOT NULL,
    peak_tier       tier NOT NULL,
    report_count    integer NOT NULL CHECK (report_count >= 1),
    opened_at       timestamptz NOT NULL,
    last_report_at  timestamptz NOT NULL,
    state           incident_state NOT NULL DEFAULT 'OPEN',
    -- MERGED and CLOSED are reserved values with no designed trigger (domain-model
    -- sections 5 and 10): "no code may write them". Lifting this is a migration that
    -- ships with the action that closes or merges an incident.
    CONSTRAINT incidents_open_only_until_close_and_merge_are_designed CHECK (state = 'OPEN')
);

-- verification.md's candidate query: open incidents in these cells, inside the window.
CREATE INDEX incidents_open_cell_ix ON incidents (grid_cell, last_report_at)
    WHERE state = 'OPEN';

-- ---------------------------------------------------------------- reports ---
-- Written by ingest-api before anything else happens to a report. description is
-- evidence and stored verbatim; received_at is the server's clock at request time.

CREATE TABLE reports (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    description        text NOT NULL
                       CHECK (char_length(description) BETWEEN 1 AND 2000)
                       CHECK (description ~ '[^[:space:]]'),
    reported_coords    geography(Point, 4326),
    reported_landmark  text CHECK (char_length(reported_landmark) <= 200),
    category_hint      text CHECK (char_length(category_hint) <= 100),
    state              report_state NOT NULL DEFAULT 'RECEIVED',
    received_at        timestamptz NOT NULL DEFAULT now(),
    incident_id        uuid REFERENCES incidents (id),
    -- 'web' for the MVP, deliberately not restricted to it: a CCTV or IoT publisher is
    -- meant to arrive without a schema migration.
    source_channel     text NOT NULL DEFAULT 'web' CHECK (source_channel ~ '[^[:space:]]')
);

-- ingest.md section 6.3.
CREATE INDEX reports_state_received_ix ON reports (state, received_at);
CREATE INDEX reports_incident_ix ON reports (incident_id) WHERE incident_id IS NOT NULL;
CREATE INDEX reports_coords_gix ON reports USING gist (reported_coords);

-- --------------------------------------------------------- triage_results ---
-- One per report (report_id UNIQUE), written for every triage attempt that completes,
-- failed ones included, so the failure and whatever evidence was retrieved are kept.

-- evidence is List<EvidenceChunk>: [{landmark_id, text, similarity}, ...], exactly those
-- keys. Stored with the result it justified rather than as rows pointing at landmarks,
-- so a rebuilt landmark index can never change or orphan the evidence behind a past
-- decision: the chunk text is kept as the model saw it.
CREATE FUNCTION evidence_is_well_formed(evidence jsonb) RETURNS boolean
LANGUAGE sql IMMUTABLE AS $$
    SELECT jsonb_typeof(evidence) = 'array'
       AND NOT EXISTS (
           SELECT 1
             FROM jsonb_array_elements(evidence) AS chunk
            WHERE NOT (
                jsonb_typeof(chunk) = 'object'
                AND (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(chunk) AS k)
                    = ARRAY['landmark_id', 'similarity', 'text']
                AND jsonb_typeof(chunk -> 'landmark_id') = 'string'
                AND chunk ->> 'landmark_id'
                    ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
                AND jsonb_typeof(chunk -> 'text') = 'string'
                AND chunk ->> 'text' ~ '[^[:space:]]'
                AND jsonb_typeof(chunk -> 'similarity') = 'number'
            )
       )
$$;

CREATE TABLE triage_results (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    report_id            uuid NOT NULL UNIQUE REFERENCES reports (id),
    tier                 tier,
    reason               text CHECK (reason ~ '[^[:space:]]'),
    evidence             jsonb NOT NULL DEFAULT '[]'
                         CONSTRAINT triage_evidence_is_well_formed
                         CHECK (evidence_is_well_formed(evidence)),
    grid_cell            h3_cell,
    resolved_coords      geography(Point, 4326),
    location_confidence  location_confidence NOT NULL,
    model_id             text NOT NULL CHECK (model_id ~ '[^[:space:]]'),
    prompt_version       text NOT NULL CHECK (prompt_version ~ '[^[:space:]]'),
    triaged_at           timestamptz NOT NULL DEFAULT now(),
    failure_reason       text CHECK (failure_reason ~ '[^[:space:]]'),

    -- The grounding rule. One constraint per case, so a violation names which it was.
    CONSTRAINT triage_ambiguous_has_no_grid_cell
        CHECK (location_confidence <> 'AMBIGUOUS' OR grid_cell IS NULL),
    CONSTRAINT triage_unknown_has_no_grid_cell
        CHECK (location_confidence <> 'UNKNOWN' OR grid_cell IS NULL),
    -- The same rule for the point itself (rag.md invariant 2): coordinates without a
    -- cell are still an invented location.
    CONSTRAINT triage_ambiguous_has_no_resolved_coords
        CHECK (location_confidence <> 'AMBIGUOUS' OR resolved_coords IS NULL),
    CONSTRAINT triage_unknown_has_no_resolved_coords
        CHECK (location_confidence <> 'UNKNOWN' OR resolved_coords IS NULL),

    -- Exactly one of the two shapes (section 3).
    CONSTRAINT triage_success_xor_failure CHECK (
        (tier IS NOT NULL AND reason IS NOT NULL AND failure_reason IS NULL)
        OR (tier IS NULL AND reason IS NULL AND failure_reason IS NOT NULL)
    )
);

CREATE INDEX triage_results_tier_ix ON triage_results (tier);

-- ========================================================== lifecycle rules ==
-- "Transitions not drawn here must be made impossible in code, not merely
-- unimplemented." The database is the one piece of code every service shares.

-- Report.state, section 5. Any pair not listed is refused. NEEDS_REVIEW -> TRIAGED and
-- ACKNOWLEDGED -> RESOLVED are drawn but have no designed action yet; they are legal
-- here so the action can be built without a migration, and unreachable until it is.
CREATE FUNCTION report_transition_allowed(from_state report_state, to_state report_state)
RETURNS boolean
LANGUAGE sql IMMUTABLE AS $$
    SELECT (from_state, to_state) IN (
        ('RECEIVED'::report_state,     'TRIAGED'::report_state),
        ('RECEIVED'::report_state,     'NEEDS_REVIEW'::report_state),
        ('NEEDS_REVIEW'::report_state, 'TRIAGED'::report_state),
        ('RECEIVED'::report_state,     'ACKNOWLEDGED'::report_state),
        ('TRIAGED'::report_state,      'ACKNOWLEDGED'::report_state),
        ('NEEDS_REVIEW'::report_state, 'ACKNOWLEDGED'::report_state),
        ('ACKNOWLEDGED'::report_state, 'RESOLVED'::report_state)
    )
$$;

CREATE FUNCTION reports_guard_insert() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.state <> 'RECEIVED' THEN
        RAISE EXCEPTION 'a report is created in state RECEIVED, not %', NEW.state
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END
$$;

CREATE FUNCTION reports_guard_update() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    -- What the reporter sent, and when the server received it, is evidence.
    IF NEW.id IS DISTINCT FROM OLD.id
       OR NEW.description IS DISTINCT FROM OLD.description
       OR NEW.reported_coords IS DISTINCT FROM OLD.reported_coords
       OR NEW.reported_landmark IS DISTINCT FROM OLD.reported_landmark
       OR NEW.category_hint IS DISTINCT FROM OLD.category_hint
       OR NEW.received_at IS DISTINCT FROM OLD.received_at
       OR NEW.source_channel IS DISTINCT FROM OLD.source_channel THEN
        RAISE EXCEPTION 'report % is evidence: only state and incident_id may change', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;

    IF NEW.state IS DISTINCT FROM OLD.state
       AND NOT report_transition_allowed(OLD.state, NEW.state) THEN
        RAISE EXCEPTION 'report % cannot move from % to %', OLD.id, OLD.state, NEW.state
            USING ERRCODE = 'check_violation';
    END IF;

    -- A state claims something about the triage outcome; the outcome must exist and agree.
    -- The triage engine writes the result first, then moves the report.
    IF NEW.state IS DISTINCT FROM OLD.state AND NEW.state = 'TRIAGED'
       AND NOT EXISTS (SELECT 1 FROM triage_results t
                        WHERE t.report_id = NEW.id AND t.tier IS NOT NULL) THEN
        RAISE EXCEPTION 'report % cannot be TRIAGED without a triage result carrying a tier', NEW.id
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.state IS DISTINCT FROM OLD.state AND NEW.state = 'NEEDS_REVIEW'
       AND NOT EXISTS (SELECT 1 FROM triage_results t
                        WHERE t.report_id = NEW.id AND t.failure_reason IS NOT NULL) THEN
        RAISE EXCEPTION 'report % cannot be NEEDS_REVIEW without a recorded failure_reason', NEW.id
            USING ERRCODE = 'check_violation';
    END IF;

    -- Grouping is history too: a resolved report stays with the incident it closed in, and
    -- a report can only join an incident that is open.
    IF NEW.incident_id IS DISTINCT FROM OLD.incident_id THEN
        IF OLD.state = 'RESOLVED' THEN
            RAISE EXCEPTION 'report % is RESOLVED; its incident link is final', OLD.id
                USING ERRCODE = 'restrict_violation';
        END IF;
        IF NEW.incident_id IS NOT NULL AND NOT EXISTS (
               SELECT 1 FROM incidents i WHERE i.id = NEW.incident_id AND i.state = 'OPEN') THEN
            RAISE EXCEPTION 'report % can only be linked to an OPEN incident', OLD.id
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    RETURN NEW;
END
$$;

-- A TRIAGED report's result keeps its tier. (A re-run may turn a failure into a success;
-- nothing may turn a TRIAGED report's success back into a failure behind its state.)
CREATE FUNCTION triage_results_guard_update() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.report_id IS DISTINCT FROM OLD.report_id THEN
        RAISE EXCEPTION 'a triage result belongs to one report for good'
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF NEW.tier IS NULL AND EXISTS (
           SELECT 1 FROM reports r WHERE r.id = NEW.report_id AND r.state = 'TRIAGED') THEN
        RAISE EXCEPTION 'report % is TRIAGED; its result cannot lose its tier', NEW.report_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END
$$;

-- No deletion path, by design: a mistaken close is corrected by a new report, not by
-- rewriting history, and the tier, reason and evidence behind a decision are persisted
-- together, always. TRUNCATE is refused too, since it bypasses row triggers.
CREATE FUNCTION refuse_removal() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% is an audit trail and is never deleted (%)', TG_TABLE_NAME, TG_OP
        USING ERRCODE = 'restrict_violation';
END
$$;

CREATE TRIGGER reports_guard_insert BEFORE INSERT ON reports
    FOR EACH ROW EXECUTE FUNCTION reports_guard_insert();
CREATE TRIGGER reports_guard_update BEFORE UPDATE ON reports
    FOR EACH ROW EXECUTE FUNCTION reports_guard_update();
CREATE TRIGGER reports_refuse_delete BEFORE DELETE ON reports
    FOR EACH ROW EXECUTE FUNCTION refuse_removal();
CREATE TRIGGER reports_refuse_truncate BEFORE TRUNCATE ON reports
    FOR EACH STATEMENT EXECUTE FUNCTION refuse_removal();

CREATE TRIGGER triage_results_guard_update BEFORE UPDATE ON triage_results
    FOR EACH ROW EXECUTE FUNCTION triage_results_guard_update();
CREATE TRIGGER triage_results_refuse_delete BEFORE DELETE ON triage_results
    FOR EACH ROW EXECUTE FUNCTION refuse_removal();
CREATE TRIGGER triage_results_refuse_truncate BEFORE TRUNCATE ON triage_results
    FOR EACH STATEMENT EXECUTE FUNCTION refuse_removal();
