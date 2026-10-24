"""Review 2026-10-24: worker claims one task at a time; a lost lease stops the handler and nothing is reported."""
import time
from coderag_core import worker as W


class Fake:
    def __init__(self, beat): self.beat, self.calls = beat, []
    def claim(self, max, versions, rid):
        self.calls.append(("claim", max)); return {"tasks": [{"task_id": 1, "lease_token": 1, "lease_epoch": 1, "handler": "p.h", "policy": {}}]}
    def heartbeat(self, t, checkpoint=None, extend=False): return {"outcome": self.beat}
    def complete(self, t, result, rid): self.calls.append(("complete",)); return {"outcome": "OK"}
    def fail(self, t, code, detail, rid): self.calls.append(("fail", code)); return {"outcome": "OK"}
    def stage(self, op, t, **kw): self.calls.append(("stage", op)); return {}


def run(beat, monkeypatch):
    monkeypatch.setattr(W.Worker, "HEARTBEAT_S", 0.01)
    def h(ctx, t):
        for _ in range(100):
            time.sleep(0.01); ctx.put_job_item("k", "ok")
    b = Fake(beat)
    w = W.Worker("p", "1", {"h": h}, b)
    return w.run_once(), b.calls


def test_claims_one_and_stops_on_lost_lease(monkeypatch):
    n, calls = run("STALE_LEASE", monkeypatch)
    assert n == 1 and calls[0] == ("claim", 1)
    assert not any(c[0] in ("complete", "fail") for c in calls) and len(calls) < 50


def test_live_lease_completes(monkeypatch):
    _, calls = run("EXTENDED", monkeypatch)
    assert calls[-1] == ("complete",)


def test_oidc_fails_closed():
    import pytest
    from coderag_core import auth
    with pytest.raises(ValueError): auth.Oidc("https://kc/realms/x")


def test_db_logins_per_role(base, tmp_path):
    import json, os, stat
    from conftest import DB
    from coderag_core.__main__ import db_logins
    db_logins(f"{base} user=postgres dbname={DB}", str(tmp_path))
    pw = json.load(open(tmp_path / "roles.json"))
    assert {"api", "runtime", "maint", "purge"} <= pw.keys() and len(set(pw.values())) == len(pw)
    api_f, wk_f = (tmp_path / "api/pgpass").read_text(), (tmp_path / "worker/pgpass").read_text()
    assert ":maint:" not in api_f and ":api:" not in wk_f and ":plugin_echo:" in api_f
    assert stat.S_IMODE(os.stat(tmp_path / "api/pgpass").st_mode) == 0o600
    db_logins(f"{base} user=postgres dbname={DB}", str(tmp_path))
    assert json.load(open(tmp_path / "roles.json")) == pw                 # re-init keeps passwords
    os.environ.pop("PGPASSFILE", None)


def test_orphan_sweep(tmp_path):
    import os
    from coderag_core import runner
    for n in ("live", "orphan", "fresh"): os.makedirs(tmp_path / "sources" / "s1" / n)
    os.utime(tmp_path / "sources/s1/live", (0, 0)); os.utime(tmp_path / "sources/s1/orphan", (0, 0))
    assert runner.sweep_orphans({"vol://sources/s1/live"}, str(tmp_path)) == 1
    assert sorted(os.listdir(tmp_path / "sources/s1")) == ["fresh", "live"]


def test_zip_case_duplicates_and_size_lie(tmp_path):
    import os, pytest, zipfile
    from coderag_core import files
    z = tmp_path / "a.zip"
    with zipfile.ZipFile(z, "w") as f: f.writestr("A.py", "x"); f.writestr("a.py", "y")
    with pytest.raises(files.InputError): files.safe_extract(str(z), "vol://sources/t/1", str(tmp_path), strip_root=False)
    with zipfile.ZipFile(z, "w") as f: f.writestr("b.py", "x" * 100)
    man = files.safe_extract(str(z), "vol://sources/t/2", str(tmp_path), strip_root=False)
    assert man["files"][0]["size"] == 100 and sorted(os.listdir(tmp_path / "sources/t")) == ["2"]
