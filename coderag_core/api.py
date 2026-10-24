"""Core API (thin): token + JWKS (P-P4), health, and the Worker Protocol HTTP binding (§28.1, D-15).
Each request opens its own connection as the right login: `api` for user contracts, `plugin_<id>` for worker calls."""
import json, os, threading
import psycopg
from fastapi import FastAPI, APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import HTMLResponse
from . import auth
from .worker import STAGE_OPS, SqlBinding
from .userapi import user_router


class Settings:
    def __init__(self, dsn_base=None, dsn_for=None):
        # dsn_for(login) -> DSN string; default: CODERAG_DSN_BASE + " user=<login>" (password via PGPASSFILE / env)
        self.dsn_for = dsn_for or (lambda login: f"{dsn_base or os.environ['CODERAG_DSN_BASE']} user={login}")
        self.pools, self.lock = {}, threading.Lock()
    def connect(self, login): return psycopg.connect(self.dsn_for(login), autocommit=True)
    def conn(self, login):
        """Pooled connection per login for API requests (review 7): `with settings.conn(login) as c`."""
        with self.lock:
            p = self.pools.get(login)
            if p is None:
                from psycopg_pool import ConnectionPool
                p = self.pools[login] = ConnectionPool(self.dsn_for(login), min_size=1, max_size=int(os.environ.get("CODERAG_DB_POOL", "10")),
                                                       kwargs={"autocommit": True}, reset=lambda c: setattr(c, "autocommit", True),
                                                       check=ConnectionPool.check_connection, open=True)
        return p.connection()


def create_app(settings: Settings, manifests=None, oidc=None) -> FastAPI:
    app = FastAPI(title="code-rag core", version="0.4.0")
    oidc = oidc if oidc is not None else auth.Oidc.from_env()

    def identify(tok):
        """(claims, unlinked identity): a Core token, or a linked Keycloak token mapped to its principal."""
        if not tok: return None, None
        with settings.conn("api") as c:
            cl = auth.verify_token(c, tok)
            if cl or not oidc: return cl, None
            ident = oidc.verify(tok)
            if not ident: return None, None
            pid = c.execute("SELECT core.principal_for_identity(%s, %s)", (ident["issuer"], ident["subject"])).fetchone()[0]
        return ({"sub": str(pid), "scope": "core", "via": "oidc"}, None) if pid else (None, ident)

    def claims(authorization: str = Header(default="")):
        cl, ident = identify(authorization.removeprefix("Bearer ").strip())
        if not cl: raise HTTPException(401, "identity_not_linked" if ident else "invalid token")
        return cl

    @app.get("/core/v1/auth/config")
    def auth_config():
        return {"oidc": {"issuer": oidc.issuer, "client_id": oidc.client_id} if oidc else None}

    @app.get("/core/v1/whoami")
    def whoami(authorization: str = Header(default="")):
        """Signed-in principal; for a Keycloak user not linked yet: the (issuer, subject) an admin needs to link."""
        cl, ident = identify(authorization.removeprefix("Bearer ").strip())
        if ident: return {"outcome": "NOT_LINKED", **ident}
        if not cl: raise HTTPException(401, "invalid token")
        with settings.conn("api") as c:
            return c.execute("SELECT core.admin_overview(%s)", (cl["sub"],)).fetchone()[0]

    @app.get("/admin", response_class=HTMLResponse)
    def admin_page():
        with open(os.path.join(os.path.dirname(__file__), "admin.html"), encoding="utf-8") as f: return f.read()

    async def form_of(req: Request): return await req.form()
    limiter = auth.Limiter()

    @app.post("/core/v1/token")
    def token(req: Request, form=Depends(form_of)):
        """Threadpool route: argon2 (64 MB, t=3) never runs on the event loop; failures are rate limited (review 1)."""
        cid, who = str(form.get("client_id", "")), req.client.host if req.client else ""
        if not limiter.allow(cid, who): raise HTTPException(429, "too_many_attempts")
        with limiter.slot(), settings.conn("api") as c:
            t = auth.issue_token(c, cid, form.get("client_secret", ""))
        if not t: limiter.fail(cid, who); raise HTTPException(401, "invalid_client")
        return {"access_token": t, "token_type": "Bearer"}

    @app.get("/core/v1/jwks")
    def jwks():
        with settings.conn("api") as c:
            return {"keys": c.execute("SELECT core.jwks()").fetchone()[0]}

    @app.get("/core/v1/health")
    def health():
        with settings.conn("api") as c:
            c.execute("SELECT 1")
        return {"db": "ok"}

    app.include_router(worker_router(settings, claims))
    app.include_router(user_router(settings, claims, manifests if manifests is not None else load_manifests()))
    return app


def worker_router(settings, claims) -> APIRouter:
    """Same request/response JSON as the in-process binding; scope runtime.worker:<plugin> selects the plugin login."""
    r = APIRouter(prefix="/runtime/v1")
    ARGS = {"claim": ("max", "versions", "request_id"),
            "heartbeat": ("task_id", "lease_token", "lease_epoch", "checkpoint", "extend"),
            "complete": ("task_id", "lease_token", "lease_epoch", "result", "request_id"),
            "fail": ("task_id", "lease_token", "lease_epoch", "error_code", "detail", "request_id"),
            "split": ("task_id", "lease_token", "lease_epoch", "children", "request_id")}

    def plugin_of(cl):
        ps = [s.split(":", 1)[1] for s in cl.get("scope", "").split() if s.startswith("runtime.worker:")]
        if len(ps) != 1: raise HTTPException(403, "scope runtime.worker:<plugin> required")
        return ps[0]

    async def jbody(req: Request): return await req.json()

    def make(op):
        def handler(body=Depends(jbody), cl=Depends(claims)):
            args = [body.get(k) for k in ARGS[op]]
            args = [json.dumps(a) if isinstance(a, (dict, list)) and k in ("result", "detail", "children") else a
                    for k, a in zip(ARGS[op], args)]
            with settings.conn(f"plugin_{plugin_of(cl)}") as c:
                ph = ", ".join(["%s"] * len(args))
                return c.execute(f"SELECT runtime.{op}({ph})", args).fetchone()[0]
        return handler

    for op in ARGS: r.add_api_route(f"/{op}", make(op), methods=["POST"])

    @r.post("/stage/{op}")
    def stage(op: str, body=Depends(jbody), cl=Depends(claims)):
        if op not in STAGE_OPS: raise HTTPException(404)
        with settings.conn(f"plugin_{plugin_of(cl)}") as c:
            return SqlBinding(c).stage(op, body, **{k: body.get(k) for k in STAGE_OPS[op][1]})
    return r


def load_manifests(root=None):
    """{plugin id: manifest} for every plugin folder (CODERAG_PLUGINS, default <repo>/plugins)."""
    import glob
    from . import manifest as mf
    root = root or os.environ.get("CODERAG_PLUGINS") or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plugins")
    return {m["id"]: m for m in (mf.load(os.path.dirname(p)) for p in sorted(glob.glob(os.path.join(root, "*", "plugin.yaml"))))}
