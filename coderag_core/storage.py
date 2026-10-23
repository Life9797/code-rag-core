"""Storage URIs (P-P10, P-P11, I-F1): vol://<volume>/<path> under CODERAG_VOL_ROOT (/data/vol); paths cannot escape
their volume (no '..', no absolute paths, no symlink exits). A URI never grants access by itself."""
import os, re, tempfile

ROOT = os.environ.get("CODERAG_VOL_ROOT", "/data/vol")


class StorageError(ValueError):
    pass


def resolve(uri, root=None):
    m = re.fullmatch(r"vol://([a-z0-9_-]{1,32})/(.+)", uri or "")
    if not m: raise StorageError("not a vol:// uri")
    name, rel = m.groups()
    parts = rel.split("/")
    if "\\" in rel or "\0" in rel or rel.startswith("/") or any(p in ("", ".", "..") for p in parts):
        raise StorageError("bad path")
    base = os.path.realpath(os.path.join(root or ROOT, name))
    real = os.path.realpath(os.path.join(base, *parts))
    if not real.startswith(base + os.sep): raise StorageError("path escapes its volume")
    return real


def write_bytes(uri, data, root=None):
    """Write then fsync then rename; the DB row is written only after this returns (§26 recovery)."""
    path = resolve(uri, root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path))
    with os.fdopen(fd, "wb") as f:
        f.write(data); f.flush(); os.fsync(f.fileno())
    os.replace(tmp, resolve(uri, root))
    return path


def remove(uri, root=None):
    try: os.remove(resolve(uri, root))
    except FileNotFoundError: pass
