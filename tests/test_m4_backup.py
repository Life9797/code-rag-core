"""§38: coreadm verify finds missing/changed files and changed plugin folders; backup mode pauses file deletes (BR-02).
The full drill BR-01 (deploy/backup.sh -> fresh host -> deploy/restore.sh) is run against Docker, not here."""
import os, shutil, uuid
from conftest import connect, rid
from coderag_core import backup, runner, storage
from test_m3 import upload, make_zip, vol, proj  # noqa: F401 (fixtures)


def test_verify_and_backup_mode(base, su, call, world, proj, tmp_path):
    s, rev = upload(call, world, proj, make_zip(tmp_path, {"a.py": "x = 1\n", "b.py": "y = 2\n"}))
    plugins = os.path.join(os.path.dirname(__file__), "plugins")
    ref = su.execute("SELECT storage_ref FROM core.source_revisions WHERE id = %s", (rev,)).fetchone()[0]
    r = backup.verify(su, plugins, sample=1.0)            # other modules' volumes are gone: look at this revision only
    assert not r["plugin_mismatch"] and not [x for x in r["missing"] + r["bad_hash"] if x.startswith(ref)], r
    with open(storage.resolve(ref + "/a.py"), "w") as f: f.write("changed")
    os.remove(storage.resolve(ref + "/b.py"))
    r = backup.verify(su, plugins, sample=1.0)
    assert f"{ref}/a.py" in r["bad_hash"] and f"{ref}/b.py" in r["missing"]
    cp = tmp_path / "plugins"; shutil.copytree(plugins, cp, symlinks=False)
    (cp / "echo" / "extra.txt").write_text("x")
    assert "echo@0.1.0" in backup.verify(su, str(cp), sample=0)["plugin_mismatch"]
    su.execute("SELECT core.backup_mode_set(true)"); su.commit()
    try:
        assert runner.maint_tick(lambda login: connect(base, login))["backup_mode"] is True
    finally:
        su.execute("SELECT core.backup_mode_set(false)"); su.commit()
