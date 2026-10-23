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
