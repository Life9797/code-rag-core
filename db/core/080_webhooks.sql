-- G1 outbound webhooks (§23 G1, U7, M4 deliver): project-scoped subscriptions, per-subscription delivery rows,
-- HMAC secret encrypted by the api with the host key (core.secrets owner core.webhook), sent by maintenance only.
INSERT INTO core.registry_keys (item_type, key, owner, spec) VALUES
    ('policy_key', 'core.webhook_max_attempts', 'core', '{"type": "int", "min": 1}'),
    ('error_code', 'core.webhook_dead', 'core', '{"retryable": false, "health_sample": false}');

CREATE FUNCTION core.webhook_allowed(_url text, _tenant uuid, _project uuid) RETURNS boolean
LANGUAGE sql STABLE SET search_path = pg_catalog, pg_temp AS $$
    SELECT _url ~ '^https://[^/@]+(/|$)' AND EXISTS (
        SELECT 1 FROM regexp_split_to_table(coalesce(core.policy_value('core.webhook_allowlist',
                          ARRAY[_project::text, _tenant::text]) #>> '{}', ''), '[\s,]+') p
        WHERE p <> '' AND left(_url, length(p)) = p) $$;

CREATE FUNCTION core.webhook_subscribe(_p uuid, _project uuid, _url text, _types text[], _key_id text, _ciphertext bytea, _rid uuid)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE t uuid; r jsonb; sid uuid := gen_random_uuid();
        a jsonb := jsonb_build_object('project', _project, 'url', _url, 'types', _types);
BEGIN
    t := core.authorize(_p, _project, 3); IF t IS NULL THEN RETURN core.res('FORBIDDEN'); END IF;
    IF NOT core.webhook_allowed(_url, t, _project) THEN RETURN core.res('DESTINATION_NOT_ALLOWED'); END IF;
    IF cardinality(_types) = 0 THEN RETURN core.res('INPUT_INVALID'); END IF;
    r := core.req_check(_p, _rid, 'webhook_subscribe', a); IF r IS NOT NULL THEN RETURN r; END IF;
    INSERT INTO core.secrets (tenant_id, owner, name, version, key_id, ciphertext, status)
        VALUES (t, 'core.webhook', sid::text, 1, _key_id, _ciphertext, 'active');
    INSERT INTO core.webhook_subscriptions (tenant_id, project_id, id, url, event_types, secret_name, created_by)
        VALUES (t, _project, sid, _url, _types, sid::text, _p);
    PERFORM core.audit_write(_p, 'webhook_subscribe', 'subscription:' || sid);
    RETURN core.req_save(_p, _rid, 'webhook_subscribe', a, core.res('CREATED', jsonb_build_object('id', sid)));
END $$;

CREATE FUNCTION core.webhook_unsubscribe(_p uuid, _sub uuid, _rid uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE s core.webhook_subscriptions;
BEGIN
    SELECT * INTO s FROM core.webhook_subscriptions WHERE id = _sub;
    IF NOT FOUND OR core.authorize(_p, s.project_id, 3) IS NULL THEN RETURN core.res('FORBIDDEN'); END IF;
    UPDATE core.webhook_subscriptions SET status = 'disabled' WHERE id = _sub;
    UPDATE core.outbox_deliveries SET state = 'dead' WHERE subscription_id = _sub AND state = 'pending';
    PERFORM core.audit_write(_p, 'webhook_unsubscribe', 'subscription:' || _sub);
    RETURN core.res('OK');
END $$;

-- maint: new outbox events -> delivery rows (cursor 'webhooks'), then due deliveries with what the sender needs.
-- Membership is checked again here: a creator who lost reader access ends the delivery (G1).
CREATE FUNCTION core.webhook_due(_max int DEFAULT 100) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE cur bigint; top bigint;
BEGIN
    INSERT INTO core.event_consumers (name) VALUES ('webhooks') ON CONFLICT DO NOTHING;
    SELECT last_id INTO cur FROM core.event_consumers WHERE name = 'webhooks' FOR UPDATE;
    SELECT coalesce(max(id), cur) INTO top FROM core.outbox_events WHERE id > cur;
    INSERT INTO core.outbox_deliveries (event_id, subscription_id, state)
    SELECT e.id, s.id, 'pending' FROM core.outbox_events e JOIN core.webhook_subscriptions s
        ON s.project_id = e.project_id AND s.status = 'active' AND e.type = ANY (s.event_types)
    WHERE e.id > cur AND e.id <= top ON CONFLICT DO NOTHING;
    UPDATE core.event_consumers SET last_id = top WHERE name = 'webhooks';
    UPDATE core.outbox_deliveries d SET state = 'dead' FROM core.webhook_subscriptions s
        WHERE d.subscription_id = s.id AND d.state = 'pending'
          AND (core.project_role(s.created_by, s.project_id) < 1 OR NOT core.webhook_allowed(s.url, s.tenant_id, s.project_id));
    RETURN coalesce((SELECT jsonb_agg(jsonb_build_object('event_id', d.event_id, 'subscription_id', d.subscription_id, 'url', s.url,
               'type', e.type, 'payload', e.payload, 'created_at', e.created_at, 'key_id', k.key_id,
               'secret', encode(k.ciphertext, 'base64'), 'attempts', d.attempts))
        FROM (SELECT * FROM core.outbox_deliveries WHERE state = 'pending' AND next_at <= now() ORDER BY next_at LIMIT _max) d
        JOIN core.webhook_subscriptions s ON s.id = d.subscription_id JOIN core.outbox_events e ON e.id = d.event_id
        JOIN core.secrets k ON k.tenant_id = s.tenant_id AND k.owner = 'core.webhook' AND k.name = s.secret_name AND k.status = 'active'), '[]');
END $$;

-- maint: record one send (send then mark: at-least-once). Failures back off; after the budget -> dead + alert.
CREATE FUNCTION core.webhook_mark(_event bigint, _sub uuid, _ok boolean, _status int) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE d core.outbox_deliveries; mx int;
BEGIN
    SELECT * INTO d FROM core.outbox_deliveries WHERE event_id = _event AND subscription_id = _sub AND state = 'pending' FOR UPDATE;
    IF NOT FOUND THEN RETURN core.res('NOT_FOUND'); END IF;
    mx := coalesce((core.policy_value('core.webhook_max_attempts') #>> '{}')::int, 8);
    IF _ok THEN
        UPDATE core.outbox_deliveries SET state = 'delivered', attempts = attempts + 1, last_status = _status
            WHERE event_id = _event AND subscription_id = _sub;
        RETURN core.res('DELIVERED');
    END IF;
    IF d.attempts + 1 >= mx THEN
        UPDATE core.outbox_deliveries SET state = 'dead', attempts = attempts + 1, last_status = _status
            WHERE event_id = _event AND subscription_id = _sub;
        INSERT INTO core.alerts (code, object, severity) VALUES ('core.webhook_dead', 'subscription:' || _sub, 'warn')
            ON CONFLICT (code, object) WHERE cleared_at IS NULL DO UPDATE SET last_at = now(), count = core.alerts.count + 1;
        RETURN core.res('DEAD');
    END IF;
    UPDATE core.outbox_deliveries SET attempts = attempts + 1, last_status = _status,
           next_at = now() + least(interval '30 seconds' * power(2, d.attempts), interval '1 hour')
        WHERE event_id = _event AND subscription_id = _sub;
    RETURN core.res('RETRY');
END $$;
