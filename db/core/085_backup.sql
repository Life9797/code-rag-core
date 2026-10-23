-- §38 backup mode: while set, maintenance deletes no volume files (snapshot retention paused); alert after backup_mode_max.
INSERT INTO core.registry_keys (item_type, key, owner, spec) VALUES ('policy_key', 'core.backup_mode', 'core', '{"type": "bool"}');

CREATE FUNCTION core.backup_mode() RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE since timestamptz;
BEGIN
    SELECT set_at INTO since FROM core.policies WHERE scope = 'global' AND key = 'core.backup_mode' AND value = 'true';
    IF since IS NULL THEN RETURN false; END IF;
    IF since + coalesce((core.policy_value('core.backup_mode_max') #>> '{}')::interval, '6 hours') < now() THEN
        INSERT INTO core.alerts (code, object, severity) VALUES ('core.backup_mode_long', 'backup', 'warn')
            ON CONFLICT (code, object) WHERE cleared_at IS NULL DO UPDATE SET last_at = now(), count = core.alerts.count + 1;
    END IF;
    RETURN true;
END $$;

-- superuser only (coreadm backup); no grant.
CREATE FUNCTION core.backup_mode_set(_on boolean) RETURNS void
LANGUAGE sql SET search_path = pg_catalog, pg_temp AS $$
    INSERT INTO core.policies (scope, scope_ref, key, value) VALUES ('global', '', 'core.backup_mode', to_jsonb(_on))
    ON CONFLICT (scope, scope_ref, key) DO UPDATE SET value = EXCLUDED.value, set_at = now();
    UPDATE core.alerts SET cleared_at = now() WHERE code = 'core.backup_mode_long' AND cleared_at IS NULL AND NOT _on $$;
