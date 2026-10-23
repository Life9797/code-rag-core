-- Project delete / restore (U8), purge (G2), source revisions (P-P13), snapshot retention (G4),
-- non-publishing jobs (all stages done -> succeeded).

-- U8: tenant admin; jobs stop with 'purge'; search and user calls refuse the project from now on.
CREATE FUNCTION core.delete_project(_p uuid, _project uuid, _rid uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE a jsonb := jsonb_build_object('project', _project); r jsonb; pr core.projects; j runtime.jobs;
BEGIN
    SELECT * INTO pr FROM core.projects WHERE id = _project;
    IF NOT FOUND OR core.tenant_role(_p, pr.tenant_id) < 4 THEN RETURN core.res('FORBIDDEN'); END IF;
    r := core.req_check(_p, _rid, 'delete_project', a); IF r IS NOT NULL THEN RETURN r; END IF;
    SELECT * INTO pr FROM core.projects WHERE id = _project FOR NO KEY UPDATE;
    IF pr.state <> 'active' THEN RETURN core.res('ALREADY_DELETING'); END IF;
    UPDATE core.projects SET state = 'deleting',
           delete_after = now() + coalesce((core.policy_value('core.purge_grace', ARRAY[pr.tenant_id::text]) #>> '{}')::interval, '7 days')
        WHERE id = _project RETURNING * INTO pr;
    FOR j IN UPDATE runtime.jobs SET status = 'canceled', stop_requested = 'purge', outcome = 'CANCELED', finished_at = now()
             WHERE project_id = _project AND status = 'queued' RETURNING * LOOP
        PERFORM runtime.life(j, NULL, 'queued', 'canceled');
    END LOOP;
    UPDATE runtime.jobs SET stop_requested = 'purge' WHERE project_id = _project AND finished_at IS NULL AND stop_requested IS NULL;
    INSERT INTO core.outbox_events (tenant_id, project_id, type, payload)
        VALUES (pr.tenant_id, pr.id, 'core.project_deleting', jsonb_build_object('project', pr.id, 'delete_after', pr.delete_after));
    PERFORM core.audit_write(_p, 'delete_project', 'project:' || _project, a);
    RETURN core.req_save(_p, _rid, 'delete_project', a, core.res('OK', jsonb_build_object('delete_after', pr.delete_after)));
END $$;

CREATE FUNCTION core.restore_project(_p uuid, _project uuid, _rid uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE a jsonb := jsonb_build_object('project', _project); r jsonb; pr core.projects;
BEGIN
    SELECT * INTO pr FROM core.projects WHERE id = _project;
    IF NOT FOUND OR core.tenant_role(_p, pr.tenant_id) < 4 THEN RETURN core.res('FORBIDDEN'); END IF;
    r := core.req_check(_p, _rid, 'restore_project', a); IF r IS NOT NULL THEN RETURN r; END IF;
    SELECT * INTO pr FROM core.projects WHERE id = _project FOR NO KEY UPDATE;
    IF pr.state <> 'deleting' OR pr.delete_after <= now() THEN RETURN core.res('NOT_RESTORABLE', jsonb_build_object('state', pr.state)); END IF;
    IF EXISTS (SELECT 1 FROM core.projects WHERE tenant_id = pr.tenant_id AND name = pr.name AND state = 'active') THEN
        RETURN core.res('NAME_TAKEN');
    END IF;
    UPDATE core.projects SET state = 'active', delete_after = NULL WHERE id = _project;
    PERFORM core.audit_write(_p, 'restore_project', 'project:' || _project, a);
    RETURN core.req_save(_p, _rid, 'restore_project', a, core.res('OK'));
END $$;

-- G2 purge, one idempotent step per call (purge login); the maint runner loops until DONE and runs plugin hooks
-- (purge_project) with each plugin's own login in between, then calls purge_hook_done.
CREATE FUNCTION core.purge_step(_project uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE pr core.projects; hooks text[]; pj uuid[];
BEGIN
    SELECT * INTO pr FROM core.projects WHERE id = _project FOR NO KEY UPDATE;
    IF NOT FOUND THEN RETURN core.res('DONE'); END IF;
    IF pr.state = 'active' OR (pr.state = 'deleting' AND pr.delete_after > now()) THEN RETURN core.res('NOT_DUE'); END IF;
    IF pr.state = 'deleting' THEN
        UPDATE core.projects SET state = 'purging' WHERE id = _project;
        PERFORM core.audit_write(NULL, 'purge_start', 'project:' || _project);
    END IF;
    IF EXISTS (SELECT 1 FROM runtime.jobs WHERE project_id = _project AND finished_at IS NULL) THEN
        PERFORM runtime.finish_stopped_jobs(id) FROM runtime.jobs WHERE project_id = _project AND finished_at IS NULL;
        IF EXISTS (SELECT 1 FROM runtime.jobs WHERE project_id = _project AND finished_at IS NULL) THEN
            RETURN core.res('WAITING_TASKS');
        END IF;
    END IF;
    SELECT coalesce(array_agg(pp.plugin_id), '{}') INTO hooks FROM core.project_plugins pp
        JOIN core.plugins p ON p.id = pp.plugin_id AND p.status = 'registered' AND p.manifest #> '{hooks,purge_project}' IS NOT NULL
        WHERE pp.project_id = _project
          AND NOT EXISTS (SELECT 1 FROM core.audit a WHERE a.action = 'purge_hook_done' AND a.object = 'project:' || _project
                          AND a.detail->>'plugin' = pp.plugin_id);
    IF cardinality(hooks) > 0 THEN                                 -- plugins get their collection and job ids (they cannot read Core tables)
        RETURN core.res('RUN_HOOKS', jsonb_build_object('plugins', hooks,
            'collections', (SELECT coalesce(jsonb_object_agg(plugin_id, ids), '{}') FROM (SELECT plugin_id, jsonb_agg(id) ids
                            FROM knowledge.collections WHERE project_id = _project GROUP BY 1) x),
            'jobs', (SELECT coalesce(jsonb_object_agg(plugin_id, ids), '{}') FROM (SELECT plugin_id, jsonb_agg(id) ids
                     FROM runtime.jobs WHERE project_id = _project GROUP BY 1) y)));
    END IF;
    -- delete order follows foreign keys (no CASCADE except version -> chunks/links)
    SELECT coalesce(array_agg(id), '{}') INTO pj FROM runtime.jobs WHERE project_id = _project;
    DELETE FROM knowledge.candidate_keys WHERE job_id = ANY (pj);
    DELETE FROM knowledge.generation_pins WHERE collection_id IN (SELECT id FROM knowledge.collections WHERE project_id = _project);
    DELETE FROM knowledge.generations WHERE collection_id IN (SELECT id FROM knowledge.collections WHERE project_id = _project);
    DELETE FROM knowledge.unit_versions WHERE unit_id IN (SELECT id FROM knowledge.units WHERE project_id = _project);
    DELETE FROM knowledge.units WHERE project_id = _project;
    DELETE FROM knowledge.collection_sources WHERE project_id = _project;
    DELETE FROM knowledge.representation_values v WHERE v.tenant_id = pr.tenant_id
        AND NOT EXISTS (SELECT 1 FROM knowledge.representation_links l WHERE l.value_id = v.id);  -- shared values stay
    DELETE FROM runtime.task_events WHERE task_id IN (SELECT id FROM runtime.tasks WHERE job_id = ANY (pj));
    DELETE FROM runtime.tasks WHERE job_id = ANY (pj);                     -- one statement: parent FK checked at its end
    DELETE FROM runtime.job_stages WHERE job_id = ANY (pj);
    DELETE FROM runtime.job_items WHERE job_id = ANY (pj);
    DELETE FROM runtime.lifecycle_events WHERE job_id = ANY (pj);
    DELETE FROM runtime.schedule_runs WHERE job_id = ANY (pj)
        OR schedule_id IN (SELECT id FROM core.schedules WHERE project_id = _project);
    UPDATE runtime.jobs SET retry_of = NULL WHERE retry_of = ANY (pj);
    DELETE FROM runtime.jobs WHERE id = ANY (pj);
    DELETE FROM knowledge.collections WHERE project_id = _project;
    DELETE FROM core.schedules WHERE project_id = _project;
    DELETE FROM core.outbox_deliveries WHERE subscription_id IN (SELECT id FROM core.webhook_subscriptions WHERE project_id = _project);
    DELETE FROM core.webhook_subscriptions WHERE project_id = _project;
    DELETE FROM core.source_bindings WHERE project_id = _project;
    DELETE FROM core.source_revisions WHERE project_id = _project;      -- files: runner removes vol paths listed in the result
    DELETE FROM core.sources WHERE project_id = _project;
    DELETE FROM core.artifacts WHERE project_id = _project;
    DELETE FROM core.project_plugins WHERE project_id = _project;
    DELETE FROM core.project_members WHERE project_id = _project;
    DELETE FROM core.projects WHERE id = _project;
    PERFORM core.audit_write(NULL, 'purge_done', 'project:' || _project);
    RETURN core.res('DONE');
END $$;

CREATE FUNCTION core.purge_hook_done(_project uuid, _plugin text) RETURNS jsonb
LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
    SELECT core.audit_write(NULL, 'purge_hook_done', 'project:' || _project, jsonb_build_object('plugin', _plugin));
    SELECT core.res('OK') $$;

CREATE FUNCTION core.purge_due() RETURNS uuid[]
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
    SELECT coalesce(array_agg(id), '{}') FROM core.projects
    WHERE state = 'purging' OR (state = 'deleting' AND delete_after <= now()) $$;

-- P-P13: a revision row is written only after its snapshot and manifest are complete on disk (api / connector).
CREATE FUNCTION core.revision_create(_p uuid, _source uuid, _revision_key text, _manifest jsonb, _manifest_hash text,
                                     _storage_ref text, _input_complete boolean, _rid uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE a jsonb := jsonb_build_object('source', _source, 'revision_key', _revision_key, 'manifest_hash', _manifest_hash,
                                      'storage_ref', _storage_ref, 'input_complete', _input_complete);
        r jsonb; so core.sources; rid uuid;
BEGIN
    SELECT * INTO so FROM core.sources WHERE id = _source;
    IF NOT FOUND OR core.authorize(_p, so.project_id, 2) IS NULL THEN RETURN core.res('FORBIDDEN'); END IF;
    IF (SELECT state FROM core.projects WHERE id = so.project_id) <> 'active' THEN RETURN core.res('PROJECT_DELETING'); END IF;
    IF _storage_ref !~ '^(vol|ext)://' THEN RETURN core.res('INPUT_INVALID'); END IF;
    r := core.req_check(_p, _rid, 'revision_create', a); IF r IS NOT NULL THEN RETURN r; END IF;
    INSERT INTO core.source_revisions (tenant_id, project_id, source_id, revision_key, manifest_hash, manifest, storage_ref, input_complete)
        VALUES (so.tenant_id, so.project_id, so.id, _revision_key, _manifest_hash, _manifest, _storage_ref, _input_complete)
        ON CONFLICT (source_id, revision_key) DO NOTHING RETURNING id INTO rid;
    IF rid IS NULL THEN                                            -- revision_key is immutable: same key = same revision
        SELECT id INTO rid FROM core.source_revisions WHERE source_id = so.id AND revision_key = _revision_key;
        RETURN core.res('EXISTS', jsonb_build_object('id', rid));
    END IF;
    RETURN core.req_save(_p, _rid, 'revision_create', a, core.res('CREATED', jsonb_build_object('id', rid)));
END $$;

-- G4 (maint): keep the latest revision per source and revisions of non-terminal jobs; prune the rest after retention.
CREATE FUNCTION core.snapshot_retention(_max int DEFAULT 1000) RETURNS jsonb
LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
    WITH p AS (
        UPDATE core.source_revisions r SET storage_state = 'pruned'
        WHERE r.id IN (
            SELECT x.id FROM core.source_revisions x
            WHERE x.storage_state = 'present'
              AND x.created_at < now() - coalesce((core.policy_value('core.snapshot_retention', ARRAY[x.project_id::text, x.tenant_id::text]) #>> '{}')::interval, '30 days')
              AND x.id <> (SELECT y.id FROM core.source_revisions y WHERE y.source_id = x.source_id ORDER BY y.created_at DESC, y.id DESC LIMIT 1)
              AND NOT EXISTS (SELECT 1 FROM runtime.jobs j WHERE j.source_revision_id = x.id AND j.finished_at IS NULL)
            LIMIT _max)
        RETURNING r.storage_ref)
    SELECT jsonb_build_object('pruned', coalesce(jsonb_agg(storage_ref), '[]')) FROM p $$;

-- Jobs without a publish stage (purge hooks, maintenance pipelines) succeed once every stage succeeded.
CREATE FUNCTION runtime.finish_done_jobs() RETURNS int
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE j runtime.jobs; n int := 0;
BEGIN
    FOR j IN SELECT * FROM runtime.jobs jb WHERE jb.status = 'running' AND jb.stop_requested IS NULL
               AND NOT EXISTS (SELECT 1 FROM runtime.job_stages s WHERE s.job_id = jb.id AND s.status <> 'succeeded')
               AND EXISTS (SELECT 1 FROM runtime.job_stages s WHERE s.job_id = jb.id)
             ORDER BY jb.id FOR UPDATE SKIP LOCKED LOOP
        UPDATE runtime.jobs SET status = 'succeeded', outcome = 'DONE', finished_at = now() WHERE id = j.id RETURNING * INTO j;
        PERFORM runtime.life(j, NULL, 'running', 'succeeded');
        n := n + 1;
    END LOOP;
    RETURN n;
END $$;

-- M3 over every collection (maint loop); busy collections are skipped and retried next round.
CREATE FUNCTION knowledge.gc_all() RETURNS jsonb
LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
    SELECT coalesce(jsonb_object_agg(id, knowledge.gc(id)), '{}') FROM knowledge.collections $$;
