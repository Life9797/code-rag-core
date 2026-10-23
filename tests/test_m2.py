"""M2: stage writes, publish (§24 TP-xx), explicit deletes, search (§16), pins, GC, indexes."""
import hashlib, json, uuid
import psycopg, pytest
from conftest import connect, rid
from coderag_core.worker import Worker, SqlBinding
from plugins.echo import main as echo_main
from test_m1b import stop_project_jobs


def sha(s): return hashlib.sha256(s.encode()).hexdigest()


def tst_h(ctx, t):
    for key, text in t["payload"].get("docs", {}).items():
        r = ctx.put_unit("tst.doc", key, sha(text), {"k": key}, [{"ordinal": 0, "text": text}])
        if r["outcome"] == "STAGED" and not t["payload"].get("no_vec"):
            assert ctx.put_representation("tst.vec4", "cfg4", sha(text), chunk_id=r["chunk_ids"]["0"],
                                          vec=echo_main.vec(text, 4))["outcome"] == "OK"
    for key in t["payload"].get("hold", []):
        ctx.hold("tst.doc", [key], "lfs_missing")
    for key, reason in t["payload"].get("excluded", {}).items():
        ctx.put_job_item(key, "excluded", reason)
    return {}


@pytest.fixture
def env(su, call, world, base):
    w = world
    p = call.api("core.project_create", w["A.admin"], w["A"], f"m2-{uuid.uuid4().hex[:6]}", rid())["id"]
    for plug in ("echo", "tst"):
        call.api("core.plugin_enable", w["A.admin"], p, plug, True, rid())
    for who, role in (("A.contrib", 2), ("A.reader", 1), ("A.op", 3)):
        call.api("core.member_change", w["A.admin"], w["A"], p, w[who], role, rid())
    ce, ct = connect(base, "plugin_echo"), connect(base, "plugin_tst")
    e = {"p": p, "echo": Worker("echo", "0.1.0", echo_main.HANDLERS, SqlBinding(ce), db=ce),
         "tst": Worker("tst", "0.1.0", {"h": tst_h}, SqlBinding(ct))}
    yield e
    ce.close(); ct.close()
    stop_project_jobs(su, p)


def create(call, world, env, plugin="echo", pipeline="run", who="A.contrib", force=False):
    """Sourceless test jobs: input changes come through the task payload, outside the fingerprint, so reruns use force."""
    return call.api("runtime.create_job", world[who], env["p"], plugin, pipeline, None, force, rid())


def payload(su, j, **kw):
    su.execute("UPDATE runtime.tasks SET payload = payload || %s WHERE job_id = %s AND parent_id IS NULL", (json.dumps(kw), j)); su.commit()


def publish_all(call):
    out = {}
    for j in call("runtime", "knowledge.publish_ready", 50):
        out[str(j)] = call("runtime", "knowledge.publish", j)
    return out


def run(su, call, env, plugin, j, **pl):
    call("runtime", "runtime.start_jobs", 50)
    if pl: payload(su, j, **pl)
    env[plugin].drain()
    return publish_all(call).get(j)


def search(base, p, projects, query="", qvecs=None, k=10, **kw):
    with connect(base, "api", autocommit=False) as c:
        c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        return c.execute("SELECT knowledge.search(%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                         (p, projects, kw.get("collections"), query, json.dumps(qvecs or {}), json.dumps(kw.get("filters", {})),
                          k, json.dumps(kw["pins"]) if kw.get("pins") else None, False)).fetchone()[0]


def visible(su, coll):
    return sorted(r[0] for r in su.execute("""
        SELECT u.key FROM knowledge.units u JOIN knowledge.unit_versions uv ON uv.unit_id = u.id
        JOIN knowledge.collections c ON c.id = u.collection_id
        WHERE u.collection_id = %s AND uv.valid_from_gen <= c.current_gen AND (uv.valid_to_gen IS NULL OR uv.valid_to_gen > c.current_gen)""", (coll,)).fetchall())


LEAVES = ["r.0.0", "r.0.1", "r.1.0", "r.1.1"]


def test_ec06_delta_publish_and_no_change(su, call, world, env):
    j = create(call, world, env)
    assert run(su, call, env, "echo", j["job_id"]) == {"outcome": "PUBLISHED", "gen": 1}
    assert visible(su, j["collection_id"]) == LEAVES
    j2 = create(call, world, env)                                       # same content: touched only -> NO_CHANGE, gen stays
    assert run(su, call, env, "echo", j2["job_id"])["outcome"] == "NO_CHANGE"
    j3 = create(call, world, env)                                       # changed content: new versions, nothing ended
    assert run(su, call, env, "echo", j3["job_id"], text="v2") == {"outcome": "PUBLISHED", "gen": 2}
    assert visible(su, j["collection_id"]) == LEAVES
    assert su.execute("SELECT count(*) FROM knowledge.unit_versions uv JOIN knowledge.units u ON u.id = uv.unit_id "
                      "WHERE u.collection_id = %s AND valid_to_gen = 2", (j["collection_id"],)).fetchone()[0] == 4
    assert su.execute("SELECT count(*) FROM core.outbox_events WHERE type = 'knowledge.published' AND payload->>'collection' = %s",
                      (j["collection_id"],)).fetchone()[0] == 2


def test_ec05_dim_mismatch(su, call, world, env):
    j = create(call, world, env)
    run(su, call, env, "echo", j["job_id"], dims=4)
    assert su.execute("SELECT count(*) FROM runtime.task_events e JOIN runtime.tasks t ON t.id = e.task_id "
                      "WHERE t.job_id = %s AND e.detail->'detail'->>'outcome' = 'DIM_MISMATCH'", (j["job_id"],)).fetchone()[0] > 0


def test_tp10_coverage_fail_discards_staged(su, call, world, env):
    j = create(call, world, env)
    r = run(su, call, env, "echo", j["job_id"], no_vec=True)
    assert r["outcome"] == "COVERAGE_FAILED"
    assert su.execute("SELECT status FROM runtime.jobs WHERE id = %s", (j["job_id"],)).fetchone()[0] == "failed"
    assert su.execute("SELECT count(*) FROM knowledge.unit_versions WHERE candidate_job = %s", (j["job_id"],)).fetchone()[0] == 0
    assert visible(su, j["collection_id"]) == []


def test_full_mode_omission_and_hold(su, call, world, env):
    j1 = create(call, world, env, "tst", "p")
    assert run(su, call, env, "tst", j1["job_id"], docs={"a": "alpha", "b": "beta", "c": "gamma"})["outcome"] == "PUBLISHED"
    j2 = create(call, world, env, "tst", "p", force=True)               # c omitted -> ended; b held -> stays (I-K5)
    assert run(su, call, env, "tst", j2["job_id"], docs={"a": "alpha"}, hold=["b"])["outcome"] == "PUBLISHED"
    assert visible(su, j1["collection_id"]) == ["a", "b"]
    # fingerprint unchanged and nothing running -> NO_CHANGE without a job run (G5, TP-07)
    assert create(call, world, env, "tst", "p")["outcome"] == "NO_CHANGE"


def test_tp02_older_full_job_superseded(su, call, world, env):
    j1 = create(call, world, env, "tst", "p")["job_id"]
    call("runtime", "runtime.start_jobs", 50)
    j2 = create(call, world, env, "tst", "p")["job_id"]
    call("runtime", "runtime.start_jobs", 50)
    payload(su, j1, docs={"a": "old"}); payload(su, j2, docs={"a": "new"})
    env["tst"].drain()
    assert call("runtime", "knowledge.publish", j2)["outcome"] == "PUBLISHED"
    r1 = call("runtime", "knowledge.publish", j1)
    assert r1["outcome"] in ("SUPERSEDED", "STOPPED")
    call("maint", "runtime.sweep", 100)
    assert su.execute("SELECT status FROM runtime.jobs WHERE id = %s", (j1,)).fetchone()[0] == "superseded"


def test_g6_excluded_ratio(su, call, world, env):
    j = create(call, world, env, "tst", "p")
    r = run(su, call, env, "tst", j["job_id"], docs={"a": "x"}, excluded={"big1": "tst.too_big", "big2": "tst.too_big"})
    assert r["outcome"] == "COVERAGE_FAILED"


def test_tp11_delete_beats_older_ingest_then_newer_recreates(su, call, world, env):
    w = world
    a = create(call, world, env)
    coll = a["collection_id"]
    call("runtime", "runtime.start_jobs", 50); env["echo"].drain()                 # A staged, not published
    d = call.api("knowledge.delete_units", w["A.contrib"], coll, None, "echo.item", ["r.0.0", "never.seen"], rid())
    assert d["outcome"] == "ACCEPTED" and d["tombstoned"] == ["never.seen"]
    call("runtime", "runtime.start_jobs", 50)
    assert call("runtime", "knowledge.publish", d["job_id"])["outcome"] == "NO_CHANGE"    # nothing was open yet
    assert call("runtime", "knowledge.publish", a["job_id"])["outcome"] == "PUBLISHED"
    assert visible(su, coll) == ["r.0.1", "r.1.0", "r.1.1"]                          # older ingest lost r.0.0 (PI-7)
    c = create(call, world, env)
    assert run(su, call, env, "echo", c["job_id"])["outcome"] == "PUBLISHED"
    assert visible(su, coll) == LEAVES                                               # newer ingest recreates
    d2 = call.api("knowledge.delete_units", w["A.contrib"], coll, None, "echo.item", ["r.1.1"], rid())
    call("runtime", "runtime.start_jobs", 50)
    assert call("runtime", "knowledge.publish", d2["job_id"])["outcome"] == "PUBLISHED"
    assert visible(su, coll) == ["r.0.0", "r.0.1", "r.1.0"]
    assert call.api("knowledge.delete_units", w["A.reader"], coll, None, "echo.item", ["r.0.1"], rid())["outcome"] == "FORBIDDEN"


def test_delete_refused_in_full_mode(call, world, env, su):
    j = create(call, world, env, "tst", "p")
    assert call.api("knowledge.delete_units", world["A.op"], j["collection_id"], None, "tst.doc", ["a"], rid())["outcome"] == "FULL_MODE_COLLECTION"


def test_search_fts_vector_rrf_and_access(su, base, call, world, env):
    w = world
    j = create(call, world, env, "tst", "p")
    run(su, call, env, "tst", j["job_id"], docs={"a": "postgres vacuum tuning", "b": "hnsw index recall", "c": "rrf fusion search"})
    assert call("maint", "knowledge.ensure_indexes") >= 1
    q = {"tst.vec4": {"config_hash": "cfg4", "vec": "[" + ",".join(map(str, echo_main.vec("hnsw index recall", 4))) + "]"}}
    r = search(base, w["A.reader"], [env["p"]], "vacuum", q, 3)
    assert r["outcome"] == "OK"
    keys = [h["key"] for h in r["hits"]]
    assert keys[0] in ("a", "b") and set(keys[:2]) == {"a", "b"}
    assert {h["key"]: h["matched"] for h in r["hits"]}["b"] == ["tst.vec4"]
    assert search(base, w["B.admin"], [env["p"]], "vacuum")["outcome"] == "FORBIDDEN"                 # ST-02
    assert search(base, w["A.reader"], [env["p"], w["B.p"]], "vacuum")["outcome"] == "FORBIDDEN"     # ST-03
    assert search(base, w["A.reader"], [uuid.uuid4()], "vacuum")["outcome"] == "FORBIDDEN"          # ST-04
    bad = {"tst.vec4": {"config_hash": "other", "vec": q["tst.vec4"]["vec"]}}
    assert search(base, w["A.reader"], [env["p"]], "", bad)["outcome"] == "CONFIG_MISMATCH"
    assert search(base, w["A.reader"], [env["p"]], "fusion", filters={"attrs": {"k": "c"}})["hits"][0]["key"] == "c"
    with connect(base, "api") as c, pytest.raises(psycopg.errors.RaiseException):                      # TP-20: RR only
        c.execute("SELECT knowledge.search(%s, %s, NULL, 'x', '{}', '{}', 5)", (w["A.reader"], [env["p"]]))


def test_ts01_search_sets_iterative_scan(su):
    cfg = su.execute("SELECT proconfig FROM pg_proc WHERE proname = 'search' AND pronamespace = 'knowledge'::regnamespace").fetchone()[0]
    assert "hnsw.iterative_scan=relaxed_order" in cfg


def test_st26_deleting_project(su, base, call, world, env):
    su.execute("UPDATE core.projects SET state = 'deleting', delete_after = now() + interval '1 day' WHERE id = %s", (env["p"],)); su.commit()
    try:
        assert search(base, world["A.reader"], [env["p"]], "x")["outcome"] == "PROJECT_DELETING"
    finally:
        su.execute("UPDATE core.projects SET state = 'active', delete_after = NULL WHERE id = %s", (env["p"],)); su.commit()


def test_tp20_pin_and_gc_between_pages(su, base, call, world, env):
    w = world
    j = create(call, world, env, "tst", "p")
    run(su, call, env, "tst", j["job_id"], docs={"a": "first words"})
    coll = j["collection_id"]
    pin = call.api("knowledge.pin", w["A.reader"], coll, None)
    assert pin["gen"] == 1
    j2 = create(call, world, env, "tst", "p", force=True)
    run(su, call, env, "tst", j2["job_id"], docs={"a": "second words"})
    call("maint", "knowledge.gc", coll)
    page = search(base, w["A.reader"], [env["p"]], "words", pins={coll: 1})
    assert [h["chunk"]["text"] for h in page["hits"]] == ["first words"] and page["generations"][coll] == 1
    assert search(base, w["A.reader"], [env["p"]], "words")["hits"][0]["chunk"]["text"] == "second words"
    call.api("knowledge.unpin", w["A.reader"], pin["pin_id"])
    assert call("maint", "knowledge.gc", coll)["versions"] == 1
    assert search(base, w["A.reader"], [env["p"]], "words", pins={coll: 1})["outcome"] == "GENERATION_GONE"


def test_st27_shared_value_same_answer(su, base, call, world, env):
    """Two projects of one tenant stage the same input: both get OK, one value row (I-K2, ST-27)."""
    w = world
    p2 = call.api("core.project_create", w["A.admin"], w["A"], f"m2b-{uuid.uuid4().hex[:6]}", rid())["id"]
    call.api("core.plugin_enable", w["A.admin"], p2, "tst", True, rid())
    for p in (env["p"], p2):
        j = call.api("runtime.create_job", w["A.admin"], p, "tst", "p", None, False, rid())
        assert run(su, call, env, "tst", j["job_id"], docs={"same": "identical text 27"})["outcome"] == "PUBLISHED"
    assert su.execute("SELECT count(*) FROM knowledge.representation_values WHERE input_hash = %s", (sha("identical text 27"),)).fetchone()[0] == 1
    stop_project_jobs(su, p2)


def test_tp14_retry_below_fence_floor(su, call, world, env):
    j = create(call, world, env, "tst", "p")["job_id"]
    call.api("runtime.cancel_job", world["A.contrib"], j, rid())
    j2 = create(call, world, env, "tst", "p")
    run(su, call, env, "tst", j2["job_id"], docs={"z": "zz"})
    call("maint", "knowledge.gc", j2["collection_id"])
    assert call.api("runtime.retry_job", world["A.op"], j, rid())["outcome"] == "RETRY_TOO_OLD"


def test_tp01_concurrent_publishers_same_scope(su, base, call, world, env):
    import threading
    for _ in range(3):
        j1 = create(call, world, env, "tst", "p", force=True)["job_id"]; call("runtime", "runtime.start_jobs", 50)
        j2 = create(call, world, env, "tst", "p", force=True)["job_id"]; call("runtime", "runtime.start_jobs", 50)
        payload(su, j1, docs={"a": f"one {j1}"}); payload(su, j2, docs={"a": f"two {j2}"})
        env["tst"].drain()
        gate, res = threading.Barrier(2), {}
        def pub(j):
            with connect(base, "runtime") as c:
                gate.wait(); res[j] = c.execute("SELECT knowledge.publish(%s)", (j,)).fetchone()[0]["outcome"]
        th = [threading.Thread(target=pub, args=(j,)) for j in (j1, j2)]
        [t.start() for t in th]; [t.join() for t in th]
        assert res[j2] == "PUBLISHED" and res[j1] in ("PUBLISHED", "SUPERSEDED", "STOPPED")
        coll = su.execute("SELECT collection_id FROM runtime.jobs WHERE id = %s", (j1,)).fetchone()[0]
        seqs = [r[0] for r in su.execute("SELECT j.seq FROM knowledge.generations g JOIN runtime.jobs j ON j.id = g.job_id "
                                         "WHERE g.collection_id = %s ORDER BY g.gen", (coll,)).fetchall()]
        assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)                    # PI-1
        assert su.execute("SELECT max(n) FROM (SELECT count(*) n FROM knowledge.unit_versions uv JOIN knowledge.units u ON u.id = uv.unit_id "
                          "WHERE u.collection_id = %s AND valid_from_gen IS NOT NULL AND valid_to_gen IS NULL GROUP BY uv.unit_id) x",
                          (coll,)).fetchone()[0] == 1                                    # PI-3
        assert visible(su, coll) == ["a"]
        call("maint", "runtime.sweep", 100)
