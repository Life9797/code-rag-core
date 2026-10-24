"""Service tokens (P-P4, MG-05..07): argon2id secrets, ES256 JWT with kid, JWKS, per-request client check.
The DB never sees a secret; the api never reads secret_hash (two definer calls, MG-06)."""
import base64, contextlib, json, os, secrets, threading, time, uuid
import jwt
from argon2 import PasswordHasher, low_level
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

AUD = "coderag-core"
_ph = PasswordHasher()   # argon2id defaults: m=65536, t=3, p=4


def new_secret():
    s = secrets.token_urlsafe(32)
    return s, _ph.hash(s)


def _digest(salt_prefix, secret):
    # salt_prefix = "$argon2id$v=19$m=..,t=..,p=..$<b64 salt>"
    _, _, _, params, salt = salt_prefix.split("$")
    p = dict(kv.split("=") for kv in params.split(","))
    raw = low_level.hash_secret_raw(secret.encode(), base64.b64decode(salt + "=" * (-len(salt) % 4)),
                                    time_cost=int(p["t"]), memory_cost=int(p["m"]), parallelism=int(p["p"]),
                                    hash_len=32, type=low_level.Type.ID)
    return base64.b64encode(raw).decode().rstrip("=")


def issue_token(conn, client_id, secret, ttl=3600):
    """POST /core/v1/token (client credentials). Returns a JWT or None (DENIED); the same work runs for unknown ids."""
    try: client_id = str(uuid.UUID(str(client_id)))
    except ValueError: client_id = str(uuid.UUID(int=0))          # malformed id: same work, then 401 (not a 500)
    with conn.cursor() as cur:
        cur.execute("SELECT core.get_client_salt(%s)", (client_id,))
        digest = _digest(cur.fetchone()[0], secret)
        cur.execute("SELECT core.verify_and_issue(%s, %s)", (client_id, digest))
        r = cur.fetchone()[0]
        if r["outcome"] != "OK": return None
        cur.execute("SELECT core.signing_key_active()")
        key = cur.fetchone()[0]
    now = int(time.time())
    claims = {"sub": r["sub"], "client": r["client"], "scope": " ".join(r["scopes"]), "aud": r["aud"],
              "iat": now, "exp": now + ttl, "jti": str(uuid.uuid4())}
    with open(key["private_ref"], "rb") as f:
        pk = serialization.load_pem_private_key(f.read(), None)
    return jwt.encode(claims, pk, algorithm="ES256", headers={"kid": key["kid"]})


def verify_token(conn, token):
    """Returns claims or None (-> 401): bad signature, unknown kid, wrong aud, expired, revoked client."""
    try:
        kid = jwt.get_unverified_header(token).get("kid")
        with conn.cursor() as cur:
            cur.execute("SELECT core.jwks()")
            jwk = next((k for k in cur.fetchone()[0] if k["kid"] == kid), None)
            if jwk is None: return None
            claims = jwt.decode(token, jwt.PyJWK(jwk).key, algorithms=["ES256"], audience=AUD)
            cur.execute("SELECT core.client_active(%s)", (claims["client"],))
            return claims if cur.fetchone()[0] else None
    except jwt.PyJWTError:
        return None


def rotate_key(conn, keys_dir, principal_id=None):
    """MG-07: new P-256 key in the host keys folder; DB keeps only the public JWK and a path reference."""
    kid = "k" + time.strftime("%Y%m%d%H%M%S") + secrets.token_hex(2)
    pk = ec.generate_private_key(ec.SECP256R1())
    path = os.path.join(keys_dir, kid + ".pem")
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(pk.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    pub = json.loads(jwt.algorithms.ECAlgorithm.to_jwk(pk.public_key()))
    with conn.cursor() as cur:
        cur.execute("SELECT core.signing_key_rotate(%s, %s, %s::jsonb, %s)", (principal_id, kid, json.dumps(pub), path))
        return cur.fetchone()[0]


class Limiter:
    """Token endpoint guard: at most `fails` failures per client id and per source address in `window` s (then 429),
    and at most `slots` argon2 checks at once (64 MB each). In-process; one api process per host (§28.2)."""
    def __init__(self, fails=10, window=60, slots=4):
        self.fails, self.window, self.lock, self.hits = fails, window, threading.Lock(), {}
        self.sem = threading.BoundedSemaphore(slots)
    def _recent(self, k, now):
        v = self.hits[k] = [t for t in self.hits.get(k, []) if now - t < self.window]
        if not v: del self.hits[k]
        return v
    def allow(self, *keys):
        now = time.time()
        with self.lock: return all(len(self._recent(k, now)) < self.fails for k in keys if k)
    def fail(self, *keys):
        with self.lock:
            for k in keys:
                if k: self.hits.setdefault(k, []).append(time.time())
    @contextlib.contextmanager
    def slot(self):
        with self.sem: yield


def _http_json(url):
    import urllib.request
    with urllib.request.urlopen(url, timeout=10) as r: return json.loads(r.read())


class Oidc:
    """Human sign-in through Keycloak (or any OIDC issuer), off unless CODERAG_OIDC_ISSUER is set (P-P5 "human OIDC later").
    The API accepts the issuer's access tokens (signature via its JWKS, iss, exp, and aud or azp) and maps (iss, sub) to a
    principal through core.external_identities; an unlinked identity gets 401 until a tenant admin links it."""
    def __init__(self, issuer, client_id=None, audience=None, jwks_url=None, fetch=None):
        if not (client_id or audience): raise ValueError("OIDC needs CODERAG_OIDC_AUDIENCE or CODERAG_OIDC_CLIENT_ID")   # fail closed
        self.issuer, self.client_id, self.audience = issuer.rstrip("/"), client_id, audience
        self.jwks_url, self.fetch, self._keys, self._at = jwks_url, fetch or _http_json, {}, 0.0

    @classmethod
    def from_env(cls):
        e = lambda k: os.environ.get(k) or None
        return cls(e("CODERAG_OIDC_ISSUER"), e("CODERAG_OIDC_CLIENT_ID"), e("CODERAG_OIDC_AUDIENCE"), e("CODERAG_OIDC_JWKS_URL")) \
            if e("CODERAG_OIDC_ISSUER") else None

    def _key(self, kid):
        if kid not in self._keys and time.time() - self._at > 30:     # refetch on an unknown kid (key rotation), at most every 30 s
            url = self.jwks_url or self.fetch(self.issuer + "/.well-known/openid-configuration")["jwks_uri"]
            self._keys, self._at = {k["kid"]: k for k in self.fetch(url)["keys"] if k.get("use", "sig") == "sig"}, time.time()
        return self._keys.get(kid)

    def verify(self, token):
        """{issuer, subject, username} for a valid token of this issuer, else None."""
        try:
            if (jwt.decode(token, options={"verify_signature": False}).get("iss") or "").rstrip("/") != self.issuer: return None
            jwk = self._key(jwt.get_unverified_header(token).get("kid"))
            if jwk is None: return None
            cl = jwt.decode(token, jwt.PyJWK(jwk).key, algorithms=["RS256", "PS256", "ES256"], audience=self.audience,
                            options={"verify_aud": bool(self.audience), "require": ["exp", "sub"]})
            if (cl.get("iss") or "").rstrip("/") != self.issuer: return None
            if not self.audience and self.client_id and cl.get("azp") != self.client_id: return None
            return {"issuer": self.issuer, "subject": cl["sub"], "username": cl.get("preferred_username") or cl.get("email") or ""}
        except (jwt.PyJWTError, KeyError, ValueError, OSError):
            return None
