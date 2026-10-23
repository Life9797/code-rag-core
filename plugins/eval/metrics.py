"""Copied from code-rag v0.2.4 eval/evaluator/metrics.py (O-16).
Hit matching and retrieval metrics."""

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

from .golden import Expected


@dataclass
class Hit:
    """One search result as returned by a target."""
    path: str
    start_line: int = 0
    end_line: int = 0
    symbol: str = ""
    score: float = 0.0


def matches(hit: Hit, exp: Expected) -> bool:
    if hit.path != exp.path:
        return False
    if exp.span is None:                 # whole-file answer
        return True
    if hit.symbol and hit.symbol == exp.symbol:
        return True
    if hit.start_line and hit.end_line:
        a, b = exp.span
        return hit.start_line <= b and hit.end_line >= a      # line ranges overlap
    return False


def score_query(hits: Sequence[Hit], expected: List[Expected], k: int) -> Dict:
    top = list(hits)[:k]
    found = [any(matches(h, e) for h in top) for e in expected]
    first_rank: Optional[int] = None
    for rank, h in enumerate(top, 1):
        if any(matches(h, e) for e in expected):
            first_rank = rank
            break
    return {
        "recall": sum(found) / len(expected) if expected else 0.0,   # share of expected locations in top-k
        "hit": 1.0 if any(found) else 0.0,                            # at least one in top-k
        "rr": 1.0 / first_rank if first_rank else 0.0,                # reciprocal rank of first relevant
        "first_rank": first_rank,
    }


def percentile(values: List[float], p: float) -> Optional[float]:
    if not values:
        return None
    s = sorted(values)
    return s[max(0, math.ceil(p / 100 * len(s)) - 1)]


def summarize(rows: List[Dict]) -> Dict:
    n = len(rows)
    lat = [r["latency_ms"] for r in rows if r.get("latency_ms") is not None]
    ok = [r for r in rows if not r.get("error")]
    return {
        "queries": n,
        "errors": n - len(ok),
        "recall_at_k": round(sum(r["recall"] for r in ok) / len(ok), 4) if ok else None,
        "hit_at_k": round(sum(r["hit"] for r in ok) / len(ok), 4) if ok else None,
        "mrr": round(sum(r["rr"] for r in ok) / len(ok), 4) if ok else None,
        "p50_ms": percentile(lat, 50),
        "p95_ms": percentile(lat, 95),
    }
