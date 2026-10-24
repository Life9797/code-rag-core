"""M4 Open WebUI filter + tool (§29, §42) against the real API over ASGI: CH-01, 04, 06, 07, 09, 11, 12."""
import asyncio, importlib.util, os, uuid
import httpx, pytest
from conftest import connect, rid, DB
from coderag_core import api, auth, runner, storage
from coderag_core.coreadm import issue_client
from coderag_core.worker import Worker, SqlBinding
from plugins.code import main as code
from test_m1b import stop_project_jobs

spec = importlib.util.spec_from_file_location("owui", os.path.join(os.path.dirname(__file__), "..", "deploy/openwebui/code_rag.py"))
owui = importlib.util.module_from_spec(spec); spec.loader.exec_module(owui)


@pytest.fixture(scope="module")
def env(base, su, call, world, tmp_path_factory):
    old = storage.ROOT
    storage.ROOT = str(tmp_path_factory.mktemp("vol"))
    code._models.clear(); code._models["chat"] = None
    if not su.execute("SELECT core.signing_key_active()").fetchone()[0]:
        auth.rotate_key(su, str(tmp_path_factory.mktemp("keys")), world["root"]); su.commit()
    app = api.create_app(api.Settings(dsn_for=lambda login: f"{base} user={login} dbname={DB}"))
    r, secret = issue_client(su, world["root"], world["A"], "owui", 2)
    tag = uuid.uuid4().hex[:4]
    ps = {}
    for name in ("scratch", f"shop{tag}", f"shop{tag}-v2"):
        p = call.api("core.project_create", world["A.admin"], world["A"], name, rid())["id"]
        call.api("core.plugin_enable", world["A.admin"], p, "code", True, rid())
        assert call.api("core.member_change", world["A.admin"], world["A"], p, r["principal_id"], 2, rid())["outcome"] == "OK"
        ps[name] = p
    v = owui.Valves(CORE_URL="http://t", CLIENT_ID=r["client_id"], CLIENT_SECRET=secret, SCRATCH_PROJECT=ps["scratch"], MAX_WAIT_S=0)
    f, t = owui.Filter(httpx.ASGITransport(app=app)), owui.Tools(httpx.ASGITransport(app=app))
    f.valves = t.valves = v
    yield {"f": f, "t": t, "ps": ps, "tag": tag}
    for p in ps.values(): stop_project_jobs(su, p)
    storage.ROOT = old


def drive(base):
    cn = lambda login: connect(base, login)
    with connect(base, "plugin_code") as c:
        w = Worker("code", "0.1.0", code.HANDLERS, SqlBinding(c), db=c)
        for _ in range(20):
            runner.runtime_tick(cn)
            if not w.drain(): runner.runtime_tick(cn); break


def inlet(f, msgs, chat="c1", mid="m1", **kw):
    body = {"messages": [dict(m) for m in msgs], **kw}
    return asyncio.run(f.inlet(body, __metadata__={"chat_id": chat, "message_id": mid}))["messages"][0]["content"]


def tool(t, req, chat="c1"):
    return asyncio.run(t.code_rag(req, __metadata__={"chat_id": chat}))


PASTE = "이거 분석해줘\n```python\ndef refund(order):\n    '''Refund a paid order.'''\n    return order.total\n```"


def test_ch01_ch06_ch09_paste_status_replay(base, env):
    n1 = inlet(env["f"], [{"role": "user", "content": PASTE}])
    assert "진행 중" in n1 and "job " in n1                              # MAX_WAIT_S=0: not done yet
    assert inlet(env["f"], [{"role": "user", "content": PASTE}]) == n1    # CH-09: resent message -> same job
    drive(base)
    st = tool(env["t"], "끝났어?")
    assert "succeeded" in st and n1.split("job ")[1].split(")")[0] in st  # CH-06
    hits = tool(env["t"], "refund paid order")
    assert "pasted/1.py" in hits and "refund" in hits
    assert tool(env["t"], "refund paid order", chat="other") == owui.NONE   # ST-25: other chat sees nothing


def test_ch07_language_question_then_answer(base, env):
    code_txt = "```\nint add(int a, int b) { return a + b; }\n```"
    n = inlet(env["f"], [{"role": "user", "content": code_txt}], chat="c7")
    assert owui.ASK in n
    msgs = [{"role": "user", "content": code_txt}, {"role": "assistant", "content": owui.ASK + " python"}, {"role": "user", "content": "파이썬"}]
    assert "job " in inlet(env["f"], msgs, chat="c7", mid="m2")


def test_ch04_two_projects_named_and_ch12_context_cap(base, env):
    tag = env["tag"]
    n = inlet(env["f"], [{"role": "user", "content": f"shop{tag} 랑 shop{tag}-v2 에서 refund 찾아줘"}])
    assert "어느 프로젝트" in n and f"shop{tag}-v2" in n
    # save into a named project, then a question naming it gets capped search context
    inlet(env["f"], [{"role": "user", "content": f"shop{tag}-v2 에 저장해줘\n" + PASTE.split("\n", 1)[1]}], chat="c12")
    drive(base)
    env["f"].valves.CONTEXT_MAX_CHARS = 120
    n = inlet(env["f"], [{"role": "user", "content": f"shop{tag}-v2 refund 어디 있어?"}], chat="c12", mid="q")
    assert n.startswith("[code-rag]") and len(n) <= len("[code-rag]\n") + 120
    env["f"].valves.CONTEXT_MAX_CHARS = 12000


def test_ch11_attachment_too_large(env, tmp_path):
    p = tmp_path / "big.py"; p.write_text("x = 1\n")
    env["f"].valves.MAX_ATTACH_MB = 0
    try:
        n = inlet(env["f"], [{"role": "user", "content": "저장해줘"}], files=[{"name": "big.py", "path": str(p)}])
    finally:
        env["f"].valves.MAX_ATTACH_MB = 50
    assert owui.KO["TOO_LARGE"] in n


def test_tool_uploads_paste_when_filter_off(base, env):
    msgs = [{"role": "user", "content": PASTE.replace("refund", "rebate")}]
    out = asyncio.run(env["t"].code_rag("분석해줘", __metadata__={"chat_id": "c13", "message_id": "m1"}, __messages__=msgs))
    assert "job " in out
    drive(base)
    assert "rebate" in tool(env["t"], "rebate paid order", chat="c13")


def test_tool_creates_project(env, su, world):
    name = f"new{env['tag']}"
    assert "만들었습니다" not in tool(env["t"], f"x{name} 프로젝트 만들어줘")         # contributor client: FORBIDDEN
    r, secret = issue_client(su, world["root"], world["A"], "owui-admin", 4)
    t = owui.Tools(env["t"].transport); t.valves = env["t"].valves.model_copy(update={"CLIENT_ID": r["client_id"], "CLIENT_SECRET": secret})
    env = {**env, "t": t}
    assert "만들었습니다" in tool(env["t"], f"{name} 프로젝트 만들어줘")
    assert owui.KO["NAME_TAKEN"] in tool(env["t"], f"{name} 프로젝트 만들어줘", chat="c2")
    assert name in tool(env["t"], "프로젝트 목록 보여줘")
    assert "이름" in tool(env["t"], "새 프로젝트 만들어줘")
