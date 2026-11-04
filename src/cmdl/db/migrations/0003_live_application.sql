-- Live application extension. Historical source events and published snapshots stay immutable.
CREATE TABLE metadata_assessments (
    metadata_version_id UUID NOT NULL REFERENCES metadata_versions,
    checks_version TEXT NOT NULL,
    assessment_id UUID NOT NULL UNIQUE,
    result JSONB NOT NULL,
    assessed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (metadata_version_id, checks_version)
);
CREATE TABLE assessment_warnings (
    warning_id UUID PRIMARY KEY,
    assessment_id UUID NOT NULL REFERENCES metadata_assessments(assessment_id),
    metadata_version_id UUID NOT NULL REFERENCES metadata_versions,
    detail JSONB NOT NULL
);
CREATE TABLE assessment_reviews (
    review_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    warning_id UUID NOT NULL REFERENCES assessment_warnings,
    client_request_id UUID NOT NULL UNIQUE,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE live_reading_events (
    client_request_id UUID PRIMARY KEY,
    session_id UUID NOT NULL,
    person_id source_id,
    content_id source_id NOT NULL REFERENCES articles,
    metadata_version_id UUID NOT NULL REFERENCES metadata_versions,
    navigation_id UUID NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('view', 'complete')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE INDEX live_reader_history ON live_reading_events(person_id, content_id);
CREATE INDEX live_session_history ON live_reading_events(session_id, content_id);
CREATE TABLE reader_navigation (
    session_id UUID PRIMARY KEY,
    person_id source_id,
    content_id source_id NOT NULL REFERENCES articles,
    navigation_id UUID NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE next_requests (
    request_id UUID PRIMARY KEY,
    session_id UUID NOT NULL,
    person_id source_id,
    content_id source_id NOT NULL REFERENCES articles,
    navigation_id UUID NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'ready', 'failed')),
    result JSONB,
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE live_catalog_events (
    event_id UUID PRIMARY KEY,
    content_id source_id NOT NULL REFERENCES articles,
    kind TEXT NOT NULL CHECK (kind IN ('available', 'removed')),
    effective_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    reason TEXT NOT NULL
);
CREATE INDEX live_catalog_timeline ON live_catalog_events(content_id, effective_at DESC);
CREATE TABLE serving_releases (
    release_id UUID PRIMARY KEY,
    snapshot_id UUID NOT NULL REFERENCES snapshots,
    artifact_path TEXT NOT NULL,
    artifact_sha256 sha256_hex NOT NULL,
    manifest JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE serving_candidates (
    release_id UUID NOT NULL REFERENCES serving_releases,
    person_id source_id NOT NULL,
    content_ids JSONB NOT NULL CHECK (jsonb_array_length(content_ids) <= 50),
    PRIMARY KEY (release_id, person_id)
);
CREATE TABLE app_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
ALTER TABLE metadata_variants DROP CONSTRAINT metadata_variants_kind_check;
ALTER TABLE metadata_variants ADD CHECK (kind IN ('original', 'damage', 'correction'));
CREATE TABLE live_metadata_heads (
    content_id source_id PRIMARY KEY REFERENCES articles,
    metadata_version_id UUID NOT NULL,
    FOREIGN KEY (metadata_version_id, content_id)
        REFERENCES metadata_versions(metadata_version_id, content_id),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
-- Corrections are new versions; immutable originals and benchmark memberships are untouched.
CREATE FUNCTION guard_correction() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF EXISTS (SELECT 1 FROM metadata_variants WHERE variant_id=NEW.variant_id AND kind='correction')
        AND NOT EXISTS (SELECT 1 FROM metadata_versions b WHERE b.metadata_version_id=NEW.base_version_id
          AND b.content_id=NEW.content_id AND b.share_event_id=NEW.share_event_id) THEN
        RAISE EXCEPTION 'Correction requires the same article/share base version';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER correction_base BEFORE INSERT ON metadata_versions
FOR EACH ROW EXECUTE FUNCTION guard_correction();
DO $$
DECLARE name TEXT;
BEGIN
    FOREACH name IN ARRAY ARRAY['metadata_assessments','assessment_warnings','assessment_reviews',
        'live_reading_events','live_catalog_events','serving_releases','serving_candidates'] LOOP
        EXECUTE format('CREATE TRIGGER immutable_rows BEFORE UPDATE OR DELETE ON %I FOR EACH ROW EXECUTE FUNCTION reject_mutation()', name);
        EXECUTE format('CREATE TRIGGER no_truncate BEFORE TRUNCATE ON %I EXECUTE FUNCTION reject_mutation()', name);
    END LOOP;
END $$;
