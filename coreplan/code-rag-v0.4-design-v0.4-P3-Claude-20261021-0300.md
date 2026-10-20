# code-rag Design v0.4-P3 — RAG Core + Plugins

- Document version: v0.4-P3 (design only, no implementation). Supersedes v0.4-P2 as the reference; P1 and P2 stay in `coreplan/` unchanged.
- System version: v0.4. The previous implementation (v0.2.4) stays in the `code-rag` repository.
- Repository: `code-rag-core`.
- Written by: Claude, 2026-10-21 03:00 KST.
- Estimated design progress: about 85% (Claude's estimate, §21).

## 0. How to read this document

Each item has an ID, a status and a basis, so every decision and open point can be traced.

Under D-22 every item marked PROP in this document proceeds as PROP-A; items without a proposal are HOLD.

| Status | Meaning |
| --- | --- |
| DECIDED | Dy decided it explicitly; the basis gives the time of Dy's message |
| PROP | Proposed, not adopted (D-14) |
| PROP-A | Proposed and proceeding as proposed under D-22, not individually reviewed by Dy; Dy may revert it |
| HOLD | No proposal yet; on hold under D-22 |
| OPEN | Not decided; needs Dy or a measurement |
| UNSET | A value intentionally left unset until measured |

Basis codes:

- `SRC-1`: Dy's summary "RAG Core 설계 변경 정리" (2026-10-20 21:38 KST), covering analysis of `code-rag-main.zip` and `minicore_websvc-main.zip`. Its 확정 items are carried as DECIDED, its 권장 items as PROP.
- `T hh:mm`: a message by Dy in the design thread at that KST time on 2026-10-20. This includes design reviews Dy posted in the thread.
- `R hh:mm`: Claude's design answer in the thread at that time (PROP only).
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

## 2. Layers (PROP)

| ID | Layer | Responsibility | Basis |
| --- | --- | --- | --- |
| P-L1 | Platform Core | Identity, tenants and projects, memberships, plugin registry, events and audit, API gateway, storage volumes, sources, connector secrets, schedules, webhook routing, artifact and model registry | SRC-1 §5 |
| P-L2 | Runtime | Pipelines as registered data, jobs, stages, tasks, leases, retries, budgets, circuit breakers, resource leases, approval gates | SRC-1 §7 |
| P-L3 | Knowledge Services | Content model, collections, unit versions, representations, generation publishing, search, GC | SRC-1 §7 |
| P-L4 | Plugins | Fill extension slots; code-rag first, echo for validation | SRC-1 §5 |

minicore patterns kept (SRC-1 §5.1): `plugin.yaml` manifest, self-registration, outbox and inbox, JWKS verification, permission registry, schema per plugin with UUID-only cross-schema references. Dropped (SRC-1 §5.2): HR integration, departments and ranks, LDAP, approval domains, and the assumption that every plugin is request/response plus UI.

minicore has no job or task runtime; queue, retry, circuit breaker and generation publishing are RAG-specific layers (SRC-1 §4).

## 3. System layout (PROP)

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

## 4. Invariants (PROP)

| ID | Invariant | Basis |
| --- | --- | --- |
| I-S1 | Each component has its own DB login (`api`, `runtime`, `purge`, `plugin_<id>`); the superuser is used only for init and migration | SRC-1 §6.2; v0.2.4 `deploy/docker-compose.yml:15` connects as `user=postgres` |
| I-S2 | The `api` login has no table privileges; it runs only `SECURITY DEFINER` contract functions with a fixed `search_path`; callers cannot pass object names to privileged SQL | SRC-1 §8, T 23:09 |
| I-S3 | Every contract function takes `principal_id` and resolves access from memberships; token tenant claims are ignored | SRC-1 §8, D-11 |
| I-S4 | A project member must be a tenant member, enforced by composite FKs | D-11, T 23:09 (C-1) |
| I-S5 | Tenant-owned rows carry `tenant_id` with composite FK `(tenant_id, id)` | SRC-1 §4 |
| I-S6 | Destructive work runs only as the purge role, limited to the operations it needs; its flag is transaction-local | SRC-1 §6.4; v0.2.4 `db/functions/21-sys-maint.sql:31` uses session scope; T 23:09 (C-3) |
| I-S7 | A plugin login reaches only its own schema and Core contract functions; external services (R server, storage) never get DB credentials | SRC-1 §5.1, R 23:14 |
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

## 5. Platform Core (PROP)

### 5.1 Identity and access

| ID | Item | Design | Basis |
| --- | --- | --- | --- |
| P-P1 | Principals | `principals(kind: human/service, status)`; `external_identities(issuer, subject)` → principal | SRC-1 §8 |
| P-P2 | Roles | reader=1 < contributor=2 < operator=3 < admin=4. Effective project role = max(project role, admin if tenant admin); a project membership never lowers a tenant admin; other tenant roles do not carry to projects | R 22:46 |
| P-P3 | Authorization | `core.authorize(principal, project, min_role)` inside every contract function; only the API maps a token to a principal | I-S2, I-S3 |
| P-P4 | Service tokens | `service_clients(principal, secret hash argon2id, audiences, scopes, expires_at, status)`; `POST /core/v1/token` (client credentials) → short JWT, ES256 with `kid`; `/core/v1/jwks`; key rotation keeps the old key verifying until the max TTL passes; `aud = coderag-core`; scope per endpoint; client status checked on every request (immediate revocation); membership always re-checked. Token TTL UNSET. API keys in dev mode only | R 22:46 |
| P-P5 | Login | Core provides no user login in the first step. People use Open WebUI, which is its own service principal with its own memberships; admin work uses a CLI with a service token; human OIDC later | R 23:09 (answer to O-01) |
| P-P6 | Audit | `core_audit` owned by `core_owner`; insert only through a `SECURITY DEFINER` function; a trigger rejects UPDATE and DELETE; retention through an owner-only procedure | R 22:46 |

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

`tenants`; `principals`; `external_identities`; `tenant_members(tenant, principal, role)` unique `(tenant, principal)`; `projects` PK `(tenant, project)`; `project_members(tenant, project, principal, role)` with FKs to `tenant_members` and `projects`; `service_clients`; `signing_keys(kid, alg, public_jwk, status)`; `plugins`; `registry_items`; `volumes`; `connector_secrets`; `sources`; `source_revisions`; `source_bindings`; `webhook_deliveries`; `schedules`; `schedule_runs`; `artifacts`; `models`; `outbox_events`; `core_audit`. `core_owner` owns all tables; `api`, `runtime`, `purge` and each `plugin_<id>` get EXECUTE on contract functions only. Basis: R 23:09, T 23:09.

## 6. Runtime (PROP)

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

Runtime schema sketch (`runtime`, logical): `pipelines`, `pipeline_stages`, `jobs`, `job_stages`, `tasks`, `task_events`, `lifecycle_events`, `policies`, `resource_state`, `resource_leases`, `schedule_runs` link. Basis: T 23:09.

### 6.2 Worker Protocol v1 (transport-neutral, D-15)

Every mutation carries `(task_id, lease_token, request_id)`. `request_id` is unique per task (unique index on `task_events`); the outcome, including split child ids, is stored atomically with the state change. Check order: lease epoch or token not current → `STALE_LEASE` (no change); task terminal with the same `request_id` → `REPLAY` (stored outcome); task terminal with a new `request_id` → `ALREADY_TERMINAL`. Basis: R 22:37, T 22:31.

| ID | Message | Transition | Outcomes |
| --- | --- | --- | --- |
| P-W1 | `claim(worker, loaded[(plugin, version, manifest_hash)], max)` | ready → running; epoch + 1; attempt + 1 | Only tasks whose pinned pipeline matches a loaded tuple; none for stopping jobs |
| P-W2 | `heartbeat` | Extends the lease | `EXTENDED`, `STALE_LEASE`, `DEADLINE` |
| P-W3 | `complete` | running → succeeded | `OK`, `REPLAY`, `STALE_LEASE`, `ALREADY_TERMINAL`, `OUTPUT_INVALID` |
| P-W4 | `fail(code, retryable)` | running → retry_wait (budget left) or failed via `rt_fail_terminal` | `RETRY_SCHEDULED`, `FAILED`, `REPLAY`, `STALE_LEASE`, `ALREADY_TERMINAL`, `UNKNOWN_ERROR_CODE` |
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

## 7. Knowledge Services (PROP)

### 7.1 Content model

`Source → Source revision → Unit → Unit version → Chunk → Representation → Generation`, grouped by collection. Unit kinds and representation kinds are registered by plugins and namespaced `plugin.kind`.

| ID | Item | Design | Basis |
| --- | --- | --- | --- |
| P-K1 | Collection | Publication namespace `(project, collection)` declared in `plugin.yaml`; a plugin publishes only into collections it declared; holds `current_gen` and `published_seq` | T 22:39, R 22:41 |
| P-K2 | Unit and version | Logical unit `(collection, source, kind, key)`; immutable `unit_versions` with `valid_from_gen`, `valid_to_gen`; visible at g iff `valid_from ≤ g` and (`valid_to` NULL or `g < valid_to`) | R 22:41, 23:09, 23:27 |
| P-K3 | Chunk | Ordered text segments of a unit version; tables are ordinary chunks with `attrs` | R 23:17 |
| P-K4 | Representation | `representation_values` keyed `(tenant, rep_kind, config_hash, input_hash)`; `config_hash` = model, revision, dims, dtype, normalization, template; `input_hash` = exact model input; `representation_links` from a unit version or chunk to a value. Each kind declares its target type (unit or chunk), dims and config; validated at registration; `DIM_MISMATCH` on wrong vectors | R 22:37, 23:09, T 23:09 |
| P-K5 | Attribution | Units record `created_by` (authenticated principal) and `on_behalf_of` (client-supplied, unverified until delegation; grants nothing) | R 22:41, I-S10 |
| P-K6 | No-change record | `collection_sources(collection, source, pipeline_key, version, last_manifest, last_seq)` | R 23:27 |
| P-K7 | Candidate keys | `candidate_keys(job, unit, mode: touch | hold)` | R 23:27, 23:34 |
| P-K8 | Generation pins | `generation_pins(collection, gen, expires_at)`; TTL UNSET | R 23:23 |
| P-K9 | Index | One HNSW index on `representation_values` plus the visibility filter, replacing v0.2.4 per-generation partitions (saves code); depends on measurement O-05 | R 23:23 |

Knowledge schema sketch (`knowledge`, logical): `collections`, `collection_sources`, `units`, `unit_versions`, `chunks`, `representation_values`, `representation_links`, `generations`, `candidate_keys`, `generation_pins`. Sources and revisions live in `core` (P-P13). Basis: T 23:09.

### 7.2 Contracts

Tenant, project, collection and source revision always come from the job (or memberships, for search), never from the caller.

| ID | Contract | Rules | Outcomes |
| --- | --- | --- | --- |
| P-C1 | `put_unit(task_ctx, kind, key, content_hash, attrs, chunks[])` | Registered kinds of the calling plugin only. Same hash as the visible version → no write, key recorded as `touch`. New hash → staged version (`valid_from` NULL, candidate = job). Same key twice in one job: same hash `REPLAY`, different hash `CONFLICT` (first wins, task fails). Lease-fenced | `unit_version_id`, `REUSED`, `REPLAY`, `CONFLICT`, `STALE_LEASE` |
| P-C2 | `put_representation(task_ctx, target, rep_kind, model_id, content_hash, value)` | Reuse per P-K4, tenant-scoped | `OK`, `REUSED`, `REPLAY`, `CONFLICT`, `DIM_MISMATCH`, `STALE_LEASE` |
| P-C3 | `hold_unit(task_ctx, kind, key, reason)` | Input temporarily missing (e.g. `lfs_missing`): touch without a new version; the old version stays open | `HELD` |
| P-C4 | `publish(job)` | §7.3 | `PUBLISHED`, `NO_CHANGE`, `COVERAGE_FAILED`, `SUPERSEDED`, `STALE` |
| P-C5 | `search(principal, projects[], collections[], query, k, rep_kinds[])` | Authorize all requested projects first; any failure → `FORBIDDEN` with no results; an unknown project is also `FORBIDDEN`. Reads `current_gen` per collection in the same statement as the results; RRF fusion across representation kinds; no cross-collection atomicity (documented); paging pins a generation | results + `generation_id` per collection, `FORBIDDEN`, `GENERATION_GONE` |
| P-C6 | `delete_unit(principal, unit)` | Staged, disappears at the next publish. Allowed: project contributor, or the verified `created_by` principal | `STAGED`, `FORBIDDEN`, `NOT_FOUND` |

### 7.3 Publish transaction

One transaction, run by a final Core-owned stage (R 23:23, 23:27):

1. Lock the collection row (`FOR UPDATE`); this serializes publishers.
2. If `job.seq ≤ published_seq` → `SUPERSEDED`.
3. Check coverage on the resulting visible set in the job's scope (reused versions keep their links, so a delta job never re-embeds unchanged units) → else `COVERAGE_FAILED`.
4. `full` mode: ends = visible units of `(collection, source, kinds of this pipeline)` minus `candidate_keys`. `delta` mode: no implicit ends; only staged deletes.
5. No new versions and no ends → `NO_CHANGE`: advance `published_seq`, update `collection_sources` (guard `last_seq < job.seq`), no new generation.
6. Otherwise `g_new = current_gen + 1` (dense, assigned only here); set `valid_from = g_new` on candidate versions and `valid_to = g_new` on ended and replaced versions; set `current_gen`, `published_seq`; update `collection_sources` (guard `last_seq < job.seq`); retire the old generation; write the lifecycle event and the outbox event `knowledge.published(collection, source, pipeline_key, seq)`.

A failed, superseded or canceled job's candidate versions never get `valid_from`; cleanup `DELETE … WHERE candidate = job AND valid_from IS NULL` runs only after the job is terminal, is idempotent and safe after a crash.

### 7.4 Stopping older jobs

The runtime consumes `knowledge.published` and sets the stop flag on running jobs in scope `(project, collection, source, pipeline_key)` with a lower `seq`; they end `superseded`. The publish transaction never writes runtime tables. Racing publishers are decided by the collection lock and sequence check. Basis: R 23:27, T 23:27 review, R 23:34.

### 7.5 GC and readers

Search holds the collection's shared GC lock for its transaction; GC takes it exclusively with `lock_timeout`. A pin is active while `expires_at > now()`. `floor = min(current_gen, oldest active pin)`; GC deletes versions with `valid_to ≤ floor`, then their links, then values with no links left. Basis: R 22:37, 23:23, 23:27; v0.2.4 `gc_lock_shared` (D-07).

### 7.6 Incomplete inputs

A file excluded because the snapshot is incomplete (e.g. missing LFS object) is recorded with `hold_unit`; the revision is marked incomplete and reconciliation retries; a hold longer than a limit (UNSET) raises an operator alert. Only a path actually absent from the manifest counts as deleted. Basis: T 23:27 review, R 23:34.

## 8. `plugin.yaml` v1 (PROP)

Core reads only: `id`, `version`, `contract: 1`, `requires: {core: ">=0.4,<0.5"}`, `entry` (Python module), `schema` (`plugin_<id>` + migrations folder), `handlers[]`, `pipelines[]` (stages, `inputs[]`, `publish_mode`, collection), `produces[]`, `collections[]`, `unit_kinds[]`, `representation_kinds[]` (target type, model, dims), `policy_keys[]`, `resources[]`, `permissions[]`, `schedules[]`, `events: {emits, consumes}`, optional `api: {router, prefix}`, optional `service: {...}` for request→task plugins. Basis: R 22:29, 23:14.

## 9. End-to-end trace: Gitea commit to search (PROP)

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

| ID | Plugin | Design (PROP) | Basis |
| --- | --- | --- | --- |
| L-01 | code-rag (`code`) | Unit kinds `code.file`, `code.symbol` (parent file; chunks from symbol text). Representation kinds `code.embed` (bge-m3 1024, chunk), `code.summary` (LLM summary and summary_ko, unit), `code.summary_embed`. Collection `code`, `publish_mode: full`. Pipeline `code.analyze@1`: ingest → parse (items split by file batch) → graph; embed after parse; knowledge after graph; knowledge_embed after knowledge; Core publish after embed and knowledge_embed. Schema `plugin_code`: inventory, dependency and call facts, resolved edges (to symbol unit UUIDs), graph quality summaries, knowledge runs. Resources `embedder`, `llm`. Policy keys `code.parse.*`, `code.graph.*`, `code.knowledge.*` UNSET. Parse reuse key `(file sha256, parser version, parser config hash)`. Graph expansion in search later as a plugin hook | R 22:46, 22:55 |
| L-02 | echo (validation) | Unit `echo.item`; representation `echo.vec` (deterministic 8-dim, no model; exercises reuse and `DIM_MISMATCH`); one split stage reaching depth 2, then delta publish into collection `echo`; handler over the HTTP binding; the I-K3 name check must pass with both plugins | SRC-1 §9.1, R 22:46 |

Search parameters (`candidate_k`, `rrf_k`, …) belong to Knowledge Services and keep the v0.2.4 Phase 3 values as PROP (memory 2026-10-16).

### 10.2 Registered for later development (D-16, D-19, D-20)

| ID | Plugin | Summary (PROP) | Basis |
| --- | --- | --- | --- |
| L-10 | Document | Document ingest and search with format handlers: pdf, md, docx (sections by heading, paragraphs, tables; images to Vision later), xlsx (workbook, sheet, table units; header+rows chunks; cached values, not formulas; row cap with summary; numeric tables can feed `rstats`), pptx (one unit per slide plus notes). Collection `docs`; shared bge-m3; optional LLM summary; input `file_tree@1`. Pure-Python libraries (openpyxl, python-docx, python-pptx); old binary formats need optional LibreOffice conversion. Macros never executed, external links ignored, zip limits from safe extraction | SRC-1 §9, R 23:17, D-18 |
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

### 10.3 L-20 `lora` plan (PROP)

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

Core needs (generic, no LoRA names): artifact and model registry (P-P20, P-P21), approval gate (P-R10), resource lease with schedule window (P-R9), long-task support (P-R11), `model.use` permission.

## 11. Milestones (PROP, SRC-1 §9.2)

| Step | Output | Done when |
| --- | --- | --- |
| M0 Design | Invariants, concept model, contracts, plugin manifest, tenant decision (D-11) | This document adopted by Dy |
| M1a | Identity, tenants, memberships, access layer, DB logins, plugin registry | Security tests pass under real logins |
| M1b | Runtime: pipeline registry, state machine, Worker Protocol, policy store | echo and code pipelines both run |
| M2 | Knowledge Services: content model, collections, versions, publish, search, GC | Publish, search and tenant isolation verified on dummy data; O-05 measured |
| M3 | code-rag plugin | Golden 3 repos + at least 1 real repo + more Korean queries, comparable to v0.2.4 results |
| M4 | Deploy and evaluation | compose, eval plugin, Open WebUI tool; single server starts with `up` only |

## 12. What v0.2.4 shows vs what v0.4 needs

- Shown by v0.2.4: lease, claim, sweeper and breaker semantics; a full ingest-to-publish run; partition build; Phase 3 search quality (recall@10 0.931, memory 2026-10-16).
- Not shown, needed by v0.4: generic stages (phases hard-coded), collections, unit versions and delta publish, separate DB logins, membership authorization, plugin loading, a second consumer, generic sources and connectors, artifacts and models, approval gates, resource leases.

## 13. Open items

| ID | Item | Needs |
| --- | --- | --- |
| O-01 | Core user login: proceeding with P-P5 (none in the first step) | PROP-A under D-22 |
| O-02 | ADR numbering conflict in the source material (ADR-01 void vs kept) | Dy (ADR text not available) |
| O-03 | Chat archive scope: personal vs shared project | Dy, when L-15 is built |
| O-04 | Delegation (RFC 8693) timing | Dy, when L-15 is built |
| O-05 | pgvector recall and latency with one HNSW index and the generation filter (current-only vs many versions, selective vs broad, concurrent publish, index size, GC) | Measurement |
| O-06 | UNSET values: token TTL, `max_attempts`, reconciliation interval, pin TTL, GC grace, hold alert limit, policy values | Measurement |
| O-07 | Prompt removal from search on remote revocation | Later design |
| O-08 | `/api/core/v1/auth/delegated-token` assumed by `miniweb_files_tool.py` is not in the minicore router list | Check (SRC-1 §11) |
| O-09 | LoRA training and vLLM LoRA loading for `gpt-oss-120b-awq`; runtime stacking in vLLM | Test on the site's vLLM version |
| O-10 | Sandbox design for arbitrary R code (L-19) | Later design |
| O-11 | Full-text (keyword) search alongside vector search | HOLD (no proposal) |
| O-12 | Search plugin hooks: query expansion, rerank, graph expansion | HOLD (later) |
| O-13 | Run the DDL draft on PG 18 + pgvector 0.8 | At implementation (D-13) |

## 14. Error-code registry (PROP-A)

One registry table `core.error_codes(code, layer, retryable, health_sample, owner_plugin)`. Core codes are fixed (`core.*`); plugins register namespaced codes (`<plugin>.*`) through `registry_items(item_type = 'error_code')`. A `fail()` with an unregistered code returns `UNKNOWN_ERROR_CODE`, and the task fails with `core.unknown_error_code` (not retryable). Basis: v0.2.4 signal registry pattern (PV5-04, D-07); REASON (one registry replaces per-table CHECK lists, saves code).

| Code | Layer | Retryable | Health sample |
| --- | --- | --- | --- |
| `core.attempts_exhausted` | runtime | no | no |
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

## 15. `plugin.yaml` JSON schema v1 (PROP-A)

Required: `id` (`^[a-z][a-z0-9_]{1,29}$`), `version` (semver), `contract` (const 1), `requires.core` (semver range), `entry` (Python module), `schema` (`^plugin_[a-z0-9_]+$`), `migrations` (relative folder).

Optional arrays (keys unique per type, pattern `^[a-z][a-z0-9_.]*$`, Core prefixes the plugin id):

| Field | Item shape |
| --- | --- |
| `handlers[]` | `{name, kind: control / item / service}` |
| `pipelines[]` | `{key, version, collection, publish_mode: full / delta, inputs: [format@range], stages: [{key, kind: control / item / approval, handler?, depends_on[], split?: {max_items > 0, max_depth >= 0}, policy_keys[], barrier: all}]}` |
| `produces[]` | `format@version` (e.g. `file_tree@1`) |
| `collections[]` | `{key}` |
| `unit_kinds[]` | `{key, parent_kind?}` |
| `representation_kinds[]` | `{key, target: unit / chunk, value: vector / text / json, dims (required for vector), model_ref (resource key), template_version}` |
| `policy_keys[]` | `{key, type: int / number / interval / bool / text, min?, max?, unit?}` (no defaults: values UNSET) |
| `resources[]` | `{key, kind: http / grpc / process, breaker: bool}` |
| `permissions[]` | `{key, min_role}` |
| `schedules[]` | `{key, handler, interval_policy_key}` |
| `error_codes[]` | `{key, retryable, health_sample}` |
| `events` | `{emits[], consumes[]}` |
| `api` | `{router: "module:attr", prefix: ^/api/<id>/v1$}` |
| `service` | `{endpoints: [{path, handler, permission}]}` |

Validation at load: JSON Schema 2020-12 plus semantic checks (DAG, a handler for every non-approval stage, `depends_on` keys exist, `dims` for vector kinds, a declared collection per pipeline, parseable format ranges). Unknown top-level fields are refused (strict contract; minicore drops them instead, SRC-1 appendix). Basis: §8, REASON.

## 16. Search contract details (PROP-A)

Request: `{projects[], collections[] (default: all readable collections of those projects), query, k (1..max_k), rep_kinds[] (default: all searchable kinds), filters: {unit_kinds[], attrs, source_ids[]}, include_content, page: {generation_ids, offset}}`.

Steps:

1. Authorize all projects (any failure → `FORBIDDEN`); collections must belong to the projects.
2. Pin: use the page's generation ids (`GENERATION_GONE` if expired), otherwise read `current_gen` per collection.
3. Embed the query once per distinct model configuration among the requested kinds.
4. Per representation kind: ANN with `candidate_k` on `representation_values`, joined through links to unit versions visible at the pinned generation, filters in the same query (pgvector iterative scan). Text kinds (summaries) are searched through their embedding kind.
5. Fuse with RRF (`rrf_k`) and per-kind weights; group by unit version, keeping the best chunk.
6. Return the top k: unit id, version id, kind, key, attrs, score, matched kinds, chunk excerpt, content if requested, generation ids, and a page token (creates a generation pin).

Policy keys: `knowledge.search.candidate_k`, `rrf_k`, `max_k`, `ef_search`, `weight.<rep_kind>`; the v0.2.4 Phase 3 values stay PROP (candidate_k 50, rrf_k 60, max_k 20, ef_search 100, weights 1; memory 2026-10-16). Full-text search: HOLD (O-11). Hooks: HOLD (O-12).

Chunk contract: `{ordinal, text (≤ knowledge.chunk.max_chars, not empty), span (plugin-defined), attrs}`, ordinal unique per unit version; empty text → `OUTPUT_INVALID`.

## 17. code-rag port mapping from v0.2.4 (PROP-A)

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

## 18. Policy catalog (PROP-A; values UNSET)

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
| `<plugin>.*` | registered by the plugin | bounds from the manifest |

Constraints are checked whenever a policy row is written (`core.policy_set`), not only at startup. Storage: `core.policies(scope, scope_ref, key, value)`, value NULL = UNSET. Basis: SRC-1 §7; v0.2.4 `rt_policy` constraint pattern (D-07); REASON (v0.2.4 had about 60 policy columns; v0.4 keeps about 25 Core keys).

## 19. Security test plan (PROP-A)

Each test opens its own connection as the real login (I-S9). Runner: pytest on PG 18.6 + pgvector 0.8.1 (as in v0.2.4).

| ID | Test | Expected |
| --- | --- | --- |
| ST-01 | `api` login SELECT or INSERT on any table | permission denied |
| ST-02 | principal of tenant A calls a contract on a project of tenant B | `FORBIDDEN` |
| ST-03 | search with one forbidden project among several | `FORBIDDEN`, zero rows |
| ST-04 | unknown project id | `FORBIDDEN`, same as ST-03 |
| ST-05 | project member without tenant member | FK violation |
| ST-06 | `plugin_code` login reads `plugin_echo` | denied |
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

## 20. Physical DDL draft and contract functions (PROP-A)

The draft in Appendix A has 43 tables in `core`, `runtime` and `knowledge`.

Load check (Claude, 2026-10-20 23:5x KST): it loads with zero errors on PostgreSQL 16 in the design container, using two stand-ins (`uuidv7()` → `gen_random_uuid()`, and `vector` stubbed as a domain). PG 18 has `uuidv7()` natively; real pgvector is still needed. It requires the `btree_gist` extension. It has not been run on PG 18 + pgvector (O-13, D-13).

Physical choices: uuidv7 keys; roles as smallint 1–4; one open version per unit (partial unique index); one staged version per unit and job; one active signing key; resource lease windows cannot overlap (EXCLUDE); `vec` is an untyped `vector` with one partial HNSW expression index per vector kind, created at plugin registration (pgvector pattern for mixed dimensions); `NULLS NOT DISTINCT` uniques.

Contract functions:

| Schema | Functions |
| --- | --- |
| `core` | `authorize`, `token_issue`, `membership_set`, `membership_remove`, `plugin_register`, `plugin_refuse`, `plugin_disable`, `volume_register`, `get_connector_secret`, `source_revision_commit`, `create_job`, `cancel_job`, `webhook_accept`, `artifact_put`, `artifact_authorize_download`, `model_register`, `model_authorize_use`, `policy_set`, `audit_write` (internal) |
| `runtime` | `claim`, `heartbeat`, `complete`, `fail`, `split`, `approve`, `sweep`, `watchdog`, `rt_fail_terminal` (internal), `stage_advance` (internal), `breaker_evaluate`, `resource_lease_acquire`, `resource_lease_release` |
| `knowledge` | `put_unit`, `put_representation`, `hold_unit`, `delete_unit`, `publish` (internal), `search`, `pin_extend`, `gc`, `candidate_cleanup` |

Grants: `api` → user-facing `core` functions, `knowledge.search`, `knowledge.delete_unit`, `runtime.approve`, `core.cancel_job`. `runtime` → runtime functions, `knowledge.publish`, `gc`, `candidate_cleanup`. `plugin_<id>` → Worker Protocol functions, `knowledge.put_*`, and for connectors `core.get_connector_secret`, `source_revision_commit`, `create_job`. `purge` → GC, cleanup and project purge only.

## 21. Design progress (Claude's estimate)

| Area | Progress |
| --- | --- |
| Layers, invariants, layout | 90% |
| Platform Core | 85% |
| Runtime | 85% |
| Knowledge Services | 80% |
| `plugin.yaml` | 85% |
| code-rag plugin | 70% |
| echo plugin | 60% |
| Later plugins (sketch level on purpose) | 25% |
| Physical DDL | 60% (draft, not run on PG 18) |
| Overall | about 85% |

Next design steps: echo HTTP binding test case; deploy layout for M4 (compose services, logins, volumes, offline bundle); migration and upgrade rules for plugin schemas; observability (metrics and alerts per layer).

## Appendix A. Physical DDL draft (PROP-A)

```sql
-- code-rag v0.4 physical DDL draft (design, PROP-A). PostgreSQL 18 + pgvector 0.8.
-- Owner of every object: core_owner. Logins get EXECUTE on contract functions only.

CREATE SCHEMA core;
CREATE SCHEMA runtime;
CREATE SCHEMA knowledge;

-- ===================== core =====================
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
    tenant_id   uuid NOT NULL REFERENCES core.tenants,
    id          uuid NOT NULL DEFAULT uuidv7(),
    name        text NOT NULL,
    deleting    boolean NOT NULL DEFAULT false,
    created_by  uuid NOT NULL REFERENCES core.principals,
    created_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, id),
    UNIQUE (id)
);
CREATE UNIQUE INDEX uq_projects_live_name ON core.projects (tenant_id, name) WHERE NOT deleting;

CREATE TABLE core.project_members (
    tenant_id     uuid NOT NULL,
    project_id    uuid NOT NULL,
    principal_id  uuid NOT NULL,
    role          smallint NOT NULL CHECK (role BETWEEN 1 AND 4),
    PRIMARY KEY (tenant_id, project_id, principal_id),
    FOREIGN KEY (tenant_id, project_id) REFERENCES core.projects (tenant_id, id) ON DELETE CASCADE,
    FOREIGN KEY (tenant_id, principal_id) REFERENCES core.tenant_members (tenant_id, principal_id) ON DELETE CASCADE
);

CREATE TABLE core.service_clients (
    id            uuid PRIMARY KEY DEFAULT uuidv7(),
    principal_id  uuid NOT NULL REFERENCES core.principals,
    secret_hash   text NOT NULL,                       -- argon2id
    audiences     text[] NOT NULL,
    scopes        text[] NOT NULL,
    expires_at    timestamptz,
    status        text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'revoked'))
);

CREATE TABLE core.signing_keys (
    kid         text PRIMARY KEY,
    alg         text NOT NULL CHECK (alg = 'ES256'),
    public_jwk  jsonb NOT NULL,
    private_ref text NOT NULL,                          -- host key store reference, never the key
    status      text NOT NULL CHECK (status IN ('active', 'retiring', 'retired')),
    created_at  timestamptz NOT NULL DEFAULT now(),
    retired_at  timestamptz
);
CREATE UNIQUE INDEX uq_signing_one_active ON core.signing_keys ((true)) WHERE status = 'active';

CREATE TABLE core.plugins (
    id             text NOT NULL CHECK (id ~ '^[a-z][a-z0-9_]{1,29}$'),
    version        text NOT NULL,
    contract       int  NOT NULL CHECK (contract = 1),
    manifest       jsonb NOT NULL,
    manifest_hash  text NOT NULL,
    status         text NOT NULL DEFAULT 'registered' CHECK (status IN ('registered', 'refused', 'disabled')),
    registered_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (id, version)
);

CREATE TABLE core.registry_items (
    plugin_id       text NOT NULL,
    plugin_version  text NOT NULL,
    item_type       text NOT NULL CHECK (item_type IN ('handler', 'collection', 'unit_kind', 'representation_kind',
                        'policy_key', 'resource', 'permission', 'schedule', 'format', 'error_code', 'event')),
    key             text NOT NULL,                       -- namespaced: <plugin>.<key>
    spec            jsonb NOT NULL,
    PRIMARY KEY (plugin_id, plugin_version, item_type, key),
    FOREIGN KEY (plugin_id, plugin_version) REFERENCES core.plugins (id, version)
);

CREATE TABLE core.error_codes (
    code              text PRIMARY KEY,                  -- core.* or <plugin>.*
    layer             text NOT NULL CHECK (layer IN ('core', 'runtime', 'knowledge', 'plugin')),
    retryable         boolean NOT NULL,
    health_sample     boolean NOT NULL DEFAULT false,
    owner_plugin      text
);

CREATE TABLE core.volumes (
    name        text PRIMARY KEY CHECK (name ~ '^[a-z0-9_-]{1,32}$'),
    mount_path  text NOT NULL UNIQUE CHECK (mount_path ~ '^/data/vol/[a-z0-9_-]+$'),
    status      text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'readonly', 'retired'))
);

CREATE TABLE core.connector_secrets (
    tenant_id   uuid NOT NULL REFERENCES core.tenants,
    plugin_id   text NOT NULL,
    connector   text NOT NULL,
    version     int  NOT NULL,
    ciphertext  bytea NOT NULL,                          -- encrypted with the host-held key
    status      text NOT NULL CHECK (status IN ('active', 'retiring', 'retired')),
    PRIMARY KEY (tenant_id, plugin_id, connector, version)
);

CREATE TABLE core.sources (
    tenant_id    uuid NOT NULL,
    project_id   uuid NOT NULL,
    id           uuid NOT NULL DEFAULT uuidv7() UNIQUE,
    connector    text NOT NULL,                          -- 'upload' or <plugin>.<connector>
    external_id  text NOT NULL,
    PRIMARY KEY (tenant_id, id),
    UNIQUE (project_id, connector, external_id),
    FOREIGN KEY (tenant_id, project_id) REFERENCES core.projects (tenant_id, id)
);

CREATE TABLE core.source_revisions (
    id             uuid PRIMARY KEY DEFAULT uuidv7(),
    source_id      uuid NOT NULL REFERENCES core.sources (id),
    revision_key   text NOT NULL,                        -- immutable: commit SHA, upload UUID
    manifest_hash  text NOT NULL,
    manifest       jsonb NOT NULL,                       -- [{path, sha256, size, status}]
    storage_ref    text NOT NULL CHECK (storage_ref ~ '^(vol|ext)://'),
    complete       boolean NOT NULL,
    created_at     timestamptz NOT NULL DEFAULT now(),
    UNIQUE (source_id, revision_key)
);

CREATE TABLE core.source_bindings (
    id          uuid PRIMARY KEY DEFAULT uuidv7(),
    project_id  uuid NOT NULL REFERENCES core.projects (id),
    connector   text NOT NULL,
    external_ref text NOT NULL,
    config      jsonb NOT NULL DEFAULT '{}'
);

CREATE TABLE core.webhook_deliveries (
    plugin_id     text NOT NULL,
    delivery_id   text NOT NULL,
    received_at   timestamptz NOT NULL DEFAULT now(),
    state         text NOT NULL CHECK (state IN ('received', 'enqueued', 'rejected')),
    PRIMARY KEY (plugin_id, delivery_id)
);

CREATE TABLE core.schedules (
    plugin_id   text NOT NULL,
    key         text NOT NULL,
    project_id  uuid REFERENCES core.projects (id),
    next_run_at timestamptz NOT NULL,
    PRIMARY KEY (plugin_id, key, project_id)
);

CREATE TABLE core.artifacts (
    id           uuid PRIMARY KEY DEFAULT uuidv7(),
    tenant_id    uuid NOT NULL,
    project_id   uuid NOT NULL,
    kind         text NOT NULL,                          -- registered: <plugin>.<kind>
    storage_ref  text NOT NULL CHECK (storage_ref ~ '^vol://'),
    sha256       text NOT NULL,
    provenance   jsonb NOT NULL DEFAULT '{}',
    created_by   uuid NOT NULL REFERENCES core.principals,
    created_at   timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (tenant_id, project_id) REFERENCES core.projects (tenant_id, id)
);

CREATE TABLE core.models (
    id               uuid PRIMARY KEY DEFAULT uuidv7(),
    tenant_id        uuid NOT NULL REFERENCES core.tenants,
    kind             text NOT NULL CHECK (kind IN ('base', 'adapter')),
    name             text NOT NULL,
    identity_hash    text NOT NULL,
    base_model_id    uuid REFERENCES core.models,
    artifact_id      uuid REFERENCES core.artifacts,
    config           jsonb NOT NULL DEFAULT '{}',        -- quantization, rank, modules, serving version
    provenance       jsonb NOT NULL DEFAULT '{}',        -- datasets, parents and weights
    evaluation       jsonb,
    deploy_status    text NOT NULL DEFAULT 'registered' CHECK (deploy_status IN ('registered', 'deployed', 'undeployed')),
    allowed_projects uuid[] NOT NULL DEFAULT '{}',
    UNIQUE (tenant_id, identity_hash),
    CHECK ((kind = 'adapter') = (base_model_id IS NOT NULL))
);

CREATE TABLE core.outbox_events (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id   uuid,
    type        text NOT NULL,
    payload     jsonb NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    delivered_at timestamptz
);

CREATE TABLE core.audit (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    at          timestamptz NOT NULL DEFAULT now(),
    actor       uuid,                                    -- sub
    acting_client uuid,                                  -- act
    action      text NOT NULL,
    object      text NOT NULL,
    detail      jsonb NOT NULL DEFAULT '{}'
);
CREATE FUNCTION core.audit_immutable() RETURNS trigger LANGUAGE plpgsql AS
$$BEGIN RAISE EXCEPTION 'core.audit is append-only'; END$$;
CREATE TRIGGER trg_audit_immutable BEFORE UPDATE OR DELETE ON core.audit
    FOR EACH ROW EXECUTE FUNCTION core.audit_immutable();

CREATE TABLE core.policies (
    scope       text NOT NULL CHECK (scope IN ('global', 'plugin', 'stage')),
    scope_ref   text NOT NULL DEFAULT '',                -- plugin id or pipeline.stage
    key         text NOT NULL,
    value       jsonb,                                   -- NULL = UNSET
    set_by      uuid REFERENCES core.principals,
    set_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (scope, scope_ref, key)
);

-- ===================== runtime =====================
CREATE TABLE runtime.pipelines (
    plugin_id       text NOT NULL,
    plugin_version  text NOT NULL,
    key             text NOT NULL,
    version         int  NOT NULL,
    manifest_hash   text NOT NULL,
    collection_key  text NOT NULL,
    publish_mode    text NOT NULL CHECK (publish_mode IN ('full', 'delta')),
    inputs          text[] NOT NULL DEFAULT '{}',
    PRIMARY KEY (plugin_id, key, version),
    FOREIGN KEY (plugin_id, plugin_version) REFERENCES core.plugins (id, version)
);

CREATE TABLE runtime.pipeline_stages (
    plugin_id    text NOT NULL,
    pipeline_key text NOT NULL,
    version      int  NOT NULL,
    stage_key    text NOT NULL,
    kind         text NOT NULL CHECK (kind IN ('control', 'item', 'approval', 'publish')),
    handler      text,
    depends_on   text[] NOT NULL DEFAULT '{}',
    split_max_items int CHECK (split_max_items > 0),
    split_max_depth int CHECK (split_max_depth >= 0),
    PRIMARY KEY (plugin_id, pipeline_key, version, stage_key),
    FOREIGN KEY (plugin_id, pipeline_key, version) REFERENCES runtime.pipelines,
    CHECK ((kind IN ('approval', 'publish')) = (handler IS NULL))
);

CREATE TABLE runtime.jobs (
    id                 uuid PRIMARY KEY DEFAULT uuidv7(),
    tenant_id          uuid NOT NULL,
    project_id         uuid NOT NULL,
    collection_id      uuid NOT NULL,
    source_revision_id uuid REFERENCES core.source_revisions,
    plugin_id          text NOT NULL,
    pipeline_key       text NOT NULL,
    pipeline_version   int  NOT NULL,
    seq                bigint NOT NULL,                  -- per (project, collection)
    status             text NOT NULL CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'canceled', 'superseded')),
    stop_reason        text CHECK (stop_reason IN ('cancel', 'failure', 'superseded')),
    outcome            text,                             -- PUBLISHED, NO_CHANGE, ...
    created_by         uuid NOT NULL REFERENCES core.principals,
    created_at         timestamptz NOT NULL DEFAULT now(),
    finished_at        timestamptz,
    UNIQUE (collection_id, seq),
    FOREIGN KEY (tenant_id, project_id) REFERENCES core.projects (tenant_id, id),
    FOREIGN KEY (plugin_id, pipeline_key, pipeline_version) REFERENCES runtime.pipelines,
    CHECK ((status IN ('succeeded', 'failed', 'canceled', 'superseded')) = (finished_at IS NOT NULL))
);

CREATE TABLE runtime.job_stages (
    job_id      uuid NOT NULL REFERENCES runtime.jobs,
    stage_key   text NOT NULL,
    status      text NOT NULL CHECK (status IN ('blocked', 'ready', 'running', 'succeeded', 'failed', 'canceled')),
    approved_by uuid REFERENCES core.principals,
    PRIMARY KEY (job_id, stage_key)
);

CREATE TABLE runtime.tasks (
    id           uuid PRIMARY KEY DEFAULT uuidv7(),
    job_id       uuid NOT NULL,
    stage_key    text NOT NULL,
    parent_id    uuid REFERENCES runtime.tasks,
    child_key    text,
    depth        int  NOT NULL DEFAULT 0,
    scope        jsonb NOT NULL DEFAULT '{}',
    payload      jsonb NOT NULL DEFAULT '{}',
    status       text NOT NULL CHECK (status IN ('ready', 'running', 'retry_wait', 'succeeded', 'split', 'failed', 'canceled')),
    resolution   text CHECK (resolution IN ('ok', 'failed', 'canceled')),
    attempt      int  NOT NULL DEFAULT 0,
    lease_epoch  bigint NOT NULL DEFAULT 0,
    lease_token  uuid,
    lease_until  timestamptz,
    ready_at     timestamptz NOT NULL DEFAULT now(),
    deadline_at  timestamptz,
    error_code   text REFERENCES core.error_codes,
    FOREIGN KEY (job_id, stage_key) REFERENCES runtime.job_stages,
    UNIQUE (parent_id, child_key),
    CHECK ((parent_id IS NULL) = (child_key IS NULL)),
    CHECK ((status = 'running') = (lease_token IS NOT NULL)),
    CHECK ((status = 'failed') = (error_code IS NOT NULL))
);
CREATE INDEX ix_tasks_claim ON runtime.tasks (ready_at) WHERE status = 'ready';
CREATE INDEX ix_tasks_lease ON runtime.tasks (lease_until) WHERE status = 'running';

CREATE TABLE runtime.task_events (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    task_id      uuid NOT NULL REFERENCES runtime.tasks,
    request_id   uuid,
    op           text NOT NULL CHECK (op IN ('claim', 'heartbeat', 'complete', 'fail', 'split', 'expire', 'retry', 'cancel', 'terminal_fail')),
    lease_epoch  bigint NOT NULL,
    outcome      text NOT NULL,
    error_code   text REFERENCES core.error_codes,
    detail       jsonb NOT NULL DEFAULT '{}',            -- child ids for split
    at           timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX uq_task_events_request ON runtime.task_events (task_id, request_id) WHERE request_id IS NOT NULL;

CREATE TABLE runtime.lifecycle_events (
    id        bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    job_id    uuid NOT NULL REFERENCES runtime.jobs,
    stage_key text,
    from_status text,
    to_status text NOT NULL,
    detail    jsonb NOT NULL DEFAULT '{}',
    at        timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE runtime.resource_state (
    resource_key  text PRIMARY KEY,                      -- <plugin>.<resource> or core.embedder/core.llm
    state         text NOT NULL CHECK (state IN ('closed', 'open', 'half_open')),
    opened_at     timestamptz,
    samples       jsonb NOT NULL DEFAULT '[]'
);

CREATE TABLE runtime.resource_leases (
    resource_key  text NOT NULL,
    job_id        uuid NOT NULL REFERENCES runtime.jobs,
    window_start  timestamptz NOT NULL,
    window_end    timestamptz NOT NULL CHECK (window_end > window_start),
    status        text NOT NULL CHECK (status IN ('reserved', 'held', 'released')),
    PRIMARY KEY (resource_key, job_id),
    EXCLUDE USING gist (resource_key WITH =, tstzrange(window_start, window_end) WITH &&) WHERE (status <> 'released')
);

CREATE TABLE runtime.schedule_runs (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    plugin_id   text NOT NULL,
    key         text NOT NULL,
    project_id  uuid,
    due_at      timestamptz NOT NULL,
    job_id      uuid REFERENCES runtime.jobs,
    status      text NOT NULL CHECK (status IN ('due', 'started', 'done', 'failed')),
    UNIQUE (plugin_id, key, project_id, due_at)
);

-- ===================== knowledge =====================
CREATE TABLE knowledge.collections (
    id             uuid PRIMARY KEY DEFAULT uuidv7(),
    tenant_id      uuid NOT NULL,
    project_id     uuid NOT NULL,
    plugin_id      text NOT NULL,
    key            text NOT NULL,
    current_gen    bigint NOT NULL DEFAULT 0,            -- 0 = nothing published
    published_seq  bigint NOT NULL DEFAULT 0,
    next_seq       bigint NOT NULL DEFAULT 1,
    UNIQUE (project_id, plugin_id, key),
    FOREIGN KEY (tenant_id, project_id) REFERENCES core.projects (tenant_id, id)
);

CREATE TABLE knowledge.collection_sources (
    collection_id   uuid NOT NULL REFERENCES knowledge.collections,
    source_id       uuid NOT NULL REFERENCES core.sources (id),
    pipeline_key    text NOT NULL,
    pipeline_version int NOT NULL,
    last_manifest   text NOT NULL,
    last_seq        bigint NOT NULL,
    PRIMARY KEY (collection_id, source_id, pipeline_key, pipeline_version)
);

CREATE TABLE knowledge.generations (
    collection_id  uuid NOT NULL REFERENCES knowledge.collections,
    gen            bigint NOT NULL,
    job_id         uuid NOT NULL REFERENCES runtime.jobs,
    status         text NOT NULL CHECK (status IN ('published', 'retired')),
    published_at   timestamptz NOT NULL DEFAULT now(),
    retired_at     timestamptz,
    PRIMARY KEY (collection_id, gen)
);

CREATE TABLE knowledge.units (
    id             uuid PRIMARY KEY DEFAULT uuidv7(),
    collection_id  uuid NOT NULL REFERENCES knowledge.collections,
    source_id      uuid REFERENCES core.sources (id),
    kind           text NOT NULL,                        -- <plugin>.<kind>
    key            text NOT NULL,
    created_by     uuid NOT NULL REFERENCES core.principals,
    on_behalf_of   text,                                 -- unverified
    UNIQUE NULLS NOT DISTINCT (collection_id, source_id, kind, key)
);

CREATE TABLE knowledge.unit_versions (
    id              uuid PRIMARY KEY DEFAULT uuidv7(),
    unit_id         uuid NOT NULL REFERENCES knowledge.units,
    parent_version  uuid REFERENCES knowledge.unit_versions,
    content_hash    text NOT NULL,
    attrs           jsonb NOT NULL DEFAULT '{}',
    candidate_job   uuid REFERENCES runtime.jobs,        -- set while staged
    valid_from_gen  bigint,                              -- NULL = staged, invisible
    valid_to_gen    bigint,
    CHECK (valid_to_gen IS NULL OR (valid_from_gen IS NOT NULL AND valid_to_gen > valid_from_gen)),
    CHECK (valid_from_gen IS NOT NULL OR candidate_job IS NOT NULL)
);
CREATE UNIQUE INDEX uq_unit_open_version ON knowledge.unit_versions (unit_id) WHERE valid_from_gen IS NOT NULL AND valid_to_gen IS NULL;
CREATE UNIQUE INDEX uq_unit_staged_per_job ON knowledge.unit_versions (unit_id, candidate_job) WHERE valid_from_gen IS NULL;
CREATE INDEX ix_unit_versions_visible ON knowledge.unit_versions (unit_id, valid_from_gen, valid_to_gen);

CREATE TABLE knowledge.chunks (
    id               uuid PRIMARY KEY DEFAULT uuidv7(),
    unit_version_id  uuid NOT NULL REFERENCES knowledge.unit_versions ON DELETE CASCADE,
    ordinal          int  NOT NULL CHECK (ordinal >= 0),
    text             text NOT NULL CHECK (text <> ''),
    span             jsonb NOT NULL DEFAULT '{}',
    attrs            jsonb NOT NULL DEFAULT '{}',
    UNIQUE (unit_version_id, ordinal)
);

CREATE TABLE knowledge.representation_values (
    id           uuid PRIMARY KEY DEFAULT uuidv7(),
    tenant_id    uuid NOT NULL REFERENCES core.tenants,
    rep_kind     text NOT NULL,
    config_hash  text NOT NULL,
    input_hash   text NOT NULL,
    dims         int,
    vec          vector,                                 -- dims vary per kind; HNSW per kind via partial expression index
    body         text,                                   -- text/json values (summaries)
    UNIQUE (tenant_id, rep_kind, config_hash, input_hash),
    CHECK ((vec IS NULL) = (dims IS NULL)),
    CHECK (vec IS NOT NULL OR body IS NOT NULL)
);
-- One HNSW index per vector representation kind, created at plugin registration, e.g.:
-- CREATE INDEX ix_rv_code_embed ON knowledge.representation_values
--     USING hnsw ((vec::vector(1024)) vector_cosine_ops) WHERE rep_kind = 'code.embed';

CREATE TABLE knowledge.representation_links (
    value_id         uuid NOT NULL REFERENCES knowledge.representation_values,
    unit_version_id  uuid REFERENCES knowledge.unit_versions ON DELETE CASCADE,
    chunk_id         uuid REFERENCES knowledge.chunks ON DELETE CASCADE,
    rep_kind         text NOT NULL,
    CHECK ((unit_version_id IS NULL) <> (chunk_id IS NULL)),
    UNIQUE NULLS NOT DISTINCT (rep_kind, unit_version_id, chunk_id)
);
CREATE INDEX ix_rl_value ON knowledge.representation_links (value_id);

CREATE TABLE knowledge.candidate_keys (
    job_id   uuid NOT NULL REFERENCES runtime.jobs,
    unit_id  uuid NOT NULL REFERENCES knowledge.units,
    mode     text NOT NULL CHECK (mode IN ('touch', 'hold', 'delete')),
    reason   text,
    PRIMARY KEY (job_id, unit_id)
);

CREATE TABLE knowledge.generation_pins (
    id             uuid PRIMARY KEY DEFAULT uuidv7(),
    collection_id  uuid NOT NULL REFERENCES knowledge.collections,
    gen            bigint NOT NULL,
    expires_at     timestamptz NOT NULL
);
CREATE INDEX ix_pins_active ON knowledge.generation_pins (collection_id, gen, expires_at);
```
