-- User API reads (M4): readable projects, source access check before files are written (upload connector).
CREATE FUNCTION core.list_projects(_p uuid) RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
    SELECT core.res('OK', jsonb_build_object('projects', coalesce(jsonb_agg(jsonb_build_object(
        'id', pr.id, 'tenant_id', pr.tenant_id, 'name', pr.name, 'state', pr.state, 'role', core.project_role(_p, pr.id))
        ORDER BY pr.name), '[]')))
    FROM core.projects pr WHERE core.project_role(_p, pr.id) >= 1 $$;

CREATE FUNCTION core.source_info(_p uuid, _source uuid, _min_role int) RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
    SELECT coalesce((SELECT core.res('OK', jsonb_build_object('project_id', s.project_id, 'connector', s.connector,
                         'external_id', s.external_id))
                     FROM core.sources s WHERE s.id = _source AND core.authorize(_p, s.project_id, _min_role) IS NOT NULL),
                    core.res('FORBIDDEN')) $$;

-- Latest jobs of a project (optionally one source), newest first; reader.
CREATE FUNCTION runtime.list_jobs(_p uuid, _project uuid, _source uuid, _limit int) RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
    SELECT CASE WHEN core.authorize(_p, _project, 1) IS NULL THEN core.res('FORBIDDEN') ELSE
        core.res('OK', jsonb_build_object('jobs', coalesce((SELECT jsonb_agg(x ORDER BY x->>'created_at' DESC) FROM (
            SELECT jsonb_build_object('job_id', j.id, 'source_id', j.source_id, 'status', j.status, 'outcome', j.outcome,
                   'pipeline', j.plugin_id || '.' || j.pipeline_key, 'created_at', j.created_at, 'finished_at', j.finished_at) x
            FROM runtime.jobs j WHERE j.project_id = _project AND (_source IS NULL OR j.source_id = _source)
            ORDER BY j.created_at DESC LIMIT least(coalesce(_limit, 20), 100)) y), '[]'))) END $$;

-- Worker search (eval, §35): the job's project with the job creator's rights; caller must hold the task lease.
CREATE FUNCTION knowledge.task_search(_task uuid, _token uuid, _epoch bigint, _query text, _qvecs jsonb, _filters jsonb, _k int)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE e jsonb; j runtime.jobs;
BEGIN
    e := knowledge.lease_err(_task, _token, _epoch); IF e IS NOT NULL THEN RETURN e; END IF;
    SELECT jb.* INTO j FROM runtime.jobs jb JOIN runtime.tasks t ON t.job_id = jb.id WHERE t.id = _task;
    RETURN knowledge.search(j.created_by, ARRAY[j.project_id], NULL, _query, _qvecs, _filters, _k);
END $$;
