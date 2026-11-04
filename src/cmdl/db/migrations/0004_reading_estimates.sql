-- Preserve historical explicit completions; automatic estimates are distinct events.
ALTER TABLE live_reading_events DROP CONSTRAINT live_reading_events_kind_check;
ALTER TABLE live_reading_events ADD CONSTRAINT live_reading_events_kind_check
    CHECK (kind IN ('view', 'complete', 'estimated_complete'));
CREATE UNIQUE INDEX one_estimate_per_navigation
    ON live_reading_events(session_id, navigation_id) WHERE kind='estimated_complete';
CREATE TABLE reading_completion_estimates (
    client_request_id UUID PRIMARY KEY REFERENCES live_reading_events,
    rule_version TEXT NOT NULL,
    active_seconds DOUBLE PRECISION NOT NULL
        CHECK (active_seconds >= 0 AND active_seconds < 'Infinity'::float8),
    required_seconds INTEGER NOT NULL CHECK (required_seconds BETWEEN 10 AND 120),
    end_visible BOOLEAN NOT NULL CHECK (end_visible),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE TRIGGER immutable_rows BEFORE UPDATE OR DELETE ON reading_completion_estimates
    FOR EACH ROW EXECUTE FUNCTION reject_mutation();
CREATE TRIGGER no_truncate BEFORE TRUNCATE ON reading_completion_estimates
    FOR EACH STATEMENT EXECUTE FUNCTION reject_mutation();
