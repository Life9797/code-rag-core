-- Grants (I-S1, I-S2, R8). Re-run by init after every Core migration. No table privileges for any login.
REVOKE ALL ON ALL TABLES IN SCHEMA core, runtime, knowledge FROM PUBLIC;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA core, runtime, knowledge FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA core, runtime, knowledge FROM PUBLIC;
GRANT USAGE ON SCHEMA core, runtime, knowledge TO api, runtime, purge, maint, plugin_worker;

-- user functions (api)
GRANT EXECUTE ON FUNCTION
    core.tenant_create(uuid, text, uuid),
    core.principal_create(uuid, uuid, text, text, int, text, text, uuid),
    core.project_create(uuid, uuid, text, uuid),
    core.member_change(uuid, uuid, uuid, uuid, int, uuid),
    core.plugin_enable(uuid, uuid, text, boolean, uuid),
    core.plugin_disable(uuid, text, text, uuid),
    core.service_client_create(uuid, uuid, text, int, text[], text[], timestamptz, text, uuid),
    core.service_client_revoke(uuid, uuid, uuid),
    core.get_client_salt(uuid), core.verify_and_issue(uuid, text), core.client_active(uuid),
    core.jwks(), core.signing_key_active(),
    core.signing_key_rotate(uuid, text, jsonb, text),
    core.artifact_authorize_download(uuid, uuid),
    core.source_create(uuid, uuid, text, text, boolean, uuid),
    core.binding_set(uuid, uuid, text, text, jsonb, uuid),
    core.policy_set(uuid, text, text, text, jsonb, uuid)
TO api;

-- maintenance
GRANT EXECUTE ON FUNCTION core.signing_key_rotate(uuid, text, jsonb, text) TO maint;

-- runtime: user contracts (api), Worker Protocol (plugin logins via plugin_worker), Core runner and maintenance
GRANT EXECUTE ON FUNCTION runtime.create_job(uuid, uuid, text, text, uuid, boolean, uuid), runtime.get_job(uuid, uuid),
    runtime.cancel_job(uuid, uuid, uuid), runtime.retry_job(uuid, uuid, uuid) TO api;
GRANT EXECUTE ON FUNCTION runtime.claim(int, text[], uuid), runtime.heartbeat(uuid, uuid, bigint, text, boolean),
    runtime.complete(uuid, uuid, bigint, jsonb, uuid), runtime.fail(uuid, uuid, bigint, text, jsonb, uuid),
    runtime.split(uuid, uuid, bigint, jsonb, uuid) TO plugin_worker;
GRANT EXECUTE ON FUNCTION runtime.start_jobs(int), runtime.sweep(int), runtime.finish_stopped_jobs(uuid) TO runtime, maint;
GRANT EXECUTE ON FUNCTION runtime.breaker_eval() TO runtime, maint;
