-- Management contracts MG-01..MG-10 (§34, §25). User functions: take principal + request_id, check access now,
-- replay through core.requests, audit row in the same transaction. Return jsonb {outcome, ...}.

-- MG-01
CREATE FUNCTION core.tenant_create(_p uuid, _name text, _rid uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE a jsonb := jsonb_build_object('name', _name); r jsonb; tid uuid;
BEGIN
    IF NOT core.is_platform_admin(_p) THEN RETURN core.res('FORBIDDEN'); END IF;
    r := core.req_check(_p, _rid, 'tenant_create', a); IF r IS NOT NULL THEN RETURN r; END IF;
    INSERT INTO core.tenants (name) VALUES (_name) ON CONFLICT DO NOTHING RETURNING id INTO tid;
    IF tid IS NULL THEN RETURN core.res('NAME_TAKEN'); END IF;
    PERFORM core.audit_write(_p, 'tenant_create', 'tenant:' || tid, a);
    RETURN core.req_save(_p, _rid, 'tenant_create', a, core.res('CREATED', jsonb_build_object('id', tid)));
END $$;

-- Principal in a tenant (tenant admin or platform admin; role <= caller's). Humans map by (issuer, subject).
CREATE FUNCTION core.principal_create(_p uuid, _tenant uuid, _kind text, _display text, _role int,
                                      _issuer text, _subject text, _rid uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE a jsonb := jsonb_build_object('tenant', _tenant, 'kind', _kind, 'display', _display, 'role', _role,
                                      'issuer', _issuer, 'subject', _subject);
        r jsonb; pid uuid; my int;
BEGIN
    my := CASE WHEN core.is_platform_admin(_p) THEN 4 ELSE core.tenant_role(_p, _tenant) END;
    IF my < 4 OR _role > my THEN RETURN core.res('FORBIDDEN'); END IF;
    IF _kind NOT IN ('human', 'service') OR _role NOT BETWEEN 1 AND 4 OR (_issuer IS NULL) <> (_subject IS NULL) THEN
        RETURN core.res('INPUT_INVALID');
    END IF;
    r := core.req_check(_p, _rid, 'principal_create', a); IF r IS NOT NULL THEN RETURN r; END IF;
    IF _issuer IS NOT NULL AND EXISTS (SELECT 1 FROM core.external_identities WHERE issuer = _issuer AND subject = _subject) THEN
        RETURN core.res('EXISTS');
    END IF;
    INSERT INTO core.principals (kind, display_name) VALUES (_kind, _display) RETURNING id INTO pid;
    IF _issuer IS NOT NULL THEN
        INSERT INTO core.external_identities (issuer, subject, principal_id) VALUES (_issuer, _subject, pid);
    END IF;
    INSERT INTO core.tenant_members (tenant_id, principal_id, role) VALUES (_tenant, pid, _role);
    PERFORM core.audit_write(_p, 'principal_create', 'principal:' || pid, a);
    RETURN core.req_save(_p, _rid, 'principal_create', a, core.res('CREATED', jsonb_build_object('id', pid)));
END $$;

-- MG-02
CREATE FUNCTION core.project_create(_p uuid, _tenant uuid, _name text, _rid uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE a jsonb := jsonb_build_object('tenant', _tenant, 'name', _name); r jsonb; pid uuid;
BEGIN
    IF core.tenant_role(_p, _tenant) < 3 THEN RETURN core.res('FORBIDDEN'); END IF;
    r := core.req_check(_p, _rid, 'project_create', a); IF r IS NOT NULL THEN RETURN r; END IF;
    INSERT INTO core.projects (tenant_id, name, created_by) VALUES (_tenant, _name, _p)
        ON CONFLICT DO NOTHING RETURNING id INTO pid;
    IF pid IS NULL THEN RETURN core.res('NAME_TAKEN'); END IF;
    INSERT INTO core.project_members (tenant_id, project_id, principal_id, role) VALUES (_tenant, pid, _p, 4);
    PERFORM core.audit_write(_p, 'project_create', 'project:' || pid, a);
    RETURN core.req_save(_p, _rid, 'project_create', a, core.res('CREATED', jsonb_build_object('id', pid)));
END $$;

-- MG-03 set (role 1..4) or remove (role NULL). _project NULL = tenant scope.
CREATE FUNCTION core.member_change(_p uuid, _tenant uuid, _project uuid, _target uuid, _role int, _rid uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE a jsonb := jsonb_build_object('tenant', _tenant, 'project', _project, 'target', _target, 'role', _role);
        op text := CASE WHEN _role IS NULL THEN 'member_remove' ELSE 'member_set' END;
        r jsonb; my int; cur int; admins int;
BEGIN
    IF _role IS NOT NULL AND _role NOT BETWEEN 1 AND 4 THEN RETURN core.res('INPUT_INVALID'); END IF;
    IF _project IS NULL THEN
        my := CASE WHEN core.is_platform_admin(_p) THEN 4 ELSE core.tenant_role(_p, _tenant) END;
    ELSE
        IF NOT EXISTS (SELECT 1 FROM core.projects WHERE tenant_id = _tenant AND id = _project) THEN
            RETURN core.res('FORBIDDEN');
        END IF;
        my := core.project_role(_p, _project);
    END IF;
    IF my < 4 OR coalesce(_role, 0) > my THEN RETURN core.res('FORBIDDEN'); END IF;
    r := core.req_check(_p, _rid, op, a); IF r IS NOT NULL THEN RETURN r; END IF;
    IF _project IS NULL THEN
        PERFORM 1 FROM core.tenants WHERE id = _tenant FOR NO KEY UPDATE;          -- serializes LAST_ADMIN checks
        SELECT role INTO cur FROM core.tenant_members WHERE tenant_id = _tenant AND principal_id = _target;
        SELECT count(*) INTO admins FROM core.tenant_members WHERE tenant_id = _tenant AND role = 4;
    ELSE
        PERFORM 1 FROM core.projects WHERE id = _project FOR NO KEY UPDATE;
        SELECT role INTO cur FROM core.project_members WHERE project_id = _project AND principal_id = _target;
        SELECT count(*) INTO admins FROM core.project_members WHERE project_id = _project AND role = 4;
    END IF;
    IF cur = 4 AND coalesce(_role, 0) < 4 AND admins <= 1 THEN RETURN core.res('LAST_ADMIN'); END IF;
    IF _role IS NULL THEN
        IF cur IS NULL THEN RETURN core.res('NOT_FOUND'); END IF;
        IF _project IS NULL THEN DELETE FROM core.tenant_members WHERE tenant_id = _tenant AND principal_id = _target;
        ELSE DELETE FROM core.project_members WHERE project_id = _project AND principal_id = _target; END IF;
    ELSIF _project IS NULL THEN
        IF NOT EXISTS (SELECT 1 FROM core.principals WHERE id = _target) THEN RETURN core.res('NOT_FOUND'); END IF;
        INSERT INTO core.tenant_members (tenant_id, principal_id, role) VALUES (_tenant, _target, _role)
            ON CONFLICT (tenant_id, principal_id) DO UPDATE SET role = EXCLUDED.role;
    ELSE
        IF NOT EXISTS (SELECT 1 FROM core.tenant_members WHERE tenant_id = _tenant AND principal_id = _target) THEN
            RETURN core.res('NOT_TENANT_MEMBER');
        END IF;
        INSERT INTO core.project_members (tenant_id, project_id, principal_id, role) VALUES (_tenant, _project, _target, _role)
            ON CONFLICT (tenant_id, project_id, principal_id) DO UPDATE SET role = EXCLUDED.role;
    END IF;
    PERFORM core.audit_write(_p, op, coalesce('project:' || _project, 'tenant:' || _tenant), a);
    RETURN core.req_save(_p, _rid, op, a, core.res('OK'));
END $$;

-- MG-04 (enable per project: project admin; disable a version: platform admin). Registration is in 030.
CREATE FUNCTION core.plugin_enable(_p uuid, _project uuid, _plugin text, _enabled boolean, _rid uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE a jsonb := jsonb_build_object('project', _project, 'plugin', _plugin, 'enabled', _enabled); r jsonb; t uuid;
BEGIN
    t := core.authorize(_p, _project, 4); IF t IS NULL THEN RETURN core.res('FORBIDDEN'); END IF;
    IF NOT EXISTS (SELECT 1 FROM core.plugins WHERE id = _plugin AND status = 'registered') THEN
        RETURN core.res('NOT_FOUND');
    END IF;
    r := core.req_check(_p, _rid, 'plugin_enable', a); IF r IS NOT NULL THEN RETURN r; END IF;
    INSERT INTO core.project_plugins (tenant_id, project_id, plugin_id, enabled) VALUES (t, _project, _plugin, _enabled)
        ON CONFLICT (tenant_id, project_id, plugin_id) DO UPDATE SET enabled = EXCLUDED.enabled;
    PERFORM core.audit_write(_p, 'plugin_enable', 'project:' || _project, a);
    RETURN core.req_save(_p, _rid, 'plugin_enable', a, core.res('OK'));
END $$;

CREATE FUNCTION core.plugin_disable(_p uuid, _plugin text, _version text, _rid uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE a jsonb := jsonb_build_object('plugin', _plugin, 'version', _version); r jsonb;
BEGIN
    IF NOT core.is_platform_admin(_p) THEN RETURN core.res('FORBIDDEN'); END IF;
    r := core.req_check(_p, _rid, 'plugin_disable', a); IF r IS NOT NULL THEN RETURN r; END IF;
    UPDATE core.plugins SET status = 'disabled' WHERE id = _plugin AND version = _version;
    IF NOT FOUND THEN RETURN core.res('NOT_FOUND'); END IF;
    PERFORM core.audit_write(_p, 'plugin_disable', 'plugin:' || _plugin || '@' || _version, a);
    RETURN core.req_save(_p, _rid, 'plugin_disable', a, core.res('OK'));
END $$;

-- MG-05: service principal + client in a tenant. The api generates the secret and passes only its argon2id hash.
CREATE FUNCTION core.service_client_create(_p uuid, _tenant uuid, _display text, _role int, _scopes text[],
                                           _audiences text[], _expires timestamptz, _secret_hash text, _rid uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE a jsonb := jsonb_build_object('tenant', _tenant, 'display', _display, 'role', _role, 'scopes', _scopes,
                                      'audiences', _audiences, 'expires', _expires);
        r jsonb; pid uuid; cid uuid;
BEGIN
    IF core.tenant_role(_p, _tenant) < 4 AND NOT core.is_platform_admin(_p) THEN RETURN core.res('FORBIDDEN'); END IF;
    -- worker scopes act as a plugin login across tenants (§28.1): platform admin only
    IF EXISTS (SELECT 1 FROM unnest(_scopes) s WHERE s LIKE 'runtime.%') AND NOT core.is_platform_admin(_p) THEN
        RETURN core.res('FORBIDDEN');
    END IF;
    IF _secret_hash !~ '^\$argon2id\$' OR _role NOT BETWEEN 1 AND 4 THEN RETURN core.res('INPUT_INVALID'); END IF;
    r := core.req_check(_p, _rid, 'service_client_create', a); IF r IS NOT NULL THEN RETURN r; END IF;
    INSERT INTO core.principals (kind, display_name) VALUES ('service', _display) RETURNING id INTO pid;
    INSERT INTO core.tenant_members (tenant_id, principal_id, role) VALUES (_tenant, pid, _role);
    INSERT INTO core.service_clients (principal_id, secret_hash, audiences, scopes, expires_at)
        VALUES (pid, _secret_hash, _audiences, _scopes, _expires) RETURNING id INTO cid;
    PERFORM core.audit_write(_p, 'service_client_create', 'client:' || cid, a);
    RETURN core.req_save(_p, _rid, 'service_client_create', a,
                         core.res('CREATED', jsonb_build_object('client_id', cid, 'principal_id', pid)));
END $$;

CREATE FUNCTION core.service_client_revoke(_p uuid, _client uuid, _rid uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE a jsonb := jsonb_build_object('client', _client); r jsonb;
BEGIN
    IF NOT core.is_platform_admin(_p) AND NOT EXISTS (
        SELECT 1 FROM core.service_clients c JOIN core.tenant_members m ON m.principal_id = c.principal_id
        WHERE c.id = _client AND core.tenant_role(_p, m.tenant_id) = 4) THEN RETURN core.res('FORBIDDEN'); END IF;
    r := core.req_check(_p, _rid, 'service_client_revoke', a); IF r IS NOT NULL THEN RETURN r; END IF;
    UPDATE core.service_clients SET status = 'revoked' WHERE id = _client;
    PERFORM core.audit_write(_p, 'service_client_revoke', 'client:' || _client, a);
    RETURN core.req_save(_p, _rid, 'service_client_revoke', a, core.res('OK'));
END $$;

-- MG-06 step 1: argon2 parameters + salt of the stored hash; unknown ids get a stable dummy of the same shape.
CREATE FUNCTION core.get_client_salt(_client uuid) RETURNS text
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
    SELECT coalesce(
        (SELECT regexp_replace(secret_hash, '\$[^$]*$', '') FROM core.service_clients WHERE id = _client),
        '$argon2id$v=19$m=65536,t=3,p=4$'
          || rtrim(encode(substring(sha256(convert_to('crc-dummy:' || coalesce(_client::text, ''), 'UTF8')) FROM 1 FOR 16), 'base64'), '=')) $$;

-- MG-06 step 2: compare the digest computed by the api; returns claims for the JWT or DENIED.
CREATE FUNCTION core.verify_and_issue(_client uuid, _digest text) RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
    SELECT coalesce((
        SELECT core.res('OK', jsonb_build_object('sub', c.principal_id, 'client', c.id, 'scopes', c.scopes, 'aud', c.audiences))
        FROM core.service_clients c JOIN core.principals x ON x.id = c.principal_id
        WHERE c.id = _client AND c.status = 'active' AND x.status = 'active'
          AND (c.expires_at IS NULL OR c.expires_at > now())
          AND substring(c.secret_hash FROM '\$([^$]*)$') = _digest), core.res('DENIED')) $$;

-- Checked on every request (immediate revocation, P-P4).
CREATE FUNCTION core.client_active(_client uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
    SELECT EXISTS (SELECT 1 FROM core.service_clients c JOIN core.principals x ON x.id = c.principal_id
                   WHERE c.id = _client AND c.status = 'active' AND x.status = 'active'
                     AND (c.expires_at IS NULL OR c.expires_at > now())) $$;

-- Keys that verify tokens: the active key and retiring keys until retired_at (= planned retire time).
CREATE FUNCTION core.jwks() RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
    SELECT coalesce(jsonb_agg(public_jwk || jsonb_build_object('kid', kid, 'alg', alg) ORDER BY created_at DESC), '[]')
    FROM core.signing_keys WHERE status = 'active' OR (status = 'retiring' AND retired_at > now()) $$;

CREATE FUNCTION core.signing_key_active() RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
    SELECT jsonb_build_object('kid', kid, 'private_ref', private_ref) FROM core.signing_keys WHERE status = 'active' $$;

-- MG-07: platform admin, or the maint login (_p NULL). Old active key verifies until now + retire delay.
CREATE FUNCTION core.signing_key_rotate(_p uuid, _kid text, _public_jwk jsonb, _private_ref text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE delay interval := coalesce((SELECT (value #>> '{}')::interval FROM core.policies
                                    WHERE scope = 'global' AND key = 'core.signing_key_retire_delay'), '24 hours');
BEGIN
    IF NOT (session_user = 'maint' OR core.is_platform_admin(_p)) THEN RETURN core.res('FORBIDDEN'); END IF;
    UPDATE core.signing_keys SET status = 'retired' WHERE status = 'retiring' AND retired_at <= now();
    UPDATE core.signing_keys SET status = 'retiring', retired_at = now() + delay WHERE status = 'active';
    INSERT INTO core.signing_keys (kid, alg, public_jwk, private_ref, status) VALUES (_kid, 'ES256', _public_jwk, _private_ref, 'active');
    PERFORM core.audit_write(_p, 'signing_key_rotate', 'kid:' || _kid);
    RETURN core.res('OK', jsonb_build_object('kid', _kid));
END $$;

-- MG-09: reader on the artifact's project; the api signs a short-lived path.
CREATE FUNCTION core.artifact_authorize_download(_p uuid, _artifact uuid) RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
    SELECT coalesce((SELECT core.res('OK', jsonb_build_object('storage_ref', a.storage_ref, 'sha256', a.sha256))
                     FROM core.artifacts a JOIN core.projects pr ON pr.id = a.project_id AND pr.state = 'active'
                     WHERE a.id = _artifact AND core.authorize(_p, a.project_id, 1) IS NOT NULL),
                    core.res('FORBIDDEN')) $$;

-- MG-10
CREATE FUNCTION core.source_create(_p uuid, _project uuid, _connector text, _external_id text, _refetchable boolean, _rid uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE a jsonb := jsonb_build_object('project', _project, 'connector', _connector, 'external_id', _external_id,
                                      'refetchable', _refetchable);
        r jsonb; t uuid; sid uuid;
BEGIN
    t := core.authorize(_p, _project, 2); IF t IS NULL THEN RETURN core.res('FORBIDDEN'); END IF;
    IF (SELECT state FROM core.projects WHERE id = _project) <> 'active' THEN RETURN core.res('PROJECT_DELETING'); END IF;
    IF NOT EXISTS (SELECT 1 FROM core.registry_keys WHERE item_type = 'connector' AND key = _connector) THEN
        RETURN core.res('NOT_REGISTERED');
    END IF;
    r := core.req_check(_p, _rid, 'source_create', a); IF r IS NOT NULL THEN RETURN r; END IF;
    INSERT INTO core.sources (tenant_id, project_id, connector, external_id, refetchable)
        VALUES (t, _project, _connector, _external_id, _refetchable) ON CONFLICT DO NOTHING RETURNING id INTO sid;
    IF sid IS NULL THEN
        SELECT id INTO sid FROM core.sources WHERE project_id = _project AND connector = _connector AND external_id = _external_id;
        RETURN core.res('EXISTS', jsonb_build_object('id', sid));
    END IF;
    PERFORM core.audit_write(_p, 'source_create', 'source:' || sid, a);
    RETURN core.req_save(_p, _rid, 'source_create', a, core.res('CREATED', jsonb_build_object('id', sid)));
END $$;

CREATE FUNCTION core.binding_set(_p uuid, _project uuid, _connector text, _external_ref text, _config jsonb, _rid uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE a jsonb := jsonb_build_object('project', _project, 'connector', _connector, 'external_ref', _external_ref, 'config', _config);
        r jsonb; t uuid; bid uuid;
BEGIN
    t := core.authorize(_p, _project, 3); IF t IS NULL THEN RETURN core.res('FORBIDDEN'); END IF;
    IF NOT EXISTS (SELECT 1 FROM core.registry_keys WHERE item_type = 'connector' AND key = _connector) THEN
        RETURN core.res('NOT_REGISTERED');
    END IF;
    r := core.req_check(_p, _rid, 'binding_set', a); IF r IS NOT NULL THEN RETURN r; END IF;
    INSERT INTO core.source_bindings (tenant_id, project_id, connector, external_ref, config)
        VALUES (t, _project, _connector, _external_ref, _config)
        ON CONFLICT (connector, external_ref, project_id) DO UPDATE SET config = EXCLUDED.config RETURNING id INTO bid;
    PERFORM core.audit_write(_p, 'binding_set', 'binding:' || bid, a);
    RETURN core.req_save(_p, _rid, 'binding_set', a, core.res('OK', jsonb_build_object('id', bid)));
END $$;
