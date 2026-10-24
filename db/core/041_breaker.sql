-- Circuit breaker per resource (P-R8, §33 refund replacement). Samples = task events with resource_key:
-- health-sample fails (W4) and completes of stages bound to a resource. Evaluated by maintenance.

CREATE OR REPLACE FUNCTION runtime.complete_sample() RETURNS trigger
LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $$
BEGIN                                                              -- completes of resource-bound stages count as successes
    IF NEW.op = 'complete' AND NEW.resource_key IS NULL THEN
        SELECT ps.resource_key INTO NEW.resource_key
        FROM runtime.tasks t JOIN runtime.jobs j ON j.id = t.job_id
        JOIN runtime.pipeline_stages ps ON (ps.plugin_id, ps.pipeline_key, ps.version, ps.stage_key)
                                         = (j.plugin_id, j.pipeline_key, j.pipeline_version, t.stage_key)
        WHERE t.id = NEW.task_id;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER trg_task_events_sample BEFORE INSERT ON runtime.task_events FOR EACH ROW EXECUTE FUNCTION runtime.complete_sample();

-- closed -> open when the last `window` samples hold >= min_sample and fail ratio >= ratio;
-- open -> half_open after cooldown (claims resume); half_open -> closed on a success sample, -> open on a fail.
CREATE FUNCTION runtime.breaker_eval() RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE r record; win int := coalesce((core.policy_value('runtime.breaker.window') #>> '{}')::int, 50);
        ratio numeric := coalesce((core.policy_value('runtime.breaker.ratio') #>> '{}')::numeric, 0.5);
        mins int := coalesce((core.policy_value('runtime.breaker.min_sample') #>> '{}')::int, 10);
        cool interval := coalesce((core.policy_value('runtime.breaker.cooldown') #>> '{}')::interval, '2 minutes');
        out jsonb := '{}'; st text; last_op text;
BEGIN
    FOR r IN SELECT k.key AS rk, coalesce(s.state, 'closed') AS state, s.opened_at,
                    (SELECT count(*) FROM (SELECT op FROM runtime.task_events e WHERE e.resource_key = k.key ORDER BY e.id DESC LIMIT win) x) AS n,
                    (SELECT count(*) FILTER (WHERE op <> 'complete') FROM (SELECT op FROM runtime.task_events e WHERE e.resource_key = k.key ORDER BY e.id DESC LIMIT win) x) AS bad
             FROM core.registry_keys k LEFT JOIN runtime.resource_state s ON s.resource_key = k.key
             WHERE k.item_type = 'resource' LOOP
        st := r.state;
        SELECT e.op INTO last_op FROM runtime.task_events e WHERE e.resource_key = r.rk ORDER BY e.id DESC LIMIT 1;
        IF st = 'closed' AND r.n >= mins AND r.bad::numeric / r.n >= ratio THEN st := 'open';
        ELSIF st = 'open' AND r.opened_at + cool <= now() THEN st := 'half_open';
        ELSIF st = 'half_open' AND last_op = 'complete' THEN st := 'closed';
        ELSIF st = 'half_open' AND last_op IN ('fail', 'terminal_fail')
              AND (SELECT at FROM runtime.task_events WHERE resource_key = r.rk ORDER BY id DESC LIMIT 1)
                  > r.opened_at + cool THEN st := 'open';
        END IF;
        IF st <> r.state OR NOT EXISTS (SELECT 1 FROM runtime.resource_state WHERE resource_key = r.rk) THEN
            INSERT INTO runtime.resource_state (resource_key, state, opened_at) VALUES (r.rk, st, CASE WHEN st = 'open' THEN now() END)
            ON CONFLICT (resource_key) DO UPDATE SET state = EXCLUDED.state,
                opened_at = CASE WHEN EXCLUDED.state = 'open' THEN now() ELSE runtime.resource_state.opened_at END;
            IF st = 'open' THEN
                INSERT INTO core.alerts (code, object, severity) VALUES ('core.breaker_open', r.rk, 'warn')
                ON CONFLICT (code, object) WHERE cleared_at IS NULL DO UPDATE SET last_at = now(), count = core.alerts.count + 1;
            ELSIF st = 'closed' THEN
                UPDATE core.alerts SET cleared_at = now() WHERE code = 'core.breaker_open' AND object = r.rk AND cleared_at IS NULL;
            END IF;
        END IF;
        out := out || jsonb_build_object(r.rk, st);
    END LOOP;
    RETURN out;
END $$;
