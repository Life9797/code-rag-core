"""Admin page (§43) and Keycloak/OIDC sign-in prep: page served, admin reads, client/person management, OIDC token mapping."""
import time, uuid
import jwt, pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from conftest import rid, DB
from coderag_core import api, auth, storage
from coderag_core.coreadm import issue_client

ISS = "http://kc.local/realms/coderag"


@pytest.fixture(scope="module")
def kc():
    k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = {**jwt.algorithms.RSAAlgorithm.to_jwk(k.public_key(), as_dict=True), "kid": "k1", "use": "sig", "alg": "RS256"}
    docs = {ISS + "/.well-known/openid-configuration": {"jwks_uri": ISS + "/certs"}, ISS + "/certs": {"keys": [jwk]}}
    def token(sub, azp="coderag-admin", exp=600, iss=ISS):
        return jwt.encode({"iss": iss, "sub": sub, "azp": azp, "exp": int(time.time()) + exp, "preferred_username": "dy"},
                          k, algorithm="RS256", headers={"kid": "k1"})
    return {"oidc": auth.Oidc(ISS + "/", "coderag-admin", fetch=lambda u: docs[u]), "token": token}


@pytest.fixture(scope="module")
def h(base, su, world, kc, tmp_path_factory):
    if not su.execute("SELECT core.signing_key_active()").fetchone()[0]:
        auth.rotate_key(su, str(tmp_path_factory.mktemp("keys")), world["root"]); su.commit()
    c = TestClient(api.create_app(api.Settings(dsn_for=lambda login: f"{base} user={login} dbname={DB}"), manifests={}, oidc=kc["oidc"]))
    def tok(role):
        r, secret = issue_client(su, world["root"], world["A"], f"adm-{role}-{uuid.uuid4().hex[:4]}", role)
        return {"Authorization": "Bearer " + c.post("/core/v1/token", data={"client_id": r["client_id"], "client_secret": secret}).json()["access_token"]}
    return {"c": c, "admin": tok(4), "reader": tok(1)}


def test_page_and_auth_config(h):
    r = h["c"].get("/admin")
    assert r.status_code == 200 and "code-rag 관리" in r.text and "<script" in r.text
    assert h["c"].get("/core/v1/auth/config").json() == {"oidc": {"issuer": ISS, "client_id": "coderag-admin"}}


def test_admin_reads_and_management(h, world):
    c, A, R = h["c"], h["admin"], h["reader"]
    me = c.get("/core/v1/whoami", headers=A).json()
    assert me["outcome"] == "OK" and not me["platform_admin"] and any(t["id"] == str(world["A"]) and t["role"] == 4 for t in me["tenants"])
    assert c.get(f"/core/v1/tenants/{world['A']}", headers=R).status_code == 403
    assert c.get(f"/core/v1/tenants/{world['B']}", headers=A).status_code == 403                     # other tenant
    d = c.get(f"/core/v1/tenants/{world['A']}", headers=A).json()
    assert any(m["principal_id"] == str(world["A.op"]) and m["role"] == 3 for m in d["members"])
    # service client from the API: secret shown once and usable; revoke ends it
    s = c.post(f"/core/v1/tenants/{world['A']}/clients", json={"name": "owui2", "role": 2}, headers=A).json()
    assert s["outcome"] == "CREATED" and s["secret"]
    assert c.post("/core/v1/token", data={"client_id": s["client_id"], "client_secret": s["secret"]}).status_code == 200
    assert c.request("DELETE", f"/core/v1/clients/{s['client_id']}", json={}, headers=A).json()["outcome"] == "OK"
    assert c.post("/core/v1/token", data={"client_id": s["client_id"], "client_secret": s["secret"]}).status_code == 401
    # project detail: members, plugins on/off, policy keys, webhooks (operator+ only)
    pd = c.get(f"/core/v1/projects/{world['A.p']}", headers=A).json()
    assert pd["project"]["role"] == 4 and {"members", "plugins", "policies"} <= pd.keys() and pd["webhooks"] == []
    assert any(k["key"] == "runtime.approval_ttl" for k in pd["policies"])
    pr = c.get(f"/core/v1/projects/{world['A.p']}", headers=R).json()
    assert pr["outcome"] == "FORBIDDEN" or pr["webhooks"] is None
    assert c.post("/core/v1/tenants", json={"name": "x"}, headers=A).status_code == 403                  # platform admin only
    m = c.post(f"/core/v1/tenants/{world['A']}/members", json={"principal": str(world["A.reader"]), "role": 2}, headers=A).json()
    assert m["outcome"] == "OK"


def test_keycloak_token_link_flow(h, world, kc):
    c, A = h["c"], h["admin"]
    sub = str(uuid.uuid4())
    K = {"Authorization": "Bearer " + kc["token"](sub)}
    w = c.get("/core/v1/whoami", headers=K).json()
    assert w == {"outcome": "NOT_LINKED", "issuer": ISS, "subject": sub, "username": "dy"}
    r = c.get("/core/v1/projects", headers=K)
    assert r.status_code == 401 and r.json()["detail"] == "identity_not_linked"
    p = c.post(f"/core/v1/tenants/{world['A']}/principals", json={"name": "Dy", "role": 1, "issuer": ISS + "/", "subject": sub}, headers=A).json()
    assert p["outcome"] == "CREATED"
    assert c.post(f"/core/v1/projects/{world['A.p']}/members", json={"tenant": str(world["A"]), "principal": p["id"], "role": 1}, headers=A).json()["outcome"] == "OK"
    assert any(x["id"] == str(world["A.p"]) for x in c.get("/core/v1/projects", headers=K).json()["projects"])
    assert c.get("/core/v1/whoami", headers=K).json()["principal"]["name"] == "Dy"
    for bad in (kc["token"](sub, azp="other"), kc["token"](sub, exp=-10), kc["token"](sub, iss="http://evil/realms/x")):
        assert c.get("/core/v1/projects", headers={"Authorization": "Bearer " + bad}).status_code == 401
