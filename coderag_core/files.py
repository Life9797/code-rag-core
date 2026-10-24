"""Safe archive extraction into a volume snapshot -> file_tree@1 manifest (§17 core.files.safe_extract).
Ported from code-rag v0.2.4 worker/coderag/ingest.py (extract, _clean, _source_root); classification moved to plugins."""
import hashlib, os, shutil, stat, unicodedata, uuid, zipfile
from . import storage

LIMITS = {"max_files": 20000, "max_total_bytes": 2 << 30, "max_depth": 32, "max_ratio": 200}


class InputError(ValueError):
    def __init__(self, code, detail=""):
        super().__init__(f"{code}: {detail}"); self.code = code


def _clean(name):
    p = unicodedata.normalize("NFC", name.replace("\\", "/"))
    while p.startswith("./"): p = p[2:]
    p = p.rstrip("/")
    if (not p or p.startswith("/") or (len(p) > 1 and p[1] == ":")
            or any(c in ("", ".", "..") for c in p.split("/")) or any(ord(ch) < 32 for ch in p)):
        raise InputError("zip_path_traversal", name)
    return p


def _source_root(names):
    tops = {n.split("/", 1)[0] for n in names}
    return next(iter(tops)) + "/" if len(tops) == 1 and all("/" in n for n in names) else ""


def safe_extract(zip_path, dest_uri, root=None, limits=None, strip_root=True):
    """Extract into vol:// dest_uri; returns {format, files:[{path, sha256, size}]} sorted by path bytes. Writes a temp
    folder then renames it into place; an existing dest (same content key) is kept, so readers never see it change."""
    lim = {**LIMITS, **(limits or {})}
    dest = storage.resolve(dest_uri, root)
    tmp_uri = dest_uri + ".tmp-" + uuid.uuid4().hex
    try:
        man = _extract(zip_path, tmp_uri, root, lim, strip_root)
        if os.path.isdir(dest): storage.remove_tree(tmp_uri, root)
        else: os.replace(storage.resolve(tmp_uri, root), dest)
        return man
    except BaseException:
        storage.remove_tree(tmp_uri, root); raise


def _extract(zip_path, dest_uri, root, lim, strip_root):
    os.makedirs(storage.resolve(dest_uri, root))
    try: zf = zipfile.ZipFile(zip_path)
    except (zipfile.BadZipFile, OSError) as e: raise InputError("invalid_zip", str(e))
    with zf:
        members = [m for m in zf.infolist() if not m.is_dir()]
        if any(stat.S_ISLNK(m.external_attr >> 16) for m in members): raise InputError("symlink_rejected")
        names = [_clean(m.filename) for m in members]
        if len({n.casefold() for n in names}) != len(names): raise InputError("invalid_zip", "duplicate member path (case-insensitive)")
        if len(members) > lim["max_files"]: raise InputError("file_count_limit", str(len(members)))
        if sum(m.file_size for m in members) > lim["max_total_bytes"]: raise InputError("total_size_limit")
        if not names: raise InputError("source_root_invalid", "no files")
        root_dir, files = _source_root(names) if strip_root else "", []
        for m, name in zip(members, names):
            path = name[len(root_dir):]
            parts = path.split("/")
            if len(parts) > lim["max_depth"]: raise InputError("depth_limit", path)
            if m.compress_size and m.file_size / m.compress_size > lim["max_ratio"] and m.file_size > 1 << 20:
                raise InputError("zip_bomb_limit", path)
            target = storage.resolve(dest_uri + "/" + path, root)
            os.makedirs(os.path.dirname(target), exist_ok=True)
            h, n = hashlib.sha256(), 0
            with zf.open(m) as src, open(target, "wb") as f:         # stream: never more than the declared size (review 5)
                while chunk := src.read(1 << 20):
                    n += len(chunk)
                    if n > m.file_size: raise InputError("zip_bomb_limit", path)
                    h.update(chunk); f.write(chunk)
            if n != m.file_size: raise InputError("zip_bomb_limit", path)
            files.append({"path": path, "sha256": h.hexdigest(), "size": n})
    return {"format": "file_tree@1", "files": sorted(files, key=lambda f: f["path"].encode())}


def read(dest_uri, path, root=None):
    with open(storage.resolve(dest_uri + "/" + path, root), "rb") as f: return f.read()
