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
GRANT EXECUTE ON FUNCTION core.signing_key_rotate(uuid, text, jsonb, text), core.signing_key_active() TO maint;

-- runtime: user contracts (api), Worker Protocol (plugin logins via plugin_worker), Core runner and maintenance
GRANT EXECUTE ON FUNCTION runtime.create_job(uuid, uuid, text, text, uuid, boolean, uuid), runtime.get_job(uuid, uuid),
    runtime.cancel_job(uuid, uuid, uuid), runtime.retry_job(uuid, uuid, uuid) TO api;
GRANT EXECUTE ON FUNCTION runtime.claim(int, text[], uuid), runtime.heartbeat(uuid, uuid, bigint, text, boolean),
    runtime.complete(uuid, uuid, bigint, jsonb, uuid), runtime.fail(uuid, uuid, bigint, text, jsonb, uuid),
    runtime.split(uuid, uuid, bigint, jsonb, uuid) TO plugin_worker;
GRANT EXECUTE ON FUNCTION runtime.start_jobs(int), runtime.sweep(int), runtime.finish_stopped_jobs(uuid) TO runtime, maint;
GRANT EXECUTE ON FUNCTION runtime.breaker_eval() TO runtime, maint;

-- knowledge
GRANT EXECUTE ON FUNCTION knowledge.put_unit(uuid, uuid, bigint, text, text, text, jsonb, jsonb, text),
    knowledge.put_representation(uuid, uuid, bigint, text, text, text, uuid, uuid, text, text),
    knowledge.mark_units(uuid, uuid, bigint, text, text, text[], text),
    runtime.put_job_item(uuid, uuid, bigint, text, text, text, jsonb),
    knowledge.get_values(uuid, uuid, bigint, text, text, text[]), runtime.task_input(uuid, uuid, bigint),
    knowledge.task_search(uuid, uuid, bigint, text, jsonb, jsonb, int) TO plugin_worker;
GRANT EXECUTE ON FUNCTION knowledge.publish(uuid), knowledge.publish_ready(int) TO runtime;
GRANT EXECUTE ON FUNCTION knowledge.delete_units(uuid, uuid, uuid, text, text[], uuid),
    knowledge.pin(uuid, uuid, interval), knowledge.unpin(uuid, uuid),
    knowledge.search(uuid, uuid[], uuid[], text, jsonb, jsonb, int, jsonb, boolean), runtime.job_items(uuid, uuid, text, int),
    core.list_projects(uuid), core.source_info(uuid, uuid, int), runtime.list_jobs(uuid, uuid, uuid, int) TO api;
GRANT EXECUTE ON FUNCTION knowledge.gc(uuid), knowledge.gc_values(uuid, int), knowledge.ensure_indexes() TO maint;

-- lifecycle
GRANT EXECUTE ON FUNCTION core.delete_project(uuid, uuid, uuid), core.restore_project(uuid, uuid, uuid),
    core.revision_create(uuid, uuid, text, jsonb, text, text, boolean, uuid) TO api;
GRANT EXECUTE ON FUNCTION core.purge_step(uuid), core.purge_hook_done(uuid, text), core.purge_due() TO purge;
GRANT EXECUTE ON FUNCTION core.snapshot_retention(int), runtime.finish_done_jobs() TO maint, runtime;
GRANT EXECUTE ON FUNCTION knowledge.gc_all() TO maint;

-- webhooks (G1)
GRANT EXECUTE ON FUNCTION core.webhook_subscribe(uuid, uuid, text, text[], text, bytea, uuid), core.webhook_unsubscribe(uuid, uuid, uuid) TO api;
GRANT EXECUTE ON FUNCTION core.webhook_due(int), core.webhook_mark(bigint, uuid, boolean, int) TO maint;
GRANT EXECUTE ON FUNCTION core.backup_mode() TO maint;

-- approval gate, connector secrets, observability (087)
GRANT EXECUTE ON FUNCTION runtime.approve(uuid, uuid, text, text, text, uuid), core.status(uuid),
    core.connector_secret_set(uuid, uuid, text, text, text, bytea, uuid) TO api;
GRANT EXECUTE ON FUNCTION runtime.approval_tick() TO maint;
GRANT EXECUTE ON FUNCTION core.get_connector_secret(uuid, uuid, bigint, text, text) TO plugin_worker;
GRANT SELECT ON runtime.v_queue, runtime.v_job_durations, runtime.v_breaker_state, knowledge.v_collections,
    knowledge.v_gc_backlog, core.v_plugins TO maint;

-- admin page (088)
GRANT EXECUTE ON FUNCTION core.admin_overview(uuid), core.tenant_detail(uuid, uuid), core.project_detail(uuid, uuid),
    core.principal_for_identity(text, text) TO api;
