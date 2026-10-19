-- A successful import and its exclusion audit commit with both normalized files.
CREATE TABLE source_imports (
    import_id UUID PRIMARY KEY,
    import_version TEXT NOT NULL CHECK (length(import_version) > 0),
    protocol_id TEXT NOT NULL CHECK (length(protocol_id) > 0),
    article_file_sha256 sha256_hex NOT NULL REFERENCES source_files ON DELETE RESTRICT,
    interaction_file_sha256 sha256_hex NOT NULL REFERENCES source_files ON DELETE RESTRICT,
    source_manifest JSONB NOT NULL CHECK (jsonb_typeof(source_manifest) = 'object'),
    report JSONB NOT NULL CHECK (jsonb_typeof(report) = 'object'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (import_version, protocol_id, article_file_sha256, interaction_file_sha256),
    CHECK (article_file_sha256 <> interaction_file_sha256)
);
CREATE TRIGGER immutable_rows BEFORE UPDATE OR DELETE ON source_imports
    FOR EACH ROW EXECUTE FUNCTION reject_mutation();
CREATE TRIGGER no_truncate BEFORE TRUNCATE ON source_imports EXECUTE FUNCTION reject_mutation();
