-- Core registry keys (§14 error codes, §18 policy keys, core.upload connector) and the platform admin list.

-- coreadm platform admins (bootstrap, §25 Management): tenant_create, plugin_disable, global policy, signing keys.
CREATE TABLE core.platform_admins (
    principal_id  uuid PRIMARY KEY REFERENCES core.principals,
    added_at      timestamptz NOT NULL DEFAULT now()
);

INSERT INTO core.registry_keys (item_type, key, owner, spec)
SELECT 'error_code', k, 'core', jsonb_build_object('layer', l, 'retryable', r, 'health_sample', h)
FROM (VALUES
    ('core.attempts_exhausted', 'runtime', false, false), ('core.deadline_exceeded', 'runtime', false, false),
    ('core.approval_expired', 'runtime', false, false),   ('core.approval_rejected', 'runtime', false, false),
    ('core.fence_expired', 'knowledge', false, false),    ('knowledge.coverage_failed', 'knowledge', false, false),
    ('core.plugin_version_missing', 'runtime', true, false), ('core.unknown_error_code', 'runtime', false, false),
    ('core.split_limit', 'runtime', false, false),        ('core.handler_crashed', 'runtime', true, false),
    ('core.resource_unavailable', 'runtime', true, true), ('core.resource_timeout', 'runtime', true, true),
    ('core.resource_rate_limited', 'runtime', true, true), ('core.breaker_open', 'runtime', true, false),
    ('core.output_invalid', 'knowledge', false, false),   ('core.coverage_failed', 'knowledge', false, false),
    ('core.input_invalid', 'core', false, false),         ('core.input_incomplete', 'core', true, false),
    ('core.source_unstable', 'core', false, false),       ('core.permission_denied', 'core', false, false)
) v(k, l, r, h);

INSERT INTO core.registry_keys (item_type, key, owner, spec)
SELECT 'policy_key', k, 'core', jsonb_strip_nulls(jsonb_build_object('type', t, 'min', mn, 'max', mx))
FROM (VALUES
    ('runtime.lease', 'interval', NULL::numeric, NULL::numeric), ('runtime.heartbeat', 'interval', NULL, NULL),
    ('runtime.sweeper_interval', 'interval', NULL, NULL),     ('runtime.max_attempts', 'int', 1, NULL),
    ('runtime.backoff_base', 'interval', NULL, NULL),         ('runtime.backoff_max', 'interval', NULL, NULL),
    ('runtime.claim_statement_timeout', 'interval', NULL, NULL), ('runtime.claim_lock_timeout', 'interval', NULL, NULL),
    ('runtime.deadline.item', 'interval', NULL, NULL),        ('runtime.deadline.control', 'interval', NULL, NULL),
    ('runtime.deadline.publish', 'interval', NULL, NULL),     ('runtime.deadline.approval', 'interval', NULL, NULL),
    ('runtime.deadline_extension_max', 'int', 0, NULL),       ('runtime.breaker.window', 'int', 1, NULL),
    ('runtime.breaker.ratio', 'number', 0, 1),                ('runtime.breaker.min_sample', 'int', 1, NULL),
    ('runtime.breaker.cooldown', 'interval', NULL, NULL),     ('runtime.max_running', 'int', 1, NULL),
    ('runtime.approval_ttl', 'interval', NULL, NULL),         ('runtime.job_deadline', 'interval', NULL, NULL),
    ('runtime.retry_cap', 'int', 0, NULL),                    ('runtime.max_uncharged', 'int', 0, NULL),
    ('knowledge.pin_ttl', 'interval', NULL, NULL),            ('knowledge.gc.grace', 'interval', NULL, NULL),
    ('knowledge.gc.lock_timeout', 'interval', NULL, NULL),    ('knowledge.gc.reindex_ratio', 'number', 0, 1),
    ('knowledge.hold_alert_after', 'interval', NULL, NULL),   ('knowledge.chunk.max_chars', 'int', 1, NULL),
    ('knowledge.search.candidate_k', 'int', 1, NULL),         ('knowledge.search.rrf_k', 'int', 1, NULL),
    ('knowledge.search.max_k', 'int', 1, NULL),               ('knowledge.search.ef_search', 'int', 1, NULL),
    ('knowledge.min_coverage', 'number', 0, 1),               ('knowledge.max_excluded_ratio', 'number', 0, 1),
    ('knowledge.delete_batch_max', 'int', 1, NULL),           ('core.token_ttl', 'interval', NULL, NULL),
    ('core.signing_key_retire_delay', 'interval', NULL, NULL), ('core.purge_grace', 'interval', NULL, NULL),
    ('core.snapshot_retention', 'interval', NULL, NULL),      ('core.scratch_ttl', 'interval', NULL, NULL),
    ('core.backup_mode_max', 'interval', NULL, NULL),         ('core.webhook_allowlist', 'text', NULL, NULL),
    ('core.retention.task_events', 'interval', NULL, NULL),   ('core.retention.outbox', 'interval', NULL, NULL)
) v(k, t, mn, mx);

INSERT INTO core.registry_keys (item_type, key, owner, spec)
VALUES ('connector', 'core.upload', 'core', '{"produces": "file_tree@1", "refetchable": false}');

-- §41 START values (global scope; replaced by measured values later).
INSERT INTO core.policies (scope, scope_ref, key, value) VALUES
    ('global', '', 'runtime.heartbeat', '"30 seconds"'), ('global', '', 'runtime.lease', '"120 seconds"'),
    ('global', '', 'runtime.sweeper_interval', '"30 seconds"'), ('global', '', 'runtime.max_attempts', '3'),
    ('global', '', 'runtime.retry_cap', '3'), ('global', '', 'runtime.max_uncharged', '20'),
    ('global', '', 'runtime.backoff_base', '"30 seconds"'), ('global', '', 'runtime.backoff_max', '"15 minutes"'),
    ('global', '', 'runtime.claim_statement_timeout', '"5 seconds"'), ('global', '', 'runtime.claim_lock_timeout', '"1 second"'),
    ('global', '', 'runtime.deadline.item', '"1 hour"'), ('global', '', 'runtime.deadline.control', '"2 hours"'),
    ('global', '', 'runtime.deadline.publish', '"30 minutes"'), ('global', '', 'runtime.deadline_extension_max', '3'),
    ('global', '', 'runtime.job_deadline', '"24 hours"'), ('global', '', 'runtime.breaker.window', '50'),
    ('global', '', 'runtime.breaker.ratio', '0.5'), ('global', '', 'runtime.breaker.min_sample', '10'),
    ('global', '', 'runtime.breaker.cooldown', '"2 minutes"'), ('global', '', 'runtime.max_running', '4'),
    ('global', '', 'runtime.approval_ttl', '"7 days"'),
    ('global', '', 'knowledge.pin_ttl', '"15 minutes"'), ('global', '', 'knowledge.gc.grace', '"1 hour"'),
    ('global', '', 'knowledge.gc.lock_timeout', '"2 seconds"'), ('global', '', 'knowledge.gc.reindex_ratio', '0.2'),
    ('global', '', 'knowledge.hold_alert_after', '"24 hours"'), ('global', '', 'knowledge.chunk.max_chars', '8000'),
    ('global', '', 'knowledge.search.candidate_k', '50'), ('global', '', 'knowledge.search.rrf_k', '60'),
    ('global', '', 'knowledge.search.max_k', '20'), ('global', '', 'knowledge.search.ef_search', '200'),
    ('global', '', 'knowledge.min_coverage', '0.95'), ('global', '', 'knowledge.max_excluded_ratio', '0.5'),
    ('global', '', 'knowledge.delete_batch_max', '10000'),
    ('global', '', 'core.token_ttl', '"1 hour"'), ('global', '', 'core.signing_key_retire_delay', '"24 hours"'),
    ('global', '', 'core.purge_grace', '"7 days"'), ('global', '', 'core.snapshot_retention', '"30 days"'),
    ('global', '', 'core.scratch_ttl', '"7 days"'), ('global', '', 'core.backup_mode_max', '"6 hours"'),
    ('global', '', 'core.retention.task_events', '"90 days"'), ('global', '', 'core.retention.outbox', '"30 days"');
