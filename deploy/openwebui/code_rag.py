"""
title: code-rag
description: code-rag v0.4 for Open WebUI. One file holds the Filter (pasted code, attachments, search context) and the Tool code_rag(request).
version: 0.4.0
requirements: httpx
"""
# Install this same file twice (§42): Workspace > Functions > + (Filter; attach it to the code-rag model and turn off
# Open WebUI's own file retrieval for that model) and Workspace > Tools > + (Tool). Valves: CORE_URL, CLIENT_ID,
# CLIENT_SECRET (a contributor service client, D-31), SCRATCH_PROJECT (project id). User valve: DEFAULT_PROJECT.
# Pasted-code extraction is ported from code-rag v0.2.4 deploy/openwebui/code_rag_tool.py (code_blocks, pasted_files).
import asyncio, base64, glob, hashlib, io, json, os, re, time, zipfile
import httpx
from pydantic import BaseModel, Field

OPEN = re.compile(r"^\s*(`{3,}|~{3,})\s*([^`]*)$")
PATH = re.compile(r"^[\w.\-/]+\.[A-Za-z0-9]+$")
PY_HINT = re.compile(r"^\s*(def |class |import |from \S+ import |async def |@\w)", re.M)
LANGS = {"python": ("py", {"python", "py", "python3", "파이썬"})}        # languages the code plugin registers (§39)
KNOWN = {"java", "javascript", "js", "typescript", "ts", "go", "rust", "c", "cpp", "c++", "csharp", "sql", "bash", "sh", "ruby", "php"}
SAVE = re.compile(r"저장|등록|반영|인덱스|\bsave\b|\bregister\b|\bindex\b", re.I)
STATUS = re.compile(r"끝났|상태|진행|\bstatus\b|\bdone\b|finished", re.I)
LIST = re.compile(r"프로젝트\s*(목록|리스트)|list projects", re.I)
ASK = "이 코드는 어떤 언어인가요?"
KO = {"FORBIDDEN": "이 프로젝트에 접근 권한이 없습니다", "INPUT_INVALID": "입력 형식이 올바르지 않습니다",
      "PROJECT_DELETING": "삭제 중인 프로젝트입니다", "TOO_LARGE": "첨부 파일이 너무 큽니다", "NO_PROJECT": "사용할 프로젝트가 없습니다",
      "UNSUPPORTED": "지금은 python 코드만 분석합니다", "ERROR": "code-rag 요청이 실패했습니다"}
UPLOADS = "/app/backend/data/uploads"


class Valves(BaseModel):
    CORE_URL: str = Field("http://coderag-api:8080", description="code-rag Core API")
    CLIENT_ID: str = ""
    CLIENT_SECRET: str = ""
    SCRATCH_PROJECT: str = Field("", description="project id for per-chat scratch sources (D-31)")
    MAX_WAIT_S: int = 60
    MAX_ATTACH_MB: int = 50
    CONTEXT_MAX_CHARS: int = 12000                                       # code.chat.context_max_chars START (§41)
    SEARCH_K: int = 8


class UserValves(BaseModel):
    DEFAULT_PROJECT: str = Field("", description="project name or id used when the message names none")


# ---------- pasted code ----------
def code_blocks(text):
    out, lines, i = [], text.split("\n"), 0
    while i < len(lines):
        m = OPEN.match(lines[i])
        if not m:
            i += 1; continue
        fence, info, body = m.group(1), m.group(2).strip(), []
        i += 1
        while i < len(lines) and not (set(lines[i].strip()) == {fence[0]} and len(lines[i].strip()) >= len(fence)):
            body.append(lines[i]); i += 1
        out.append((info, "\n".join(body))); i += 1
    return out


def lang_of(word):
    w = (word or "").lower()
    return next((k for k, (_, names) in LANGS.items() if w in names), "unsupported" if w in KNOWN else None)


def pasted_files(text, forced=None):
    """[{path, text, lang}] from fenced blocks, else from the first code-looking line; lang None = unclear (D-28)."""
    blocks = code_blocks(text)
    if not blocks:
        m = PY_HINT.search(text)
        if m is None and not forced: return []
        blocks = [("", text[text.rfind("\n", 0, m.start()) + 1:] if m else text)]
    out = []
    for n, (info, body) in enumerate(blocks, 1):
        if not body.strip(): continue
        words = info.split()
        path = next((w for w in words if PATH.match(w)), None)
        lang = forced or (lang_of(os.path.splitext(path)[1][1:]) if path else None) or (lang_of(words[0]) if words else None) \
            or ("python" if PY_HINT.search(body) else None)
        ext = LANGS[lang][0] if lang in LANGS else "txt"
        out.append({"path": path or f"pasted/{n}.{ext}", "text": body, "lang": lang})
    return out


def last_user(msgs):
    for m in reversed(msgs or []):
        if m.get("role") == "user":
            c = m.get("content")
            return c if isinstance(c, str) else " ".join(x.get("text", "") for x in c or [] if isinstance(x, dict))
    return ""


def held(msgs):
    """Resume a paste held by the language question: [..., user code, assistant ASK, user answer]."""
    if len(msgs) >= 3 and msgs[-2].get("role") == "assistant" and ASK in str(msgs[-2].get("content")):
        lang = next((k for k, (_, names) in LANGS.items() if any(n in last_user(msgs).lower() for n in names)), None)
        if lang: return pasted_files(last_user(msgs[:-2]), forced=lang)
    return []


def attachments(body, meta):
    out = []
    for f in (body.get("files") or []) + (meta.get("files") or []):
        info = f.get("file") if isinstance(f.get("file"), dict) else f
        fid, name = info.get("id") or f.get("id"), info.get("filename") or info.get("name") or f.get("name")
        path = info.get("path") or (info.get("meta") or {}).get("path")
        if not path or not os.path.isfile(path):
            hits = glob.glob(os.path.join(UPLOADS, f"{fid}_*")) if fid else []
            path = hits[0] if hits else None
        if path and os.path.isfile(path) and (name or path) not in [n for n, _ in out]: out.append((name or os.path.basename(path), path))
    return out


def sha(*parts): return hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()


# ---------- Core client ----------
class Client:
    _tokens = {}

    def __init__(self, v, transport=None):
        self.v, self.http = v, httpx.AsyncClient(base_url=v.CORE_URL.rstrip("/"), timeout=120, transport=transport)

    async def _token(self, renew=False):
        key = (self.v.CORE_URL, self.v.CLIENT_ID)
        tok, exp = self._tokens.get(key, (None, 0))
        if renew or not tok or exp - 60 < time.time():
            r = await self.http.post("/core/v1/token", data={"client_id": self.v.CLIENT_ID, "client_secret": self.v.CLIENT_SECRET})
            r.raise_for_status()
            tok = r.json()["access_token"]
            pl = tok.split(".")[1]
            exp = json.loads(base64.urlsafe_b64decode(pl + "=" * (-len(pl) % 4)))["exp"]
            self._tokens[key] = (tok, exp)
        return tok

    async def req(self, method, path, **kw):
        for renew in (False, True):                                     # CH-10: one renewal on 401
            r = await self.http.request(method, path, headers={"Authorization": f"Bearer {await self._token(renew)}"}, **kw)
            if r.status_code != 401: break
        try: return r.json()
        except ValueError: return {"outcome": "ERROR", "status": r.status_code}

    async def projects(self):
        return (await self.req("GET", "/core/v1/projects")).get("projects", [])

    async def resolve(self, text, uv):
        """named in the text -> user default -> None; two equal matches -> ('ask', names) (CH-04)."""
        ps = await self.projects()
        named = [p for p in ps if re.search(r"(?<![\w-])" + re.escape(p["name"].lower()) + r"(?![\w-])", text.lower())]
        if len(named) > 1: return "ask", [p["name"] for p in named]
        if named: return named[0], None
        d = (uv.DEFAULT_PROJECT if uv else "").strip()
        return next((p for p in ps if d and d in (p["id"], p["name"])), None), None

    async def source(self, project, ext_id):
        r = await self.req("POST", f"/core/v1/projects/{project}/sources", json={"external_id": ext_id, "request_id": sha("src", project, ext_id)})
        return r.get("id"), r

    async def search(self, projects, query, source=None, k=8, include=True):
        b = {"projects": projects, "query": query, "k": k, "include_content": include}
        if source: b["filters"] = {"source_ids": [source]}
        return await self.req("POST", "/core/v1/search", json=b)

    async def last_job(self, targets):
        jobs = []
        for p, s in targets:
            jobs += (await self.req("GET", f"/core/v1/projects/{p}/jobs", params={"source": s, "limit": 1})).get("jobs", [])
        return max(jobs, key=lambda j: j["created_at"], default=None)


def render(hits, cap):
    out, used = [], 0
    for h in hits:
        a, ch = h.get("attrs", {}), h.get("chunk") or {}
        sp = ch.get("span") or {}
        block = (f"### {a.get('path')}:{sp.get('start_line', a.get('start_line', ''))}-{sp.get('end_line', a.get('end_line', ''))}"
                 f" ({a.get('symbol', '')})\n" + (f"요약: {a.get('summary_ko') or (ch.get('attrs') or {}).get('summary_ko', '')}\n")
                 + f"```\n{ch.get('text', '')}\n```")
        if used + len(block) > cap:
            if not out: out.append(block[:cap])
            break
        out.append(block); used += len(block) + 2
    return "\n\n".join(out)


def note(body, text):
    """Inject a system note for the model (one Korean line for errors)."""
    msgs = body.setdefault("messages", [])
    if msgs and msgs[0].get("role") == "system": msgs[0]["content"] = f"{msgs[0]['content']}\n\n[code-rag]\n{text}"
    else: msgs.insert(0, {"role": "system", "content": f"[code-rag]\n{text}"})
    return body


async def say(emit, text, done=False):
    if emit: await emit({"type": "status", "data": {"description": text, "done": done}})


class Filter:
    Valves, UserValves = Valves, UserValves

    def __init__(self, transport=None):
        self.valves, self.transport = Valves(), transport

    async def inlet(self, body, __user__=None, __metadata__=None, __event_emitter__=None):
        meta, uv = __metadata__ or {}, (__user__ or {}).get("valves")
        msgs = body.get("messages", [])
        text, chat = last_user(msgs), meta.get("chat_id") or "local"
        mid = meta.get("message_id") or sha(text)
        files, atts = pasted_files(text) or held(msgs), attachments(body, meta)
        c = Client(self.valves, self.transport)
        try:
            if any(os.path.getsize(p) > self.valves.MAX_ATTACH_MB << 20 for _, p in atts):   # CH-11
                return note(body, KO["TOO_LARGE"] + f" (최대 {self.valves.MAX_ATTACH_MB} MB)")
            if not files and not atts:                                    # question -> search context
                proj, ask = await c.resolve(text, uv)
                if ask: return note(body, f"사용자에게 이렇게만 물어보세요: 어느 프로젝트인가요? {' / '.join(ask)}")
                if proj:
                    r = await c.search([proj["id"]], text, k=self.valves.SEARCH_K)
                    if r.get("hits"): note(body, render(r["hits"], self.valves.CONTEXT_MAX_CHARS))
                return body
            if any(f["lang"] is None for f in files):                      # D-28: one short question, paste held
                return note(body, f"사용자에게 이렇게만 물어보세요: {ASK} {' / '.join(LANGS)}")
            if files and all(f["lang"] == "unsupported" for f in files) and not atts:
                return note(body, KO["UNSUPPORTED"])
            proj, ask = await c.resolve(text, uv)
            if ask: return note(body, f"사용자에게 이렇게만 물어보세요: 어느 프로젝트인가요? {' / '.join(ask)}")
            pid, ext = (proj["id"], f"owui:{chat}") if proj else (self.valves.SCRATCH_PROJECT, f"chat:{chat}")
            if not pid: return note(body, KO["NO_PROJECT"])
            src, sr = await c.source(pid, ext)
            if not src: return note(body, KO.get(sr.get("outcome"), KO["ERROR"]))
            await say(__event_emitter__, "code-rag: 업로드 중")
            rid = sha(chat, mid, "upload")                                 # CH-09: a resent message replays
            if atts:
                buf = io.BytesIO()
                with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                    for name, path in atts:
                        if name.lower().endswith(".zip"):
                            with zipfile.ZipFile(path) as inner:
                                for i in inner.infolist():
                                    if not i.is_dir(): z.writestr(f"{name[:-4]}/{i.filename}", inner.read(i))
                        else: z.write(path, name)
                    for f in files: z.writestr(f["path"], f["text"])
                r = await c.req("POST", f"/core/v1/sources/{src}/revisions", files={"archive": ("chat.zip", buf.getvalue())},
                                data={"plugin": "code", "request_id": rid})
            else:
                r = await c.req("POST", f"/core/v1/sources/{src}/revisions", json={
                    "files": [{"path": f["path"], "text": f["text"]} for f in files], "plugin": "code", "request_id": rid})
            job = (r.get("job") or {}).get("job_id")
            if not job: return note(body, KO.get(r.get("outcome"), KO["ERROR"]))
            g, end = {}, time.time() + self.valves.MAX_WAIT_S
            while True:
                g = await c.req("GET", f"/core/v1/jobs/{job}")
                if g.get("status") in ("succeeded", "failed", "canceled", "superseded") or time.time() >= end: break
                await say(__event_emitter__, f"code-rag: 분석 중 ({', '.join(f'{k} {v}' for k, v in (g.get('stages') or {}).items())})")
                await asyncio.sleep(2)
            await say(__event_emitter__, f"code-rag: {g.get('status')}", True)
            where = f"프로젝트 {proj['name']}" if proj else "이 대화의 임시 공간"
            if g.get("status") != "succeeded":
                state = "진행 중입니다. '끝났어?'라고 물으면 상태를 알려드립니다" if g.get("status") in ("queued", "running") else f"실패했습니다 ({g.get('outcome')})"
                return note(body, f"{where}에 올린 코드 분석이 {state}. (job {job})")
            if SAVE.search(text): return note(body, f"{where}에 저장했습니다 (job {job}, {g.get('outcome')}).")
            hits = (await c.search([pid], text, source=src, k=self.valves.SEARCH_K)).get("hits") or []
            return note(body, f"방금 분석한 코드 ({where}):\n" + render(hits, self.valves.CONTEXT_MAX_CHARS))
        except (httpx.HTTPError, KeyError, ValueError):
            return note(body, KO["ERROR"])
        finally:
            await c.http.aclose()


class Tools:
    Valves, UserValves = Valves, UserValves

    def __init__(self, transport=None):
        self.valves, self.transport = Valves(), transport

    async def code_rag(self, request: str, __user__: dict = None, __metadata__: dict = None, __event_emitter__=None) -> str:
        """
        code-rag follow-ups in plain language: job status ("끝났어?"), search again, or list projects.
        :param request: what the user asked, in their words.
        """
        meta, uv = __metadata__ or {}, (__user__ or {}).get("valves")
        chat = meta.get("chat_id") or "local"
        c = Client(self.valves, self.transport)
        try:
            if LIST.search(request):
                return "\n".join(f"- {p['name']}" for p in await c.projects()) or "읽을 수 있는 프로젝트가 없습니다"
            proj, ask = await c.resolve(request, uv)
            if ask: return f"어느 프로젝트인가요? {' / '.join(ask)}"
            targets = []
            for pid, ext in ((proj or {}).get("id"), f"owui:{chat}"), (self.valves.SCRATCH_PROJECT, f"chat:{chat}"):
                if pid:
                    src, _ = await c.source(pid, ext)
                    if src: targets.append((pid, src))
            if STATUS.search(request):
                j = await c.last_job(targets)
                return f"마지막 작업: {j['status']} ({j.get('outcome') or '진행 중'}), job {j['job_id']}" if j else "이 대화에서 시작한 작업이 없습니다"
            out = []
            if proj: out += (await c.search([proj["id"]], request, k=self.valves.SEARCH_K)).get("hits") or []
            for pid, src in targets:
                if pid == self.valves.SCRATCH_PROJECT:                    # ST-25: scratch data only of this chat
                    out += (await c.search([pid], request, source=src, k=self.valves.SEARCH_K)).get("hits") or []
            return render(out, self.valves.CONTEXT_MAX_CHARS) or "결과가 없습니다"
        except (httpx.HTTPError, KeyError, ValueError):
            return KO["ERROR"]
        finally:
            await c.http.aclose()
