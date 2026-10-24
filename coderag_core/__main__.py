"""Process entry (§28.2, one image for every role):
  python -m coderag_core init          superuser: roles, migrations, plugins, grants; first signing key if none
  python -m coderag_core api [--port]  user API + Worker Protocol HTTP binding
  python -m coderag_core worker        Core runtime/maintenance loops + in-process workers for every loaded plugin
Env: CODERAG_ADMIN_DSN (init), CODERAG_DB_SECRETS_DIR (init: per-login passwords; PGPASSFILE in api/worker), CODERAG_DSN_BASE (+ user=<login>), CODERAG_PLUGINS, CODERAG_VOL_ROOT, CODERAG_KEYS_DIR."""
import argparse, logging, os, sys, time
import psycopg
from . import api, auth, initdb, runner, secrets
from .worker import Worker, SqlBinding

log = logging.getLogger("coderag")


def db_logins(dsn, d):
    """Review 6: one random password per login, kept in <d>/roles.json (0600, reused on re-init). pgpass files per
    container: <d>/api/pgpass (api + plugin_*: HTTP worker binding), <d>/worker/pgpass (runtime, purge, maint, plugin_*)."""
    import json, secrets as pysecrets
    from psycopg import sql
    os.makedirs(d, mode=0o700, exist_ok=True)
    path = os.path.join(d, "roles.json")
    pw = json.load(open(path)) if os.path.exists(path) else {}
    with psycopg.connect(dsn, autocommit=True) as c:
        roles = [r for (r,) in c.execute("SELECT rolname FROM pg_roles WHERE rolcanlogin AND (rolname IN ('api', 'runtime', 'purge', "
                                         "'maint') OR rolname LIKE 'plugin\\_%') ORDER BY 1")]
        for r in roles:
            pw.setdefault(r, pysecrets.token_urlsafe(24))
            c.execute(sql.SQL("ALTER ROLE {} PASSWORD {}").format(sql.Identifier(r), sql.Literal(pw[r])))
    def write(p, text):
        fd = os.open(p + ".tmp", os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f: f.write(text)
        os.replace(p + ".tmp", p)
    write(path, json.dumps(pw, indent=1))
    for box, keep in (("api", lambda r: r == "api" or r.startswith("plugin_")), ("worker", lambda r: r != "api")):
        os.makedirs(os.path.join(d, box), mode=0o700, exist_ok=True)
        write(os.path.join(d, box, "pgpass"), "".join(f"*:*:*:{r}:{pw[r]}\n" for r in roles if keep(r)))
    os.environ["PGPASSFILE"] = os.path.join(d, "worker", "pgpass")     # init itself goes on as maint


def main():
    a = argparse.ArgumentParser()
    a.add_argument("role", choices=["init", "api", "worker"]); a.add_argument("--port", type=int, default=8080)
    a.add_argument("--roles-only", action="store_true", help="restore drill: roles before pg_restore (§38)")
    o = a.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    plugins = os.environ.get("CODERAG_PLUGINS") or None
    if o.role == "init":
        dsn = os.environ["CODERAG_ADMIN_DSN"]
        initdb.init(dsn, plugins, log=log.info, roles_only=o.roles_only)
        db_logins(dsn, os.environ.get("CODERAG_DB_SECRETS_DIR", "/data/db"))
        if o.roles_only: return
        log.info("secrets key: %s", secrets.ensure_key())
        with api.Settings().connect("maint") as c:                 # MG-07: first key by the maint login
            if not c.execute("SELECT core.signing_key_active()").fetchone()[0]:
                keys = os.environ.get("CODERAG_KEYS_DIR", "/data/keys"); os.makedirs(keys, exist_ok=True)
                log.info("signing key: %s", auth.rotate_key(c, keys))
        return
    settings = api.Settings()
    if o.role == "api":
        import uvicorn
        uvicorn.run(api.create_app(settings), host="0.0.0.0", port=o.port)
        return
    manifests = api.load_manifests(plugins)
    workers = []
    for pid, m in manifests.items():
        b, db = settings.connect(f"plugin_{pid}"), settings.connect(f"plugin_{pid}")
        workers.append(Worker.load(m, SqlBinding(b), db))
    import threading
    threading.Thread(target=runner.webhook_loop, args=(settings.connect,), daemon=True).start()
    last_maint = 0.0
    while True:
        busy = 0
        try:
            runner.runtime_tick(settings.connect)
            for w in workers: busy += w.run_once()
            if time.time() - last_maint > float(os.environ.get("CODERAG_MAINT_S", "30")):
                runner.maint_tick(settings.connect, manifests, webhooks=False); last_maint = time.time()
        except Exception:
            log.exception("worker tick failed")
            time.sleep(5)
        if not busy: time.sleep(1)


if __name__ == "__main__":
    sys.exit(main())
