"""Copied from code-rag v0.2.4 worker/coderag/parse_python.py (O-16); input is decoded text, output is the parse result.

F03 parse for Python with tree-sitter: symbols, chunks and raw facts (F03-01..F03-07, PV5-109).

Choices (W5-6): one chunk per class header (class line to its first method), per function or method that is
not nested in a function, and per run of consecutive module-level statements (kind 'module'). Nested functions
are symbols inside their parent's chunk. normalization 'n1': CRLF -> LF, trailing spaces stripped.
"""
import hashlib

import tree_sitter_python
from tree_sitter import Language, Parser

PARSER_NAME = "tree-sitter-python"
PARSER_VERSION = "0.25"
NORMALIZATION = "n1"
_LANG = Language(tree_sitter_python.language())


def _text(node, src: bytes) -> str:
    return src[node.start_byte:node.end_byte].decode("utf-8", "replace")


def normalize(text: str) -> str:
    return "\n".join(line.rstrip() for line in text.replace("\r\n", "\n").split("\n")).strip("\n")


def _dotted(node, src):
    """identifier or attribute chain of identifiers -> 'a.b.c', else None."""
    if node.type == "identifier":
        return _text(node, src)
    if node.type == "attribute":
        obj = _dotted(node.child_by_field_name("object"), src)
        attr = node.child_by_field_name("attribute")
        return f"{obj}.{_text(attr, src)}" if obj and attr is not None else None
    return None


def module_name(path: str) -> str:
    return path.rsplit(".", 1)[0].replace("/", ".")


def parse_file(path: str, src_text: str, raw_fact_cap: int) -> dict:
    """symbols, chunks, dependencies, calls (+ graph_excluded_reason) for one decoded file."""
    base = {"language": "python", "parser": {"name": PARSER_NAME, "version": PARSER_VERSION, "has_syntax_error": False}}
    src = src_text.encode("utf-8")
    tree = Parser(_LANG).parse(src)
    root = tree.root_node
    mod = module_name(path)
    lines = src_text.count("\n") + 1
    symbols = [{"ordinal": 0, "kind": "module", "name": mod.rsplit(".", 1)[-1], "qualified_name": mod,
                "start_line": 1, "end_line": max(lines, 1), "docstring": _docstring(root, src)}]
    chunks, deps, calls = [], [], []

    def add_symbol(node, outer, parent_ord, scope_q, parent_kind):
        defn = node.child_by_field_name("definition") if node.type == "decorated_definition" else node
        name = _text(defn.child_by_field_name("name"), src)
        is_class = defn.type == "class_definition"
        kind = "class" if is_class else ("method" if parent_kind == "class" else "function")
        sym = {"ordinal": len(symbols), "parent_ordinal": parent_ord, "kind": kind, "name": name,
               "qualified_name": f"{scope_q}.{name}", "start_line": outer.start_point[0] + 1,
               "end_line": outer.end_point[0] + 1, "visibility": "private" if name.startswith("_") and not name.endswith("__") else "public",
               "decorators": [_text(d, src).lstrip("@").strip() for d in node.children if d.type == "decorator"]
               if node.type == "decorated_definition" else []}
        if not is_class:
            params = defn.child_by_field_name("parameters")
            ret = defn.child_by_field_name("return_type")
            sym["signature"] = (_text(params, src) if params is not None else "()") + (f" -> {_text(ret, src)}" if ret is not None else "")
            sym["is_async"] = any(c.type == "async" for c in defn.children)
        body = defn.child_by_field_name("body")
        doc = _docstring(body, src) if body is not None else None
        if doc is not None:
            sym["docstring"], sym["docstring_truncated"] = doc[:4000], len(doc) > 4000
        symbols.append(sym)
        return sym, defn, body, kind

    def chunk(start_node_or_line, end_line, kind, sym_ord, text):
        norm = normalize(text)
        if not norm.strip():
            return
        chunks.append({"text_sha256": hashlib.sha256(norm.encode()).hexdigest(), "normalization_version": NORMALIZATION,
                       "content": norm, "kind": kind, "symbol_ordinal": sym_ord,
                       "start_line": start_node_or_line, "end_line": end_line})

    def walk(node, sym_ord, scope_q, scope_kind, in_function):
        """Pre-order over definitions; facts collected with the innermost enclosing symbol."""
        for child in node.children:
            if child.type in ("class_definition", "function_definition", "decorated_definition"):
                defn = child.child_by_field_name("definition") if child.type == "decorated_definition" else child
                if defn is None or defn.type not in ("class_definition", "function_definition"):
                    walk(child, sym_ord, scope_q, scope_kind, in_function)
                    continue
                sym, defn, body, kind = add_symbol(child, child, sym_ord, scope_q, scope_kind)
                if child.type == "decorated_definition":
                    for d in child.children:
                        if d.type == "decorator":
                            facts(d, sym_ord)
                for part in ("superclasses", "parameters", "return_type"):
                    sub = defn.child_by_field_name(part)
                    if sub is not None:
                        facts(sub, sym["ordinal"])
                if kind == "class":
                    first_def = next((c for c in (body.children if body else []) if c.type in
                                      ("function_definition", "decorated_definition", "class_definition")), None)
                    end = first_def.start_byte if first_def is not None else child.end_byte
                    end_line = (first_def.start_point[0] if first_def is not None else child.end_point[0] + 1)
                    if not in_function:
                        chunk(child.start_point[0] + 1, max(end_line, child.start_point[0] + 1), "class", sym["ordinal"],
                              src[child.start_byte:end].decode("utf-8", "replace"))
                elif not in_function:
                    chunk(child.start_point[0] + 1, child.end_point[0] + 1, kind, sym["ordinal"], _text(child, src))
                if body is not None:
                    walk(body, sym["ordinal"], sym["qualified_name"], kind, in_function or kind != "class")
            else:
                facts(child, sym_ord)

    def facts(node, sym_ord):
        stack = [node]
        while stack:
            n = stack.pop()
            if n.type in ("class_definition", "function_definition", "decorated_definition"):
                continue   # handled by walk with its own symbol
            if n.type == "import_statement":
                names = [c for c in n.children if c.type in ("dotted_name", "aliased_import")]
                binds = []
                for c in names:
                    full = _text(c.child_by_field_name("name") if c.type == "aliased_import" else c, src)
                    alias = c.child_by_field_name("alias")
                    binds.append({"kind": "module", "name": full, "local_name": _text(alias, src) if alias is not None else full.split(".")[0]})
                if binds:
                    deps.append({"kind": "import", "style": "module", "raw_target": binds[0]["name"], "bindings": binds,
                                 "scope_symbol_ordinal": sym_ord, "line": n.start_point[0] + 1})
                continue
            if n.type == "import_from_statement":
                modn = n.child_by_field_name("module_name")
                level, target = 0, ""
                if modn is not None:
                    if modn.type == "relative_import":
                        prefix = next((c for c in modn.children if c.type == "import_prefix"), None)
                        level = len(_text(prefix, src)) if prefix is not None else 1
                        dn = next((c for c in modn.children if c.type == "dotted_name"), None)
                        target = _text(dn, src) if dn is not None else ""
                    else:
                        target = _text(modn, src)
                binds = []
                for c in n.children_by_field_name("name"):
                    nm = c.child_by_field_name("name") if c.type == "aliased_import" else c
                    alias = c.child_by_field_name("alias") if c.type == "aliased_import" else None
                    binds.append({"kind": "name", "name": _text(nm, src), "local_name": _text(alias or nm, src)})
                if any(c.type == "wildcard_import" for c in n.children):
                    binds.append({"kind": "wildcard"})
                deps.append({"kind": "import", "style": "dotted_relative" if level else "module", "relative_level": level,
                             "raw_target": target, "bindings": binds, "scope_symbol_ordinal": sym_ord, "line": n.start_point[0] + 1})
                continue
            if n.type == "call":
                fn = n.child_by_field_name("function")
                fact = {"kind": "call", "receiver_kind": "none", "caller_symbol_ordinal": sym_ord, "line": n.start_point[0] + 1}
                if fn.type == "identifier":
                    fact["callee_name"] = _text(fn, src)
                elif fn.type == "attribute":
                    obj, attr = fn.child_by_field_name("object"), fn.child_by_field_name("attribute")
                    fact["callee_name"] = _text(attr, src)
                    dotted = _dotted(obj, src)
                    if obj.type == "call" and _text(obj.child_by_field_name("function"), src) == "super":
                        fact["receiver_kind"] = "super"
                    elif dotted in ("self", "cls"):
                        fact["receiver_kind"] = dotted
                    elif dotted:
                        fact["receiver_kind"], fact["receiver_name"] = "name", dotted
                    else:
                        fact["receiver_kind"] = "expression"
                else:
                    fact["kind"], fact["receiver_kind"] = "dynamic", "expression"
                calls.append(fact)
            stack.extend(reversed(n.children))

    # module-level statement runs become module chunks
    run = []

    def flush():
        if run:
            chunk(run[0].start_point[0] + 1, run[-1].end_point[0] + 1, "module", 0,
                  src[run[0].start_byte:run[-1].end_byte].decode("utf-8", "replace"))
            run.clear()

    for child in root.children:
        if child.type in ("class_definition", "function_definition", "decorated_definition"):
            flush()
        elif child.type != "comment" or run:
            run.append(child)
    flush()
    walk(root, 0, mod, "module", False)

    chunks.sort(key=lambda c: (c["start_line"], c["end_line"]))
    for i, c in enumerate(chunks):
        c["ordinal"] = i
    for i, d in enumerate(deps):
        d["ordinal"] = i
    calls.sort(key=lambda c: (c["line"], c["caller_symbol_ordinal"]))
    for i, c in enumerate(calls):
        c["ordinal"] = i
    payload = {**base, "symbols": symbols, "chunks": chunks}
    payload["parser"]["has_syntax_error"] = root.has_error
    if len(deps) + len(calls) > raw_fact_cap:
        payload.update(dependencies=[], calls=[], graph_excluded_reason="raw_fact_cap_exceeded")
    else:
        payload.update(dependencies=deps, calls=calls)
        if root.has_error:
            payload["graph_excluded_reason"] = "syntax_error"
    return payload


def _docstring(body, src):
    if body is None:
        return None
    for c in body.children:
        if c.type == "comment":
            continue
        if c.type == "expression_statement" and c.children and c.children[0].type == "string":
            s = _text(c.children[0], src)
            for q in ('"""', "'''", '"', "'"):
                if s.startswith(q) and s.endswith(q) and len(s) >= 2 * len(q):
                    return s[len(q):-len(q)].strip()
            return s
        return None
    return None
