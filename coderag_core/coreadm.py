"""coreadm (§28.2 bootstrap): runs as the DB superuser on the host.
  bootstrap NAME                 first platform admin principal (prints its id)
  init-tenant NAME               tenant created by the first platform admin (prints its id)
  rotate-key KEYS_DIR            new JWT signing key (MG-07)
  issue-client ADMIN TENANT NAME ROLE   service client via MG-05 (prints client_id and the secret once)
"""
import json, sys, uuid
import psycopg
from . import auth


def bootstrap(conn, name):
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("INSERT INTO core.principals (kind, display_name) VALUES ('human', %s) RETURNING id", (name,))
        pid = cur.fetchone()[0]
        cur.execute("INSERT INTO core.platform_admins (principal_id) VALUES (%s)", (pid,))
        cur.execute("SELECT core.audit_write(NULL, 'platform_admin_add', %s)", (f"principal:{pid}",))
    return pid


def issue_client(conn, admin, tenant, name, role, scopes=("core",), audiences=(auth.AUD,), expires=None):
    secret, h = auth.new_secret()
    with conn.cursor() as cur:
        cur.execute("SELECT core.service_client_create(%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (admin, tenant, name, role, list(scopes), list(audiences), expires, h, uuid.uuid4()))
        r = cur.fetchone()[0]
    conn.commit()
    return r, secret


if __name__ == "__main__":
    import os
    conn = psycopg.connect(os.environ["CODERAG_ADMIN_DSN"])
    cmd, args = sys.argv[1], sys.argv[2:]
    if cmd == "bootstrap": print(bootstrap(conn, args[0]))
    elif cmd == "init-tenant":
        admin = conn.execute("SELECT principal_id FROM core.platform_admins ORDER BY 1 LIMIT 1").fetchone()[0]
        print(json.dumps(conn.execute("SELECT core.tenant_create(%s, %s, %s)", (admin, args[0], uuid.uuid4())).fetchone()[0])); conn.commit()
    elif cmd == "rotate-key": print(auth.rotate_key(conn, args[0])); conn.commit()
    elif cmd == "issue-client":
        r, s = issue_client(conn, args[0], args[1], args[2], int(args[3]))
        print(json.dumps({**r, "secret": s}))
    else: sys.exit(__doc__)
