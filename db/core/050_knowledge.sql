-- Knowledge Services (M2): stage writes W6 (P-C1..C3, G3), publish C3 (§24, §40), delete_unit C2, search U5 (§16),
-- pins U6, GC M3, vector indexes (P-K9). Lock order: collection -> collection_sources -> jobs -> units -> versions (§26).

-- core plugin row + core.delete pipeline (single Core publish stage), so explicit deletes are ordinary jobs.
INSERT INTO core.plugins (id, version, contract, manifest, manifest_hash, dir_hash)
VALUES ('core', '0.4.0', 1, '{"id": "core"}', 'core', 'core');
INSERT INTO runtime.pipelines (plugin_id, plugin_version, key, version, pipeline_hash, collection_key, publish_mode)
VALUES ('core', '0.4.0', 'delete', 1, 'core.delete@1', '*', 'delta');
INSERT INTO runtime.pipeline_stages (plugin_id, pipeline_key, version, stage_key, kind) VALUES ('core', 'delete', 1, 'publish', 'publish');

-- ---------- helpers ----------
-- Lease-fenced task context for stage writes: NULL when the caller may write for this task, else the outcome.
CREATE FUNCTION knowledge.lease_err(_task uuid, _token uuid, _epoch bigint) RETURNS jsonb
LANGUAGE plpgsql SET search_path = pg_catalog, pg_temp AS $$
DECLARE g jsonb;
BEGIN
    g := runtime.guard(_task, _token, _epoch, NULL);
    IF g IS NULL AND runtime.stopping((SELECT job_id FROM runtime.tasks WHERE id = _task)) THEN g := core.res('JOB_STOPPING'); END IF;
    RETURN g;
END $$;

CREATE FUNCTION knowledge.registered(_type text, _key text, _plugin text) RETURNS jsonb
LANGUAGE sql STABLE SET search_path = pg_catalog, pg_temp AS $$
    SELECT spec FROM core.registry_keys WHERE item_type = _type AND key = _key AND owner = _plugin $$;

-- Delta when every pipeline of the collection's plugin writing that collection key is delta.
CREATE FUNCTION knowledge.is_delta(_collection uuid) RETURNS boolean
LANGUAGE sql STABLE SET search_path = pg_catalog, pg_temp AS $$
    SELECT bool_and(p.publish_mode = 'delta') FROM knowledge.collections c
    JOIN runtime.pipelines p ON p.plugin_id = c.plugin_id AND p.collection_key = c.key WHERE c.id = _collection $$;

-- ---------- W6 stage writes (plugin logins) ----------
-- P-C1: same hash as the visible version -> touch only; new hash -> staged version + chunks.
CREATE FUNCTION knowledge.put_unit(_task uuid, _token uuid, _epoch bigint, _kind text, _key text, _content_hash text,
                                   _attrs jsonb, _chunks jsonb, _on_behalf_of text DEFAULT NULL) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE e jsonb; j runtime.jobs; uid uuid; openv knowledge.unit_versions; staged knowledge.unit_versions; vid uuid; maxc int; ids jsonb;
BEGIN
    e := knowledge.lease_err(_task, _token, _epoch); IF e IS NOT NULL THEN RETURN e; END IF;
    SELECT jb.* INTO j FROM runtime.jobs jb JOIN runtime.tasks t ON t.job_id = jb.id WHERE t.id = _task;
    IF knowledge.registered('unit_kind', _kind, j.plugin_id) IS NULL THEN RETURN core.res('NOT_REGISTERED'); END IF;
    maxc := runtime.nval(j.policy_snapshot, 'knowledge.chunk.max_chars', 8000);
    IF jsonb_typeof(coalesce(_chunks, '[]')) <> 'array'
       OR EXISTS (SELECT 1 FROM jsonb_array_elements(coalesce(_chunks, '[]')) c
                  WHERE coalesce(c->>'text', '') = '' OR length(c->>'text') > maxc OR (c->>'ordinal')::int < 0)
       OR (SELECT count(DISTINCT c->>'ordinal') <> count(*) FROM jsonb_array_elements(coalesce(_chunks, '[]')) c) THEN
        RETURN core.res('OUTPUT_INVALID');
    END IF;
    INSERT INTO knowledge.units (tenant_id, project_id, collection_id, source_id, kind, key, created_by, on_behalf_of)
        VALUES (j.tenant_id, j.project_id, j.collection_id, j.source_id, _kind, _key, j.created_by, _on_behalf_of)
        ON CONFLICT (collection_id, source_id, kind, key) DO NOTHING;
    SELECT id INTO uid FROM knowledge.units
        WHERE collection_id = j.collection_id AND source_id IS NOT DISTINCT FROM j.source_id AND kind = _kind AND key = _key FOR UPDATE;
    SELECT * INTO staged FROM knowledge.unit_versions WHERE unit_id = uid AND candidate_job = j.id AND valid_from_gen IS NULL;
    IF FOUND THEN
        RETURN core.res(CASE WHEN staged.content_hash = _content_hash THEN 'REPLAY' ELSE 'CONFLICT' END,
                        jsonb_build_object('unit_version_id', staged.id));
    END IF;
    INSERT INTO knowledge.candidate_keys (job_id, unit_id, mode) VALUES (j.id, uid, 'touch') ON CONFLICT DO NOTHING;
    SELECT * INTO openv FROM knowledge.unit_versions WHERE unit_id = uid AND valid_from_gen IS NOT NULL AND valid_to_gen IS NULL;
    IF FOUND AND openv.content_hash = _content_hash THEN
        RETURN core.res('TOUCHED', jsonb_build_object('unit_version_id', openv.id));
    END IF;
    INSERT INTO knowledge.unit_versions (tenant_id, unit_id, parent_version, content_hash, attrs, candidate_job)
        VALUES (j.tenant_id, uid, openv.id, _content_hash, coalesce(_attrs, '{}'), j.id) RETURNING id INTO vid;
    WITH ins AS (
        INSERT INTO knowledge.chunks (tenant_id, unit_version_id, ordinal, text, span, attrs)
        SELECT j.tenant_id, vid, (c->>'ordinal')::int, c->>'text', coalesce(c->'span', '{}'), coalesce(c->'attrs', '{}')
        FROM jsonb_array_elements(coalesce(_chunks, '[]')) c RETURNING id, ordinal)
    SELECT coalesce(jsonb_object_agg(ordinal, id), '{}') INTO ids FROM ins;
    RETURN core.res('STAGED', jsonb_build_object('unit_version_id', vid, 'chunk_ids', ids));
END $$;

-- P-C2: values reused only on exact (tenant, rep_kind, config_hash, input_hash) (I-K2); same answer new or reused (ST-27).
CREATE FUNCTION knowledge.put_representation(_task uuid, _token uuid, _epoch bigint, _rep_kind text, _config_hash text,
                                             _input_hash text, _unit_version uuid, _chunk uuid, _vec text, _body text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, ext, pg_temp AS $$
DECLARE e jsonb; j runtime.jobs; spec jsonb; v ext.vector; d int; vid uuid; cur uuid;
BEGIN
    e := knowledge.lease_err(_task, _token, _epoch); IF e IS NOT NULL THEN RETURN e; END IF;
    SELECT jb.* INTO j FROM runtime.jobs jb JOIN runtime.tasks t ON t.job_id = jb.id WHERE t.id = _task;
    spec := knowledge.registered('representation_kind', _rep_kind, j.plugin_id);
    IF spec IS NULL THEN RETURN core.res('NOT_REGISTERED'); END IF;
    IF (_unit_version IS NULL) = (_chunk IS NULL) OR (spec->>'target' = 'unit') <> (_unit_version IS NOT NULL) THEN
        RETURN core.res('OUTPUT_INVALID', '{"why": "target"}');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM knowledge.unit_versions uv LEFT JOIN knowledge.chunks c ON c.unit_version_id = uv.id
                   WHERE uv.candidate_job = j.id AND uv.valid_from_gen IS NULL
                     AND (uv.id = _unit_version OR c.id = _chunk)) THEN
        RETURN core.res('NOT_FOUND', '{"why": "target is not staged by this job"}');
    END IF;
    IF spec->>'value' = 'vector' THEN
        BEGIN v := _vec::ext.vector; EXCEPTION WHEN OTHERS THEN RETURN core.res('OUTPUT_INVALID', '{"why": "vector"}'); END;
        d := ext.vector_dims(v);
        IF d <> (spec->>'dims')::int THEN RETURN core.res('DIM_MISMATCH', jsonb_build_object('expected', (spec->>'dims')::int, 'got', d)); END IF;
    ELSIF _body IS NULL THEN RETURN core.res('OUTPUT_INVALID', '{"why": "body"}');
    END IF;
    INSERT INTO knowledge.representation_values (tenant_id, rep_kind, config_hash, input_hash, dims, vec, body)
        VALUES (j.tenant_id, _rep_kind, _config_hash, _input_hash, d, v, CASE WHEN v IS NULL THEN _body END)
        ON CONFLICT (tenant_id, rep_kind, config_hash, input_hash) DO NOTHING;
    SELECT id INTO vid FROM knowledge.representation_values
        WHERE tenant_id = j.tenant_id AND rep_kind = _rep_kind AND config_hash = _config_hash AND input_hash = _input_hash;
    SELECT value_id INTO cur FROM knowledge.representation_links
        WHERE rep_kind = _rep_kind AND config_hash = _config_hash
          AND unit_version_id IS NOT DISTINCT FROM _unit_version AND chunk_id IS NOT DISTINCT FROM _chunk;
    IF cur IS NOT NULL THEN RETURN core.res(CASE WHEN cur = vid THEN 'OK' ELSE 'CONFLICT' END); END IF;
    INSERT INTO knowledge.representation_links (tenant_id, rep_kind, config_hash, value_id, unit_version_id, chunk_id)
        VALUES (j.tenant_id, _rep_kind, _config_hash, vid, _unit_version, _chunk);
    RETURN core.res('OK');
END $$;

-- P-C3 hold (input temporarily missing; the open version stays) and batch touch (unchanged inputs).
CREATE FUNCTION knowledge.mark_units(_task uuid, _token uuid, _epoch bigint, _mode text, _kind text, _keys text[], _reason text)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE e jsonb; j runtime.jobs; n int;
BEGIN
    e := knowledge.lease_err(_task, _token, _epoch); IF e IS NOT NULL THEN RETURN e; END IF;
    SELECT jb.* INTO j FROM runtime.jobs jb JOIN runtime.tasks t ON t.job_id = jb.id WHERE t.id = _task;
    IF _mode NOT IN ('touch', 'hold') THEN RETURN core.res('INPUT_INVALID'); END IF;
    IF knowledge.registered('unit_kind', _kind, j.plugin_id) IS NULL THEN RETURN core.res('NOT_REGISTERED'); END IF;
    INSERT INTO knowledge.candidate_keys (job_id, unit_id, mode, reason)
    SELECT j.id, u.id, _mode, _reason FROM knowledge.units u
    WHERE u.collection_id = j.collection_id AND u.source_id IS NOT DISTINCT FROM j.source_id AND u.kind = _kind AND u.key = ANY (_keys)
    ON CONFLICT (job_id, unit_id) DO UPDATE SET mode = EXCLUDED.mode, reason = EXCLUDED.reason
        WHERE knowledge.candidate_keys.mode = 'touch';
    GET DIAGNOSTICS n = ROW_COUNT;
    RETURN core.res(upper(_mode) || 'ED', jsonb_build_object('count', n));
END $$;

-- G3 per-item report.
CREATE FUNCTION runtime.put_job_item(_task uuid, _token uuid, _epoch bigint, _key text, _status text, _code text, _detail jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE e jsonb; j runtime.jobs;
BEGIN
    e := knowledge.lease_err(_task, _token, _epoch); IF e IS NOT NULL THEN RETURN e; END IF;
    SELECT jb.* INTO j FROM runtime.jobs jb JOIN runtime.tasks t ON t.job_id = jb.id WHERE t.id = _task;
    IF _code IS NOT NULL AND NOT EXISTS (SELECT 1 FROM core.registry_keys WHERE key = _code AND owner IN ('core', j.plugin_id)
                                          AND item_type IN ('error_code', 'exclusion_reason')) THEN
        RETURN core.res('NOT_REGISTERED');
    END IF;
    INSERT INTO runtime.job_items (job_id, key, status, code, detail) VALUES (j.id, _key, _status, _code, coalesce(_detail, '{}'))
        ON CONFLICT (job_id, key) DO UPDATE SET status = EXCLUDED.status, code = EXCLUDED.code, detail = EXCLUDED.detail;
    RETURN core.res('OK');
END $$;

CREATE FUNCTION runtime.job_items(_p uuid, _job uuid, _after text, _limit int) RETURNS jsonb
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
    SELECT CASE WHEN core.authorize(_p, (SELECT project_id FROM runtime.jobs WHERE id = _job), 1) IS NULL THEN core.res('FORBIDDEN')
           ELSE core.res('OK', jsonb_build_object('items', coalesce((
               SELECT jsonb_agg(jsonb_build_object('key', key, 'status', status, 'code', code) ORDER BY key)
               FROM (SELECT * FROM runtime.job_items WHERE job_id = _job AND key > coalesce(_after, '')
                     ORDER BY key LIMIT least(coalesce(_limit, 100), 1000)) x), '[]'))) END $$;

-- ---------- C3 publish (runtime login: the Core publish stage) ----------
CREATE FUNCTION knowledge.publish(_job uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE j runtime.jobs; c knowledge.collections; s knowledge.collection_sources; pl runtime.pipelines; rev core.source_revisions;
        g bigint; staged int; covered int; items int; excl int; applied int; res_out text; stop text; code text;
BEGIN
    SELECT * INTO j FROM runtime.jobs WHERE id = _job;
    SELECT * INTO c FROM knowledge.collections WHERE id = j.collection_id FOR NO KEY UPDATE;     -- publishes of one collection serialize
    SELECT * INTO pl FROM runtime.pipelines WHERE (plugin_id, key, version) = (j.plugin_id, j.pipeline_key, j.pipeline_version);
    SELECT * INTO s FROM knowledge.collection_sources
        WHERE collection_id = c.id AND source_id IS NOT DISTINCT FROM j.source_id AND pipeline_key = j.pipeline_key FOR UPDATE;
    SELECT * INTO j FROM runtime.jobs WHERE id = _job FOR UPDATE;
    IF j.finished_at IS NOT NULL THEN RETURN core.res('ALREADY_TERMINAL', jsonb_build_object('outcome', j.outcome)); END IF;
    IF runtime.stopping(j.id) OR (SELECT status FROM runtime.job_stages WHERE job_id = j.id AND stage_key = 'publish') <> 'ready' THEN
        RETURN core.res('STOPPED');
    END IF;
    -- refusals that end the job (staged rows discarded by finish_stopped_jobs in this transaction)
    IF j.fence_seq < c.fence_floor THEN stop := 'failure'; res_out := 'FENCE_EXPIRED'; code := 'core.fence_expired';
    ELSIF pl.publish_mode = 'full' AND s.last_seq >= j.fence_seq THEN stop := 'superseded'; res_out := 'SUPERSEDED';
    ELSE
        SELECT count(*), count(*) FILTER (WHERE NOT EXISTS (
                   SELECT 1 FROM core.registry_keys k, jsonb_array_elements_text(coalesce(k.spec->'required_representations', '[]')) rr
                   WHERE k.item_type = 'unit_kind' AND k.key = u.kind
                     AND NOT EXISTS (SELECT 1 FROM knowledge.representation_links l LEFT JOIN knowledge.chunks ch ON ch.id = l.chunk_id
                                     WHERE l.rep_kind = rr AND (l.unit_version_id = uv.id OR ch.unit_version_id = uv.id))))
        INTO staged, covered
        FROM knowledge.unit_versions uv JOIN knowledge.units u ON u.id = uv.unit_id
        WHERE uv.candidate_job = j.id AND uv.valid_from_gen IS NULL;
        SELECT count(*), count(*) FILTER (WHERE status = 'excluded') INTO items, excl FROM runtime.job_items WHERE job_id = j.id;
        IF (staged > 0 AND covered::numeric / staged < runtime.nval(j.policy_snapshot, 'knowledge.min_coverage', 0.95))
           OR (items > 0 AND excl::numeric / items > runtime.nval(j.policy_snapshot, 'knowledge.max_excluded_ratio', 0.5)) THEN
            stop := 'failure'; res_out := 'COVERAGE_FAILED'; code := 'knowledge.coverage_failed';
        END IF;
    END IF;
    IF stop IS NOT NULL THEN
        UPDATE runtime.jobs SET stop_requested = stop, outcome = res_out WHERE id = j.id;
        UPDATE runtime.job_stages SET status = CASE WHEN stop = 'failure' THEN 'failed' ELSE 'canceled' END
            WHERE job_id = j.id AND stage_key = 'publish';
        PERFORM runtime.finish_stopped_jobs(j.id);
        RETURN core.res(res_out, CASE WHEN code IS NULL THEN '{}'::jsonb ELSE jsonb_build_object('error_code', code) END);
    END IF;

    g := c.current_gen + 1;
    -- delta per-unit guard: stale staged rows of units already changed by a newer job are dropped (PI-2, PI-8)
    DELETE FROM knowledge.unit_versions uv USING knowledge.units u
        WHERE uv.unit_id = u.id AND uv.candidate_job = j.id AND uv.valid_from_gen IS NULL AND u.last_seq >= j.fence_seq;
    DELETE FROM knowledge.candidate_keys ck USING knowledge.units u
        WHERE ck.unit_id = u.id AND ck.job_id = j.id AND ck.mode = 'delete' AND u.last_seq >= j.fence_seq;
    SELECT * INTO rev FROM core.source_revisions WHERE id = j.source_revision_id;
    -- change set: staged units, explicit deletes (delta), full-mode omissions of this source + pipeline kinds (PI-5, I-K5)
    CREATE TEMP TABLE IF NOT EXISTS pg_temp.chg (unit_id uuid PRIMARY KEY) ON COMMIT DROP;
    TRUNCATE pg_temp.chg;
    INSERT INTO pg_temp.chg SELECT DISTINCT unit_id FROM knowledge.unit_versions WHERE candidate_job = j.id AND valid_from_gen IS NULL;
    INSERT INTO pg_temp.chg SELECT unit_id FROM knowledge.candidate_keys WHERE job_id = j.id AND mode = 'delete' ON CONFLICT DO NOTHING;
    IF pl.publish_mode = 'full' AND coalesce(rev.input_complete, true) THEN
        INSERT INTO pg_temp.chg
        SELECT u.id FROM knowledge.units u JOIN knowledge.unit_versions uv ON uv.unit_id = u.id
            AND uv.valid_from_gen IS NOT NULL AND uv.valid_to_gen IS NULL
        WHERE u.collection_id = c.id AND u.source_id IS NOT DISTINCT FROM j.source_id AND u.kind = ANY (pl.unit_kinds)
          AND NOT EXISTS (SELECT 1 FROM knowledge.candidate_keys ck WHERE ck.job_id = j.id AND ck.unit_id = u.id)
        ON CONFLICT DO NOTHING;
    END IF;
    PERFORM 1 FROM knowledge.units WHERE id IN (SELECT unit_id FROM pg_temp.chg) ORDER BY id FOR UPDATE;
    -- close before open (uq_unit_open_version, PI-3); a delete or omission without an open version changes nothing
    UPDATE knowledge.unit_versions SET valid_to_gen = g
        WHERE unit_id IN (SELECT unit_id FROM pg_temp.chg) AND valid_from_gen IS NOT NULL AND valid_to_gen IS NULL;
    GET DIAGNOSTICS applied = ROW_COUNT;
    UPDATE knowledge.unit_versions SET valid_from_gen = g, candidate_job = NULL WHERE candidate_job = j.id AND valid_from_gen IS NULL;
    GET DIAGNOSTICS staged = ROW_COUNT;
    UPDATE knowledge.units SET last_seq = j.fence_seq WHERE id IN (SELECT unit_id FROM pg_temp.chg) AND last_seq < j.fence_seq;
    DELETE FROM knowledge.candidate_keys WHERE job_id = j.id;
    IF applied + staged = 0 THEN
        res_out := 'NO_CHANGE';
    ELSE
        res_out := 'PUBLISHED';
        UPDATE knowledge.collections SET current_gen = g,
               active_config = active_config || coalesce((SELECT jsonb_object_agg(l.rep_kind, l.config_hash)
                   FROM knowledge.representation_links l LEFT JOIN knowledge.chunks ch ON ch.id = l.chunk_id
                   JOIN knowledge.unit_versions uv ON uv.id = coalesce(l.unit_version_id, ch.unit_version_id)
                   WHERE uv.valid_from_gen = g AND uv.unit_id IN (SELECT unit_id FROM pg_temp.chg)), '{}')
            WHERE id = c.id;
        INSERT INTO knowledge.generations (collection_id, gen, job_id, status) VALUES (c.id, g, j.id, 'published');
        INSERT INTO core.outbox_events (tenant_id, project_id, type, payload)
            VALUES (j.tenant_id, j.project_id, 'knowledge.published', jsonb_build_object('collection', c.id, 'gen', g,
                    'source', j.source_id, 'seq', j.seq, 'job', j.id));
    END IF;
    IF s.id IS NOT NULL THEN
        UPDATE knowledge.collection_sources SET last_seq = greatest(last_seq, j.fence_seq),
               last_revision_id = CASE WHEN pl.publish_mode = 'full' THEN j.source_revision_id ELSE last_revision_id END,
               last_fingerprint = CASE WHEN pl.publish_mode = 'full' THEN j.fingerprint ELSE last_fingerprint END
            WHERE id = s.id;
    END IF;
    UPDATE runtime.job_stages SET status = 'succeeded' WHERE job_id = j.id AND stage_key = 'publish';
    UPDATE runtime.jobs SET status = 'succeeded', outcome = res_out, finished_at = now() WHERE id = j.id RETURNING * INTO j;
    PERFORM runtime.life(j, NULL, 'running', 'succeeded', jsonb_build_object('gen', CASE WHEN res_out = 'PUBLISHED' THEN g END));
    IF pl.publish_mode = 'full' THEN                               -- §7.4: older running jobs in the same scope stop
        UPDATE runtime.jobs SET stop_requested = 'superseded'
        WHERE collection_id = c.id AND source_id IS NOT DISTINCT FROM j.source_id AND pipeline_key = j.pipeline_key
          AND finished_at IS NULL AND seq < j.seq AND stop_requested IS NULL;
    END IF;
    RETURN core.res(res_out, jsonb_build_object('gen', CASE WHEN res_out = 'PUBLISHED' THEN g ELSE c.current_gen END));
END $$;

-- Jobs whose publish stage is ready (runtime loop calls publish for each).
CREATE FUNCTION knowledge.publish_ready(_max int DEFAULT 20) RETURNS uuid[]
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
    SELECT coalesce(array_agg(job_id), '{}') FROM (
        SELECT s.job_id FROM runtime.job_stages s JOIN runtime.jobs j ON j.id = s.job_id
        WHERE s.stage_key = 'publish' AND s.status = 'ready' AND j.status = 'running' ORDER BY j.seq LIMIT _max) x $$;

-- ---------- C2 delete_unit (api) ----------
CREATE FUNCTION knowledge.delete_units(_p uuid, _collection uuid, _source uuid, _kind text, _keys text[], _rid uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE a jsonb := jsonb_build_object('collection', _collection, 'source', _source, 'kind', _kind, 'keys', _keys);
        r jsonb; c knowledge.collections; role int; j runtime.jobs; tomb text[]; maxn numeric; sq bigint;
BEGIN
    SELECT * INTO c FROM knowledge.collections WHERE id = _collection;
    role := CASE WHEN FOUND THEN core.project_role(_p, c.project_id) ELSE 0 END;
    IF role < 2 THEN RETURN core.res('FORBIDDEN'); END IF;
    IF (SELECT state FROM core.projects WHERE id = c.project_id) <> 'active' THEN RETURN core.res('PROJECT_DELETING'); END IF;
    IF NOT coalesce(knowledge.is_delta(_collection), false) THEN RETURN core.res('FULL_MODE_COLLECTION'); END IF;
    maxn := coalesce((core.policy_value('knowledge.delete_batch_max', ARRAY[c.project_id::text, c.tenant_id::text]) #>> '{}')::numeric, 10000);
    IF cardinality(_keys) > maxn THEN RETURN core.res('BATCH_TOO_LARGE'); END IF;
    IF role < 3 AND EXISTS (SELECT 1 FROM knowledge.units WHERE collection_id = _collection AND source_id IS NOT DISTINCT FROM _source
                            AND kind = _kind AND key = ANY (_keys) AND created_by <> _p) THEN
        RETURN core.res('FORBIDDEN', '{"why": "units of another principal need operator"}');
    END IF;
    r := core.req_check(_p, _rid, 'delete_units', a); IF r IS NOT NULL THEN RETURN r; END IF;
    sq := nextval('runtime.job_seq');
    INSERT INTO runtime.jobs (tenant_id, project_id, collection_id, source_id, plugin_id, plugin_version, pipeline_key,
                              pipeline_version, pipeline_hash, policy_snapshot, fingerprint, request_id, fence_seq, seq, status, created_by)
    VALUES (c.tenant_id, c.project_id, c.id, _source, 'core', '0.4.0', 'delete', 1, 'core.delete@1',
            runtime.snapshot('core', c.tenant_id, c.project_id), 'delete', _rid, sq, sq, 'queued', _p) RETURNING * INTO j;
    -- a key never seen gets a tombstone unit row so an older ingest cannot create it later (§24, TP-11)
    WITH ins AS (
        INSERT INTO knowledge.units (tenant_id, project_id, collection_id, source_id, kind, key, created_by)
        SELECT c.tenant_id, c.project_id, c.id, _source, _kind, k, _p FROM unnest(_keys) k
        ON CONFLICT (collection_id, source_id, kind, key) DO NOTHING RETURNING key)
    SELECT coalesce(array_agg(key), '{}') INTO tomb FROM ins;
    INSERT INTO knowledge.candidate_keys (job_id, unit_id, mode)
    SELECT j.id, u.id, 'delete' FROM knowledge.units u
    WHERE u.collection_id = c.id AND u.source_id IS NOT DISTINCT FROM _source AND u.kind = _kind AND u.key = ANY (_keys);
    PERFORM runtime.life(j, NULL, NULL, 'queued', '{"op": "delete"}');
    PERFORM core.audit_write(_p, 'delete_units', 'collection:' || c.id, a);
    RETURN core.req_save(_p, _rid, 'delete_units', a, core.res('ACCEPTED', jsonb_build_object('job_id', j.id, 'tombstoned', tomb)));
END $$;

-- ---------- U6 pins ----------
CREATE FUNCTION knowledge.pin(_p uuid, _collection uuid, _ttl interval) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE c knowledge.collections; maxttl interval; pid uuid;
BEGIN
    SELECT * INTO c FROM knowledge.collections WHERE id = _collection;
    IF NOT FOUND OR core.authorize(_p, c.project_id, 1) IS NULL THEN RETURN core.res('FORBIDDEN'); END IF;
    maxttl := coalesce((core.policy_value('knowledge.pin_ttl') #>> '{}')::interval, '15 minutes');
    IF (SELECT count(*) FROM knowledge.generation_pins WHERE principal_id = _p AND expires_at > now()) >= 100 THEN
        RETURN core.res('PIN_LIMIT');                              -- per-principal cap (R17)
    END IF;
    INSERT INTO knowledge.generation_pins (collection_id, gen, principal_id, expires_at)
        VALUES (c.id, c.current_gen, _p, now() + least(coalesce(_ttl, maxttl), maxttl)) RETURNING id INTO pid;
    RETURN core.res('OK', jsonb_build_object('pin_id', pid, 'gen', c.current_gen));
END $$;

CREATE FUNCTION knowledge.unpin(_p uuid, _pin uuid) RETURNS jsonb
LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
    WITH d AS (DELETE FROM knowledge.generation_pins WHERE id = _pin AND principal_id = _p RETURNING 1)
    SELECT core.res(CASE WHEN EXISTS (SELECT 1 FROM d) THEN 'OK' ELSE 'NOT_FOUND' END) $$;

-- ---------- U5 search (§16): keyword (FTS) + vector kinds, RRF; caller runs it in a REPEATABLE READ transaction ----------
CREATE FUNCTION knowledge.search(_p uuid, _projects uuid[], _collections uuid[], _query text, _qvecs jsonb,
                                 _filters jsonb, _k int, _pins jsonb DEFAULT NULL, _include_content boolean DEFAULT false)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, ext, pg_temp SET hnsw.iterative_scan = relaxed_order AS $$
DECLARE pr uuid; colls uuid[]; gens jsonb := '{}'; snap jsonb; cand int; rrfk numeric; maxk int; rk text; ch text; dims int;
        sqlv text; hits jsonb := '[]'; part jsonb; out jsonb; vis uuid[]; cc record; f jsonb := coalesce(_filters, '{}'); tenants uuid[];
BEGIN
    IF current_setting('transaction_isolation') <> 'repeatable read' THEN
        RAISE EXCEPTION 'knowledge.search must run in a REPEATABLE READ transaction (TP-20)';
    END IF;
    IF cardinality(_projects) = 0 THEN RETURN core.res('INPUT_INVALID'); END IF;
    FOREACH pr IN ARRAY _projects LOOP                              -- P-C5: all or nothing; unknown = FORBIDDEN
        IF core.authorize(_p, pr, 1) IS NULL THEN RETURN core.res('FORBIDDEN'); END IF;
    END LOOP;
    IF EXISTS (SELECT 1 FROM core.projects WHERE id = ANY (_projects) AND state <> 'active') THEN RETURN core.res('PROJECT_DELETING'); END IF;
    SELECT array_agg(DISTINCT tenant_id) INTO tenants FROM core.projects WHERE id = ANY (_projects);
    SELECT array_agg(id) INTO colls FROM knowledge.collections
        WHERE project_id = ANY (_projects) AND (_collections IS NULL OR id = ANY (_collections));
    IF _collections IS NOT NULL AND cardinality(_collections) <> coalesce(cardinality(colls), 0) THEN RETURN core.res('FORBIDDEN'); END IF;
    IF colls IS NULL THEN RETURN core.res('OK', '{"hits": [], "generations": {}}'); END IF;
    FOR cc IN SELECT * FROM knowledge.collections WHERE id = ANY (colls) LOOP
        IF _pins ? cc.id::text THEN
            IF NOT EXISTS (SELECT 1 FROM knowledge.generation_pins WHERE collection_id = cc.id AND principal_id = _p
                           AND gen = (_pins->>cc.id::text)::bigint AND expires_at > now()) THEN
                RETURN core.res('GENERATION_GONE');
            END IF;
            gens := gens || jsonb_build_object(cc.id::text, (_pins->>cc.id::text)::bigint);
        ELSE gens := gens || jsonb_build_object(cc.id::text, cc.current_gen);
        END IF;
        FOR rk IN SELECT jsonb_object_keys(coalesce(_qvecs, '{}')) LOOP
            IF cc.active_config ? rk AND cc.active_config->>rk <> (_qvecs->rk->>'config_hash') THEN
                RETURN core.res('CONFIG_MISMATCH', jsonb_build_object('rep_kind', rk, 'active', cc.active_config->>rk));
            END IF;
        END LOOP;
    END LOOP;
    snap := runtime.snapshot('core', tenants[1], _projects[1]);
    cand := runtime.nval(snap, 'knowledge.search.candidate_k', 50);
    rrfk := runtime.nval(snap, 'knowledge.search.rrf_k', 60);
    maxk := runtime.nval(snap, 'knowledge.search.max_k', 20);
    IF _k IS NULL OR _k < 1 OR _k > maxk THEN RETURN core.res('INPUT_INVALID', jsonb_build_object('max_k', maxk)); END IF;
    PERFORM set_config('hnsw.ef_search', runtime.nval(snap, 'knowledge.search.ef_search', 200)::text, true);

    -- visible versions at the pinned generation, filters applied (one REPEATABLE READ snapshot for every query below)
    SELECT coalesce(array_agg(uv.id), '{}') INTO vis FROM knowledge.units u JOIN knowledge.unit_versions uv ON uv.unit_id = u.id
    WHERE u.collection_id = ANY (colls)
      AND uv.valid_from_gen <= (gens->>u.collection_id::text)::bigint
      AND (uv.valid_to_gen IS NULL OR uv.valid_to_gen > (gens->>u.collection_id::text)::bigint)
      AND (NOT f ? 'unit_kinds' OR u.kind IN (SELECT jsonb_array_elements_text(f->'unit_kinds')))
      AND (NOT f ? 'source_ids' OR u.source_id IN (SELECT (jsonb_array_elements_text(f->'source_ids'))::uuid))
      AND (NOT f ? 'attrs' OR uv.attrs @> (f->'attrs'));
    IF coalesce(_query, '') <> '' THEN
        SELECT coalesce(jsonb_agg(jsonb_build_object('r', 'fts', 'n', n, 'v', vid, 'c', cid)), '[]') INTO hits FROM (
            SELECT row_number() OVER (ORDER BY ts_rank_cd(c.fts, q) DESC, c.id) AS n, c.unit_version_id AS vid, c.id AS cid
            FROM knowledge.chunks c, websearch_to_tsquery('simple', _query) q
            WHERE c.fts @@ q AND c.unit_version_id = ANY (vis)
            ORDER BY ts_rank_cd(c.fts, q) DESC, c.id LIMIT cand) x;
    END IF;
    FOR rk IN SELECT jsonb_object_keys(coalesce(_qvecs, '{}')) LOOP
        ch := _qvecs->rk->>'config_hash';
        dims := (SELECT (spec->>'dims')::int FROM core.registry_keys WHERE item_type = 'representation_kind' AND key = rk);
        IF dims IS NULL THEN RETURN core.res('NOT_REGISTERED', jsonb_build_object('rep_kind', rk)); END IF;
        -- ANN over the partial HNSW index of (rep_kind, config_hash), re-sorted by exact distance (§16 step 4)
        sqlv := format($q$
            SELECT coalesce(jsonb_agg(jsonb_build_object('r', %L, 'n', n, 'v', vid, 'c', cid)), '[]') FROM (
            SELECT row_number() OVER (ORDER BY d, vid, cid) AS n, vid, cid FROM (
                SELECT v.vec::ext.vector(%s) OPERATOR(ext.<=>) $1::ext.vector(%s) AS d,
                       coalesce(l.unit_version_id, c.unit_version_id) AS vid, l.chunk_id AS cid
                FROM knowledge.representation_values v
                JOIN knowledge.representation_links l ON l.value_id = v.id AND l.rep_kind = v.rep_kind AND l.config_hash = v.config_hash
                LEFT JOIN knowledge.chunks c ON c.id = l.chunk_id
                WHERE v.tenant_id = ANY ($2) AND v.rep_kind = %L AND v.config_hash = %L
                  AND coalesce(l.unit_version_id, c.unit_version_id) = ANY ($3)
                ORDER BY v.vec::ext.vector(%s) OPERATOR(ext.<=>) $1::ext.vector(%s) LIMIT %s) x) y$q$,
            rk, dims, dims, rk, ch, dims, dims, cand);
        EXECUTE sqlv INTO part USING (_qvecs->rk->>'vec'), tenants, vis;
        hits := hits || part;
    END LOOP;
    -- RRF per unit version (weights knowledge.search.weight.<kind>, default 1); best chunk kept
    SELECT coalesce(jsonb_agg(h ORDER BY (h->>'score')::numeric DESC), '[]') INTO out FROM (
        SELECT jsonb_build_object('unit_id', u.id, 'unit_version_id', uv.id, 'collection_id', u.collection_id, 'kind', u.kind,
                   'key', u.key, 'source_id', u.source_id, 'attrs', uv.attrs, 'score', round(r.score, 6), 'matched', r.matched,
                   'gen', (gens->>u.collection_id::text)::bigint,
                   'chunk', CASE WHEN bc.id IS NOT NULL THEN jsonb_build_object('id', bc.id, 'ordinal', bc.ordinal, 'span', bc.span,
                            'text', CASE WHEN _include_content THEN bc.text ELSE left(bc.text, 300) END) END) AS h
        FROM (SELECT x.v AS unit_version_id, sum(coalesce(runtime.nval(snap, 'knowledge.search.weight.' || x.r, 1), 1) / (rrfk + x.n)) AS score,
                     jsonb_agg(DISTINCT x.r) AS matched,
                     (array_agg(x.c ORDER BY x.n) FILTER (WHERE x.c IS NOT NULL))[1] AS best_chunk
              FROM jsonb_to_recordset(hits) AS x(r text, n int, v uuid, c uuid) GROUP BY x.v ORDER BY score DESC LIMIT _k) r
        JOIN knowledge.unit_versions uv ON uv.id = r.unit_version_id JOIN knowledge.units u ON u.id = uv.unit_id
        LEFT JOIN knowledge.chunks bc ON bc.id = coalesce(r.best_chunk,
            (SELECT id FROM knowledge.chunks WHERE unit_version_id = uv.id ORDER BY ordinal LIMIT 1))) z;
    RETURN core.res('OK', jsonb_build_object('hits', out, 'generations', gens));
END $$;

-- ---------- P-K9 vector indexes (maint): one partial HNSW per (rep_kind, config_hash) ----------
CREATE FUNCTION knowledge.ensure_indexes() RETURNS int
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, ext, pg_temp AS $$
DECLARE r record; n int := 0; nm text; todo jsonb;
BEGIN
    SELECT coalesce(jsonb_agg(DISTINCT jsonb_build_object('rep_kind', rep_kind, 'config_hash', config_hash, 'dims', dims)), '[]') INTO todo
        FROM knowledge.representation_values WHERE dims IS NOT NULL;
    FOR r IN SELECT * FROM jsonb_to_recordset(todo) AS x(rep_kind text, config_hash text, dims int) LOOP
        nm := 'ix_rv_' || left(md5(r.rep_kind || '|' || r.config_hash), 20);
        CONTINUE WHEN to_regclass('knowledge.' || nm) IS NOT NULL;
        EXECUTE format('CREATE INDEX %I ON knowledge.representation_values USING hnsw ((vec::ext.vector(%s)) ext.vector_cosine_ops)
                        WITH (m = 16, ef_construction = 128) WHERE rep_kind = %L AND config_hash = %L',
                       nm, r.dims, r.rep_kind, r.config_hash);
        n := n + 1;
    END LOOP;
    RETURN n;
END $$;

-- ---------- M3 GC (maint) ----------
CREATE FUNCTION knowledge.gc(_collection uuid) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE c knowledge.collections; floor_gen bigint; ff bigint; nv int; nu int;
BEGIN
    BEGIN
        SELECT * INTO c FROM knowledge.collections WHERE id = _collection FOR NO KEY UPDATE NOWAIT;
    EXCEPTION WHEN lock_not_available THEN RETURN core.res('BUSY');
    END;
    DELETE FROM knowledge.generation_pins WHERE collection_id = c.id AND expires_at <= now();
    floor_gen := least(c.current_gen, (SELECT min(gen) FROM knowledge.generation_pins WHERE collection_id = c.id));
    DELETE FROM knowledge.unit_versions WHERE unit_id IN (SELECT id FROM knowledge.units WHERE collection_id = c.id)
        AND valid_to_gen <= floor_gen;                             -- chunks and links cascade
    GET DIAGNOSTICS nv = ROW_COUNT;
    UPDATE knowledge.generations SET status = 'retired', retired_at = now()
        WHERE collection_id = c.id AND gen < floor_gen AND status = 'published';
    -- fences (§24, §26 F2): raise the floor first, then drop unit rows no non-terminal job could still need
    ff := coalesce((SELECT min(fence_seq) FROM runtime.jobs WHERE collection_id = c.id AND finished_at IS NULL),
                   (SELECT last_value FROM runtime.job_seq));
    UPDATE knowledge.collections SET fence_floor = greatest(fence_floor, ff) WHERE id = c.id;
    DELETE FROM knowledge.units u WHERE u.collection_id = c.id AND u.last_seq <= ff
        AND NOT EXISTS (SELECT 1 FROM knowledge.unit_versions uv WHERE uv.unit_id = u.id)
        AND NOT EXISTS (SELECT 1 FROM knowledge.candidate_keys ck WHERE ck.unit_id = u.id);
    GET DIAGNOSTICS nu = ROW_COUNT;
    RETURN core.res('OK', jsonb_build_object('versions', nv, 'units', nu, 'floor_gen', floor_gen, 'fence_floor', greatest(c.fence_floor, ff)));
END $$;

-- Orphan representation values (no links left), tenant-wide pass.
CREATE FUNCTION knowledge.gc_values(_tenant uuid, _max int DEFAULT 10000) RETURNS int
LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
    WITH d AS (DELETE FROM knowledge.representation_values v WHERE v.id IN (
        SELECT id FROM knowledge.representation_values x WHERE x.tenant_id = _tenant
          AND NOT EXISTS (SELECT 1 FROM knowledge.representation_links l WHERE l.value_id = x.id) LIMIT _max) RETURNING 1)
    SELECT count(*)::int FROM d $$;
