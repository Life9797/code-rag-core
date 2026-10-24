"""M1b HTTP binding: EC-02 (split replay over HTTP), EC-08 (same task_events as in-process), worker scope rules."""
import uuid
import pytest
from fastapi.testclient import TestClient
from conftest import connect, rid, DB
from coderag_core import api, auth
from coderag_core.coreadm import issue_client
from coderag_core.worker import Worker, SqlBinding, HttpBinding
from plugins.echo import main as echo_main
from test_m1b import job, start, stop_project_jobs


@pytest.fixture(scope="module")
def http(base, su, world, tmp_path_factory):
    if not su.execute("SELECT core.signing_key_active()").fetchone()[0]:
        auth.rotate_key(su, str(tmp_path_factory.mktemp("keys")), world["root"]); su.commit()
    client = TestClient(api.create_app(api.Settings(dsn_for=lambda login: f"{base} user={login} dbname={DB}")))
    r, secret = issue_client(su, world["root"], world["A"], "plugin.echo", 1, scopes=("runtime.worker:echo",))
    tok = client.post("/core/v1/token", data={"client_id": r["client_id"], "client_secret": secret}).json()["access_token"]
    def post(path, body): return client.post(path, json=body, headers={"Authorization": f"Bearer {tok}"}).json()
    return {"client": client, "post": post, "token": tok}


@pytest.fixture
def proj(su, call, world):
    w = world
    p = call.api("core.project_create", w["A.admin"], w["A"], f"http-{uuid.uuid4().hex[:6]}", rid())["id"]
    call.api("core.plugin_enable", w["A.admin"], p, "echo", True, rid())
    call.api("core.member_change", w["A.admin"], w["A"], p, w["A.contrib"], 2, rid())
    yield p
    stop_project_jobs(su, p)


def events(su, j):
    return sorted(su.execute("""
        SELECT coalesce(t.child_key, 'root'), e.op, e.outcome, e.lease_epoch, coalesce(e.error_code, '')
        FROM runtime.task_events e JOIN runtime.tasks t ON t.id = e.task_id WHERE t.job_id = %s""", (j,)).fetchall())


def test_ec08_transport_neutral(base, su, call, world, proj, http):
    j1 = job(call, world, proj); start(call)
    with connect(base, "plugin_echo") as c:
        Worker("echo", "0.1.0", echo_main.HANDLERS, SqlBinding(c), db=c).drain()
    stop_project_jobs(su, proj)
    j2 = job(call, world, proj); start(call)
    Worker("echo", "0.1.0", echo_main.HANDLERS, HttpBinding(post=http["post"])).drain()
    assert len(events(su, j2)) == 7 * 2 and events(su, j1) == events(su, j2)


def test_ec02_split_replay_over_http(call, world, proj, http):
    job(call, world, proj); start(call)
    b = HttpBinding(post=http["post"])
    t = b.claim(1, ["0.1.0"], str(uuid.uuid4()))["tasks"][0]
    r = str(uuid.uuid4())
    first, again = b.split(t, [{"key": "x"}, {"key": "y"}], r), b.split(t, [{"key": "x"}, {"key": "y"}], r)
    assert first["outcome"] == "SPLIT" and again["children"] == first["children"]


def test_http_auth_rules(su, call, world, http):
    c = http["client"]
    assert c.post("/runtime/v1/claim", json={"max": 1, "versions": ["0.1.0"], "request_id": str(uuid.uuid4())}).status_code == 401
    # a token without a worker scope cannot use the binding
    r, s = issue_client(su, world["root"], world["A"], "nowork", 1)
    tok = c.post("/core/v1/token", data={"client_id": r["client_id"], "client_secret": s}).json()["access_token"]
    assert c.post("/runtime/v1/claim", json={"max": 1, "versions": ["0.1.0"], "request_id": str(uuid.uuid4())},
                  headers={"Authorization": f"Bearer {tok}"}).status_code == 403
    assert c.post("/core/v1/token", data={"client_id": r["client_id"], "client_secret": "bad"}).status_code == 401
    # only a platform admin may hand out worker scopes
    r2, _ = issue_client(su, world["A.admin"], world["A"], "sneaky", 1, scopes=("runtime.worker:echo",))
    assert r2["outcome"] == "FORBIDDEN"
