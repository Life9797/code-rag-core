"""Shared model clients (§17): embedder (stub | TEI | OpenAI-compatible) and LLM chat (OpenAI-compatible).
Ported from code-rag v0.2.4 worker/coderag/providers.py (StubEmbedder, HttpEmbedder, OpenAIEmbedder, HttpKnowledge._one).
Env: CODERAG_EMBED_URL, CODERAG_EMBED_API (tei|openai), CODERAG_EMBED_MODEL, CODERAG_EMBED_MODEL_ID;
CODERAG_LLM_URL, CODERAG_LLM_MODEL, CODERAG_LLM_REVISION, CODERAG_LLM_TIMEOUT, CODERAG_LLM_MAX_TOKENS,
CODERAG_LLM_JSON_MODE, CODERAG_LLM_REASONING_EFFORT. Phase 3 values (PROP): reasoning low, max_tokens 2048, json_mode off."""
import hashlib, math, os, re

E = os.environ.get
_WORD = re.compile(r"[A-Za-z][a-z0-9]*|[A-Z]+(?![a-z])|\d+|[^\x00-\x7f]+")


class ProviderError(Exception):
    """code: unavailable | timeout | rate_limited | context_length | refused | empty"""
    def __init__(self, code, detail=""):
        super().__init__(f"{code}: {detail}"); self.code = code


def tokens(text):
    out = []
    for w in re.findall(r"\w+", text):
        parts = [p.lower() for p in _WORD.findall(w)] or [w.lower()]
        out.extend(parts if len(parts) > 1 else [w.lower()])
    return out


def _unit(v):
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


class StubEmbedder:
    """Deterministic, offline: hashed word/identifier-part features, unit length."""
    identity = "stub-embed-v1"
    def __init__(self, dims): self.dims = dims
    def embed(self, texts):
        out = []
        for t in texts:
            v = [0.0] * self.dims
            for tok in tokens(t) or ["empty"]:
                h = hashlib.sha256(tok.encode()).digest()
                v[int.from_bytes(h[:4], "big") % self.dims] += 1.0 if h[4] & 1 else -1.0
            if not any(v): v[0] = 1.0
            out.append(_unit(v))
        return out


class HttpEmbedder:
    """TEI (/embed) or OpenAI-compatible (/v1/embeddings); vectors returned at unit length."""
    def __init__(self, url, dims, api="tei", model="bge-m3", timeout=60):
        import httpx
        self.c, self.dims, self.api, self.model = httpx.Client(base_url=url.rstrip("/"), timeout=timeout), dims, api, model
        self.identity = E("CODERAG_EMBED_MODEL_ID") or model
    def embed(self, texts):
        import httpx
        try:
            if self.api == "openai":
                r = self.c.post("/v1/embeddings", json={"model": self.model, "input": texts})
                r.raise_for_status()
                return [_unit(d["embedding"]) for d in sorted(r.json()["data"], key=lambda d: d["index"])]
            r = self.c.post("/embed", json={"inputs": texts, "normalize": True})
            r.raise_for_status()
            return [_unit(v) for v in r.json()]
        except httpx.TimeoutException as e: raise ProviderError("timeout", str(e))
        except httpx.HTTPError as e: raise ProviderError("unavailable", str(e))


def make_embedder(dims):
    url = E("CODERAG_EMBED_URL")
    return HttpEmbedder(url, dims, E("CODERAG_EMBED_API", "tei"), E("CODERAG_EMBED_MODEL", "bge-m3")) if url else StubEmbedder(dims)


class Chat:
    """OpenAI-compatible chat completions; returns message text or raises ProviderError."""
    def __init__(self, url, model, revision="", timeout=120, max_tokens=2048, json_mode=False, reasoning_effort="low"):
        import httpx
        self.c = httpx.Client(base_url=url.rstrip("/"), timeout=timeout)
        self.model, self.max_tokens, self.json_mode, self.reasoning = model, max_tokens, json_mode, reasoning_effort
        self.identity = f"{model}@{revision}"
    def __call__(self, messages):
        import httpx
        body = {"model": self.model, "messages": messages, "temperature": 0, "max_tokens": self.max_tokens}
        if self.json_mode: body["response_format"] = {"type": "json_object"}
        if self.reasoning: body["reasoning_effort"] = self.reasoning
        try: r = self.c.post("/v1/chat/completions", json=body)
        except httpx.TimeoutException as e: raise ProviderError("timeout", str(e))
        except httpx.HTTPError as e: raise ProviderError("unavailable", repr(e))
        if r.status_code == 429: raise ProviderError("rate_limited")
        if r.status_code == 400 and "context" in r.text.lower(): raise ProviderError("context_length")
        if r.status_code >= 400: raise ProviderError("unavailable", f"HTTP {r.status_code}")
        try:
            ch = r.json()["choices"][0]
            msg = ch.get("message") or {}
        except (ValueError, KeyError, IndexError, TypeError): raise ProviderError("empty", "bad response")
        if ch.get("finish_reason") == "content_filter" or msg.get("refusal"): raise ProviderError("refused")
        return msg.get("content") or ""


def make_chat():
    url = E("CODERAG_LLM_URL")
    if not url: return None
    return Chat(url, E("CODERAG_LLM_MODEL", ""), E("CODERAG_LLM_REVISION", ""), float(E("CODERAG_LLM_TIMEOUT", "120")),
                int(E("CODERAG_LLM_MAX_TOKENS", "2048")), E("CODERAG_LLM_JSON_MODE", "0") == "1",
                E("CODERAG_LLM_REASONING_EFFORT", "low") or None)
