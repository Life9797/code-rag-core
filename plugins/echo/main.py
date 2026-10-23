"""echo plugin handlers (§32): fan splits to depth 2 with N = echo.fanout children; leaves record a call."""
from coderag_core.worker import PluginError


def fan(ctx, task):
    n = int(ctx.policy.get("echo.fanout") or 2)
    if task["payload"].get("boom"):
        raise PluginError("echo.boom", {"at": task["child_key"]})
    if task["depth"] < 2:
        ctx.split([{"key": f"{task['child_key'] or 'r'}.{i}", "payload": {"i": i}} for i in range(n)])
        return None
    if ctx.db is not None:
        ctx.db.execute("INSERT INTO plugin_echo.calls (task_id) VALUES (%s)", (task["task_id"],))
    return {"echo": task["child_key"]}


def purge(ctx, task):
    return {}


HANDLERS = {"fan": fan, "purge": purge}
