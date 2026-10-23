"""Service tokens (P-P4, MG-05..07): argon2id secrets, ES256 JWT with kid, JWKS, per-request client check.
The DB never sees a secret; the api never reads secret_hash (two definer calls, MG-06)."""
import base64, json, os, secrets, time, uuid
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
