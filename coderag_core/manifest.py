"""plugin.yaml v1: JSON Schema (§15) + semantic checks, manifest hash (P-P9) and folder hash (R13)."""
import hashlib, json, os, re
import yaml, jsonschema

KEY = {"type": "string", "pattern": r"^[a-z][a-z0-9_.]*$"}
FMT = {"type": "string", "pattern": r"^[a-z][a-z0-9_]*@\^?[0-9]+$"}
def arr(item): return {"type": "array", "items": item}
def obj(req, **props): return {"type": "object", "required": req, "properties": props, "additionalProperties": False}

STAGE = obj(["key", "kind"], key=KEY, kind={"enum": ["control", "item", "approval", "publish"]}, handler=KEY,
            depends_on=arr(KEY), split=obj(["max_items", "max_depth"], max_items={"type": "integer", "minimum": 1},
                                           max_depth={"type": "integer", "minimum": 0}),
            policy_keys=arr(KEY), resource=KEY, barrier={"const": "all"})
SCHEMA = obj(
    ["id", "version", "contract", "requires", "entry", "schema", "migrations"],
    id={"type": "string", "pattern": r"^[a-z][a-z0-9_]{1,29}$"},
    version={"type": "string", "pattern": r"^\d+\.\d+\.\d+([-+].*)?$"},
    contract={"const": 1},
    requires=obj(["core"], core={"type": "string"}),
    entry={"type": "string"}, schema={"type": "string", "pattern": r"^plugin_[a-z0-9_]+$"},
    migrations={"type": "string"},
    handlers=arr(obj(["name", "kind"], name=KEY, kind={"enum": ["control", "item", "service"]})),
    pipelines=arr(obj(["key", "version", "collection", "publish_mode", "stages"], key=KEY,
                      version={"type": "integer", "minimum": 1}, collection=KEY,
                      publish_mode={"enum": ["full", "delta"]}, inputs=arr(FMT), stages=arr(STAGE))),
    produces=arr(FMT),
    collections=arr(obj(["key"], key=KEY)),
    unit_kinds=arr(obj(["key"], key=KEY, parent_kind=KEY, required_representations=arr(KEY))),
    connectors=arr(obj(["key", "produces", "refetchable"], key=KEY, produces=FMT, refetchable={"type": "boolean"})),
    exclusion_reasons=arr(obj(["key"], key=KEY)),
    hooks=obj([], purge_project=KEY),
    representation_kinds=arr(obj(["key", "target", "value"], key=KEY, target={"enum": ["unit", "chunk"]},
                                 value={"enum": ["vector", "text", "json"]}, dims={"type": "integer", "minimum": 1},
                                 model_ref=KEY, template_version={"type": "string"})),
    policy_keys=arr(obj(["key", "type"], key=KEY, type={"enum": ["int", "number", "interval", "bool", "text"]},
                        min={"type": "number"}, max={"type": "number"}, unit={"type": "string"},
                        affects_output={"type": "boolean"})),
    resources=arr(obj(["key", "kind"], key=KEY, kind={"enum": ["http", "grpc", "process"]}, breaker={"type": "boolean"})),
    permissions=arr(obj(["key", "min_role"], key=KEY, min_role={"enum": ["reader", "contributor", "operator", "admin"]})),
    schedules=arr(obj(["key", "handler", "interval_policy_key"], key=KEY, handler=KEY, interval_policy_key=KEY)),
    error_codes=arr(obj(["key", "retryable", "health_sample"], key=KEY, retryable={"type": "boolean"},
                        health_sample={"type": "boolean"})),
    events=obj([], emits=arr(KEY), consumes=arr(KEY)),
    api=obj(["router", "prefix"], router={"type": "string"}, prefix={"type": "string"}),
    service=obj(["endpoints"], endpoints=arr(obj(["path", "handler", "permission"], path={"type": "string"},
                                                 handler=KEY, permission=KEY))),
)


class ManifestError(ValueError):
    pass


def check(m: dict) -> dict:
    """Schema + semantic checks; raises ManifestError. Returns the manifest."""
    try:
        jsonschema.Draft202012Validator(SCHEMA).validate(m)
    except jsonschema.ValidationError as e:
        raise ManifestError(f"{list(e.absolute_path)}: {e.message}") from None
    if m["schema"] != "plugin_" + m["id"]:
        raise ManifestError("schema must be plugin_<id>")
    for field in ("handlers", "collections", "unit_kinds", "representation_kinds", "policy_keys", "resources",
                  "permissions", "schedules", "error_codes", "connectors", "exclusion_reasons", "pipelines"):
        keys = [x.get("name", x.get("key")) for x in m.get(field, [])]
        if field == "pipelines":
            keys = [(x["key"], x["version"]) for x in m.get(field, [])]
        if len(keys) != len(set(keys)):
            raise ManifestError(f"duplicate key in {field}")
    handlers = {h["name"] for h in m.get("handlers", [])}
    collections = {c["key"] for c in m.get("collections", [])}
    for r in m.get("representation_kinds", []):
        if r["value"] == "vector" and "dims" not in r:
            raise ManifestError(f"representation {r['key']}: dims required for vector")
    for p in m.get("pipelines", []):
        if p["collection"] not in collections:
            raise ManifestError(f"pipeline {p['key']}: collection {p['collection']} not declared")
        stages = {s["key"]: s for s in p["stages"]}
        if len(stages) != len(p["stages"]):
            raise ManifestError(f"pipeline {p['key']}: duplicate stage")
        for s in p["stages"]:
            core_kind = s["kind"] in ("approval", "publish")
            if core_kind == ("handler" in s):
                raise ManifestError(f"stage {s['key']}: handler required for control/item, refused for approval/publish")
            if "handler" in s and s["handler"] not in handlers:
                raise ManifestError(f"stage {s['key']}: handler {s['handler']} not declared")
            for d in s.get("depends_on", []):
                if d not in stages:
                    raise ManifestError(f"stage {s['key']}: depends_on {d} unknown")
            if "split" in s and s["kind"] != "item":
                raise ManifestError(f"stage {s['key']}: split only for item stages")
        seen, done = set(), set()                              # DAG check (I-R5)
        def visit(k):
            if k in done: return
            if k in seen: raise ManifestError(f"pipeline {p['key']}: cycle at {k}")
            seen.add(k)
            for d in stages[k].get("depends_on", []): visit(d)
            done.add(k)
        for k in stages: visit(k)
        if sum(s["kind"] == "publish" for s in p["stages"]) != 1:
            raise ManifestError(f"pipeline {p['key']}: exactly one publish stage")
    return m


def canonical(m: dict) -> str:
    """RFC 8785-style canonical JSON (sorted keys, no spaces); manifests hold no floats needing JCS number rules."""
    return json.dumps(m, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def manifest_hash(m: dict) -> str:
    return hashlib.sha256(canonical(m).encode()).hexdigest()


def dir_hash(path: str) -> str:
    h = hashlib.sha256()
    for root, dirs, files in os.walk(path):
        dirs[:] = sorted(d for d in dirs if d != "__pycache__")
        for f in sorted(files):
            if f.endswith(".pyc"): continue
            full = os.path.join(root, f)
            h.update(os.path.relpath(full, path).encode() + b"\0")
            with open(full, "rb") as fh: h.update(hashlib.sha256(fh.read()).digest())
    return h.hexdigest()


def load(plugin_dir: str) -> dict:
    with open(os.path.join(plugin_dir, "plugin.yaml")) as f:
        return check(yaml.safe_load(f))


def migrations(plugin_dir: str, m: dict):
    """[(number, path)] from <dir>/<migrations>/NNN_name.sql in order."""
    d = os.path.join(plugin_dir, m["migrations"])
    out = []
    for f in sorted(os.listdir(d)) if os.path.isdir(d) else []:
        mt = re.match(r"^(\d{3})_[a-z0-9_]+\.sql$", f)
        if mt: out.append((int(mt.group(1)), os.path.join(d, f)))
    return out
