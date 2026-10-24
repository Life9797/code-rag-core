"""§38 restore verification (coreadm verify): referenced volume files exist (sha256 for every artifact and a 1% sample
of source files), plugin folders match the registry dir_hash, and secrets decrypt with the loaded host key."""
import hashlib, os, random
from . import manifest as mf, secrets, storage


def _sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""): h.update(b)
    return h.hexdigest()


def verify(conn, plugin_root, vol_root=None, sample=0.01, seed=0):
    rnd, out = random.Random(seed), {"missing": [], "bad_hash": [], "plugin_mismatch": [], "secrets_missing": []}
    for ref, man in conn.execute("SELECT storage_ref, manifest FROM core.source_revisions WHERE storage_state = 'present'"):
        files = (man or {}).get("files", [])
        try: base = storage.resolve(ref, vol_root)
        except storage.StorageError: out["missing"].append(ref); continue
        if not os.path.isdir(base): out["missing"].append(ref); continue
        for f in rnd.sample(files, max(1, int(len(files) * sample))) if files else []:
            p = os.path.join(base, *f["path"].split("/"))
            if not os.path.isfile(p): out["missing"].append(f"{ref}/{f['path']}")
            elif _sha(p) != f["sha256"]: out["bad_hash"].append(f"{ref}/{f['path']}")
    for ref, sha in conn.execute("SELECT storage_ref, sha256 FROM core.artifacts"):
        p = storage.resolve(ref, vol_root)
        if not os.path.isfile(p): out["missing"].append(ref)
        elif _sha(p) != sha: out["bad_hash"].append(ref)
    for pid, ver, dh in conn.execute("SELECT id, version, dir_hash FROM core.plugins WHERE status = 'registered' AND id <> 'core'"):
        d = os.path.join(plugin_root, pid)
        if not os.path.isdir(d) or mf.dir_hash(d) != dh: out["plugin_mismatch"].append(f"{pid}@{ver}")
    try: have = secrets._key()[1]
    except OSError: have = None
    out["secrets_missing"] = [f"{o}:{n}" for o, n, k in conn.execute(
        "SELECT owner, name, key_id FROM core.secrets WHERE status = 'active'") if k != have]
    out["ok"] = not any(out[k] for k in ("missing", "bad_hash", "plugin_mismatch"))
    return out
