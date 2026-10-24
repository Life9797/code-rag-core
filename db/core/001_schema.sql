-- v0.4 core schema: P7 Appendix A (core, runtime, knowledge). Applied by init as core_owner.
ALTER DEFAULT PRIVILEGES REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;
CREATE SCHEMA core;
CREATE SCHEMA runtime;
CREATE SCHEMA knowledge;

-- ===================== core: identity =====================
CREATE TABLE core.tenants (
    id          uuid PRIMARY KEY DEFAULT uuidv7(),
    name        text NOT NULL UNIQUE,
    status      text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'disabled')),
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE core.principals (
    id            uuid PRIMARY KEY DEFAULT uuidv7(),
    kind          text NOT NULL CHECK (kind IN ('human', 'service')),
    display_name  text NOT NULL,
    status        text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'disabled')),
    created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE core.external_identities (
    issuer        text NOT NULL,
    subject       text NOT NULL,
    principal_id  uuid NOT NULL REFERENCES core.principals,
    PRIMARY KEY (issuer, subject)
);

CREATE TABLE core.tenant_members (
    tenant_id     uuid NOT NULL REFERENCES core.tenants,
    principal_id  uuid NOT NULL REFERENCES core.principals,
    role          smallint NOT NULL CHECK (role BETWEEN 1 AND 4),   -- 1 reader 2 contributor 3 operator 4 admin
    PRIMARY KEY (tenant_id, principal_id)
);

CREATE TABLE core.projects (
    tenant_id    uuid NOT NULL REFERENCES core.tenants,
    id           uuid NOT NULL DEFAULT uuidv7() UNIQUE,
    name         text NOT NULL,
    state        text NOT NULL DEFAULT 'active' CHECK (state IN ('active', 'deleting', 'purging')),
    delete_after timestamptz,                            -- end of grace window (G2)
    created_by   uuid NOT NULL REFERENCES core.principals,
    created_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, id),
    CHECK ((state = 'active') = (delete_after IS NULL))
);
CREATE UNIQUE INDEX uq_projects_live_name ON core.projects (tenant_id, name) WHERE state = 'active';

CREATE TABLE core.project_members (
    tenant_id     uuid NOT NULL,
    project_id    uuid NOT NULL,
    principal_id  uuid NOT NULL,
    role          smallint NOT NULL CHECK (role BETWEEN 1 AND 4),
    PRIMARY KEY (tenant_id, project_id, principal_id),
    FOREIGN KEY (tenant_id, project_id) REFERENCES core.projects (tenant_id, id),
    FOREIGN KEY (tenant_id, principal_id) REFERENCES core.tenant_members (tenant_id, principal_id) ON DELETE CASCADE
);
CREATE INDEX ix_project_members_principal ON core.project_members (principal_id);

CREATE TABLE core.service_clients (
    id            uuid PRIMARY KEY DEFAULT uuidv7(),
    principal_id  uuid NOT NULL REFERENCES core.principals,
    secret_hash   text NOT NULL,                         -- argon2id, verified in the api process
    audiences     text[] NOT NULL,
    scopes        text[] NOT NULL,
    expires_at    timestamptz,
    status        text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'revoked'))
);

CREATE TABLE core.signing_keys (
    kid          text PRIMARY KEY,
    alg          text NOT NULL CHECK (alg = 'ES256'),
    public_jwk   jsonb NOT NULL,
    private_ref  text NOT NULL,                          -- host key path reference, never the key
    status       text NOT NULL CHECK (status IN ('active', 'retiring', 'retired')),
    created_at   timestamptz NOT NULL DEFAULT now(),
    retired_at   timestamptz
);
CREATE UNIQUE INDEX uq_signing_one_active ON core.signing_keys ((true)) WHERE status = 'active';

-- ===================== core: plugins and registry =====================
CREATE TABLE core.plugins (
    id             text NOT NULL CHECK (id ~ '^[a-z][a-z0-9_]{1,29}$'),
    version        text NOT NULL,
    contract       int  NOT NULL CHECK (contract = 1),
    manifest       jsonb NOT NULL,
    manifest_hash  text NOT NULL,
    dir_hash       text NOT NULL,                        -- sha256 of the plugin folder at registration (R13)
    status         text NOT NULL DEFAULT 'registered' CHECK (status IN ('registered', 'refused', 'disabled')),
    registered_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (id, version)                            -- same version + different manifest_hash -> PLUGIN_VERSION_CONFLICT (R4)
);

-- One row per registered name across versions: the single source for error codes and other keys (R9).
CREATE TABLE core.registry_keys (
    item_type   text NOT NULL CHECK (item_type IN ('handler', 'collection', 'unit_kind', 'representation_kind',
                    'policy_key', 'resource', 'permission', 'schedule', 'format', 'error_code', 'event',
                    'exclusion_reason', 'connector')),
    key         text NOT NULL,                           -- namespaced: core.<key> or <plugin>.<key>
    owner       text NOT NULL,                           -- 'core' or plugin id
    spec        jsonb NOT NULL,                          -- current spec; error_code: {layer, retryable, health_sample}
    PRIMARY KEY (item_type, key)
);

CREATE TABLE core.registry_items (                       -- which plugin version declared which key
    plugin_id       text NOT NULL,
    plugin_version  text NOT NULL,
    item_type       text NOT NULL,
    key             text NOT NULL,
    spec            jsonb NOT NULL,
    PRIMARY KEY (plugin_id, plugin_version, item_type, key),
    FOREIGN KEY (plugin_id, plugin_version) REFERENCES core.plugins (id, version),
    FOREIGN KEY (item_type, key) REFERENCES core.registry_keys (item_type, key)
);

CREATE TABLE core.plugin_migrations (                    -- §28: immutable, applied by init in order
    plugin_id   text NOT NULL,                           -- 'core' for Core schemas
    number      int  NOT NULL,
    sha256      text NOT NULL,
    applied_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (plugin_id, number)
);

CREATE TABLE core.schema_version (                       -- written last by init (D-07)
    component   text PRIMARY KEY,
    version     text NOT NULL,
    stamped_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE core.project_plugins (                      -- plugin activation per project
    tenant_id   uuid NOT NULL,
    project_id  uuid NOT NULL,
    plugin_id   text NOT NULL,
    enabled     boolean NOT NULL DEFAULT true,
    PRIMARY KEY (tenant_id, project_id, plugin_id),
    FOREIGN KEY (tenant_id, project_id) REFERENCES core.projects (tenant_id, id)
);

CREATE TABLE core.policies (
    scope       text NOT NULL CHECK (scope IN ('global', 'tenant', 'project', 'plugin', 'stage')),
    scope_ref   text NOT NULL DEFAULT '',                -- tenant id, project id, plugin id or pipeline.stage
    key         text NOT NULL,
    value       jsonb,                                   -- NULL = UNSET
    set_by      uuid REFERENCES core.principals,
    set_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (scope, scope_ref, key)
);

-- ===================== core: storage, sources =====================
CREATE TABLE core.volumes (
    name        text PRIMARY KEY CHECK (name ~ '^[a-z0-9_-]{1,32}$'),
    mount_path  text NOT NULL UNIQUE CHECK (mount_path ~ '^/data/vol/[a-z0-9_-]+$'),
    status      text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'readonly', 'retired'))
);

CREATE TABLE core.secrets (                              -- connector credentials and webhook HMAC keys (P-P12, G1)
    tenant_id   uuid NOT NULL REFERENCES core.tenants,
    owner       text NOT NULL,                           -- <plugin>.<connector> or core.webhook
    name        text NOT NULL,
    version     int  NOT NULL,
    key_id      text NOT NULL,                           -- host key used to encrypt
    ciphertext  bytea NOT NULL,
    status      text NOT NULL CHECK (status IN ('active', 'retiring', 'retired')),
    PRIMARY KEY (tenant_id, owner, name, version)
);

CREATE TABLE core.sources (
    tenant_id    uuid NOT NULL,
    project_id   uuid NOT NULL,
    id           uuid NOT NULL DEFAULT uuidv7() UNIQUE,
    connector    text NOT NULL,                          -- 'core.upload' or <plugin>.<connector>
    external_id  text NOT NULL,
    refetchable  boolean NOT NULL,                       -- git by SHA yes, upload no (G4)
    PRIMARY KEY (tenant_id, project_id, id),
    UNIQUE (project_id, connector, external_id),
    FOREIGN KEY (tenant_id, project_id) REFERENCES core.projects (tenant_id, id)
);

CREATE TABLE core.source_revisions (
    tenant_id          uuid NOT NULL,
    project_id         uuid NOT NULL,
    id                 uuid NOT NULL DEFAULT uuidv7() UNIQUE,
    source_id          uuid NOT NULL,
    revision_key       text NOT NULL,                    -- immutable: commit SHA, upload content sha256
    manifest_hash      text NOT NULL,
    manifest           jsonb NOT NULL,                   -- kept after prune
    storage_ref        text NOT NULL CHECK (storage_ref ~ '^(vol|ext)://'),
    storage_state      text NOT NULL DEFAULT 'present' CHECK (storage_state IN ('writing', 'present', 'pruned')),
    input_complete     boolean NOT NULL,                 -- every manifest path present (R3)
    created_at         timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, project_id, id),
    UNIQUE (source_id, revision_key),
    FOREIGN KEY (tenant_id, project_id, source_id) REFERENCES core.sources (tenant_id, project_id, id)
);
CREATE INDEX ix_revisions_latest ON core.source_revisions (source_id, created_at DESC);

CREATE TABLE core.source_bindings (
    tenant_id     uuid NOT NULL,
    project_id    uuid NOT NULL,
    id            uuid PRIMARY KEY DEFAULT uuidv7(),
    connector     text NOT NULL,
    external_ref  text NOT NULL,
    config        jsonb NOT NULL DEFAULT '{}',
    UNIQUE (connector, external_ref, project_id),
    FOREIGN KEY (tenant_id, project_id) REFERENCES core.projects (tenant_id, id)
);

CREATE TABLE core.webhook_inbox (                        -- inbound, renamed from webhook_deliveries (G1)
    plugin_id     text NOT NULL,
    delivery_id   text NOT NULL,
    received_at   timestamptz NOT NULL DEFAULT now(),
    state         text NOT NULL CHECK (state IN ('received', 'enqueued', 'rejected')),
    PRIMARY KEY (plugin_id, delivery_id)
);

CREATE TABLE core.schedules (
    id           uuid PRIMARY KEY DEFAULT uuidv7(),
    plugin_id    text NOT NULL,
    key          text NOT NULL,
    tenant_id    uuid,
    project_id   uuid,                                   -- NULL = plugin-wide
    next_run_at  timestamptz NOT NULL,
    enabled      boolean NOT NULL DEFAULT true,
    UNIQUE NULLS NOT DISTINCT (plugin_id, key, project_id),
    FOREIGN KEY (tenant_id, project_id) REFERENCES core.projects (tenant_id, id),
    CHECK ((tenant_id IS NULL) = (project_id IS NULL))
);
CREATE INDEX ix_schedules_due ON core.schedules (next_run_at) WHERE enabled;

CREATE TABLE core.artifacts (
    tenant_id    uuid NOT NULL,
    project_id   uuid NOT NULL,
    id           uuid NOT NULL DEFAULT uuidv7() UNIQUE,
    kind         text NOT NULL,                          -- registered: <plugin>.<kind>
    storage_ref  text NOT NULL CHECK (storage_ref ~ '^vol://'),
    sha256       text NOT NULL,
    provenance   jsonb NOT NULL DEFAULT '{}',
    created_by   uuid NOT NULL REFERENCES core.principals,
    created_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, project_id, id),
    FOREIGN KEY (tenant_id, project_id) REFERENCES core.projects (tenant_id, id)
);

-- ===================== core: events, audit, alerts =====================
CREATE TABLE core.outbox_events (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id   uuid,
    project_id  uuid,                                    -- G1: project-scoped subscriptions
    type        text NOT NULL,
    payload     jsonb NOT NULL,                          -- ids only, never content
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE core.event_consumers (                      -- internal consumers (runtime stop flag, maintenance)
    name        text PRIMARY KEY,
    last_id     bigint NOT NULL DEFAULT 0                -- cursor into outbox_events
);

CREATE TABLE core.webhook_subscriptions (                -- outbound F10 (G1)
    tenant_id    uuid NOT NULL,
    project_id   uuid NOT NULL,
    id           uuid PRIMARY KEY DEFAULT uuidv7(),
    url          text NOT NULL CHECK (url ~ '^https://'),
    event_types  text[] NOT NULL,
    secret_name  text NOT NULL,                          -- core.secrets (owner core.webhook)
    created_by   uuid NOT NULL REFERENCES core.principals,
    status       text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'disabled')),
    FOREIGN KEY (tenant_id, project_id) REFERENCES core.projects (tenant_id, id)
);

CREATE TABLE core.outbox_deliveries (
    event_id         bigint NOT NULL REFERENCES core.outbox_events,
    subscription_id  uuid NOT NULL REFERENCES core.webhook_subscriptions,
    state            text NOT NULL CHECK (state IN ('pending', 'delivered', 'dead')),
    attempts         int  NOT NULL DEFAULT 0,
    next_at          timestamptz NOT NULL DEFAULT now(),
    last_status      int,
    PRIMARY KEY (event_id, subscription_id)
);
CREATE INDEX ix_deliveries_due ON core.outbox_deliveries (next_at) WHERE state = 'pending';

CREATE TABLE core.requests (                             -- replay store for user operations (§26)
    principal_id  uuid NOT NULL REFERENCES core.principals,
    request_id    uuid NOT NULL,
    op            text NOT NULL,
    args_hash     text NOT NULL,                         -- same id + different args -> REQUEST_ID_REUSED
    result        jsonb NOT NULL,
    at            timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (principal_id, request_id)
);

CREATE TABLE core.audit (
    id             bigint GENERATED ALWAYS AS IDENTITY,
    at             timestamptz NOT NULL DEFAULT now(),
    actor          uuid,                                 -- sub
    acting_client  uuid,                                 -- act
    action         text NOT NULL,
    object         text NOT NULL,
    detail         jsonb NOT NULL DEFAULT '{}',
    PRIMARY KEY (id, at)
) PARTITION BY RANGE (at);                               -- monthly partitions; retention = DROP PARTITION by core_owner only
CREATE FUNCTION core.audit_immutable() RETURNS trigger LANGUAGE plpgsql AS
$$BEGIN RAISE EXCEPTION 'core.audit is append-only'; END$$;
CREATE TRIGGER trg_audit_immutable BEFORE UPDATE OR DELETE ON core.audit
    FOR EACH ROW EXECUTE FUNCTION core.audit_immutable();
CREATE TRIGGER trg_audit_no_truncate BEFORE TRUNCATE ON core.audit
    FOR EACH STATEMENT EXECUTE FUNCTION core.audit_immutable();
CREATE TABLE core.audit_default PARTITION OF core.audit DEFAULT;    -- maint creates monthly partitions ahead

CREATE TABLE core.alerts (                               -- §28
    code      text NOT NULL,
    object    text NOT NULL,
    severity  text NOT NULL CHECK (severity IN ('info', 'warn', 'error')),
    first_at  timestamptz NOT NULL DEFAULT now(),
    last_at   timestamptz NOT NULL DEFAULT now(),
    count     int NOT NULL DEFAULT 1,
    cleared_at timestamptz,
    PRIMARY KEY (code, object, first_at)
);
CREATE UNIQUE INDEX uq_alerts_open ON core.alerts (code, object) WHERE cleared_at IS NULL;

-- ===================== runtime =====================
CREATE SEQUENCE runtime.job_seq;

CREATE TABLE runtime.pipelines (
    plugin_id       text NOT NULL,
    plugin_version  text NOT NULL,
    key             text NOT NULL,
    version         int  NOT NULL,
    pipeline_hash   text NOT NULL,
    collection_key  text NOT NULL,
    publish_mode    text NOT NULL CHECK (publish_mode IN ('full', 'delta')),
    inputs          text[] NOT NULL DEFAULT '{}',
    unit_kinds      text[] NOT NULL DEFAULT '{}',        -- full-mode end scope (R7)
    PRIMARY KEY (plugin_id, key, version),
    FOREIGN KEY (plugin_id, plugin_version) REFERENCES core.plugins (id, version)
);

CREATE TABLE runtime.pipeline_stages (
    plugin_id        text NOT NULL,
    pipeline_key     text NOT NULL,
    version          int  NOT NULL,
    stage_key        text NOT NULL,
    kind             text NOT NULL CHECK (kind IN ('control', 'item', 'approval', 'publish')),  -- approval/publish: Core-only
    handler          text,
    depends_on       text[] NOT NULL DEFAULT '{}',
    resource_key     text,
    split_max_items  int CHECK (split_max_items > 0),
    split_max_depth  int CHECK (split_max_depth >= 0),
    PRIMARY KEY (plugin_id, pipeline_key, version, stage_key),
    FOREIGN KEY (plugin_id, pipeline_key, version) REFERENCES runtime.pipelines,
    CHECK ((kind IN ('approval', 'publish')) = (handler IS NULL))
);

CREATE TABLE runtime.jobs (
    tenant_id           uuid NOT NULL,
    project_id          uuid NOT NULL,
    id                  uuid NOT NULL DEFAULT uuidv7() UNIQUE,
    collection_id       uuid NOT NULL,
    source_id           uuid,                            -- NULL for sourceless units (chat) and core.delete batches without source
    source_revision_id  uuid,
    plugin_id           text NOT NULL,                   -- 'core' for core.delete / core.project_purge
    plugin_version      text NOT NULL,
    pipeline_key        text NOT NULL,
    pipeline_version    int  NOT NULL,
    pipeline_hash       text NOT NULL,
    policy_snapshot     jsonb NOT NULL,                  -- R5: every key the pipeline reads, frozen at create_job
    fingerprint         text NOT NULL,                   -- G5
    force               boolean NOT NULL DEFAULT false,  -- G7 retry bypasses NO_CHANGE
    retry_of            uuid,
    request_id          uuid NOT NULL,                   -- idempotent create_job / delete_unit
    fence_seq           bigint NOT NULL,                 -- ordering value used by fences; = seq, or the original job's fence_seq for a retry
    seq                 bigint NOT NULL DEFAULT nextval('runtime.job_seq'),  -- global, no collection lock needed (§26)
    status              text NOT NULL CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'canceled', 'superseded')),
    stop_requested      text CHECK (stop_requested IN ('cancel', 'failure', 'superseded', 'purge')),
    outcome             text,                            -- PUBLISHED, NO_CHANGE, REVISION_STALE, SNAPSHOT_EXPIRED, ...
    created_by          uuid NOT NULL REFERENCES core.principals,
    created_at          timestamptz NOT NULL DEFAULT now(),
    finished_at         timestamptz,
    PRIMARY KEY (tenant_id, project_id, id),
    UNIQUE (collection_id, seq),
    UNIQUE (created_by, request_id),
    UNIQUE (id, plugin_id, pipeline_key, pipeline_version),
    FOREIGN KEY (tenant_id, project_id) REFERENCES core.projects (tenant_id, id),
    FOREIGN KEY (tenant_id, project_id, source_id) REFERENCES core.sources (tenant_id, project_id, id),
    FOREIGN KEY (tenant_id, project_id, source_revision_id) REFERENCES core.source_revisions (tenant_id, project_id, id),
    FOREIGN KEY (retry_of) REFERENCES runtime.jobs (id),
    CHECK ((status IN ('succeeded', 'failed', 'canceled', 'superseded')) = (finished_at IS NOT NULL)),
    CHECK (source_revision_id IS NULL OR source_id IS NOT NULL)
);
CREATE INDEX ix_jobs_scope_open ON runtime.jobs (collection_id, source_id, pipeline_key) WHERE finished_at IS NULL;

CREATE TABLE runtime.job_stages (
    job_id       uuid NOT NULL,
    plugin_id    text NOT NULL,
    pipeline_key text NOT NULL,
    pipeline_version int NOT NULL,
    stage_key    text NOT NULL,
    status       text NOT NULL CHECK (status IN ('blocked', 'ready', 'running', 'waiting_approval', 'succeeded', 'failed', 'canceled')),
    approved_by  uuid REFERENCES core.principals,
    approved_hash text,                                  -- R15: artifact sha256 the approval covers
    wait_until   timestamptz,                            -- approval expiry (runtime.approval_ttl, O-17)
    PRIMARY KEY (job_id, stage_key),
    FOREIGN KEY (job_id, plugin_id, pipeline_key, pipeline_version) REFERENCES runtime.jobs (id, plugin_id, pipeline_key, pipeline_version),
    FOREIGN KEY (plugin_id, pipeline_key, pipeline_version, stage_key) REFERENCES runtime.pipeline_stages
);

CREATE TABLE runtime.tasks (
    id            uuid PRIMARY KEY DEFAULT uuidv7(),
    job_id        uuid NOT NULL,
    stage_key     text NOT NULL,
    plugin_id     text NOT NULL,                         -- claim filter: only the caller login's plugin (R8)
    tenant_id     uuid NOT NULL,                         -- fairness (R11)
    project_id    uuid NOT NULL,
    parent_id     uuid REFERENCES runtime.tasks,
    child_key     text,
    depth         int  NOT NULL DEFAULT 0,
    scope         jsonb NOT NULL DEFAULT '{}',
    payload       jsonb NOT NULL DEFAULT '{}',
    status        text NOT NULL CHECK (status IN ('ready', 'running', 'retry_wait', 'succeeded', 'split', 'failed', 'canceled')),
    resolution    text CHECK (resolution IN ('ok', 'failed', 'canceled')),
    attempt       int  NOT NULL DEFAULT 0,               -- mutable counter; task_events is the append-only record (R10)
    lease_epoch   bigint NOT NULL DEFAULT 0,
    lease_token   uuid,
    lease_until   timestamptz,
    ready_at      timestamptz NOT NULL DEFAULT now(),
    deadline_at   timestamptz,
    error_type    text NOT NULL DEFAULT 'error_code' CHECK (error_type = 'error_code'),
    error_code    text,
    FOREIGN KEY (job_id, stage_key) REFERENCES runtime.job_stages,
    FOREIGN KEY (tenant_id, project_id, job_id) REFERENCES runtime.jobs (tenant_id, project_id, id),
    FOREIGN KEY (error_type, error_code) REFERENCES core.registry_keys (item_type, key),
    UNIQUE (parent_id, child_key),
    CHECK ((parent_id IS NULL) = (child_key IS NULL)),
    CHECK ((status = 'running') = (lease_token IS NOT NULL)),
    CHECK ((status = 'failed') = (error_code IS NOT NULL))
);
CREATE INDEX ix_tasks_claim ON runtime.tasks (plugin_id, ready_at) WHERE status = 'ready';
CREATE INDEX ix_tasks_lease ON runtime.tasks (lease_until) WHERE status = 'running';
CREATE INDEX ix_tasks_stage ON runtime.tasks (job_id, stage_key, status);

CREATE TABLE runtime.task_events (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    task_id       uuid NOT NULL REFERENCES runtime.tasks,
    request_id    uuid,
    op            text NOT NULL CHECK (op IN ('claim', 'heartbeat', 'complete', 'fail', 'split', 'expire', 'retry', 'cancel', 'terminal_fail')),
    lease_epoch   bigint NOT NULL,
    outcome       text NOT NULL,
    resource_key  text,                                  -- breaker window source (R10)
    error_type    text NOT NULL DEFAULT 'error_code' CHECK (error_type = 'error_code'),
    error_code    text,
    detail        jsonb NOT NULL DEFAULT '{}',
    at            timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (error_type, error_code) REFERENCES core.registry_keys (item_type, key)
);
CREATE UNIQUE INDEX uq_task_events_request ON runtime.task_events (task_id, request_id) WHERE request_id IS NOT NULL;
CREATE INDEX ix_task_events_task ON runtime.task_events (task_id);
CREATE INDEX ix_task_events_resource ON runtime.task_events (resource_key, at) WHERE resource_key IS NOT NULL;

CREATE TABLE runtime.job_items (                         -- G3 per-file report, written via put_job_item
    job_id   uuid NOT NULL REFERENCES runtime.jobs (id),
    key      text NOT NULL,                              -- e.g. file path
    status   text NOT NULL CHECK (status IN ('ok', 'excluded', 'failed', 'held', 'unchanged')),
    code     text,                                       -- error code or exclusion reason
    detail   jsonb NOT NULL DEFAULT '{}',
    PRIMARY KEY (job_id, key)
);

CREATE TABLE runtime.lifecycle_events (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    job_id       uuid NOT NULL REFERENCES runtime.jobs (id),
    stage_key    text,
    from_status  text,
    to_status    text NOT NULL,
    detail       jsonb NOT NULL DEFAULT '{}',
    at           timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_lifecycle_job ON runtime.lifecycle_events (job_id);

CREATE TABLE runtime.resource_state (
    resource_key  text PRIMARY KEY,                      -- <plugin>.<resource> or core.embedder / core.llm / core.gpu
    state         text NOT NULL CHECK (state IN ('closed', 'open', 'half_open')),
    opened_at     timestamptz
);

CREATE TABLE runtime.schedule_runs (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    schedule_id  uuid NOT NULL REFERENCES core.schedules,
    due_at       timestamptz NOT NULL,
    job_id       uuid REFERENCES runtime.jobs (id),
    status       text NOT NULL CHECK (status IN ('due', 'started', 'done', 'failed')),
    UNIQUE (schedule_id, due_at)
);

-- ===================== knowledge =====================
CREATE TABLE knowledge.collections (
    tenant_id      uuid NOT NULL,
    project_id     uuid NOT NULL,
    id             uuid NOT NULL DEFAULT uuidv7() UNIQUE,
    plugin_id      text NOT NULL,
    key            text NOT NULL,
    current_gen    bigint NOT NULL DEFAULT 0,            -- 0 = nothing published
    active_config  jsonb NOT NULL DEFAULT '{}',          -- {rep_kind: config_hash} searched; switched at publish
    fence_floor    bigint NOT NULL DEFAULT 0,            -- fences below this were GC'd; retries with fence_seq < floor refused
    PRIMARY KEY (tenant_id, project_id, id),
    UNIQUE (tenant_id, id),
    UNIQUE (project_id, plugin_id, key),
    FOREIGN KEY (tenant_id, project_id) REFERENCES core.projects (tenant_id, id)
);

CREATE TABLE knowledge.collection_sources (              -- publish ordering scope (R1) and no-change record (G5)
    id                uuid PRIMARY KEY DEFAULT uuidv7(),
    tenant_id         uuid NOT NULL,
    project_id        uuid NOT NULL,
    collection_id     uuid NOT NULL,
    source_id         uuid,
    pipeline_key      text NOT NULL,
    last_seq          bigint NOT NULL DEFAULT 0,
    last_revision_id  uuid,
    last_fingerprint  text,
    UNIQUE NULLS NOT DISTINCT (collection_id, source_id, pipeline_key),
    FOREIGN KEY (tenant_id, project_id, collection_id) REFERENCES knowledge.collections (tenant_id, project_id, id),
    FOREIGN KEY (tenant_id, project_id, source_id) REFERENCES core.sources (tenant_id, project_id, id),
    FOREIGN KEY (tenant_id, project_id, last_revision_id) REFERENCES core.source_revisions (tenant_id, project_id, id)
);

CREATE TABLE knowledge.generations (
    collection_id  uuid NOT NULL REFERENCES knowledge.collections (id),
    gen            bigint NOT NULL,
    job_id         uuid NOT NULL REFERENCES runtime.jobs (id),
    status         text NOT NULL CHECK (status IN ('published', 'retired')),
    published_at   timestamptz NOT NULL DEFAULT now(),
    retired_at     timestamptz,
    PRIMARY KEY (collection_id, gen)
);

CREATE TABLE knowledge.units (
    tenant_id      uuid NOT NULL,
    project_id     uuid NOT NULL,
    id             uuid NOT NULL DEFAULT uuidv7() UNIQUE,
    collection_id  uuid NOT NULL,
    source_id      uuid,
    kind           text NOT NULL,                        -- <plugin>.<kind>
    key            text NOT NULL,
    created_by     uuid NOT NULL REFERENCES core.principals,
    on_behalf_of   text,                                 -- unverified until delegation (O-04)
    last_seq       bigint NOT NULL DEFAULT 0,            -- seq of the last job that changed this unit (§24 per-unit guard)
    PRIMARY KEY (tenant_id, id),
    UNIQUE NULLS NOT DISTINCT (collection_id, source_id, kind, key),
    FOREIGN KEY (tenant_id, project_id, collection_id) REFERENCES knowledge.collections (tenant_id, project_id, id),
    FOREIGN KEY (tenant_id, project_id, source_id) REFERENCES core.sources (tenant_id, project_id, id)
);

CREATE TABLE knowledge.unit_versions (
    tenant_id       uuid NOT NULL,
    id              uuid NOT NULL DEFAULT uuidv7() UNIQUE,
    unit_id         uuid NOT NULL,
    parent_version  uuid REFERENCES knowledge.unit_versions (id) ON DELETE SET NULL,   -- GC removes old versions
    content_hash    text NOT NULL,
    attrs           jsonb NOT NULL DEFAULT '{}',
    candidate_job   uuid REFERENCES runtime.jobs (id),   -- set while staged
    valid_from_gen  bigint,                              -- NULL = staged, invisible
    valid_to_gen    bigint,
    PRIMARY KEY (tenant_id, id),
    FOREIGN KEY (tenant_id, unit_id) REFERENCES knowledge.units (tenant_id, id),
    CHECK (valid_to_gen IS NULL OR (valid_from_gen IS NOT NULL AND valid_to_gen > valid_from_gen)),
    CHECK (valid_from_gen IS NOT NULL OR candidate_job IS NOT NULL)
);
-- Publish closes the old open version before opening the new one (same txn) so this index never conflicts.
CREATE UNIQUE INDEX uq_unit_open_version ON knowledge.unit_versions (unit_id) WHERE valid_from_gen IS NOT NULL AND valid_to_gen IS NULL;
CREATE UNIQUE INDEX uq_unit_staged_per_job ON knowledge.unit_versions (unit_id, candidate_job) WHERE valid_from_gen IS NULL;
CREATE INDEX ix_unit_versions_visible ON knowledge.unit_versions (unit_id, valid_from_gen, valid_to_gen);
CREATE INDEX ix_unit_versions_candidate ON knowledge.unit_versions (candidate_job) WHERE valid_from_gen IS NULL;

CREATE TABLE knowledge.chunks (
    tenant_id        uuid NOT NULL,
    id               uuid NOT NULL DEFAULT uuidv7() UNIQUE,
    unit_version_id  uuid NOT NULL,
    ordinal          int  NOT NULL CHECK (ordinal >= 0),
    text             text NOT NULL CHECK (text <> ''),
    span             jsonb NOT NULL DEFAULT '{}',
    attrs            jsonb NOT NULL DEFAULT '{}',
    fts              tsvector GENERATED ALWAYS AS (to_tsvector('simple'::regconfig, text || ' ' || coalesce(attrs->>'summary', '') || ' '
                         || coalesce(attrs->>'summary_ko', '') || ' ' || coalesce(attrs->>'keywords', ''))) STORED,  -- keyword search (M2)
    PRIMARY KEY (tenant_id, id),
    UNIQUE (unit_version_id, ordinal),
    FOREIGN KEY (tenant_id, unit_version_id) REFERENCES knowledge.unit_versions (tenant_id, id) ON DELETE CASCADE
);
CREATE INDEX ix_chunks_fts ON knowledge.chunks USING gin (fts);

CREATE TABLE knowledge.representation_values (           -- tenant-scoped, shared across projects (I-K2)
    tenant_id    uuid NOT NULL REFERENCES core.tenants,
    id           uuid NOT NULL DEFAULT uuidv7() UNIQUE,
    rep_kind     text NOT NULL,
    config_hash  text NOT NULL,
    input_hash   text NOT NULL,
    dims         int,
    vec          vector,                                 -- one HNSW per vector kind (partial expression index), O-05
    body         text,
    PRIMARY KEY (tenant_id, rep_kind, id),
    UNIQUE (tenant_id, rep_kind, config_hash, input_hash),
    UNIQUE (tenant_id, rep_kind, config_hash, id),
    CHECK ((vec IS NULL) = (dims IS NULL)),
    CHECK (vec IS NOT NULL OR body IS NOT NULL)
);

CREATE TABLE knowledge.representation_links (
    tenant_id        uuid NOT NULL,
    rep_kind         text NOT NULL,
    config_hash      text NOT NULL,                      -- a new model setting adds links; versions stay immutable
    value_id         uuid NOT NULL,
    unit_version_id  uuid,
    chunk_id         uuid,
    FOREIGN KEY (tenant_id, rep_kind, config_hash, value_id) REFERENCES knowledge.representation_values (tenant_id, rep_kind, config_hash, id),
    FOREIGN KEY (tenant_id, unit_version_id) REFERENCES knowledge.unit_versions (tenant_id, id) ON DELETE CASCADE,
    FOREIGN KEY (tenant_id, chunk_id) REFERENCES knowledge.chunks (tenant_id, id) ON DELETE CASCADE,
    CHECK ((unit_version_id IS NULL) <> (chunk_id IS NULL)),
    UNIQUE NULLS NOT DISTINCT (rep_kind, config_hash, unit_version_id, chunk_id)
);
CREATE INDEX ix_rl_value ON knowledge.representation_links (value_id);
CREATE INDEX ix_rl_unit_version ON knowledge.representation_links (unit_version_id) WHERE unit_version_id IS NOT NULL;
CREATE INDEX ix_rl_chunk ON knowledge.representation_links (chunk_id) WHERE chunk_id IS NOT NULL;

CREATE TABLE knowledge.candidate_keys (
    job_id   uuid NOT NULL REFERENCES runtime.jobs (id),
    unit_id  uuid NOT NULL REFERENCES knowledge.units (id),
    mode     text NOT NULL CHECK (mode IN ('touch', 'hold', 'delete')),  -- delete = explicit delete (R2)
    reason   text,
    PRIMARY KEY (job_id, unit_id)
);
CREATE INDEX ix_candidate_keys_unit ON knowledge.candidate_keys (unit_id);

CREATE TABLE knowledge.generation_pins (
    id             uuid PRIMARY KEY DEFAULT uuidv7(),
    collection_id  uuid NOT NULL REFERENCES knowledge.collections (id),
    gen            bigint NOT NULL,
    principal_id   uuid NOT NULL REFERENCES core.principals,   -- per-principal pin cap (R17)
    expires_at     timestamptz NOT NULL
);
CREATE INDEX ix_pins_active ON knowledge.generation_pins (collection_id, gen, expires_at);

-- runtime.jobs -> collections FK is added after knowledge.collections exists
ALTER TABLE runtime.jobs ADD FOREIGN KEY (tenant_id, project_id, collection_id)
    REFERENCES knowledge.collections (tenant_id, project_id, id);
