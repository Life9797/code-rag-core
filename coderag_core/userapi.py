"""Core user API (/core/v1): thin JSON mapping onto the SQL contracts as the `api` login, principal = token `sub`.
Upload connector (§29, core.upload): zip or pasted files -> vol snapshot -> file_tree@1 revision (revision_key = content
sha256, so the same upload twice is the same revision) -> optional job. Search adds each enabled plugin's query vectors."""
import psycopg
import hashlib, importlib, io, json, os, re, tempfile, uuid
from fastapi import APIRouter, Depends, Request, UploadFile, File, Form
from fastapi.responses import JSONResponse
import secrets as pysecrets
from . import auth, files, secrets, storage

STATUS = {"NAME_TAKEN": 409, "LAST_ADMIN": 409, "NOT_TENANT_MEMBER": 409, "EXISTS": 409, "UNKNOWN_KEY": 400, "DESTINATION_NOT_ALLOWED": 400, "HASH_MISMATCH": 409, "EXPIRED": 409, "ALREADY_DECIDED": 409, "FORBIDDEN": 403, "NOT_FOUND": 404, "INPUT_INVALID": 400, "REQUEST_ID_REUSED": 409, "PROJECT_DELETING": 409}
PASTE = re.compile(r"^[A-Za-z0-9_.\-/]{1,200}$")


def user_router(settings, claims, manifests) -> APIRouter:
    r = APIRouter(prefix="/core/v1")

    def call(fn, *args):
        try:
            with settings.connect("api") as c:
                return c.execute(f"SELECT {fn}({', '.join(['%s'] * len(args))})",
                                 [json.dumps(a) if isinstance(a, dict) else a for a in args]).fetchone()[0]
        except psycopg.errors.DataError:                          # malformed id or value -> 400, not 500
            return {"outcome": "INPUT_INVALID"}

    def out(res):
        return JSONResponse(res, STATUS.get(res.get("outcome"), 200))

    def rid(body):
        v = body.get("request_id")
        if not v: return str(uuid.uuid4())
        try: return str(uuid.UUID(str(v)))
        except ValueError: return str(uuid.uuid5(uuid.NAMESPACE_URL, str(v)))

    async def body(req):
        try: return await req.json()
        except ValueError: return {}

    # ---------- admin page (§43): tenants, principals, service clients, project detail ----------
    @r.post("/tenants")
    async def tenant_create(req: Request, cl=Depends(claims)):
        b = await body(req); return out(call("core.tenant_create", cl["sub"], b.get("name"), rid(b)))

    @r.get("/tenants/{tenant}")
    def tenant_detail(tenant: str, cl=Depends(claims)): return out(call("core.tenant_detail", cl["sub"], tenant))

    @r.post("/tenants/{tenant}/members")
    async def tenant_member(tenant: str, req: Request, cl=Depends(claims)):
        """{principal, role: 1..4 | null = remove}"""
        b = await body(req); return out(call("core.member_change", cl["sub"], tenant, None, b.get("principal"), b.get("role"), rid(b)))

    @r.post("/tenants/{tenant}/principals")
    async def principal_create(tenant: str, req: Request, cl=Depends(claims)):
        """{name, role, issuer?, subject?}: a person; issuer + subject link a Keycloak account (whoami shows both)."""
        b = await body(req)
        return out(call("core.principal_create", cl["sub"], tenant, "human", b.get("name"), b.get("role"),
                        (b.get("issuer") or "").rstrip("/") or None, b.get("subject") or None, rid(b)))

    @r.post("/tenants/{tenant}/clients")
    async def client_create(tenant: str, req: Request, cl=Depends(claims)):
        """{name, role}: service client for Open WebUI or scripts; the secret is shown once."""
        b = await body(req)
        secret, h = auth.new_secret()
        res = call("core.service_client_create", cl["sub"], tenant, b.get("name"), b.get("role"), ["core"], [auth.AUD], None, h, rid(b))
        return out({**res, "secret": secret} if res.get("outcome") == "CREATED" and not res.get("replay") else res)

    @r.delete("/clients/{client}")
    async def client_revoke(client: str, req: Request, cl=Depends(claims)):
        b = await body(req); return out(call("core.service_client_revoke", cl["sub"], client, rid(b)))

    @r.get("/projects/{project}")
    def project_detail(project: str, cl=Depends(claims)): return out(call("core.project_detail", cl["sub"], project))

    @r.get("/projects")
    def projects(cl=Depends(claims)): return out(call("core.list_projects", cl["sub"]))

    @r.post("/projects")
    async def project_create(req: Request, cl=Depends(claims)):
        b = await body(req); return out(call("core.project_create", cl["sub"], b.get("tenant"), b.get("name"), rid(b)))

    @r.post("/projects/{project}/members")
    async def member(project: str, req: Request, cl=Depends(claims)):
        b = await body(req)
        return out(call("core.member_change", cl["sub"], b.get("tenant"), project, b.get("principal"), b.get("role"), rid(b)))

    @r.post("/projects/{project}/plugins/{plugin}")
    async def plugin(project: str, plugin: str, req: Request, cl=Depends(claims)):
        b = await body(req); return out(call("core.plugin_enable", cl["sub"], project, plugin, b.get("enabled", True), rid(b)))

    @r.delete("/projects/{project}")
    async def project_delete(project: str, req: Request, cl=Depends(claims)):
        b = await body(req); return out(call("core.delete_project", cl["sub"], project, rid(b)))

    @r.post("/projects/{project}/sources")
    async def source(project: str, req: Request, cl=Depends(claims)):
        b = await body(req)
        return out(call("core.source_create", cl["sub"], project, b.get("connector", "core.upload"), b.get("external_id"), False, rid(b)))

    @r.post("/projects/{project}/webhooks")
    async def webhook(project: str, req: Request, cl=Depends(claims)):
        """{url, event_types[]} -> id + HMAC secret (shown once; stored encrypted with the host key)."""
        b = await body(req)
        secret = pysecrets.token_urlsafe(32)
        kid, ct = secrets.encrypt(secret)
        res = call("core.webhook_subscribe", cl["sub"], project, b.get("url"), b.get("event_types") or ["knowledge.published"], kid, ct, rid(b))
        return out({**res, "secret": secret} if res["outcome"] == "CREATED" and not res.get("replay") else res)

    @r.delete("/webhooks/{sub}")
    async def webhook_delete(sub: str, req: Request, cl=Depends(claims)):
        b = await body(req); return out(call("core.webhook_unsubscribe", cl["sub"], sub, rid(b)))

    @r.post("/policies")
    async def policy(req: Request, cl=Depends(claims)):
        b = await body(req)
        return out(call("core.policy_set", cl["sub"], b.get("scope"), b.get("ref"), b.get("key"), json.dumps(b.get("value")), rid(b)))

    @r.post("/sources/{source}/revisions")
    async def upload(source: str, req: Request, cl=Depends(claims)):
        """multipart: archive=<zip> [+ plugin, pipeline] | json: {files: [{path, text}], plugin?, pipeline?}"""
        si = call("core.source_info", cl["sub"], source, 2)
        if si["outcome"] != "OK": return out(si)
        if req.headers.get("content-type", "").startswith("multipart/"):
            form = await req.form()
            b = {k: v for k, v in form.items() if isinstance(v, str)}
            arc = form.get("archive")
            data = await arc.read() if arc is not None and not isinstance(arc, str) else b""
            members = None
        else:
            b = await body(req)
            members = {}
            for f in b.get("files") or []:
                p = f.get("path") or ""
                if not PASTE.match(p) or ".." in p.split("/") or p.startswith("/"): return out({"outcome": "INPUT_INVALID", "path": p})
                members[p] = (f.get("text") or "").encode()
            if not members: return out({"outcome": "INPUT_INVALID"})
            buf = io.BytesIO()
            import zipfile
            with zipfile.ZipFile(buf, "w") as z:
                for p in sorted(members): z.writestr(p, members[p])
            data = buf.getvalue()
        key = hashlib.sha256(data if members is None else json.dumps({p: hashlib.sha256(v).hexdigest() for p, v in members.items()},
                                                                     sort_keys=True).encode()).hexdigest()
        ref = f"vol://sources/{source}/{key[:32]}"
        with tempfile.NamedTemporaryFile(suffix=".zip") as tmp:
            tmp.write(data); tmp.flush()
            try: man = files.safe_extract(tmp.name, ref, strip_root=members is None)
            except files.InputError as e: return out({"outcome": "INPUT_INVALID", "error_code": f"core.{e.code}"})
        mh = hashlib.sha256(json.dumps(man, sort_keys=True).encode()).hexdigest()
        rv = call("core.revision_create", cl["sub"], source, key, man, mh, ref, True, rid(b))
        if rv["outcome"] not in ("CREATED", "EXISTS"):
            if rv["outcome"] != "REQUEST_ID_REUSED": storage.remove_tree(ref)
            return out(rv)
        res = {**rv, "revision_id": rv["id"], "files": len(man["files"])}
        if b.get("plugin"):
            res["job"] = call("runtime.create_job", cl["sub"], si["project_id"], b["plugin"], b.get("pipeline", "index"), rv["id"],
                              str(b.get("force", "")).lower() in ("1", "true"), str(uuid.uuid5(uuid.NAMESPACE_URL, rid(b) + ":job")))
        return out(res)

    @r.post("/jobs")
    async def job_create(req: Request, cl=Depends(claims)):
        b = await body(req)
        return out(call("runtime.create_job", cl["sub"], b.get("project"), b.get("plugin"), b.get("pipeline"), b.get("revision"),
                        bool(b.get("force")), rid(b)))

    @r.get("/projects/{project}/jobs")
    def jobs(project: str, source: str = None, limit: int = 20, cl=Depends(claims)):
        return out(call("runtime.list_jobs", cl["sub"], project, source, limit))

    @r.get("/jobs/{job}")
    def job_get(job: str, cl=Depends(claims)): return out(call("runtime.get_job", cl["sub"], job))

    @r.get("/jobs/{job}/items")
    def job_items(job: str, after: str = "", limit: int = 100, cl=Depends(claims)):
        return out(call("runtime.job_items", cl["sub"], job, after, limit))

    @r.post("/jobs/{job}/cancel")
    async def job_cancel(job: str, req: Request, cl=Depends(claims)):
        b = await body(req); return out(call("runtime.cancel_job", cl["sub"], job, rid(b)))

    @r.post("/jobs/{job}/retry")
    async def job_retry(job: str, req: Request, cl=Depends(claims)):
        b = await body(req); return out(call("runtime.retry_job", cl["sub"], job, rid(b)))

    @r.post("/jobs/{job}/approve")
    async def job_approve(job: str, req: Request, cl=Depends(claims)):
        """U4 {stage, artifact_sha256?, decision: approve|reject}; approval binds the current artifact hash (R15)."""
        b = await body(req)
        return out(call("runtime.approve", cl["sub"], job, b.get("stage"), b.get("artifact_sha256"), b.get("decision", "approve"), rid(b)))

    @r.post("/tenants/{tenant}/secrets")
    async def secret_set(tenant: str, req: Request, cl=Depends(claims)):
        """P-P12 {connector: <plugin>.<connector>, name?, value}: stored encrypted with the host key; never returned."""
        b = await body(req)
        if not isinstance(b.get("value"), str) or not b["value"]: return out({"outcome": "INPUT_INVALID"})
        kid, ct = secrets.encrypt(b["value"])
        return out(call("core.connector_secret_set", cl["sub"], tenant, b.get("connector"), b.get("name") or "default", kid, ct, rid(b)))

    @r.get("/status")
    def status(cl=Depends(claims)): return out(call("core.status", cl["sub"]))

    @r.post("/search")
    async def search(req: Request, cl=Depends(claims)):
        """{projects[], query, k?, collections?, filters?, pins?, include_content?}; query vectors from loaded plugins."""
        b = await body(req)
        q = b.get("query") or ""
        qv = {}
        for m in manifests.values():
            fn = getattr(importlib.import_module(m["entry"]), "query_vectors", None)
            if fn and q: qv.update(fn(q))
        with settings.connect("api") as c:
            c.autocommit = False
            c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            res = c.execute("SELECT knowledge.search(%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                            (cl["sub"], b.get("projects") or [], b.get("collections"), q, json.dumps(qv),
                             json.dumps(b.get("filters") or {}), b.get("k", 10), json.dumps(b["pins"]) if b.get("pins") else None,
                             bool(b.get("include_content")))).fetchone()[0]
            c.rollback()
        return out(res)

    return r
