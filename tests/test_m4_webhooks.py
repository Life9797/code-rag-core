"""M4 G1 outbound webhooks: allowlist (ST-28), signed delivery, retry/backoff, redirect = failure, dead + alert,
membership re-check at delivery, replay does not mint a second secret."""
import hashlib, hmac, json, uuid
import pytest
from fastapi.testclient import TestClient
from conftest import connect, rid, DB
from coderag_core import api, auth, runner, secrets


@pytest.fixture(scope="module")
def env(base, su, call, world, tmp_path_factory, monkeypatch_module=None):
    import os
    os.environ["CODERAG_SECRETS_KEY"] = secrets.ensure_key(str(tmp_path_factory.mktemp("sk") / "master.key"))
    if not su.execute("SELECT core.signing_key_active()").fetchone()[0]:
        auth.rotate_key(su, str(tmp_path_factory.mktemp("keys")), world["root"]); su.commit()
    c = TestClient(api.create_app(api.Settings(dsn_for=lambda login: f"{base} user={login} dbname={DB}"), manifests={}))
    from coderag_core.coreadm import issue_client
    r, secret = issue_client(su, world["root"], world["A"], "hooks", 4)
    tok = c.post("/core/v1/token", data={"client_id": r["client_id"], "client_secret": secret}).json()["access_token"]
    assert call.api("core.policy_set", world["root"], "global", None, "core.webhook_allowlist", '"https://hooks.example/ https://ci.example/x"', rid())["outcome"] == "OK"
    assert call.api("core.policy_set", world["root"], "global", None, "core.webhook_max_attempts", "3", rid())["outcome"] == "OK"
    p = call.api("core.project_create", world["A.admin"], world["A"], f"hooks-{uuid.uuid4().hex[:6]}", rid())["id"]
    yield {"c": c, "H": {"Authorization": f"Bearer {tok}"}, "p": p, "principal": r["principal_id"]}


def event(su, world, p, n=1):
    for _ in range(n):
        su.execute("INSERT INTO core.outbox_events (tenant_id, project_id, type, payload) VALUES (%s, %s, 'knowledge.published', '{\"gen\": 1}')",
                   (world["A"], p))
    su.commit()


def cn(base): return lambda login: connect(base, login)


def test_st28_allowlist_and_signed_delivery(base, su, world, env):
    c, H, p = env["c"], env["H"], env["p"]
    bad = c.post(f"/core/v1/projects/{p}/webhooks", json={"url": "https://evil.example/x"}, headers=H)
    assert bad.status_code == 400 and bad.json()["outcome"] == "DESTINATION_NOT_ALLOWED"
    assert c.post(f"/core/v1/projects/{p}/webhooks", json={"url": "http://hooks.example/a"}, headers=H).json()["outcome"] == "DESTINATION_NOT_ALLOWED"
    body = {"url": "https://hooks.example/a", "request_id": "sub-1"}
    r = c.post(f"/core/v1/projects/{p}/webhooks", json=body, headers=H).json()
    assert r["outcome"] == "CREATED" and r["secret"]
    again = c.post(f"/core/v1/projects/{p}/webhooks", json=body, headers=H).json()
    assert again["id"] == r["id"] and "secret" not in again                  # replay: no second secret
    event(su, world, p)
    sent = []
    def post(url, b, h): sent.append((url, b, h)); return 204
    out = runner.deliver_webhooks(cn(base), post)
    assert list(out.values()) == ["DELIVERED"] and len(sent) == 1
    url, b, h = sent[0]
    t, v1 = (x.split("=", 1)[1] for x in h["X-CodeRag-Signature"].split(","))
    assert hmac.compare_digest(v1, hmac.new(r["secret"].encode(), t.encode() + b"." + b, hashlib.sha256).hexdigest())
    assert json.loads(b)["data"] == {"gen": 1} and h["X-CodeRag-Event-Id"] == str(json.loads(b)["id"])
    assert runner.deliver_webhooks(cn(base), post) == {}                    # nothing new
    env["sub"] = r["id"]


def test_retry_redirect_dead_alert_and_membership(base, su, call, world, env):
    p = env["p"]
    event(su, world, p)
    for st in (500, 302, None):                                             # 3 failures (budget 3) -> dead
        su.execute("UPDATE core.outbox_deliveries SET next_at = now() WHERE state = 'pending'"); su.commit()
        out = runner.deliver_webhooks(cn(base), lambda u, b, h: st)
    assert list(out.values()) == ["DEAD"]
    assert su.execute("SELECT count(*) FROM core.alerts WHERE code = 'core.webhook_dead' AND object = %s AND cleared_at IS NULL",
                      (f"subscription:{env['sub']}",)).fetchone()[0] == 1
    # creator loses access -> pending deliveries end at the next due scan
    event(su, world, p)
    su.execute("DELETE FROM core.tenant_members WHERE principal_id = %s", (env["principal"],)); su.commit()
    assert runner.deliver_webhooks(cn(base), lambda u, b, h: 204) == {}
    assert su.execute("SELECT state FROM core.outbox_deliveries WHERE subscription_id = %s ORDER BY event_id DESC LIMIT 1",
                      (env["sub"],)).fetchone()[0] == "dead"


def test_allowlist_boundary_and_ssrf_guard(su, world, env):
    for url, ok in (("https://ci.example/x.evil.org/", False), ("https://ci.example/xy", False), ("https://ci.example/x/y", True),
                    ("https://ci.example/x", True), ("https://hooks.example.evil/", False)):
        assert su.execute("SELECT core.webhook_allowed(%s, %s, %s)", (url, world["A"], env["p"])).fetchone()[0] is ok, url
    assert runner._blocked("https://127.0.0.1/x") and runner._blocked("https://169.254.169.254/") and not runner._blocked("https://10.1.2.3/")
