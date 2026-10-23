-- Plugin registration (MG-04, P-P7, R4, R9) and policies (MG-08, §18).

-- Called by init only (superuser; no grant). The manifest was validated by coderag_core.manifest (schema + DAG).
CREATE FUNCTION core.plugin_register(_m jsonb, _manifest_hash text, _dir_hash text) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE pid text := _m->>'id'; ver text := _m->>'version'; old core.plugins; it record; pl jsonb; st jsonb; ph text;
BEGIN
    SELECT * INTO old FROM core.plugins WHERE id = pid AND version = ver;
    IF FOUND THEN
        IF old.manifest_hash = _manifest_hash AND old.dir_hash = _dir_hash THEN RETURN core.res('EXISTS'); END IF;
        RETURN core.res('PLUGIN_VERSION_CONFLICT');
    END IF;
    INSERT INTO core.plugins (id, version, contract, manifest, manifest_hash, dir_hash)
        VALUES (pid, ver, (_m->>'contract')::int, _m, _manifest_hash, _dir_hash);
    FOR it IN
        SELECT t.item_type, CASE WHEN jsonb_typeof(e) = 'string' THEN e #>> '{}' ELSE e->>'key' END AS k,
               CASE WHEN jsonb_typeof(e) = 'string' THEN '{}'::jsonb ELSE e END AS spec
        FROM (VALUES ('handlers', 'handler'), ('collections', 'collection'), ('unit_kinds', 'unit_kind'),
                     ('representation_kinds', 'representation_kind'), ('policy_keys', 'policy_key'), ('resources', 'resource'),
                     ('permissions', 'permission'), ('schedules', 'schedule'), ('produces', 'format'),
                     ('error_codes', 'error_code'), ('exclusion_reasons', 'exclusion_reason'), ('connectors', 'connector')) t(field, item_type)
        CROSS JOIN LATERAL jsonb_array_elements(coalesce(_m->t.field, '[]')) e
        UNION ALL
        SELECT 'event', e #>> '{}', '{}'::jsonb FROM jsonb_array_elements(coalesce(_m#>'{events,emits}', '[]')) e
    LOOP
        it.k := CASE WHEN it.item_type = 'handler' THEN pid || '.' || (it.spec->>'name')
                     WHEN it.item_type = 'format' THEN it.k ELSE pid || '.' || it.k END;
        IF EXISTS (SELECT 1 FROM core.registry_keys WHERE item_type = it.item_type AND key = it.k AND owner <> pid) THEN
            RAISE EXCEPTION 'KEY_OWNED: % % belongs to another owner', it.item_type, it.k;
        END IF;
        INSERT INTO core.registry_keys (item_type, key, owner, spec) VALUES (it.item_type, it.k, pid, it.spec)
            ON CONFLICT (item_type, key) DO UPDATE SET spec = EXCLUDED.spec;
        INSERT INTO core.registry_items (plugin_id, plugin_version, item_type, key, spec) VALUES (pid, ver, it.item_type, it.k, it.spec);
    END LOOP;
    FOR pl IN SELECT * FROM jsonb_array_elements(coalesce(_m->'pipelines', '[]')) LOOP
        ph := encode(sha256(convert_to(pl::text, 'UTF8')), 'hex');
        IF EXISTS (SELECT 1 FROM runtime.pipelines WHERE plugin_id = pid AND key = pl->>'key' AND version = (pl->>'version')::int) THEN
            IF (SELECT pipeline_hash FROM runtime.pipelines
                WHERE plugin_id = pid AND key = pl->>'key' AND version = (pl->>'version')::int) <> ph THEN
                RAISE EXCEPTION 'PIPELINE_VERSION_CONFLICT: %.% v%', pid, pl->>'key', pl->>'version';
            END IF;
            CONTINUE;                                              -- same pipeline version kept from an older plugin version
        END IF;
        INSERT INTO runtime.pipelines (plugin_id, plugin_version, key, version, pipeline_hash, collection_key, publish_mode, inputs, unit_kinds)
        VALUES (pid, ver, pl->>'key', (pl->>'version')::int, ph, pid || '.' || (pl->>'collection'), pl->>'publish_mode',
                ARRAY(SELECT jsonb_array_elements_text(coalesce(pl->'inputs', '[]'))),
                ARRAY(SELECT pid || '.' || (u->>'key') FROM jsonb_array_elements(coalesce(_m->'unit_kinds', '[]')) u));
        FOR st IN SELECT * FROM jsonb_array_elements(pl->'stages') LOOP
            INSERT INTO runtime.pipeline_stages (plugin_id, pipeline_key, version, stage_key, kind, handler, depends_on,
                                                 resource_key, split_max_items, split_max_depth)
            VALUES (pid, pl->>'key', (pl->>'version')::int, st->>'key', st->>'kind',
                    CASE WHEN st ? 'handler' THEN pid || '.' || (st->>'handler') END,
                    ARRAY(SELECT jsonb_array_elements_text(coalesce(st->'depends_on', '[]'))),
                    st->>'resource', (st#>>'{split,max_items}')::int, (st#>>'{split,max_depth}')::int);
        END LOOP;
    END LOOP;
    PERFORM core.audit_write(NULL, 'plugin_register', 'plugin:' || pid || '@' || ver,
                             jsonb_build_object('manifest_hash', _manifest_hash, 'dir_hash', _dir_hash));
    RETURN core.res('REGISTERED');
END $$;

-- Effective policy value: stage, then project, tenant, plugin (plugin-wide default), then global. NULL = UNSET.
CREATE FUNCTION core.policy_value(_key text, _refs text[] DEFAULT '{}') RETURNS jsonb
LANGUAGE sql STABLE SET search_path = pg_catalog, pg_temp AS $$
    SELECT value FROM core.policies
    WHERE key = _key AND (scope = 'global' OR scope_ref = ANY (_refs))
    ORDER BY CASE scope WHEN 'stage' THEN 1 WHEN 'project' THEN 2 WHEN 'tenant' THEN 3 WHEN 'plugin' THEN 4 ELSE 5 END
    LIMIT 1 $$;

-- §18 relations over the effective values seen from scope_ref; returns the first broken rule or NULL.
CREATE FUNCTION core.policy_violation(_refs text[]) RETURNS text
LANGUAGE plpgsql STABLE SET search_path = pg_catalog, pg_temp AS $$
DECLARE
    rule record;
BEGIN
    FOR rule IN SELECT * FROM (VALUES
        ('runtime.lease',                   '>',  2, 'runtime.heartbeat'),
        ('runtime.sweeper_interval',        '<',  1, 'runtime.lease'),
        ('runtime.backoff_max',             '>=', 1, 'runtime.backoff_base'),
        ('runtime.claim_statement_timeout', '<',  1, 'runtime.lease'),
        ('runtime.claim_lock_timeout',      '<',  1, 'runtime.claim_statement_timeout'),
        ('runtime.deadline.item',           '>',  1, 'runtime.lease'),
        ('runtime.deadline.control',        '>',  1, 'runtime.lease'),
        ('runtime.deadline.publish',        '>',  1, 'runtime.lease'),
        ('runtime.deadline.approval',       '>',  1, 'runtime.lease'),
        ('runtime.job_deadline',            '>',  1, 'runtime.lease'),
        ('runtime.breaker.min_sample',      '<=', 1, 'runtime.breaker.window'),
        ('knowledge.gc.grace',              '>=', 1, 'knowledge.pin_ttl'),
        ('knowledge.search.max_k',          '<=', 1, 'knowledge.search.candidate_k'),
        ('knowledge.search.ef_search',      '>=', 1, 'knowledge.search.max_k'),
        ('core.token_ttl',                  '<=', 1, 'core.signing_key_retire_delay'),
        ('core.snapshot_retention',         '>',  1, 'runtime.job_deadline')) r(a, op, f, b)
    LOOP
        DECLARE va jsonb := core.policy_value(rule.a, _refs); vb jsonb := core.policy_value(rule.b, _refs); x numeric; y numeric;
        BEGIN
            CONTINUE WHEN va IS NULL OR vb IS NULL;
            IF jsonb_typeof(va) = 'string' THEN
                x := extract(epoch FROM (va #>> '{}')::interval); y := extract(epoch FROM (vb #>> '{}')::interval);
            ELSE x := (va #>> '{}')::numeric; y := (vb #>> '{}')::numeric; END IF;
            y := y * rule.f;
            IF NOT (CASE rule.op WHEN '>' THEN x > y WHEN '<' THEN x < y WHEN '>=' THEN x >= y ELSE x <= y END) THEN
                RETURN rule.a || ' ' || rule.op || ' ' || CASE WHEN rule.f <> 1 THEN rule.f || ' x ' ELSE '' END || rule.b;
            END IF;
        END;
    END LOOP;
    RETURN NULL;
END $$;

-- MG-08. global/plugin/stage: platform admin; tenant: tenant admin; project: project admin.
CREATE FUNCTION core.policy_set(_p uuid, _scope text, _ref text, _key text, _value jsonb, _rid uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE a jsonb := jsonb_build_object('scope', _scope, 'ref', _ref, 'key', _key, 'value', _value);
        r jsonb; spec jsonb; t text; n numeric; refs text[]; bad text;
BEGIN
    IF NOT (CASE _scope
        WHEN 'global'  THEN core.is_platform_admin(_p) AND coalesce(_ref, '') = ''
        WHEN 'tenant'  THEN _ref ~ '^[0-9a-f-]{36}$' AND core.tenant_role(_p, _ref::uuid) = 4
        WHEN 'project' THEN _ref ~ '^[0-9a-f-]{36}$' AND core.project_role(_p, _ref::uuid) = 4
        WHEN 'plugin'  THEN core.is_platform_admin(_p)
        WHEN 'stage'   THEN core.is_platform_admin(_p)
        ELSE false END) THEN RETURN core.res('FORBIDDEN'); END IF;
    SELECT k.spec INTO spec FROM core.registry_keys k WHERE k.item_type = 'policy_key' AND k.key = _key;
    IF NOT FOUND THEN RETURN core.res('UNKNOWN_KEY'); END IF;
    t := spec->>'type';
    IF _value IS NOT NULL AND _value <> 'null' THEN
        BEGIN
            IF t = 'interval' THEN n := extract(epoch FROM (_value #>> '{}')::interval);
                IF jsonb_typeof(_value) <> 'string' OR n < 0 THEN RAISE EXCEPTION 'bad'; END IF;
            ELSIF t IN ('int', 'number') THEN n := (_value #>> '{}')::numeric;
                IF jsonb_typeof(_value) <> 'number' OR (t = 'int' AND n <> trunc(n)) THEN RAISE EXCEPTION 'bad'; END IF;
                IF n < (spec->>'min')::numeric OR n > (spec->>'max')::numeric THEN RAISE EXCEPTION 'bad'; END IF;
            ELSIF t = 'bool' AND jsonb_typeof(_value) <> 'boolean' THEN RAISE EXCEPTION 'bad';
            ELSIF t = 'text' AND jsonb_typeof(_value) <> 'string' THEN RAISE EXCEPTION 'bad';
            END IF;
        EXCEPTION WHEN OTHERS THEN
            RETURN core.res('CONSTRAINT_VIOLATION', jsonb_build_object('rule', 'type ' || t || ' / bounds'));
        END;
    ELSE _value := NULL;
    END IF;
    r := core.req_check(_p, _rid, 'policy_set', a); IF r IS NOT NULL THEN RETURN r; END IF;
    INSERT INTO core.policies (scope, scope_ref, key, value, set_by) VALUES (_scope, coalesce(_ref, ''), _key, _value, _p)
        ON CONFLICT (scope, scope_ref, key) DO UPDATE SET value = EXCLUDED.value, set_by = EXCLUDED.set_by, set_at = now();
    refs := CASE WHEN _scope = 'global' THEN '{}' ELSE ARRAY[_ref] END;
    bad := core.policy_violation(refs);
    IF bad IS NULL AND _scope = 'global' THEN                     -- a global change must hold under every override too
        SELECT core.policy_violation(ARRAY[o.scope_ref]) INTO bad
        FROM (SELECT DISTINCT scope_ref FROM core.policies WHERE scope <> 'global') o
        WHERE core.policy_violation(ARRAY[o.scope_ref]) IS NOT NULL LIMIT 1;
    END IF;
    IF bad IS NOT NULL THEN RAISE EXCEPTION USING ERRCODE = 'P0001', MESSAGE = 'CONSTRAINT_VIOLATION', DETAIL = bad; END IF;
    PERFORM core.audit_write(_p, 'policy_set', _scope || ':' || coalesce(_ref, '') || ':' || _key, a);
    RETURN core.req_save(_p, _rid, 'policy_set', a, core.res('OK'));
EXCEPTION WHEN raise_exception THEN
    IF SQLERRM <> 'CONSTRAINT_VIOLATION' THEN RAISE; END IF;
    GET STACKED DIAGNOSTICS bad = PG_EXCEPTION_DETAIL;
    RETURN core.res('CONSTRAINT_VIOLATION', jsonb_build_object('rule', bad));   -- block rolled back: old value kept (MG-T3)
END $$;
