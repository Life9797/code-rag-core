"""Copied from code-rag v0.2.4 eval/evaluator/golden.py (O-16).
Load golden queries (*.jsonl) and source archives (*.zip) from the init folder.

Each expected answer is a (path, symbol) pair. The source archive is parsed to
find the symbol's line span, so a search result counts as a hit when it points
at the same file and its line range overlaps the symbol's definition. An empty
symbol means "anywhere in the file".
"""

import ast
import glob
import io
import json
import os
import zipfile
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple


@dataclass
class Expected:
    path: str
    symbol: str
    span: Optional[Tuple[int, int]] = None      # (first_line, last_line), None = whole file


@dataclass
class Query:
    id: str
    repo: str
    type: str
    text: Dict[str, str]                          # language -> query text, e.g. {"ko": ..., "en": ...}
    expected: List[Expected]


@dataclass
class Repo:
    name: str
    zip_path: str
    files: Dict[str, str] = field(default_factory=dict)   # repo-relative path -> source text


def _strip_root(name: str, repo: str) -> str:
    """Archive entries look like 'shoplite/shoplite/orders.py'; queries use
    'shoplite/orders.py' (relative to the repository root)."""
    parts = name.split("/", 1)
    return parts[1] if len(parts) == 2 and parts[0] == repo else name


def load_repos(init_dir: str) -> Dict[str, Repo]:
    repos: Dict[str, Repo] = {}
    for zp in sorted(glob.glob(os.path.join(init_dir, "*.zip"))):
        with zipfile.ZipFile(zp) as zf:
            names = [n for n in zf.namelist() if not n.endswith("/")]
            top = {n.split("/", 1)[0] for n in names}
            repo = top.pop() if len(top) == 1 else os.path.splitext(os.path.basename(zp))[0]
            r = Repo(repo, zp)
            for n in names:
                with zf.open(n) as f:
                    try:
                        r.files[_strip_root(n, repo)] = io.TextIOWrapper(f, encoding="utf-8").read()
                    except UnicodeDecodeError:
                        continue
            repos[repo] = r
    return repos


def symbol_spans(source: str) -> Dict[str, Tuple[int, int]]:
    """Top-level functions, classes, methods (Class.method) and assignments."""
    spans: Dict[str, Tuple[int, int]] = {}
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return spans
    for n in tree.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            spans[n.name] = (n.lineno, n.end_lineno)
            if isinstance(n, ast.ClassDef):
                for m in n.body:
                    if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        spans[f"{n.name}.{m.name}"] = (m.lineno, m.end_lineno)
        elif isinstance(n, (ast.Assign, ast.AnnAssign)):
            targets = n.targets if isinstance(n, ast.Assign) else [n.target]
            for t in targets:
                if isinstance(t, ast.Name):
                    spans[t.id] = (n.lineno, n.end_lineno)
    return spans


def load_queries(init_dir: str, repos: Dict[str, Repo]) -> Tuple[List[Query], List[str]]:
    """Returns the queries and a list of problems (unknown repo, missing file
    or symbol). A query with a problem is still loaded; the problem is reported."""
    queries, problems = [], []
    span_cache: Dict[Tuple[str, str], Dict[str, Tuple[int, int]]] = {}
    for jp in sorted(glob.glob(os.path.join(init_dir, "*.jsonl"))):
        with open(jp, encoding="utf-8") as f:
            for ln, line in enumerate(f, 1):
                if not line.strip():
                    continue
                d = json.loads(line)
                text = {k[len("query_"):]: v for k, v in d.items() if k.startswith("query_") and v}
                if "query" in d:
                    text.setdefault("default", d["query"])
                exp = []
                repo = repos.get(d["repo"])
                if repo is None:
                    problems.append(f"{d['id']}: no source archive for repo {d['repo']!r}")
                for e in d["expected"]:
                    span = None
                    if repo is not None:
                        src = repo.files.get(e["path"])
                        if src is None:
                            problems.append(f"{d['id']}: file {e['path']} not in {repo.name}")
                        elif e.get("symbol"):
                            key = (repo.name, e["path"])
                            if key not in span_cache:
                                span_cache[key] = symbol_spans(src)
                            span = span_cache[key].get(e["symbol"])
                            if span is None:
                                problems.append(f"{d['id']}: symbol {e['symbol']} not in {e['path']}")
                    exp.append(Expected(e["path"], e.get("symbol", ""), span))
                queries.append(Query(d["id"], d["repo"], d.get("type", ""), text, exp))
    return queries, problems
