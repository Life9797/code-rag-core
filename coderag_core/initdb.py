"""init (§28.2): extensions, roles, Core migrations as core_owner, plugin schemas/migrations as plugin_<id>_owner,
plugin registration, grants, schema_version stamp. Runs as the DB superuser; nothing else uses that login (I-S1).

python -m coderag_core.initdb --dsn "host=... user=postgres dbname=coderag" [--plugins plugins]
"""
import argparse, glob, hashlib, json, os, re, sys
import psycopg
from psycopg import sql
from . import CORE_VERSION, manifest as mf

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOGINS = ("api", "runtime", "purge", "maint")


def _sha(path):
    with open(path, "rb") as f: return hashlib.sha256(f.read()).hexdigest()


def _role(cur, name, login):
    cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (name,))
    if not cur.fetchone():
        cur.execute(sql.SQL("CREATE ROLE {} " + ("LOGIN" if login else "NOLOGIN")).format(sql.Identifier(name)))


def _applied(cur, plugin_id):
    cur.execute("SELECT to_regclass('core.plugin_migrations') IS NOT NULL")
    if not cur.fetchone()[0]: return {}
    cur.execute("SELECT number, sha256 FROM core.plugin_migrations WHERE plugin_id = %s", (plugin_id,))
    return dict(cur.fetchall())


def _migrate(conn, plugin_id, owner, search_path, files, log):
    with conn.cursor() as cur: done = _applied(cur, plugin_id)
    for n, path in files:
        sha = _sha(path)
        if n in done:
            if done[n] != sha: raise SystemExit(f"{plugin_id} migration {n} changed after apply (immutable, §28.3)")
            continue
        with conn.transaction(), conn.cursor() as cur:
            cur.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(owner)))
            cur.execute(sql.SQL("SET LOCAL search_path = {}").format(sql.SQL(search_path)))
            cur.execute(open(path).read())
            cur.execute("RESET ROLE")
            cur.execute("INSERT INTO core.plugin_migrations (plugin_id, number, sha256) VALUES (%s, %s, %s)", (plugin_id, n, sha))
        log(f"applied {plugin_id} {os.path.basename(path)}")


def _requires_ok(rng, version):
    v = tuple(int(x) for x in version.split(".")[:3])
    for op, ver in re.findall(r"(>=|<=|>|<|=)\s*([0-9.]+)", rng):
        w = tuple(int(x) for x in ver.split(".")) + (0,) * (3 - len(ver.split(".")))
        if not {">=": v >= w, "<=": v <= w, ">": v > w, "<": v < w, "=": v == w}[op]: return False
    return True


def init(dsn, plugin_root=None, log=print):
    conn = psycopg.connect(dsn, autocommit=True)
    cur = conn.cursor()
    dbname = conn.info.dbname
    cur.execute("CREATE SCHEMA IF NOT EXISTS ext")
    cur.execute("CREATE EXTENSION IF NOT EXISTS vector SCHEMA ext")
    cur.execute("SELECT to_regproc('pg_catalog.uuidv7') IS NULL")
    if cur.fetchone()[0]:                                     # PG16 test server: stand-in for PG18 uuidv7()
        cur.execute(open(os.path.join(ROOT, "db/shim/uuidv7_pg16.sql")).read())
    cur.execute("REVOKE CREATE ON SCHEMA ext FROM PUBLIC; GRANT USAGE ON SCHEMA ext TO PUBLIC")
    cur.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")
    _role(cur, "core_owner", False); _role(cur, "plugin_worker", False)
    for r in LOGINS: _role(cur, r, True)
    cur.execute(sql.SQL("GRANT CREATE ON DATABASE {} TO core_owner").format(sql.Identifier(dbname)))

    core_files = sorted((int(os.path.basename(p)[:3]), p) for p in glob.glob(os.path.join(ROOT, "db/core/[0-9]*.sql"))
                        if not os.path.basename(p).startswith("09"))           # 09x = grants, re-run every init
    _migrate(conn, "core", "core_owner", "pg_catalog, ext", core_files, log)

    for d in sorted(glob.glob(os.path.join(plugin_root or os.path.join(ROOT, "plugins"), "*", "plugin.yaml"))):
        pdir = os.path.dirname(d)
        try:
            m = mf.load(pdir)
        except mf.ManifestError as e:
            log(f"plugin {pdir}: refused ({e})"); continue
        if not _requires_ok(m["requires"]["core"], CORE_VERSION):
            log(f"plugin {m['id']}: refused (requires core {m['requires']['core']})"); continue
        pid, owner, schema = m["id"], f"plugin_{m['id']}_owner", m["schema"]
        _role(cur, f"plugin_{pid}", True); _role(cur, owner, False)
        I = sql.Identifier
        cur.execute(sql.SQL("GRANT plugin_worker TO {}").format(I(f"plugin_{pid}")))
        cur.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {} AUTHORIZATION {}").format(I(schema), I(owner)))
        cur.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(I(schema), I(f"plugin_{pid}")))
        cur.execute(sql.SQL("ALTER DEFAULT PRIVILEGES FOR ROLE {o} IN SCHEMA {s} GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {l};"
                            "ALTER DEFAULT PRIVILEGES FOR ROLE {o} IN SCHEMA {s} GRANT USAGE, SELECT ON SEQUENCES TO {l}")
                    .format(o=I(owner), s=I(schema), l=I(f"plugin_{pid}")))
        _migrate(conn, pid, owner, f"{schema}, pg_catalog, ext", mf.migrations(pdir, m), log)
        cur.execute("SELECT core.plugin_register(%s::jsonb, %s, %s)",
                    (mf.canonical(m), mf.manifest_hash(m), mf.dir_hash(pdir)))
        log(f"plugin {pid}@{m['version']}: {cur.fetchone()[0]['outcome']}")

    with conn.transaction():
        cur.execute("SET LOCAL ROLE core_owner")
        cur.execute(open(os.path.join(ROOT, "db/core/090_grants.sql")).read())
        cur.execute("RESET ROLE")
        cur.execute("INSERT INTO core.schema_version (component, version) VALUES ('core', %s) "
                    "ON CONFLICT (component) DO UPDATE SET version = EXCLUDED.version, stamped_at = now()", (CORE_VERSION,))
    conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", default=os.environ.get("CODERAG_ADMIN_DSN"))
    ap.add_argument("--plugins")
    a = ap.parse_args()
    init(a.dsn, a.plugins)
