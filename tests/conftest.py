"""Test harness: fresh DB per session on the local test server (scripts/testpg.sh), init as superuser,
then every test call opens its own connection as the real login (I-S9)."""
import os, subprocess, uuid
import psycopg, pytest
from coderag_core import initdb

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.environ.get("CRC_TEST_DB", "crc_test")


@pytest.fixture(scope="session")
def base():
    out = subprocess.run(["bash", os.path.join(ROOT, "scripts/testpg.sh")], check=True, capture_output=True, text=True)
    b = out.stdout.strip().replace("user=postgres dbname=postgres", "")
    with psycopg.connect(b + " user=postgres dbname=postgres", autocommit=True) as c:
        c.execute(f"DROP DATABASE IF EXISTS {DB} WITH (FORCE)"); c.execute(f"CREATE DATABASE {DB}")
    initdb.init(f"{b} user=postgres dbname={DB}", os.path.join(ROOT, "tests/plugins"), log=lambda *_: None)
    return b


def connect(base, login, autocommit=True):
    return psycopg.connect(f"{base} user={login} dbname={DB}", autocommit=autocommit)


@pytest.fixture(scope="session")
def su(base):
    with connect(base, "postgres") as c: yield c


class Caller:
    """Calls a contract function as a login on a fresh connection; returns the jsonb result."""
    def __init__(self, base): self.base = base
    def __call__(self, login, fn, *args):
        with connect(self.base, login) as c:
            ph = ", ".join(["%s"] * len(args))
            return c.execute(f"SELECT {fn}({ph})", args).fetchone()[0]
    def api(self, fn, *args): return self("api", fn, *args)


@pytest.fixture(scope="session")
def call(base): return Caller(base)


def rid(): return uuid.uuid4()


@pytest.fixture(scope="session")
def world(su, call):
    """Platform admin, tenants A and B, principals with roles, one project per tenant."""
    from coderag_core.coreadm import bootstrap
    w = {"root": bootstrap(su, "root")}
    for t in ("A", "B"):
        tid = call.api("core.tenant_create", w["root"], f"tenant-{t}-{uuid.uuid4().hex[:6]}", rid())["id"]
        w[t] = tid
        for name, role in (("admin", 4), ("op", 3), ("contrib", 2), ("reader", 1)):
            w[f"{t}.{name}"] = call.api("core.principal_create", w["root"], tid, "human", f"{t}-{name}", role,
                                        None, None, rid())["id"]
        w[f"{t}.p"] = call.api("core.project_create", w[f"{t}.admin"], tid, "proj", rid())["id"]
        for name, role in (("op", 3), ("contrib", 2), ("reader", 1)):
            r = call.api("core.member_change", w[f"{t}.admin"], tid, w[f"{t}.p"], w[f"{t}.{name}"], role, rid())
            assert r["outcome"] == "OK", r
    return w
