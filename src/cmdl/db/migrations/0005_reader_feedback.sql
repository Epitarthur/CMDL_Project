-- Serving feedback stays separate from the immutable Deskdrop benchmark.
CREATE TABLE feed_deliveries (
    feed_id UUID PRIMARY KEY,
    session_id UUID NOT NULL,
    person_id source_id,
    release_id UUID NOT NULL REFERENCES serving_releases,
    items JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX delivered_reader ON feed_deliveries(person_id, created_at DESC);
CREATE INDEX delivered_session ON feed_deliveries(session_id, created_at DESC);
CREATE TABLE reader_feed_state (
    session_id UUID PRIMARY KEY,
    person_id source_id,
    feed_id UUID REFERENCES feed_deliveries,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE feed_impressions (
    impression_id UUID PRIMARY KEY,
    feed_id UUID NOT NULL REFERENCES feed_deliveries,
    content_id source_id NOT NULL REFERENCES articles,
    metadata_version_id UUID NOT NULL REFERENCES metadata_versions,
    visible_ms INTEGER NOT NULL CHECK (visible_ms BETWEEN 1000 AND 60000),
    rule_version TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (feed_id, content_id)
);
CREATE TABLE reader_preference_events (
    client_request_id UUID PRIMARY KEY,
    subject TEXT NOT NULL,
    language TEXT NOT NULL CHECK (language IN ('auto','all') OR language ~ '^[a-z]{2}$'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX latest_reader_preference ON reader_preference_events(subject, created_at DESC);
DO $$
DECLARE name TEXT;
BEGIN
    FOREACH name IN ARRAY ARRAY['feed_deliveries','feed_impressions','reader_preference_events'] LOOP
        EXECUTE format('CREATE TRIGGER immutable_rows BEFORE UPDATE OR DELETE ON %I FOR EACH ROW EXECUTE FUNCTION reject_mutation()', name);
        EXECUTE format('CREATE TRIGGER no_truncate BEFORE TRUNCATE ON %I EXECUTE FUNCTION reject_mutation()', name);
    END LOOP;
END $$;
