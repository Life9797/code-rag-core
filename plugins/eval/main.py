"""eval plugin (L-18, §35): run a golden set (revision with *.jsonl queries + *.zip sources) against the job's project.
query (item, split by 20 queries) -> score (control: per-language recall@k, hit@k, MRR, p50/p95, baseline compare).
Matching and metrics are copied from code-rag v0.2.4 eval/evaluator (golden.py, metrics.py; O-16)."""
import json, time
from coderag_core import storage
from coderag_core.worker import PluginError
from . import golden, metrics

BASELINE = {"name": "baseline-v0.2.4", "en": {"recall_at_k": 0.939}, "ko": {"recall_at_k": 0.923},
            "all": {"recall_at_k": 0.931, "hit_at_k": 0.961, "mrr": 0.756, "p95_ms": 66}}   # Phase 3 (memory 2026-10-16)
PER_TASK = 20


def pol(ctx, key, default):
    v = ctx.policy.get("eval." + key)
    return default if v is None else type(default)(v)


def query_vectors(text):
    try: from plugins.code.main import query_vectors as qv     # the code plugin's vector kinds (in-process, trusted)
    except ImportError: return {}
    return qv(text)


def hits_from(res):
    """search hits -> metrics.Hit (path, best chunk lines, symbol relative to its module)."""
    out = []
    for x in res.get("hits", []):
        a, sp = x["attrs"], (x.get("chunk") or {}).get("span") or {}
        path, sym = a.get("path", ""), a.get("symbol", "")
        mod = path.rsplit(".", 1)[0].replace("/", ".")
        sym = sym[len(mod) + 1:] if sym.startswith(mod + ".") else ("" if sym == mod else sym)
        out.append(metrics.Hit(path, sp.get("start_line", 0), sp.get("end_line", 0), sym))
    return out


def load(ctx):
    inp = ctx.task_input()
    d = storage.resolve(inp["storage_ref"])
    qs, problems = golden.load_queries(d, golden.load_repos(d))
    if not qs: raise PluginError("eval.golden_invalid", {"problems": problems[:20]})
    return inp, qs


def query(ctx, t):
    inp, qs = load(ctx)
    if t["depth"] == 0:
        ctx.db.execute("INSERT INTO plugin_eval.runs (job_id, project_id, golden_sha) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                       (t["job_id"], t["project_id"], inp["revision_key"]))
        if len(qs) > PER_TASK:
            ctx.split([{"key": f"q{i // PER_TASK:04d}", "payload": {"ids": [q.id for q in qs[i:i + PER_TASK]]}}
                       for i in range(0, len(qs), PER_TASK)])
            return None
    ids, k, n = set(t["payload"].get("ids") or [q.id for q in qs]), pol(ctx, "k", 10), 0
    for q in qs:
        if q.id not in ids: continue
        for lang, text in q.text.items():
            t0 = time.perf_counter()
            r = ctx.search(text, query_vectors(text), {}, k)
            ms = (time.perf_counter() - t0) * 1000
            if r["outcome"] == "FORBIDDEN": raise PluginError("eval.forbidden", {"query": q.id})
            if r["outcome"] != "OK": raise RuntimeError(r["outcome"])
            hs = hits_from(r)
            sc = metrics.score_query(hs, q.expected, k)
            ctx.db.execute("""INSERT INTO plugin_eval.results (job_id, query_id, lang, hits, recall, hit, rr, latency_ms)
                              VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (job_id, query_id, lang) DO UPDATE
                              SET hits = EXCLUDED.hits, recall = EXCLUDED.recall, hit = EXCLUDED.hit, rr = EXCLUDED.rr,
                                  latency_ms = EXCLUDED.latency_ms""",
                           (t["job_id"], q.id, lang, json.dumps([h.__dict__ for h in hs]), sc["recall"], sc["hit"], sc["rr"], ms))
            n += 1
    return {"searches": n}


def compare(summary, base, max_drop=0.02, max_p95=150.0):
    """§35 pass rule per language: recall@k >= baseline - max_drop and p95 <= max_p95 ms."""
    flags = [f"recall_drop:{l}" for l in ("en", "ko") if l in summary and l in base
             and base[l]["recall_at_k"] - (summary[l]["recall_at_k"] or 0) > max_drop + 1e-9]
    flags += [f"p95:{l}" for l, s in summary.items() if s.get("p95_ms") is not None and s["p95_ms"] > max_p95]
    return {"baseline": base["name"], "verdict": "fail" if flags else "pass", "flags": flags}


def score(ctx, t):
    rows = ctx.db.execute("SELECT lang, recall, hit, rr, latency_ms FROM plugin_eval.results WHERE job_id = %s", (t["job_id"],)).fetchall()
    by = {}
    for lang, rc, hit, rr, ms in rows:
        by.setdefault(lang, []).append({"recall": float(rc), "hit": float(hit), "rr": float(rr), "latency_ms": float(ms)})
    summary = {l: metrics.summarize(v) for l, v in by.items()}
    summary["all"] = metrics.summarize([r for v in by.values() for r in v])
    report = {"summary": summary, "compare": compare(summary, BASELINE, pol(ctx, "max_recall_drop", 0.02), pol(ctx, "max_p95_ms", 150.0))}
    ctx.db.execute("UPDATE plugin_eval.runs SET summary = %s, finished_at = now() WHERE job_id = %s", (json.dumps(report), t["job_id"]))
    ctx.put_job_item("report", "ok", None, report)
    return report


def purge(db, project_id, info):
    db.execute("DELETE FROM plugin_eval.runs WHERE project_id = %s", (project_id,))
    return {}


HANDLERS = {"query": query, "score": score, "purge": purge}
