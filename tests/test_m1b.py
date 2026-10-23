"""M1b: runtime contracts and the echo plugin over the in-process binding (EC-01..04, ST-12/13, TP-18/19/21, RT-03/04)."""
import json, uuid
import psycopg, pytest
from conftest import connect, rid
from coderag_core.worker import Worker, SqlBinding
from plugins.echo import main as echo_main


@pytest.fixture
def proj(su, call, world):
    w = world
    p = call.api("core.project_create", w["A.admin"], w["A"], f"m1b-{uuid.uuid4().hex[:6]}", rid())["id"]
    for plug in ("echo", "tst"):
        assert call.api("core.plugin_enable", w["A.admin"], p, plug, True, rid())["outcome"] == "OK"
    call.api("core.member_change", w["A.admin"], w["A"], p, w["A.contrib"], 2, rid())
    call.api("core.member_change", w["A.admin"], w["A"], p, w["A.reader"], 1, rid())
    yield p
    stop_project_jobs(su, p)


def stop_project_jobs(su, p):
    """Teardown: end this project's jobs so later tests claim only their own tasks."""
    su.execute("UPDATE runtime.tasks SET status = 'ready', lease_token = NULL, lease_until = NULL "
               "WHERE project_id = %s AND status = 'running'", (p,))
    su.execute("UPDATE runtime.jobs SET status = 'canceled', stop_requested = 'cancel', finished_at = now() "
               "WHERE project_id = %s AND status = 'queued'", (p,))
    su.execute("UPDATE runtime.jobs SET stop_requested = 'cancel' WHERE project_id = %s AND finished_at IS NULL", (p,))
    for (j,) in su.execute("SELECT id FROM runtime.jobs WHERE project_id = %s AND finished_at IS NULL", (p,)).fetchall():
        su.execute("SELECT runtime.finish_stopped_jobs(%s)", (j,))
    su.commit()


@pytest.fixture
def echo(base):
    c = connect(base, "plugin_echo")
    yield Worker("echo", "0.1.0", echo_main.HANDLERS, SqlBinding(c), db=c)
    c.close()


def job(call, world, proj, plugin="echo", pipeline="run", who="A.contrib"):
    r = call.api("runtime.create_job", world[who], proj, plugin, pipeline, None, False, rid())
    assert r["outcome"] == "CREATED", r
    return r["job_id"]


def start(call): return call("runtime", "runtime.start_jobs", 50)
def sweep(call): return call("maint", "runtime.sweep", 500)
def tasks(su, j): return su.execute("SELECT status, resolution, depth, attempt FROM runtime.tasks WHERE job_id = %s", (j,)).fetchall()
def get(call, world, j): return call.api("runtime.get_job", world["A.reader"], j)


def test_ec01_split_depth2_all_ok(su, call, world, proj, echo):
    j = job(call, world, proj); start(call)
    echo.drain()
    ts = tasks(su, j)
    assert len(ts) == 1 + 2 + 4
    assert all(r == "ok" for _, r, _, _ in ts)
    g = get(call, world, j)
    assert g["stages"] == {"fan": "succeeded", "publish": "ready"} and g["status"] == "running"
    assert su.execute("SELECT count(*) FROM plugin_echo.calls c JOIN runtime.tasks t ON t.id = c.task_id WHERE t.job_id = %s", (j,)).fetchone()[0] == 4


def test_policy_snapshot_fanout_and_rt04(su, call, world, proj, echo):
    assert call.api("core.policy_set", world["A.admin"], "project", str(proj), "echo.fanout", "3", rid())["outcome"] == "OK"
    j = job(call, world, proj); start(call)
    assert call.api("core.policy_set", world["A.admin"], "project", str(proj), "echo.fanout", "1", rid())["outcome"] == "OK"
    echo.drain()
    assert len(tasks(su, j)) == 1 + 3 + 9                                               # RT-04: snapshot wins mid-job


def claim_one(base, login="plugin_echo"):
    with connect(base, login) as c:
        r = SqlBinding(c).claim(1, ["0.1.0"], str(uuid.uuid4()))
    assert r["outcome"] == "OK", r
    return r["tasks"][0]


def test_st13_split_replay_same_children(base, call, world, proj):
    job(call, world, proj); start(call)
    t = claim_one(base)
    with connect(base, "plugin_echo") as c:
        b, r = SqlBinding(c), str(uuid.uuid4())
        kids = [{"key": "a"}, {"key": "b"}]
        first, again = b.split(t, kids, r), b.split(t, kids, r)
    assert first["outcome"] == "SPLIT" and again["children"] == first["children"] and again["replay"]


def test_st12_stale_lease_no_change(su, base, call, world, proj):
    j = job(call, world, proj); start(call)
    t = claim_one(base)
    su.execute("UPDATE runtime.tasks SET lease_until = now() - interval '1 second' WHERE id = %s", (t["task_id"],)); su.commit()
    assert sweep(call)["expired"] == 1                                                  # EC-03: back to ready
    t2 = claim_one(base)
    assert t2["task_id"] == t["task_id"] and t2["attempt"] == 2 and t2["lease_epoch"] == t["lease_epoch"] + 1
    with connect(base, "plugin_echo") as c:
        b = SqlBinding(c)
        assert b.complete(t, {}, str(uuid.uuid4()))["outcome"] == "STALE_LEASE"           # EC-04
        assert b.fail(t, "echo.boom", {}, str(uuid.uuid4()))["outcome"] == "STALE_LEASE"
        assert b.split(t, [{"key": "x"}], str(uuid.uuid4()))["outcome"] == "STALE_LEASE"
        assert b.heartbeat(t)["outcome"] == "STALE_LEASE"
    assert su.execute("SELECT status FROM runtime.tasks WHERE id = %s", (t["task_id"],)).fetchone()[0] == "running"


def test_tp21_other_plugin_and_terminal(base, call, world, proj):
    job(call, world, proj); start(call)
    t = claim_one(base)
    with connect(base, "plugin_tst") as c:
        assert SqlBinding(c).complete(t, {}, str(uuid.uuid4()))["outcome"] == "FORBIDDEN"
    with connect(base, "plugin_echo") as c:
        b = SqlBinding(c)
        assert b.complete(t, {}, str(uuid.uuid4()))["outcome"] == "OK"
        assert b.complete(t, {}, str(uuid.uuid4()))["outcome"] == "ALREADY_TERMINAL"
    for login in ("api", "runtime"):
        with connect(base, login) as c, pytest.raises(psycopg.errors.InsufficientPrivilege):
            SqlBinding(c).complete(t, {}, str(uuid.uuid4()))


def test_retry_then_exhausted_job_failed(su, base, call, world, proj):
    j = job(call, world, proj); start(call)
    su.execute("UPDATE runtime.tasks SET payload = '{\"boom\": true}' WHERE job_id = %s", (j,)); su.commit()
    w = Worker("echo", "0.1.0", echo_main.HANDLERS, SqlBinding(connect(base, "plugin_echo")))
    outs = []
    for _ in range(3):
        su.execute("UPDATE runtime.tasks SET ready_at = now() WHERE job_id = %s AND status = 'retry_wait'", (j,)); su.commit()
        t = claim_one(base); outs.append(w.run_task(t)["outcome"])
    assert outs == ["RETRY_SCHEDULED", "RETRY_SCHEDULED", "FAILED"]                   # max_attempts 3 (§41)
    assert get(call, world, j)["stages"]["fan"] == "failed"
    sweep(call)
    g = get(call, world, j)
    assert g["status"] == "failed" and g["stages"]["publish"] == "canceled"
    assert su.execute("SELECT count(*) FROM runtime.task_events e JOIN runtime.tasks t ON t.id = e.task_id "
                      "WHERE t.job_id = %s AND e.op = 'terminal_fail'", (j,)).fetchone()[0] == 1
    r = call.api("runtime.retry_job", world["A.admin"], j, rid())                       # U3
    assert r["outcome"] == "CREATED"
    assert su.execute("SELECT retry_of = %s AND force AND fence_seq = (SELECT fence_seq FROM runtime.jobs WHERE id = %s) "
                      "FROM runtime.jobs WHERE id = %s", (j, j, r["job_id"])).fetchone()[0]
    assert call.api("runtime.retry_job", world["A.contrib"], j, rid())["outcome"] == "FORBIDDEN"


def test_unknown_error_code(su, base, call, world, proj):
    job(call, world, proj); start(call)
    t = claim_one(base)
    with connect(base, "plugin_echo") as c:
        assert SqlBinding(c).fail(t, "tst.whatever", {}, str(uuid.uuid4()))["outcome"] == "UNKNOWN_ERROR_CODE"
    assert su.execute("SELECT status, error_code FROM runtime.tasks WHERE id = %s", (t["task_id"],)).fetchone() == ("failed", "core.unknown_error_code")


def test_rt03_killed_every_claim_fails_after_max_attempts(su, base, call, world, proj):
    j = job(call, world, proj); start(call)
    for _ in range(3):
        t = claim_one(base)
        su.execute("UPDATE runtime.tasks SET lease_until = now() - interval '1 second' WHERE id = %s", (t["task_id"],)); su.commit()
        sweep(call)
    assert su.execute("SELECT status, error_code FROM runtime.tasks WHERE job_id = %s", (j,)).fetchone() == ("failed", "core.attempts_exhausted")
    assert get(call, world, j)["status"] == "failed"


def test_cancel_running_job_tp18(su, base, call, world, proj):
    j = job(call, world, proj); start(call)
    t = claim_one(base)
    assert call.api("runtime.cancel_job", world["A.reader"], j, rid())["outcome"] == "FORBIDDEN"
    assert call.api("runtime.cancel_job", world["A.contrib"], j, rid())["outcome"] == "OK"   # own job
    with connect(base, "plugin_echo") as c:
        b = SqlBinding(c)
        assert b.heartbeat(t)["outcome"] == "STOP"
        assert b.split(t, [{"key": "x"}], str(uuid.uuid4()))["outcome"] == "JOB_STOPPING"
        assert b.claim(5, ["0.1.0"], str(uuid.uuid4()))["outcome"] == "EMPTY"             # nothing runs after stop
    sweep(call)
    assert get(call, world, j)["status"] == "canceled"
    assert call.api("runtime.cancel_job", world["A.contrib"], j, rid())["outcome"] == "ALREADY_TERMINAL"


def test_terminal_immutable(su, call, world, proj):
    j = job(call, world, proj)
    call.api("runtime.cancel_job", world["A.contrib"], j, rid())
    with pytest.raises(psycopg.errors.RaiseException):
        with su.transaction(): su.execute("UPDATE runtime.jobs SET status = 'running', finished_at = NULL WHERE id = %s", (j,))


def test_full_mode_coalescing(su, call, world, proj):
    j1 = job(call, world, proj, "tst", "p"); j2 = job(call, world, proj, "tst", "p")
    assert su.execute("SELECT status FROM runtime.jobs WHERE id = %s", (j1,)).fetchone()[0] == "superseded"
    assert su.execute("SELECT status FROM runtime.jobs WHERE id = %s", (j2,)).fetchone()[0] == "queued"


def test_create_job_refusals(call, world, proj):
    w = world
    assert call.api("runtime.create_job", w["A.reader"], proj, "echo", "run", None, False, rid())["outcome"] == "FORBIDDEN"
    assert call.api("runtime.create_job", w["B.admin"], proj, "echo", "run", None, False, rid())["outcome"] == "FORBIDDEN"
    call.api("core.plugin_enable", w["A.admin"], proj, "echo", False, rid())
    assert call.api("runtime.create_job", w["A.contrib"], proj, "echo", "run", None, False, rid())["outcome"] == "PLUGIN_DISABLED"
    assert call.api("runtime.create_job", w["A.contrib"], proj, "tst", "nope", None, False, rid())["outcome"] == "NOT_FOUND"
    r = rid()
    a = call.api("runtime.create_job", w["A.contrib"], proj, "tst", "p", None, False, r)
    assert call.api("runtime.create_job", w["A.contrib"], proj, "tst", "p", None, False, r)["job_id"] == a["job_id"]


def test_heartbeat_extends_deadline_bounded(base, call, world, proj):
    job(call, world, proj); start(call)
    t = claim_one(base)
    with connect(base, "plugin_echo") as c:
        b = SqlBinding(c)
        ds = [b.heartbeat(t, "ck1", True)["deadline_at"] for _ in range(5)]
    assert len(set(ds)) == 3                                                            # 3 extensions max (§41)


def test_tp19_siblings_complete_together(su, base, call, world, proj):
    import threading
    for _ in range(5):
        j = job(call, world, proj); start(call)
        root = claim_one(base)
        with connect(base, "plugin_echo") as c:
            SqlBinding(c).split(root, [{"key": "a"}, {"key": "b"}], str(uuid.uuid4()))
        kids = [claim_one(base), claim_one(base)]
        gate = threading.Barrier(2)
        def done(t):
            with connect(base, "plugin_echo") as c:
                gate.wait(); SqlBinding(c).complete(t, {}, str(uuid.uuid4()))
        th = [threading.Thread(target=done, args=(k,)) for k in kids]
        [x.start() for x in th]; [x.join() for x in th]
        g = get(call, world, j)
        assert g["stages"] == {"fan": "succeeded", "publish": "ready"}
        assert su.execute("SELECT count(*) FROM runtime.lifecycle_events WHERE job_id = %s AND stage_key = 'fan'", (j,)).fetchone()[0] == 1
        stop_project_jobs(su, proj)
