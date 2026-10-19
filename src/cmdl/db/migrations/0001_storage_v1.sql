-- cmdl-storage-v1. Immutable once applied; extend through a new migration.
CREATE DOMAIN source_id AS TEXT
    CHECK (VALUE ~ '^-?(0|[1-9][0-9]*)$' AND VALUE !~ '[^0-9-]');
CREATE DOMAIN sha256_hex AS TEXT
    CHECK (VALUE ~ '^[0-9a-f]{64}$' AND length(VALUE) = 64);
CREATE DOMAIN unit_score AS DOUBLE PRECISION
    CHECK (VALUE >= 0 AND VALUE <= 1);

CREATE TABLE source_files (
    sha256 sha256_hex PRIMARY KEY,
    filename TEXT NOT NULL CHECK (length(filename) > 0),
    bytes BIGINT NOT NULL CHECK (bytes >= 0),
    dataset_ref TEXT NOT NULL,
    upstream_version TEXT NOT NULL,
    event_category TEXT NOT NULL CHECK (event_category IN ('article', 'interaction')),
    imported_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE source_rows (
    file_sha256 sha256_hex NOT NULL REFERENCES source_files ON DELETE RESTRICT,
    row_number BIGINT NOT NULL CHECK (row_number >= 1),
    raw_fields JSONB NOT NULL CHECK (jsonb_typeof(raw_fields) = 'object'),
    duplicate_file_sha256 sha256_hex,
    duplicate_row_number BIGINT,
    PRIMARY KEY (file_sha256, row_number),
    FOREIGN KEY (duplicate_file_sha256, duplicate_row_number)
        REFERENCES source_rows(file_sha256, row_number) MATCH FULL ON DELETE RESTRICT,
    CHECK (duplicate_file_sha256 IS NULL OR
        (duplicate_file_sha256 = file_sha256 AND duplicate_row_number < row_number))
);
CREATE TABLE users (
    person_id source_id PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE articles (
    content_id source_id PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE article_events (
    article_event_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    content_id source_id NOT NULL REFERENCES articles ON DELETE RESTRICT,
    event_type TEXT NOT NULL CHECK (event_type IN ('CONTENT SHARED', 'CONTENT REMOVED')),
    event_at TIMESTAMPTZ NOT NULL CHECK (isfinite(event_at)),
    file_sha256 sha256_hex NOT NULL,
    row_number BIGINT NOT NULL,
    UNIQUE (file_sha256, row_number),
    UNIQUE (article_event_id, content_id),
    FOREIGN KEY (file_sha256, row_number) REFERENCES source_rows ON DELETE RESTRICT
);
CREATE TABLE interactions (
    interaction_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    person_id source_id NOT NULL REFERENCES users ON DELETE RESTRICT,
    content_id source_id NOT NULL REFERENCES articles ON DELETE RESTRICT,
    session_id source_id NOT NULL,
    event_type TEXT NOT NULL CHECK (event_type IN ('VIEW', 'LIKE', 'BOOKMARK', 'FOLLOW', 'COMMENT CREATED')),
    event_at TIMESTAMPTZ NOT NULL CHECK (isfinite(event_at)),
    file_sha256 sha256_hex NOT NULL,
    row_number BIGINT NOT NULL,
    UNIQUE (file_sha256, row_number),
    FOREIGN KEY (file_sha256, row_number) REFERENCES source_rows ON DELETE RESTRICT
);
CREATE TABLE metadata_variants (
    variant_id TEXT PRIMARY KEY CHECK (length(variant_id) > 0),
    kind TEXT NOT NULL CHECK (kind IN ('original', 'damage')),
    config JSONB NOT NULL CHECK (jsonb_typeof(config) = 'object'),
    config_sha256 sha256_hex NOT NULL,
    seed BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE UNIQUE INDEX one_original_variant ON metadata_variants(kind) WHERE kind = 'original';
CREATE TABLE metadata_versions (
    metadata_version_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    content_id source_id NOT NULL REFERENCES articles ON DELETE RESTRICT,
    share_event_id UUID NOT NULL,
    variant_id TEXT NOT NULL REFERENCES metadata_variants ON DELETE RESTRICT,
    base_version_id UUID REFERENCES metadata_versions ON DELETE RESTRICT,
    title TEXT NOT NULL,
    text TEXT NOT NULL,
    url TEXT NOT NULL,
    lang TEXT NOT NULL,
    content_type TEXT NOT NULL,
    payload_sha256 sha256_hex NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (share_event_id, variant_id),
    UNIQUE (metadata_version_id, content_id),
    FOREIGN KEY (share_event_id, content_id)
        REFERENCES article_events(article_event_id, content_id) ON DELETE RESTRICT
);
CREATE TABLE snapshots (
    snapshot_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    protocol_id TEXT NOT NULL CHECK (length(protocol_id) > 0),
    as_of_at TIMESTAMPTZ NOT NULL CHECK (isfinite(as_of_at)),
    source_manifest JSONB NOT NULL CHECK (jsonb_typeof(source_manifest) = 'object'),
    transform_version TEXT NOT NULL CHECK (length(transform_version) > 0),
    metadata_variant_id TEXT NOT NULL REFERENCES metadata_variants ON DELETE RESTRICT,
    status TEXT NOT NULL DEFAULT 'building' CHECK (status IN ('building', 'published', 'failed')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE snapshot_articles (
    snapshot_id UUID NOT NULL REFERENCES snapshots ON DELETE RESTRICT,
    content_id source_id NOT NULL REFERENCES articles ON DELETE RESTRICT,
    metadata_version_id UUID,
    availability TEXT NOT NULL CHECK (availability IN ('available', 'not_shared', 'removed', 'ambiguous')),
    PRIMARY KEY (snapshot_id, content_id),
    UNIQUE (snapshot_id, metadata_version_id),
    FOREIGN KEY (metadata_version_id, content_id)
        REFERENCES metadata_versions(metadata_version_id, content_id) ON DELETE RESTRICT,
    CHECK (availability <> 'available' OR metadata_version_id IS NOT NULL)
);
CREATE TABLE snapshot_interactions (
    snapshot_id UUID NOT NULL REFERENCES snapshots ON DELETE RESTRICT,
    interaction_id UUID NOT NULL REFERENCES interactions ON DELETE RESTRICT,
    duplicate_of_interaction_id UUID REFERENCES interactions ON DELETE RESTRICT,
    exclusion_reason TEXT CHECK (exclusion_reason IN ('unresolved', 'removal_only', 'pre_share', 'ambiguous', 'removed')),
    PRIMARY KEY (snapshot_id, interaction_id),
    CHECK (duplicate_of_interaction_id IS NULL OR duplicate_of_interaction_id <> interaction_id)
);
CREATE TABLE quality_runs (
    quality_run_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    snapshot_id UUID NOT NULL REFERENCES snapshots ON DELETE RESTRICT,
    checks_version TEXT NOT NULL CHECK (length(checks_version) > 0),
    config JSONB NOT NULL CHECK (jsonb_typeof(config) = 'object'),
    config_sha256 sha256_hex NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'running', 'completed', 'failed')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    completed_at TIMESTAMPTZ,
    UNIQUE (quality_run_id, snapshot_id),
    CHECK ((status = 'completed') = (completed_at IS NOT NULL)),
    CHECK (completed_at IS NULL OR (isfinite(completed_at) AND completed_at >= created_at))
);
CREATE TABLE quality_results (
    quality_run_id UUID NOT NULL,
    metadata_version_id UUID NOT NULL,
    snapshot_id UUID NOT NULL,
    completeness_score unit_score,
    validity_score unit_score,
    consistency_score unit_score,
    informativeness_score unit_score,
    overall_score unit_score,
    dimension_coverage JSONB NOT NULL,
    PRIMARY KEY (quality_run_id, metadata_version_id),
    FOREIGN KEY (quality_run_id, snapshot_id)
        REFERENCES quality_runs(quality_run_id, snapshot_id) ON DELETE RESTRICT,
    FOREIGN KEY (snapshot_id, metadata_version_id)
        REFERENCES snapshot_articles(snapshot_id, metadata_version_id) ON DELETE RESTRICT
);
CREATE TABLE quality_warnings (
    warning_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    quality_run_id UUID NOT NULL,
    metadata_version_id UUID NOT NULL,
    check_id TEXT NOT NULL CHECK (length(check_id) > 0),
    field TEXT NOT NULL CHECK (length(field) > 0),
    dimension TEXT NOT NULL CHECK (dimension IN ('completeness', 'validity', 'consistency', 'informativeness')),
    severity TEXT NOT NULL CHECK (severity IN ('info', 'warning', 'error')),
    reason TEXT NOT NULL CHECK (length(reason) > 0),
    UNIQUE (quality_run_id, metadata_version_id, check_id, field),
    UNIQUE (warning_id, quality_run_id, metadata_version_id),
    FOREIGN KEY (quality_run_id, metadata_version_id) REFERENCES quality_results ON DELETE RESTRICT
);
CREATE TABLE review_decisions (
    review_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    warning_id UUID NOT NULL,
    quality_run_id UUID NOT NULL,
    metadata_version_id UUID NOT NULL,
    snapshot_id UUID NOT NULL,
    reviewer_label TEXT NOT NULL CHECK (length(reviewer_label) BETWEEN 1 AND 100
        AND reviewer_label = btrim(reviewer_label) AND reviewer_label !~ '^\s*$'),
    decision TEXT NOT NULL CHECK (decision IN ('confirm', 'reject')),
    client_request_id UUID NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp() CHECK (isfinite(created_at)),
    comment TEXT CHECK (length(comment) <= 1000),
    FOREIGN KEY (warning_id, quality_run_id, metadata_version_id)
        REFERENCES quality_warnings(warning_id, quality_run_id, metadata_version_id) ON DELETE RESTRICT,
    FOREIGN KEY (quality_run_id, snapshot_id)
        REFERENCES quality_runs(quality_run_id, snapshot_id) ON DELETE RESTRICT
);
CREATE TABLE feature_sets (
    feature_set_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    snapshot_id UUID NOT NULL REFERENCES snapshots ON DELETE RESTRICT,
    feature_version TEXT NOT NULL CHECK (length(feature_version) > 0),
    artifact_uri TEXT NOT NULL CHECK (length(artifact_uri) > 0),
    artifact_sha256 sha256_hex NOT NULL,
    config JSONB NOT NULL CHECK (jsonb_typeof(config) = 'object'),
    fitted_before_at TIMESTAMPTZ NOT NULL CHECK (isfinite(fitted_before_at)),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (feature_set_id, snapshot_id)
);
CREATE TABLE feature_items (
    feature_set_id UUID NOT NULL,
    content_id source_id NOT NULL,
    metadata_version_id UUID NOT NULL,
    snapshot_id UUID NOT NULL,
    PRIMARY KEY (feature_set_id, content_id),
    FOREIGN KEY (feature_set_id, snapshot_id)
        REFERENCES feature_sets(feature_set_id, snapshot_id) ON DELETE RESTRICT,
    FOREIGN KEY (metadata_version_id, content_id)
        REFERENCES metadata_versions(metadata_version_id, content_id) ON DELETE RESTRICT,
    FOREIGN KEY (snapshot_id, metadata_version_id)
        REFERENCES snapshot_articles(snapshot_id, metadata_version_id) ON DELETE RESTRICT
);
CREATE TABLE model_runs (
    model_run_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    snapshot_id UUID NOT NULL REFERENCES snapshots ON DELETE RESTRICT,
    protocol_id TEXT NOT NULL CHECK (length(protocol_id) > 0),
    model_type TEXT NOT NULL CHECK (model_type IN ('popularity', 'tfidf', 'als', 'fixed_hybrid', 'semantic', 'quality_hybrid')),
    feature_set_id UUID,
    quality_run_id UUID,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'running', 'completed', 'failed')),
    manifest JSONB NOT NULL CHECK (jsonb_typeof(manifest) = 'object'),
    artifact_uri TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (model_run_id, snapshot_id),
    FOREIGN KEY (feature_set_id, snapshot_id)
        REFERENCES feature_sets(feature_set_id, snapshot_id) ON DELETE RESTRICT,
    FOREIGN KEY (quality_run_id, snapshot_id)
        REFERENCES quality_runs(quality_run_id, snapshot_id) ON DELETE RESTRICT,
    CHECK (model_type <> 'quality_hybrid' OR quality_run_id IS NOT NULL)
);
CREATE TABLE ranking_sets (
    ranking_set_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    snapshot_id UUID NOT NULL REFERENCES snapshots ON DELETE RESTRICT,
    model_run_id UUID NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('user', 'similar')),
    status TEXT NOT NULL DEFAULT 'building' CHECK (status IN ('building', 'published', 'failed')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    UNIQUE (ranking_set_id, snapshot_id),
    FOREIGN KEY (model_run_id, snapshot_id)
        REFERENCES model_runs(model_run_id, snapshot_id) ON DELETE RESTRICT
);
CREATE TABLE prepared_rankings (
    ranking_entry_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ranking_set_id UUID NOT NULL,
    snapshot_id UUID NOT NULL,
    person_id source_id REFERENCES users ON DELETE RESTRICT,
    reference_content_id source_id REFERENCES articles ON DELETE RESTRICT,
    content_id source_id NOT NULL REFERENCES articles ON DELETE RESTRICT,
    metadata_version_id UUID NOT NULL,
    rank SMALLINT NOT NULL CHECK (rank BETWEEN 1 AND 50),
    score DOUBLE PRECISION NOT NULL CHECK (score > '-Infinity'::float8 AND score < 'Infinity'::float8),
    explanation_code TEXT NOT NULL CHECK (length(explanation_code) > 0),
    FOREIGN KEY (ranking_set_id, snapshot_id)
        REFERENCES ranking_sets(ranking_set_id, snapshot_id) ON DELETE RESTRICT,
    FOREIGN KEY (metadata_version_id, content_id)
        REFERENCES metadata_versions(metadata_version_id, content_id) ON DELETE RESTRICT,
    FOREIGN KEY (snapshot_id, metadata_version_id)
        REFERENCES snapshot_articles(snapshot_id, metadata_version_id) ON DELETE RESTRICT,
    CHECK ((person_id IS NULL) <> (reference_content_id IS NULL)),
    CHECK (reference_content_id IS NULL OR reference_content_id <> content_id)
);
CREATE UNIQUE INDEX user_rank_unique ON prepared_rankings(ranking_set_id, person_id, rank) WHERE person_id IS NOT NULL;
CREATE UNIQUE INDEX user_item_unique ON prepared_rankings(ranking_set_id, person_id, content_id) WHERE person_id IS NOT NULL;
CREATE UNIQUE INDEX similar_rank_unique ON prepared_rankings(ranking_set_id, reference_content_id, rank) WHERE reference_content_id IS NOT NULL;
CREATE UNIQUE INDEX similar_item_unique ON prepared_rankings(ranking_set_id, reference_content_id, content_id) WHERE reference_content_id IS NOT NULL;
CREATE INDEX article_timeline ON article_events(content_id, event_at, article_event_id);
CREATE INDEX user_history ON interactions(person_id, event_at, interaction_id);
CREATE INDEX item_history ON interactions(content_id, event_at);
CREATE INDEX metadata_lookup ON metadata_versions(content_id, share_event_id, variant_id);
CREATE INDEX quality_detail ON quality_results(metadata_version_id, quality_run_id);
CREATE INDEX warning_detail ON quality_warnings(quality_run_id, metadata_version_id);
CREATE INDEX review_history ON review_decisions(warning_id, reviewer_label, created_at DESC, review_id DESC);
-- Hash index limits full-row comparisons to a hash bucket without indexing article text.
CREATE INDEX source_row_payload_hash ON source_rows(file_sha256, md5(raw_fields::text));

CREATE FUNCTION reject_mutation() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% is append-only', TG_TABLE_NAME USING ERRCODE = '23514';
END $$;
DO $$
DECLARE name TEXT;
BEGIN
    FOREACH name IN ARRAY ARRAY['source_files', 'source_rows', 'users', 'articles',
        'article_events', 'interactions', 'metadata_variants', 'metadata_versions',
        'review_decisions', 'feature_sets', 'feature_items'] LOOP
        EXECUTE format('CREATE TRIGGER immutable_rows BEFORE UPDATE OR DELETE ON %I FOR EACH ROW EXECUTE FUNCTION reject_mutation()', name);
        EXECUTE format('CREATE TRIGGER no_truncate BEFORE TRUNCATE ON %I EXECUTE FUNCTION reject_mutation()', name);
    END LOOP;
END $$;

CREATE FUNCTION guard_source_insert() RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE category TEXT; canonical BIGINT; expected_category TEXT;
BEGIN
    SELECT event_category INTO category FROM source_files WHERE sha256 = NEW.file_sha256 FOR UPDATE;
    IF EXISTS (SELECT 1 FROM snapshots WHERE status = 'published'
        AND source_manifest->'files' ? NEW.file_sha256::text) THEN
        RAISE EXCEPTION 'Published snapshot source is sealed' USING ERRCODE = '23514';
    END IF;
    IF TG_TABLE_NAME = 'source_rows' THEN
        IF EXISTS (SELECT 1 FROM jsonb_each(NEW.raw_fields) WHERE jsonb_typeof(value) <> 'string') THEN
            RAISE EXCEPTION 'Raw fields must retain CSV strings' USING ERRCODE = '23514';
        END IF;
        SELECT min(row_number) INTO canonical FROM source_rows
            WHERE file_sha256 = NEW.file_sha256 AND md5(raw_fields::text) = md5(NEW.raw_fields::text)
            AND raw_fields = NEW.raw_fields;
        IF canonical IS NOT NULL AND (NEW.row_number <= canonical
            OR NEW.duplicate_file_sha256 IS DISTINCT FROM NEW.file_sha256
            OR NEW.duplicate_row_number IS DISTINCT FROM canonical) THEN
            RAISE EXCEPTION 'Exact copies must point to earliest canonical row; import in source order' USING ERRCODE = '23514';
        END IF;
        IF canonical IS NULL AND NEW.duplicate_row_number IS NOT NULL THEN
            RAISE EXCEPTION 'Duplicate must match an earlier full canonical row' USING ERRCODE = '23514';
        END IF;
    ELSE
        expected_category := CASE WHEN TG_TABLE_NAME = 'article_events' THEN 'article' ELSE 'interaction' END;
        IF category IS DISTINCT FROM expected_category THEN
            RAISE EXCEPTION 'Source file category differs from event table' USING ERRCODE = '23514';
        END IF;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER source_insert BEFORE INSERT ON source_rows FOR EACH ROW EXECUTE FUNCTION guard_source_insert();
CREATE TRIGGER source_insert BEFORE INSERT ON article_events FOR EACH ROW EXECUTE FUNCTION guard_source_insert();
CREATE TRIGGER source_insert BEFORE INSERT ON interactions FOR EACH ROW EXECUTE FUNCTION guard_source_insert();

CREATE FUNCTION guard_metadata() RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE kind TEXT; base metadata_versions;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM article_events WHERE article_event_id = NEW.share_event_id
        AND content_id = NEW.content_id AND event_type = 'CONTENT SHARED') THEN
        RAISE EXCEPTION 'Metadata requires a share event for the same article' USING ERRCODE = '23514';
    END IF;
    SELECT v.kind INTO kind FROM metadata_variants v WHERE variant_id = NEW.variant_id;
    IF kind = 'original' AND NEW.base_version_id IS NOT NULL THEN
        RAISE EXCEPTION 'Original metadata cannot have a base' USING ERRCODE = '23514';
    ELSIF kind = 'damage' THEN
        SELECT * INTO base FROM metadata_versions WHERE metadata_version_id = NEW.base_version_id;
        IF base.metadata_version_id IS NULL OR base.share_event_id <> NEW.share_event_id
            OR base.content_id <> NEW.content_id OR NOT EXISTS
            (SELECT 1 FROM metadata_variants WHERE variant_id = base.variant_id AND metadata_variants.kind = 'original') THEN
            RAISE EXCEPTION 'Damage base must be the same original share/version' USING ERRCODE = '23514';
        END IF;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER metadata_insert BEFORE INSERT ON metadata_versions FOR EACH ROW EXECUTE FUNCTION guard_metadata();

-- Manifest wire shape is {"files": ["<sha256>", ...]}; extra provenance keys allowed.
CREATE FUNCTION snapshot_files(manifest JSONB) RETURNS SETOF TEXT LANGUAGE sql IMMUTABLE AS $$
    SELECT jsonb_array_elements_text(manifest->'files')
$$;
CREATE FUNCTION availability_at(item TEXT, boundary TIMESTAMPTZ, manifest JSONB)
RETURNS TEXT LANGUAGE plpgsql STABLE AS $$
DECLARE latest TIMESTAMPTZ; states TEXT[];
BEGIN
    SELECT max(event_at) INTO latest FROM article_events
        WHERE content_id = item AND event_at <= boundary AND file_sha256::text IN (SELECT snapshot_files(manifest));
    IF latest IS NULL THEN RETURN 'not_shared'; END IF;
    IF latest = boundary THEN RETURN 'ambiguous'; END IF;
    SELECT array_agg(DISTINCT event_type) INTO states FROM article_events
        WHERE content_id = item AND event_at = latest AND file_sha256::text IN (SELECT snapshot_files(manifest));
    IF cardinality(states) <> 1 THEN RETURN 'ambiguous'; END IF;
    RETURN CASE WHEN states[1] = 'CONTENT SHARED' THEN 'available' ELSE 'removed' END;
END $$;
CREATE FUNCTION interaction_exclusion(event interactions, manifest JSONB) RETURNS TEXT LANGUAGE plpgsql STABLE AS $$
DECLARE first_share TIMESTAMPTZ; state TEXT;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM article_events WHERE content_id = event.content_id
        AND file_sha256::text IN (SELECT snapshot_files(manifest))) THEN RETURN 'unresolved'; END IF;
    SELECT min(event_at) INTO first_share FROM article_events WHERE content_id = event.content_id
        AND event_type = 'CONTENT SHARED' AND file_sha256::text IN (SELECT snapshot_files(manifest));
    IF first_share IS NULL THEN RETURN 'removal_only'; END IF;
    IF event.event_at < first_share THEN RETURN 'pre_share'; END IF;
    state := availability_at(event.content_id, event.event_at, manifest);
    IF state = 'ambiguous' THEN RETURN 'ambiguous'; END IF;
    IF state = 'removed' THEN RETURN 'removed'; END IF;
    RETURN NULL;
END $$;

CREATE FUNCTION guard_snapshot_member() RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE parent snapshots; v metadata_versions; e article_events; i interactions; canonical UUID;
BEGIN
    SELECT * INTO parent FROM snapshots WHERE snapshot_id = COALESCE(NEW.snapshot_id, OLD.snapshot_id) FOR UPDATE;
    IF parent.status <> 'building' THEN
        RAISE EXCEPTION 'Snapshot members are frozen outside building state' USING ERRCODE = '23514';
    END IF;
    IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
    IF TG_OP = 'UPDATE' AND NEW.snapshot_id <> OLD.snapshot_id THEN
        RAISE EXCEPTION 'Cannot move snapshot members' USING ERRCODE = '23514';
    END IF;
    IF TG_TABLE_NAME = 'snapshot_articles' THEN
      IF NEW.metadata_version_id IS NOT NULL THEN
        SELECT * INTO v FROM metadata_versions WHERE metadata_version_id = NEW.metadata_version_id;
        SELECT * INTO e FROM article_events WHERE article_event_id = v.share_event_id;
        IF v.variant_id IS DISTINCT FROM parent.metadata_variant_id OR e.event_at >= parent.as_of_at
            OR NOT (parent.source_manifest->'files' ? e.file_sha256::text) THEN
            RAISE EXCEPTION 'Snapshot metadata variant, cutoff or manifest mismatch' USING ERRCODE = '23514';
        END IF;
      END IF;
    ELSIF TG_TABLE_NAME = 'snapshot_interactions' THEN
        SELECT * INTO i FROM interactions WHERE interaction_id = NEW.interaction_id;
        IF i.event_at >= parent.as_of_at OR NOT (parent.source_manifest->'files' ? i.file_sha256::text) THEN
            RAISE EXCEPTION 'Interaction is outside snapshot inputs/cutoff' USING ERRCODE = '23514';
        END IF;
        SELECT original.interaction_id INTO canonical FROM source_rows r JOIN interactions original
            ON original.file_sha256 = r.duplicate_file_sha256 AND original.row_number = r.duplicate_row_number
            WHERE r.file_sha256 = i.file_sha256 AND r.row_number = i.row_number;
        IF NEW.duplicate_of_interaction_id IS DISTINCT FROM canonical
            OR NEW.exclusion_reason IS DISTINCT FROM interaction_exclusion(i, parent.source_manifest) THEN
            RAISE EXCEPTION 'Interaction duplicate pointer or exclusion differs from source history' USING ERRCODE = '23514';
        END IF;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER snapshot_members BEFORE INSERT OR UPDATE OR DELETE ON snapshot_articles FOR EACH ROW EXECUTE FUNCTION guard_snapshot_member();
CREATE TRIGGER snapshot_members BEFORE INSERT OR UPDATE OR DELETE ON snapshot_interactions FOR EACH ROW EXECUTE FUNCTION guard_snapshot_member();

CREATE FUNCTION guard_snapshot() RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE hash TEXT;
BEGIN
    IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'Snapshots retain history' USING ERRCODE = '23514'; END IF;
    IF TG_OP = 'INSERT' AND NEW.status <> 'building' THEN
        RAISE EXCEPTION 'Snapshots must begin building' USING ERRCODE = '23514';
    END IF;
    IF TG_OP = 'UPDATE' AND (OLD.status <> 'building' OR
        (to_jsonb(NEW) - 'status') IS DISTINCT FROM (to_jsonb(OLD) - 'status')) THEN
        RAISE EXCEPTION 'Only building snapshot status may change' USING ERRCODE = '23514';
    END IF;
    IF jsonb_typeof(NEW.source_manifest->'files') IS DISTINCT FROM 'array'
        OR jsonb_array_length(NEW.source_manifest->'files') = 0 THEN
        RAISE EXCEPTION 'Snapshot manifest must pin a nonempty files array' USING ERRCODE = '23514';
    END IF;
    FOR hash IN SELECT snapshot_files(NEW.source_manifest) ORDER BY 1 LOOP
        PERFORM 1 FROM source_files WHERE sha256::text = hash FOR UPDATE;
        IF NOT FOUND THEN RAISE EXCEPTION 'Unknown manifest file' USING ERRCODE = '23514'; END IF;
    END LOOP;
    IF NEW.status = 'published' THEN
        IF EXISTS (SELECT 1 FROM snapshot_interactions si JOIN interactions i USING (interaction_id)
            WHERE si.snapshot_id = NEW.snapshot_id AND
                si.exclusion_reason IS DISTINCT FROM interaction_exclusion(i, NEW.source_manifest)) THEN
            RAISE EXCEPTION 'Interaction exclusion changed before publication' USING ERRCODE = '23514';
        END IF;
        IF EXISTS (SELECT 1 FROM snapshot_articles a WHERE a.snapshot_id = NEW.snapshot_id AND
            a.availability <> availability_at(a.content_id, NEW.as_of_at, NEW.source_manifest)) THEN
            RAISE EXCEPTION 'Snapshot availability differs from source history' USING ERRCODE = '23514';
        END IF;
        IF EXISTS (
            SELECT content_id FROM article_events WHERE file_sha256::text IN (SELECT snapshot_files(NEW.source_manifest))
            UNION SELECT content_id FROM interactions WHERE file_sha256::text IN (SELECT snapshot_files(NEW.source_manifest))
            EXCEPT SELECT content_id FROM snapshot_articles WHERE snapshot_id = NEW.snapshot_id
        ) OR EXISTS (
            SELECT interaction_id FROM interactions WHERE event_at < NEW.as_of_at
                AND file_sha256::text IN (SELECT snapshot_files(NEW.source_manifest))
            EXCEPT SELECT interaction_id FROM snapshot_interactions WHERE snapshot_id = NEW.snapshot_id
        ) THEN
            RAISE EXCEPTION 'Snapshot memberships are incomplete' USING ERRCODE = '23514';
        END IF;
        -- Source rows cannot be sealed while their normalized events are incomplete.
        IF EXISTS (SELECT 1 FROM source_rows r JOIN source_files f ON f.sha256 = r.file_sha256
            WHERE f.sha256::text IN (SELECT snapshot_files(NEW.source_manifest)) AND
                ((f.event_category = 'article' AND NOT EXISTS (SELECT 1 FROM article_events e
                    WHERE e.file_sha256 = r.file_sha256 AND e.row_number = r.row_number)) OR
                 (f.event_category = 'interaction' AND NOT EXISTS (SELECT 1 FROM interactions i
                    WHERE i.file_sha256 = r.file_sha256 AND i.row_number = r.row_number)))) THEN
            RAISE EXCEPTION 'Source normalization is incomplete' USING ERRCODE = '23514';
        END IF;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER snapshot_state BEFORE INSERT OR UPDATE OR DELETE ON snapshots FOR EACH ROW EXECUTE FUNCTION guard_snapshot();

CREATE FUNCTION valid_quality_scores(coverage JSONB, scores DOUBLE PRECISION[], overall DOUBLE PRECISION)
RETURNS BOOLEAN LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE dimensions TEXT[] := ARRAY['completeness', 'validity', 'consistency', 'informativeness'];
    c JSONB; assessed NUMERIC; passed NUMERIC; unassessed NUMERIC; idx INTEGER;
    complete BOOLEAN := true; total DOUBLE PRECISION := 0;
BEGIN
    IF jsonb_typeof(coverage) IS DISTINCT FROM 'object' OR
        (SELECT count(*) FROM jsonb_object_keys(coverage)) <> 4 THEN RETURN false; END IF;
    FOR idx IN 1..4 LOOP
        c := coverage->dimensions[idx];
        IF jsonb_typeof(c) IS DISTINCT FROM 'object' THEN RETURN false; END IF;
        IF (SELECT count(*) FROM jsonb_object_keys(c)) <> 3 THEN RETURN false; END IF;
        IF NOT (c ?& ARRAY['assessed_checks', 'passed_checks', 'unassessed_checks']) THEN RETURN false; END IF;
        IF EXISTS (SELECT 1 FROM jsonb_each(c) WHERE jsonb_typeof(value) <> 'number'
            OR value::text !~ '^[0-9]+$') THEN RETURN false; END IF;
        assessed := (c->>'assessed_checks')::numeric;
        passed := (c->>'passed_checks')::numeric;
        unassessed := (c->>'unassessed_checks')::numeric;
        IF passed > assessed THEN RETURN false; END IF;
        IF assessed = 0 THEN
            IF scores[idx] IS NOT NULL THEN RETURN false; END IF;
            complete := false;
        ELSE
            IF scores[idx] IS NULL OR abs(scores[idx] - (passed / assessed)::float8) > 1e-12 THEN RETURN false; END IF;
            total := total + scores[idx];
        END IF;
        IF unassessed > 0 THEN complete := false; END IF;
    END LOOP;
    IF complete THEN RETURN overall IS NOT NULL AND abs(overall - total / 4) <= 1e-12; END IF;
    RETURN overall IS NULL;
END $$;
ALTER TABLE quality_results ADD CHECK (valid_quality_scores(dimension_coverage,
    ARRAY[completeness_score, validity_score, consistency_score, informativeness_score], overall_score));

CREATE FUNCTION guard_run() RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE parent snapshots; branch JSONB; dependency model_runs;
BEGIN
    IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'Run history is retained' USING ERRCODE = '23514'; END IF;
    IF TG_OP = 'INSERT' THEN
        IF NEW.status <> 'pending' THEN RAISE EXCEPTION 'Runs must begin pending' USING ERRCODE = '23514'; END IF;
    ELSE
        IF OLD.status NOT IN ('pending', 'running') OR
            NOT ((OLD.status = 'pending' AND NEW.status IN ('running', 'failed')) OR
                 (OLD.status = 'running' AND NEW.status IN ('completed', 'failed'))) THEN
            RAISE EXCEPTION 'Invalid run transition' USING ERRCODE = '23514';
        END IF;
        IF TG_TABLE_NAME = 'quality_runs' THEN
            IF (to_jsonb(NEW) - ARRAY['status', 'completed_at']) IS DISTINCT FROM
                (to_jsonb(OLD) - ARRAY['status', 'completed_at']) THEN
                RAISE EXCEPTION 'Quality run configuration is immutable' USING ERRCODE = '23514';
            END IF;
        ELSE
            IF (to_jsonb(NEW) - ARRAY['status', 'artifact_uri']) IS DISTINCT FROM
                (to_jsonb(OLD) - ARRAY['status', 'artifact_uri']) THEN
                RAISE EXCEPTION 'Model run configuration is immutable' USING ERRCODE = '23514';
            END IF;
        END IF;
    END IF;
    SELECT * INTO parent FROM snapshots WHERE snapshot_id = NEW.snapshot_id FOR SHARE;
    IF parent.status IS DISTINCT FROM 'published' THEN
        RAISE EXCEPTION 'Runs require a published snapshot' USING ERRCODE = '23514';
    END IF;
    IF TG_TABLE_NAME = 'model_runs' THEN
        IF NEW.protocol_id <> parent.protocol_id THEN
            RAISE EXCEPTION 'Model protocol differs from snapshot' USING ERRCODE = '23514';
        END IF;
        -- Serialize feature membership additions with model creation/completion.
        PERFORM 1 FROM feature_sets WHERE feature_set_id = NEW.feature_set_id FOR UPDATE;
        IF NEW.status = 'completed' THEN
            IF NEW.quality_run_id IS NOT NULL THEN
                IF NOT EXISTS (SELECT 1 FROM quality_runs WHERE quality_run_id = NEW.quality_run_id AND status = 'completed')
                    OR NEW.feature_set_id IS NULL OR EXISTS (
                        SELECT 1 FROM feature_items f WHERE f.feature_set_id = NEW.feature_set_id
                        AND NOT EXISTS (SELECT 1 FROM quality_results q WHERE q.quality_run_id = NEW.quality_run_id
                            AND q.metadata_version_id = f.metadata_version_id AND q.snapshot_id = f.snapshot_id)) THEN
                    RAISE EXCEPTION 'Quality-dependent features need completed matching quality results' USING ERRCODE = '23514';
                END IF;
            END IF;
            IF NEW.model_type IN ('fixed_hybrid', 'quality_hybrid') THEN
                IF jsonb_typeof(NEW.manifest->'branches') IS DISTINCT FROM 'array' THEN
                    RAISE EXCEPTION 'Hybrid manifest requires branches array' USING ERRCODE = '23514';
                END IF;
                IF jsonb_array_length(NEW.manifest->'branches') = 0 AND
                    coalesce(NEW.manifest->>'fallback_reason', '') = '' THEN
                    RAISE EXCEPTION 'Empty hybrid branches require recorded fallback reason' USING ERRCODE = '23514';
                END IF;
                FOR branch IN SELECT value FROM jsonb_array_elements(NEW.manifest->'branches') LOOP
                    SELECT * INTO dependency FROM model_runs WHERE model_run_id = (branch->>'model_run_id')::uuid;
                    IF NOT (branch ?& ARRAY['model_run_id', 'feature_set_id'])
                        OR dependency.model_run_id IS NULL OR dependency.model_run_id = NEW.model_run_id
                        OR dependency.status <> 'completed' OR dependency.snapshot_id <> NEW.snapshot_id
                        OR dependency.protocol_id <> NEW.protocol_id
                        OR branch->>'feature_set_id' IS DISTINCT FROM dependency.feature_set_id::text THEN
                        RAISE EXCEPTION 'Hybrid branch run/feature/snapshot/protocol mismatch' USING ERRCODE = '23514';
                    END IF;
                END LOOP;
            END IF;
        END IF;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER run_state BEFORE INSERT OR UPDATE OR DELETE ON quality_runs FOR EACH ROW EXECUTE FUNCTION guard_run();
CREATE TRIGGER run_state BEFORE INSERT OR UPDATE OR DELETE ON model_runs FOR EACH ROW EXECUTE FUNCTION guard_run();

CREATE FUNCTION guard_quality_output() RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE parent quality_runs;
BEGIN
    SELECT * INTO parent FROM quality_runs WHERE quality_run_id = COALESCE(OLD.quality_run_id, NEW.quality_run_id) FOR UPDATE;
    IF parent.status IS DISTINCT FROM 'running' THEN
        RAISE EXCEPTION 'Quality output may change only during its running run' USING ERRCODE = '23514';
    END IF;
    IF TG_OP = 'UPDATE' AND (NEW.quality_run_id <> OLD.quality_run_id
        OR NEW.metadata_version_id <> OLD.metadata_version_id) THEN
        RAISE EXCEPTION 'Cannot move quality outputs between runs/versions' USING ERRCODE = '23514';
    END IF;
    IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER quality_output BEFORE INSERT OR UPDATE OR DELETE ON quality_results FOR EACH ROW EXECUTE FUNCTION guard_quality_output();
CREATE TRIGGER quality_output BEFORE INSERT OR UPDATE OR DELETE ON quality_warnings FOR EACH ROW EXECUTE FUNCTION guard_quality_output();
CREATE FUNCTION guard_review() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM quality_runs WHERE quality_run_id = NEW.quality_run_id
        AND snapshot_id = NEW.snapshot_id AND status = 'completed') THEN
        RAISE EXCEPTION 'Reviews require completed quality for the exact snapshot' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER review_insert BEFORE INSERT ON review_decisions FOR EACH ROW EXECUTE FUNCTION guard_review();

CREATE FUNCTION guard_feature() RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE parent snapshots;
BEGIN
    SELECT * INTO parent FROM snapshots WHERE snapshot_id = NEW.snapshot_id FOR SHARE;
    IF parent.status IS DISTINCT FROM 'published' THEN
        RAISE EXCEPTION 'Features require published snapshot' USING ERRCODE = '23514';
    END IF;
    IF TG_TABLE_NAME = 'feature_sets' THEN
        IF NEW.fitted_before_at > parent.as_of_at THEN
            RAISE EXCEPTION 'Feature fitting cannot include future data' USING ERRCODE = '23514';
        END IF;
    ELSE
        PERFORM 1 FROM feature_sets WHERE feature_set_id = NEW.feature_set_id FOR UPDATE;
        IF EXISTS (SELECT 1 FROM model_runs WHERE feature_set_id = NEW.feature_set_id) THEN
            RAISE EXCEPTION 'Feature membership is frozen once a model references it' USING ERRCODE = '23514';
        END IF;
        IF NOT EXISTS (SELECT 1 FROM snapshot_articles WHERE snapshot_id = NEW.snapshot_id
            AND content_id = NEW.content_id AND metadata_version_id = NEW.metadata_version_id AND availability = 'available') THEN
            RAISE EXCEPTION 'Features need available matching snapshot metadata' USING ERRCODE = '23514';
        END IF;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER feature_insert BEFORE INSERT ON feature_sets FOR EACH ROW EXECUTE FUNCTION guard_feature();
CREATE TRIGGER feature_insert BEFORE INSERT ON feature_items FOR EACH ROW EXECUTE FUNCTION guard_feature();

CREATE FUNCTION guard_ranking_entry() RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE parent ranking_sets;
BEGIN
    SELECT * INTO parent FROM ranking_sets WHERE ranking_set_id = COALESCE(OLD.ranking_set_id, NEW.ranking_set_id) FOR UPDATE;
    IF parent.status IS DISTINCT FROM 'building' THEN
        RAISE EXCEPTION 'Ranking entries frozen outside building state' USING ERRCODE = '23514';
    END IF;
    IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
    IF TG_OP = 'UPDATE' AND NEW.ranking_set_id <> OLD.ranking_set_id THEN
        RAISE EXCEPTION 'Cannot move ranking entries' USING ERRCODE = '23514';
    END IF;
    IF (parent.kind = 'user') <> (NEW.person_id IS NOT NULL) THEN
        RAISE EXCEPTION 'Ranking subject differs from set kind' USING ERRCODE = '23514';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM snapshot_articles WHERE snapshot_id = NEW.snapshot_id
        AND content_id = NEW.content_id AND metadata_version_id = NEW.metadata_version_id AND availability = 'available') THEN
        RAISE EXCEPTION 'Ranking requires available matching snapshot version' USING ERRCODE = '23514';
    END IF;
    IF NEW.reference_content_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM snapshot_articles
        WHERE snapshot_id = NEW.snapshot_id AND content_id = NEW.reference_content_id AND availability = 'available') THEN
        RAISE EXCEPTION 'Similar reference must be available in the same snapshot' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER ranking_entry BEFORE INSERT OR UPDATE OR DELETE ON prepared_rankings FOR EACH ROW EXECUTE FUNCTION guard_ranking_entry();
CREATE FUNCTION guard_ranking_set() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN RAISE EXCEPTION 'Ranking sets retain history' USING ERRCODE = '23514'; END IF;
    IF TG_OP = 'INSERT' AND NEW.status <> 'building' THEN
        RAISE EXCEPTION 'Ranking sets must begin building' USING ERRCODE = '23514';
    END IF;
    IF TG_OP = 'UPDATE' AND (OLD.status <> 'building' OR
        (to_jsonb(NEW) - 'status') IS DISTINCT FROM (to_jsonb(OLD) - 'status')) THEN
        RAISE EXCEPTION 'Only building ranking status may change' USING ERRCODE = '23514';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM model_runs WHERE model_run_id = NEW.model_run_id AND status = 'completed') THEN
        RAISE EXCEPTION 'Ranking sets require completed models' USING ERRCODE = '23514';
    END IF;
    IF NEW.status = 'published' AND EXISTS (
        SELECT 1 FROM (
            SELECT rank, row_number() OVER (
                PARTITION BY person_id, reference_content_id
                ORDER BY score DESC, content_id COLLATE "C" ASC) expected
            FROM prepared_rankings WHERE ranking_set_id = NEW.ranking_set_id
        ) entries WHERE rank <> expected
    ) THEN
        RAISE EXCEPTION 'Ranks must be contiguous and ordered by score then exact content ID' USING ERRCODE = '23514';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER ranking_state BEFORE INSERT OR UPDATE OR DELETE ON ranking_sets FOR EACH ROW EXECUTE FUNCTION guard_ranking_set();
DO $$
DECLARE name TEXT;
BEGIN
    FOREACH name IN ARRAY ARRAY['snapshots', 'snapshot_articles', 'snapshot_interactions',
        'quality_runs', 'quality_results', 'quality_warnings', 'model_runs', 'ranking_sets', 'prepared_rankings'] LOOP
        EXECUTE format('CREATE TRIGGER no_truncate BEFORE TRUNCATE ON %I EXECUTE FUNCTION reject_mutation()', name);
    END LOOP;
END $$;
