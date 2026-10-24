"""M4 user API: projects, upload connector (paste + zip, CH-05 same paste = same revision), jobs, items, search."""
import io, os, uuid, zipfile
import pytest
from fastapi.testclient import TestClient
from conftest import connect, rid, DB
from coderag_core import api, auth, runner, storage
from coderag_core.coreadm import issue_client
from coderag_core.worker import Worker, SqlBinding
from plugins.code import main as code
from test_m1b import stop_project_jobs


@pytest.fixture(scope="module")
def h(base, su, world, tmp_path_factory):
    old = storage.ROOT
    storage.ROOT = str(tmp_path_factory.mktemp("vol"))
    code._models.clear(); code._models["chat"] = None
    if not su.execute("SELECT core.signing_key_active()").fetchone()[0]:
        auth.rotate_key(su, str(tmp_path_factory.mktemp("keys")), world["root"]); su.commit()
    client = TestClient(api.create_app(api.Settings(dsn_for=lambda login: f"{base} user={login} dbname={DB}")))
    def tok(role):
        r, secret = issue_client(su, world["root"], world["A"], f"svc-{role}-{uuid.uuid4().hex[:4]}", role)
        t = client.post("/core/v1/token", data={"client_id": r["client_id"], "client_secret": secret}).json()["access_token"]
        return {"Authorization": f"Bearer {t}"}
    yield {"c": client, "admin": tok(4), "reader": tok(1)}
    storage.ROOT = old


def drive(base, call, world, j):
    cn = lambda login: connect(base, login)
    with connect(base, "plugin_code") as c:
        w = Worker("code", "0.1.0", code.HANDLERS, SqlBinding(c), db=c)
        for _ in range(50):
            runner.runtime_tick(cn)
            with connect(base, "postgres") as su:
                if su.execute("SELECT finished_at IS NOT NULL FROM runtime.jobs WHERE id = %s", (j,)).fetchone()[0]: break
            w.drain()


def test_upload_paste_job_search(base, su, call, world, h):
    c, A = h["c"], h["admin"]
    p = c.post("/core/v1/projects", json={"tenant": str(world["A"]), "name": f"api-{uuid.uuid4().hex[:6]}"}, headers=A).json()["id"]
    assert c.post(f"/core/v1/projects/{p}/plugins/code", json={}, headers=A).json()["outcome"] == "OK"
    assert any(x["id"] == p for x in c.get("/core/v1/projects", headers=A).json()["projects"])
    s = c.post(f"/core/v1/projects/{p}/sources", json={"external_id": "chat:1"}, headers=A).json()["id"]
    paste = {"files": [{"path": "pasted/1.py", "text": "def checkout(cart):\n    '''Pay for the cart.'''\n    return sum(cart)\n"}],
             "plugin": "code", "request_id": "chat1-msg1"}
    r = c.post(f"/core/v1/sources/{s}/revisions", json=paste, headers=A).json()
    assert r["outcome"] == "CREATED" and r["job"]["outcome"] == "CREATED", r
    again = c.post(f"/core/v1/sources/{s}/revisions", json=paste, headers=A).json()       # CH-09: resent message replays
    assert again["revision_id"] == r["revision_id"] and again["job"]["job_id"] == r["job"]["job_id"]
    drive(base, call, world, r["job"]["job_id"])
    g = c.get(f"/core/v1/jobs/{r['job']['job_id']}", headers=A).json()
    assert g["outcome"] == "PUBLISHED", g
    items = c.get(f"/core/v1/jobs/{r['job']['job_id']}/items", headers=A).json()
    assert any(i["key"] == "pasted/1.py" for i in items["items"]), items
    hits = c.post("/core/v1/search", json={"projects": [p], "query": "pay cart checkout", "k": 5}, headers=A).json()
    assert hits["outcome"] == "OK" and hits["hits"][0]["attrs"]["symbol"].endswith("checkout"), hits
    # same paste under a new request id -> same revision (revision_key = content hash), CH-05
    r2 = c.post(f"/core/v1/sources/{s}/revisions", json={**paste, "request_id": None, "plugin": None}, headers=A).json()
    assert r2["outcome"] == "EXISTS" and r2["revision_id"] == r["revision_id"]
    # zip upload (multipart)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z: z.writestr("repo/m.py", "x = 1\n"); z.writestr("repo/n.py", "y = 2\n")
    rz = c.post(f"/core/v1/sources/{s}/revisions", files={"archive": ("a.zip", buf.getvalue())}, headers=A).json()
    assert rz["outcome"] == "CREATED" and rz["files"] == 2
    # a reader cannot upload: refused before any file is written
    before = sum(len(f) for _, _, f in os.walk(storage.ROOT))
    rr = c.post(f"/core/v1/sources/{s}/revisions", json=paste, headers=h["reader"])
    assert rr.status_code == 403 and sum(len(f) for _, _, f in os.walk(storage.ROOT)) == before
    bad = c.post(f"/core/v1/sources/{s}/revisions", json={"files": [{"path": "../x.py", "text": "x"}]}, headers=A)
    assert bad.status_code == 400
    assert c.get("/core/v1/projects").status_code == 401
    assert c.post("/core/v1/token", data={"client_id": "", "client_secret": ""}).status_code == 401
    h["last"] = {"p": p, "s": s, "job": r["job"]["job_id"], "rev": r["revision_id"]}
    stop_project_jobs(su, p)


def test_other_tenant_ids_refused(su, world, h):
    """Review 2 P0-1: a tenant-B admin token gets nothing from tenant A's ids over the HTTP API."""
    c, x = h["c"], h["last"]
    r, secret = issue_client(su, world["root"], world["B"], f"svc-b-{uuid.uuid4().hex[:4]}", 4)
    B = {"Authorization": "Bearer " + c.post("/core/v1/token", data={"client_id": r["client_id"], "client_secret": secret}).json()["access_token"]}
    for path in (f"/core/v1/projects/{x['p']}", f"/core/v1/jobs/{x['job']}", f"/core/v1/jobs/{x['job']}/items",
                 f"/core/v1/projects/{x['p']}/jobs", f"/core/v1/tenants/{world['A']}"):
        res = c.get(path, headers=B)
        assert res.status_code in (403, 404), (path, res.json())
    for path, body in ((f"/core/v1/sources/{x['s']}/revisions", {"files": [{"path": "a.py", "text": "x"}]}),
                       (f"/core/v1/jobs/{x['job']}/cancel", {}), (f"/core/v1/projects/{x['p']}/members", {"tenant": str(world["A"])}),
                       ("/core/v1/jobs", {"project": x["p"], "plugin": "code", "pipeline": "index", "revision": x["rev"]})):
        assert c.post(path, json=body, headers=B).status_code in (403, 404), path
    hits = c.post("/core/v1/search", json={"projects": [x["p"]], "query": "pay cart checkout"}, headers=B).json()
    assert hits["outcome"] in ("FORBIDDEN", "NOT_FOUND") or not hits.get("hits"), hits


def test_token_rate_limit(h):
    c = h["c"]
    codes = [c.post("/core/v1/token", data={"client_id": "00000000-0000-0000-0000-00000000000a", "client_secret": "x"}).status_code
             for _ in range(12)]
    assert codes[0] == 401 and codes[-1] == 429


def test_status_and_approve_routes(su, call, world, h):
    c, A = h["c"], h["admin"]
    st = c.get("/core/v1/status", headers=A).json()
    assert st["outcome"] == "OK" and "waiting_approval" in st
    r = c.post("/core/v1/jobs/00000000-0000-0000-0000-000000000000/approve", json={"stage": "gate"}, headers=A)
    assert r.status_code == 403
    assert c.post("/core/v1/projects", json={"tenant": "TENANT", "name": "x"}, headers=A).status_code == 400
    assert c.post(f"/core/v1/tenants/{world['A']}/secrets", json={"connector": "tst.api"}, headers=A).status_code == 400
