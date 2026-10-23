"""Core loops (§28.2): the runtime loop (start jobs, Core publish stage, finish non-publishing jobs) and the
maintenance loop (sweep, breaker, indexes, GC, snapshot retention, project purge with plugin hooks). Each call
uses its own login: runtime, maint, purge, and plugin_<id> for purge hooks (I-S1, I-P2)."""
import importlib, logging, time
from . import storage

log = logging.getLogger("coderag.runner")


def q(conn, sql, *args):
    return conn.execute(sql, args).fetchone()[0]


def runtime_tick(connect):
    with connect("runtime") as c:
        started = q(c, "SELECT runtime.start_jobs(50)")
        published = {str(j): q(c, "SELECT knowledge.publish(%s)", j) for j in q(c, "SELECT knowledge.publish_ready(20)")}
        done = q(c, "SELECT runtime.finish_done_jobs()")
    return {"started": started, "published": published, "done": done}


def maint_tick(connect, manifests=None, vol_root=None):
    out = {}
    with connect("maint") as c:
        out["sweep"] = q(c, "SELECT runtime.sweep(500)")
        out["breaker"] = q(c, "SELECT runtime.breaker_eval()")
        out["indexes"] = q(c, "SELECT knowledge.ensure_indexes()")
        out["gc"] = q(c, "SELECT knowledge.gc_all()")
        pruned = q(c, "SELECT core.snapshot_retention(1000)")["pruned"]
    for ref in pruned:
        if ref.startswith("vol://"): storage.remove(ref, vol_root)
    out["purge"] = purge(connect, manifests or {})
    return out


def purge(connect, manifests):
    """G2: step each due project; RUN_HOOKS -> call <plugin>.purge_project(db, project, info) with the plugin login."""
    done = {}
    with connect("purge") as c:
        for p in q(c, "SELECT core.purge_due()"):
            for _ in range(10):
                r = q(c, "SELECT core.purge_step(%s)", p)
                missing = [x for x in r.get("plugins", []) if x not in manifests]
                if r["outcome"] != "RUN_HOOKS" or missing:         # a plugin not loaded here: wait for a host that has it
                    if missing: log.warning("purge %s waits for plugins %s", p, missing)
                    break
                for plug in r["plugins"]:
                    m = manifests[plug]
                    hook = importlib.import_module(m["entry"]).HANDLERS[m["hooks"]["purge_project"]]
                    with connect(f"plugin_{plug}") as pc:
                        hook(pc, str(p), {"collections": r["collections"].get(plug, []), "jobs": r["jobs"].get(plug, [])})
                    q(c, "SELECT core.purge_hook_done(%s, %s)", p, plug)
            done[str(p)] = r["outcome"]
    return done


def loop(connect, manifests, interval=2.0):
    while True:
        try:
            runtime_tick(connect); maint_tick(connect, manifests)
        except Exception:
            log.exception("tick failed")
        time.sleep(interval)
