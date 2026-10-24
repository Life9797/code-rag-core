"""M4 ops: U4 approval gate (R15, O-17), connector secrets (P-P12, ST-07), observability views and status (§28.4)."""
import base64, os, uuid
import pytest
from conftest import connect, rid
from coderag_core import secrets
from test_m1b import stop_project_jobs, claim_one


@pytest.fixture
def proj(su, call, world):
    p = call.api("core.project_create", world["A.admin"], world["A"], f"ops-{uuid.uuid4().hex[:6]}", rid())["id"]
    assert call.api("core.plugin_enable", world["A.admin"], p, "tst", True, rid())["outcome"] == "OK"
    call.api("core.member_change", world["A.admin"], world["A"], p, world["A.op"], 3, rid())
    call.api("core.member_change", world["A.admin"], world["A"], p, world["A.contrib"], 2, rid())
    yield p
    stop_project_jobs(su, p)


def gate_job(call, world, p):
    j = call.api("runtime.create_job", world["A.contrib"], p, "tst", "ap", None, False, rid())["job_id"]
    call("runtime", "runtime.start_jobs", 50)
    g = call.api("runtime.get_job", world["A.op"], j)
    assert g["stages"]["gate"] == "waiting_approval" and g["stages"]["s"] == "blocked", g
    return j


def approve(call, who, j, h, d="approve", r=None):
    return call.api("runtime.approve", who, j, "gate", h, d, r or rid())


def test_u4_approve_hash_binding_replay(su, call, world, proj):
    j = gate_job(call, world, proj)
    assert approve(call, world["A.contrib"], j, None)["outcome"] == "FORBIDDEN"          # operator only
    su.execute("INSERT INTO core.artifacts (tenant_id, project_id, kind, storage_ref, sha256, provenance, created_by) "
               "VALUES (%s, %s, 'tst.ds', 'vol://a/1', 'aaa', %s, %s)", (world["A"], proj, f'{{"job_id": "{j}"}}', world["A.op"])); su.commit()
    assert approve(call, world["A.op"], j, "bbb")["outcome"] == "HASH_MISMATCH"
    st = call.api("core.status", world["A.op"])
    assert any(w["job_id"] == str(j) and w["artifact_sha256"] == "aaa" for w in st["waiting_approval"])
    r1 = rid()
    assert approve(call, world["A.op"], j, "aaa", r=r1)["outcome"] == "OK"
    assert approve(call, world["A.op"], j, "aaa", r=r1)["outcome"] == "OK"                 # replay
    assert approve(call, world["A.op"], j, "aaa", "reject")["outcome"] == "ALREADY_DECIDED"
    g = call.api("runtime.get_job", world["A.op"], j)
    assert g["stages"]["gate"] == "succeeded" and g["stages"]["s"] == "ready" and g["tasks"] == {"ready": 1}, g
    assert su.execute("SELECT approved_hash FROM runtime.job_stages WHERE job_id = %s AND stage_key = 'gate'", (j,)).fetchone()[0] == "aaa"


def test_u4_reject_and_expire(su, call, world, proj):
    j = gate_job(call, world, proj)
    assert approve(call, world["A.op"], j, None, "reject")["outcome"] == "OK"
    g = call.api("runtime.get_job", world["A.op"], j)
    assert g["status"] == "failed" and g["outcome"] == "core.approval_rejected" and g["stages"]["s"] == "canceled", g
    j2 = gate_job(call, world, proj)
    assert call("maint", "runtime.approval_tick")["waiting"] >= 1
    assert su.execute("SELECT count(*) FROM core.alerts WHERE code = 'core.approval_waiting' AND object LIKE %s AND cleared_at IS NULL",
                      (f"job:{j2}/%",)).fetchone()[0] == 1
    su.execute("UPDATE runtime.job_stages SET wait_until = now() - interval '1 second' WHERE job_id = %s", (j2,)); su.commit()
    assert call("maint", "runtime.approval_tick")["expired"] == 1
    g = call.api("runtime.get_job", world["A.op"], j2)
    assert g["status"] == "failed" and g["outcome"] == "core.approval_expired", g
    assert approve(call, world["A.op"], j2, None)["outcome"] == "ALREADY_DECIDED"          # expiry never implies approval
    assert su.execute("SELECT count(*) FROM core.alerts WHERE code = 'core.approval_waiting' AND object LIKE %s AND cleared_at IS NULL",
                      (f"job:{j2}/%",)).fetchone()[0] == 0


def test_st07_connector_secret(base, su, call, world, proj, tmp_path):
    os.environ["CODERAG_SECRETS_KEY"] = secrets.ensure_key(str(tmp_path / "master.key"))
    for t, val in (("A", "token-A"), ("B", "token-B")):
        kid, ct = secrets.encrypt(val)
        assert call.api("core.connector_secret_set", world[f"{t}.admin"], world[t], "tst.api", "default", kid, ct, rid())["outcome"] == "CREATED"
    kid, ct = secrets.encrypt("token-A2")
    assert call.api("core.connector_secret_set", world["A.admin"], world["A"], "tst.api", "default", kid, ct, rid())["version"] == 2
    assert call.api("core.connector_secret_set", world["A.op"], world["A"], "tst.api", "default", kid, ct, rid())["outcome"] == "FORBIDDEN"
    assert call.api("core.connector_secret_set", world["A.admin"], world["B"], "tst.api", "default", kid, ct, rid())["outcome"] == "FORBIDDEN"
    j = gate_job(call, world, proj)
    approve(call, world["A.op"], j, None)
    for _ in range(50):                                                  # skip tst tasks left by earlier modules
        t = claim_one(base, "plugin_tst")
        if t["job_id"] == str(j): break
    assert t["job_id"] == str(j)
    with connect(base, "plugin_tst") as c:
        g = lambda conn, name="default": c.execute("SELECT core.get_connector_secret(%s, %s, %s, %s, %s)",
                                                   (t["task_id"], t["lease_token"], t["lease_epoch"], conn, name)).fetchone()[0]
        r = g("tst.api")
        assert r["outcome"] == "OK" and r["version"] == 2
        assert secrets.decrypt(r["key_id"], base64.b64decode(r["ciphertext"])) == "token-A2"     # own tenant, newest version
        assert g("echo.api")["outcome"] == "FORBIDDEN"                                           # another plugin's connector
        assert g("tst.api", "other")["outcome"] == "FORBIDDEN"
    with connect(base, "plugin_echo") as c:                                                      # another plugin's task
        assert c.execute("SELECT core.get_connector_secret(%s, %s, %s, 'tst.api', 'default')",
                         (t["task_id"], t["lease_token"], t["lease_epoch"])).fetchone()[0]["outcome"] == "FORBIDDEN"
    with connect(base, "api") as c:
        with pytest.raises(Exception): c.execute("SELECT core.get_connector_secret(%s, %s, %s, 'tst.api', 'default')",
                                                 (t["task_id"], t["lease_token"], t["lease_epoch"]))


def test_observability_views(su, call, world, proj):
    gate_job(call, world, proj)
    with connect(call.base, "maint") as c:
        for v in ("runtime.v_queue", "runtime.v_job_durations", "runtime.v_breaker_state", "knowledge.v_collections",
                  "knowledge.v_gc_backlog", "core.v_plugins"):
            c.execute(f"SELECT * FROM {v} LIMIT 5").fetchall()
        assert ("tst",) in c.execute("SELECT id FROM core.v_plugins").fetchall()
    with connect(call.base, "api") as c:
        with pytest.raises(Exception): c.execute("SELECT * FROM runtime.v_queue").fetchall()
    st = call.api("core.status", world["root"])
    assert st["outcome"] == "OK" and isinstance(st["alerts"], list)
    assert call.api("core.status", world["A.reader"])["alerts"] is None
