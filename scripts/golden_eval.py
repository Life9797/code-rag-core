"""M3 golden evaluation as a script (§35: the eval plugin comes in M4). Indexes the golden repos with the code plugin
in-process, then runs every query per language through knowledge.search (k=10) as the project admin.
Usage: python3 -m scripts.golden_eval --dsn "host=... user=postgres dbname=crc_eval" --golden DIR --vol DIR [--repeats 3]
Embedder/LLM come from the environment (CODERAG_EMBED_URL, CODERAG_LLM_URL); unset = stubs."""
import argparse, glob, hashlib, json, os, time, uuid
import psycopg
from coderag_core import coreadm, files, initdb, runner, storage
from coderag_core.worker import Worker, SqlBinding
from plugins.code import main as code
from scripts.evalkit import golden, metrics

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main():
    a = argparse.ArgumentParser()
    a.add_argument("--dsn", required=True); a.add_argument("--golden", required=True); a.add_argument("--vol", required=True)
    a.add_argument("--repeats", type=int, default=1); a.add_argument("--k", type=int, default=10)
    o = a.parse_args()
    storage.ROOT = o.vol
    initdb.init(o.dsn, os.path.join(ROOT, "plugins"), log=lambda *_: None)
    base = " ".join(p for p in o.dsn.split() if not p.startswith("user="))
    cn = lambda login, ac=True: psycopg.connect(f"{base} user={login}", autocommit=ac)
    def api(fn, *args):
        with cn("api") as c: return c.execute(f"SELECT {fn}({', '.join(['%s'] * len(args))})", args).fetchone()[0]
    with cn("postgres") as su: admin = coreadm.bootstrap(su, f"eval-{uuid.uuid4().hex[:6]}")
    rid = lambda: uuid.uuid4()
    t = api("core.tenant_create", admin, f"eval-{uuid.uuid4().hex[:6]}", rid())["id"]
    admin = api("core.principal_create", admin, t, "service", "eval", 4, None, None, rid())["id"]
    p = api("core.project_create", admin, t, "golden", rid())["id"]
    api("core.plugin_enable", admin, p, "code", True, rid())
    t0 = time.time()
    for z in sorted(glob.glob(os.path.join(o.golden, "*.zip"))):
        s = api("core.source_create", admin, p, "core.upload", os.path.basename(z)[:60], False, rid())["id"]
        ref = f"vol://sources/{s}/r1"
        man = files.safe_extract(z, ref)
        mh = hashlib.sha256(json.dumps(man, sort_keys=True).encode()).hexdigest()
        rev = api("core.revision_create", admin, s, mh[:16], json.dumps(man), mh, ref, True, rid())["id"]
        j = api("runtime.create_job", admin, p, "code", "index", rev, False, rid())["job_id"]
        with cn("plugin_code") as c:
            w = Worker("code", "0.1.0", code.HANDLERS, SqlBinding(c), db=c)
            while api("runtime.get_job", admin, j)["status"] not in ("succeeded", "failed", "canceled"):
                runner.runtime_tick(cn); w.drain()
        print(os.path.basename(z), api("runtime.get_job", admin, j)["outcome"])
    with cn("maint") as c: c.execute("SELECT knowledge.ensure_indexes()")
    print(f"indexed in {time.time() - t0:.1f} s")
    repos = golden.load_repos(o.golden)
    qs, problems = golden.load_queries(o.golden, repos)
    for x in problems: print("golden problem:", x)
    runs = []
    for rep in range(o.repeats):
        rows = {}
        for q in qs:
            for lang, text in q.text.items():
                t1 = time.perf_counter()
                with cn("api", False) as c:
                    c.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                    r = c.execute("SELECT knowledge.search(%s, %s, NULL, %s, %s, NULL, %s)",
                                  (admin, [p], text, json.dumps(code.query_vectors(text)), o.k)).fetchone()[0]
                ms = (time.perf_counter() - t1) * 1000
                hits = []
                for x in r.get("hits", []):
                    at, sp = x["attrs"], (x.get("chunk") or {}).get("span") or {}
                    mod = code.py.module_name(at.get("path", ""))
                    sym = at.get("symbol", "")
                    sym = sym[len(mod) + 1:] if sym.startswith(mod + ".") else ("" if sym == mod else sym)
                    hits.append(metrics.Hit(at.get("path", ""), sp.get("start_line", 0), sp.get("end_line", 0), sym))
                rows.setdefault(lang, []).append({**metrics.score_query(hits, q.expected, o.k), "latency_ms": ms})
        out = {lang: metrics.summarize(v) for lang, v in rows.items()}
        out["all"] = metrics.summarize([r for v in rows.values() for r in v])
        runs.append(out); print(f"repeat {rep + 1}:", json.dumps(out))
    return runs


if __name__ == "__main__":
    main()
