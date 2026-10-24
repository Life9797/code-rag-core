"""M1a: security tests (§19 ST-xx, §34 ST-21..29) and management tests (MG-T1..3, replay) under real logins."""
import copy, json, os, re, time, uuid
import psycopg, pytest
from conftest import connect, rid
from coderag_core import auth, manifest as mf, initdb
from coderag_core.coreadm import issue_client

LOGINS = ("api", "runtime", "purge", "maint", "plugin_echo", "plugin_tst")


def tables(su):
    return su.execute("SELECT format('%I.%I', schemaname, tablename) FROM pg_tables "
                      "WHERE schemaname IN ('core', 'runtime', 'knowledge')").fetchall()


@pytest.mark.parametrize("login", LOGINS)
def test_st01_no_table_privileges(base, su, login):
    with connect(base, login) as c:
        for (t,) in tables(su):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                c.execute(f"SELECT 1 FROM {t} LIMIT 1")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("INSERT INTO core.tenants (name) VALUES ('x')")


def test_st01_only_granted_functions(base, su):
    # the internal helpers carry no grant: api cannot call them directly
    with connect(base, "api") as c, pytest.raises(psycopg.errors.InsufficientPrivilege):
        c.execute("SELECT core.req_save(NULL, NULL, 'x', '{}', '{}')")
    with connect(base, "plugin_echo") as c, pytest.raises(psycopg.errors.InsufficientPrivilege):
        c.execute("SELECT core.tenant_create(NULL, 'x', gen_random_uuid())")


def test_st02_cross_tenant(call, world):
    w = world
    assert call.api("core.project_create", w["A.admin"], w["B"], "x", rid())["outcome"] == "FORBIDDEN"
    assert call.api("core.source_create", w["A.admin"], w["B.p"], "core.upload", "s", False, rid())["outcome"] == "FORBIDDEN"
    assert call.api("core.member_change", w["A.admin"], w["B"], w["B.p"], w["A.reader"], 1, rid())["outcome"] == "FORBIDDEN"
    assert call.api("core.member_change", w["A.admin"], w["B"], None, w["A.reader"], 1, rid())["outcome"] == "FORBIDDEN"
    # token tenant claims are never used: a B project with A's tenant id is still refused (I-S3)
    assert call.api("core.member_change", w["A.admin"], w["A"], w["B.p"], w["A.reader"], 1, rid())["outcome"] == "FORBIDDEN"


def test_st04_unknown_project_same_as_forbidden(call, world):
    assert call.api("core.source_create", world["A.admin"], uuid.uuid4(), "core.upload", "s", False, rid()) == {"outcome": "FORBIDDEN"}


def test_st05_project_member_needs_tenant_member(su, world):
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        with su.transaction():
            su.execute("INSERT INTO core.project_members VALUES (%s, %s, %s, 1)", (world["A"], world["A.p"], world["B.reader"]))


def test_st05_contract_refuses(call, world):
    w = world
    r = call.api("core.member_change", w["A.admin"], w["A"], w["A.p"], w["B.reader"], 1, rid())
    assert r["outcome"] == "NOT_TENANT_MEMBER"


def test_st06_plugin_logins_own_schema_only(base):
    with connect(base, "plugin_echo") as c:
        c.execute("INSERT INTO plugin_echo.calls (task_id) VALUES (gen_random_uuid())")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("SELECT * FROM plugin_tst.secret_notes")
    with connect(base, "plugin_tst") as c, pytest.raises(psycopg.errors.InsufficientPrivilege):
        c.execute("SELECT * FROM plugin_echo.calls")
    with connect(base, "api") as c, pytest.raises(psycopg.errors.InsufficientPrivilege):
        c.execute("SELECT * FROM plugin_echo.calls")


@pytest.mark.parametrize("stmt", ["UPDATE core.audit SET action = 'x'", "DELETE FROM core.audit", "TRUNCATE core.audit"])
def test_st08_audit_append_only(su, world, stmt):
    with pytest.raises(psycopg.errors.RaiseException):
        with su.transaction(): su.execute(stmt)


def test_audit_written_with_change(su, call, world):
    n0 = su.execute("SELECT count(*) FROM core.audit WHERE action = 'source_create'").fetchone()[0]
    assert call.api("core.source_create", world["A.contrib"], world["A.p"], "core.upload", "aud", False, rid())["outcome"] == "CREATED"
    assert su.execute("SELECT count(*) FROM core.audit WHERE action = 'source_create'").fetchone()[0] == n0 + 1


@pytest.fixture(scope="module")
def tokens(su, base, world, tmp_path_factory):
    keys = str(tmp_path_factory.mktemp("keys"))
    auth.rotate_key(su, keys, world["root"])
    r, secret = issue_client(su, world["root"], world["A"], "owui", 1)
    return {"client": r["client_id"], "principal": r["principal_id"], "secret": secret, "keys": keys}


def test_st10_tokens(base, su, world, tokens):
    with connect(base, "api") as api:
        tok = auth.issue_token(api, tokens["client"], tokens["secret"])
        assert auth.verify_token(api, tok)["sub"] == tokens["principal"]
        assert auth.verify_token(api, auth.issue_token(api, tokens["client"], tokens["secret"], ttl=-10)) is None  # expired
        import jwt
        claims = jwt.decode(tok, options={"verify_signature": False})
        key = api.execute("SELECT core.signing_key_active()").fetchone()[0]
        pk = open(key["private_ref"], "rb").read()
        assert auth.verify_token(api, jwt.encode({**claims, "aud": "other"}, pk, "ES256", headers={"kid": key["kid"]})) is None
        assert auth.verify_token(api, jwt.encode(claims, pk, "ES256", headers={"kid": "nope"})) is None
        # rotation: the old key still verifies (retiring), new tokens use the new kid
        auth.rotate_key(su, tokens["keys"], world["root"])
        assert auth.verify_token(api, tok) is not None
        r2, s2 = issue_client(su, world["root"], world["A"], "tmp", 1)
        tok2 = auth.issue_token(api, r2["client_id"], s2)
        assert jwt.get_unverified_header(tok2)["kid"] != jwt.get_unverified_header(tok)["kid"]
        assert call_revoke(api, world["root"], r2["client_id"]) == "OK"
        assert auth.verify_token(api, tok2) is None                                      # revoked -> 401 at once


def call_revoke(api, admin, client):
    return api.execute("SELECT core.service_client_revoke(%s, %s, %s)", (admin, client, rid())).fetchone()[0]["outcome"]


def test_st11_membership_removed_after_token(base, call, world, tokens):
    w = world
    p = call.api("core.project_create", w["A.admin"], w["A"], "st11", rid())["id"]
    assert call.api("core.member_change", w["A.admin"], w["A"], p, tokens["principal"], 2, rid())["outcome"] == "OK"
    with connect(base, "api") as api:
        claims = auth.verify_token(api, auth.issue_token(api, tokens["client"], tokens["secret"]))
    sub = claims["sub"]
    assert call.api("core.source_create", sub, p, "core.upload", "a", False, rid())["outcome"] == "CREATED"
    assert call.api("core.member_change", w["A.admin"], w["A"], p, sub, None, rid())["outcome"] == "OK"
    assert call.api("core.source_create", sub, p, "core.upload", "b", False, rid())["outcome"] == "FORBIDDEN"


def test_st18_artifact_after_revoke(su, call, world):
    w = world
    a = su.execute("INSERT INTO core.artifacts (tenant_id, project_id, kind, storage_ref, sha256, created_by) "
                   "VALUES (%s, %s, 'echo.x', 'vol://v1/a', 'ab', %s) RETURNING id", (w["A"], w["A.p"], w["A.admin"])).fetchone()[0]
    su.commit()
    x = call.api("core.principal_create", w["A.admin"], w["A"], "human", "tmp-reader", 1, None, None, rid())["id"]
    call.api("core.member_change", w["A.admin"], w["A"], w["A.p"], x, 1, rid())
    assert call.api("core.artifact_authorize_download", x, a)["outcome"] == "OK"
    assert call.api("core.artifact_authorize_download", w["B.admin"], a)["outcome"] == "FORBIDDEN"
    call.api("core.member_change", w["A.admin"], w["A"], w["A.p"], x, None, rid())
    assert call.api("core.artifact_authorize_download", x, a)["outcome"] == "FORBIDDEN"


def test_st19_same_version_other_manifest(su):
    m = mf.load(os.path.join(os.path.dirname(__file__), "plugins/echo"))
    row = su.execute("SELECT manifest_hash, dir_hash FROM core.plugins WHERE id = 'echo'").fetchone()
    assert su.execute("SELECT core.plugin_register(%s::jsonb, %s, %s)", (mf.canonical(m), *row)).fetchone()[0]["outcome"] == "EXISTS"
    m2 = copy.deepcopy(m); m2["error_codes"].append({"key": "other", "retryable": False, "health_sample": False})
    r = su.execute("SELECT core.plugin_register(%s::jsonb, %s, %s)", (mf.canonical(m2), mf.manifest_hash(m2), row[1])).fetchone()[0]
    assert r["outcome"] == "PLUGIN_VERSION_CONFLICT"
    r = su.execute("SELECT core.plugin_register(%s::jsonb, %s, %s)", (mf.canonical(m), row[0], "changed-dir")).fetchone()[0]
    assert r["outcome"] == "PLUGIN_VERSION_CONFLICT"                                   # D-32: folder changed
    su.rollback()


FORBIDDEN_TOKENS = {"symbol", "symbols", "file", "files", "repo", "repository", "ast", "parser", "language", "embedding"}


def test_st20_no_code_names_in_core(su):
    names = su.execute("""
        SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname IN ('core','runtime','knowledge')
        UNION SELECT a.attname FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid JOIN pg_namespace n ON n.oid = c.relnamespace
              WHERE n.nspname IN ('core','runtime','knowledge') AND a.attnum > 0
        UNION SELECT p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace WHERE n.nspname IN ('core','runtime','knowledge')
    """).fetchall()
    bad = [n for (n,) in names if FORBIDDEN_TOKENS & set(n.split("_"))]
    assert bad == []


def test_st21_wrong_secret_same_timing_class(base, tokens):
    with connect(base, "api") as api:
        def t(client, secret):
            t0 = time.perf_counter(); r = auth.issue_token(api, client, secret); return r, time.perf_counter() - t0
        r1, d1 = t(tokens["client"], "wrong")
        r2, d2 = t(str(uuid.uuid4()), "wrong")
        assert r1 is None and r2 is None
        assert 0.33 < d1 / d2 < 3                                                        # argon2 runs for unknown ids too


def test_st22_operator_other_project(call, world):
    w = world
    p2 = call.api("core.project_create", w["A.admin"], w["A"], "st22", rid())["id"]
    assert call.api("core.member_change", w["A.op"], w["A"], p2, w["A.reader"], 1, rid())["outcome"] == "FORBIDDEN"


def test_st23_plugin_migration_cannot_touch_core(base, tmp_path):
    f = tmp_path / "002_evil.sql"; f.write_text("CREATE TABLE core.evil (id int)")
    with psycopg.connect(f"{base} user=postgres dbname={os.environ.get('CRC_TEST_DB', 'crc_test')}", autocommit=True) as c:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            initdb._migrate(c, "echo", "plugin_echo_owner", "plugin_echo, pg_catalog, ext", [(2, str(f))], lambda *_: None)


def test_st29_owui_contributor_cannot_manage(call, world):
    w = world
    owui = call.api("core.principal_create", w["A.admin"], w["A"], "service", "owui-29", 1, None, None, rid())["id"]
    call.api("core.member_change", w["A.admin"], w["A"], w["A.p"], owui, 2, rid())
    assert call.api("core.member_change", owui, w["A"], w["A.p"], w["A.reader"], 2, rid())["outcome"] == "FORBIDDEN"
    assert call.api("core.policy_set", owui, "project", str(w["A.p"]), "knowledge.search.max_k", "10", rid())["outcome"] == "FORBIDDEN"
    assert call.api("core.project_create", owui, w["A"], "x29", rid())["outcome"] == "FORBIDDEN"
    assert call.api("core.source_create", owui, w["A.p"], "core.upload", "chat:1", False, rid())["outcome"] == "CREATED"


def test_mgt1_last_admin(call, world):
    w = world
    p = call.api("core.project_create", w["A.admin"], w["A"], "mgt1", rid())["id"]
    assert call.api("core.member_change", w["A.admin"], w["A"], p, w["A.admin"], 3, rid())["outcome"] == "LAST_ADMIN"
    assert call.api("core.member_change", w["A.admin"], w["A"], p, w["A.admin"], None, rid())["outcome"] == "LAST_ADMIN"
    assert call.api("core.member_change", w["root"], w["A"], None, w["A.admin"], 3, rid())["outcome"] == "LAST_ADMIN"


def test_mgt2_operator_cannot_grant_admin(call, world):
    w = world
    assert call.api("core.member_change", w["A.op"], w["A"], w["A.p"], w["A.reader"], 4, rid())["outcome"] == "FORBIDDEN"
    # a project admin who is not tenant admin can grant up to admin in its project only
    x = call.api("core.principal_create", w["A.admin"], w["A"], "human", "padm", 1, None, None, rid())["id"]
    call.api("core.member_change", w["A.admin"], w["A"], w["A.p"], x, 4, rid())
    assert call.api("core.member_change", x, w["A"], None, w["A.reader"], 2, rid())["outcome"] == "FORBIDDEN"


def test_mgt3_policy_constraint_keeps_old_value(su, call, world):
    root = world["root"]
    assert call.api("core.policy_set", root, "global", "", "runtime.heartbeat", json.dumps("90 seconds"), rid())["outcome"] == "CONSTRAINT_VIOLATION"
    assert su.execute("SELECT value FROM core.policies WHERE scope='global' AND key='runtime.heartbeat'").fetchone()[0] == "30 seconds"
    assert call.api("core.policy_set", root, "global", "", "runtime.heartbeat", json.dumps("40 seconds"), rid())["outcome"] == "OK"
    assert call.api("core.policy_set", root, "global", "", "runtime.max_attempts", "0", rid())["outcome"] == "CONSTRAINT_VIOLATION"
    assert call.api("core.policy_set", root, "global", "", "nope.key", "1", rid())["outcome"] == "UNKNOWN_KEY"
    # project override checked against global values
    w = world
    assert call.api("core.policy_set", w["A.admin"], "project", str(w["A.p"]), "knowledge.search.max_k", "100", rid())["outcome"] == "CONSTRAINT_VIOLATION"
    assert call.api("core.policy_set", w["A.admin"], "project", str(w["A.p"]), "knowledge.search.max_k", "30", rid())["outcome"] == "OK"
    # a global change that would break that override is refused too
    assert call.api("core.policy_set", root, "global", "", "knowledge.search.candidate_k", "25", rid())["outcome"] == "CONSTRAINT_VIOLATION"
    su.rollback()


def test_replay_and_reuse(call, world):
    w = world; r = rid()
    first = call.api("core.source_create", w["A.contrib"], w["A.p"], "core.upload", "rp", False, r)
    again = call.api("core.source_create", w["A.contrib"], w["A.p"], "core.upload", "rp", False, r)
    assert again["id"] == first["id"] and again["replay"] is True
    assert call.api("core.source_create", w["A.contrib"], w["A.p"], "core.upload", "rp2", False, r)["outcome"] == "REQUEST_ID_REUSED"


def test_roles_reader_contributor(call, world):
    w = world
    assert call.api("core.source_create", w["A.reader"], w["A.p"], "core.upload", "r", False, rid())["outcome"] == "FORBIDDEN"
    assert call.api("core.binding_set", w["A.contrib"], w["A.p"], "core.upload", "x", "{}", rid())["outcome"] == "FORBIDDEN"
    assert call.api("core.binding_set", w["A.op"], w["A.p"], "core.upload", "x", "{}", rid())["outcome"] == "OK"
    # tenant admin is admin in every project of its tenant without a project row; tenant operator is not
    p = call.api("core.project_create", w["A.op"], w["A"], "byop", rid())["id"]
    assert call.api("core.plugin_enable", w["A.admin"], p, "echo", True, rid())["outcome"] == "OK"
    assert call.api("core.plugin_enable", w["A.op"], w["A.p"], "echo", True, rid())["outcome"] == "FORBIDDEN"


@pytest.mark.parametrize("change,err", [
    (lambda m: m["pipelines"][0]["stages"][0].update(depends_on=["publish"]), "cycle"),
    (lambda m: m["pipelines"][0]["stages"][0].update(handler="nope"), "not declared"),
    (lambda m: m.update(extra=1), "Additional properties"),
    (lambda m: m["pipelines"][0].update(collection="zzz"), "not declared"),
    (lambda m: m["representation_kinds"][0].pop("dims"), "dims required"),
])
def test_manifest_refused(change, err):
    m = mf.load(os.path.join(os.path.dirname(__file__), "plugins/echo")); change(m)
    with pytest.raises(mf.ManifestError, match=err): mf.check(m)
