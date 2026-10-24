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
