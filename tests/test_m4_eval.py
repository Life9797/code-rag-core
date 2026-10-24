"""M4 eval plugin (§35): EV-01 identical runs give identical scores, EV-02 forbidden project -> run fails eval.forbidden,
EV-03 baseline compare flags a 0.03 recall drop, EV-04 purge removes runs and results."""
import glob, io, os, uuid, zipfile
import pytest
from conftest import connect, rid
from coderag_core import runner
from coderag_core.worker import Worker, SqlBinding
from plugins.code import main as code
from plugins.eval import main as ev
from test_m3 import upload, vol, GOLDEN, ZIPS  # noqa: F401
from test_m1b import stop_project_jobs


def drive(base, su, j):
    cn = lambda login: connect(base, login)
    with connect(base, "plugin_code") as c1, connect(base, "plugin_eval") as c2:
        ws = [Worker("code", "0.1.0", code.HANDLERS, SqlBinding(c1), db=c1), Worker("eval", "0.1.0", ev.HANDLERS, SqlBinding(c2), db=c2)]
        for _ in range(60):
            runner.runtime_tick(cn)
            if su.execute("SELECT finished_at IS NOT NULL FROM runtime.jobs WHERE id = %s", (j,)).fetchone()[0]: break
            for w in ws: w.drain()
    su.commit()
    return su.execute("SELECT status, outcome FROM runtime.jobs WHERE id = %s", (j,)).fetchone()


@pytest.mark.skipif(not ZIPS, reason="golden repos not mounted")
def test_ev01_ev02_ev04(base, su, call, world, vol, tmp_path):
    w = world
    p = call.api("core.project_create", w["A.admin"], w["A"], f"ev-{uuid.uuid4().hex[:6]}", rid())["id"]
    for plug in ("code", "eval"): call.api("core.plugin_enable", w["A.admin"], p, plug, True, rid())
    for z in ZIPS:
        s, rev = upload(call, w, p, z, os.path.basename(z)[:30])
        j = call.api("runtime.create_job", w["A.admin"], p, "code", "index", rev, False, rid())["job_id"]
        assert drive(base, su, j) == ("succeeded", "PUBLISHED")
    gz = tmp_path / "golden.zip"
    with zipfile.ZipFile(gz, "w") as zf:
        for f in glob.glob(f"{GOLDEN}/*"): zf.write(f, "golden/" + os.path.basename(f))
    _, grev = upload(call, w, p, str(gz), "golden-set")
    scores = []
    for force in (False, True):
        j = call.api("runtime.create_job", w["A.admin"], p, "eval", "run", grev, force, rid())["job_id"]
        assert drive(base, su, j) == ("succeeded", "DONE")
        rep = su.execute("SELECT detail FROM runtime.job_items WHERE job_id = %s AND key = 'report'", (j,)).fetchone()[0]
        s = rep["summary"]
        assert s["all"]["queries"] == 180 and s["all"]["errors"] == 0 and s["en"]["recall_at_k"] > 0.2
        scores.append({l: (v["recall_at_k"], v["hit_at_k"], v["mrr"]) for l, v in s.items()})
    assert scores[0] == scores[1]                                                   # EV-01 (stub embedder)
    # EV-02: the job creator loses access before the queries run
    call.api("core.member_change", w["A.admin"], w["A"], p, w["A.contrib"], 2, rid())
    j = call.api("runtime.create_job", w["A.contrib"], p, "eval", "run", grev, True, rid())["job_id"]
    call.api("core.member_change", w["A.admin"], w["A"], p, w["A.contrib"], None, rid())
    assert drive(base, su, j)[0] == "failed"
    assert su.execute("SELECT count(*) FROM runtime.task_events e JOIN runtime.tasks t ON t.id = e.task_id "
                      "WHERE t.job_id = %s AND e.error_code = 'eval.forbidden'", (j,)).fetchone()[0] >= 1
    # EV-04
    with connect(base, "plugin_eval") as c:
        ev.purge(c, p, {})
        assert c.execute("SELECT count(*) FROM plugin_eval.results r JOIN plugin_eval.runs u USING (job_id) WHERE u.project_id = %s",
                         (p,)).fetchone()[0] == 0
    stop_project_jobs(su, p)


def test_ev03_baseline_compare():
    s = {"en": {"recall_at_k": 0.909, "p95_ms": 40}, "ko": {"recall_at_k": 0.92, "p95_ms": 40}}
    c = ev.compare(s, ev.BASELINE)
    assert c["verdict"] == "fail" and c["flags"] == ["recall_drop:en"]
    assert ev.compare({"en": {"recall_at_k": 0.93, "p95_ms": 151}}, ev.BASELINE)["flags"] == ["p95:en"]
