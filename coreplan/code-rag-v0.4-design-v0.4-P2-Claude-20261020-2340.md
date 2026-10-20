# code-rag Design v0.4-P2 — RAG Core + Plugins

- Document version: v0.4-P2 (design only, no implementation). Supersedes v0.4-P1 as the reference; P1 stays in `coreplan/` unchanged.
- System version: v0.4. The previous implementation (v0.2.4) stays in the `code-rag` repository.
- Repository: `code-rag-core`.
- Written by: Claude, 2026-10-20 23:40 KST.

## 0. How to read this document

Each item has an ID, a status and a basis, so every decision and open point can be traced.

| Status | Meaning |
| --- | --- |
| DECIDED | Dy decided it explicitly; the basis gives the time of Dy's message |
| PROP | Proposed, not adopted. Stays PROP until Dy adopts it explicitly (D-14) |
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
| O-01 | Core user login: proposed answer P-P5 (none in the first step) | Dy adoption |
| O-02 | ADR numbering conflict in the source material (ADR-01 void vs kept) | Dy (ADR text not available) |
| O-03 | Chat archive scope: personal vs shared project | Dy, when L-15 is built |
| O-04 | Delegation (RFC 8693) timing | Dy, when L-15 is built |
| O-05 | pgvector recall and latency with one HNSW index and the generation filter (current-only vs many versions, selective vs broad, concurrent publish, index size, GC) | Measurement |
| O-06 | UNSET values: token TTL, `max_attempts`, reconciliation interval, pin TTL, GC grace, hold alert limit, policy values | Measurement |
| O-07 | Prompt removal from search on remote revocation | Later design |
| O-08 | `/api/core/v1/auth/delegated-token` assumed by `miniweb_files_tool.py` is not in the minicore router list | Check (SRC-1 §11) |
| O-09 | LoRA training and vLLM LoRA loading for `gpt-oss-120b-awq`; runtime stacking in vLLM | Test on the site's vLLM version |
| O-10 | Sandbox design for arbitrary R code (L-19) | Later design |
