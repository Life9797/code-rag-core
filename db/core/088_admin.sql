-- Admin page reads (§43) and the OIDC identity lookup (P-P1, P-P5 "human OIDC later": Keycloak ready, off by default).

-- Who am I: platform admin flag and the tenants the principal belongs to (all tenants for a platform admin).
CREATE FUNCTION core.admin_overview(_p uuid) RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
    SELECT core.res('OK', jsonb_build_object(
        'principal', (SELECT jsonb_build_object('id', x.id, 'name', x.display_name, 'kind', x.kind) FROM core.principals x WHERE x.id = _p),
        'platform_admin', core.is_platform_admin(_p),
        'tenants', coalesce((SELECT jsonb_agg(jsonb_build_object('id', t.id, 'name', t.name, 'status', t.status,
                        'role', CASE WHEN core.is_platform_admin(_p) THEN 4 ELSE core.tenant_role(_p, t.id) END) ORDER BY t.name)
                    FROM core.tenants t WHERE core.is_platform_admin(_p) OR core.tenant_role(_p, t.id) > 0), '[]'))) $$;

-- Tenant admin view: members (with identities and service clients) and projects.
CREATE FUNCTION core.tenant_detail(_p uuid, _tenant uuid) RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
    SELECT CASE WHEN NOT core.is_platform_admin(_p) AND core.tenant_role(_p, _tenant) < 4 THEN core.res('FORBIDDEN') ELSE
        core.res('OK', jsonb_build_object(
            'members', coalesce((SELECT jsonb_agg(jsonb_build_object('principal_id', x.id, 'name', x.display_name, 'kind', x.kind,
                    'status', x.status, 'role', m.role,
                    'identities', (SELECT coalesce(jsonb_agg(jsonb_build_object('issuer', e.issuer, 'subject', e.subject)), '[]')
                                   FROM core.external_identities e WHERE e.principal_id = x.id),
                    'clients', (SELECT coalesce(jsonb_agg(jsonb_build_object('client_id', c.id, 'status', c.status, 'expires_at', c.expires_at)), '[]')
                                FROM core.service_clients c WHERE c.principal_id = x.id)) ORDER BY x.kind, x.display_name)
                FROM core.tenant_members m JOIN core.principals x ON x.id = m.principal_id WHERE m.tenant_id = _tenant), '[]'),
            'projects', coalesce((SELECT jsonb_agg(jsonb_build_object('id', pr.id, 'name', pr.name, 'state', pr.state,
                    'delete_after', pr.delete_after) ORDER BY pr.name) FROM core.projects pr WHERE pr.tenant_id = _tenant), '[]'))) END $$;

-- Project view (reader+): members, plugins with on/off, project policies with the settable keys, webhooks (operator+).
CREATE FUNCTION core.project_detail(_p uuid, _project uuid) RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
    SELECT CASE WHEN core.authorize(_p, _project, 1) IS NULL THEN core.res('FORBIDDEN') ELSE
        core.res('OK', jsonb_build_object(
            'project', (SELECT jsonb_build_object('id', pr.id, 'tenant_id', pr.tenant_id, 'name', pr.name, 'state', pr.state,
                               'role', core.project_role(_p, pr.id)) FROM core.projects pr WHERE pr.id = _project),
            'members', coalesce((SELECT jsonb_agg(jsonb_build_object('principal_id', x.id, 'name', x.display_name, 'kind', x.kind,
                    'role', m.role) ORDER BY x.display_name)
                FROM core.project_members m JOIN core.principals x ON x.id = m.principal_id WHERE m.project_id = _project), '[]'),
            'plugins', coalesce((SELECT jsonb_agg(jsonb_build_object('id', p.id, 'version', p.version,
                    'enabled', coalesce(pp.enabled, false)) ORDER BY p.id)
                FROM (SELECT DISTINCT ON (id) id, version FROM core.plugins WHERE status = 'registered' ORDER BY id, registered_at DESC) p
                LEFT JOIN core.project_plugins pp ON pp.project_id = _project AND pp.plugin_id = p.id), '[]'),
            'policies', coalesce((SELECT jsonb_agg(jsonb_build_object('key', k.key, 'owner', k.owner, 'spec', k.spec,
                    'value', po.value, 'set_at', po.set_at,
                    'effective', core.policy_value(k.key, ARRAY[_project::text, (SELECT tenant_id::text FROM core.projects WHERE id = _project)]))
                    ORDER BY k.key)
                FROM core.registry_keys k LEFT JOIN core.policies po ON po.scope = 'project' AND po.scope_ref = _project::text AND po.key = k.key
                WHERE k.item_type = 'policy_key'), '[]'),
            'webhooks', CASE WHEN core.authorize(_p, _project, 3) IS NULL THEN NULL ELSE coalesce((SELECT jsonb_agg(jsonb_build_object(
                    'id', w.id, 'url', w.url, 'event_types', w.event_types, 'status', w.status)) FROM core.webhook_subscriptions w
                WHERE w.project_id = _project), '[]') END)) END $$;

-- OIDC (Keycloak): (issuer, subject) of a verified external token -> active principal; NULL = not linked yet.
CREATE FUNCTION core.principal_for_identity(_issuer text, _subject text) RETURNS uuid
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
    SELECT e.principal_id FROM core.external_identities e JOIN core.principals x ON x.id = e.principal_id AND x.status = 'active'
    WHERE e.issuer = _issuer AND e.subject = _subject $$;
