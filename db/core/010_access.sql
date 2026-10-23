-- Access layer helpers (P-P2, P-P3, I-S3), replay store (§26 F4), audit insert (P-P6).
-- Internal: owned by core_owner, no grants; called only from SECURITY DEFINER contracts.

CREATE FUNCTION core.res(outcome text, extra jsonb DEFAULT '{}') RETURNS jsonb
LANGUAGE sql IMMUTABLE SET search_path = pg_catalog, pg_temp
AS $$ SELECT jsonb_build_object('outcome', outcome) || extra $$;

CREATE FUNCTION core.is_platform_admin(p uuid) RETURNS boolean
LANGUAGE sql STABLE SET search_path = pg_catalog, pg_temp AS $$
    SELECT EXISTS (SELECT 1 FROM core.platform_admins a JOIN core.principals x ON x.id = a.principal_id
                   WHERE a.principal_id = p AND x.status = 'active') $$;

-- Tenant role of an active principal in an active tenant; 0 = none.
CREATE FUNCTION core.tenant_role(p uuid, t uuid) RETURNS smallint
LANGUAGE sql STABLE SET search_path = pg_catalog, pg_temp AS $$
    SELECT coalesce((SELECT m.role FROM core.tenant_members m
                     JOIN core.principals x ON x.id = m.principal_id AND x.status = 'active'
                     JOIN core.tenants n ON n.id = m.tenant_id AND n.status = 'active'
                     WHERE m.tenant_id = t AND m.principal_id = p), 0)::smallint $$;

-- Effective project role (P-P2): max(project role, admin if tenant admin); other tenant roles do not carry over.
CREATE FUNCTION core.project_role(p uuid, proj uuid) RETURNS smallint
LANGUAGE sql STABLE SET search_path = pg_catalog, pg_temp AS $$
    SELECT coalesce((
        SELECT greatest(coalesce(pm.role, 0), CASE WHEN core.tenant_role(p, pr.tenant_id) = 4 THEN 4 ELSE 0 END)
        FROM core.projects pr
        LEFT JOIN core.project_members pm ON pm.tenant_id = pr.tenant_id AND pm.project_id = pr.id AND pm.principal_id = p
        WHERE pr.id = proj AND core.tenant_role(p, pr.tenant_id) > 0), 0)::smallint $$;

-- P-P3: returns the project's tenant when the principal holds min_role, else NULL (unknown project = NULL too).
CREATE FUNCTION core.authorize(p uuid, proj uuid, min_role int) RETURNS uuid
LANGUAGE sql STABLE SET search_path = pg_catalog, pg_temp AS $$
    SELECT pr.tenant_id FROM core.projects pr WHERE pr.id = proj AND core.project_role(p, proj) >= min_role $$;

CREATE FUNCTION core.args_hash(args jsonb) RETURNS text
LANGUAGE sql IMMUTABLE SET search_path = pg_catalog, pg_temp
AS $$ SELECT encode(sha256(convert_to(args::text, 'UTF8')), 'hex') $$;

-- Replay check: stored result, REQUEST_ID_REUSED, or NULL (new request). Serializes the same id with an xact lock.
CREATE FUNCTION core.req_check(p uuid, rid uuid, op text, args jsonb) RETURNS jsonb
LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $$
DECLARE r core.requests;
BEGIN
    IF rid IS NULL THEN RETURN core.res('INPUT_INVALID', '{"field": "request_id"}'); END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended(coalesce(p::text, '') || rid::text, 0));
    SELECT * INTO r FROM core.requests WHERE principal_id = p AND request_id = rid;
    IF NOT FOUND THEN RETURN NULL; END IF;
    IF r.op <> op OR r.args_hash <> core.args_hash(args) THEN RETURN core.res('REQUEST_ID_REUSED'); END IF;
    RETURN r.result || '{"replay": true}';
END $$;

CREATE FUNCTION core.req_save(p uuid, rid uuid, op text, args jsonb, result jsonb) RETURNS jsonb
LANGUAGE sql SET search_path = pg_catalog, pg_temp AS $$
    INSERT INTO core.requests (principal_id, request_id, op, args_hash, result)
    VALUES (p, rid, op, core.args_hash(args), result) RETURNING result $$;

CREATE FUNCTION core.audit_write(actor uuid, action text, object text, detail jsonb DEFAULT '{}') RETURNS void
LANGUAGE sql SET search_path = pg_catalog, pg_temp AS $$
    INSERT INTO core.audit (actor, action, object, detail) VALUES (actor, action, object, detail) $$;
