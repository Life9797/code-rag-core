"""code plugin handlers (§31, §39). Stages: parse (inventory + per-file parse, cached per tenant), resolve (graph over
the whole job, quality counts, G8 gate), assemble (summaries + units + chunks + embeddings), then Core publish.
Inventory rules, stub knowledge and the knowledge prompt are ported from code-rag v0.2.4 worker/coderag/ingest.py and
providers.py (O-16). Handlers use only TaskCtx stage writes and their own schema plugin_code."""
import hashlib, json, os, re
from concurrent.futures import ThreadPoolExecutor
from coderag_core import files, providers
from coderag_core.worker import PluginError
from . import parse_python as py, resolver as rs

DIMS = 1024
ENCODINGS = ("utf-8", "utf-16", "cp949")                  # §39 code.encodings (UTF-16 only with a BOM)
LANGUAGES = {".py": ("python", py)}                        # §39 language handlers; ids/versions are in PARSER_KEY
PARSER_KEY = f"py:{py.PARSER_NAME}@{py.PARSER_VERSION}:{py.NORMALIZATION}:enc={','.join(ENCODINGS)}"
IGNORED_DIRS = {".git", ".hg", ".svn", "__pycache__", ".mypy_cache", ".pytest_cache", ".tox", ".venv", "venv",
                "node_modules", ".idea", ".vscode", "dist", "build", ".eggs"}
VENDORED_DIRS = {"vendor", "third_party", "site-packages"}
TIER2 = {"global_name_unique"}                             # not used for graph context (Phase 3 outgoing_exact)
PROMPT, CB = "kp2", "cb1"
KS_PROMPT = """You document source code for a code search index. Reply with one JSON object only:
{"summary": "<one or two English sentences: what the code does and why>",
 "summary_ko": "<the same summary in Korean>",
 "keywords": ["<up to 12 search terms: identifiers, concepts>"],
 "side_effects": ["<I/O, network, state mutation, or nothing>"]}"""
STUB_KNOWLEDGE = "stub-knowledge-v1"
_models = {}                                               # tests may set "embed" / "chat"


def embedder():
    if "embed" not in _models: _models["embed"] = providers.make_embedder(DIMS)
    return _models["embed"]


def chat():
    if "chat" not in _models: _models["chat"] = providers.make_chat()
    return _models["chat"]


def h(*parts):
    return hashlib.sha256(json.dumps(parts, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def pol(ctx, key, default):
    v = ctx.policy.get("code." + key)
    if v is None: return default
    if isinstance(default, bool): return v in (True, "true", "1", 1)
    return type(default)(v)


def llm_on(ctx):
    return pol(ctx, "llm.enabled", chat() is not None) and chat() is not None


def configs(llm=True):
    """config_hash per representation kind; search passes the same values for its query vectors."""
    e = embedder().identity
    k = chat().identity if llm and chat() is not None else STUB_KNOWLEDGE
    return {"code.embed": h("embed", "1", e, DIMS)[:24], "code.knowledge_embed": h("kembed", "1", e, DIMS)[:24],
            "code.summary": h("summary", PROMPT, CB, k)[:24]}


def classify(path, size, max_bytes):
    """Path rules (v0.2.4 ingest._classify); None = eligible. Content rules run at parse."""
    dirs = path.split("/")[:-1]
    if any(d in IGNORED_DIRS or d.endswith(".egg-info") for d in dirs): return "ignored_pattern"
    if any(d in VENDORED_DIRS for d in dirs): return "vendored"
    if os.path.splitext(path)[1].lower() not in LANGUAGES: return "unsupported_language"
    if size > max_bytes: return "too_large"
    return None


def eligible(ctx, manifest):
    mx = pol(ctx, "max_file_bytes", 1 << 20)
    return [f for f in manifest["files"] if classify(f["path"], f["size"], mx) is None]


def batches(ctx, items):
    n = pol(ctx, "batch_files", 50)
    return [{"key": f"b{i // n:05d}", "payload": {"files": items[i:i + n]}} for i in range(0, len(items), n)]


def decode(data):
    for enc in ENCODINGS:
        if enc == "utf-16" and data[:2] not in (b"\xff\xfe", b"\xfe\xff"): continue
        try: return data.decode(enc), enc
        except UnicodeDecodeError: pass
    return None, None


def cached(db, tenant, shas):
    rows = db.execute("UPDATE plugin_code.parse_cache SET last_used_at = now() WHERE tenant_id = %s AND parser_version = %s "
                      "AND content_sha256 = ANY (%s) RETURNING content_sha256, result", (tenant, PARSER_KEY, list(shas))).fetchall()
    return dict(rows)


# ---------- parse (item) ----------
def parse(ctx, t):
    inp = ctx.task_input()
    if t["depth"] == 0:
        mx, skipped = pol(ctx, "max_file_bytes", 1 << 20), {}
        for f in inp["manifest"]["files"]:
            r = classify(f["path"], f["size"], mx)
            if r == "too_large": ctx.put_job_item(f["path"], "excluded", "code.too_large", {"size": f["size"]})
            elif r: skipped[r] = skipped.get(r, 0) + 1
        if skipped: ctx.put_job_item(".skipped", "ok", None, skipped)   # G3: non-code files counted, not G6 exclusions
        todo = eligible(ctx, inp["manifest"])
        if len(todo) > pol(ctx, "batch_files", 50):
            ctx.split(batches(ctx, todo)); return None
    else:
        todo = t["payload"]["files"]
    db, cap, out = ctx.db, pol(ctx, "raw_fact_cap", 5000), {}
    have = cached(db, t["tenant_id"], {f["sha256"] for f in todo})
    for f in todo:
        res = have.get(f["sha256"])
        if res is None:
            data = files.read(inp["storage_ref"], f["path"])
            text, enc = decode(data) if b"\0" not in data[:8192] else (None, "binary")
            if enc == "binary": res = {"status": "excluded", "reason": "binary"}
            elif text is None: res = {"status": "excluded", "reason": "encoding_unsupported"}
            elif not text.strip(): res = {"status": "excluded", "reason": "empty_file"}
            else: res = {"status": "parsed", "encoding": enc, **py.parse_file(f["path"], text, cap)}
            db.execute("INSERT INTO plugin_code.parse_cache (tenant_id, content_sha256, parser_version, result) VALUES (%s, %s, %s, %s) "
                       "ON CONFLICT DO NOTHING", (t["tenant_id"], f["sha256"], PARSER_KEY, json.dumps(res)))
        if res["status"] != "parsed":
            ctx.put_job_item(f["path"], "excluded", "code." + res["reason"]); continue
        rows = [(t["job_id"], f["path"], d["ordinal"], "dep", json.dumps(d)) for d in res["dependencies"]] + \
               [(t["job_id"], f["path"], c["ordinal"], "call", json.dumps(c)) for c in res["calls"]]
        with db.transaction():
            db.execute("DELETE FROM plugin_code.raw_facts WHERE job_id = %s AND file_key = %s", (t["job_id"], f["path"]))
            if rows:
                db.cursor().executemany("INSERT INTO plugin_code.raw_facts (job_id, file_key, ordinal, kind, fact) VALUES (%s, %s, %s, %s, %s)", rows)
        out[f["path"]] = len(rows)
    return {"files": len(todo), "facts": sum(out.values())}


# ---------- resolve (control) ----------
GROUPS = {"unsupported_resolution": "unsupported", "reexport_chain_unsupported": "unsupported", "dynamic_target": "inherent",
          "dynamic_dispatch": "inherent", "receiver_type_unknown": "inherent", "target_file_parse_failed": "upstream",
          "target_file_excluded": "upstream", "target_language_unsupported": "upstream", "invalid_reference": "upstream"}


def resolve(ctx, t):
    db, job = ctx.db, t["job_id"]
    inp = ctx.task_input()
    mx = pol(ctx, "max_file_bytes", 1 << 20)
    todo = eligible(ctx, inp["manifest"])
    res = cached(db, t["tenant_id"], {f["sha256"] for f in todo})
    inventory = {f["path"]: classify(f["path"], f["size"], mx) or "eligible" for f in inp["manifest"]["files"]}
    infos = {}
    for f in todo:
        r = res.get(f["sha256"])
        if r is None: raise PluginError("code.parse_missing", {"path": f["path"]})
        if r["status"] != "parsed":
            inventory[f["path"]] = r["reason"]; continue
        fi = rs.FileInfo(f["path"], f["path"], "parsed", r.get("graph_excluded_reason"))
        for s in r["symbols"]:
            fi.symbols[s["ordinal"]] = rs.Sym(f"{f['path']}#{s['ordinal']}", f["path"], s["ordinal"], s.get("parent_ordinal"),
                                              s["kind"], s["name"], s["qualified_name"])
        infos[f["path"]] = fi
    ix = rs.Index(list(infos.values()), inventory)
    facts = {}
    for fk, kind, o, fact in db.execute("SELECT file_key, kind, ordinal, fact FROM plugin_code.raw_facts WHERE job_id = %s "
                                        "ORDER BY file_key, kind, ordinal", (job,)):
        facts.setdefault((fk, kind), []).append(fact)
    upd, counts = [], {}
    for path, fi in infos.items():
        deps, calls = facts.get((path, "dep"), []), facts.get((path, "call"), [])
        if fi.graph_excluded:
            for kind, n in (("dependency", len(deps)), ("call", len(calls))):
                c = counts.setdefault(kind, dict.fromkeys(("raw_total", "unsupported", "inherent", "upstream", "resolver_miss", "resolved", "external"), 0))
                c["raw_total"] += n; c["unsupported"] += n
            continue
        dres = [rs.resolve_dependency(ix, fi, d) for d in deps]
        binds = rs.bindings(ix, fi, deps, dres)
        cres = [rs.resolve_call(ix, fi, binds, {**c, "caller_symbol_ordinal": c.get("caller_symbol_ordinal") or 0}) for c in calls]
        for kind, fk, items, results in (("dependency", "dep", deps, dres), ("call", "call", calls, cres)):
            c = counts.setdefault(kind, dict.fromkeys(("raw_total", "unsupported", "inherent", "upstream", "resolver_miss", "resolved", "external"), 0))
            for f, r in zip(items, results):
                c["raw_total"] += 1
                c[r["status"] if r["status"] in ("resolved", "external") else GROUPS.get(r.get("reason"), "resolver_miss")] += 1
                out = {"status": r["status"], "method": r.get("method"), "reason": r.get("reason"), "resolver_version": rs.RESOLVER_VERSION}
                tgt = r.get("target_file_id") or r.get("callee_symbol_id")
                if tgt:
                    p, _, o = tgt.partition("#")
                    out["target_path"] = p
                    if o: out["target_key"] = infos[p].symbols[int(o)].qualified_name
                if r.get("target_external") or r.get("callee_external"): out["external"] = r.get("target_external") or r.get("callee_external")
                upd.append({"f": path, "k": fk, "o": f["ordinal"], "r": out})
    src = inp["source_id"]
    with db.transaction():
        db.execute("UPDATE plugin_code.raw_facts r SET resolution = x.r FROM jsonb_to_recordset(%s) AS x(f text, k text, o int, r jsonb) "
                   "WHERE r.job_id = %s AND r.file_key = x.f AND r.kind = x.k AND r.ordinal = x.o", (json.dumps(upd), job))
        db.execute("DELETE FROM plugin_code.graph_quality WHERE job_id = %s", (job,))
        for kind, c in counts.items():
            db.execute("INSERT INTO plugin_code.graph_quality (job_id, collection_id, source_id, kind, language, raw_total, unsupported, "
                       "inherent, upstream, resolver_miss, resolved, external, resolver_version) VALUES (%s,%s,%s,%s,'python',%s,%s,%s,%s,%s,%s,%s,%s)",
                       (job, t["collection_id"], src, kind, *c.values(), rs.RESOLVER_VERSION))
    return {"quality": counts, "gate": gate(ctx, t, src)}


def gate(ctx, t, src):
    """G8: compare (resolved + external) / attemptable with the previous job of this (collection, source)."""
    rows = ctx.db.execute("""SELECT g.kind, (g.resolved + g.external)::numeric / nullif(g.attemptable, 0),
                (SELECT (b.resolved + b.external)::numeric / nullif(b.attemptable, 0) FROM plugin_code.graph_quality b
                 WHERE b.collection_id = g.collection_id AND b.source_id IS NOT DISTINCT FROM g.source_id AND b.kind = g.kind
                   AND b.language = g.language AND b.job_id < g.job_id ORDER BY b.job_id DESC LIMIT 1)
            FROM plugin_code.graph_quality g WHERE g.job_id = %s""", (t["job_id"],)).fetchall()
    drop = pol(ctx, "graph_gate_drop", 0.05)
    bad = {k: [float(r), float(b)] for k, r, b in rows if r is not None and b is not None and b - r > drop}
    if bad and pol(ctx, "graph_gate", "warn") == "fail":
        raise PluginError("code.graph_quality_blocked", bad)
    return {"verdict": "warn" if bad else "pass", "drops": bad}


# ---------- assemble (item) ----------
def stub_summary(c):
    """v0.2.4 StubKnowledge: docstring or first line, names, graph context."""
    doc = re.search(r'"""(.*?)"""', c["text"], re.S) or re.search(r"'''(.*?)'''", c["text"], re.S)
    first = c["text"].strip().split("\n")[0][:120]
    summary = doc.group(1).strip().split("\n")[0] if doc and doc.group(1).strip() else f"{c['kind']} {first}"
    if c["ctx"]["callees"]: summary += f" Calls {', '.join(c['ctx']['callees'][:5])}."
    return {"summary": summary, "summary_ko": f"{c['path']}의 {c['kind']}: {summary}",
            "keywords": sorted({x for x in providers.tokens(c["text"]) if len(x) > 3})[:12], "side_effects": []}


def valid_ks(text):
    m = re.search(r"\{.*\}", text or "", re.S)
    try: o = json.loads(m.group(0) if m else text)
    except (ValueError, TypeError): return None
    if not isinstance(o, dict) or not all(isinstance(o.get(k), str) and o[k].strip() and len(o[k]) <= 400 for k in ("summary", "summary_ko")):
        return None
    if not isinstance(o.get("keywords", []), list) or not isinstance(o.get("side_effects", []), list): return None
    return {"summary": o["summary"], "summary_ko": o["summary_ko"], "keywords": [str(k) for k in o.get("keywords", [])][:12],
            "side_effects": [str(x) for x in o.get("side_effects", [])]}


def llm_summary(c):
    """kp2: KS_PROMPT + Path/Language/Kind/Graph context/Code; one retry with a JSON-only reminder; None = llm_failed."""
    user = (f"Path: {c['path']}\nLanguage: {c['language']}\nKind: {c['kind']}\n"
            f"Graph context: {json.dumps(c['ctx'], ensure_ascii=False, sort_keys=True)}\nCode:\n{c['text']}")
    msgs = [{"role": "system", "content": KS_PROMPT}, {"role": "user", "content": user}]
    for attempt in range(2):
        try: out = valid_ks(chat()(msgs))
        except providers.ProviderError as e:
            if e.code in ("context_length", "refused", "empty"): return None
            raise PluginError("code.llm_unavailable", {"provider": e.code})
        if out: return out
        msgs = msgs + [{"role": "user", "content": "Reply with the JSON object only."}]
    return None


def file_chunks(path, r, facts, ctx_max, max_chars):
    """Symbol units with their chunks (v0.2.4 boundaries; long chunks split into line windows)."""
    syms = {s["ordinal"]: s for s in r["symbols"]}
    calls = [(f["fact"]["line"], f["resolution"]) for f in facts if f["kind"] == "call" and f.get("resolution")]
    units, seen = {}, {}
    for c in r["chunks"]:
        s = syms[c["symbol_ordinal"]]
        if s["ordinal"] not in units:
            n = seen[s["qualified_name"]] = seen.get(s["qualified_name"], 0) + 1
            key = f"{path}::{s['qualified_name']}" + (f"#{n}" if n > 1 else "")
            attrs = {k: s.get(k) for k in ("kind", "signature", "decorators", "is_async", "visibility", "start_line", "end_line") if s.get(k) is not None}
            attrs.update(path=path, symbol=s["qualified_name"], language=r["language"],
                         nested=[x["qualified_name"] for x in r["symbols"] if x.get("parent_ordinal") == s["ordinal"] and x["kind"] != "class"])
            units[s["ordinal"]] = {"key": key, "attrs": attrs, "chunks": []}
        callees = sorted({res["target_key"] for line, res in calls if c["start_line"] <= line <= c["end_line"]
                          and res["status"] == "resolved" and res.get("method") not in TIER2 and res.get("target_key")})
        ctx = {"callees": callees[:ctx_max], "omitted": max(0, len(callees) - ctx_max)}
        lines, parts, cur = c["content"].split("\n"), [], []
        for ln in lines:
            if cur and len("\n".join(cur + [ln])) > max_chars: parts.append(cur); cur = []
            cur.append(ln[:max_chars])
        parts.append(cur)
        line0 = c["start_line"]
        for i, p in enumerate(parts):
            u = units[s["ordinal"]]
            u["chunks"].append({"text": "\n".join(p), "path": path, "kind": c["kind"], "language": r["language"], "ctx": ctx,
                                "symbol": s["qualified_name"], "start_line": line0, "end_line": line0 + len(p) - 1,
                                **({"part": i + 1, "parts": len(parts)} if len(parts) > 1 else {})})
            line0 += len(p)
    return list(units.values())


def assemble(ctx, t):
    inp = ctx.task_input()
    if t["depth"] == 0:
        todo = eligible(ctx, inp["manifest"])
        if len(todo) > pol(ctx, "batch_files", 50):
            ctx.split(batches(ctx, todo)); return None
    else:
        todo = t["payload"]["files"]
    db, llm = ctx.db, llm_on(ctx)
    cfg = configs(llm)
    kid = chat().identity if llm else STUB_KNOWLEDGE
    have = cached(db, t["tenant_id"], {f["sha256"] for f in todo})
    facts = {}
    for fk, kind, fact, res in db.execute("SELECT file_key, kind, fact, resolution FROM plugin_code.raw_facts WHERE job_id = %s "
                                          "AND file_key = ANY (%s)", (t["job_id"], [f["path"] for f in todo])):
        facts.setdefault(fk, []).append({"kind": kind, "fact": fact, "resolution": res})
    ctx_max, max_chars = pol(ctx, "llm.context_max_items", 20), int(ctx.policy.get("knowledge.chunk.max_chars") or 8000)
    files_out, units = [], []
    for f in todo:
        r = have.get(f["sha256"])
        if r is None: raise PluginError("code.parse_missing", {"path": f["path"]})
        if r["status"] != "parsed": continue
        fs = facts.get(f["path"], [])
        deps = [x["resolution"] or {} for x in fs if x["kind"] == "dep"]
        files_out.append((f["path"], {"path": f["path"], "language": r["language"], "size": f["size"], "sha256": f["sha256"],
                          "encoding": r["encoding"], "has_syntax_error": r["parser"]["has_syntax_error"],
                          "graph_excluded": r.get("graph_excluded_reason"),
                          "imports": sorted({d["target_path"] for d in deps if d.get("target_path")}),
                          "external": sorted({d["external"] for d in deps if d.get("external")})}))
        for u in file_chunks(f["path"], r, fs, ctx_max, max_chars):
            u["attrs"]["calls"] = sorted({c for ch in u["chunks"] for c in ch["ctx"]["callees"]})
            units.append(u)
    chunks = [c for u in units for c in u["chunks"]]
    # summaries (reused by input_hash across jobs and projects of the tenant)
    for c in chunks: c["sh"] = h(PROMPT, kid, CB, c["language"], c["ctx"], c["text"])
    known = ctx.get_values("code.summary", cfg["code.summary"], sorted({c["sh"] for c in chunks})) if chunks else {}
    todo_llm = {c["sh"]: c for c in chunks if c["sh"] not in known}
    made = {}
    if todo_llm:
        if llm:
            with ThreadPoolExecutor(pol(ctx, "llm.concurrency", 8)) as ex:
                made = dict(zip(todo_llm, ex.map(llm_summary, todo_llm.values())))
        else:
            made = {k: stub_summary(c) for k, c in todo_llm.items()}
    for c in chunks:
        c["ks"] = json.loads(known[c["sh"]]) if c["sh"] in known else made.get(c["sh"])
    # units: file units, then symbol units with chunks (summary in chunk attrs for FTS)
    staged, failed = [], []
    for path, attrs in files_out:
        r = ctx.put_unit("code.file", path, h(attrs), attrs)
        check(r); ctx.put_job_item(path, "ok")
    for u in units:
        cks = [{"ordinal": i, "text": c["text"], "span": {"start_line": c["start_line"], "end_line": c["end_line"]},
                "attrs": {k: c[k] for k in ("path", "kind", "language", "symbol", "start_line", "end_line", "part", "parts") if k in c}
                         | ({k: c["ks"][k] for k in ("summary", "summary_ko", "keywords")} if c["ks"] else {})}
               for i, c in enumerate(u["chunks"])]
        r = ctx.put_unit("code.symbol", u["key"], h(u["attrs"], cks, cfg), u["attrs"], cks)
        check(r)
        if any(c["ks"] is None for c in u["chunks"]):
            failed.append(u["key"]); ctx.put_job_item(u["key"], "excluded", "code.llm_failed")
        if r["outcome"] in ("STAGED", "REPLAY"):
            for i, c in enumerate(u["chunks"]): staged.append((r["chunk_ids"][str(i)], c))
    # representations of staged chunks: summary body, code embedding, knowledge embedding
    emb_in = []
    for cid, c in staged:
        if c["ks"]:
            body = None if c["sh"] in known else json.dumps(c["ks"], ensure_ascii=False, sort_keys=True)
            check(ctx.put_representation("code.summary", cfg["code.summary"], c["sh"], chunk_id=cid, body=body))
            kt = " ".join([c["ks"]["summary"], c["ks"]["summary_ko"], " ".join(c["ks"]["keywords"])]).strip()
            emb_in.append(("code.knowledge_embed", cid, kt))
        emb_in.append(("code.embed", cid, f"{c['symbol']}\n{c['text']}"))
    for rk in ("code.embed", "code.knowledge_embed"):
        items = [(cid, txt, h(rk, txt)) for k, cid, txt in emb_in if k == rk]
        if not items: continue
        known_v = ctx.get_values(rk, cfg[rk], sorted({x[2] for x in items}))
        need = {ih: txt for _, txt, ih in items if ih not in known_v}
        vecs = {}
        if need:
            keys = list(need)
            try:
                for i in range(0, len(keys), 32):
                    vecs.update(zip(keys[i:i + 32], embedder().embed([need[k] for k in keys[i:i + 32]])))
            except providers.ProviderError as e:
                raise PluginError("code.embed_unavailable", {"provider": e.code})
        for cid, _, ih in items:
            check(ctx.put_representation(rk, cfg[rk], ih, chunk_id=cid, vec=vecs.get(ih)))
            vecs.pop(ih, None)                               # one write per value; later links reuse it
    return {"files": len(files_out), "units": len(units), "staged_chunks": len(staged), "llm_failed": len(failed),
            "summaries_reused": len(chunks) - len(todo_llm)}


def check(r):
    if r["outcome"] not in ("OK", "STAGED", "REPLAY", "TOUCHED"):
        raise PluginError("code.stage_write_failed", r)


def query_vectors(text):
    """qvecs for knowledge.search: both vector kinds with their active config hashes."""
    v = embedder().embed([text])[0]
    vs = "[" + ",".join(map(str, v)) + "]"
    cfg = configs()
    return {k: {"config_hash": cfg[k], "vec": vs} for k in ("code.embed", "code.knowledge_embed")}


# ---------- G2 purge hook ----------
def purge(db, project_id, info):
    jobs = [str(j) for j in info.get("jobs", [])]
    with db.transaction():
        db.execute("DELETE FROM plugin_code.raw_facts WHERE job_id = ANY (%s::uuid[])", (jobs,))
        db.execute("DELETE FROM plugin_code.graph_quality WHERE job_id = ANY (%s::uuid[]) OR collection_id = ANY (%s::uuid[])",
                   (jobs, [str(c) for c in info.get("collections", [])]))
    return {}


HANDLERS = {"parse": parse, "resolve": resolve, "assemble": assemble, "purge": purge}
