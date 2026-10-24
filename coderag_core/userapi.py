"""Core user API (/core/v1): thin JSON mapping onto the SQL contracts as the `api` login, principal = token `sub`.
Upload connector (§29, core.upload): zip or pasted files -> vol snapshot -> file_tree@1 revision (revision_key = content
sha256, so the same upload twice is the same revision) -> optional job. Search adds each enabled plugin's query vectors."""
import psycopg
import hashlib, importlib, io, json, os, re, tempfile, uuid
from fastapi import APIRouter, Depends, Request, UploadFile, File, Form
from fastapi.responses import JSONResponse
import secrets as pysecrets
from . import auth, files, secrets, storage

STATUS = {"NAME_TAKEN": 409, "LAST_ADMIN": 409, "NOT_TENANT_MEMBER": 409, "EXISTS": 409, "UNKNOWN_KEY": 400, "DESTINATION_NOT_ALLOWED": 400, "HASH_MISMATCH": 409, "EXPIRED": 409, "ALREADY_DECIDED": 409, "FORBIDDEN": 403, "NOT_FOUND": 404, "INPUT_INVALID": 400, "REQUEST_ID_REUSED": 409, "PROJECT_DELETING": 409, "TOO_LARGE": 413}
MAX_UPLOAD = int(os.environ.get("CODERAG_MAX_UPLOAD_BYTES", str(1 << 30)))   # also cap the body at the proxy
PASTE = re.compile(r"^[A-Za-z0-9_.\-/]{1,200}$")


def user_router(settings, claims, manifests) -> APIRouter:
    r = APIRouter(prefix="/core/v1")

    def call(fn, *args):
        try:
            with settings.conn("api") as c:
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

    async def body(req: Request):        # async dependency: routes stay plain `def` and run in the threadpool (review 1)
        try: b = await req.json()
        except ValueError: return {}
        return b if isinstance(b, dict) else {}

    # ---------- admin page (§43): tenants, principals, service clients, project detail ----------
    @r.post("/tenants")
    def tenant_create(b=Depends(body), cl=Depends(claims)):
        return out(call("core.tenant_create", cl["sub"], b.get("name"), rid(b)))

    @r.get("/tenants/{tenant}")
    def tenant_detail(tenant: str, cl=Depends(claims)): return out(call("core.tenant_detail", cl["sub"], tenant))

    @r.post("/tenants/{tenant}/members")
    def tenant_member(tenant: str, b=Depends(body), cl=Depends(claims)):
        """{principal, role: 1..4 | null = remove}"""
        return out(call("core.member_change", cl["sub"], tenant, None, b.get("principal"), b.get("role"), rid(b)))

    @r.post("/tenants/{tenant}/principals")
    def principal_create(tenant: str, b=Depends(body), cl=Depends(claims)):
        """{name, role, issuer?, subject?}: a person; issuer + subject link a Keycloak account (whoami shows both)."""
        return out(call("core.principal_create", cl["sub"], tenant, "human", b.get("name"), b.get("role"),
                        (b.get("issuer") or "").rstrip("/") or None, b.get("subject") or None, rid(b)))

    @r.post("/tenants/{tenant}/clients")
    def client_create(tenant: str, b=Depends(body), cl=Depends(claims)):
        """{name, role}: service client for Open WebUI or scripts; the secret is shown once."""
        secret, h = auth.new_secret()
        res = call("core.service_client_create", cl["sub"], tenant, b.get("name"), b.get("role"), ["core"], [auth.AUD], None, h, rid(b))
        return out({**res, "secret": secret} if res.get("outcome") == "CREATED" and not res.get("replay") else res)

    @r.delete("/clients/{client}")
    def client_revoke(client: str, b=Depends(body), cl=Depends(claims)):
        return out(call("core.service_client_revoke", cl["sub"], client, rid(b)))

    @r.get("/projects/{project}")
    def project_detail(project: str, cl=Depends(claims)): return out(call("core.project_detail", cl["sub"], project))

    @r.get("/projects")
    def projects(cl=Depends(claims)): return out(call("core.list_projects", cl["sub"]))

    @r.post("/projects")
    def project_create(b=Depends(body), cl=Depends(claims)):
        return out(call("core.project_create", cl["sub"], b.get("tenant"), b.get("name"), rid(b)))

    @r.post("/projects/{project}/members")
    def member(project: str, b=Depends(body), cl=Depends(claims)):
        return out(call("core.member_change", cl["sub"], b.get("tenant"), project, b.get("principal"), b.get("role"), rid(b)))

    @r.post("/projects/{project}/plugins/{plugin}")
    def plugin(project: str, plugin: str, b=Depends(body), cl=Depends(claims)):
        return out(call("core.plugin_enable", cl["sub"], project, plugin, b.get("enabled", True), rid(b)))

    @r.delete("/projects/{project}")
    def project_delete(project: str, b=Depends(body), cl=Depends(claims)):
        return out(call("core.delete_project", cl["sub"], project, rid(b)))

    @r.post("/projects/{project}/sources")
    def source(project: str, b=Depends(body), cl=Depends(claims)):
        return out(call("core.source_create", cl["sub"], project, b.get("connector", "core.upload"), b.get("external_id"), False, rid(b)))

    @r.post("/projects/{project}/webhooks")
    def webhook(project: str, b=Depends(body), cl=Depends(claims)):
        """{url, event_types[]} -> id + HMAC secret (shown once; stored encrypted with the host key)."""
        secret = pysecrets.token_urlsafe(32)
        kid, ct = secrets.encrypt(secret)
        res = call("core.webhook_subscribe", cl["sub"], project, b.get("url"), b.get("event_types") or ["knowledge.published"], kid, ct, rid(b))
        return out({**res, "secret": secret} if res["outcome"] == "CREATED" and not res.get("replay") else res)

    @r.delete("/webhooks/{sub}")
    def webhook_delete(sub: str, b=Depends(body), cl=Depends(claims)):
        return out(call("core.webhook_unsubscribe", cl["sub"], sub, rid(b)))

    @r.post("/policies")
    def policy(b=Depends(body), cl=Depends(claims)):
        return out(call("core.policy_set", cl["sub"], b.get("scope"), b.get("ref"), b.get("key"), json.dumps(b.get("value")), rid(b)))

    async def upload_in(req: Request):
        """Reads the request; the route runs in the threadpool. Multipart files spool to disk (review 10)."""
        if int(req.headers.get("content-length") or 0) > MAX_UPLOAD: return None, None, None
        if req.headers.get("content-type", "").startswith("multipart/"):
            form = await req.form()
            arc = form.get("archive")
            return {k: v for k, v in form.items() if isinstance(v, str)}, None if isinstance(arc, str) else arc, True
        return await body(req), None, False

    @r.post("/sources/{source}/revisions")
    def upload(source: str, inp=Depends(upload_in), cl=Depends(claims)):
        """multipart: archive=<zip> [+ plugin, pipeline] | json: {files: [{path, text}], plugin?, pipeline?}"""
        b, arc, multipart = inp
        if b is None: return out({"outcome": "TOO_LARGE"})
        si = call("core.source_info", cl["sub"], source, 2)
        if si["outcome"] != "OK": return out(si)
        with tempfile.NamedTemporaryFile(suffix=".zip") as tmp:
            if multipart:
                members, h, n = None, hashlib.sha256(), 0
                while arc is not None and (chunk := arc.file.read(1 << 20)):    # stream with a running cap
                    n += len(chunk)
                    if n > MAX_UPLOAD: return out({"outcome": "TOO_LARGE"})
                    h.update(chunk); tmp.write(chunk)
                key = h.hexdigest()
            else:
                members = {}
                for f in b.get("files") or []:
                    p = f.get("path") or ""
                    if not PASTE.match(p) or ".." in p.split("/") or p.startswith("/"): return out({"outcome": "INPUT_INVALID", "path": p})
                    members[p] = (f.get("text") or "").encode()
                if not members: return out({"outcome": "INPUT_INVALID"})
                import zipfile
                with zipfile.ZipFile(tmp, "w") as z:
                    for p in sorted(members): z.writestr(p, members[p])
                key = hashlib.sha256(json.dumps({p: hashlib.sha256(v).hexdigest() for p, v in members.items()}, sort_keys=True).encode()).hexdigest()
            tmp.flush()
            ref = f"vol://sources/{source}/{key[:32]}"
            existed = os.path.isdir(storage.resolve(ref))
            try: man = files.safe_extract(tmp.name, ref, strip_root=members is None)
            except files.InputError as e: return out({"outcome": "INPUT_INVALID", "error_code": f"core.{e.code}"})
        mh = hashlib.sha256(json.dumps(man, sort_keys=True).encode()).hexdigest()
        rv = call("core.revision_create", cl["sub"], source, key, man, mh, ref, True, rid(b))
        if rv["outcome"] not in ("CREATED", "EXISTS"):
            if not existed: storage.remove_tree(ref)
            return out(rv)
        res = {**rv, "revision_id": rv["id"], "files": len(man["files"])}
        if b.get("plugin"):
            res["job"] = call("runtime.create_job", cl["sub"], si["project_id"], b["plugin"], b.get("pipeline", "index"), rv["id"],
                              str(b.get("force", "")).lower() in ("1", "true"), str(uuid.uuid5(uuid.NAMESPACE_URL, rid(b) + ":job")))
        return out(res)

    @r.post("/jobs")
    def job_create(b=Depends(body), cl=Depends(claims)):
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
    def job_cancel(job: str, b=Depends(body), cl=Depends(claims)):
        return out(call("runtime.cancel_job", cl["sub"], job, rid(b)))

    @r.post("/jobs/{job}/retry")
    def job_retry(job: str, b=Depends(body), cl=Depends(claims)):
        return out(call("runtime.retry_job", cl["sub"], job, rid(b)))

    @r.post("/jobs/{job}/approve")
    def job_approve(job: str, b=Depends(body), cl=Depends(claims)):
        """U4 {stage, artifact_sha256?, decision: approve|reject}; approval binds the current artifact hash (R15)."""
        return out(call("runtime.approve", cl["sub"], job, b.get("stage"), b.get("artifact_sha256"), b.get("decision", "approve"), rid(b)))

    @r.post("/tenants/{tenant}/secrets")
    def secret_set(tenant: str, b=Depends(body), cl=Depends(claims)):
        """P-P12 {connector: <plugin>.<connector>, name?, value}: stored encrypted with the host key; never returned."""
        if not isinstance(b.get("value"), str) or not b["value"]: return out({"outcome": "INPUT_INVALID"})
        kid, ct = secrets.encrypt(b["value"])
        return out(call("core.connector_secret_set", cl["sub"], tenant, b.get("connector"), b.get("name") or "default", kid, ct, rid(b)))

    @r.get("/status")
    def status(cl=Depends(claims)): return out(call("core.status", cl["sub"]))

    @r.post("/search")
    def search(b=Depends(body), cl=Depends(claims)):
        """{projects[], query, k?, collections?, filters?, pins?, include_content?}; query vectors from loaded plugins."""
        q = b.get("query") or ""
        qv = {}
        for m in manifests.values():
            fn = getattr(importlib.import_module(m["entry"]), "query_vectors", None)
            if fn and q: qv.update(fn(q))
        with settings.conn("api") as c:
            c.autocommit = False
            c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            res = c.execute("SELECT knowledge.search(%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                            (cl["sub"], b.get("projects") or [], b.get("collections"), q, json.dumps(qv),
                             json.dumps(b.get("filters") or {}), b.get("k", 10), json.dumps(b["pins"]) if b.get("pins") else None,
                             bool(b.get("include_content")))).fetchone()[0]
            c.rollback()
        return out(res)

    return r
