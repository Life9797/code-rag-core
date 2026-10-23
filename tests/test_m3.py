"""M3: code plugin over golden repos (stub embedder, stub or fake LLM): CP-01..04, reuse, NO_CHANGE, search, purge."""
import glob, hashlib, io, json, os, sys, uuid, zipfile
import psycopg, pytest
from conftest import connect, rid
from test_m1b import stop_project_jobs
from coderag_core import files, runner, storage
from coderag_core.worker import Worker, SqlBinding
from plugins.code import main as code

GOLDEN = "/mnt/project-files/golden"
ZIPS = sorted(glob.glob(f"{GOLDEN}/*-src-*.zip"))


@pytest.fixture(scope="module")
def vol(tmp_path_factory):
    old = storage.ROOT
    storage.ROOT = str(tmp_path_factory.mktemp("vol"))
    code._models.clear()
    code._models["chat"] = None                              # stub knowledge unless a test sets a fake chat
    yield storage.ROOT
    storage.ROOT = old


@pytest.fixture
def proj(su, call, world, vol):
    w = world
    p = call.api("core.project_create", w["A.admin"], w["A"], f"m3-{uuid.uuid4().hex[:6]}", rid())["id"]
    assert call.api("core.plugin_enable", w["A.admin"], p, "code", True, rid())["outcome"] == "OK"
    yield p
    stop_project_jobs(su, p)


def upload(call, world, p, zip_path, name="src"):
    s = call.api("core.source_create", world["A.admin"], p, "core.upload", name, False, rid())["id"]
    ref = f"vol://sources/{s}/{uuid.uuid4().hex[:8]}"
    man = files.safe_extract(zip_path, ref)
    mh = hashlib.sha256(json.dumps(man, sort_keys=True).encode()).hexdigest()
    r = call.api("core.revision_create", world["A.admin"], s, mh[:16], json.dumps(man), mh, ref, True, rid())
    assert r["outcome"] == "CREATED", r
    return s, r["id"]


def run(base, call, world, p, rev, force=False):
    j = call.api("runtime.create_job", world["A.admin"], p, "code", "index", rev, force, rid())
    assert j["outcome"] == "CREATED", j
    cn = lambda login: connect(base, login)
    with connect(base, "plugin_code") as c:
        w = Worker("code", "0.1.0", code.HANDLERS, SqlBinding(c), db=c)
        for _ in range(50):
            runner.runtime_tick(cn)
            g = call.api("runtime.get_job", world["A.admin"], j["job_id"])
            if g["status"] in ("succeeded", "failed", "canceled"):
                break
            w.drain()
    return call.api("runtime.get_job", world["A.admin"], j["job_id"])


def make_zip(tmp_path, members):
    z = tmp_path / "x.zip"
    with zipfile.ZipFile(z, "w") as zf:
        for name, data in members.items(): zf.writestr(name, data)
    return str(z)


def items(su, j):
    return {k: (s, c, d) for k, s, c, d in su.execute("SELECT key, status, code, detail FROM runtime.job_items WHERE job_id = %s", (j,))}


@pytest.mark.skipif(not ZIPS, reason="golden repos not mounted")
def test_cp01_golden_repos_index_and_chunk_boundaries(su, base, call, world, proj):
    sys.path.insert(0, "/home/claude/code-rag/worker")
    for z in ZIPS:
        s, rev = upload(call, world, proj, z, os.path.basename(z)[:30])
        g = run(base, call, world, proj, rev)
        assert g["status"] == "succeeded" and g["outcome"] == "PUBLISHED", g
        # chunk count equals the parser's chunk list for every python file (no drops, no merges; none above max_chars)
        man = su.execute("SELECT manifest, storage_ref FROM core.source_revisions WHERE id = %s", (rev,)).fetchone()
        expect = 0
        for f in man[0]["files"]:
            if f["path"].endswith(".py"):
                text = files.read(man[1], f["path"]).decode()
                if text.strip(): expect += len(code.py.parse_file(f["path"], text, 5000)["chunks"])
        got = su.execute("""SELECT count(*) FROM knowledge.chunks c JOIN knowledge.unit_versions uv ON uv.id = c.unit_version_id
                            JOIN knowledge.units u ON u.id = uv.unit_id WHERE u.source_id = %s AND uv.valid_to_gen IS NULL""", (s,)).fetchone()[0]
        assert got == expect > 0
        q = su.execute("SELECT kind, raw_total, resolved, external, attemptable FROM plugin_code.graph_quality WHERE job_id = %s", (g["job_id"],)).fetchall()
        assert {k for k, *_ in q} == {"dependency", "call"} and all(r + e <= a for _, _, r, e, a in q)


def test_rerun_no_change_and_value_reuse(su, base, call, world, proj, tmp_path):
    z = make_zip(tmp_path, {"pkg/a.py": "def f():\n    '''Add.'''\n    return g()\n\ndef g():\n    return 1\n",
                            "pkg/b.py": "from pkg.a import f\n\nclass C:\n    def m(self):\n        return f()\n", "setup.cfg": ""})
    s, rev = upload(call, world, proj, z)
    g1 = run(base, call, world, proj, rev)
    assert g1["outcome"] == "PUBLISHED", g1
    n_vals = su.execute("SELECT count(*) FROM knowledge.representation_values WHERE tenant_id = %s", (world["A"],)).fetchone()[0]
    g2 = run(base, call, world, proj, rev, force=True)
    assert g2["outcome"] == "NO_CHANGE", g2                  # every unit touched
    assert su.execute("SELECT count(*) FROM knowledge.representation_values WHERE tenant_id = %s", (world["A"],)).fetchone()[0] == n_vals
    # graph: b.C.m calls pkg.a.f (import_binding) -> file imports + symbol calls attrs
    attrs = dict(su.execute("""SELECT u.key, uv.attrs FROM knowledge.units u JOIN knowledge.unit_versions uv ON uv.unit_id = u.id
                               WHERE u.source_id = %s AND uv.valid_to_gen IS NULL""", (s,)).fetchall())
    assert attrs["pkg/b.py"]["imports"] == ["pkg/a.py"]
    assert attrs["pkg/b.py::pkg.b.C.m"]["calls"] == ["pkg.a.f"]
    # search: FTS + both vector kinds, REPEATABLE READ
    with connect(base, "api", autocommit=False) as c:
        c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        r = c.execute("SELECT knowledge.search(%s, %s, NULL, %s, %s, NULL, 5)",
                      (world["A.admin"], [proj], "add", json.dumps(code.query_vectors("add")))).fetchone()[0]
    assert r["outcome"] == "OK" and r["hits"] and r["hits"][0]["attrs"]["path"].startswith("pkg/")


def test_cp02_cp949_and_cp03_unknown_extension(su, base, call, world, proj, tmp_path):
    z = make_zip(tmp_path, {"k.py": "# 한글 주석\ndef f():\n    return 1\n".encode("cp949"), "notes.xyz": "x", "README.md": "# r",
                            "big.py": "x = 1\n" * 10, "empty.py": "  \n"})
    call.api("core.policy_set", world["A.admin"], "project", str(proj), "code.max_file_bytes", "40", rid())
    s, rev = upload(call, world, proj, z)
    g = run(base, call, world, proj, rev)
    assert g["outcome"] == "PUBLISHED", g
    it = items(su, g["job_id"])
    assert it[".skipped"][2] == {"unsupported_language": 2}
    assert it["big.py"][:2] == ("excluded", "code.too_large") and it["empty.py"][:2] == ("excluded", "code.empty_file")
    enc = su.execute("""SELECT uv.attrs->>'encoding' FROM knowledge.units u JOIN knowledge.unit_versions uv ON uv.unit_id = u.id
                        WHERE u.source_id = %s AND u.key = 'k.py'""", (s,)).fetchone()[0]
    assert enc == "cp949"


class FakeChat:
    identity = "fake@1"
    def __init__(self, bad=()): self.bad, self.calls = bad, 0
    def __call__(self, msgs):
        self.calls += 1
        if any(msgs[1]["content"].endswith(b) for b in self.bad): return "not json"
        return json.dumps({"summary": "Does a thing.", "summary_ko": "일을 한다.", "keywords": ["thing"], "side_effects": []})


def test_cp04_llm_invalid_json_excluded_and_coverage(su, base, call, world, proj, tmp_path):
    funcs = "".join(f"def f{i}():\n    return {i}\n\n" for i in range(30))
    z = make_zip(tmp_path, {"m.py": funcs})
    code._models["chat"] = FakeChat(bad=("return 7",))
    try:
        s, rev = upload(call, world, proj, z)
        g = run(base, call, world, proj, rev)
        assert g["outcome"] == "PUBLISHED", g                 # 1 of 30 symbols lacks knowledge_embed: coverage 0.967 >= 0.95
        it = items(su, g["job_id"])
        assert it["m.py::m.f7"][:2] == ("excluded", "code.llm_failed")
        assert code._models["chat"].calls == 29 + 2           # one retry for the invalid answer
        code._models["chat"] = FakeChat(bad=tuple(f"return {i}" for i in range(30)) + ("x = 1",))      # every chunk invalid -> coverage decides: job fails
        z2 = make_zip(tmp_path, {"m.py": funcs + "x = 1\n"})
        s2, rev2 = upload(call, world, proj, z2, "src2")
        g2 = run(base, call, world, proj, rev2)
        assert g2["status"] == "failed" and g2["outcome"] == "COVERAGE_FAILED", g2
    finally:
        code._models["chat"] = None


def test_purge_hook_removes_plugin_rows(su, base, call, world, proj, tmp_path):
    s, rev = upload(call, world, proj, make_zip(tmp_path, {"a.py": "import os\nos.getcwd()\n"}))
    g = run(base, call, world, proj, rev)
    with connect(base, "plugin_code") as c:
        assert c.execute("SELECT count(*) FROM plugin_code.raw_facts WHERE job_id = %s", (g["job_id"],)).fetchone()[0] == 2
        code.purge(c, proj, {"jobs": [g["job_id"]], "collections": []})
        assert c.execute("SELECT count(*) FROM plugin_code.raw_facts WHERE job_id = %s", (g["job_id"],)).fetchone()[0] == 0
        assert c.execute("SELECT count(*) FROM plugin_code.graph_quality WHERE job_id = %s", (g["job_id"],)).fetchone()[0] == 0
