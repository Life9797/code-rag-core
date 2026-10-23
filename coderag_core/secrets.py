"""Connector and webhook secrets (P-P12): AES-256-GCM with a host key file kept apart from backups of the DB (§38).
key_id = first 16 hex of sha256(key); the DB stores only key_id + ciphertext."""
import base64, hashlib, os
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def key_path(): return os.environ.get("CODERAG_SECRETS_KEY", os.path.join(os.environ.get("CODERAG_KEYS_DIR", "/data/keys"), "master.key"))


def ensure_key(path=None):
    path = path or key_path()
    if not os.path.exists(path):
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f: f.write(os.urandom(32))
    return path


def _key(path=None):
    with open(path or key_path(), "rb") as f: k = f.read()
    return k, hashlib.sha256(k).hexdigest()[:16]


def encrypt(plain: str, path=None):
    k, kid = _key(path)
    n = os.urandom(12)
    return kid, n + AESGCM(k).encrypt(n, plain.encode(), kid.encode())


def decrypt(kid, blob: bytes, path=None):
    k, have = _key(path)
    if kid != have: raise KeyError(f"secret key {kid} not loaded")
    return AESGCM(k).decrypt(blob[:12], blob[12:], kid.encode()).decode()
