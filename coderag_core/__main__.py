"""Process entry (§28.2, one image for every role):
  python -m coderag_core init          superuser: roles, migrations, plugins, grants; first signing key if none
  python -m coderag_core api [--port]  user API + Worker Protocol HTTP binding
  python -m coderag_core worker        Core runtime/maintenance loops + in-process workers for every loaded plugin
Env: CODERAG_ADMIN_DSN (init), CODERAG_DSN_BASE (+ user=<login>), CODERAG_PLUGINS, CODERAG_VOL_ROOT, CODERAG_KEYS_DIR."""
import argparse, logging, os, sys, time
import psycopg
from . import api, auth, initdb, runner, secrets
from .worker import Worker, SqlBinding

log = logging.getLogger("coderag")


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
        with psycopg.connect(dsn, autocommit=True) as c:
            pw = os.environ.get("CODERAG_DB_PASSWORD")
            if pw:
                from psycopg import sql
                for (r,) in c.execute("SELECT rolname FROM pg_roles WHERE rolcanlogin AND rolname IN ('api', 'runtime', 'purge', 'maint') "
                                      "OR rolname LIKE 'plugin\\_%' AND rolcanlogin"):
                    c.execute(sql.SQL("ALTER ROLE {} PASSWORD {}").format(sql.Identifier(r), sql.Literal(pw)))
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
    last_maint = 0.0
    while True:
        busy = 0
        try:
            runner.runtime_tick(settings.connect)
            for w in workers: busy += w.run_once()
            if time.time() - last_maint > float(os.environ.get("CODERAG_MAINT_S", "30")):
                runner.maint_tick(settings.connect, manifests); last_maint = time.time()
        except Exception:
            log.exception("worker tick failed")
            time.sleep(5)
        if not busy: time.sleep(1)


if __name__ == "__main__":
    sys.exit(main())
