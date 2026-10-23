-- Runtime (M1b): job creation (C1), start, Worker Protocol W1-W5 (§6.2, §25, §40), sweep (M1), finish (M2),
-- cancel (U2), retry (U3), get_job (U1). Lock order: job -> task -> parent tasks -> job_stages (§26).
-- A failed stage marks the job as stopping without updating the job row (workers hold the job FOR SHARE);
-- finish_stopped_jobs (job FOR UPDATE) makes the job terminal once no task runs.

-- ---------- helpers (internal) ----------
CREATE FUNCTION runtime.caller_plugin() RETURNS text
LANGUAGE sql STABLE SET search_path = pg_catalog, pg_temp
AS $$ SELECT substring(session_user::text FROM '^plugin_([a-z][a-z0-9_]*)$') $$;

CREATE FUNCTION runtime.ival(_snap jsonb, _key text, _default interval) RETURNS interval
LANGUAGE sql IMMUTABLE SET search_path = pg_catalog, pg_temp
AS $$ SELECT coalesce((_snap->>_key)::interval, _default) $$;

CREATE FUNCTION runtime.nval(_snap jsonb, _key text, _default numeric) RETURNS numeric
LANGUAGE sql IMMUTABLE SET search_path = pg_catalog, pg_temp
AS $$ SELECT coalesce((_snap->>_key)::numeric, _default) $$;

CREATE FUNCTION runtime.life(_job runtime.jobs, _stage text, _from text, _to text, _detail jsonb DEFAULT '{}') RETURNS void
LANGUAGE sql SET search_path = pg_catalog, pg_temp AS $$
    INSERT INTO runtime.lifecycle_events (job_id, stage_key, from_status, to_status, detail) VALUES (_job.id, _stage, _from, _to, _detail);
    INSERT INTO core.outbox_events (tenant_id, project_id, type, payload)
    SELECT _job.tenant_id, _job.project_id, 'runtime.job_' || _to,
           jsonb_build_object('job', _job.id, 'collection', _job.collection_id, 'seq', _job.seq, 'outcome', _job.outcome)
    WHERE _stage IS NULL;                                          -- job-level changes reach the outbox (I-R2)
$$;

CREATE FUNCTION runtime.stopping(_job uuid) RETURNS boolean
LANGUAGE sql STABLE SET search_path = pg_catalog, pg_temp AS $$
    SELECT j.stop_requested IS NOT NULL OR j.finished_at IS NOT NULL
        OR EXISTS (SELECT 1 FROM runtime.job_stages s WHERE s.job_id = _job AND s.status = 'failed')
    FROM runtime.jobs j WHERE j.id = _job $$;

-- Every policy key the pipeline can read, frozen at create_job (R5).
CREATE FUNCTION runtime.snapshot(_plugin text, _tenant uuid, _project uuid) RETURNS jsonb
LANGUAGE sql STABLE SET search_path = pg_catalog, pg_temp AS $$
    SELECT coalesce(jsonb_object_agg(k.key, core.policy_value(k.key, ARRAY[_plugin, _project::text, _tenant::text])), '{}')
    FROM core.registry_keys k WHERE k.item_type = 'policy_key' AND k.owner IN ('core', _plugin) $$;

-- I-R7: terminal states are immutable.
CREATE FUNCTION runtime.terminal_guard() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $$
BEGIN
    IF OLD.status = ANY (TG_ARGV) AND NEW.status IS DISTINCT FROM OLD.status THEN
        RAISE EXCEPTION 'terminal state is immutable: % % -> %', TG_TABLE_NAME, OLD.status, NEW.status;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER trg_jobs_terminal BEFORE UPDATE OF status ON runtime.jobs FOR EACH ROW
    EXECUTE FUNCTION runtime.terminal_guard('succeeded', 'failed', 'canceled', 'superseded');
CREATE TRIGGER trg_stages_terminal BEFORE UPDATE OF status ON runtime.job_stages FOR EACH ROW
    EXECUTE FUNCTION runtime.terminal_guard('succeeded', 'failed', 'canceled');
CREATE TRIGGER trg_tasks_terminal BEFORE UPDATE OF status ON runtime.tasks FOR EACH ROW
    EXECUTE FUNCTION runtime.terminal_guard('succeeded', 'failed', 'canceled', 'split');

-- Ready stages of a running job get their root task (item/control); approval waits; publish is run by Core.
CREATE FUNCTION runtime.open_ready_stages(_job uuid) RETURNS void
LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $$
DECLARE j runtime.jobs; s record;
BEGIN
    SELECT * INTO j FROM runtime.jobs WHERE id = _job;
    FOR s IN SELECT js.stage_key, ps.kind FROM runtime.job_stages js
             JOIN runtime.pipeline_stages ps ON (ps.plugin_id, ps.pipeline_key, ps.version, ps.stage_key)
                                              = (js.plugin_id, js.pipeline_key, js.pipeline_version, js.stage_key)
             WHERE js.job_id = _job AND js.status = 'ready'
               AND NOT EXISTS (SELECT 1 FROM runtime.tasks t WHERE t.job_id = _job AND t.stage_key = js.stage_key)
    LOOP
        IF s.kind IN ('item', 'control') THEN
            INSERT INTO runtime.tasks (job_id, stage_key, plugin_id, tenant_id, project_id, status)
                VALUES (_job, s.stage_key, j.plugin_id, j.tenant_id, j.project_id, 'ready');
        ELSIF s.kind = 'approval' THEN
            UPDATE runtime.job_stages SET status = 'waiting_approval',
                   wait_until = now() + runtime.ival(j.policy_snapshot, 'runtime.approval_ttl', '7 days')
            WHERE job_id = _job AND stage_key = s.stage_key;
        END IF;
    END LOOP;
END $$;

CREATE FUNCTION runtime.task_view(_t runtime.tasks, _j runtime.jobs) RETURNS jsonb
LANGUAGE sql STABLE SET search_path = pg_catalog, pg_temp AS $$
    SELECT jsonb_build_object('task_id', _t.id, 'job_id', _t.job_id, 'stage', _t.stage_key, 'handler', ps.handler,
        'lease_token', _t.lease_token, 'lease_epoch', _t.lease_epoch, 'attempt', _t.attempt, 'depth', _t.depth,
        'child_key', _t.child_key, 'scope', _t.scope, 'payload', _t.payload, 'deadline_at', _t.deadline_at,
        'tenant_id', _j.tenant_id, 'project_id', _j.project_id, 'collection_id', _j.collection_id,
        'source_revision_id', _j.source_revision_id, 'plugin_version', _j.plugin_version, 'policy', _j.policy_snapshot)
    FROM runtime.pipeline_stages ps
    WHERE (ps.plugin_id, ps.pipeline_key, ps.version, ps.stage_key) = (_j.plugin_id, _j.pipeline_key, _j.pipeline_version, _t.stage_key) $$;

-- ---------- C1 create_job (api) ----------
CREATE FUNCTION runtime.create_job(_p uuid, _project uuid, _plugin text, _pipeline text, _revision uuid, _force boolean, _rid uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE a jsonb := jsonb_build_object('project', _project, 'plugin', _plugin, 'pipeline', _pipeline, 'revision', _revision, 'force', _force);
        r jsonb; t uuid; pv text; mh text; pl runtime.pipelines; rev core.source_revisions; coll uuid;
        s knowledge.collection_sources; snap jsonb; fp text; sq bigint; j runtime.jobs; q runtime.jobs;
BEGIN
    t := core.authorize(_p, _project, 2); IF t IS NULL THEN RETURN core.res('FORBIDDEN'); END IF;
    IF (SELECT state FROM core.projects WHERE id = _project) <> 'active' THEN RETURN core.res('PROJECT_DELETING'); END IF;
    IF NOT EXISTS (SELECT 1 FROM core.project_plugins WHERE project_id = _project AND plugin_id = _plugin AND enabled) THEN
        RETURN core.res('PLUGIN_DISABLED');
    END IF;
    SELECT version, manifest_hash INTO pv, mh FROM core.plugins WHERE id = _plugin AND status = 'registered'
        ORDER BY registered_at DESC LIMIT 1;
    IF pv IS NULL THEN RETURN core.res('PLUGIN_DISABLED'); END IF;
    SELECT p.* INTO pl FROM runtime.pipelines p
    WHERE p.plugin_id = _plugin AND p.key = _pipeline
      AND p.version = (SELECT (x->>'version')::int FROM core.plugins c, jsonb_array_elements(c.manifest->'pipelines') x
                       WHERE c.id = _plugin AND c.version = pv AND x->>'key' = _pipeline);
    IF NOT FOUND THEN RETURN core.res('NOT_FOUND', '{"what": "pipeline"}'); END IF;
    IF _revision IS NOT NULL THEN
        SELECT * INTO rev FROM core.source_revisions WHERE id = _revision AND project_id = _project;
        IF NOT FOUND THEN RETURN core.res('NOT_FOUND', '{"what": "revision"}'); END IF;
        IF rev.storage_state <> 'present' THEN RETURN core.res('SNAPSHOT_EXPIRED'); END IF;
    END IF;
    IF cardinality(pl.inputs) > 0 AND NOT EXISTS (                 -- P-P15: format name equal, major version equal
        SELECT 1 FROM core.sources so JOIN core.registry_keys k ON k.item_type = 'connector' AND k.key = so.connector,
                      unnest(pl.inputs) i
        WHERE so.id = rev.source_id AND split_part(k.spec->>'produces', '@', 1) = split_part(i, '@', 1)
          AND split_part(k.spec->>'produces', '@', 2) = ltrim(split_part(i, '@', 2), '^')) THEN
        RETURN core.res('INPUT_FORMAT_INCOMPATIBLE');
    END IF;
    r := core.req_check(_p, _rid, 'create_job', a); IF r IS NOT NULL THEN RETURN r; END IF;

    INSERT INTO knowledge.collections (tenant_id, project_id, plugin_id, key) VALUES (t, _project, _plugin, pl.collection_key)
        ON CONFLICT (project_id, plugin_id, key) DO NOTHING;
    SELECT id INTO coll FROM knowledge.collections WHERE project_id = _project AND plugin_id = _plugin AND key = pl.collection_key;
    INSERT INTO knowledge.collection_sources (tenant_id, project_id, collection_id, source_id, pipeline_key)
        VALUES (t, _project, coll, rev.source_id, _pipeline) ON CONFLICT (collection_id, source_id, pipeline_key) DO NOTHING;
    SELECT * INTO s FROM knowledge.collection_sources
        WHERE collection_id = coll AND source_id IS NOT DISTINCT FROM rev.source_id AND pipeline_key = _pipeline FOR UPDATE;

    snap := runtime.snapshot(_plugin, t, _project);
    fp := encode(sha256(convert_to(mh || '|' || pl.pipeline_hash || '|' || coalesce(rev.manifest_hash, '') || '|' ||
          (SELECT coalesce(jsonb_object_agg(k.key, snap->k.key), '{}') FROM core.registry_keys k
           WHERE k.item_type = 'policy_key' AND k.owner = _plugin AND (k.spec->>'affects_output')::boolean)::text, 'UTF8')), 'hex');
    sq := nextval('runtime.job_seq');                              -- after S lock: seq order = S order (§26 F1)

    INSERT INTO runtime.jobs (tenant_id, project_id, collection_id, source_id, source_revision_id, plugin_id, plugin_version,
                              pipeline_key, pipeline_version, pipeline_hash, policy_snapshot, fingerprint, force, request_id,
                              fence_seq, seq, status, outcome, created_by, finished_at)
    SELECT t, _project, coll, rev.source_id, _revision, _plugin, pv, _pipeline, pl.version, pl.pipeline_hash, snap, fp, _force, _rid,
           sq, sq, CASE WHEN nc THEN 'succeeded' ELSE 'queued' END, CASE WHEN nc THEN 'NO_CHANGE' END, _p,
           CASE WHEN nc THEN now() END
    FROM (SELECT NOT _force AND fp = s.last_fingerprint AND NOT EXISTS (
              SELECT 1 FROM runtime.jobs o WHERE o.collection_id = coll AND o.source_id IS NOT DISTINCT FROM rev.source_id
                 AND o.pipeline_key = _pipeline AND o.finished_at IS NULL) AS nc) x
    RETURNING * INTO j;
    IF j.status = 'succeeded' THEN
        UPDATE knowledge.collection_sources SET last_seq = sq WHERE id = s.id;
    ELSIF pl.publish_mode = 'full' THEN                            -- coalescing: queued older jobs in S are superseded
        FOR q IN UPDATE runtime.jobs SET status = 'superseded', outcome = 'SUPERSEDED', finished_at = now()
                 WHERE collection_id = coll AND source_id IS NOT DISTINCT FROM rev.source_id AND pipeline_key = _pipeline
                   AND status = 'queued' AND seq < sq RETURNING * LOOP
            PERFORM runtime.life(q, NULL, 'queued', 'superseded', jsonb_build_object('by', j.id));
        END LOOP;
    END IF;
    PERFORM runtime.life(j, NULL, NULL, j.status, jsonb_build_object('outcome', j.outcome));
    RETURN core.req_save(_p, _rid, 'create_job', a,
        core.res(coalesce(j.outcome, 'CREATED'), jsonb_build_object('job_id', j.id, 'seq', j.seq, 'collection_id', coll)));
END $$;

-- ---------- start queued jobs (runtime, maint) ----------
CREATE FUNCTION runtime.start_jobs(_max int DEFAULT 50) RETURNS int
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE j runtime.jobs; n int := 0;
BEGIN
    FOR j IN SELECT * FROM runtime.jobs WHERE status = 'queued' ORDER BY seq FOR UPDATE SKIP LOCKED LIMIT _max LOOP
        CONTINUE WHEN (SELECT count(*) FROM runtime.jobs WHERE plugin_id = j.plugin_id AND pipeline_key = j.pipeline_key
                       AND status = 'running') >= runtime.nval(j.policy_snapshot, 'runtime.max_running', 4);
        UPDATE runtime.jobs SET status = 'running' WHERE id = j.id RETURNING * INTO j;
        INSERT INTO runtime.job_stages (job_id, plugin_id, pipeline_key, pipeline_version, stage_key, status)
        SELECT j.id, s.plugin_id, s.pipeline_key, s.version, s.stage_key,
               CASE WHEN cardinality(s.depends_on) = 0 THEN 'ready' ELSE 'blocked' END
        FROM runtime.pipeline_stages s
        WHERE (s.plugin_id, s.pipeline_key, s.version) = (j.plugin_id, j.pipeline_key, j.pipeline_version);
        PERFORM runtime.open_ready_stages(j.id);
        PERFORM runtime.life(j, NULL, 'queued', 'running');
        n := n + 1;
    END LOOP;
    RETURN n;
END $$;

-- ---------- stage barrier and split resolution ----------
CREATE FUNCTION runtime.stage_check(_job uuid, _stage text) RETURNS void
LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $$
DECLARE pending int; failed int; canceled int; st text; j runtime.jobs;
BEGIN
    SELECT count(*) FILTER (WHERE resolution IS NULL), count(*) FILTER (WHERE resolution = 'failed'),
           count(*) FILTER (WHERE resolution = 'canceled')
    INTO pending, failed, canceled FROM runtime.tasks WHERE job_id = _job AND stage_key = _stage AND parent_id IS NULL;
    st := CASE WHEN failed > 0 THEN 'failed' WHEN pending > 0 THEN NULL WHEN canceled > 0 THEN 'canceled' ELSE 'succeeded' END;
    IF st IS NULL THEN RETURN; END IF;
    UPDATE runtime.job_stages SET status = st WHERE job_id = _job AND stage_key = _stage
        AND status NOT IN ('succeeded', 'failed', 'canceled');
    IF NOT FOUND THEN RETURN; END IF;
    SELECT * INTO j FROM runtime.jobs WHERE id = _job;
    PERFORM runtime.life(j, _stage, NULL, st);
    IF st = 'succeeded' AND NOT runtime.stopping(_job) THEN
        UPDATE runtime.job_stages b SET status = 'ready'
        WHERE b.job_id = _job AND b.status = 'blocked'
          AND NOT EXISTS (SELECT 1 FROM runtime.pipeline_stages ps, unnest(ps.depends_on) d
                          JOIN runtime.job_stages x ON x.job_id = _job AND x.stage_key = d
                          WHERE (ps.plugin_id, ps.pipeline_key, ps.version, ps.stage_key)
                              = (b.plugin_id, b.pipeline_key, b.pipeline_version, b.stage_key) AND x.status <> 'succeeded');
        PERFORM runtime.open_ready_stages(_job);
    END IF;
END $$;

-- Resolve split ancestors once every child is resolved (child before parent, parent locked; TP-19).
CREATE FUNCTION runtime.resolve(_task uuid) RETURNS void
LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $$
DECLARE tk runtime.tasks; par runtime.tasks; pending int; failed int; canceled int;
BEGIN
    SELECT * INTO tk FROM runtime.tasks WHERE id = _task;
    WHILE tk.parent_id IS NOT NULL LOOP
        SELECT * INTO par FROM runtime.tasks WHERE id = tk.parent_id FOR UPDATE;
        IF par.resolution IS NOT NULL THEN RETURN; END IF;
        SELECT count(*) FILTER (WHERE resolution IS NULL), count(*) FILTER (WHERE resolution = 'failed'),
               count(*) FILTER (WHERE resolution = 'canceled')
        INTO pending, failed, canceled FROM runtime.tasks WHERE parent_id = par.id;
        IF failed = 0 AND pending > 0 THEN RETURN; END IF;
        UPDATE runtime.tasks SET resolution = CASE WHEN failed > 0 THEN 'failed' WHEN canceled > 0 THEN 'canceled' ELSE 'ok' END
            WHERE id = par.id RETURNING * INTO tk;
    END LOOP;
    PERFORM runtime.stage_check(tk.job_id, tk.stage_key);
END $$;

-- I-R3: the only writer of task status 'failed'.
CREATE FUNCTION runtime.fail_terminal(_task uuid, _code text, _detail jsonb DEFAULT '{}', _rid uuid DEFAULT NULL,
                                      _result jsonb DEFAULT NULL) RETURNS void
LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $$
DECLARE tk runtime.tasks;
BEGIN
    UPDATE runtime.tasks SET status = 'failed', resolution = 'failed', error_code = _code,
           lease_token = NULL, lease_until = NULL WHERE id = _task RETURNING * INTO tk;
    INSERT INTO runtime.task_events (task_id, request_id, op, lease_epoch, outcome, error_code, detail)
        VALUES (_task, _rid, 'terminal_fail', tk.lease_epoch, 'FAILED', _code,
                _detail || jsonb_build_object('result', coalesce(_result, core.res('FAILED'))));
    PERFORM runtime.resolve(_task);
END $$;

CREATE FUNCTION runtime.cancel_task(_task uuid, _op text, _rid uuid, _result jsonb) RETURNS void
LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $$
DECLARE tk runtime.tasks;
BEGIN
    UPDATE runtime.tasks SET status = 'canceled', resolution = 'canceled', lease_token = NULL, lease_until = NULL
        WHERE id = _task RETURNING * INTO tk;
    INSERT INTO runtime.task_events (task_id, request_id, op, lease_epoch, outcome, detail)
        VALUES (_task, _rid, _op, tk.lease_epoch, _result->>'outcome', jsonb_build_object('result', _result));
    PERFORM runtime.resolve(_task);
END $$;

-- ---------- Worker Protocol (plugin logins) ----------
-- Common check (§6.2): caller's plugin, replay, job FOR SHARE then task FOR UPDATE, epoch, terminal, token.
CREATE FUNCTION runtime.guard(_task uuid, _token uuid, _epoch bigint, _rid uuid) RETURNS jsonb
LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $$
DECLARE tk runtime.tasks; j runtime.jobs; d jsonb;
BEGIN
    SELECT * INTO tk FROM runtime.tasks WHERE id = _task;
    IF NOT FOUND OR tk.plugin_id IS DISTINCT FROM runtime.caller_plugin() THEN RETURN core.res('FORBIDDEN'); END IF;
    IF _rid IS NOT NULL THEN
        SELECT detail->'result' INTO d FROM runtime.task_events WHERE task_id = _task AND request_id = _rid;
        IF FOUND THEN RETURN d || '{"replay": true}'; END IF;
    END IF;
    SELECT * INTO j FROM runtime.jobs WHERE id = tk.job_id FOR SHARE;
    SELECT * INTO tk FROM runtime.tasks WHERE id = _task FOR UPDATE;
    IF tk.lease_epoch <> _epoch THEN RETURN core.res('STALE_LEASE'); END IF;
    IF tk.status <> 'running' OR j.finished_at IS NOT NULL THEN RETURN core.res('ALREADY_TERMINAL'); END IF;
    IF tk.lease_token IS DISTINCT FROM _token THEN RETURN core.res('STALE_LEASE'); END IF;
    RETURN NULL;
END $$;

-- W1
CREATE FUNCTION runtime.claim(_max int, _versions text[], _rid uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE plug text := runtime.caller_plugin(); c record; j runtime.jobs; tk runtime.tasks; skind text; out jsonb := '[]';
BEGIN
    IF plug IS NULL THEN RETURN core.res('FORBIDDEN'); END IF;
    IF _rid IS NULL OR _max < 1 THEN RETURN core.res('INPUT_INVALID'); END IF;
    IF EXISTS (SELECT 1 FROM runtime.task_events e JOIN runtime.tasks t ON t.id = e.task_id
               WHERE e.request_id = _rid AND e.op = 'claim' AND t.plugin_id = plug) THEN
        RETURN core.res('REPLAY', jsonb_build_object('tasks', (
            SELECT coalesce(jsonb_agg(runtime.task_view(t, jb)), '[]') FROM runtime.task_events e
            JOIN runtime.tasks t ON t.id = e.task_id AND t.status = 'running' AND t.lease_epoch = e.lease_epoch
            JOIN runtime.jobs jb ON jb.id = t.job_id
            WHERE e.request_id = _rid AND e.op = 'claim' AND t.plugin_id = plug)));
    END IF;
    IF NOT EXISTS (SELECT 1 FROM core.plugins WHERE id = plug AND version = ANY (_versions) AND status = 'registered') THEN
        RETURN core.res('PLUGIN_DISABLED');
    END IF;
    FOR c IN
        SELECT x.id, x.job_id FROM (
            SELECT t.id, t.job_id, t.ready_at,
                   row_number() OVER (PARTITION BY t.tenant_id, t.project_id ORDER BY t.ready_at) AS rn   -- R11 fairness
            FROM runtime.tasks t JOIN runtime.jobs jb ON jb.id = t.job_id
            JOIN runtime.pipeline_stages ps ON (ps.plugin_id, ps.pipeline_key, ps.version, ps.stage_key)
                                             = (jb.plugin_id, jb.pipeline_key, jb.pipeline_version, t.stage_key)
            LEFT JOIN runtime.resource_state rs ON rs.resource_key = ps.resource_key
            WHERE t.plugin_id = plug AND t.status IN ('ready', 'retry_wait') AND t.ready_at <= now()
              AND jb.status = 'running' AND jb.plugin_version = ANY (_versions) AND coalesce(rs.state, 'closed') <> 'open'
              AND NOT runtime.stopping(jb.id)) x
        ORDER BY x.rn, x.ready_at LIMIT _max * 4
    LOOP
        EXIT WHEN jsonb_array_length(out) >= _max;
        BEGIN
            SELECT * INTO j FROM runtime.jobs WHERE id = c.job_id FOR SHARE NOWAIT;
        EXCEPTION WHEN lock_not_available THEN CONTINUE;
        END;
        CONTINUE WHEN runtime.stopping(j.id);
        SELECT * INTO tk FROM runtime.tasks WHERE id = c.id AND status IN ('ready', 'retry_wait') AND ready_at <= now()
            FOR UPDATE SKIP LOCKED;
        CONTINUE WHEN NOT FOUND;
        IF tk.attempt >= runtime.nval(j.policy_snapshot, 'runtime.max_attempts', 3) THEN
            PERFORM runtime.fail_terminal(tk.id, 'core.attempts_exhausted');
            CONTINUE;
        END IF;
        SELECT ps.kind INTO skind FROM runtime.pipeline_stages ps
            WHERE (ps.plugin_id, ps.pipeline_key, ps.version, ps.stage_key) = (j.plugin_id, j.pipeline_key, j.pipeline_version, tk.stage_key);
        UPDATE runtime.tasks SET status = 'running', lease_epoch = lease_epoch + 1, lease_token = gen_random_uuid(),
               lease_until = now() + runtime.ival(j.policy_snapshot, 'runtime.lease', '120 seconds'), attempt = attempt + 1,
               deadline_at = now() + runtime.ival(j.policy_snapshot, 'runtime.deadline.' || skind, '1 hour')
        WHERE id = tk.id RETURNING * INTO tk;
        INSERT INTO runtime.task_events (task_id, request_id, op, lease_epoch, outcome) VALUES (tk.id, _rid, 'claim', tk.lease_epoch, 'OK');
        UPDATE runtime.job_stages SET status = 'running' WHERE job_id = tk.job_id AND stage_key = tk.stage_key AND status = 'ready';
        out := out || jsonb_build_array(runtime.task_view(tk, j));
    END LOOP;
    RETURN core.res(CASE WHEN jsonb_array_length(out) = 0 THEN 'EMPTY' ELSE 'OK' END, jsonb_build_object('tasks', out));
END $$;

-- W2 (heartbeats are not stored as task events)
CREATE FUNCTION runtime.heartbeat(_task uuid, _token uuid, _epoch bigint, _checkpoint text DEFAULT NULL,
                                  _extend boolean DEFAULT false) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE g jsonb; tk runtime.tasks; j runtime.jobs; skind text; ext int;
BEGIN
    g := runtime.guard(_task, _token, _epoch, NULL); IF g IS NOT NULL THEN RETURN g; END IF;
    SELECT * INTO tk FROM runtime.tasks WHERE id = _task;
    SELECT * INTO j FROM runtime.jobs WHERE id = tk.job_id;
    IF runtime.stopping(j.id) THEN RETURN core.res('STOP'); END IF;
    IF tk.deadline_at < now() THEN RETURN core.res('DEADLINE'); END IF;
    ext := coalesce((tk.payload->>'_extensions')::int, 0);
    IF _extend AND ext < runtime.nval(j.policy_snapshot, 'runtime.deadline_extension_max', 3) THEN
        SELECT ps.kind INTO skind FROM runtime.pipeline_stages ps
            WHERE (ps.plugin_id, ps.pipeline_key, ps.version, ps.stage_key) = (j.plugin_id, j.pipeline_key, j.pipeline_version, tk.stage_key);
        tk.deadline_at := tk.deadline_at + runtime.ival(j.policy_snapshot, 'runtime.deadline.' || skind, '1 hour');
        ext := ext + 1;
    END IF;
    UPDATE runtime.tasks SET lease_until = now() + runtime.ival(j.policy_snapshot, 'runtime.lease', '120 seconds'),
           deadline_at = tk.deadline_at,
           payload = payload || jsonb_build_object('_extensions', ext) || coalesce(jsonb_build_object('checkpoint', _checkpoint), '{}')
    WHERE id = _task RETURNING * INTO tk;
    RETURN core.res('EXTENDED', jsonb_build_object('lease_until', tk.lease_until, 'deadline_at', tk.deadline_at));
END $$;

-- W3
CREATE FUNCTION runtime.complete(_task uuid, _token uuid, _epoch bigint, _result jsonb, _rid uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE g jsonb; tk runtime.tasks; res jsonb := core.res('OK');
BEGIN
    IF _rid IS NULL THEN RETURN core.res('INPUT_INVALID'); END IF;
    g := runtime.guard(_task, _token, _epoch, _rid); IF g IS NOT NULL THEN RETURN g; END IF;
    UPDATE runtime.tasks SET status = 'succeeded', resolution = 'ok', lease_token = NULL, lease_until = NULL
        WHERE id = _task RETURNING * INTO tk;
    INSERT INTO runtime.task_events (task_id, request_id, op, lease_epoch, outcome, detail)
        VALUES (_task, _rid, 'complete', tk.lease_epoch, 'OK', jsonb_build_object('result', res, 'output', _result));
    PERFORM runtime.resolve(_task);                                -- a stopping job never unblocks further stages
    RETURN res;
END $$;

-- W4: retryability from the registry; health samples while the breaker is open are not charged (§33).
CREATE FUNCTION runtime.fail(_task uuid, _token uuid, _epoch bigint, _code text, _detail jsonb, _rid uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE g jsonb; tk runtime.tasks; j runtime.jobs; spec jsonb; rkey text; res jsonb; unch int; charged boolean := true;
BEGIN
    IF _rid IS NULL THEN RETURN core.res('INPUT_INVALID'); END IF;
    g := runtime.guard(_task, _token, _epoch, _rid); IF g IS NOT NULL THEN RETURN g; END IF;
    SELECT * INTO tk FROM runtime.tasks WHERE id = _task;
    SELECT * INTO j FROM runtime.jobs WHERE id = tk.job_id;
    SELECT k.spec INTO spec FROM core.registry_keys k
        WHERE k.item_type = 'error_code' AND k.key = _code AND k.owner IN ('core', tk.plugin_id);
    IF spec IS NULL THEN
        res := core.res('UNKNOWN_ERROR_CODE');
        PERFORM runtime.fail_terminal(_task, 'core.unknown_error_code', jsonb_build_object('reported', _code), _rid, res);
        RETURN res;
    END IF;
    IF runtime.stopping(j.id) THEN
        res := core.res('CANCELED');
        PERFORM runtime.cancel_task(_task, 'fail', _rid, res);
        RETURN res;
    END IF;
    SELECT ps.resource_key INTO rkey FROM runtime.pipeline_stages ps
        WHERE (ps.plugin_id, ps.pipeline_key, ps.version, ps.stage_key) = (j.plugin_id, j.pipeline_key, j.pipeline_version, tk.stage_key);
    IF (spec->>'health_sample')::boolean AND (SELECT state FROM runtime.resource_state WHERE resource_key = rkey) = 'open' THEN
        SELECT count(*) INTO unch FROM runtime.task_events WHERE task_id = _task AND detail ? 'uncharged';
        charged := unch >= runtime.nval(j.policy_snapshot, 'runtime.max_uncharged', 20);
    END IF;
    IF (spec->>'retryable')::boolean AND (NOT charged OR tk.attempt < runtime.nval(j.policy_snapshot, 'runtime.max_attempts', 3)) THEN
        res := core.res('RETRY_SCHEDULED');
        UPDATE runtime.tasks SET status = 'retry_wait', lease_token = NULL, lease_until = NULL,
               attempt = attempt - CASE WHEN charged THEN 0 ELSE 1 END,
               ready_at = now() + least(runtime.ival(j.policy_snapshot, 'runtime.backoff_base', '30 seconds') * power(2, greatest(tk.attempt - 1, 0)),
                                        runtime.ival(j.policy_snapshot, 'runtime.backoff_max', '15 minutes'))
        WHERE id = _task;
        INSERT INTO runtime.task_events (task_id, request_id, op, lease_epoch, outcome, resource_key, error_code, detail)
            VALUES (_task, _rid, 'fail', tk.lease_epoch, 'RETRY_SCHEDULED',
                    CASE WHEN (spec->>'health_sample')::boolean THEN rkey END, _code,
                    jsonb_build_object('result', res, 'detail', _detail)
                    || CASE WHEN charged THEN '{}'::jsonb ELSE '{"uncharged": true}' END);
        RETURN res;
    END IF;
    res := core.res('FAILED');
    PERFORM runtime.fail_terminal(_task, _code, jsonb_build_object('detail', _detail), _rid, res);
    RETURN res;
END $$;

-- W5
CREATE FUNCTION runtime.split(_task uuid, _token uuid, _epoch bigint, _children jsonb, _rid uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE g jsonb; tk runtime.tasks; j runtime.jobs; ps runtime.pipeline_stages; res jsonb; kids jsonb;
BEGIN
    IF _rid IS NULL OR jsonb_typeof(_children) <> 'array' OR jsonb_array_length(_children) = 0 THEN
        RETURN core.res('INPUT_INVALID');
    END IF;
    g := runtime.guard(_task, _token, _epoch, _rid); IF g IS NOT NULL THEN RETURN g; END IF;
    SELECT * INTO tk FROM runtime.tasks WHERE id = _task;
    SELECT * INTO j FROM runtime.jobs WHERE id = tk.job_id;
    SELECT * INTO ps FROM runtime.pipeline_stages s
        WHERE (s.plugin_id, s.pipeline_key, s.version, s.stage_key) = (j.plugin_id, j.pipeline_key, j.pipeline_version, tk.stage_key);
    IF runtime.stopping(j.id) THEN
        res := core.res('JOB_STOPPING');
        PERFORM runtime.cancel_task(_task, 'split', _rid, res);
        RETURN res;
    END IF;
    IF ps.kind <> 'item' OR ps.split_max_items IS NULL OR tk.depth >= ps.split_max_depth
       OR jsonb_array_length(_children) > ps.split_max_items THEN
        RETURN core.res('SPLIT_LIMIT');
    END IF;
    UPDATE runtime.tasks SET status = 'split', lease_token = NULL, lease_until = NULL WHERE id = _task;
    WITH ins AS (
        INSERT INTO runtime.tasks (job_id, stage_key, plugin_id, tenant_id, project_id, parent_id, child_key, depth, scope, payload, status)
        SELECT tk.job_id, tk.stage_key, tk.plugin_id, tk.tenant_id, tk.project_id, tk.id, c->>'key', tk.depth + 1,
               coalesce(c->'scope', '{}'), coalesce(c->'payload', '{}'), 'ready'
        FROM jsonb_array_elements(_children) c RETURNING id, child_key)
    SELECT jsonb_agg(jsonb_build_object('key', child_key, 'task_id', id) ORDER BY child_key) INTO kids FROM ins;
    res := core.res('SPLIT', jsonb_build_object('children', kids));
    INSERT INTO runtime.task_events (task_id, request_id, op, lease_epoch, outcome, detail)
        VALUES (_task, _rid, 'split', tk.lease_epoch, 'SPLIT', jsonb_build_object('result', res));
    RETURN res;
END $$;

-- ---------- maintenance ----------
-- M2: stopping jobs with no running task become terminal; pending work canceled; staged rows discarded (I-K4).
CREATE FUNCTION runtime.finish_stopped_jobs(_only uuid DEFAULT NULL) RETURNS int
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE j runtime.jobs; stop text; n int := 0;
BEGIN
    FOR j IN SELECT * FROM runtime.jobs jb WHERE jb.status = 'running' AND (_only IS NULL OR jb.id = _only)
               AND (jb.stop_requested IS NOT NULL OR EXISTS (SELECT 1 FROM runtime.job_stages s WHERE s.job_id = jb.id AND s.status = 'failed'))
             ORDER BY jb.id FOR UPDATE SKIP LOCKED LOOP
        CONTINUE WHEN EXISTS (SELECT 1 FROM runtime.tasks WHERE job_id = j.id AND status = 'running');
        stop := coalesce(j.stop_requested, 'failure');
        UPDATE runtime.tasks SET status = 'canceled', resolution = 'canceled' WHERE job_id = j.id AND status IN ('ready', 'retry_wait');
        UPDATE runtime.tasks SET resolution = 'canceled' WHERE job_id = j.id AND status = 'split' AND resolution IS NULL;
        UPDATE runtime.job_stages SET status = 'canceled' WHERE job_id = j.id AND status NOT IN ('succeeded', 'failed', 'canceled');
        DELETE FROM knowledge.candidate_keys WHERE job_id = j.id;
        DELETE FROM knowledge.unit_versions WHERE candidate_job = j.id AND valid_from_gen IS NULL;
        UPDATE runtime.jobs SET stop_requested = stop, finished_at = now(),
               status = CASE stop WHEN 'superseded' THEN 'superseded' WHEN 'failure' THEN 'failed' ELSE 'canceled' END,
               outcome = coalesce(outcome, CASE stop WHEN 'superseded' THEN 'SUPERSEDED' WHEN 'failure' THEN 'FAILED' ELSE 'CANCELED' END)
        WHERE id = j.id RETURNING * INTO j;
        PERFORM runtime.life(j, NULL, 'running', j.status);
        n := n + 1;
    END LOOP;
    RETURN n;
END $$;

-- M1: expired leases, task deadlines, job deadlines; then M2.
CREATE FUNCTION runtime.sweep(_max int DEFAULT 500) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE c record; j runtime.jobs; tk runtime.tasks; expired int := 0; failed int := 0; jobs int;
BEGIN
    FOR c IN SELECT id, job_id FROM runtime.tasks WHERE status = 'running' AND (lease_until < now() OR deadline_at < now())
             ORDER BY job_id LIMIT _max LOOP
        SELECT * INTO j FROM runtime.jobs WHERE id = c.job_id FOR SHARE;
        SELECT * INTO tk FROM runtime.tasks WHERE id = c.id AND status = 'running'
            AND (lease_until < now() OR deadline_at < now()) FOR UPDATE SKIP LOCKED;
        CONTINUE WHEN NOT FOUND;
        IF tk.deadline_at < now() THEN
            PERFORM runtime.fail_terminal(tk.id, 'core.deadline_exceeded'); failed := failed + 1;
        ELSIF tk.attempt >= runtime.nval(j.policy_snapshot, 'runtime.max_attempts', 3) THEN
            PERFORM runtime.fail_terminal(tk.id, 'core.attempts_exhausted'); failed := failed + 1;
        ELSE
            UPDATE runtime.tasks SET status = 'ready', lease_token = NULL, lease_until = NULL, ready_at = now() WHERE id = tk.id;
            INSERT INTO runtime.task_events (task_id, op, lease_epoch, outcome) VALUES (tk.id, 'expire', tk.lease_epoch, 'EXPIRED');
            expired := expired + 1;
        END IF;
    END LOOP;
    UPDATE runtime.jobs SET stop_requested = 'failure', outcome = 'core.deadline_exceeded'
    WHERE status = 'running' AND stop_requested IS NULL
      AND created_at + runtime.ival(policy_snapshot, 'runtime.job_deadline', '24 hours') < now();
    jobs := runtime.finish_stopped_jobs();
    RETURN jsonb_build_object('expired', expired, 'failed', failed, 'finished_jobs', jobs);
END $$;

-- ---------- user contracts (api) ----------
-- U1
CREATE FUNCTION runtime.get_job(_p uuid, _job uuid) RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
    SELECT coalesce((
        SELECT core.res('OK', jsonb_build_object('job_id', j.id, 'status', j.status, 'outcome', j.outcome, 'seq', j.seq,
            'stop_requested', j.stop_requested, 'pipeline', j.plugin_id || '.' || j.pipeline_key || '@' || j.pipeline_version,
            'created_at', j.created_at, 'finished_at', j.finished_at,
            'stages', (SELECT jsonb_object_agg(stage_key, status) FROM runtime.job_stages WHERE job_id = j.id),
            'tasks', (SELECT jsonb_object_agg(status, n) FROM (SELECT status, count(*) n FROM runtime.tasks WHERE job_id = j.id GROUP BY 1) x)))
        FROM runtime.jobs j WHERE j.id = _job AND core.authorize(_p, j.project_id, 1) IS NOT NULL), core.res('FORBIDDEN')) $$;

-- U2
CREATE FUNCTION runtime.cancel_job(_p uuid, _job uuid, _rid uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE a jsonb := jsonb_build_object('job', _job); r jsonb; j runtime.jobs;
BEGIN
    SELECT * INTO j FROM runtime.jobs WHERE id = _job;
    IF NOT FOUND OR core.authorize(_p, j.project_id, CASE WHEN j.created_by = _p THEN 2 ELSE 3 END) IS NULL THEN
        RETURN core.res('FORBIDDEN');
    END IF;
    r := core.req_check(_p, _rid, 'cancel_job', a); IF r IS NOT NULL THEN RETURN r; END IF;
    SELECT * INTO j FROM runtime.jobs WHERE id = _job FOR UPDATE;
    IF j.finished_at IS NOT NULL THEN RETURN core.res('ALREADY_TERMINAL', jsonb_build_object('status', j.status)); END IF;
    IF j.status = 'queued' THEN
        UPDATE runtime.jobs SET status = 'canceled', stop_requested = 'cancel', outcome = 'CANCELED', finished_at = now()
            WHERE id = _job RETURNING * INTO j;
        PERFORM runtime.life(j, NULL, 'queued', 'canceled');
    ELSE
        UPDATE runtime.jobs SET stop_requested = coalesce(stop_requested, 'cancel') WHERE id = _job;
        PERFORM runtime.finish_stopped_jobs(_job);
    END IF;
    PERFORM core.audit_write(_p, 'cancel_job', 'job:' || _job);
    RETURN core.req_save(_p, _rid, 'cancel_job', a, core.res('OK'));
END $$;

-- U3 (original pipeline version and policy snapshot; fence_seq inherited)
CREATE FUNCTION runtime.retry_job(_p uuid, _job uuid, _rid uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE a jsonb := jsonb_build_object('job', _job); r jsonb; o runtime.jobs; j runtime.jobs; mode text; latest uuid; n int;
BEGIN
    SELECT * INTO o FROM runtime.jobs WHERE id = _job;
    IF NOT FOUND OR core.authorize(_p, o.project_id, 3) IS NULL THEN RETURN core.res('FORBIDDEN'); END IF;
    IF o.finished_at IS NULL OR o.status = 'succeeded' THEN RETURN core.res('NOT_RETRYABLE', jsonb_build_object('status', o.status)); END IF;
    SELECT publish_mode INTO mode FROM runtime.pipelines WHERE (plugin_id, key, version) = (o.plugin_id, o.pipeline_key, o.pipeline_version);
    IF mode = 'full' AND o.source_id IS NOT NULL THEN
        SELECT id INTO latest FROM core.source_revisions WHERE source_id = o.source_id ORDER BY created_at DESC, id DESC LIMIT 1;
        IF latest IS DISTINCT FROM o.source_revision_id THEN RETURN core.res('REVISION_STALE'); END IF;
    END IF;
    IF o.fence_seq < (SELECT fence_floor FROM knowledge.collections WHERE id = o.collection_id) THEN
        RETURN core.res('RETRY_TOO_OLD');
    END IF;
    SELECT count(*) INTO n FROM runtime.jobs WHERE retry_of IS NOT NULL AND collection_id = o.collection_id
        AND pipeline_key = o.pipeline_key AND source_revision_id IS NOT DISTINCT FROM o.source_revision_id
        AND source_id IS NOT DISTINCT FROM o.source_id AND fence_seq = o.fence_seq;
    IF n >= runtime.nval(o.policy_snapshot, 'runtime.retry_cap', 3) THEN RETURN core.res('RETRY_LIMIT'); END IF;
    r := core.req_check(_p, _rid, 'retry_job', a); IF r IS NOT NULL THEN RETURN r; END IF;
    INSERT INTO runtime.jobs (tenant_id, project_id, collection_id, source_id, source_revision_id, plugin_id, plugin_version,
                              pipeline_key, pipeline_version, pipeline_hash, policy_snapshot, fingerprint, force, retry_of,
                              request_id, fence_seq, status, created_by)
    VALUES (o.tenant_id, o.project_id, o.collection_id, o.source_id, o.source_revision_id, o.plugin_id, o.plugin_version,
            o.pipeline_key, o.pipeline_version, o.pipeline_hash, o.policy_snapshot, o.fingerprint, true, o.id,
            _rid, o.fence_seq, 'queued', _p) RETURNING * INTO j;
    PERFORM runtime.life(j, NULL, NULL, 'queued', jsonb_build_object('retry_of', o.id));
    PERFORM core.audit_write(_p, 'retry_job', 'job:' || j.id, a);
    RETURN core.req_save(_p, _rid, 'retry_job', a, core.res('CREATED', jsonb_build_object('job_id', j.id, 'seq', j.seq)));
END $$;
