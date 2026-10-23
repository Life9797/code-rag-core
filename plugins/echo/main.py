"""echo plugin handlers (§32): fan splits to depth 2 with N = echo.fanout children; each leaf stages one unit
with a deterministic 8-d vector. Payload test switches: boom (fail), dims (wrong vector size), no_vec (skip vector)."""
import hashlib
from coderag_core.worker import PluginError

CONFIG = "echo-v1"


def vec(text, dims=8):
    h = hashlib.sha256(text.encode()).digest()
    return [round((b - 128) / 128, 4) for b in h[:dims]]


def fan(ctx, task):
    n = int(ctx.policy.get("echo.fanout") or 2)
    p = task["payload"]
    if p.get("boom"):
        raise PluginError("echo.boom", {"at": task["child_key"]})
    if task["depth"] < 2:
        ctx.split([{"key": f"{task['child_key'] or 'r'}.{i}", "payload": {k: v for k, v in p.items() if k != "i"} | {"i": i}}
                   for i in range(n)])
        return None
    key = task["child_key"]
    text = f"echo item {key} " + p.get("text", "")
    r = ctx.put_unit("echo.item", key, hashlib.sha256(text.encode()).hexdigest(), {"key": key}, [{"ordinal": 0, "text": text}])
    if r["outcome"] == "STAGED" and not p.get("no_vec"):
        v = ctx.put_representation("echo.vec", CONFIG, hashlib.sha256(text.encode()).hexdigest(),
                                   unit_version_id=r["unit_version_id"], vec=vec(text, p.get("dims", 8)))
        if v["outcome"] != "OK":
            raise PluginError("echo.boom", v)
    if ctx.db is not None:
        ctx.db.execute("INSERT INTO plugin_echo.calls (task_id) VALUES (%s)", (task["task_id"],))
    return {"echo": key, "unit": r["outcome"]}


def purge(ctx, task):
    return {}


HANDLERS = {"fan": fan, "purge": purge}
