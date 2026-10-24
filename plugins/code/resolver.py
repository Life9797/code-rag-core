"""Copied from code-rag v0.2.4 worker/coderag/resolver.py (O-16); ids are strings (path / path#ordinal).

F05 Python resolver (F05-02..F05-05). Pure computation over the job's files, symbols and raw facts; the
transaction is f05_persist_graph. Exact methods are tier 1; global_name_unique and package_rule are tier 2.

Choices (W5-6): a third-party top-level module (not in the repo, not stdlib) is external via package_rule;
self/cls calls look only in the enclosing class (an inherited method is candidate_not_found); super() calls
are unsupported_resolution; calls on variables are receiver_type_unknown.
"""
import builtins
import sys
from dataclasses import dataclass, field

RESOLVER_VERSION = "py-r1"
STDLIB = set(sys.stdlib_module_names)
BUILTINS = set(dir(builtins))


@dataclass
class Sym:
    id: str
    file_id: str
    ordinal: int
    parent_ordinal: int | None
    kind: str
    name: str
    qualified_name: str


@dataclass
class FileInfo:
    id: str
    path: str
    status: str
    graph_excluded: str | None
    symbols: dict = field(default_factory=dict)   # ordinal -> Sym


def module_of(path: str) -> str:
    m = path.rsplit(".", 1)[0].replace("/", ".")
    return m[: -len(".__init__")] if m.endswith(".__init__") else m


class Index:
    def __init__(self, files: list[FileInfo], inventory: dict[str, str]):
        self.files = {f.id: f for f in files}
        self.by_module = {module_of(f.path): f for f in files}
        self.inventory = inventory                       # path -> 'eligible' | excluded reason
        self.by_name: dict[str, list[Sym]] = {}
        for f in files:
            for s in f.symbols.values():
                if s.kind in ("function", "class") and self._top_or_class_free(f, s):
                    self.by_name.setdefault(s.name, []).append(s)

    @staticmethod
    def _top_or_class_free(f, s):
        return s.parent_ordinal == 0

    def module_state(self, mod: str):
        """('file', FileInfo) | ('excluded', reason) | ('failed', None) | (None, None)"""
        f = self.by_module.get(mod)
        if f is not None:
            return ("failed", None) if f.status == "failed" else ("file", f)
        for cand in (mod.replace(".", "/") + ".py", mod.replace(".", "/") + "/__init__.py"):
            if cand in self.inventory:
                return "excluded", self.inventory[cand]
        return None, None

    @staticmethod
    def children(f: FileInfo, parent_ord: int, name: str, kinds=("function", "class")):
        return [s for s in f.symbols.values() if s.parent_ordinal == parent_ord and s.name == name and s.kind in kinds]


def _unres(reason):
    return {"status": "unresolved", "reason": reason}


def _upstream(state, reason):
    if state == "failed":
        return _unres("target_file_parse_failed")
    return _unres("target_language_unsupported" if reason == "unsupported_language" else "target_file_excluded")


def resolve_dependency(ix: Index, f: FileInfo, d: dict) -> dict:
    if d["kind"] == "dynamic" or d["raw_target"] is None:
        return _unres("dynamic_target")
    if d["style"] == "dotted_relative":
        pkg = module_of(f.path).split(".")
        if not f.path.endswith("__init__.py"):
            pkg = pkg[:-1]
        up = d["relative_level"] - 1
        if up > len(pkg):
            return _unres("invalid_reference")
        base = pkg[: len(pkg) - up] if up else pkg
        mod = ".".join(base + ([d["raw_target"]] if d["raw_target"] else []))
        state, x = ix.module_state(mod)
        if state == "file":
            return {"status": "resolved", "method": "relative_path", "target_file_id": x.id, "module": mod}
        if state in ("excluded", "failed"):
            return _upstream(state, x)
        return _unres("import_unresolved")
    mod = d["raw_target"]
    state, x = ix.module_state(mod)
    if state == "file":
        return {"status": "resolved", "method": "module_path_map", "target_file_id": x.id, "module": mod}
    if state in ("excluded", "failed"):
        return _upstream(state, x)
    top = mod.split(".")[0]
    if top in STDLIB:
        return {"status": "external", "method": "stdlib_registry", "target_external": mod, "module": mod}
    if any(m == top or m.startswith(top + ".") for m in ix.by_module):
        return _unres("import_unresolved")              # an in-repo package without this module
    return {"status": "external", "method": "package_rule", "target_external": mod, "module": mod}


def bindings(ix: Index, f: FileInfo, deps: list[dict], dep_res: list[dict]) -> dict:
    """local name -> ('symbol', Sym) | ('module', FileInfo) | ('external', dotted, method)"""
    out = {}
    for d, r in zip(deps, dep_res):
        for b in d.get("bindings") or []:
            local = b.get("local_name") or b.get("name")
            if not local:
                continue
            if b["kind"] == "module":
                name = b["name"]
                bound = name if b.get("local_name") and b["local_name"] != name.split(".")[0] else name.split(".")[0]
                st, x = ix.module_state(bound)
                if st == "file":
                    out[local] = ("module", x)
                elif r["status"] == "external" or (st is None and bound.split(".")[0] in STDLIB):
                    out[local] = ("external", bound, "stdlib_registry" if bound.split(".")[0] in STDLIB else "package_rule")
            elif b["kind"] == "name":
                if r["status"] == "resolved":
                    t = ix.files[r["target_file_id"]]
                    syms = Index.children(t, 0, b["name"])
                    if len(syms) == 1:
                        out[local] = ("symbol", syms[0])
                        continue
                    st, x = ix.module_state(f"{r['module']}.{b['name']}")
                    if st == "file":
                        out[local] = ("module", x)
                elif r["status"] == "external":
                    out[local] = ("external", f"{r['module']}.{b['name']}", r["method"])
    return out


def _hit(sym: Sym, method: str):
    return {"status": "resolved", "method": "constructor_class" if sym.kind == "class" else method, "callee_symbol_id": sym.id}


def resolve_call(ix: Index, f: FileInfo, binds: dict, c: dict) -> dict:
    if c["kind"] == "dynamic" or not c.get("callee_name"):
        return _unres("dynamic_target")
    name, rk = c["callee_name"], c["receiver_kind"]
    caller = f.symbols.get(c["caller_symbol_ordinal"])
    if rk == "none":
        scope = caller
        while scope is not None:                     # enclosing function / module scopes (class bodies are not)
            if scope.kind != "class" or scope is caller:
                hits = Index.children(f, scope.ordinal, name)
                if len(hits) == 1:
                    return _hit(hits[0], "same_file_scope")
            scope = f.symbols.get(scope.parent_ordinal) if scope.parent_ordinal is not None else None
        b = binds.get(name)
        if b is not None:
            if b[0] == "symbol":
                return _hit(b[1], "import_binding")
            if b[0] == "external":
                return {"status": "external", "method": b[2], "callee_external": b[1]}
            return _unres("invalid_reference")
        if name in BUILTINS:
            return {"status": "external", "method": "stdlib_registry", "callee_external": f"builtins.{name}"}
        cands = ix.by_name.get(name, [])
        if len(cands) == 1:
            return {"status": "resolved", "method": "global_name_unique", "callee_symbol_id": cands[0].id}
        if len(cands) > 1:
            return {"status": "ambiguous", "candidate_count": len(cands)}
        return _unres("candidate_not_found")
    if rk in ("self", "cls"):
        cls = caller
        while cls is not None and cls.kind != "class":
            cls = f.symbols.get(cls.parent_ordinal) if cls.parent_ordinal is not None else None
        if cls is None:
            return _unres("receiver_type_unknown")
        hits = Index.children(f, cls.ordinal, name, ("method", "class"))
        return _hit(hits[0], "enclosing_class_member") if len(hits) == 1 else _unres("candidate_not_found")
    if rk == "super":
        return _unres("unsupported_resolution")
    if rk == "name":
        recv = c.get("receiver_name") or ""
        head = recv.split(".")[0]
        b = binds.get(recv) or binds.get(head)
        if b is not None and b[0] == "module":
            target = b[1]
            rest = recv.split(".")[1:] if recv not in binds else []
            if rest:
                st, x = ix.module_state(f"{module_of(target.path)}.{'.'.join(rest)}")
                if st != "file":
                    return _unres("candidate_not_found")
                target = x
            hits = Index.children(target, 0, name)
            return _hit(hits[0], "dependency_target") if len(hits) == 1 else _unres("candidate_not_found")
        if b is not None and b[0] == "external":
            dotted = b[1] if recv in binds else b[1] + recv[len(head):]
            return {"status": "external", "method": b[2], "callee_external": f"{dotted}.{name}"}
        if b is not None and b[0] == "symbol" and b[1].kind == "class":
            t = ix.files[b[1].file_id]
            hits = Index.children(t, b[1].ordinal, name, ("method",))
            return _hit(hits[0], "import_binding") if len(hits) == 1 else _unres("candidate_not_found")
        local = Index.children(f, 0, recv, ("class",))
        if len(local) == 1:
            hits = Index.children(f, local[0].ordinal, name, ("method",))
            return _hit(hits[0], "same_file_scope") if len(hits) == 1 else _unres("candidate_not_found")
        if head in STDLIB and head not in binds:
            return _unres("import_unresolved")
        return _unres("receiver_type_unknown")
    return _unres("receiver_type_unknown")
