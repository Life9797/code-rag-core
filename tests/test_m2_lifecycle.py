"""M2 lifecycle: project delete/restore (U8), purge with plugin hooks (G2, zero residue), source revisions and input
formats (P-P13, P-P15), snapshot retention (G4), vol:// paths (ST-15), Core loops end to end."""
import json, os, uuid
import pytest
from conftest import connect, rid, DB
from coderag_core import runner, storage, manifest as mf
from coderag_core.worker import Worker, SqlBinding
from plugins.echo import main as echo_main

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def connect_as(base):
    return lambda login: connect(base, login)


def new_project(call, w, plugins=("echo", "tst")):
    p = call.api("core.project_create", w["A.admin"], w["A"], f"lc-{uuid.uuid4().hex[:6]}", rid())["id"]
    for plug in plugins:
        call.api("core.plugin_enable", w["A.admin"], p, plug, True, rid())
    return p


def test_e2e_loops_and_purge_zero_residue(su, base, call, world, connect_as):
    w = world
    p = new_project(call, w)
    j = call.api("runtime.create_job", w["A.admin"], p, "echo", "run", None, False, rid())
    runner.runtime_tick(connect_as)
    with connect(base, "plugin_echo") as c:
        Worker("echo", "0.1.0", echo_main.HANDLERS, SqlBinding(c), db=c).drain()
    out = runner.runtime_tick(connect_as)
    assert out["published"].get(j["job_id"], {}).get("outcome") == "PUBLISHED", (j, out)
    # U8
    assert call.api("core.delete_project", w["A.op"], p, rid())["outcome"] == "FORBIDDEN"
    assert call.api("core.delete_project", w["A.admin"], p, rid())["outcome"] == "OK"
    assert call.api("runtime.create_job", w["A.admin"], p, "echo", "run", None, False, rid())["outcome"] == "PROJECT_DELETING"
    assert call.api("core.restore_project", w["A.admin"], p, rid())["outcome"] == "OK"
    assert call.api("core.delete_project", w["A.admin"], p, rid())["outcome"] == "OK"
    with connect(base, "purge") as c:
        assert c.execute("SELECT core.purge_step(%s)", (p,)).fetchone()[0]["outcome"] == "NOT_DUE"
    su.execute("UPDATE core.projects SET delete_after = now() - interval '1 second' WHERE id = %s", (p,)); su.commit()
    echo_m = mf.load(os.path.join(ROOT, "plugins/echo"))
    assert runner.purge(connect_as, {})[str(p)] == "RUN_HOOKS"                       # waits for the echo hook
    assert runner.purge(connect_as, {"echo": echo_m, "tst": {**echo_m, "hooks": {"purge_project": "purge"}}})[str(p)] == "DONE"
    residue = su.execute("""SELECT (SELECT count(*) FROM core.projects WHERE id = %(p)s) + (SELECT count(*) FROM runtime.jobs WHERE project_id = %(p)s)
        + (SELECT count(*) FROM knowledge.units WHERE project_id = %(p)s) + (SELECT count(*) FROM knowledge.collections WHERE project_id = %(p)s)
        + (SELECT count(*) FROM core.project_members WHERE project_id = %(p)s) + (SELECT count(*) FROM runtime.tasks WHERE project_id = %(p)s)""",
        {"p": p}).fetchone()[0]
    assert residue == 0
    assert su.execute("SELECT count(*) FROM core.audit WHERE action = 'purge_hook_done' AND object = %s", (f"project:{p}",)).fetchone()[0] == 1


def test_revisions_input_formats_and_retention(su, call, world, tmp_path):
    w = world
    p = new_project(call, w, ("tst",))
    s = call.api("core.source_create", w["A.admin"], p, "core.upload", "up-1", False, rid())["id"]
    assert call.api("runtime.create_job", w["A.admin"], p, "tst", "ft", None, False, rid())["outcome"] == "INPUT_FORMAT_INCOMPATIBLE"
    revs = []
    for i in range(3):
        r = call.api("core.revision_create", w["A.admin"], s, f"sha-{i}", json.dumps({"files": []}), f"m{i}", f"vol://v1/up/{i}", True, rid())
        assert r["outcome"] == "CREATED"; revs.append(r["id"])
    assert call.api("core.revision_create", w["A.admin"], s, "sha-0", "{}", "x", "vol://v1/x", True, rid())["outcome"] == "EXISTS"
    assert call.api("core.revision_create", w["A.reader"], s, "sha-9", "{}", "x", "vol://v1/x", True, rid())["outcome"] == "FORBIDDEN"
    assert call.api("runtime.create_job", w["A.admin"], p, "tst", "ft", revs[2], False, rid())["outcome"] == "CREATED"
    su.execute("UPDATE core.source_revisions SET created_at = created_at - interval '60 days' WHERE id = ANY (%s)", (revs[:2],))
    su.execute("UPDATE core.source_revisions SET created_at = now() - interval '59 days' WHERE id = %s", (revs[1],)); su.commit()
    pruned = call("maint", "core.snapshot_retention", 100)["pruned"]
    assert "vol://v1/up/0" in pruned and "vol://v1/up/1" in pruned and "vol://v1/up/2" not in pruned   # latest kept
    assert call.api("runtime.create_job", w["A.admin"], p, "tst", "ft", revs[0], False, rid())["outcome"] == "SNAPSHOT_EXPIRED"


@pytest.mark.parametrize("uri", ["vol://v1/../v2/x", "vol://v1//etc/passwd", "vol://v1/a/../../x", "vol://V1/x",
                                 "file:///etc/passwd", "vol://v1/a\\..\\b", "vol://v1/./x", "vol://v1/link/x"])
def test_st15_vol_traversal_refused(tmp_path, uri):
    (tmp_path / "v1").mkdir(); (tmp_path / "outside").mkdir()
    os.symlink(tmp_path / "outside", tmp_path / "v1" / "link")
    with pytest.raises(storage.StorageError):
        storage.resolve(uri, str(tmp_path))


def test_storage_write_inside_volume(tmp_path):
    (tmp_path / "v1").mkdir()
    path = storage.write_bytes("vol://v1/a/b.txt", b"hi", str(tmp_path))
    assert open(path, "rb").read() == b"hi" and path.startswith(str(tmp_path / "v1"))
