-- plugin_code tables (Appendix B). Owner plugin_code_owner; the plugin_code login gets DML only.
CREATE TABLE plugin_code.raw_facts (
    job_id    uuid NOT NULL,
    file_key  text NOT NULL,
    ordinal   int  NOT NULL CHECK (ordinal >= 0),
    kind      text NOT NULL CHECK (kind IN ('dep', 'call')),
    fact      jsonb NOT NULL,
    resolution jsonb,                                     -- resolve output: target_key, external, status, method, reason, resolver_version
    PRIMARY KEY (job_id, file_key, kind, ordinal)
);
CREATE TABLE plugin_code.graph_quality (
    job_id         uuid NOT NULL,
    collection_id  uuid NOT NULL,
    source_id      uuid,
    kind           text NOT NULL CHECK (kind IN ('dependency', 'call')),
    language       text NOT NULL,
    raw_total      int NOT NULL CHECK (raw_total >= 0),
    unsupported    int NOT NULL CHECK (unsupported >= 0),
    inherent       int NOT NULL CHECK (inherent >= 0),
    upstream       int NOT NULL CHECK (upstream >= 0),
    resolver_miss  int NOT NULL CHECK (resolver_miss >= 0),
    resolved       int NOT NULL CHECK (resolved >= 0),
    external       int NOT NULL CHECK (external >= 0),
    evaluated      int GENERATED ALWAYS AS (raw_total - unsupported) STORED,
    attemptable    int GENERATED ALWAYS AS (raw_total - unsupported - inherent - upstream) STORED,
    resolver_version text NOT NULL,
    PRIMARY KEY (job_id, kind, language),
    CHECK (raw_total - unsupported - inherent - upstream >= 0),
    CHECK (raw_total - unsupported - inherent - upstream = resolved + external + resolver_miss)
);
CREATE INDEX ix_gq_baseline ON plugin_code.graph_quality (collection_id, source_id, kind, language);
CREATE TABLE plugin_code.parse_cache (
    tenant_id       uuid NOT NULL,
    content_sha256  text NOT NULL,
    parser_version  text NOT NULL,
    result          jsonb NOT NULL,
    last_used_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, content_sha256, parser_version)
);
CREATE INDEX ix_parse_cache_used ON plugin_code.parse_cache (last_used_at);
