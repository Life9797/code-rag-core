"""Worker Protocol v1 client (§6.2, D-15). Binding 1 = in-process SQL (SqlBinding); binding 2 = HTTP with the
same messages (coderag_core.http_binding). Handlers get a TaskCtx and never touch runtime tables."""
import importlib, json, uuid


class PluginError(Exception):
    """Raise from a handler with a registered error code (<plugin>.<key> or core.*)."""
    def __init__(self, code, detail=None):
        super().__init__(code); self.code, self.detail = code, detail or {}


# Stage writes (W6, G3): op -> (schema, argument names after the task's lease triple). Shared by both bindings.
STAGE_OPS = {
    "put_unit": ("knowledge", ("kind", "key", "content_hash", "attrs", "chunks", "on_behalf_of")),
    "put_representation": ("knowledge", ("rep_kind", "config_hash", "input_hash", "unit_version_id", "chunk_id", "vec", "body")),
    "mark_units": ("knowledge", ("mode", "kind", "keys", "reason")),
    "put_job_item": ("runtime", ("key", "status", "code", "detail")),
}
JSON_ARGS = {"attrs", "chunks", "detail", "result", "children"}


def sql_args(names, values):
    return [json.dumps(v) if k in JSON_ARGS and v is not None else v for k, v in zip(names, values)]


class SqlBinding:
    def __init__(self, conn): self.conn = conn          # the plugin's own login (plugin_<id>), autocommit
    def _call(self, fn, *args):
        ph = ", ".join(["%s"] * len(args))
        return self.conn.execute(f"SELECT runtime.{fn}({ph})", [json.dumps(a) if isinstance(a, dict) or (isinstance(a, list) and a and isinstance(a[0], dict)) else a for a in args]).fetchone()[0]
    def claim(self, max, versions, rid): return self._call("claim", max, versions, rid)
    def heartbeat(self, t, checkpoint=None, extend=False): return self._call("heartbeat", t["task_id"], t["lease_token"], t["lease_epoch"], checkpoint, extend)
    def complete(self, t, result, rid): return self._call("complete", t["task_id"], t["lease_token"], t["lease_epoch"], result, rid)
    def fail(self, t, code, detail, rid): return self._call("fail", t["task_id"], t["lease_token"], t["lease_epoch"], code, detail, rid)
    def split(self, t, children, rid): return self._call("split", t["task_id"], t["lease_token"], t["lease_epoch"], children, rid)
    def stage(self, op, t, **kw):
        schema, names = STAGE_OPS[op]
        args = [t["task_id"], t["lease_token"], t["lease_epoch"]] + sql_args(names, [kw.get(n) for n in names])
        ph = ", ".join(["%s"] * len(args))
        return self.conn.execute(f"SELECT {schema}.{op}({ph})", args).fetchone()[0]


class TaskCtx:
    def __init__(self, binding, task, db):
        self.b, self.task, self.db, self.split_result = binding, task, db, None
        self.policy = task["policy"]
    def split(self, children):
        self.split_result = self.b.split(self.task, children, str(uuid.uuid4()))
        return self.split_result
    def heartbeat(self, checkpoint=None, extend=False):
        return self.b.heartbeat(self.task, checkpoint, extend)
    def put_unit(self, kind, key, content_hash, attrs=None, chunks=None, on_behalf_of=None):
        return self.b.stage("put_unit", self.task, kind=kind, key=key, content_hash=content_hash, attrs=attrs or {},
                            chunks=chunks or [], on_behalf_of=on_behalf_of)
    def put_representation(self, rep_kind, config_hash, input_hash, unit_version_id=None, chunk_id=None, vec=None, body=None):
        return self.b.stage("put_representation", self.task, rep_kind=rep_kind, config_hash=config_hash, input_hash=input_hash,
                            unit_version_id=unit_version_id, chunk_id=chunk_id,
                            vec=None if vec is None else "[" + ",".join(map(str, vec)) + "]", body=body)
    def touch(self, kind, keys): return self.b.stage("mark_units", self.task, mode="touch", kind=kind, keys=list(keys))
    def hold(self, kind, keys, reason): return self.b.stage("mark_units", self.task, mode="hold", kind=kind, keys=list(keys), reason=reason)
    def put_job_item(self, key, status, code=None, detail=None):
        return self.b.stage("put_job_item", self.task, key=key, status=status, code=code, detail=detail or {})


class Worker:
    def __init__(self, plugin_id, version, handlers, binding, db=None):
        self.plugin, self.version, self.handlers, self.b, self.db = plugin_id, version, handlers, binding, db

    @classmethod
    def load(cls, manifest, binding, db=None):
        mod = importlib.import_module(manifest["entry"])
        return cls(manifest["id"], manifest["version"], mod.HANDLERS, binding, db)

    def run_once(self, max=8):
        r = self.b.claim(max, [self.version], str(uuid.uuid4()))
        for t in r.get("tasks", []):
            self.run_task(t)
        return len(r.get("tasks", []))

    def run_task(self, t):
        ctx = TaskCtx(self.b, t, self.db)
        try:
            out = self.handlers[t["handler"].split(".", 1)[1]](ctx, t)
            if ctx.split_result is None:
                return self.b.complete(t, out or {}, str(uuid.uuid4()))
            return ctx.split_result
        except PluginError as e:
            return self.b.fail(t, e.code, e.detail, str(uuid.uuid4()))
        except Exception as e:                                     # crash: retryable core code, no text leak
            return self.b.fail(t, "core.handler_crashed", {"type": type(e).__name__}, str(uuid.uuid4()))

    def drain(self, max_rounds=100):
        n = 0
        for _ in range(max_rounds):
            k = self.run_once()
            if not k: break
            n += k
        return n


class HttpBinding:
    """Binding 2: the same messages over HTTP (/runtime/v1/*). post(path, json) -> dict; default uses urllib."""
    def __init__(self, base_url=None, token=None, post=None):
        self.post = post or self._urllib(base_url, token)
    @staticmethod
    def _urllib(base, token):
        import urllib.request
        def post(path, body):
            rq = urllib.request.Request(base + path, data=json.dumps(body).encode(), method="POST",
                                        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"})
            with urllib.request.urlopen(rq) as resp: return json.loads(resp.read())
        return post
    def _t(self, t): return {"task_id": t["task_id"], "lease_token": t["lease_token"], "lease_epoch": t["lease_epoch"]}
    def claim(self, max, versions, rid): return self.post("/runtime/v1/claim", {"max": max, "versions": versions, "request_id": rid})
    def heartbeat(self, t, checkpoint=None, extend=False):
        return self.post("/runtime/v1/heartbeat", {**self._t(t), "checkpoint": checkpoint, "extend": extend})
    def complete(self, t, result, rid): return self.post("/runtime/v1/complete", {**self._t(t), "result": result, "request_id": rid})
    def fail(self, t, code, detail, rid):
        return self.post("/runtime/v1/fail", {**self._t(t), "error_code": code, "detail": detail, "request_id": rid})
    def split(self, t, children, rid): return self.post("/runtime/v1/split", {**self._t(t), "children": children, "request_id": rid})
    def stage(self, op, t, **kw): return self.post(f"/runtime/v1/stage/{op}", {**self._t(t), **kw})
