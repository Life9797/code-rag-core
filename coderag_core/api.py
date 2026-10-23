"""Core API (thin): token + JWKS (P-P4), health, and the Worker Protocol HTTP binding (§28.1, D-15).
Each request opens its own connection as the right login: `api` for user contracts, `plugin_<id>` for worker calls."""
import json, os
import psycopg
from fastapi import FastAPI, APIRouter, Depends, Header, HTTPException, Request
from . import auth
from .worker import STAGE_OPS, SqlBinding
from .userapi import user_router


class Settings:
    def __init__(self, dsn_base=None, dsn_for=None):
        # dsn_for(login) -> DSN string; default: CODERAG_DSN_BASE + " user=<login>" (password via PGPASSFILE / env)
        self.dsn_for = dsn_for or (lambda login: f"{dsn_base or os.environ['CODERAG_DSN_BASE']} user={login}")
    def connect(self, login): return psycopg.connect(self.dsn_for(login), autocommit=True)


def create_app(settings: Settings, manifests=None) -> FastAPI:
    app = FastAPI(title="code-rag core", version="0.4.0")

    def claims(authorization: str = Header(default="")):
        tok = authorization.removeprefix("Bearer ").strip()
        with settings.connect("api") as c:
            cl = auth.verify_token(c, tok) if tok else None
        if not cl: raise HTTPException(401, "invalid token")
        return cl

    @app.post("/core/v1/token")
    async def token(req: Request):
        form = await req.form()
        with settings.connect("api") as c:
            t = auth.issue_token(c, form.get("client_id", ""), form.get("client_secret", ""))
        if not t: raise HTTPException(401, "invalid_client")
        return {"access_token": t, "token_type": "Bearer"}

    @app.get("/core/v1/jwks")
    def jwks():
        with settings.connect("api") as c:
            return {"keys": c.execute("SELECT core.jwks()").fetchone()[0]}

    @app.get("/core/v1/health")
    def health():
        with settings.connect("api") as c:
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

    def make(op):
        async def handler(req: Request, cl=Depends(claims)):
            body = await req.json()
            args = [body.get(k) for k in ARGS[op]]
            args = [json.dumps(a) if isinstance(a, (dict, list)) and k in ("result", "detail", "children") else a
                    for k, a in zip(ARGS[op], args)]
            with settings.connect(f"plugin_{plugin_of(cl)}") as c:
                ph = ", ".join(["%s"] * len(args))
                return c.execute(f"SELECT runtime.{op}({ph})", args).fetchone()[0]
        return handler

    for op in ARGS: r.add_api_route(f"/{op}", make(op), methods=["POST"])

    @r.post("/stage/{op}")
    async def stage(op: str, req: Request, cl=Depends(claims)):
        if op not in STAGE_OPS: raise HTTPException(404)
        body = await req.json()
        with settings.connect(f"plugin_{plugin_of(cl)}") as c:
            return SqlBinding(c).stage(op, body, **{k: body.get(k) for k in STAGE_OPS[op][1]})
    return r


def load_manifests(root=None):
    """{plugin id: manifest} for every plugin folder (CODERAG_PLUGINS, default <repo>/plugins)."""
    import glob
    from . import manifest as mf
    root = root or os.environ.get("CODERAG_PLUGINS") or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "plugins")
    return {m["id"]: m for m in (mf.load(os.path.dirname(p)) for p in sorted(glob.glob(os.path.join(root, "*", "plugin.yaml"))))}
