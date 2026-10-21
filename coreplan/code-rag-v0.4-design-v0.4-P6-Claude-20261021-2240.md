# code-rag Design v0.4-P6 — RAG Core + Plugins

- Document version: v0.4-P6 (design only, no implementation). Supersedes v0.4-P5 as the reference; P1-P5 stay in `coreplan/` unchanged. Every section is authoritative; each behavior is defined in one place and other sections point to it.
- System version: v0.4. The previous implementation (v0.2.4) stays in the `code-rag` repository.
- Repository: `code-rag-core`.
- Written by: Claude, 2026-10-21 22:40 KST.
- Estimated design progress: specified about 85%, implemented about 5% (DDL only), tested about 3% (§21). Adoption (D-35) sets the reference design; it does not raise the implemented or tested figures.

## 0. How to read this document

Each item has an ID, a status and a basis, so every decision and open point can be traced.

Every item that was PROP or PROP-A in P5 is ADOPTED (D-35), HOLD items stay HOLD (O-02, O-12, O-20, old hwp conversion). Items new in P6 (§30-§34, Appendix B) are PROP-A under D-22. Measured values carried from v0.2.4 (Phase 3 search values) stay PROP, and UNSET values stay UNSET: D-35 adopts design, not values.

| Status | Meaning |
| --- | --- |
| DECIDED | Dy decided it explicitly; the basis gives the time of Dy's message |
| ADOPTED | Adopted by Dy as the reference design (D-35); the evidence labels still say whether it is implemented or tested |
| PROP | Proposed, not adopted (D-14) |
| PROP-A | Proposed and proceeding as proposed under D-22, not individually reviewed by Dy; Dy may revert it |
| HOLD | No proposal yet; on hold under D-22 |
| OPEN | Not decided; needs Dy or a measurement |
| UNSET | A value intentionally left unset until measured |

Basis codes:

- `SRC-1`: Dy's summary "RAG Core 설계 변경 정리" (2026-10-20 21:38 KST), covering analysis of `code-rag-main.zip` and `minicore_websvc-main.zip`. Its 확정 items are carried as DECIDED, its 권장 items as PROP.
- `T hh:mm`: a message by Dy in the design thread at that KST time on 2026-10-20. `T2 hh:mm`: the same on 2026-10-21. This includes design reviews Dy posted in the thread; posted reviews are reference material, not decisions (D-24).
- `R hh:mm` / `R T2 hh:mm`: Claude's design answer in the thread at that time (PROP only).
- Evidence labels: `specified` = written in this document; `implemented` = exists as code or DDL that runs; `tested` = a test was run and passed. An item advances only when that evidence exists.
- `v0.2.4 path:line`: checked in the v0.2.4 code (`code-rag` repository).
- `REASON`: Claude's design reasoning (PROP only).

Working rules for this design (Dy): replies in English (decision items may be Korean); save code and tokens (T 22:23); design only, no code until Dy says so (T 22:24); documents only on request, no change history, file name with time, `Claude`, document version and system version (project instructions).

## 1. Decisions by Dy (DECIDED)

| ID | Decision | Basis |
| --- | --- | --- |
| D-01 | v0.3 and below stay as they are; v0.4 starts the core-based redesign | T 22:04 |
| D-02 | v0.4 is developed in the separate repository `code-rag-core`; nothing from v0.2.4 is carried into it; only the `coreplan/` design files are needed there | T 23:04, 23:05 |
| D-03 | Preserving existing behavior is not a constraint; the golden set is a comparison baseline only | SRC-1 §3.1 |
| D-04 | minicore is a pattern reference only; the RAG Core is designed from scratch for RAG | SRC-1 §3.2, T 22:13 |
| D-05 | First scope: Core + code-rag plugin | SRC-1 §3.3 |
| D-06 | Target DB: PostgreSQL 18 | T 22:13 |
| D-07 | Existing code-rag decisions (register v5, PV5-xx) are references, applied later where relevant | T 22:13 |
| D-08 | Local DB (core) plus a remote DB attached later as a plugin, for search, long-term archive, LLM retraining data and backup | T 22:13 |
| D-09 | File storage on Docker host folders backed by disks, shared into containers, expandable by adding disks | T 22:13 |
| D-10 | Plugin apps live in host folders shared into containers so code can be edited in place, unless this causes a real problem | T 22:13 |
| D-11 | Multi-tenant membership option B: principals carry no `tenant_id`; tenant and project access come only from membership rows | T 22:16 (decision card) |
| D-12 | Save code and tokens | T 22:23 |
| D-13 | Design only for now; code work waits until Dy says so | T 22:24 |
| D-14 | PROP items stay PROP until Dy adopts them; no implicit adoption | T 22:27, 22:31 |
| D-15 | The Worker Protocol stays independent of the in-process worker, so HTTP or separate-process workers can be added without changing pipelines | T 22:27 |
| D-16 | All plugins discussed are registered for later development (§10.2) | T 22:39, 22:49, 22:55 |
| D-17 | Core is prepared for the chat archive plugin | T 22:39 |
| D-18 | Office formats (xlsx, docx, pptx) are format handlers inside the Document plugin, not a separate plugin | T 23:18 ("제안대로" on R 23:17) |
| D-19 | `rstats` (R server charts) registered as L-19 | T 23:15 |
| D-20 | `lora` (training, registration, swapping, exchange) registered as L-20 | T 23:28 |
| D-21 | The `lora` plugin is planned to support merging adapters | T 23:31 |
| D-22 | Where a decision has a proposal, proceed as proposed (PROP-A); where none exists, hold it (HOLD) and keep designing; write P3 at 03:00 KST and continue | T 23:39 |
| D-23 | The miniweb files tool is handled later, together with the Nextcloud/WebDAV connector work (L-16); not in the P4 scope | T2 08:13 |
| D-24 | Posted reviews are reference material, not the reference design; P3 is the base and only needed changes are made | T2 08:13 |
| D-25 | Open WebUI interim access rule (O-14) and plugin trust model (O-15) stay on hold until decided (decided by D-31, D-32) | T2 08:18 (decision card) |
| D-26 | Work token-light: short replies, design detail in the document | T2 20:31, 21:26 |
| D-27 | Pasted code and attachments in chat are handled from plain-language requests (§29) | T2 20:57 |
| D-28 | When the code language of a paste or attachment is unclear, ask the user first, then process (§29) | T2 20:58 |
| D-29 | The O-05 vector measurement may run before implementation (exception to D-13) | T2 21:00 (decision card), 21:04 |
| D-30 | Continue until 23:00 KST under D-22 | T2 21:27 |
| D-31 | O-14: the Open WebUI service principal is a contributor on each configured project; Open WebUI user names are stored only as unverified attributes (§30.1) | T2 22:23 (decision card) |
| D-32 | O-15: plugins run in-process as trusted code, installed by an operator with the plugin folder hash recorded; untrusted code only through the HTTP binding with its own token (§30.2) | T2 22:24 (decision card) |
| D-33 | O-19: models trained on a deleted project's data are disabled at project purge; an admin decides to delete or keep them (§30.3) | T2 22:25 (decision card) |
| D-34 | O-03/O-04: the chat archive starts project-shared; verified per-user delegation (RFC 8693) is built with L-15 before personal archives are enabled (§30.4) | T2 22:27 (decision card) |
| D-35 | All P5 PROP-A items are adopted as the reference design, except O-02, O-12, O-20 (HOLD); adoption is design status, not proof of implementation or performance | T2 22:27 (decision card) |
| D-36 | P6 is written only after the open decisions are made | T2 22:21 |

## 2. Layers (ADOPTED)

| ID | Layer | Responsibility | Basis |
| --- | --- | --- | --- |
| P-L1 | Platform Core | Identity, tenants and projects, memberships, plugin registry, events and audit, API gateway, storage volumes, sources, connector secrets, schedules, webhook routing, artifact and model registry | SRC-1 §5 |
| P-L2 | Runtime | Pipelines as registered data, jobs, stages, tasks, leases, retries, budgets, circuit breakers, resource leases, approval gates | SRC-1 §7 |
| P-L3 | Knowledge Services | Content model, collections, unit versions, representations, generation publishing, search, GC | SRC-1 §7 |
| P-L4 | Plugins | Fill extension slots; code-rag first, echo for validation | SRC-1 §5 |

minicore patterns kept (SRC-1 §5.1): `plugin.yaml` manifest, self-registration, outbox and inbox, JWKS verification, permission registry, schema per plugin with UUID-only cross-schema references. Dropped (SRC-1 §5.2): HR integration, departments and ranks, LDAP, approval domains, and the assumption that every plugin is request/response plus UI.

minicore has no job or task runtime; queue, retry, circuit breaker and generation publishing are RAG-specific layers (SRC-1 §4).

## 3. System layout (ADOPTED)

| ID | Item | Design | Basis |
| --- | --- | --- | --- |
| P-S1 | `postgres` | PG 18 + pgvector; the local DB is the system of record. Schemas `core`, `runtime`, `knowledge`, `plugin_<id>` | D-06, D-08 |
| P-S2 | `api` | One FastAPI process for Platform Core and search; loads each plugin's API router from `/plugins` | D-12 |
| P-S3 | `worker` | One process that claims tasks and runs plugin handlers in-process; also runs maintenance | D-12, D-15 |
| P-S4 | `embed` | bge-m3 on TEI | memory 2026-10-19 |
| P-S5 | External services | vLLM (gpt-oss-120b) and Open WebUI, already running at the site; R server and storage services as plugins need them | memory 2026-10-19 |
| P-S6 | Host folders | `<root>/plugins/<id>/` (plugin.yaml + code, edited on the host, restart to load); `<root>/vol/<name>/` one per disk, mounted at `/data/vol/<name>`; `<root>/pgdata` | D-09, D-10 |
| P-S7 | Dependency caveat | A new Python dependency needs an image rebuild; a code edit needs only a restart | D-10 |
| P-S8 | Air-gapped site | Single NVIDIA 96 GB GPU in a closed network; every external service a plugin uses must run inside it | memory 2026-10-14 |

Deferred until a plugin needs them (D-12): HTTP worker binding, OIDC user login and token exchange (RFC 8693), federated search over the remote DB, admin UI.

## 4. Invariants (ADOPTED)

| ID | Invariant | Basis |
| --- | --- | --- |
| I-S1 | Each component has its own DB login (`api`, `runtime`, `maint`, `purge`, `plugin_<id>`); the superuser is used only by `init` to create roles and extensions (§28.2) | SRC-1 §6.2; v0.2.4 `deploy/docker-compose.yml:15` connects as `user=postgres` |
| I-S2 | The `api` login has no table privileges; it runs only `SECURITY DEFINER` contract functions with a fixed `search_path`; callers cannot pass object names to privileged SQL | SRC-1 §8, T 23:09 |
| I-S3 | Every contract function takes `principal_id` and resolves access from memberships; token tenant claims are ignored | SRC-1 §8, D-11 |
| I-S4 | A project member must be a tenant member, enforced by composite FKs | D-11, T 23:09 (C-1) |
| I-S5 | Tenant-owned rows carry `tenant_id` with composite FK `(tenant_id, id)` | SRC-1 §4 |
| I-S6 | Destructive work runs only as the purge role, limited to the operations it needs; its flag is transaction-local | SRC-1 §6.4; v0.2.4 `db/functions/21-sys-maint.sql:31` uses session scope; T 23:09 (C-3) |
| I-S7 | A plugin login reaches only its own schema and Core contract functions; external services (R server, storage) never get DB credentials. For in-process plugins this contains SQL-level bugs only, because their code runs in the worker process, which holds every plugin pool; this is accepted under D-32 (§30.2) | SRC-1 §5.1, R 23:14 |
| I-S8 | Audit records membership, role, client and token changes; delegated calls record `sub` and `act` | SRC-1 §8 |
| I-S9 | Security tests connect as each real login | SRC-1 §6.3 |
| I-S10 | A caller-supplied human identity is never treated as authenticated until verified delegation exists | T 23:09 (C-5) |
| I-R1 | A job pins `pipeline@version` and its manifest hash at creation | SRC-1 §7, T 23:09 |
| I-R2 | A state change and its event commit in the same transaction | PV5-02 (D-07) |
| I-R3 | Exactly one function may set work to failed | PV5-01 (D-07) |
| I-R4 | Budgets are derived from events | v0.2.4 E4 (D-07) |
| I-R5 | The stage graph is a DAG, checked at registration; each stage has a barrier rule | REASON |
| I-R6 | Lease fencing with a lease epoch that changes on every claim; stale workers cannot change state | T 23:09 |
| I-R7 | Terminal job, stage and task states are immutable | T 23:19 review, R 23:23 |
| I-K1 | Search sees one coherent published generation per requested collection; publishing never exposes a partial generation | T 22:41 |
| I-K2 | Representation values are reused only on exact `(tenant, rep_kind, config_hash, input_hash)` | R 22:37 |
| I-K3 | Shared schemas contain no code-analysis names (`symbol`, `file`, `CODE_EMBEDDING` …), enforced by a name check; plugin stage names are plugin identifiers | SRC-1 §9.1, T 23:27 |
| I-K4 | A failed, superseded or canceled job's candidate data never becomes visible | R 23:23 |
| I-K5 | A temporarily missing input is never interpreted as a deletion | T 23:27 review, R 23:34 |
| I-F1 | Files are addressed as registered-scheme URIs; `vol://` paths cannot escape their volume; content identity is sha256; a URI never grants access | D-09, R 22:48 |
| I-P1 | `plugin.yaml` contract version and `requires` range are checked at load; incompatible plugins are refused | SRC-1 §9.1 |
| I-P2 | A plugin owns its schema; Core never reads plugin tables | SRC-1 §5.1 |
| I-P3 | The remote DB is never the system of record | D-08 |

## 5. Platform Core (ADOPTED)

### 5.1 Identity and access

| ID | Item | Design | Basis |
| --- | --- | --- | --- |
| P-P1 | Principals | `principals(kind: human/service, status)`; `external_identities(issuer, subject)` → principal | SRC-1 §8 |
| P-P2 | Roles | reader=1 < contributor=2 < operator=3 < admin=4. Effective project role = max(project role, admin if tenant admin); a project membership never lowers a tenant admin; other tenant roles do not carry to projects | R 22:46 |
| P-P3 | Authorization | `core.authorize(principal, project, min_role)` inside every contract function; only the API maps a token to a principal | I-S2, I-S3 |
| P-P4 | Service tokens | `service_clients(principal, secret hash argon2id, audiences, scopes, expires_at, status)`; `POST /core/v1/token` (client credentials) → short JWT, ES256 with `kid`; `/core/v1/jwks`; key rotation keeps the old key verifying until the max TTL passes; `aud = coderag-core`; scope per endpoint; client status checked on every request (immediate revocation); membership always re-checked. Token TTL UNSET. API keys in dev mode only | R 22:46 |
| P-P5 | Login | Core provides no user login in the first step. People use Open WebUI, which is its own service principal with its own memberships; admin work uses a CLI with a service token; human OIDC later | R 23:09 (answer to O-01) |
| P-P6 | Audit | `core.audit` owned by `core_owner`, partitioned by month with a DEFAULT partition; insert only through a `SECURITY DEFINER` function; triggers reject UPDATE, DELETE and TRUNCATE; `maint` creates partitions ahead, retention drops old partitions (owner-only procedure) | R 22:46 |

### 5.2 Plugins

| ID | Item | Design | Basis |
| --- | --- | --- | --- |
| P-P7 | Registry | `plugins(id, version, manifest, manifest_hash, status)`, unique `(id, version)`, manifest immutable, status mutable; `registry_items(plugin, version, item_type, key, spec)` unique `(plugin, version, item_type, key)` with a composite FK to the plugin version; `spec` validated against a schema per `item_type`. One generic table holds kinds, collections, policy keys, resources, permissions, schedules and formats | R 23:09, T 23:09 (C-2) |
| P-P8 | Load checks | At `api` and `worker` start: contract and `requires` compatible, schema migration level, handlers exist, unique kinds, valid pipeline DAG; same version with a different manifest refused. A refused plugin's tasks are not claimed; other plugins keep running | I-P1 |
| P-P9 | Manifest hash | YAML parsed → canonical JSON (RFC 8785 JCS) → sha256 | R 22:46 |

### 5.3 Storage, sources and connectors

| ID | Item | Design | Basis |
| --- | --- | --- | --- |
| P-P10 | Volumes | Volume registry; each host disk mounted at `/data/vol/<name>`; new files go to the volume with most free space | D-09 |
| P-P11 | Storage URIs | Registered schemes: `vol://<name>/<path>`, `ext://<connector>/<id>@<version>`; external ids opaque and URL-encoded | R 22:44, 22:48 |
| P-P12 | Connector secrets | `core.get_connector_secret(task_ctx, connector)`: own tenant and owning plugin only; encrypted with a host-held key; versioned rows for rotation; redacted from logs and payloads | R 22:48 |
| P-P13 | Sources | `sources(project, connector, external_id)` = logical source (a branch is binding config); `source_revisions(source, revision_key, manifest_hash, storage_ref, complete)` unique `(source, revision_key)`; `revision_key` immutable (commit SHA, upload UUID); manifest lists `(path, sha256, size, status)`. A revision row is committed only after its snapshot and manifest are complete. Upload is the connector `upload` | R 22:55, 23:27 |
| P-P14 | Source bindings | `source_bindings(project, connector, external_ref, config)`; connector settings validated as config, no provider columns | R 22:52 |
| P-P15 | Input formats | Connector `produces: [file_tree@1]`, pipeline `inputs: [file_tree@^1]`; checked in `create_job` → `INPUT_FORMAT_INCOMPATIBLE` | R 22:55 |
| P-P16 | Plugin-created jobs and coalescing | A connector creates jobs as its service principal with `job.create`. Scope `(project, collection, source, pipeline key)`: manifest equal to `collection_sources.last_manifest` for the same pipeline version → `NO_CHANGE`, no job; a queued, not-started job → `SUPERSEDED`; a running job keeps running and publication ordering decides | R 22:52, 22:55, 23:23 |
| P-P17 | Schedules | Plugins register periodic tasks (e.g. reconciliation, interval UNSET); Core keeps durable `schedule_runs` with retry and dedupe | R 22:52, T 23:09 (C-4) |
| P-P18 | Webhooks | The gateway routes inbound webhooks to the owning plugin; the plugin verifies the signature; `(plugin, delivery_id)` unique with stored processing state; returns 202 and only enqueues discovery | R 22:55, T 23:09 (C-4) |
| P-P19 | Change notifications | A notification is only a hint; scheduled reconciliation recovers missed, duplicated or reordered changes | R 22:48 |

### 5.4 Artifacts, models, events

| ID | Item | Design | Basis |
| --- | --- | --- | --- |
| P-P20 | Artifact registry | Generic artifacts: immutable id, checksum, storage URI, owner project, provenance, access policy; authorized download checked at download time, not only at creation | R 23:14, T 23:15 |
| P-P21 | Model registry | Registered models (base models, adapters): identity hash, base-model hash, quantization, configuration, provenance (datasets, parent artifacts and weights), evaluation results, deployment status, allowed projects; `model.use` permission per model | R 23:30, 23:32, T 23:28 |
| P-P22 | Outbox and events | `outbox_events` committed atomically with the state change that produced them; two layers: Runtime-internal events and platform events | SRC-1 §10, T 23:09 (C-4) |

### 5.5 Core schema sketch (`core`, logical)

`tenants`; `principals`; `external_identities`; `tenant_members(tenant, principal, role)` unique `(tenant, principal)`; `projects` PK `(tenant, project)`; `project_members(tenant, project, principal, role)` with FKs to `tenant_members` and `projects`; `service_clients`; `signing_keys(kid, alg, public_jwk, status)`; `plugins`; `registry_items`; `volumes`; `core.secrets`; `sources`; `source_revisions`; `source_bindings`; `webhook_inbox`; `webhook_subscriptions`; `outbox_deliveries`; `event_consumers`; `schedules`; `artifacts`; `outbox_events`; `requests`; `alerts`; `audit`; `registry_keys`; `plugin_migrations`; `schema_version`; `project_plugins` (physical form in Appendix A). `core_owner` owns all tables; `api`, `runtime`, `maint`, `purge` and each `plugin_<id>` get EXECUTE on contract functions only. Basis: R 23:09, T 23:09.

## 6. Runtime (ADOPTED)

### 6.1 Concept model

| ID | Concept | Design | Basis |
| --- | --- | --- | --- |
| P-R1 | Pipeline | `(plugin, key, version)`, immutable; old versions kept while jobs use them; declares `publish_mode: full | delta`, `inputs[]` and target collection | SRC-1 §7, R 23:09 |
| P-R2 | Stage | `key`, `kind` (control, item, approval), `handler`, `depends_on[]`, split rule `{max_items, max_depth}`, policy keys, barrier | SRC-1 §7, R 23:30 |
| P-R3 | Job | Pins pipeline version and manifest hash, project, collection, source revision; `seq` allocated atomically per `(project, collection)` | R 23:09 |
| P-R4 | Job stage | `(job, stage, status)`; replaces the hard-coded phases | SRC-1 §4 |
| P-R5 | Task | `(job, stage, scope, payload, parent, child_key, attempt, lease_epoch, lease_token, deadline, status, resolution)` | R 23:23, T 23:09 |
| P-R6 | Events | `task_events` (append-only; `request_id`, lease epoch, outcome, child ids) and `lifecycle_events` (job and stage), each written in its transition's transaction; external delivery only through the outbox | R 22:37, 23:23 |
| P-R7 | Policy | `(scope: global/plugin/stage, key, value)`; the plugin registers the key with type and bounds; values UNSET until measured; constraint relations (e.g. statement timeout < lease) fixed in design | SRC-1 §7 |
| P-R8 | Resource | External service a plugin calls (embedder, LLM, R server, storage API); one circuit breaker per resource | R 23:14 |
| P-R9 | Resource lease | Exclusive resource ownership with a schedule window (e.g. the GPU for training, so training and vLLM never compete) | R 23:30, T 23:28 |
| P-R10 | Approval gate | Generic stage kind `approval`: waits for an operator decision, audited | R 23:30, T 23:28 |
| P-R11 | Long tasks | Heartbeat carries checkpoint references; deadline extensions (reuse v0.2.4 watchdog extensions) | R 23:30 |

Runtime schema sketch (`runtime`, logical): `pipelines`, `pipeline_stages`, `jobs`, `job_stages`, `tasks`, `task_events`, `lifecycle_events`, `job_items`, `resource_state`, `schedule_runs`; policies live in `core.policies`; `resource_leases` comes with L-20. Basis: T 23:09.

### 6.2 Worker Protocol v1 (transport-neutral, D-15)

Every mutation carries `(task_id, lease_token, request_id)`. `request_id` is unique per task (unique index on `task_events`); the outcome, including split child ids, is stored atomically with the state change. Check order: lease epoch or token not current → `STALE_LEASE` (no change); task terminal with the same `request_id` → `REPLAY` (stored outcome); task terminal with a new `request_id` → `ALREADY_TERMINAL`. Basis: R 22:37, T 22:31.

| ID | Message | Transition | Outcomes |
| --- | --- | --- | --- |
| P-W1 | `claim(loaded[(plugin, version, pipeline_hash)], max, request_id)` | ready → running; epoch + 1; attempt + 1 | Caller plugin from `session_user`; only tasks whose pinned pipeline matches a loaded tuple; none for stopping jobs (§25 W1) |
| P-W2 | `heartbeat(checkpoint_ref?, extend_deadline?)` | Extends the lease; optional checkpoint and bounded deadline extension (`runtime.deadline_extension_max`) | `EXTENDED`, `STOP`, `STALE_LEASE`, `DEADLINE`, `ALREADY_TERMINAL` |
| P-W3 | `complete` | running → succeeded | `OK`, `REPLAY`, `STALE_LEASE`, `ALREADY_TERMINAL`, `OUTPUT_INVALID` |
| P-W4 | `fail(code, detail)`; retryability comes from the registry | running → retry_wait (budget left) or failed via `rt_fail_terminal` | `RETRY_SCHEDULED`, `FAILED`, `REPLAY`, `STALE_LEASE`, `ALREADY_TERMINAL`, `UNKNOWN_ERROR_CODE` |
| P-W5 | `split(children[{child_key, payload}])` | running → split; children unique on `(parent, child_key)` | `SPLIT`, `REPLAY`, `STALE_LEASE`, `ALREADY_TERMINAL`, `SPLIT_LIMIT`, `JOB_STOPPING` |

Payloads are JSON. Handlers never touch runtime tables; results go through Knowledge Services contracts. Binding 1: in-process Python calling SQL contracts. Binding 2 (later): the same messages over HTTP at `/runtime/v1/*`, with no pipeline change.

### 6.3 State transitions

Task (R 23:19, 23:23):

| From | To | Trigger | Writer |
| --- | --- | --- | --- |
| ready | running | `claim` (epoch + 1, attempt + 1) | runtime claim |
| running | succeeded | `complete` with current epoch | Worker Protocol |
| running | retry_wait | retryable `fail`, budget left | Worker Protocol |
| retry_wait | ready | backoff elapsed | runtime maintenance |
| running | split | `split`; execution ends, children ready | Worker Protocol |
| running | ready | lease expired (uses an attempt) | sweeper |
| running, retry_wait | failed | non-retryable, `max_attempts` (UNSET) exhausted (`attempts_exhausted`), or watchdog deadline | `rt_fail_terminal` only |
| ready, retry_wait | canceled | job stop | runtime |

- One budget: `max_attempts`, counted by claims; retryable fails and lease expiries use it through the next claim.
- Split: `resolution` is `ok` when every descendant is `ok`, `failed` when any descendant fails; the last child's terminal transition updates ancestors in the same transaction under a parent lock; barriers count root tasks' resolution. A replayed split returns the original children.
- Cancel races: the stop flag is checked by `claim` in the same transaction; a `complete` during stop is accepted but never publishes; `split` during stop returns `JOB_STOPPING` and the task ends `canceled`; `fail` or lease expiry during stop ends `canceled` without retry.

Job stage: `blocked → ready` (dependencies succeeded) → `running` (first task) → `succeeded` (barrier met, recorded atomically) or `failed` (a task terminally failed); any → `canceled` on stop. A stage that creates zero tasks succeeds in the same transaction. A failed stage sets the job stop flag; pending tasks and dependent stages are canceled; running tasks stay fenced and their output never publishes.

Job: `queued → running`; `queued → superseded` (newer revision in scope); `running → succeeded` (publish `PUBLISHED` or `NO_CHANGE`); `running → superseded` (publish `SUPERSEDED`, or stopped by a newer publication, §7.4); `running → failed` (stage failed or `COVERAGE_FAILED`); `running → canceled` (cancel request, once no tasks run). Terminal states are immutable (trigger); late messages get `ALREADY_TERMINAL` or `STALE_LEASE`.

## 7. Knowledge Services (ADOPTED)

### 7.1 Content model

`Source → Source revision → Unit → Unit version → Chunk → Representation → Generation`, grouped by collection. Unit kinds and representation kinds are registered by plugins and namespaced `plugin.kind`.

| ID | Item | Design | Basis |
| --- | --- | --- | --- |
| P-K1 | Collection | Publication namespace `(project, collection)` declared in `plugin.yaml`; a plugin publishes only into collections it declared; holds `current_gen`, `active_config` and `fence_floor`; ordering per §24 | T 22:39, R 22:41 |
| P-K2 | Unit and version | Logical unit `(collection, source, kind, key)`; immutable `unit_versions` with `valid_from_gen`, `valid_to_gen`; visible at g iff `valid_from ≤ g` and (`valid_to` NULL or `g < valid_to`) | R 22:41, 23:09, 23:27 |
| P-K3 | Chunk | Ordered text segments of a unit version; tables are ordinary chunks with `attrs` | R 23:17 |
| P-K4 | Representation | `representation_values` keyed `(tenant, rep_kind, config_hash, input_hash)`; `config_hash` = model, revision, dims, dtype, normalization, template; `input_hash` = exact model input; `representation_links(rep_kind, config_hash, target)` from a unit version or chunk to a value; a new `config_hash` adds links without changing published versions, and `collections.active_config` selects which one search uses. Each kind declares its target type (unit or chunk), dims and config; validated at registration; `DIM_MISMATCH` on wrong vectors | R 22:37, 23:09, T 23:09 |
| P-K5 | Attribution | Units record `created_by` (authenticated principal) and `on_behalf_of` (client-supplied, unverified until delegation; grants nothing) | R 22:41, I-S10 |
| P-K6 | No-change record and ordering scope | `collection_sources(collection, source, pipeline_key, last_seq, last_revision, last_fingerprint)` (§24) | R 23:27 |
| P-K7 | Candidate keys | `candidate_keys(job, unit, mode: touch | hold | delete)`; unchanged paths of a full snapshot count as touched from the manifest diff without rows | R 23:27, 23:34 |
| P-K8 | Generation pins | `generation_pins(collection, gen, expires_at)`; TTL UNSET | R 23:23 |
| P-K9 | Index | One partial HNSW index per `(rep_kind, config_hash)` on `representation_values`, created by `knowledge.ensure_index`; filters through joins with iterative scan `relaxed_order` (mandatory, set on `knowledge.search`); measured in §20 | R 23:23 |

Knowledge schema sketch (`knowledge`, logical): `collections`, `collection_sources`, `units`, `unit_versions`, `chunks`, `representation_values`, `representation_links`, `generations`, `candidate_keys`, `generation_pins`. Sources and revisions live in `core` (P-P13). Basis: T 23:09.

### 7.2 Contracts

Tenant, project, collection and source revision always come from the job (or memberships, for search), never from the caller.

| ID | Contract | Rules | Outcomes |
| --- | --- | --- | --- |
| P-C1 | `put_unit(task_ctx, kind, key, content_hash, attrs, chunks[])` | Registered kinds of the calling plugin only. Same hash as the visible version → no write, key recorded as `touch`. New hash → staged version (`valid_from` NULL, candidate = job). Same key twice in one job: same hash `REPLAY`, different hash `CONFLICT` (first wins, task fails). Lease-fenced | `unit_version_id`, `REPLAY`, `CONFLICT`, `STALE_LEASE` |
| P-C2 | `put_representation(task_ctx, target, rep_kind, model_id, content_hash, value)` | Reuse per P-K4, tenant-scoped | `OK` (also when reused, B-14), `REPLAY`, `CONFLICT`, `DIM_MISMATCH`, `STALE_LEASE` |
| P-C3 | `hold_unit(task_ctx, kind, key, reason)` | Input temporarily missing (e.g. `lfs_missing`): touch without a new version; the old version stays open | `HELD` |
| P-C4 | `publish(job)` | §24 C3 | `PUBLISHED`, `NO_CHANGE`, `COVERAGE_FAILED`, `SUPERSEDED`, `STALE` |
| P-C5 | `search(principal, projects[], collections[], query, k, rep_kinds[])` | Authorize all requested projects first; any failure → `FORBIDDEN` with no results; an unknown project is also `FORBIDDEN`. Reads `current_gen` per collection in the same statement as the results; RRF fusion across representation kinds; no cross-collection atomicity (documented); paging pins a generation | results + `generation_id` per collection, `FORBIDDEN`, `GENERATION_GONE` |
| P-C6 | `delete_unit` | §24 C2: delta collections only; contributor now, operator for another principal's or a connector's units | §24 C2 |

### 7.3 Publish transaction

Defined in §24 (C3), with lock order in §26. The collection row is locked `FOR NO KEY UPDATE`, so foreign-key checks from concurrent job creation (`KEY SHARE`) do not wait (TP-23, tested). A failed, superseded or canceled job's staged versions are discarded in the transaction that makes the job terminal.

### 7.4 Stopping older jobs

The publish transaction itself marks older running jobs of the same full-mode scope `stop_requested = superseded` (§24 C3 step 11). `core.event_consumers` is used only by maintenance and outbox delivery, not for this.

### 7.5 GC and readers

Single-call search reads one REPEATABLE READ snapshot; multi-call (paging) search pins a generation (§27 TP-20, TP-20b). There is no shared GC lock. GC rules in §25 M3 and §24 (fences). Old versions are removed as soon as no pin, generation or running job needs them; the old-version ratio per collection is reported (§28.4) because it lowers narrow-filter recall (§20).

### 7.6 Incomplete inputs

A file excluded because the snapshot is incomplete (e.g. missing LFS object) is recorded with `hold_unit`; the revision is marked incomplete and reconciliation retries; a hold longer than a limit (UNSET) raises an operator alert. Only a path actually absent from the manifest counts as deleted. Basis: T 23:27 review, R 23:34.

## 8. `plugin.yaml` v1 (ADOPTED)

Core reads only: `id`, `version`, `contract: 1`, `requires: {core: ">=0.4,<0.5"}`, `entry` (Python module), `schema` (`plugin_<id>` + migrations folder), `handlers[]`, `pipelines[]` (stages, `inputs[]`, `publish_mode`, collection), `produces[]`, `collections[]`, `unit_kinds[]`, `representation_kinds[]` (target type, model, dims), `policy_keys[]`, `resources[]`, `permissions[]`, `schedules[]`, `events: {emits, consumes}`, optional `api: {router, prefix}`, optional `service: {...}` for request→task plugins. Basis: R 22:29, 23:14.

## 9. End-to-end trace: Gitea commit to search (ADOPTED)

1. A push webhook reaches the gateway; deduplicated by `(plugin, delivery_id)`; discovery is enqueued.
2. The Gitea plugin resolves the commit SHA through the source binding, snapshots the tree into a volume and commits the `source_revision` with its manifest only when complete.
3. Core checks `file_tree@1`, the no-change record and coalescing, allocates `seq` per `(project, collection)`, and pins `code.analyze@1` with its manifest hash.
4. Runtime runs ingest, parse, graph, embed, knowledge, knowledge_embed; each mutation is lease-fenced and evented.
5. The code plugin writes units, chunks and representations through contracts; unchanged files are touched, not rewritten.
6. Publish (§7.3) switches the generation atomically.
7. Search returns results with the generation id; GC follows §7.5.

Two commits, A (slow job J1, seq 1) then B (fast job J2, seq 2): J1 keeps running; J2 publishes gen 1 = B; `knowledge.published` stops J1, which ends `superseded`, and its candidate rows are cleaned. In the reverse order, J1 publishes gen 1 = A, J2 publishes gen 2 = B and ends A-only keys; a reader pinned to gen 1 keeps A until the pin expires. A repeated push for B returns `NO_CHANGE`. Basis: R 23:27.

## 10. Plugins

### 10.1 First scope (D-05)

| ID | Plugin | Design (ADOPTED) | Basis |
| --- | --- | --- | --- |
| L-01 | code-rag (`code`) | Defined in §31: unit kinds `code.file`, `code.symbol`; representations `code.summary`, `code.embed`, `code.knowledge_embed`; collection `code`, `publish_mode: full`; pipeline `code.index@1` (7 stages); 3 plugin tables (Appendix B) | R 22:46, 22:55, §31 |
| L-02 | echo (validation) | Manifest in §32. Unit `echo.item`; representation `echo.vec` (deterministic 8-dim, no model; exercises reuse and `DIM_MISMATCH`); one split stage reaching depth 2, then delta publish into collection `echo`; handler over the HTTP binding; the I-K3 name check must pass with both plugins | SRC-1 §9.1, R 22:46 |

Search parameters (`candidate_k`, `rrf_k`, …) belong to Knowledge Services and keep the v0.2.4 Phase 3 values as PROP (memory 2026-10-16; not adopted by D-35).

### 10.2 Registered for later development (D-16, D-19, D-20)

| ID | Plugin | Summary (ADOPTED) | Basis |
| --- | --- | --- | --- |
| L-10 | Document | Document ingest and search with format handlers: pdf, md, docx (sections by heading, paragraphs, tables; images to Vision later), xlsx (workbook, sheet, table units; header+rows chunks; cached values, not formulas; row cap with summary; numeric tables can feed `rstats`), pptx (one unit per slide plus notes), hwpx (`Contents/section*.xml`; old hwp needs conversion, HOLD). Collection `docs`; shared bge-m3; optional LLM summary; input `file_tree@1`. Pure-Python libraries (openpyxl, python-docx, python-pptx); old binary formats need optional LibreOffice conversion. Macros never executed, external links ignored, zip limits from safe extraction | SRC-1 §9, R 23:17, D-18 |
| L-11 | Vision | Image representations | SRC-1 §9 |
| L-12 | CI/CD | Build and pipeline events; kept separate from Gitea (sources) | SRC-1 §9, R 22:50 |
| L-13 | Data ingest | Data collection plugin | SRC-1 §9 |
| L-14 | Remote DB store | Remote DB for search, long-term archive, LLM retraining data, backup; never the system of record; federated search with timeout and partial-result flag | D-08 |
| L-15 | Chat archive (`chat`) | Units `chat.note`, `chat.file` (the file identifies the attachment; extracted text is ordinary chunks); save API `POST /api/chat/v1/save` from an Open WebUI tool (optional auto-save filter); shared bge-m3; collection `chat`, delta publish; per-item delete | T 22:39, D-17 |
| L-16 | External storage source connector | Nextcloud/WebDAV or other storage APIs; snapshots bytes into a local volume (`produces: file_tree@1`); ETag is only a change hint, copied-bytes sha256 is the input identity; ETag change during read → retry, then `source_unstable`; connect only folders shared with the whole project team; remote deletion or revocation → staged delete at the next reconciliation; the reconciliation interval is the documented removal lag (no claim of immediate removal); storage must be inside the closed network; direct storage driver deferred | R 22:44, 22:48, T 22:49 |
| L-17 | Gitea | Git source connector; revision = commit SHA; push webhook → discovery; scheduled reconciliation; feeds `code.analyze`; read-only service account; size limits, LFS (missing object → hold), submodules (not followed in v1) handled in the plugin | T 22:49, R 22:50, 23:27 |
| L-18 | Ops / evaluation | Golden-set evaluation as a plugin; plugins register their own golden sets | SRC-1 §9.2 |
| L-19 | `rstats` | Service-type plugin; `POST /api/rstats/v1/plot` with a dataset ref and a chart template id plus typed parameters; one runtime task calling resource `rserver` (Rserve or plumber inside the closed network) with breaker and timeout; PNG/SVG plus statistics stored as an artifact with authorized download; optional searchable `rstats.chart` unit; data from search results, collections, uploaded CSV, later the remote DB; R gets only the data passed in. First step templates only, no arbitrary R code; LLM-written R code later only under a separate sandbox design (filesystem, network egress, privileges, limits, malicious input and output) | R 23:14, T 23:15 |
| L-20 | `lora` | See §10.3 | R 23:30, 23:32, 23:38, T 23:28, 23:31 |

### 10.3 L-20 `lora` plan (ADOPTED)

Lifecycle (one plugin): dataset preparation, validation, approval, training, evaluation, registration, deployment, runtime use, rollback, merge, exchange.

| Pipeline | Stages | Notes |
| --- | --- | --- |
| `lora.train@1` | build_dataset → validate → approve → train → evaluate → register | Dataset JSONL from authorized L-15 chat archive, L-14 retraining data or approved collections; `approve` is the generic approval gate (operator); `train` is one long task with heartbeat, checkpoints on a volume, resume, and an exclusive GPU lease in a schedule window; `evaluate` uses L-18 against the exact base-model identity; `register` creates an immutable model entry (P-P21) |
| `lora.merge@1` | select → merge → evaluate → approve → register | Combines adapters into a new adapter with PEFT `add_weighted_adapter` (linear, cat, TIES, DARE or SVD) and per-adapter weights. All parents must share the exact base model. Provenance records parents and weights. Access to the merged adapter is the intersection of the parents' permissions. Evaluation is required because merged adapters can interfere and lose quality |
| `lora.deploy@1` | deploy / undeploy | Loads or unloads adapters in vLLM (`--enable-lora`, `--max-loras`, `--max-lora-rank`, `VLLM_ALLOW_RUNTIME_LORA_UPDATING`, `/v1/load_lora_adapter`) only through a trusted serving interface owned by the plugin, never exposed to Open WebUI users; reconciles registry against actual serving state; undeploy is the rollback; keeps adapters beyond `--max-loras` on a volume |
| `lora.exchange@1` | export / import | Package: `adapter.safetensors`, `adapter_config`, manifest (base-model hash, dataset ref, eval scores, sha256); import verifies checksum and exact base model; imported adapters are untrusted until validated |

Use in chat: each loaded adapter is listed by vLLM in `/v1/models`, so Open WebUI shows it as a model; users pick one per chat or switch between messages. Open WebUI access groups control visibility, but the plugin and serving interface enforce `model.use` at request time.

Constraints and risks:

- One adapter applies per request in vLLM serving; runtime stacking is not available (inferred; to verify on the installed vLLM). Combining is done by `lora.merge`. Merging an adapter fully into base weights gives a new base model and needs a new vLLM load and GPU memory, so it suits only a final adapter.
- vLLM holds about 82% of the single 96 GB GPU; training needs a GPU window (vLLM paused, Open WebUI down meanwhile) or a second GPU.
- LoRA on `gpt-oss-120b-awq` and vLLM LoRA loading for it are not confirmed (O-09). Compatibility is recorded only after testing against base hash, quantization, vLLM version, adapter format and LoRA modules. First target: a separately validated 7B–32B base model.
- Training data may contain personal or confidential chat content: approval and dataset review come before training. An adapter can memorize its data; access follows the dataset's source projects; provenance does not prove an adapter is free of memorized data.
- Undeploy removes availability only; it does not undo past conversations or erase what the adapter learned.
- Project purge (D-33): every model whose dataset or parents include the purged project is undeployed and set disabled in the same purge step; it stays unusable until an admin deletes it or keeps it with a recorded reason. Keeping it never restores the purged project's access rows.

Core needs (generic, no LoRA names): artifact and model registry (P-P20, P-P21), approval gate (P-R10), resource lease with schedule window (P-R9), long-task support (P-R11), `model.use` permission.

## 11. Milestones (ADOPTED, SRC-1 §9.2)

| Step | Output | Done when |
| --- | --- | --- |
| M0 Design | Invariants, concept model, contracts, plugin manifest, tenant decision (D-11) | This document adopted by Dy |
| M1a | Identity, tenants, memberships, access layer, DB logins, plugin registry, management contracts (§25), `coreadm` bootstrap | Security tests pass under real logins; O-14 and O-15 decided (D-31, D-32) |
| M1b | Runtime: pipeline registry, state machine, Worker Protocol, policy store, retry_job (G7) | echo pipelines run (EC-01..08); code plugin pipelines start in M3 |
| M2 | Knowledge Services: content model, collections, versions, publish, search with keyword (FTS) + vector RRF, GC, explicit deletes, snapshot retention (G4), project purge (G2) | TP-01..23 pass; O-05 rerun at 1M and with real embeddings |
| M3 | code-rag plugin (incl. G3 file report, G8 graph gate, purge hook) | Golden 3 repos + at least 1 real repo + more Korean queries, comparable to v0.2.4 results |
| M4 | Deploy and evaluation: compose, eval plugin, Open WebUI filter and tool (§29), webhooks (G1), backup set, alerts | single server starts with `up` only; restore drill BR-01 passes |

## 12. What v0.2.4 shows vs what v0.4 needs

- Shown by v0.2.4: lease, claim, sweeper and breaker semantics; a full ingest-to-publish run; partition build; Phase 3 search quality (recall@10 0.931, memory 2026-10-16).
- Not shown, needed by v0.4: generic stages (phases hard-coded), collections, unit versions and delta publish, separate DB logins, membership authorization, plugin loading, a second consumer, generic sources and connectors, artifacts and models, approval gates, resource leases.

## 13. Open items

| ID | Item | Needs |
| --- | --- | --- |
| O-01 | Core user login: P-P5 (none in the first step) | ADOPTED (D-35) |
| O-02 | ADR numbering conflict in the source material (ADR-01 void vs kept) | Dy (ADR text not available) |
| O-03 | Chat archive scope | DECIDED D-34 (shared first) |
| O-04 | Delegation (RFC 8693) timing | DECIDED D-34 (with L-15, before personal archives) |
| O-05 | Measured at 200k (§20, passes); still open: 1M vectors, real bge-m3, search during publish and GC. Original item: pgvector recall and latency with one HNSW index and the generation filter (current-only vs many versions, selective vs broad, concurrent publish, index size, GC) | Measurement |
| O-06 | UNSET values: token TTL, `max_attempts`, reconciliation interval, pin TTL, GC grace, hold alert limit, policy values | Measurement |
| O-07 | Prompt removal from search on remote revocation | Later design |
| O-08 | `/api/core/v1/auth/delegated-token` assumed by `miniweb_files_tool.py` is not in the minicore router list | Check (SRC-1 §11) |
| O-09 | LoRA training and vLLM LoRA loading for `gpt-oss-120b-awq`; runtime stacking in vLLM | Test on the site's vLLM version |
| O-10 | Sandbox design for arbitrary R code (L-19) | Later design |
| O-11 | Full-text (keyword) search: in M2 (`knowledge.chunks.fts`, 'simple', RRF with vector kinds, §16) | ADOPTED |
| O-12 | Search plugin hooks: query expansion, rerank, graph expansion | HOLD (later) |
| O-13 | Run the DDL draft on PG 18 + pgvector 0.8 | At implementation (D-13) |
| O-14 | Open WebUI interim rule until delegation | DECIDED D-31 (§30.1) |
| O-15 | Plugin trust model | DECIDED D-32 (§30.2) |
| O-16 | Pure logic (parsers, chunkers, graph extraction, prompts, eval) may be copied from v0.2.4 with a source-path note and a review checklist (hidden imports, old data shapes, shell and path handling, size limits); DB, job, auth glue is rewritten. D-02 is read as: no v0.2.4 tree in the repository | ADOPTED (D-35) |
| O-17 | `runtime.approval_ttl` default 7 days from entering `waiting_approval` (§25 U4) | ADOPTED |
| O-18 | Values: `knowledge.delete_batch_max`, `runtime.job_deadline`, `core.purge_grace`, `knowledge.min_coverage`, retry cap | UNSET, measurement |
| O-19 | Models trained on a deleted project's data (L-20) | DECIDED D-33 (§30.3) |
| O-20 | Hospital DW integration contract: not available in this design; check ID prefixes and affected sections when provided | HOLD |
| O-21 | Plugin-derived data (e.g. code graph edges) outside the generation model: carry valid_from/valid_to gen, or read only through published unit versions | ADOPTED: read only through published unit versions; the code plugin keeps edges in unit version attrs (§31) |


## 14. Error-code registry (ADOPTED)

Error codes live in `core.registry_keys(item_type = 'error_code', key, owner, spec{layer, retryable, health_sample})`; tasks and task events reference them by foreign key. Core codes are fixed (`core.*`); plugins register namespaced codes (`<plugin>.*`), recorded per version in `registry_items`. A `fail()` with an unregistered code returns `UNKNOWN_ERROR_CODE`, and the task fails with `core.unknown_error_code` (not retryable). Basis: v0.2.4 signal registry pattern (PV5-04, D-07); REASON (one registry replaces per-table CHECK lists, saves code).

| Code | Layer | Retryable | Health sample |
| --- | --- | --- | --- |
| `core.attempts_exhausted` | runtime | no | no |
| `core.deadline_exceeded` | runtime | no | no |
| `core.approval_expired` | runtime | no | no |
| `core.approval_rejected` | runtime | no | no |
| `core.fence_expired` | knowledge | no | no |
| `knowledge.coverage_failed` | knowledge | no | no |
| `core.plugin_version_missing` | runtime | yes | no |
| `code.graph_quality_regressed` | plugin | no | no |
| `core.deadline_exceeded` | runtime | no | no |
| `core.unknown_error_code` | runtime | no | no |
| `core.split_limit` | runtime | no | no |
| `core.handler_crashed` | runtime | yes | no |
| `core.resource_unavailable` | runtime | yes | yes |
| `core.resource_timeout` | runtime | yes | yes |
| `core.resource_rate_limited` | runtime | yes | yes |
| `core.breaker_open` | runtime | yes | no |
| `core.output_invalid` | knowledge | no | no |
| `core.coverage_failed` | knowledge | no | no |
| `core.input_invalid` | core | no | no |
| `core.input_incomplete` | core | yes | no |
| `core.source_unstable` | core | no | no |
| `core.permission_denied` | core | no | no |

Plugin examples carried from v0.2.4: `code.parse_failed`, `code.encoding_unsupported`, `code.parser_timeout`, `code.zip_path_traversal`, `code.zip_bomb_limit`, `code.llm_output_invalid`, `code.llm_refused`.

Protocol and contract outcomes (§6.2, §7.2) are return values, not error codes. HTTP mapping: `FORBIDDEN` 403 (also for unknown projects), `NOT_FOUND` 404 (only for objects the caller may see), `CONFLICT` 409, `INPUT_FORMAT_INCOMPATIBLE` 422, `GENERATION_GONE` 410, others 200 with the outcome. Error bodies use RFC 7807 (SRC-1 appendix).

## 15. `plugin.yaml` JSON schema v1 (ADOPTED)

Required: `id` (`^[a-z][a-z0-9_]{1,29}$`), `version` (semver), `contract` (const 1), `requires.core` (semver range), `entry` (Python module), `schema` (`^plugin_[a-z0-9_]+$`), `migrations` (relative folder).

Optional arrays (keys unique per type, pattern `^[a-z][a-z0-9_.]*$`, Core prefixes the plugin id):

| Field | Item shape |
| --- | --- |
| `handlers[]` | `{name, kind: control / item / service}` |
| `pipelines[]` | `{key, version, collection, publish_mode: full / delta, inputs: [format@range], stages: [{key, kind: control / item / approval, handler?, depends_on[], split?: {max_items > 0, max_depth >= 0}, policy_keys[], barrier: all}]}` |
| `produces[]` | `format@version` (e.g. `file_tree@1`) |
| `collections[]` | `{key}` |
| `unit_kinds[]` | `{key, parent_kind?, required_representations[]}` (coverage, R6) |
| `connectors[]` | `{key, produces: format@version, refetchable: bool}` (G4) |
| `exclusion_reasons[]` | `{key}` (G6) |
| `hooks` | `{purge_project: handler}` (G2) |
| `representation_kinds[]` | `{key, target: unit / chunk, value: vector / text / json, dims (required for vector), model_ref (resource key), template_version}` |
| `policy_keys[]` | `{key, type: int / number / interval / bool / text, min?, max?, unit?, affects_output: bool}` (no defaults: values UNSET; `affects_output` keys enter the no-change fingerprint, G5) |
| `resources[]` | `{key, kind: http / grpc / process, breaker: bool}` |
| `permissions[]` | `{key, min_role}` |
| `schedules[]` | `{key, handler, interval_policy_key}` |
| `error_codes[]` | `{key, retryable, health_sample}` |
| `events` | `{emits[], consumes[]}` |
| `api` | `{router: "module:attr", prefix: ^/api/<id>/v1$}` |
| `service` | `{endpoints: [{path, handler, permission}]}` |

Validation at load: JSON Schema 2020-12 plus semantic checks (DAG, a handler for every non-approval stage, `depends_on` keys exist, `dims` for vector kinds, a declared collection per pipeline, parseable format ranges). Unknown top-level fields are refused (strict contract; minicore drops them instead, SRC-1 appendix). Basis: §8, REASON.

## 16. Search contract details (ADOPTED)

Retrievers: keyword (`knowledge.chunks.fts`, `'simple'` over text + summary + summary_ko + keywords, as v0.2.4) and each active vector kind, fused by RRF. `knowledge.search` takes `projects[]` (P-C5, §25 U5), runs with function-level `SET hnsw.iterative_scan = relaxed_order` and `hnsw.ef_search` from policy, re-sorts candidates by exact distance, and refuses projects that are not active (`PROJECT_DELETING`). Display fields (file path, line range, symbol) are built by the owning plugin's `present` hook from `attrs`/`span`; Core returns them unchanged.

Request: `{projects[], collections[] (default: all readable collections of those projects), query, k (1..max_k), rep_kinds[] (default: all searchable kinds), filters: {unit_kinds[], attrs, source_ids[]}, include_content, page: {generation_ids, offset}}`.

Steps:

1. Authorize all projects (any failure → `FORBIDDEN`); collections must belong to the projects.
2. Pin: use the page's generation ids (`GENERATION_GONE` if expired), otherwise read `current_gen` per collection.
3. Embed the query once per distinct model configuration among the requested kinds.
4. Per representation kind: ANN with `candidate_k` on `representation_values`, joined through links to unit versions visible at the pinned generation, filters in the same query (pgvector iterative scan). Text kinds (summaries) are searched through their embedding kind.
5. Fuse with RRF (`rrf_k`) and per-kind weights; group by unit version, keeping the best chunk.
6. Return the top k: unit id, version id, kind, key, attrs, score, matched kinds, chunk excerpt, content if requested, generation ids, and a page token (creates a generation pin).

Policy keys: `knowledge.search.candidate_k`, `rrf_k`, `max_k`, `ef_search`, `weight.<rep_kind>`; the v0.2.4 Phase 3 values stay PROP, not adopted by D-35 (candidate_k 50, rrf_k 60, max_k 20, ef_search 100, weights 1; memory 2026-10-16). Full-text search: in M2 (O-11, keyword retriever above). Hooks: HOLD (O-12).

Chunk contract: `{ordinal, text (≤ knowledge.chunk.max_chars, not empty), span (plugin-defined), attrs}`, ordinal unique per unit version; empty text → `OUTPUT_INVALID`.

## 17. code-rag port mapping from v0.2.4 (ADOPTED)

| v0.2.4 component | v0.4 place |
| --- | --- |
| `ingest.py` safe zip extraction and limits | Core helper `core.files.safe_extract` (used by the upload connector); the code plugin consumes `file_tree@1` |
| `parse_python.py` (tree-sitter) | `parse` item handler; `put_unit` for `code.file` and `code.symbol`; raw facts into `plugin_code`; the fixed 4-kind CHECK becomes `attrs.kind` (multi-language ready) |
| `resolver.py` | `graph` handler; `plugin_code` dependency facts, call facts, edges, quality summaries |
| `providers.py` (HTTP embedder, HTTP knowledge) | Core resource clients `embedder` and `llm` (OpenAI-compatible, shared by all plugins); stub embedder kept for tests |
| `pipeline.py` drivers | Dropped; replaced by Runtime. Handler bodies reused |
| SQL 10–17 (claim, persist, sweeper, split, reset, finalize) | Runtime contracts with one `max_attempts` budget; refund, grace, ceilings and finalize fingerprints dropped (saves code) |
| SQL 14, 15, 22 (terminalize, reconcile, breaker, watchdog) | Runtime; breaker per resource |
| SQL 18, 19, 21 (purge, GC, system tasks) | Knowledge GC and Core schedules |
| SQL 23, 26 (jobs, publish, phase driver) | Runtime jobs and the stage DAG |
| SQL 27, 28, 29 (graph, knowledge, knowledge embedding) | Code plugin handlers and generic `put_representation` |
| SQL 30 (per-generation partitions) | Dropped; one HNSW index per vector kind (O-05) |
| SQL 99 (schema gate) | Per-schema assert plus plugin migration level |
| `api.py` | Core API (projects, jobs, search) plus code plugin router (`/api/code/v1/ingest`, `/ingest/text`, `/import/folders`) |
| `deploy/` | v0.4 deploy (M4); `connect.sh` pattern kept |
| `eval/` | L-18 later; at M3 the evaluator runs as a script |
| Open WebUI tool | Rewritten against the v0.4 API; operation ids `search_code`, `list_projects`, `job_status` kept |

Representation configs: `code.embed` template v1 = qualname, signature, docstring, body (from v0.2.4 `embedding_text`); `code.summary` uses the v0.2.4 LLM prompt as v1; `config_hash` includes `template_version`.

## 18. Policy catalog (ADOPTED; values UNSET)

| Key | Type | Constraint |
| --- | --- | --- |
| `runtime.lease` | interval | > 2 × `runtime.heartbeat` |
| `runtime.heartbeat` | interval | > 0 |
| `runtime.sweeper_interval` | interval | < `runtime.lease` |
| `runtime.max_attempts` | int | ≥ 1 |
| `runtime.backoff_base` | interval | ≥ 0 |
| `runtime.backoff_max` | interval | ≥ `backoff_base` |
| `runtime.claim_statement_timeout` | interval | < `runtime.lease` |
| `runtime.claim_lock_timeout` | interval | < `claim_statement_timeout` |
| `runtime.deadline.<stage kind>` | interval | > `runtime.lease` |
| `runtime.deadline_extension_max` | int | ≥ 0 |
| `runtime.breaker.window` | int | > 0 |
| `runtime.breaker.ratio` | number | in (0, 1] |
| `runtime.breaker.min_sample` | int | ≤ `window` |
| `runtime.breaker.cooldown` | interval | > 0 |
| `knowledge.pin_ttl` | interval | > 0 |
| `knowledge.gc.grace` | interval | ≥ `knowledge.pin_ttl` |
| `knowledge.gc.lock_timeout` | interval | > 0 |
| `knowledge.hold_alert_after` | interval | > the reconciliation interval |
| `knowledge.chunk.max_chars` | int | > 0 |
| `knowledge.search.*` | see §16 | `max_k` ≤ `candidate_k` |
| `core.token_ttl` | interval | ≤ `core.signing_key_retire_delay` |
| `core.signing_key_retire_delay` | interval | ≥ `core.token_ttl` |
| `runtime.max_running` | int | ≥ 1 |
| `runtime.approval_ttl` | interval | > 0 (ADOPTED 7 days) |
| `runtime.job_deadline` | interval | > `runtime.lease` |
| `runtime.retry_cap` | int | ≥ 0 |
| `knowledge.min_coverage` | number | in (0, 1] |
| `knowledge.max_excluded_ratio` | number | in [0, 1) (v0.2.4: 0.5) |
| `knowledge.delete_batch_max` | int | > 0 |
| `knowledge.search.ef_search` | int | ≥ `max_k` (measured: 100) |
| `core.purge_grace` | interval | ≥ 0 |
| `core.snapshot_retention` | interval | > `runtime.job_deadline` |
| `core.scratch_ttl` | interval | > 0 |
| `core.webhook_allowlist` | text | URL prefixes |
| `core.retention.<table>` | interval | per event table (§28.4) |
| `<plugin>.*` | registered by the plugin | bounds from the manifest |

Constraints are checked whenever a policy row is written (`core.policy_set`), not only at startup. Storage: `core.policies(scope, scope_ref, key, value)`, value NULL = UNSET. Basis: SRC-1 §7; v0.2.4 `rt_policy` constraint pattern (D-07); REASON (v0.2.4 had about 60 policy columns; v0.4 keeps about 25 Core keys).

## 19. Security test plan (ADOPTED)

Each test opens its own connection as the real login (I-S9). Runner: pytest on PG 18.6 + pgvector 0.8.1 (as in v0.2.4).

| ID | Test | Expected |
| --- | --- | --- |
| ST-01 | `api` login SELECT or INSERT on any table | permission denied |
| ST-02 | principal of tenant A calls a contract on a project of tenant B | `FORBIDDEN` |
| ST-03 | search with one forbidden project among several | `FORBIDDEN`, zero rows |
| ST-04 | unknown project id | `FORBIDDEN`, same as ST-03 |
| ST-05 | project member without tenant member | FK violation |
| ST-06 | `plugin_code` login reads `plugin_echo` | denied (SQL level; see I-S7) |
| ST-07 | `get_connector_secret` for another plugin or tenant | `FORBIDDEN` |
| ST-08 | UPDATE or DELETE on `core.audit` | rejected |
| ST-09 | purge flag after an exception inside purge | not set in the next transaction |
| ST-10 | expired, wrong `aud`, unknown `kid`, revoked client JWT | 401 |
| ST-11 | valid JWT after membership removal | `FORBIDDEN` |
| ST-12 | stale lease `complete`, `fail`, `split` | `STALE_LEASE`, no change |
| ST-13 | replayed `split` | same children |
| ST-14 | search during publish | one generation per collection, never mixed |
| ST-15 | `vol://` traversal (`../`, absolute path, symlink) | refused |
| ST-16 | duplicate webhook delivery | one discovery |
| ST-17 | `on_behalf_of` used for `delete_unit` | `FORBIDDEN` |
| ST-18 | artifact download after access is revoked | `FORBIDDEN` |
| ST-19 | same plugin version, different manifest | refused |
| ST-20 | I-K3 name check over the Core DDL | no forbidden names |

Basis: SRC-1 §6.3; R 22:37 to 23:34.

## 20. Physical DDL draft (ADOPTED)

The draft in Appendix A has 49 tables plus the audit DEFAULT partition in `core`, `runtime` and `knowledge`, plus `runtime.job_seq`. Contract functions are in §25, lock order in §26.

Evidence (Claude, T2 08:21-11:11): implemented as DDL; loads with zero errors on PostgreSQL 16 with two stand-ins (`uuidv7()` → `gen_random_uuid()`, `vector` stubbed as a domain) and `btree_gist`. Tested: a second identical plugin-wide schedule is refused; `TRUNCATE core.audit` is refused; a collection whose tenant does not match its project is refused. Not run on PG 18 + pgvector (O-13).

Vector index measurement (O-05, D-29; tested T2 21:30, PostgreSQL 16.15 + pgvector 0.8.1 built in the design container because the PG 18 source download is blocked): 200k synthetic clustered 1024-d vectors plus 20k of a second `config_hash`, one partial HNSW (m 16, ef_construction 128), filters through the P4 join shape, 50 collections, 5% values shared by two collections, exact ground truth, 100 queries per case. Build 125 s, index 1.56 GB, plan uses the index. With `iterative_scan = relaxed_order`, ef 100 (recall@10 / results under k / p95 ms): current-only 2% visible 0.992 / 0 / 32, 21% 0.995 / 0 / 6, 100% 1.0 / 0 / 6; with 80% old versions 0.5% visible 0.906 / 0 / 113 (weakest case, thin margin), 5% 0.996 / 0 / 13, 24% 1.0 / 0 / 8. With iterative scan off, narrow cases fail (2% visible: 0.19, every query under k). Raising `max_scan_tuples` to 100k or ef to 400 did not lift the weakest case above 0.92; `strict_order` was worse (0.81). No row of the other `config_hash` and no row outside the filter was returned. Not measured: 1M vectors, real bge-m3 embeddings, search during publish, after GC/vacuum.

Physical choices: composite tenant/project foreign keys on every scoped table; `core.registry_keys` is the single source for error codes and other registered names; per-subscriber webhook delivery rows; `runtime.jobs` → `runtime.pipelines` and `job_stages` → `pipeline_stages` foreign keys; links keyed by `config_hash`; keyword `fts` column on chunks; L-20 tables (models, model access, resource leases) added later by an additive migration; `core.requests` replay store; audit partitioned by month, append-only including TRUNCATE; one open version per unit (closed before the new one opens); roles `core_owner`, `api`, `runtime`, `purge`, `maint`, `plugin_<id>` with no table privileges.

## 21. Design progress (Claude's estimate)

| Area | Specified | Implemented | Tested |
| --- | --- | --- | --- |
| Layers, invariants, layout | 90% | 0% | 0% |
| Platform Core (incl. management contracts) | 85% | 0% | 0% |
| Runtime | 85% | 0% | 0% |
| Knowledge Services | 85% | 0% | 0% |
| `plugin.yaml` | 85% | 0% | 0% |
| code-rag plugin | 80% | 0% | 0% |
| echo plugin | 85% | 0% | 0% |
| Physical DDL | 80% | 80% (builds on PG16 with stand-ins; plugin_code builds) | 4 checks (§20, TP-23) + counting CHECK (Appendix B) |
| Contracts and tests | 85% | 0% | TP-23 only |
| Vector index (O-05) | 90% | measurement only | 200k synthetic (§20) |
| Later plugins (sketch on purpose) | 25% | 0% | 0% |

Basis: Specified = the behavior has Pre/Txn/Out/Invariants/Tests in this document; Implemented = code or DDL that runs; Tested = a run that passed. Unverified assumptions: A-01 single index at 1M, real embeddings, during publish/GC; A-02 job FOR SHARE on worker calls with the stop gate does not starve terminal transitions (v0.2.4 V-25: without the gate 11-30 s waits at 16 workers, with it p99 79 ms); A-03 publish transaction length on large repositories; A-04 in-process plugins are trusted code (accepted by D-32, not an isolation claim).

Next: contract function bodies and the tests in §27, §33, §34 when code work is allowed (D-13); O-05 rerun at 1M with real embeddings in M2; O-02, O-12, O-20 stay HOLD.

## 22. v0.2.4 functions not yet covered (ADOPTED)

Basis: R T2 08:01 gap check against v0.2.4; reviews posted by Dy at T2 08:13 (reference only, P3 stays the base; D-24).

G1 webhooks F10: inbound deliveries are recorded in core.webhook_inbox. Outbound core.webhook_subscriptions(project-scoped) + core.outbox_deliveries(event, subscription, state, attempts, next_at) = per-consumer delivery rows (also fixes single delivered_at); Runtime's knowledge.published stop flag is an internal consumer, separate. outbox_events gets project_id. Secret encrypted like core.secrets (P-P12); HMAC over timestamp+body. https only, no redirects, destination allowlist (policy UNSET) incl. private ranges. Sent by maintenance loop, never the worker; per-subscription breaker (P-R8); after budget -> dead_letter + core.alerts. Payload = ids only, event id for dedupe. Membership checked at subscribe and at delivery.
G2 project delete F11: Core pipeline `core.project_purge`, idempotent resumable steps: deleting flag -> stop+fence jobs -> wait tasks -> plugin hook `<plugin>.purge_project(project)` run by plugin login (I-P2) -> knowledge, sources/revisions, artifacts, vol files, schedules, subscriptions, pins, model access rows (L-20) -> project. The code plugin's hook `code.purge_project` deletes its rows in batches of knowledge.delete_batch_max, is idempotent and is retried by the purge pipeline like any task. Explicit delete-order table (no CASCADE). Purge deletes representation_values linked only from this project in the same step; values shared with other projects stay; a weekly orphan GC covers the rest. Search and all user calls refuse a project that is not active (no search during the grace window). Delete order follows foreign keys; CASCADE exists only from a unit version to its chunks and links. Audit kept. Grace window policy core.purge_grace (UNSET) restorable before step 2. Models trained on project data (L-20): disabled at purge, admin decides (D-33, §30.3).
G3 per-file report: plugin writes items via contract `runtime.put_job_item(task_ctx, key, status, code, detail)` (incl. excluded files + reason); read via SECURITY DEFINER `runtime.job_items(principal, job, page)` (reader role, paging, error text redacted). Route /v1/jobs/{id}/items; code plugin may add /files view.
G4 snapshot retention: source_revisions.storage_state present|pruned, manifest kept. Keep latest revision per source, revisions of non-terminal jobs, failed-job revisions within window. Policy core.snapshot_retention in section 18 with constraint > max job deadline. Connector flag `refetchable` (git by SHA yes, upload no); pruned+not refetchable -> SNAPSHOT_EXPIRED. Upload revision_key = content sha256. Whole copies (no CAS blobs, code economy). Orphan-file sweeper.
G5 no-change fingerprint = manifest_hash + pipeline version/hash + values of policy_keys with affects_output=true (new flag in plugin.yaml schema section 15) + representation config_hash (model/template) + parser/rules versions. Computed and pinned at create_job with value snapshot (I-R1); collection_sources.last_fingerprint.
G6 exclusions (v0.2.4 rule): excluded stay in the coverage denominator; cap excluded/inventory <= knowledge.max_excluded_ratio (v0.2.4 value 0.5). Exclusion decided by rules before processing, distinct from failure. Excluded files are not units: plugin inventory + put_job_item; publish receives counts only. Reasons = registry item_type exclusion_reason.
G7 retry_job(principal, job): operator; new job on same revision, terminal never reopened (I-R7); only if revision is still latest for its source scope else REVISION_STALE; bypasses NO_CHANGE (job.force); pins original pipeline version by default (option current); per revision cap (policy) + audit; api grant. Reuse caches make rerun cheap.
G8 graph gate in code plugin: baseline = quality summary of latest published job for (collection, source) in plugin_code; first run skips; mode warn|approve|fail (approve via P-R10); code code.graph_quality_regressed retryable=false in section 14.
Also: L-10 add hwpx handler (Contents/section*.xml); hwp needs conversion (HOLD). M4 OWUI tools: async HTTP, no blocking requests in async methods.
Miniweb files tool: Dy T2 08:13: handled later together with the Nextcloud/L-16 connector work; not in P4 scope.

## 23. P3 review fixes (ADOPTED)

Basis: reviews posted by Dy at T2 08:09 and 08:10 (reference only, D-24).

R1 publish ordering: see §24.
R2 explicit deletes: see §24.
R3 snapshot_finalized (bytes copied, immutable) != input_complete (all manifest paths present). Publish full mode requires input_complete; incomplete -> holds per section 7.6.
R4 jobs get plugin_version + pipeline_hash; registering same version with different hash -> PLUGIN_VERSION_CONFLICT. Upgrade: running jobs keep pinned version only if loaded; else drain (no new claims for new version until old jobs terminal) - code economy, no multi-version loading.
R5 per-job policy snapshot: jobs.policy_snapshot jsonb (all keys the pipeline reads) set at create_job; tasks read only that.
R6 coverage: plugin.yaml unit_kinds[].required_representations[] (§15); publish checks each staged unit_version has them for the active config_hash; thresholds knowledge.min_coverage (UNSET) + G6 excluded cap.
R7 full-mode kind scope: ends scoped to kinds declared by the job's pipeline version; kinds dropped by a newer version are ended by an explicit migration job (R2 path), not silently orphaned.
R8 Worker Protocol auth: claim/heartbeat/complete/fail/split check session_user -> plugin login mapping (no caller-supplied plugin param); user functions take principal and check membership; two grant sets. claim returns only tasks of the caller's plugin. fail() drops `retryable`; retryability comes from the error code registry.
R9 error codes single source (§14): core.registry_keys (item_type error_code, spec holds layer, retryable, health_sample); tasks and task_events reference it by (error_type, error_code).
R10 attempt/I-R4: tasks.attempt mutable counter allowed; task_events append-only is the I-R4 record (rewrite I-R4 wording). Breaker samples move from jsonb hot row to task_events(resource) + window query.
R11 claim fairness: round-robin across (tenant, project) by last_claimed_at; per-pipeline concurrency limit policy runtime.max_running (UNSET).
R12 OWUI interim rule: decided as D-31 (contributor, not reader, because chat ingest writes); see §30.1.
R13 plugin trust: decided as D-32; see §30.2.
R14 GPU lease (L-20): lease executor = a Core maintenance action calling a configured host command (deploy/gpu-hook.sh stop|start vllm), not docker socket in api; lease covers stop -> train -> start -> health check.
R15 approval binds artifact sha256; changed dataset invalidates approval; approval wait expiry runtime.approval_ttl (O-17). An approval without an artifact (G8) binds to the hash of the stored decision payload.
R16 lora deploy: models.desired_state vs observed_state reconciled by maintenance.
R17 GC: no pins -> floor = current_gen; pin TTL cap per principal policy.

## 24. Publish ordering, explicit deletes and fences (ADOPTED)

Basis: R T2 08:32, 08:58, 10:14; reviews posted by Dy at T2 08:23, 08:33, 10:11. Status: specified.

Two guards, one per mode. Scope S = collection_sources(collection, source, pipeline_key). Seq comes from the global sequence runtime.job_seq (§26 F1) and is compared only within one collection.
- full mode: scope guard. Publish needs job.seq > S.last_seq. Coalescing (queued -> superseded) and stop-older apply only to full mode.
- delta mode: per-unit guard. units.last_seq = seq of the last job that changed the unit; a change from job j applies only if unit.last_seq < j.seq, else that unit is skipped (job_item 'stale'). No coalescing; delta jobs are independent.
- Explicit deletes only in delta collections. In full mode a unit is removed by leaving it out of the source (refuse FULL_MODE_COLLECTION). So ends = explicit deletes (delta) or omissions (full), never mixed.

C1 create_job(principal, project, collection, pipeline, source_revision?, force, request_id)
 Pre: contributor on project (checked now); project active; plugin enabled for project; input format matches; revision storage_state present (pruned+refetchable -> refetch first; else SNAPSHOT_EXPIRED); retry: revision is latest for S else REVISION_STALE.
 Txn: upsert S, lock S FOR UPDATE; replay check (created_by, request_id) -> REPLAY with first result; fingerprint (G5); NO_CHANGE only if !force and fingerprint = S.last_fingerprint and no non-terminal job in S (else a running older job could publish over it); seq = nextval(runtime.job_seq) (S already locked, so seq order = S order); insert job + policy_snapshot; full mode: queued jobs in S with lower seq -> superseded; lifecycle + outbox in same txn.
 Out: CREATED | NO_CHANGE (terminal job row, S.last_seq = seq) | REPLAY | FORBIDDEN | PROJECT_DELETING | PLUGIN_DISABLED | INPUT_FORMAT_INCOMPATIBLE | SNAPSHOT_EXPIRED | REVISION_STALE.
C2 delete_unit(principal, collection, unit_keys[], request_id)
 Pre: delta collection; contributor now; deleting another principal's unit needs operator; count <= knowledge.delete_batch_max (UNSET) else BATCH_TOO_LARGE.
 Txn: replay check; create job pipeline core.delete@1 (single publish stage, ready); candidate_keys mode delete per found unit; missing keys returned, not an error for the batch.
 Out: ACCEPTED(job, missing[]) | REPLAY | FORBIDDEN | FULL_MODE_COLLECTION | BATCH_TOO_LARGE.
C3 publish(job) - Core stage, run by runtime login
 Pre: job running, no stop_requested, earlier stages succeeded, approval hash = current artifact sha256 (R15).
 Txn: (0) job.fence_seq >= collection.fence_floor else FENCE_EXPIRED (§26 F2). (1) lock collection FOR NO KEY UPDATE (gen allocation; publishes of one collection serialize; FK checks from create_job are not blocked, TP-23). (2) full: lock S, if job.seq <= S.last_seq -> SUPERSEDED, discard staged rows. (3) coverage (R6, G6) else fail knowledge.coverage_failed (non-retryable), discard staged. (4) g = current_gen+1. (5) change set = staged units ∪ ends; delta drops units with last_seq >= job.seq. (6) close old open versions (valid_to_gen = g) BEFORE opening staged ones (valid_from_gen = g, candidate_job NULL). (7) units.last_seq = job.seq for applied units. (8) nothing applied -> NO_CHANGE, gen not bumped; else current_gen = g, generations row. (9) S.last_seq/last_revision/last_fingerprint (full; delta: last_seq = max). (10) job succeeded, outbox knowledge.published(project, collection, gen, source, seq). (11) full: older running jobs in S get stop_requested superseded.
 Out: PUBLISHED(gen) | NO_CHANGE | SUPERSEDED | COVERAGE_FAILED | ALREADY_TERMINAL (replay after commit) | STOPPED.
Invariants: PI-1 full: published seq strictly rises per S. PI-2 delta: a unit never moves back to an older job's state. PI-3 at most one open version per unit. PI-4 a version is visible at gen g iff from <= g < to; staged rows never visible. PI-5 a publish ends only units of its own source and pipeline kinds (R7). PI-6 outbox row exists iff the state change committed. PI-7 a delete issued after an ingest wins over it; an ingest issued after a delete recreates the unit.
Tests (TP-01..10): two publishers same S; old full job after newer -> SUPERSEDED; delta stale unit skipped; delete then older ingest -> stays deleted; delete then newer ingest -> recreated; full omission ends only own source+kinds; empty publish -> NO_CHANGE no gen; crash mid-txn then rerun -> one gen; NO_CHANGE refused while a job runs in S; coverage fail leaves no staged rows.
DDL: units.last_seq, jobs.request_id + UNIQUE(created_by, request_id) added; builds on PG16.
Additional rules: fence durability.
- units.last_seq lives on the units row; deletion only closes the open version (valid_to_gen), the units row stays, so the fence survives delete.
- Fence is checked and written inside C3 under the collection FOR UPDATE lock, so concurrent publishes of A and B serialize; staged rows of the losing job are dropped for those units.
- Gap fixed: delete of a key with no unit yet -> C3 inserts a tombstone units row (no versions, last_seq = delete seq), so an older ingest cannot create it later. C2 no longer reports missing keys as skipped; it reports them as tombstoned.
- Tombstone/unit row GC only when no non-terminal job in the collection has seq < units.last_seq.
- Test TP-11: delete of never-seen key, then older ingest publishes -> unit stays absent.

- One ordering domain per collection: seq from runtime.job_seq, compared only within the collection. Every fence (S.last_seq, units.last_seq) is compared with jobs.fence_seq inside the same collection; a unit belongs to exactly one collection. Supersession of queued full jobs stays scoped to S.
- Retry gap found: a delta retry got a new, higher seq and could overwrite newer state. Fix: jobs.fence_seq = seq for a new job, = original job's fence_seq for retry_job. Fences compare fence_seq; seq stays the unique allocation key.
- Fence removable (unit row with no open version, incl. placeholders) iff no job in the collection is non-terminal with fence_seq < units.last_seq. Non-terminal covers queued, running, retry_wait tasks, expired leases (task back to ready, job still running), waiting_approval, and retry-created jobs.
- Terminal is final for writes: entering a terminal state discards the job's staged rows in the same txn; every staging and complete call checks job non-terminal + lease_token/epoch, so no late commit after terminal (STALE_LEASE / ALREADY_TERMINAL). Publish is a Core stage, never a worker call.
- Future retries: GC sets collections.fence_floor = min fence_seq of non-terminal jobs (or the current runtime.job_seq value if none) before removing fences; retry_job with fence_seq < fence_floor -> RETRY_TOO_OLD. So a removed fence can never be needed again.
- PI-8: units.last_seq never decreases; it is written only by a winning change (UPDATE ... WHERE last_seq < fence_seq). A losing (stale) change only drops its own staged rows and never touches the unit row. Unit rows are deleted only by fence GC or project purge.
- Tests: TP-12 delta retry of old job after newer publish -> no overwrite; TP-13 fence GC with a queued older job -> fence kept; TP-14 retry below fence_floor -> RETRY_TOO_OLD; TP-15 stale ingest rejected leaves placeholder last_seq unchanged.

## 25. Contract functions (ADOPTED)

Basis: R T2 10:14. Status: specified, not implemented. This list replaces the function table of §20.

Common: user functions (api login) take principal_id and check membership now; worker functions map session_user -> plugin login (R8). Replay key: user = (principal, request_id); worker = (task_id, request_id) via uq_task_events_request; replay returns the first result. Every state change writes lifecycle/task_events + outbox in the same txn.

Worker Protocol (grant: runtime, plugin_<id>)
W1 claim(max, request_id): Pre caller plugin enabled; Txn pick ready tasks of caller's plugin with ready_at <= now, job non-terminal, no stop_requested, resource breaker not open, under runtime.max_running; round-robin by (tenant, project) last claim (R11); FOR UPDATE SKIP LOCKED; status running, lease_epoch+1, new lease_token, lease_until, attempt+1. Out tasks[] with job policy_snapshot | EMPTY. Inv one live lease per task.
W2 heartbeat(task, token, epoch, checkpoint_ref?, extend_deadline?): Txn extend lease_until if token/epoch match and job not stopping; store checkpoint_ref; extend deadline_at within runtime.deadline_extension_max. Heartbeats are not stored as task_events. Out OK | STALE_LEASE | STOP (stop_requested set; worker should finish fast) | ALREADY_TERMINAL.
W3 complete(task, token, epoch, result, request_id): Pre lease match. Txn status succeeded, resolution ok; resolve parent if all children resolved (split resolution derived); stage done -> unblock dependents; last stage before publish -> publish stage ready. Out OK | REPLAY | STALE_LEASE | ALREADY_TERMINAL.
W4 fail(task, token, epoch, error_code, detail, request_id): Pre code registered (registry_keys). Txn retryable (from registry) and attempts left (max_attempts budget) -> retry_wait with backoff; else failed + job failure path (stop_requested failure, job failed when running tasks end). health_sample codes counted for breaker via task_events.resource_key. Out RETRY_SCHEDULED | FAILED | REPLAY | STALE_LEASE | UNKNOWN_CODE.
W5 split(task, token, epoch, children[{key, scope, payload}], request_id): Pre stage kind item, depth < split_max_depth, count <= split_max_items. Txn task status split, children ready (UNIQUE parent_id, child_key gives idempotency). Out children ids | REPLAY | SPLIT_LIMIT | STALE_LEASE.
W6 stage writes (put_unit_version, put_chunks, put_representation, touch, hold, put_job_item): Pre caller holds a live lease for a task of that job; unit kind and rep kind registered by caller's plugin; dims match registry (DIM_MISMATCH). Txn insert staged rows (valid_from_gen NULL, candidate_job = job); representation_values upsert by (tenant, rep_kind, config_hash, input_hash) for reuse; candidate_keys touch/hold. Out OK (same response and code path for new and reused values, no cross-project signal) | STALE_LEASE | DIM_MISMATCH | NOT_REGISTERED. Inv staged rows invisible; one staged version per (unit, job).

User and operator (grant: api)
U1 get_job / job_items(principal, job, page): reader; redacted error text. 
U2 cancel_job(principal, job, request_id): contributor (own job) or operator. Txn queued -> canceled now; running -> stop_requested cancel, job canceled when running tasks end (staged rows discarded then). Out OK | ALREADY_TERMINAL | FORBIDDEN.
U3 retry_job(principal, job, use_current_pipeline, request_id): operator; original terminal and not succeeded; full: revision still latest for S else REVISION_STALE; fence_seq >= fence_floor else RETRY_TOO_OLD; per-revision retry cap (policy) else RETRY_LIMIT. Txn new job (force, retry_of, fence_seq inherited), audit. Out CREATED | REPLAY | ...
U4 approve(principal, job, stage, artifact_sha256, decision): operator; stage waiting_approval, now() < wait_until (= entered waiting_approval + approval_ttl from the job's policy_snapshot), sha256 equals current artifact. Approve and expire both lock the job FOR UPDATE; the first to commit wins, the other gets ALREADY_DECIDED; expiry is one txn (stage failed, job failed core.approval_expired, staged rows discarded, outbox) and never implies approval. Txn approved -> stage succeeded; rejected -> job failed (core.approval_rejected); expiry by maintenance -> core.approval_expired. Out OK | HASH_MISMATCH | EXPIRED | ALREADY_DECIDED.
U5 search(principal, projects[], collections[], query_text, query_vectors{config_hash: vec}, filters, k, pin?): reader on every project (any missing -> FORBIDDEN, P-C5); projects active; each collection belongs to one of them; uses current_gen or the pinned gen; query vectors must match each kind's active config_hash (else CONFIG_MISMATCH; caller embeds, SQL never embeds). Txn read-only, REPEATABLE READ snapshot. Out hits with unit/chunk ids, gen, scores | NOT_FOUND | FORBIDDEN.
U6 pin(principal, collection, ttl) / unpin: reader; ttl <= policy max; per-principal pin cap. Out pin id, gen.
U7 subscribe_webhook / unsubscribe (G1): operator; url on allowlist. Out id | DESTINATION_NOT_ALLOWED.
U8 delete_project(principal, project, request_id): admin of tenant. Txn state deleting, delete_after = now + core.purge_grace, stop_requested purge on all non-terminal jobs. restore_project before delete_after. Out OK | ALREADY_DELETING.

Maintenance (grant: maint; purge login for G2 steps)
M1 sweep_leases: running tasks with lease_until < now -> ready, or failed core.attempts_exhausted when attempt >= runtime.max_attempts; one UPDATE ... WHERE status = 'running' AND lease_until < now() per row with SKIP LOCKED, so competing sweepers cannot exceed the limit; tasks pinned to a plugin version no longer loaded stay ready and raise core.plugin_version_missing; job deadline passed -> fail core.deadline_exceeded.
M2 finish_stopped_jobs: jobs with stop_requested and no running tasks -> terminal (canceled / failed / superseded), staged rows discarded, same txn.
M3 gc(collection): Pre: collection FOR NO KEY UPDATE NOWAIT; skip if busy. floor = min(active pin gens) or current_gen if no pins (R17); delete unit_versions with valid_to_gen <= floor; fence_floor update then fence GC (§24); orphan representation_values with no links (tenant-wide pass).
Management (grant api, user functions; bootstrap by `coreadm` as superuser-created first admin): tenant_create, project_create, member_set, member_remove, plugin_register / enable_for_project / disable, service_client_create, token_issue (SECURITY DEFINER; reads secret_hash inside and returns only the verify result; argon2 runs in api on a per-call salt fetched by a definer function), signing_key_rotate, policy_set, artifact_authorize_download, source_create, binding_set, webhook_subscribe. Each takes principal_id and request_id (core.requests).
M4 deliver_webhooks, M5 snapshot_retention (G4), M6 purge_project steps (G2, plugin hooks), M7 alerts.
Cross-contract checks still to do: lock order (collection -> S -> units -> tasks) used by C1, C3, M2, M3; replay keys unique per contract; every terminal transition discards staged rows.

## 26. Cross-contract review: lock order, replay, recovery (ADOPTED)

Basis: R T2 11:11; review posted by Dy at T2 10:14. Status: specified.

Findings (issue -> fix -> contract/invariant):
F1 Lock order: a per-collection counter would make C1 lock S then the collection while C3 locks the collection then S (deadlock risk), and C1 would wait behind every long publish. Rule: seq from one global sequence runtime.job_seq; C1 never locks the collection. Ordering stays per collection because comparisons only ever happen within one collection; within S, S FOR UPDATE is taken before nextval so seq order = S order. [C1, C3; X4]
F2 In-flight job vs fence GC: a create_job that allocated seq 15 but has not committed is invisible to GC, which could remove a fence with last_seq 20, so job 15 later recreates a deleted unit. Fix: C3 refuses any job with fence_seq < collections.fence_floor (FENCE_EXPIRED, non-retryable, staged rows discarded). GC raises the floor and removes fences in the same txn under the collection lock, and publish reads the floor under that lock, so every publish path enforces the same rule as retry_job (U3 check stays as early refusal). [C3, U3, M3; X1, X3]
F3 Claim vs terminal race: M2 could end a job while W1 commits a claim for it. Fix: canonical job -> task order; every worker call takes job FOR SHARE then task FOR UPDATE and checks job non-terminal and no stop; M2/U2 take job FOR UPDATE. [W1-W6, M2, U2; X4]
F4 Replay: user ops other than job creation had no replay store. Fix: core.requests(principal, request_id, op, args_hash, result) written in the same txn; same id + different args -> REQUEST_ID_REUSED. Worker ops keep (task, request_id) in task_events with args_hash in detail. Stage writes are idempotent by natural keys and replace the staged (unit, job) version. [all; X2]
Canonical lock order: project -> collection -> collection_sources (S) -> jobs (ORDER BY id) -> job_stages -> tasks (child before parent) -> units (ORDER BY id) -> unit_versions/chunks/links -> representation_values. Per contract: C1 project SHARE, S, jobs | C2 project SHARE, S, jobs | C3 collection, S, jobs, units, versions | W* job SHARE, task, parents, job_stages | M1 job SHARE, task | M2 job, versions | M3 collection NOWAIT, units, versions, values | U2 job | U8 project, jobs | U6 none (pin at current_gen only; GC floor never removes rows visible at current_gen). No contract takes a lock earlier in the order after a later one.
Replay matrix: C1/C2/U3 core.requests + jobs UNIQUE(created_by, request_id) | U2/U4/U7/U8 core.requests | W1 claim (login, request_id) -> same tasks if same epoch still live | W2-W5 (task, request_id) | W6 natural keys | C3 publish: job terminal -> ALREADY_TERMINAL + stored outcome | webhooks (event, subscription), subscribers dedupe by event id | approve: decision stored once, same decision -> OK, different -> ALREADY_DECIDED.
Recovery: every contract is one txn, so a crash before commit leaves nothing; a crash after commit is found by replay. Multi-step work: snapshot copy (storage_state writing -> orphan sweeper), file write before DB row (row only after fsync; sweeper removes files with no row), webhook send (send then mark; crash -> resend, at-least-once), project purge (state machine, each step idempotent), GPU hook (idempotent stop/start, observed_state reconcile). GC works in batches; each batch raises the floor before deleting fences in the same txn, floor never decreases.
Cross-contract invariants: X1 no stale authority (epoch fencing, fence_seq, fence_floor at publish). X2 one logical outcome per operation identity. X3 no cleanup ahead of recovery (fence GC needs no non-terminal job below last_seq; publish rechecks floor). X4 atomic lifecycle (terminal under job FOR UPDATE; workers need job FOR SHARE + non-terminal).
New tests (distinct failure modes only): TP-16 create_job in flight during fence GC -> FENCE_EXPIRED at publish; TP-17 request_id reused with different args -> REQUEST_ID_REUSED; TP-18 claim racing cancel -> no task runs after terminal; TP-19 two siblings complete together -> parent resolved once, dependents unblocked once.
DDL: runtime.job_seq and core.requests; builds on PG16.

## 27. Traceability and tests (ADOPTED)

Basis: R T2 20:31, 20:35; reviews posted by Dy at T2 11:12, 20:31, 20:35. Status: specified.

Naming: P3 invariants keep I-x (I-R7, I-K2...). Publish invariants PI-1..PI-8 (§24). Cross-contract invariants X1..X4 (§26). Review findings F1..F4 (§26). Tests TP-xx; (c) = concurrent test that runs the competing calls at the same time, not in sequence.
Boundaries (exact):
- Cleanup boundary: publish and retry refused iff fence_seq < fence_floor; fence_seq = fence_floor is allowed. Floor = min fence_seq of non-terminal jobs in the collection (none: current job_seq value), so a job at the floor is always protected. All three values come from the one job_seq domain.
- Unit fence: a change applies iff units.last_seq < fence_seq; equal means already applied (retry of a job that already won) and is skipped, not an error.
- Fence removal: unit row with no open version removed iff no non-terminal job in the collection has fence_seq < last_seq.
- Replay: same id + same args_hash -> first result returned, no new effect; same id + different args_hash -> REQUEST_ID_REUSED, no effect.
Matrix (invariant: contracts -> tests):
- PI-1 full seq rises per S: C1, C3 -> TP-01(c), TP-02
- PI-2 delta unit never moves back: C3, U3 -> TP-03, TP-12
- PI-3 one open version per unit: C3 -> TP-01(c), TP-08
- PI-4 visibility by gen; staged invisible: C3, U5, M3 -> TP-10, TP-20(c) new
- PI-5 publish ends only own source+kinds: C3 -> TP-06
- PI-6 outbox iff commit: C3, all -> TP-08 (exactly one knowledge.published after crash+rerun)
- PI-7 delete/ingest issue order wins: C2, C3 -> TP-04, TP-05, TP-11
- PI-8 last_seq monotonic, loser never writes unit row: C3 -> TP-15
- NO_CHANGE rules: C1, C3 -> TP-07, TP-09
- X1 no stale authority: W2-W6, C3, U3 -> TP-12, TP-14, TP-16(c), TP-21 new
- X2 one outcome per identity: all user ops, W1-W5 -> TP-17 (a same args, b different args)
- X3 no cleanup ahead of recovery: M3, C3 -> TP-13, TP-14 (floor-1 refused, floor allowed), TP-16(c)
- X4 atomic lifecycle: W1-W6, M2, U2 -> TP-18(c), TP-19(c), TP-21
- Recovery of multi-step work and no job stuck non-terminal: M1, M2, M5, M6, M4 -> TP-22 new, EC-03
Gaps found and added (distinct failure modes only):
- TP-20(c): search under REPEATABLE READ while publish and GC run -> results come from exactly one gen, never a mix, never a staged row.
- TP-21: every worker entry point (heartbeat, complete, fail, split, put_unit_version, put_chunks, put_representation, touch, hold, put_job_item) x {stale epoch, other plugin login, job terminal} -> STALE_LEASE / FORBIDDEN / ALREADY_TERMINAL, no rows written. Covers the X3/X4 point that the job lock and lease check apply to all entry points, not only claim.
- TP-22: kill between steps of snapshot copy, project purge, webhook send, GC batch; restart -> work resumes, no duplicate effect beyond documented at-least-once webhook resend, floor never decreases, every job reaches a terminal state within its deadline (M1 deadline_exceeded, M2 finish).
- TP-23 (tested, PG16): collection row FOR UPDATE blocks an FK insert (KEY SHARE); FOR NO KEY UPDATE does not.
- TS-01: fails if a knowledge function with a vector ORDER BY lacks hnsw.iterative_scan in pg_proc.proconfig.
Total: TP-01..23 + EC-01..08 + ST-01..20 + TS-01 + CH-01..11 + BR-01. Every invariant has a direct test; every concurrency invariant has a (c) test.
Additional rules:
- TP-20 checks every returned row against the returned gen (valid_from_gen <= g < valid_to_gen, none staged). Single-call search relies on its REPEATABLE READ snapshot (GC deletes stay invisible to it); a multi-call search (paging) must pin, and TP-20b runs GC between pages with a pin and expects the same gen's rows.
- TP-22 liveness = terminal state, not success: a job that cannot recover reaches failed (core.deadline_exceeded, or the step's error code) by jobs deadline (policy runtime.job_deadline, UNSET); staged rows discarded. Success is never implied by the deadline.
- Tidy rule for P4: per contract one chain Pre -> Txn -> Out -> Invariants -> Tests -> Recovery; evidence kept in three columns: DDL-enforced, contract-enforced, tested.
- Evidence: implemented and tested items are listed in §20 and §21; everything else here is specified.

## 28. echo HTTP binding, M4 deploy layout, plugin migrations, observability (ADOPTED)

Basis: R T2 03:10 working notes. Status: specified.

### 28.1 echo HTTP binding test (EC-01..EC-08)
- echo handler runs as a separate process `plugins/echo/server.py` (stdlib http.server, no framework, ~60 lines) exposing the Worker Protocol client side: it polls `POST /runtime/v1/claim` and calls `/heartbeat`, `/complete`, `/fail`, `/split` on the Core API with its service token (plugin principal `plugin.echo`).
- HTTP binding = thin FastAPI router in Core mapping 1:1 onto the SQL contracts (same request/response JSON as in-process). Auth: service token with scope `runtime.worker:<plugin>`; the token's plugin must equal the task's pipeline plugin.
- Test EC-01..EC-08: (1) item split to depth 2 produces 1+N+N^2 tasks, all resolved ok; (2) replay split over HTTP returns same children; (3) kill worker mid-task → sweeper returns task to ready, attempt+1; (4) stale token complete → STALE_LEASE; (5) echo.vec dims 8 vs registered 4 → DIM_MISMATCH; (6) delta publish adds rows without ending others; (7) I-K3 name check passes with code + echo loaded; (8) same messages via in-process binding produce identical task_events (excluding ids/times) → proves transport neutrality (D-15).

### 28.2 M4 deploy layout
Services (docker compose, one host):
| service | image | login | mounts |
| postgres | pgvector/pgvector:pg18 | superuser only for init | <root>/pgdata |
| init | same as api | superuser only to create roles and extensions, then SET ROLE core_owner (Core migrations) and plugin_<id>_owner (plugin migrations) | <root>/plugins:ro |
| api | coderag-core:0.4 | api | <root>/plugins:ro, <root>/vol/* |
| worker | coderag-core:0.4 | runtime, maint, purge + plugin_<id> (one pool per login) | <root>/plugins:ro, <root>/vol/* |
| embed | TEI bge-m3 (existing) | - | models |
| external | vLLM, Open WebUI (existing, joined via connect.sh) | - | - |
- One image for api and worker (code saving); role chosen by command.
- Secrets: `.env` holds DB passwords per login, host key path for connector secret encryption, JWT signing private key path; never in plugins folder.
- Volumes: `deploy/volumes.yml` generated from `<root>/vol/*` folders (one bind per disk); `init` registers each in core.volumes.
- Offline bundle (air-gapped, memory 2026-10-14): images saved as tar, wheels for plugin deps, bge-m3 weights; `bundle.sh` / `install.sh` pattern from v0.2.4 kept.
- Plugin code is edited on the host (D-10); host write access to `<root>/plugins` is the trust boundary.
- Bootstrap: `coreadm init-tenant`, `coreadm add-admin`, `coreadm issue-client` (v0.2.4 issue-key / admin-key).
- Minimum backup set (before L-14): DB dump, then volume copy, then the keys folder (JWT signing key, secret encryption key). Restore DB, then volumes; the sweeper reconciles rows without files (storage_state pruned + alert) and files without rows (removed). Losing the keys folder makes stored connector secrets unrecoverable. Test BR-01 (restore drill).
- Health: api `/core/v1/health` (DB, embed, llm reachability, plugin load status); worker heartbeat row.

### 28.3 Plugin schema migrations and upgrades
- Each plugin ships `migrations/NNN_name.sql`; Core table `core.plugin_migrations(plugin_id, number, sha256, applied_at)`; applied by `init` as `plugin_<id>_owner` (owns only `plugin_<id>`; Core migrations as core_owner) inside one transaction per file, in order; a changed sha256 for an applied number → refuse (immutable migrations).
- Plugin upgrade = new version row + migrations; old pipeline versions stay while jobs reference them; a migration must not break tables used by old pipeline versions still running (rule: additive only while old versions have non-terminal jobs; destructive migration waits until none).
- Core upgrade: same mechanism for `core`, `runtime`, `knowledge` (owner `core`); `core.schema_version` stamp written last (v0.2.4 F00 pattern, D-07).
- Downgrade: not supported; restore from the minimum backup set (§28.2); remote DB backup plugin L-14 later.

### 28.4 Observability
- Metrics from SQL views (no metrics stack required; code saving): runtime.v_queue (ready/running/retry by stage), v_job_durations, v_breaker_state, knowledge.v_collections (current_gen, version counts, staged rows), v_gc_backlog, core.v_plugins.
- Alerts as rows in `core.alerts(code, severity, object, first_at, last_at, count)` raised by maintenance: breaker open, hold older than knowledge.hold_alert_after, stuck stage (no progress within policy), GC skipped while busy, plugin refused, webhook rejects, approval waiting.
- Retention: task_events, lifecycle_events, job_items, outbox_events and outbox_deliveries, core.requests, webhook_inbox, schedule_runs, alerts are purged by maint in batches after `core.retention.<table>` (UNSET); audit by partition.
- knowledge.v_collections reports the old-version ratio per collection; alert above a policy ratio (UNSET).
- Logs: JSON lines with job/task ids; secrets redacted by a log filter (P-P12).
- Open WebUI tool can call `/core/v1/status` for a summary.

## 29. Open WebUI chat requests: pasted code and attachments (ADOPTED; D-27, D-28)

Basis: T2 20:57, 20:58; review posted T2 20:58. Status: specified.

Need: in Open WebUI, code typed in the chat and attached files are handled from a plain-language request ("이거 분석해줘", "이 파일들 저장해줘") with no command, function name or project id.
v0.2.4 gap (old/deploy/openwebui/code_rag_tool.py): separate functions analyze_pasted_code / analyze_attached_files / search_code each need `project` (search needs numeric id); the model must pick the right one, which small local models often miss; Open WebUI may also inject attachments into its own RAG instead of passing them.
Design:
- OWUI filter (inlet) on the code-rag model runs on every message, no model choice needed: extracts pasted code (fenced blocks; unfenced code by the existing line heuristic) and attachments (bytes via the files API, not OWUI's own retrieval; OWUI file-context injection disabled for this model).
- Intent from the plain text, rules first, LLM only if unclear: save/register/index (저장, 등록, 반영, 인덱스) -> ingest into a project; otherwise -> analyze for this answer. Question with no code/attachment -> search.
- Project resolution: named in the text (fuzzy match on project names the principal can read) -> user default (OWUI user valve) -> the per-chat scratch source in the configured scratch project (§30.1; retention policy core.scratch_ttl, UNSET). Never asks for an id; asks one short question only if two projects match equally.
- One tool function `code_rag(request)` remains for the model to call in follow-ups ("상태는?", "다시 검색해줘"); it reuses the chat's last project and last job.
- Core: core.upload accepts text pastes and files: paste -> file_tree@1 with path from the fence info string or detected language (`pasted/<n>.<ext>`); archives safe-extracted with size caps; revision_key = content sha256 so a repeated paste is NO_CHANGE.
- Result: wait up to a policy limit with progress events, then answer with summary + top hits; longer jobs return a plain-language status line and the follow-up "끝났어?" works.
- Async HTTP in the filter/tool (no blocking requests in async methods).
- Limit (D-31): all OWUI users share the OWUI principal, so per-chat scratch data is separated only by the tool's source filter, not by Core membership.
Tests: CH-01 fenced paste analyzed without project; CH-02 unfenced paste detected; CH-03 zip + 2 files attached, "저장해줘" -> ingest into default project; CH-04 two equal project matches -> one question; CH-05 same paste twice -> NO_CHANGE; CH-06 "끝났어?" after a long job -> status of last job.
Language rule (D-28):
- Order: fence info string -> file extension -> content check (shebang, parser trial over the languages the code plugin registers). Clear = exactly one language passes.
- Unclear (none or several pass): nothing is processed; the filter asks one short question listing the candidate languages (or all registered ones), e.g. "이 코드는 어떤 언어인가요? python / java / c". The paste/attachment is held for that chat; a plain-text answer ("파이썬") resumes it. For attachments, one question covers all unclear files and names them; clear files wait so the batch is processed together.
- A held item expires with the chat's scratch retention; a new paste replaces the question.
Tests: CH-07 unfenced ambiguous snippet -> question, answer "java" -> analyzed as java; CH-08 zip with .txt code file -> one question naming that file.

## 30. Decisions applied (D-31..D-34)

### 30.1 Open WebUI access (D-31)

- The Open WebUI service principal holds `contributor` on each project an operator configures for it, and on one configured scratch project per tenant. It can search, upload and start jobs there; it cannot manage members, policies or projects.
- The Open WebUI user name and id go only into `created_by_attrs` / `on_behalf_of` as unverified attributes (I-S10). They are shown as "reported by Open WebUI" and never used for an access decision.
- Per-chat scratch data (§29) is a source `chat:<chat_id>` (connector `core.upload`) inside the scratch project, not a project per chat, because the principal cannot create projects. The tool always passes `filters.source_ids = [that source]` when searching scratch data. Expiry (`core.scratch_ttl`, UNSET) runs an empty full revision for that source, which ends its units in scope (§24); the source row is then removed by GC. No new code path.
- Limit: all Open WebUI users share one principal, so per-chat separation is a tool rule, not Core access control (ST-25). This ends when delegation exists (D-34).

### 30.2 Plugin trust (D-32)

- In-process plugins are trusted code: an operator installs them, `plugins.dir_hash` (sha256 of the folder) is recorded at registration and shown in the plugin list, and a changed hash on restart needs a new registration (`PLUGIN_VERSION_CONFLICT` for the same version).
- I-S7 then guards against SQL mistakes only; it is not a security boundary between in-process plugins.
- Untrusted or third-party code runs only through the HTTP binding (D-15) with its own service token and its own `plugin_<id>` login, and never gets DB credentials.
- No sandbox now; L-19 arbitrary R code still needs its own sandbox design (O-10).

### 30.3 Models from a deleted project (D-33)

- The purge pipeline (G2) adds one step before knowledge deletion: models whose dataset sources or merge parents include the project are undeployed (`lora.deploy` undeploy) and set `disabled` with reason `core.project_purged`. Same idempotent step rules as the other purge steps.
- An admin then deletes the model or keeps it with a recorded reason (audit). Keeping it does not re-grant access: the model's access stays the intersection of its remaining source projects; with none left, only tenant admins can use it.
- The L-20 tables are added by an additive migration (§20), so this step is a plugin purge hook `lora.purge_project`; Core only orders it before knowledge deletion.

### 30.4 Chat archive scope and delegation (D-34)

- L-15 starts with project-shared archives: saved notes and files belong to the project, readable by its readers; the Open WebUI user name is an unverified attribute only (§30.1), with no claim of personal ownership.
- Personal archives need verified per-user delegation (RFC 8693 token exchange, `sub` + `act` in audit, I-S8). It is built as part of L-15, and personal scope stays disabled until it passes its security tests.

## 31. code plugin (L-01) on the v0.4 content model (PROP-A)

Basis: v0.2.4 `old/db/ddl` 02-06 (files, symbols, dependencies, call_graph, graph_quality_summaries, code_chunks, knowledge_cache, embedding_cache) mapped onto Core tables; D-12. Status: specified; Appendix B implemented as DDL.

Mapping (v0.2.4 → v0.4):

| v0.2.4 | v0.4 |
| --- | --- |
| files | unit kind `code.file` (key = path; attrs language, content_class, size, sha256) + `runtime.job_items` (status, excluded_reason, error_code); no plugin table |
| symbols | unit kind `code.symbol` (key = qualified name, parent `code.file`; attrs kind, signature, decorators, is_async, visibility, start_line, end_line, path) |
| code_chunks | `knowledge.chunks` of the symbol version (text = normalized code; attrs path, lines, symbol id, language, summary, summary_ko, keywords for FTS) |
| dependencies, call_graph (resolved) | attrs of the file/symbol version (`imports[]`, `calls[]` as qualified names, `external[]`); edges version with their unit and are visible only at published generations (O-21) |
| knowledge_cache | representation `code.summary` (target chunk, json `ks-v1`), input_hash = sha256 of prompt version, model identity, context builder version, graph context status and chunk text; reused across jobs and projects of a tenant (I-K2) |
| embedding_cache | representations `code.embed` (chunk, 1024, code template) and `code.knowledge_embed` (chunk, 1024, summary template) |
| generations, search_* | Core publish and search |
| revision_chunks, code_occurrences | each version keeps its own chunks; cost is deduplicated by representation reuse |
| embedding_exclusion_reasons | registered exclusion reasons (G6), counted in coverage with the excluded cap |
| ingest folder (`IMPORT_DIR`) | connector `core.folder` reading a read-only import volume (`vol://import`), refetchable |

Plugin tables (schema `plugin_code`, owner `plugin_code_owner`; Appendix B):

- `raw_facts(job_id, file_key, ordinal, kind dep|call, fact, resolution)`: per-job working data; `resolution` holds the resolve stage output (target key, external, status, method, reason, resolver version), which also feeds the LLM graph context; deleted when the job is terminal or by purge.
- `graph_quality(...)`: v0.2.4 counting identities kept as CHECKs; the G8 baseline is the latest published job for (collection, source).
- `parse_cache(tenant_id, content_sha256, parser_version, result, last_used_at)`: parse reuse across revisions, tenant-scoped, orphan GC by `last_used_at`.

Pipeline `code.index@1` (publish_mode full, input `file_tree@1`, collection `code`):

1. inventory (control): manifest → content class, exclusions (G6), job_items; split into file batches (`split_max_items`).
2. parse (item): `parse_cache` hit or tree-sitter parse → raw_facts.
3. resolve (control, after the parse barrier): resolver over all files of the job → `raw_facts.resolution`, graph_quality rows, G8 gate (warn | approve | fail).
4. knowledge (item, when `code.llm.enabled`): LLM summary per chunk via `put_representation(code.summary)`, reused by input_hash; graph context from the job's resolved edges (Phase 3 `outgoing_exact`).
5. assemble (item): `put_unit` for file and symbol units with edge attrs and chunks (summary in attrs for FTS); unchanged content hash → touch.
6. embed (item): `code.embed` and `code.knowledge_embed`, reused by input_hash.
7. publish (Core).

Coverage: `code.symbol` requires `code.embed` (+ `code.knowledge_embed` when the LLM is on). `present` hook returns file path, lines, symbol id, language, summary_ko. `purge_project` hook deletes raw_facts and graph_quality of the project; `parse_cache` stays (tenant-scoped). Parsers, resolver, chunk normalization, prompt and context builder are copied under O-16 with source-path notes; DB glue is rewritten. Summaries are written into chunk attrs so FTS needs no join (chosen over FTS reading representation bodies).

## 32. echo plugin manifest (PROP-A)

Validates §15 and drives EC-01..08.

```yaml
id: echo
version: 0.1.0
contract: 1
requires: {core: ">=0.4 <0.5"}
entry: plugins.echo.main
schema: plugin_echo
migrations: migrations/
collections: [{key: echo}]
unit_kinds: [{key: item, required_representations: [echo.vec]}]
representation_kinds: [{key: vec, target: unit, value: vector, dims: 8, model_ref: core.none, template_version: "1"}]
pipelines:
  - {key: run, version: 1, collection: echo, publish_mode: delta, inputs: [],
     stages: [{key: fan, kind: item, handler: fan, split: {max_items: 4, max_depth: 2}}, {key: publish, kind: publish}]}
policy_keys: [{key: fanout, type: int, min: 1, max: 8, affects_output: true}]
error_codes: [{key: boom, retryable: true, health_sample: false}]
hooks: {purge_project: purge}
```

## 33. Removed v0.2.4 runtime guards (PROP-A)

Basis: v0.2.4 `old/db/functions` 10, 11, 13, 16. Each removed guard, the failure it prevented, and the v0.4 replacement:

| Guard | Failure prevented | v0.4 replacement | Test |
| --- | --- | --- | --- |
| refund (RF-01..03) | a provider outage burns `max_attempts` and fails whole jobs | claim skips tasks whose resource breaker is open; a `fail()` with a health_sample code while that breaker is open is not charged (attempt decremented in the same UPDATE), bounded by `runtime.max_uncharged` (UNSET) | RT-01: embedder down for 3× lease → no job fails; tasks resume when the breaker closes |
| grace | long multi-item tasks expire while progressing | heartbeat extends the lease; `extend_deadline` bounded by `runtime.deadline_extension_max`; long work is split | RT-02: steady heartbeats outlive 5× `runtime.lease` |
| delivery ceiling | endless redelivery of a poison task | `max_attempts` in claim and M1 sweep (`core.attempts_exhausted`) | RT-03: worker killed on every claim → task and job failed after max_attempts |
| finalize fingerprint | publishing results computed under changed rules or inputs | policy_snapshot, pipeline_hash and fingerprint pinned at create_job; seq and fence checks at publish | RT-04: policy edit mid-job → job publishes with its snapshot; next job sees the new fingerprint, no NO_CHANGE |

## 34. Management contracts and added security tests (PROP-A)

Management contracts (all take `principal_id`, `request_id` → `core.requests`; audit row in the same transaction):

| ID | Contract | Pre | Out |
| --- | --- | --- | --- |
| MG-01 | `tenant_create(name)` | `coreadm` only | id, `NAME_TAKEN` |
| MG-02 | `project_create(tenant, name)` | tenant role ≥ operator; creator becomes project admin | id, `NAME_TAKEN`, `FORBIDDEN` |
| MG-03 | `member_set(scope, principal, role)` / `member_remove` | caller ≥ admin of the scope and ≥ the role granted; project member must be tenant member | OK, `LAST_ADMIN`, `FORBIDDEN` |
| MG-04 | `plugin_register(manifest, dir_hash)` (init only) / `plugin_enable(project, plugin, enabled)` (project admin) / `plugin_disable(plugin, version)` (`coreadm`) | same version with another manifest hash refused | OK, `PLUGIN_VERSION_CONFLICT` |
| MG-05 | `service_client_create(scopes, audiences, expires)` | tenant admin; secret returned once, argon2id hash stored | id + secret |
| MG-06 | `token_issue(client_id, secret)` | two definer calls: `get_client_salt` (constant-time dummy for unknown ids) and `verify_and_issue(client_id, digest)`; JWT signed in api | claims, `DENIED` |
| MG-07 | `signing_key_rotate()` | `coreadm` or maint; old key retires after `core.signing_key_retire_delay` | OK |
| MG-08 | `policy_set(scope, ref, key, value)` | global → `coreadm`; tenant/project → admin there; plugin/stage → tenant admin; §18 constraint in the same transaction | OK, `CONSTRAINT_VIOLATION`, `UNKNOWN_KEY` |
| MG-09 | `artifact_authorize_download(artifact)` | reader on its project | short-lived signed path, `FORBIDDEN` |
| MG-10 | `source_create(project, connector, external_id)` / `binding_set(...)` | contributor / operator | id, `EXISTS` (returns id) |

Tests: MG-T1 removing the last admin is refused; MG-T2 an operator cannot grant admin; MG-T3 a `policy_set` that violates a constraint leaves the old value.

Security tests for P5/P6 additions (extends §19):

| ID | Test | Expected |
| --- | --- | --- |
| ST-21 | `token_issue` with a wrong secret | denied, same timing class as an unknown client; `secret_hash` not readable by api |
| ST-22 | `member_set` by a project operator for another project | `FORBIDDEN` |
| ST-23 | plugin migration runs CREATE/ALTER in schema `core` | denied (runs as `plugin_<id>_owner`) |
| ST-24 | chat filter asks for a project the Open WebUI principal cannot read | `FORBIDDEN`; falls back only to the chat's scratch source |
| ST-25 | scratch source of chat A searched from chat B through the tool | no rows (tool rule, D-31) |
| ST-26 | search with a project in deleting state | `PROJECT_DELETING`, no rows |
| ST-27 | `put_representation` of an input already stored by another project | response identical to a new value |
| ST-28 | webhook to a URL outside `core.webhook_allowlist`, or a redirect | `DESTINATION_NOT_ALLOWED` / not followed |
| ST-29 | Open WebUI principal calls `member_set`, `policy_set` or `project_create` | `FORBIDDEN` (contributor only, D-31) |
| ST-30 | deploy or chat use of a model disabled by purge (D-33) | refused until an admin keeps it |

## Appendix A. Physical DDL draft (ADOPTED)

```sql
-- code-rag v0.4 physical DDL draft for P5 (design, ADOPTED). PostgreSQL 18 + pgvector 0.8 + btree_gist.
-- Owner of every object: core_owner. Logins get EXECUTE on contract functions only (grants block at the end).
-- Tenant/project consistency: every table holding tenant data carries tenant_id (and project_id where it is project data),
-- except child rows reachable only through a tenant-scoped parent by FK (job_stages, task_events, job_items, generations, candidate_keys, pins);
-- cross-table references use composite FKs so a row can never point into another tenant or project.

CREATE SCHEMA core;
CREATE SCHEMA runtime;
CREATE SCHEMA knowledge;

-- ===================== core: identity =====================
CREATE TABLE core.tenants (
    id          uuid PRIMARY KEY DEFAULT uuidv7(),
    name        text NOT NULL UNIQUE,
    status      text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'disabled')),
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE core.principals (
    id            uuid PRIMARY KEY DEFAULT uuidv7(),
    kind          text NOT NULL CHECK (kind IN ('human', 'service')),
    display_name  text NOT NULL,
    status        text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'disabled')),
    created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE core.external_identities (
    issuer        text NOT NULL,
    subject       text NOT NULL,
    principal_id  uuid NOT NULL REFERENCES core.principals,
    PRIMARY KEY (issuer, subject)
);

CREATE TABLE core.tenant_members (
    tenant_id     uuid NOT NULL REFERENCES core.tenants,
    principal_id  uuid NOT NULL REFERENCES core.principals,
    role          smallint NOT NULL CHECK (role BETWEEN 1 AND 4),   -- 1 reader 2 contributor 3 operator 4 admin
    PRIMARY KEY (tenant_id, principal_id)
);

CREATE TABLE core.projects (
    tenant_id    uuid NOT NULL REFERENCES core.tenants,
    id           uuid NOT NULL DEFAULT uuidv7() UNIQUE,
    name         text NOT NULL,
    state        text NOT NULL DEFAULT 'active' CHECK (state IN ('active', 'deleting', 'purging')),
    delete_after timestamptz,                            -- end of grace window (G2)
    created_by   uuid NOT NULL REFERENCES core.principals,
    created_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, id),
    CHECK ((state = 'active') = (delete_after IS NULL))
);
CREATE UNIQUE INDEX uq_projects_live_name ON core.projects (tenant_id, name) WHERE state = 'active';

CREATE TABLE core.project_members (
    tenant_id     uuid NOT NULL,
    project_id    uuid NOT NULL,
    principal_id  uuid NOT NULL,
    role          smallint NOT NULL CHECK (role BETWEEN 1 AND 4),
    PRIMARY KEY (tenant_id, project_id, principal_id),
    FOREIGN KEY (tenant_id, project_id) REFERENCES core.projects (tenant_id, id),
    FOREIGN KEY (tenant_id, principal_id) REFERENCES core.tenant_members (tenant_id, principal_id) ON DELETE CASCADE
);
CREATE INDEX ix_project_members_principal ON core.project_members (principal_id);

CREATE TABLE core.service_clients (
    id            uuid PRIMARY KEY DEFAULT uuidv7(),
    principal_id  uuid NOT NULL REFERENCES core.principals,
    secret_hash   text NOT NULL,                         -- argon2id, verified in the api process
    audiences     text[] NOT NULL,
    scopes        text[] NOT NULL,
    expires_at    timestamptz,
    status        text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'revoked'))
);

CREATE TABLE core.signing_keys (
    kid          text PRIMARY KEY,
    alg          text NOT NULL CHECK (alg = 'ES256'),
    public_jwk   jsonb NOT NULL,
    private_ref  text NOT NULL,                          -- host key path reference, never the key
    status       text NOT NULL CHECK (status IN ('active', 'retiring', 'retired')),
    created_at   timestamptz NOT NULL DEFAULT now(),
    retired_at   timestamptz
);
CREATE UNIQUE INDEX uq_signing_one_active ON core.signing_keys ((true)) WHERE status = 'active';

-- ===================== core: plugins and registry =====================
CREATE TABLE core.plugins (
    id             text NOT NULL CHECK (id ~ '^[a-z][a-z0-9_]{1,29}$'),
    version        text NOT NULL,
    contract       int  NOT NULL CHECK (contract = 1),
    manifest       jsonb NOT NULL,
    manifest_hash  text NOT NULL,
    dir_hash       text NOT NULL,                        -- sha256 of the plugin folder at registration (R13)
    status         text NOT NULL DEFAULT 'registered' CHECK (status IN ('registered', 'refused', 'disabled')),
    registered_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (id, version)                            -- same version + different manifest_hash -> PLUGIN_VERSION_CONFLICT (R4)
);

-- One row per registered name across versions: the single source for error codes and other keys (R9).
CREATE TABLE core.registry_keys (
    item_type   text NOT NULL CHECK (item_type IN ('handler', 'collection', 'unit_kind', 'representation_kind',
                    'policy_key', 'resource', 'permission', 'schedule', 'format', 'error_code', 'event',
                    'exclusion_reason', 'connector')),
    key         text NOT NULL,                           -- namespaced: core.<key> or <plugin>.<key>
    owner       text NOT NULL,                           -- 'core' or plugin id
    spec        jsonb NOT NULL,                          -- current spec; error_code: {layer, retryable, health_sample}
    PRIMARY KEY (item_type, key)
);

CREATE TABLE core.registry_items (                       -- which plugin version declared which key
    plugin_id       text NOT NULL,
    plugin_version  text NOT NULL,
    item_type       text NOT NULL,
    key             text NOT NULL,
    spec            jsonb NOT NULL,
    PRIMARY KEY (plugin_id, plugin_version, item_type, key),
    FOREIGN KEY (plugin_id, plugin_version) REFERENCES core.plugins (id, version),
    FOREIGN KEY (item_type, key) REFERENCES core.registry_keys (item_type, key)
);

CREATE TABLE core.plugin_migrations (                    -- §28: immutable, applied by init in order
    plugin_id   text NOT NULL,                           -- 'core' for Core schemas
    number      int  NOT NULL,
    sha256      text NOT NULL,
    applied_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (plugin_id, number)
);

CREATE TABLE core.schema_version (                       -- written last by init (D-07)
    component   text PRIMARY KEY,
    version     text NOT NULL,
    stamped_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE core.project_plugins (                      -- plugin activation per project
    tenant_id   uuid NOT NULL,
    project_id  uuid NOT NULL,
    plugin_id   text NOT NULL,
    enabled     boolean NOT NULL DEFAULT true,
    PRIMARY KEY (tenant_id, project_id, plugin_id),
    FOREIGN KEY (tenant_id, project_id) REFERENCES core.projects (tenant_id, id)
);

CREATE TABLE core.policies (
    scope       text NOT NULL CHECK (scope IN ('global', 'tenant', 'project', 'plugin', 'stage')),
    scope_ref   text NOT NULL DEFAULT '',                -- tenant id, project id, plugin id or pipeline.stage
    key         text NOT NULL,
    value       jsonb,                                   -- NULL = UNSET
    set_by      uuid REFERENCES core.principals,
    set_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (scope, scope_ref, key)
);

-- ===================== core: storage, sources =====================
CREATE TABLE core.volumes (
    name        text PRIMARY KEY CHECK (name ~ '^[a-z0-9_-]{1,32}$'),
    mount_path  text NOT NULL UNIQUE CHECK (mount_path ~ '^/data/vol/[a-z0-9_-]+$'),
    status      text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'readonly', 'retired'))
);

CREATE TABLE core.secrets (                              -- connector credentials and webhook HMAC keys (P-P12, G1)
    tenant_id   uuid NOT NULL REFERENCES core.tenants,
    owner       text NOT NULL,                           -- <plugin>.<connector> or core.webhook
    name        text NOT NULL,
    version     int  NOT NULL,
    key_id      text NOT NULL,                           -- host key used to encrypt
    ciphertext  bytea NOT NULL,
    status      text NOT NULL CHECK (status IN ('active', 'retiring', 'retired')),
    PRIMARY KEY (tenant_id, owner, name, version)
);

CREATE TABLE core.sources (
    tenant_id    uuid NOT NULL,
    project_id   uuid NOT NULL,
    id           uuid NOT NULL DEFAULT uuidv7() UNIQUE,
    connector    text NOT NULL,                          -- 'core.upload' or <plugin>.<connector>
    external_id  text NOT NULL,
    refetchable  boolean NOT NULL,                       -- git by SHA yes, upload no (G4)
    PRIMARY KEY (tenant_id, project_id, id),
    UNIQUE (project_id, connector, external_id),
    FOREIGN KEY (tenant_id, project_id) REFERENCES core.projects (tenant_id, id)
);

CREATE TABLE core.source_revisions (
    tenant_id          uuid NOT NULL,
    project_id         uuid NOT NULL,
    id                 uuid NOT NULL DEFAULT uuidv7() UNIQUE,
    source_id          uuid NOT NULL,
    revision_key       text NOT NULL,                    -- immutable: commit SHA, upload content sha256
    manifest_hash      text NOT NULL,
    manifest           jsonb NOT NULL,                   -- kept after prune
    storage_ref        text NOT NULL CHECK (storage_ref ~ '^(vol|ext)://'),
    storage_state      text NOT NULL DEFAULT 'present' CHECK (storage_state IN ('writing', 'present', 'pruned')),
    input_complete     boolean NOT NULL,                 -- every manifest path present (R3)
    created_at         timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, project_id, id),
    UNIQUE (source_id, revision_key),
    FOREIGN KEY (tenant_id, project_id, source_id) REFERENCES core.sources (tenant_id, project_id, id)
);
CREATE INDEX ix_revisions_latest ON core.source_revisions (source_id, created_at DESC);

CREATE TABLE core.source_bindings (
    tenant_id     uuid NOT NULL,
    project_id    uuid NOT NULL,
    id            uuid PRIMARY KEY DEFAULT uuidv7(),
    connector     text NOT NULL,
    external_ref  text NOT NULL,
    config        jsonb NOT NULL DEFAULT '{}',
    UNIQUE (connector, external_ref, project_id),
    FOREIGN KEY (tenant_id, project_id) REFERENCES core.projects (tenant_id, id)
);

CREATE TABLE core.webhook_inbox (                        -- inbound, renamed from webhook_deliveries (G1)
    plugin_id     text NOT NULL,
    delivery_id   text NOT NULL,
    received_at   timestamptz NOT NULL DEFAULT now(),
    state         text NOT NULL CHECK (state IN ('received', 'enqueued', 'rejected')),
    PRIMARY KEY (plugin_id, delivery_id)
);

CREATE TABLE core.schedules (
    id           uuid PRIMARY KEY DEFAULT uuidv7(),
    plugin_id    text NOT NULL,
    key          text NOT NULL,
    tenant_id    uuid,
    project_id   uuid,                                   -- NULL = plugin-wide
    next_run_at  timestamptz NOT NULL,
    enabled      boolean NOT NULL DEFAULT true,
    UNIQUE NULLS NOT DISTINCT (plugin_id, key, project_id),
    FOREIGN KEY (tenant_id, project_id) REFERENCES core.projects (tenant_id, id),
    CHECK ((tenant_id IS NULL) = (project_id IS NULL))
);
CREATE INDEX ix_schedules_due ON core.schedules (next_run_at) WHERE enabled;

CREATE TABLE core.artifacts (
    tenant_id    uuid NOT NULL,
    project_id   uuid NOT NULL,
    id           uuid NOT NULL DEFAULT uuidv7() UNIQUE,
    kind         text NOT NULL,                          -- registered: <plugin>.<kind>
    storage_ref  text NOT NULL CHECK (storage_ref ~ '^vol://'),
    sha256       text NOT NULL,
    provenance   jsonb NOT NULL DEFAULT '{}',
    created_by   uuid NOT NULL REFERENCES core.principals,
    created_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, project_id, id),
    FOREIGN KEY (tenant_id, project_id) REFERENCES core.projects (tenant_id, id)
);

-- ===================== core: events, audit, alerts =====================
CREATE TABLE core.outbox_events (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id   uuid,
    project_id  uuid,                                    -- G1: project-scoped subscriptions
    type        text NOT NULL,
    payload     jsonb NOT NULL,                          -- ids only, never content
    created_at  timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE core.event_consumers (                      -- internal consumers (runtime stop flag, maintenance)
    name        text PRIMARY KEY,
    last_id     bigint NOT NULL DEFAULT 0                -- cursor into outbox_events
);

CREATE TABLE core.webhook_subscriptions (                -- outbound F10 (G1)
    tenant_id    uuid NOT NULL,
    project_id   uuid NOT NULL,
    id           uuid PRIMARY KEY DEFAULT uuidv7(),
    url          text NOT NULL CHECK (url ~ '^https://'),
    event_types  text[] NOT NULL,
    secret_name  text NOT NULL,                          -- core.secrets (owner core.webhook)
    created_by   uuid NOT NULL REFERENCES core.principals,
    status       text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'disabled')),
    FOREIGN KEY (tenant_id, project_id) REFERENCES core.projects (tenant_id, id)
);

CREATE TABLE core.outbox_deliveries (
    event_id         bigint NOT NULL REFERENCES core.outbox_events,
    subscription_id  uuid NOT NULL REFERENCES core.webhook_subscriptions,
    state            text NOT NULL CHECK (state IN ('pending', 'delivered', 'dead')),
    attempts         int  NOT NULL DEFAULT 0,
    next_at          timestamptz NOT NULL DEFAULT now(),
    last_status      int,
    PRIMARY KEY (event_id, subscription_id)
);
CREATE INDEX ix_deliveries_due ON core.outbox_deliveries (next_at) WHERE state = 'pending';

CREATE TABLE core.requests (                             -- replay store for user operations (§26)
    principal_id  uuid NOT NULL REFERENCES core.principals,
    request_id    uuid NOT NULL,
    op            text NOT NULL,
    args_hash     text NOT NULL,                         -- same id + different args -> REQUEST_ID_REUSED
    result        jsonb NOT NULL,
    at            timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (principal_id, request_id)
);

CREATE TABLE core.audit (
    id             bigint GENERATED ALWAYS AS IDENTITY,
    at             timestamptz NOT NULL DEFAULT now(),
    actor          uuid,                                 -- sub
    acting_client  uuid,                                 -- act
    action         text NOT NULL,
    object         text NOT NULL,
    detail         jsonb NOT NULL DEFAULT '{}',
    PRIMARY KEY (id, at)
) PARTITION BY RANGE (at);                               -- monthly partitions; retention = DROP PARTITION by core_owner only
CREATE FUNCTION core.audit_immutable() RETURNS trigger LANGUAGE plpgsql AS
$$BEGIN RAISE EXCEPTION 'core.audit is append-only'; END$$;
CREATE TRIGGER trg_audit_immutable BEFORE UPDATE OR DELETE ON core.audit
    FOR EACH ROW EXECUTE FUNCTION core.audit_immutable();
CREATE TRIGGER trg_audit_no_truncate BEFORE TRUNCATE ON core.audit
    FOR EACH STATEMENT EXECUTE FUNCTION core.audit_immutable();
CREATE TABLE core.audit_default PARTITION OF core.audit DEFAULT;    -- maint creates monthly partitions ahead

CREATE TABLE core.alerts (                               -- §28
    code      text NOT NULL,
    object    text NOT NULL,
    severity  text NOT NULL CHECK (severity IN ('info', 'warn', 'error')),
    first_at  timestamptz NOT NULL DEFAULT now(),
    last_at   timestamptz NOT NULL DEFAULT now(),
    count     int NOT NULL DEFAULT 1,
    cleared_at timestamptz,
    PRIMARY KEY (code, object, first_at)
);
CREATE UNIQUE INDEX uq_alerts_open ON core.alerts (code, object) WHERE cleared_at IS NULL;

-- ===================== runtime =====================
CREATE SEQUENCE runtime.job_seq;

CREATE TABLE runtime.pipelines (
    plugin_id       text NOT NULL,
    plugin_version  text NOT NULL,
    key             text NOT NULL,
    version         int  NOT NULL,
    pipeline_hash   text NOT NULL,
    collection_key  text NOT NULL,
    publish_mode    text NOT NULL CHECK (publish_mode IN ('full', 'delta')),
    inputs          text[] NOT NULL DEFAULT '{}',
    unit_kinds      text[] NOT NULL DEFAULT '{}',        -- full-mode end scope (R7)
    PRIMARY KEY (plugin_id, key, version),
    FOREIGN KEY (plugin_id, plugin_version) REFERENCES core.plugins (id, version)
);

CREATE TABLE runtime.pipeline_stages (
    plugin_id        text NOT NULL,
    pipeline_key     text NOT NULL,
    version          int  NOT NULL,
    stage_key        text NOT NULL,
    kind             text NOT NULL CHECK (kind IN ('control', 'item', 'approval', 'publish')),  -- approval/publish: Core-only
    handler          text,
    depends_on       text[] NOT NULL DEFAULT '{}',
    resource_key     text,
    split_max_items  int CHECK (split_max_items > 0),
    split_max_depth  int CHECK (split_max_depth >= 0),
    PRIMARY KEY (plugin_id, pipeline_key, version, stage_key),
    FOREIGN KEY (plugin_id, pipeline_key, version) REFERENCES runtime.pipelines,
    CHECK ((kind IN ('approval', 'publish')) = (handler IS NULL))
);

CREATE TABLE runtime.jobs (
    tenant_id           uuid NOT NULL,
    project_id          uuid NOT NULL,
    id                  uuid NOT NULL DEFAULT uuidv7() UNIQUE,
    collection_id       uuid NOT NULL,
    source_id           uuid,                            -- NULL for sourceless units (chat) and core.delete batches without source
    source_revision_id  uuid,
    plugin_id           text NOT NULL,                   -- 'core' for core.delete / core.project_purge
    plugin_version      text NOT NULL,
    pipeline_key        text NOT NULL,
    pipeline_version    int  NOT NULL,
    pipeline_hash       text NOT NULL,
    policy_snapshot     jsonb NOT NULL,                  -- R5: every key the pipeline reads, frozen at create_job
    fingerprint         text NOT NULL,                   -- G5
    force               boolean NOT NULL DEFAULT false,  -- G7 retry bypasses NO_CHANGE
    retry_of            uuid,
    request_id          uuid NOT NULL,                   -- idempotent create_job / delete_unit
    fence_seq           bigint NOT NULL,                 -- ordering value used by fences; = seq, or the original job's fence_seq for a retry
    seq                 bigint NOT NULL DEFAULT nextval('runtime.job_seq'),  -- global, no collection lock needed (§26)
    status              text NOT NULL CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'canceled', 'superseded')),
    stop_requested      text CHECK (stop_requested IN ('cancel', 'failure', 'superseded', 'purge')),
    outcome             text,                            -- PUBLISHED, NO_CHANGE, REVISION_STALE, SNAPSHOT_EXPIRED, ...
    created_by          uuid NOT NULL REFERENCES core.principals,
    created_at          timestamptz NOT NULL DEFAULT now(),
    finished_at         timestamptz,
    PRIMARY KEY (tenant_id, project_id, id),
    UNIQUE (collection_id, seq),
    UNIQUE (created_by, request_id),
    UNIQUE (id, plugin_id, pipeline_key, pipeline_version),
    FOREIGN KEY (tenant_id, project_id) REFERENCES core.projects (tenant_id, id),
    FOREIGN KEY (tenant_id, project_id, source_id) REFERENCES core.sources (tenant_id, project_id, id),
    FOREIGN KEY (tenant_id, project_id, source_revision_id) REFERENCES core.source_revisions (tenant_id, project_id, id),
    FOREIGN KEY (retry_of) REFERENCES runtime.jobs (id),
    CHECK ((status IN ('succeeded', 'failed', 'canceled', 'superseded')) = (finished_at IS NOT NULL)),
    CHECK (source_revision_id IS NULL OR source_id IS NOT NULL)
);
CREATE INDEX ix_jobs_scope_open ON runtime.jobs (collection_id, source_id, pipeline_key) WHERE finished_at IS NULL;

CREATE TABLE runtime.job_stages (
    job_id       uuid NOT NULL,
    plugin_id    text NOT NULL,
    pipeline_key text NOT NULL,
    pipeline_version int NOT NULL,
    stage_key    text NOT NULL,
    status       text NOT NULL CHECK (status IN ('blocked', 'ready', 'running', 'waiting_approval', 'succeeded', 'failed', 'canceled')),
    approved_by  uuid REFERENCES core.principals,
    approved_hash text,                                  -- R15: artifact sha256 the approval covers
    wait_until   timestamptz,                            -- approval expiry (runtime.approval_ttl, O-17)
    PRIMARY KEY (job_id, stage_key),
    FOREIGN KEY (job_id, plugin_id, pipeline_key, pipeline_version) REFERENCES runtime.jobs (id, plugin_id, pipeline_key, pipeline_version),
    FOREIGN KEY (plugin_id, pipeline_key, pipeline_version, stage_key) REFERENCES runtime.pipeline_stages
);

CREATE TABLE runtime.tasks (
    id            uuid PRIMARY KEY DEFAULT uuidv7(),
    job_id        uuid NOT NULL,
    stage_key     text NOT NULL,
    plugin_id     text NOT NULL,                         -- claim filter: only the caller login's plugin (R8)
    tenant_id     uuid NOT NULL,                         -- fairness (R11)
    project_id    uuid NOT NULL,
    parent_id     uuid REFERENCES runtime.tasks,
    child_key     text,
    depth         int  NOT NULL DEFAULT 0,
    scope         jsonb NOT NULL DEFAULT '{}',
    payload       jsonb NOT NULL DEFAULT '{}',
    status        text NOT NULL CHECK (status IN ('ready', 'running', 'retry_wait', 'succeeded', 'split', 'failed', 'canceled')),
    resolution    text CHECK (resolution IN ('ok', 'failed', 'canceled')),
    attempt       int  NOT NULL DEFAULT 0,               -- mutable counter; task_events is the append-only record (R10)
    lease_epoch   bigint NOT NULL DEFAULT 0,
    lease_token   uuid,
    lease_until   timestamptz,
    ready_at      timestamptz NOT NULL DEFAULT now(),
    deadline_at   timestamptz,
    error_type    text NOT NULL DEFAULT 'error_code' CHECK (error_type = 'error_code'),
    error_code    text,
    FOREIGN KEY (job_id, stage_key) REFERENCES runtime.job_stages,
    FOREIGN KEY (tenant_id, project_id, job_id) REFERENCES runtime.jobs (tenant_id, project_id, id),
    FOREIGN KEY (error_type, error_code) REFERENCES core.registry_keys (item_type, key),
    UNIQUE (parent_id, child_key),
    CHECK ((parent_id IS NULL) = (child_key IS NULL)),
    CHECK ((status = 'running') = (lease_token IS NOT NULL)),
    CHECK ((status = 'failed') = (error_code IS NOT NULL))
);
CREATE INDEX ix_tasks_claim ON runtime.tasks (plugin_id, ready_at) WHERE status = 'ready';
CREATE INDEX ix_tasks_lease ON runtime.tasks (lease_until) WHERE status = 'running';
CREATE INDEX ix_tasks_stage ON runtime.tasks (job_id, stage_key, status);

CREATE TABLE runtime.task_events (
    id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    task_id       uuid NOT NULL REFERENCES runtime.tasks,
    request_id    uuid,
    op            text NOT NULL CHECK (op IN ('claim', 'heartbeat', 'complete', 'fail', 'split', 'expire', 'retry', 'cancel', 'terminal_fail')),
    lease_epoch   bigint NOT NULL,
    outcome       text NOT NULL,
    resource_key  text,                                  -- breaker window source (R10)
    error_type    text NOT NULL DEFAULT 'error_code' CHECK (error_type = 'error_code'),
    error_code    text,
    detail        jsonb NOT NULL DEFAULT '{}',
    at            timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (error_type, error_code) REFERENCES core.registry_keys (item_type, key)
);
CREATE UNIQUE INDEX uq_task_events_request ON runtime.task_events (task_id, request_id) WHERE request_id IS NOT NULL;
CREATE INDEX ix_task_events_task ON runtime.task_events (task_id);
CREATE INDEX ix_task_events_resource ON runtime.task_events (resource_key, at) WHERE resource_key IS NOT NULL;

CREATE TABLE runtime.job_items (                         -- G3 per-file report, written via put_job_item
    job_id   uuid NOT NULL REFERENCES runtime.jobs (id),
    key      text NOT NULL,                              -- e.g. file path
    status   text NOT NULL CHECK (status IN ('ok', 'excluded', 'failed', 'held', 'unchanged')),
    code     text,                                       -- error code or exclusion reason
    detail   jsonb NOT NULL DEFAULT '{}',
    PRIMARY KEY (job_id, key)
);

CREATE TABLE runtime.lifecycle_events (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    job_id       uuid NOT NULL REFERENCES runtime.jobs (id),
    stage_key    text,
    from_status  text,
    to_status    text NOT NULL,
    detail       jsonb NOT NULL DEFAULT '{}',
    at           timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_lifecycle_job ON runtime.lifecycle_events (job_id);

CREATE TABLE runtime.resource_state (
    resource_key  text PRIMARY KEY,                      -- <plugin>.<resource> or core.embedder / core.llm / core.gpu
    state         text NOT NULL CHECK (state IN ('closed', 'open', 'half_open')),
    opened_at     timestamptz
);

CREATE TABLE runtime.schedule_runs (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    schedule_id  uuid NOT NULL REFERENCES core.schedules,
    due_at       timestamptz NOT NULL,
    job_id       uuid REFERENCES runtime.jobs (id),
    status       text NOT NULL CHECK (status IN ('due', 'started', 'done', 'failed')),
    UNIQUE (schedule_id, due_at)
);

-- ===================== knowledge =====================
CREATE TABLE knowledge.collections (
    tenant_id      uuid NOT NULL,
    project_id     uuid NOT NULL,
    id             uuid NOT NULL DEFAULT uuidv7() UNIQUE,
    plugin_id      text NOT NULL,
    key            text NOT NULL,
    current_gen    bigint NOT NULL DEFAULT 0,            -- 0 = nothing published
    active_config  jsonb NOT NULL DEFAULT '{}',          -- {rep_kind: config_hash} searched; switched at publish
    fence_floor    bigint NOT NULL DEFAULT 0,            -- fences below this were GC'd; retries with fence_seq < floor refused
    PRIMARY KEY (tenant_id, project_id, id),
    UNIQUE (tenant_id, id),
    UNIQUE (project_id, plugin_id, key),
    FOREIGN KEY (tenant_id, project_id) REFERENCES core.projects (tenant_id, id)
);

CREATE TABLE knowledge.collection_sources (              -- publish ordering scope (R1) and no-change record (G5)
    id                uuid PRIMARY KEY DEFAULT uuidv7(),
    tenant_id         uuid NOT NULL,
    project_id        uuid NOT NULL,
    collection_id     uuid NOT NULL,
    source_id         uuid,
    pipeline_key      text NOT NULL,
    last_seq          bigint NOT NULL DEFAULT 0,
    last_revision_id  uuid,
    last_fingerprint  text,
    UNIQUE NULLS NOT DISTINCT (collection_id, source_id, pipeline_key),
    FOREIGN KEY (tenant_id, project_id, collection_id) REFERENCES knowledge.collections (tenant_id, project_id, id),
    FOREIGN KEY (tenant_id, project_id, source_id) REFERENCES core.sources (tenant_id, project_id, id),
    FOREIGN KEY (tenant_id, project_id, last_revision_id) REFERENCES core.source_revisions (tenant_id, project_id, id)
);

CREATE TABLE knowledge.generations (
    collection_id  uuid NOT NULL REFERENCES knowledge.collections (id),
    gen            bigint NOT NULL,
    job_id         uuid NOT NULL REFERENCES runtime.jobs (id),
    status         text NOT NULL CHECK (status IN ('published', 'retired')),
    published_at   timestamptz NOT NULL DEFAULT now(),
    retired_at     timestamptz,
    PRIMARY KEY (collection_id, gen)
);

CREATE TABLE knowledge.units (
    tenant_id      uuid NOT NULL,
    project_id     uuid NOT NULL,
    id             uuid NOT NULL DEFAULT uuidv7() UNIQUE,
    collection_id  uuid NOT NULL,
    source_id      uuid,
    kind           text NOT NULL,                        -- <plugin>.<kind>
    key            text NOT NULL,
    created_by     uuid NOT NULL REFERENCES core.principals,
    on_behalf_of   text,                                 -- unverified until delegation (O-04)
    last_seq       bigint NOT NULL DEFAULT 0,            -- seq of the last job that changed this unit (§24 per-unit guard)
    PRIMARY KEY (tenant_id, id),
    UNIQUE NULLS NOT DISTINCT (collection_id, source_id, kind, key),
    FOREIGN KEY (tenant_id, project_id, collection_id) REFERENCES knowledge.collections (tenant_id, project_id, id),
    FOREIGN KEY (tenant_id, project_id, source_id) REFERENCES core.sources (tenant_id, project_id, id)
);

CREATE TABLE knowledge.unit_versions (
    tenant_id       uuid NOT NULL,
    id              uuid NOT NULL DEFAULT uuidv7() UNIQUE,
    unit_id         uuid NOT NULL,
    parent_version  uuid REFERENCES knowledge.unit_versions (id),
    content_hash    text NOT NULL,
    attrs           jsonb NOT NULL DEFAULT '{}',
    candidate_job   uuid REFERENCES runtime.jobs (id),   -- set while staged
    valid_from_gen  bigint,                              -- NULL = staged, invisible
    valid_to_gen    bigint,
    PRIMARY KEY (tenant_id, id),
    FOREIGN KEY (tenant_id, unit_id) REFERENCES knowledge.units (tenant_id, id),
    CHECK (valid_to_gen IS NULL OR (valid_from_gen IS NOT NULL AND valid_to_gen > valid_from_gen)),
    CHECK (valid_from_gen IS NOT NULL OR candidate_job IS NOT NULL)
);
-- Publish closes the old open version before opening the new one (same txn) so this index never conflicts.
CREATE UNIQUE INDEX uq_unit_open_version ON knowledge.unit_versions (unit_id) WHERE valid_from_gen IS NOT NULL AND valid_to_gen IS NULL;
CREATE UNIQUE INDEX uq_unit_staged_per_job ON knowledge.unit_versions (unit_id, candidate_job) WHERE valid_from_gen IS NULL;
CREATE INDEX ix_unit_versions_visible ON knowledge.unit_versions (unit_id, valid_from_gen, valid_to_gen);
CREATE INDEX ix_unit_versions_candidate ON knowledge.unit_versions (candidate_job) WHERE valid_from_gen IS NULL;

CREATE TABLE knowledge.chunks (
    tenant_id        uuid NOT NULL,
    id               uuid NOT NULL DEFAULT uuidv7() UNIQUE,
    unit_version_id  uuid NOT NULL,
    ordinal          int  NOT NULL CHECK (ordinal >= 0),
    text             text NOT NULL CHECK (text <> ''),
    span             jsonb NOT NULL DEFAULT '{}',
    attrs            jsonb NOT NULL DEFAULT '{}',
    fts              tsvector GENERATED ALWAYS AS (to_tsvector('simple'::regconfig, text || ' ' || coalesce(attrs->>'summary', '') || ' '
                         || coalesce(attrs->>'summary_ko', '') || ' ' || coalesce(attrs->>'keywords', ''))) STORED,  -- keyword search (M2)
    PRIMARY KEY (tenant_id, id),
    UNIQUE (unit_version_id, ordinal),
    FOREIGN KEY (tenant_id, unit_version_id) REFERENCES knowledge.unit_versions (tenant_id, id) ON DELETE CASCADE
);
CREATE INDEX ix_chunks_fts ON knowledge.chunks USING gin (fts);

CREATE TABLE knowledge.representation_values (           -- tenant-scoped, shared across projects (I-K2)
    tenant_id    uuid NOT NULL REFERENCES core.tenants,
    id           uuid NOT NULL DEFAULT uuidv7() UNIQUE,
    rep_kind     text NOT NULL,
    config_hash  text NOT NULL,
    input_hash   text NOT NULL,
    dims         int,
    vec          vector,                                 -- one HNSW per vector kind (partial expression index), O-05
    body         text,
    PRIMARY KEY (tenant_id, rep_kind, id),
    UNIQUE (tenant_id, rep_kind, config_hash, input_hash),
    UNIQUE (tenant_id, rep_kind, config_hash, id),
    CHECK ((vec IS NULL) = (dims IS NULL)),
    CHECK (vec IS NOT NULL OR body IS NOT NULL)
);

CREATE TABLE knowledge.representation_links (
    tenant_id        uuid NOT NULL,
    rep_kind         text NOT NULL,
    config_hash      text NOT NULL,                      -- a new model setting adds links; versions stay immutable
    value_id         uuid NOT NULL,
    unit_version_id  uuid,
    chunk_id         uuid,
    FOREIGN KEY (tenant_id, rep_kind, config_hash, value_id) REFERENCES knowledge.representation_values (tenant_id, rep_kind, config_hash, id),
    FOREIGN KEY (tenant_id, unit_version_id) REFERENCES knowledge.unit_versions (tenant_id, id) ON DELETE CASCADE,
    FOREIGN KEY (tenant_id, chunk_id) REFERENCES knowledge.chunks (tenant_id, id) ON DELETE CASCADE,
    CHECK ((unit_version_id IS NULL) <> (chunk_id IS NULL)),
    UNIQUE NULLS NOT DISTINCT (rep_kind, config_hash, unit_version_id, chunk_id)
);
CREATE INDEX ix_rl_value ON knowledge.representation_links (value_id);
CREATE INDEX ix_rl_unit_version ON knowledge.representation_links (unit_version_id) WHERE unit_version_id IS NOT NULL;
CREATE INDEX ix_rl_chunk ON knowledge.representation_links (chunk_id) WHERE chunk_id IS NOT NULL;

CREATE TABLE knowledge.candidate_keys (
    job_id   uuid NOT NULL REFERENCES runtime.jobs (id),
    unit_id  uuid NOT NULL REFERENCES knowledge.units (id),
    mode     text NOT NULL CHECK (mode IN ('touch', 'hold', 'delete')),  -- delete = explicit delete (R2)
    reason   text,
    PRIMARY KEY (job_id, unit_id)
);
CREATE INDEX ix_candidate_keys_unit ON knowledge.candidate_keys (unit_id);

CREATE TABLE knowledge.generation_pins (
    id             uuid PRIMARY KEY DEFAULT uuidv7(),
    collection_id  uuid NOT NULL REFERENCES knowledge.collections (id),
    gen            bigint NOT NULL,
    principal_id   uuid NOT NULL REFERENCES core.principals,   -- per-principal pin cap (R17)
    expires_at     timestamptz NOT NULL
);
CREATE INDEX ix_pins_active ON knowledge.generation_pins (collection_id, gen, expires_at);

-- runtime.jobs -> collections FK is added after knowledge.collections exists
ALTER TABLE runtime.jobs ADD FOREIGN KEY (tenant_id, project_id, collection_id)
    REFERENCES knowledge.collections (tenant_id, project_id, id);

-- ===================== roles and grants =====================
-- Logins: api, runtime, purge, maint, one plugin_<id> per plugin (created by init at registration).
CREATE ROLE core_owner NOLOGIN;
CREATE ROLE api LOGIN;
CREATE ROLE runtime LOGIN;
CREATE ROLE purge LOGIN;
CREATE ROLE maint LOGIN;
ALTER SCHEMA core OWNER TO core_owner;
ALTER SCHEMA runtime OWNER TO core_owner;
ALTER SCHEMA knowledge OWNER TO core_owner;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA core, runtime, knowledge FROM PUBLIC;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA core, runtime, knowledge FROM PUBLIC;
ALTER DEFAULT PRIVILEGES FOR ROLE core_owner REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC;
GRANT USAGE ON SCHEMA core, runtime, knowledge TO api, runtime, purge, maint;
-- Contract functions are SECURITY DEFINER owned by core_owner, SET search_path = pg_catalog, pg_temp.
-- Two grant sets (R8):
--   user functions (take principal_id, check membership): GRANT EXECUTE ... TO api
--   worker functions (check session_user -> plugin login):  GRANT EXECUTE ... TO runtime, plugin_<id>
-- No table privileges for any login.
```

## Appendix B. `plugin_code` DDL draft (PROP-A)

Evidence: implemented as DDL; builds on PostgreSQL 16; the counting CHECK was tested (a row breaking `attemptable = resolved + external + resolver_miss` is refused).

```sql
-- plugin_code schema (P6 draft). Owner plugin_code_owner; plugin_code login gets DML on these tables only.
CREATE SCHEMA plugin_code;
CREATE TABLE plugin_code.raw_facts (
    job_id    uuid NOT NULL,
    file_key  text NOT NULL,
    ordinal   int  NOT NULL CHECK (ordinal >= 0),
    kind      text NOT NULL CHECK (kind IN ('dep', 'call')),
    fact      jsonb NOT NULL,
    resolution jsonb,                                     -- resolve stage output: target_key, external, status, method, reason, resolver_version
    PRIMARY KEY (job_id, file_key, kind, ordinal)
);
CREATE TABLE plugin_code.graph_quality (
    job_id         uuid NOT NULL,
    collection_id  uuid NOT NULL,
    source_id      uuid,
    kind           text NOT NULL CHECK (kind IN ('dependency', 'call')),
    language       text NOT NULL,
    raw_total      int NOT NULL CHECK (raw_total >= 0),
    unsupported    int NOT NULL CHECK (unsupported >= 0),
    inherent       int NOT NULL CHECK (inherent >= 0),
    upstream       int NOT NULL CHECK (upstream >= 0),
    resolver_miss  int NOT NULL CHECK (resolver_miss >= 0),
    resolved       int NOT NULL CHECK (resolved >= 0),
    external       int NOT NULL CHECK (external >= 0),
    evaluated      int GENERATED ALWAYS AS (raw_total - unsupported) STORED,
    attemptable    int GENERATED ALWAYS AS (raw_total - unsupported - inherent - upstream) STORED,
    resolver_version text NOT NULL,
    PRIMARY KEY (job_id, kind, language),
    CHECK (raw_total - unsupported - inherent - upstream >= 0),
    CHECK (raw_total - unsupported - inherent - upstream = resolved + external + resolver_miss)
);
CREATE INDEX ix_gq_baseline ON plugin_code.graph_quality (collection_id, source_id, kind, language);
CREATE TABLE plugin_code.parse_cache (
    tenant_id       uuid NOT NULL,
    content_sha256  text NOT NULL,
    parser_version  text NOT NULL,
    result          jsonb NOT NULL,
    last_used_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, content_sha256, parser_version)
);
CREATE INDEX ix_parse_cache_used ON plugin_code.parse_cache (last_used_at);
```
