-- U4 approval gate (P-R10, R15, O-17), connector secrets (P-P12, ST-07), observability views (§28.4).

-- Current artifact of a job (R15): the newest artifact whose provenance names the job; none -> NULL.
CREATE FUNCTION runtime.job_artifact_sha(_job uuid) RETURNS text
LANGUAGE sql STABLE SET search_path = pg_catalog, pg_temp AS $$
    SELECT sha256 FROM core.artifacts WHERE provenance->>'job_id' = _job::text ORDER BY created_at DESC, id DESC LIMIT 1 $$;

-- Ends a waiting approval stage as failed and stops the job (reject or expiry); caller holds the job lock.
CREATE FUNCTION runtime.approval_fail(_job uuid, _stage text, _code text) RETURNS void
LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $$
DECLARE j runtime.jobs;
BEGIN
    UPDATE runtime.job_stages SET status = 'failed' WHERE job_id = _job AND stage_key = _stage;
    UPDATE runtime.jobs SET stop_requested = 'failure', outcome = _code WHERE id = _job RETURNING * INTO j;
    PERFORM runtime.life(j, _stage, 'waiting_approval', 'failed', jsonb_build_object('error_code', _code));
    PERFORM runtime.finish_stopped_jobs(_job);
    UPDATE core.alerts SET cleared_at = now() WHERE code = 'core.approval_waiting' AND object = 'job:' || _job || '/' || _stage
        AND cleared_at IS NULL;
END $$;

-- U4 approve(principal, job, stage, artifact_sha256, decision): operator. Approval without an artifact (G8) binds to the
-- hash of the decision payload. Approve and expire both lock the job FOR UPDATE; first to commit wins.
CREATE FUNCTION runtime.approve(_p uuid, _job uuid, _stage text, _hash text, _decision text, _rid uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE a jsonb := jsonb_build_object('job', _job, 'stage', _stage, 'hash', _hash, 'decision', _decision);
        r jsonb; j runtime.jobs; s runtime.job_stages; cur text; prior text;
BEGIN
    SELECT * INTO j FROM runtime.jobs WHERE id = _job;
    IF NOT FOUND OR core.authorize(_p, j.project_id, 3) IS NULL THEN RETURN core.res('FORBIDDEN'); END IF;
    IF _decision NOT IN ('approve', 'reject') THEN RETURN core.res('INPUT_INVALID'); END IF;
    r := core.req_check(_p, _rid, 'approve', a); IF r IS NOT NULL THEN RETURN r; END IF;
    SELECT * INTO j FROM runtime.jobs WHERE id = _job FOR UPDATE;
    SELECT * INTO s FROM runtime.job_stages WHERE job_id = _job AND stage_key = _stage FOR UPDATE;
    IF NOT FOUND THEN RETURN core.res('NOT_FOUND', '{"what": "stage"}'); END IF;
    IF s.status <> 'waiting_approval' THEN
        prior := CASE WHEN s.status = 'succeeded' AND s.approved_by IS NOT NULL THEN 'approve'
                      WHEN j.outcome = 'core.approval_rejected' AND s.status = 'failed' THEN 'reject' END;
        IF prior = _decision AND s.approved_by = _p THEN RETURN core.res('OK', '{"replay": true}'); END IF;
        RETURN core.res('ALREADY_DECIDED', jsonb_build_object('status', s.status, 'job_outcome', j.outcome));
    END IF;
    IF j.stop_requested IS NOT NULL OR j.status <> 'running' THEN
        RETURN core.res('ALREADY_DECIDED', jsonb_build_object('status', j.status));
    END IF;
    IF now() >= s.wait_until THEN
        PERFORM runtime.approval_fail(_job, _stage, 'core.approval_expired');
        RETURN core.res('EXPIRED');                              -- not saved: the expiry is the stored decision
    END IF;
    cur := coalesce(runtime.job_artifact_sha(_job),
                    encode(sha256(convert_to(jsonb_build_object('job', _job, 'stage', _stage)::text, 'UTF8')), 'hex'));
    IF _decision = 'approve' AND _hash IS DISTINCT FROM cur AND NOT (_hash IS NULL AND runtime.job_artifact_sha(_job) IS NULL) THEN
        RETURN core.res('HASH_MISMATCH', jsonb_build_object('current', cur));
    END IF;
    UPDATE runtime.job_stages SET approved_by = _p, approved_hash = cur WHERE job_id = _job AND stage_key = _stage;
    IF _decision = 'approve' THEN
        PERFORM runtime.stage_check(_job, _stage);              -- no tasks: stage succeeds, dependants open
        UPDATE core.alerts SET cleared_at = now() WHERE code = 'core.approval_waiting'
            AND object = 'job:' || _job || '/' || _stage AND cleared_at IS NULL;
    ELSE
        PERFORM runtime.approval_fail(_job, _stage, 'core.approval_rejected');
    END IF;
    PERFORM core.audit_write(_p, 'approve', 'job:' || _job || '/' || _stage, jsonb_build_object('decision', _decision, 'hash', cur));
    RETURN core.req_save(_p, _rid, 'approve', a, core.res('OK', jsonb_build_object('decision', _decision, 'hash', cur)));
END $$;

-- Maintenance: expire overdue approvals (never implies approval) and keep one open alert per waiting stage.
CREATE FUNCTION runtime.approval_tick() RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE s record; expired int := 0; waiting int := 0;
BEGIN
    FOR s IN SELECT js.job_id, js.stage_key, js.wait_until FROM runtime.job_stages js
             WHERE js.status = 'waiting_approval' ORDER BY js.job_id LOOP
        PERFORM 1 FROM runtime.jobs WHERE id = s.job_id FOR UPDATE SKIP LOCKED;
        CONTINUE WHEN NOT FOUND;
        CONTINUE WHEN NOT EXISTS (SELECT 1 FROM runtime.job_stages WHERE job_id = s.job_id AND stage_key = s.stage_key
                                  AND status = 'waiting_approval');
        IF now() >= s.wait_until THEN
            PERFORM runtime.approval_fail(s.job_id, s.stage_key, 'core.approval_expired'); expired := expired + 1;
        ELSE
            INSERT INTO core.alerts (code, object, severity) VALUES ('core.approval_waiting', 'job:' || s.job_id || '/' || s.stage_key, 'info')
                ON CONFLICT (code, object) WHERE cleared_at IS NULL DO UPDATE SET last_at = now();
            waiting := waiting + 1;
        END IF;
    END LOOP;
    RETURN jsonb_build_object('expired', expired, 'waiting', waiting);
END $$;

-- P-P12 connector secrets: tenant admin writes a new version (old active -> retiring); a plugin task reads only
-- its own tenant's secret of its own connector (<plugin>.<connector>). Ciphertext only; the worker decrypts with the host key.
CREATE FUNCTION core.connector_secret_set(_p uuid, _tenant uuid, _connector text, _name text, _key_id text, _ciphertext bytea, _rid uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE a jsonb := jsonb_build_object('tenant', _tenant, 'connector', _connector, 'name', _name,
                                      'ct', encode(sha256(_ciphertext), 'hex'));
        r jsonb; v int;
BEGIN
    IF core.tenant_role(_p, _tenant) < 4 THEN RETURN core.res('FORBIDDEN'); END IF;
    IF _connector !~ '^[a-z][a-z0-9_]{1,29}\.[a-z][a-z0-9_.]*$' OR _connector LIKE 'core.%' THEN RETURN core.res('INPUT_INVALID'); END IF;
    r := core.req_check(_p, _rid, 'connector_secret_set', a); IF r IS NOT NULL THEN RETURN r; END IF;
    PERFORM pg_advisory_xact_lock(hashtext(_tenant::text || _connector || _name));
    SELECT coalesce(max(version), 0) + 1 INTO v FROM core.secrets WHERE tenant_id = _tenant AND owner = _connector AND name = _name;
    UPDATE core.secrets SET status = 'retiring' WHERE tenant_id = _tenant AND owner = _connector AND name = _name AND status = 'active';
    INSERT INTO core.secrets (tenant_id, owner, name, version, key_id, ciphertext, status)
        VALUES (_tenant, _connector, _name, v, _key_id, _ciphertext, 'active');
    PERFORM core.audit_write(_p, 'connector_secret_set', 'secret:' || _connector || '/' || _name, jsonb_build_object('version', v));
    RETURN core.req_save(_p, _rid, 'connector_secret_set', a, core.res('CREATED', jsonb_build_object('version', v)));
END $$;

CREATE FUNCTION core.get_connector_secret(_task uuid, _token uuid, _epoch bigint, _connector text, _name text DEFAULT 'default')
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE g jsonb; tk runtime.tasks; s core.secrets;
BEGIN
    g := runtime.guard(_task, _token, _epoch, NULL); IF g IS NOT NULL THEN RETURN g; END IF;
    SELECT * INTO tk FROM runtime.tasks WHERE id = _task;
    IF split_part(_connector, '.', 1) IS DISTINCT FROM tk.plugin_id THEN RETURN core.res('FORBIDDEN'); END IF;
    SELECT * INTO s FROM core.secrets WHERE tenant_id = tk.tenant_id AND owner = _connector AND name = _name AND status = 'active';
    IF NOT FOUND THEN RETURN core.res('FORBIDDEN'); END IF;      -- other tenant's secret looks the same as none (ST-07)
    RETURN core.res('OK', jsonb_build_object('version', s.version, 'key_id', s.key_id, 'ciphertext', encode(s.ciphertext, 'base64')));
END $$;

-- §28.4 metrics as views (maint reads them; core.status gives a principal's own summary).
CREATE VIEW runtime.v_queue AS
    SELECT plugin_id, stage_key, status, count(*) AS n, min(ready_at) AS oldest_ready
    FROM runtime.tasks WHERE status IN ('ready', 'running', 'retry_wait') GROUP BY 1, 2, 3;
CREATE VIEW runtime.v_job_durations AS
    SELECT plugin_id, pipeline_key, status, count(*) AS n,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY extract(epoch FROM finished_at - created_at)) AS p50_s,
           percentile_cont(0.95) WITHIN GROUP (ORDER BY extract(epoch FROM finished_at - created_at)) AS p95_s
    FROM runtime.jobs WHERE finished_at > now() - interval '7 days' GROUP BY 1, 2, 3;
CREATE VIEW runtime.v_breaker_state AS SELECT resource_key, state, opened_at FROM runtime.resource_state;
CREATE VIEW knowledge.v_collections AS
    SELECT c.tenant_id, c.project_id, c.id AS collection_id, c.plugin_id, c.key, c.current_gen,
           count(uv.id) FILTER (WHERE uv.valid_from_gen IS NOT NULL AND uv.valid_to_gen IS NULL) AS open_versions,
           count(uv.id) FILTER (WHERE uv.valid_to_gen IS NOT NULL) AS old_versions,
           count(uv.id) FILTER (WHERE uv.valid_from_gen IS NULL) AS staged_versions,
           round(count(uv.id) FILTER (WHERE uv.valid_to_gen IS NOT NULL)::numeric / greatest(count(uv.id), 1), 3) AS old_ratio
    FROM knowledge.collections c LEFT JOIN knowledge.units u ON u.collection_id = c.id
    LEFT JOIN knowledge.unit_versions uv ON uv.unit_id = u.id GROUP BY c.tenant_id, c.project_id, c.id;
CREATE VIEW knowledge.v_gc_backlog AS
    SELECT collection_id, plugin_id, key, old_versions, old_ratio FROM knowledge.v_collections WHERE old_versions > 0;
CREATE VIEW core.v_plugins AS
    SELECT p.id, p.version, p.status, p.registered_at,
           (SELECT count(*) FROM core.project_plugins pp WHERE pp.plugin_id = p.id AND pp.enabled) AS enabled_projects
    FROM core.plugins p;

-- /core/v1/status: jobs and waiting approvals in the principal's readable projects; open alerts for platform admins.
CREATE FUNCTION core.status(_p uuid) RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
    SELECT core.res('OK', jsonb_build_object(
        'jobs', (SELECT coalesce(jsonb_object_agg(status, n), '{}') FROM (
                    SELECT j.status, count(*) n FROM runtime.jobs j
                    WHERE (j.finished_at IS NULL OR j.finished_at > now() - interval '1 day') AND core.authorize(_p, j.project_id, 1) IS NOT NULL
                    GROUP BY 1) x),
        'waiting_approval', (SELECT coalesce(jsonb_agg(jsonb_build_object('job_id', s.job_id, 'stage', s.stage_key,
                                 'wait_until', s.wait_until, 'artifact_sha256', runtime.job_artifact_sha(s.job_id)) ORDER BY s.wait_until), '[]')
                    FROM runtime.job_stages s JOIN runtime.jobs j ON j.id = s.job_id
                    WHERE s.status = 'waiting_approval' AND core.authorize(_p, j.project_id, 1) IS NOT NULL),
        'alerts', CASE WHEN core.is_platform_admin(_p) THEN (SELECT coalesce(jsonb_agg(jsonb_build_object('code', code,
                       'object', object, 'severity', severity, 'count', count, 'last_at', last_at) ORDER BY last_at DESC), '[]')
                       FROM core.alerts WHERE cleared_at IS NULL) END)) $$;
