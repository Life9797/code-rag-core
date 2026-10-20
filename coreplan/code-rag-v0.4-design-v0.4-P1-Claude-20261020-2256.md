# code-rag Design v0.4-P1 — RAG Core + Plugins

- Document version: v0.4-P1 (design only, no implementation)
- System version: v0.4. The previous implementation (v0.2.4) stays in the `code-rag` repository
- Written by: Claude, 2026-10-20 22:56 KST
- Repository: `code-rag-core` (v0.4 work happens here; the previous code-rag repository is not carried over)

## 0. How to read this document

Every item has an ID, a status and a basis.

| Status | Meaning |
| --- | --- |
| DECIDED | Dy decided it explicitly (basis gives the time of Dy's message) |
| PROP | Proposed by Claude, not adopted. Stays PROP until Dy adopts it explicitly (D-14) |
| OPEN | Not decided; needs Dy or measurement |
| UNSET | A value that is intentionally not set until measured |

Basis codes:

- `SRC-1`: Dy's pasted summary "RAG Core 설계 변경 정리" (written by Claude in another chat, 2026-10-20 21:38 KST). Its own items marked 확정 are carried as DECIDED; its 권장 items as PROP.
- `T hh:mm`: Dy's message in this thread at that KST time on 2026-10-20.
- `v0.2.4 file:line`: checked in the v0.2.4 code (`code-rag` repository).
- `REASON`: Claude's design reasoning (applies to PROP only).

## 1. Decisions by Dy (DECIDED)

| ID | Decision | Basis |
| --- | --- | --- |
| D-01 | v0.3 and below stay as they are; v0.4 starts the core-based redesign | T 22:04 |
| D-02 | v0.4 lives on its own branch; never pushed to `main` until stable. `main` is the v0.3 line | T 22:13 |
| D-03 | Preserving existing behavior is not a constraint; the golden set is a comparison baseline only | SRC-1 §3.1 |
| D-04 | minicore is a pattern reference only; the RAG Core is designed from scratch for RAG | SRC-1 §3.2, T 22:13 |
| D-05 | First scope: Core + code-rag plugin | SRC-1 §3.3 |
| D-06 | Target DB: PostgreSQL 18 | T 22:13 |
| D-07 | Existing code-rag decisions (register v5, PV5-xx) are references, applied later where relevant | T 22:13 |
| D-08 | DB split: local DB (core) and a remote DB attached later as a plugin, for search, long-term archive, LLM retraining data, and backup | T 22:13 |
| D-09 | File storage: Docker host folders backed by disks, shared into containers, expandable by adding disks | T 22:13 |
| D-10 | Plugin apps live in host folders shared into containers so code can be edited in place, unless this causes a real problem | T 22:13 |
| D-11 | Multi-tenant membership = option B: principals carry no `tenant_id`; tenant and project access come only from membership rows | T 22:16 (decision card) |
| D-12 | Work so that code and tokens are saved | T 22:23 |
| D-13 | Design only for now; code work waits until Dy says so | T 22:24 |
| D-14 | PROP items stay PROP until Dy adopts them; no implicit adoption | T 22:27, 22:31 |
| D-15 | Worker Protocol must stay independent of the in-process worker so HTTP or separate-process workers can be added without changing pipelines | T 22:27 |
| D-16 | All plugins mentioned so far are registered for later development (list in §9) | T 22:39, 22:49, 22:55 |
| D-17 | Core is prepared for the chat archive plugin | T 22:39 |

## 2. Layers (PROP)

| ID | Layer | Responsibility | Basis |
| --- | --- | --- | --- |
| P-L1 | Platform Core | Identity, tenants/projects, memberships, plugin registry, events, audit, API gateway, storage volumes, connector secrets, schedules, webhook routing | SRC-1 §5 |
| P-L2 | Runtime | Pipelines as registered data, jobs/stages/tasks, leases, retries, budgets, circuit breakers | SRC-1 §7 |
| P-L3 | Knowledge Services | Content model, collections, representations, generation publishing, search | SRC-1 §7 |
| P-L4 | Plugins | Fill extension slots (code-rag first, echo for validation) | SRC-1 §5 |

minicore patterns kept (SRC-1 §5.1): `plugin.yaml` manifest, self-registration, outbox/inbox, JWKS verification, permission registry, schema per plugin with UUID-only cross-schema references. Dropped (SRC-1 §5.2): HR integration, departments/ranks, LDAP, approval domains, the "every plugin is request/response + UI" assumption.

## 3. System layout (PROP)

| ID | Item | Design | Basis |
| --- | --- | --- | --- |
| P-S1 | `postgres` | PG 18 + pgvector; local DB is the system of record. Schemas `core`, `runtime`, `knowledge`, `plugin_<id>` | D-06, D-08 |
| P-S2 | `api` | One FastAPI process: Platform Core + search; loads each plugin's API router from `/plugins` | D-12, REASON (one process saves code) |
| P-S3 | `worker` | One process; claims tasks and runs plugin handlers in-process; also runs maintenance | D-12, D-15 |
| P-S4 | `embed` | bge-m3 on TEI, as in v0.2.4 | memory 2026-10-19 |
| P-S5 | External, already running | vLLM (gpt-oss-120b) and Open WebUI (calls code-rag as a tool) | memory 2026-10-19 |
| P-S6 | Host folders | `<root>/plugins/<id>/` (plugin.yaml + code, edited on host, restart to load); `<root>/vol/<name>/` one per disk, mounted at `/data/vol/<name>`; `<root>/pgdata` | D-09, D-10 |
| P-S7 | Plugin dependency caveat | A new Python dependency needs an image rebuild; code edits only need a restart | D-10, REASON |

Deferred until a plugin needs them (D-12): HTTP worker binding, OIDC user tokens and token exchange (RFC 8693), federated search over the remote DB, admin UI.

## 4. Invariants (PROP)

| ID | Invariant | Basis |
| --- | --- | --- |
| I-S1 | Each component has its own DB login (`api`, `runtime`, `purge`, `plugin_<id>`); superuser only for init/migration | SRC-1 §6.2; v0.2.4 `deploy/docker-compose.yml:15` uses `user=postgres` |
| I-S2 | The `api` login has no table privileges; only `SECURITY DEFINER` contract functions with fixed `search_path` | SRC-1 §8 |
| I-S3 | Every contract function takes `principal_id` and resolves access from memberships; token tenant claims are ignored | SRC-1 §8, D-11 |
| I-S4 | Memberships: (principal, tenant, role) and (principal, project, role); a project member must be a tenant member | D-11 |
| I-S5 | Tenant-owned rows carry `tenant_id` with composite FK `(tenant_id, id)` | SRC-1 §4 (existing pattern) |
| I-S6 | Destructive work runs only as the purge role; its flag is transaction-local | SRC-1 §6.4; v0.2.4 `db/functions/21-sys-maint.sql:31` uses session scope |
| I-S7 | A plugin login reaches only its own schema plus Core contract functions | SRC-1 §5.1 |
| I-S8 | Audit records membership, role, client, token changes; delegated calls record `sub` and `act` | SRC-1 §8 |
| I-S9 | Security tests connect as each real login | SRC-1 §6.3 |
| I-R1 | A job pins `pipeline@version` at creation | SRC-1 §7 |
| I-R2 | A state change and its event commit in the same transaction | PV5-02 (D-07) |
| I-R3 | Exactly one function may set work to failed | PV5-01 (D-07) |
| I-R4 | Budgets are derived from events | v0.2.4 E4 (D-07) |
| I-R5 | Stage graph is a DAG, checked at registration; each stage has a barrier rule | REASON |
| I-R6 | Lease fencing: work with a stale lease is dropped | v0.2.4 |
| I-K1 | Search sees one coherent published generation per requested collection; publishing never exposes a partial generation | T 22:41 review |
| I-K2 | Representation reuse only on exact `(tenant, rep_kind, config_hash, input_hash)` | T 22:37 review |
| I-K3 | Shared schemas contain no code-analysis names (`symbol`, `file`, `CODE_EMBEDDING` …), checked by a name check | SRC-1 §9.1 |
| I-F1 | Files are addressed as registered-scheme URIs; `vol://` paths cannot escape their volume; content identity is sha256 | D-09 |
| I-P1 | `plugin.yaml` contract version and `requires` range are checked at load; incompatible plugins are refused | SRC-1 §9.1 |
| I-P2 | A plugin owns its schema; Core never reads plugin tables | SRC-1 §5.1 |
| I-P3 | The remote DB is never the system of record | D-08 |

## 5. Runtime (PROP)

### 5.1 Concept model

| ID | Concept | Design | Basis |
| --- | --- | --- | --- |
| P-R1 | Pipeline | `(plugin, key, version)`, immutable once registered; old versions kept while jobs use them | SRC-1 §7 |
| P-R2 | Stage | `key`, `kind` (control/item), `handler`, `depends_on[]`, split rule `{max_items, max_depth}`, policy keys, barrier (all tasks) | SRC-1 §7 |
| P-R3 | Job | Pins pipeline version, project, collection, source revision; gets a per-project sequence number at creation | I-R1, T 22:37 |
| P-R4 | Job stage | `(job, stage, status)`; replaces `job_phases` and the hard-coded phase list | SRC-1 §4 |
| P-R5 | Task | `(job, stage, scope jsonb, payload jsonb, parent, child_key, attempt, lease, status)` | SRC-1 §7 |
| P-R6 | Task event | Append-only; carries `request_id`, outcome, and child ids for split | T 22:37 |
| P-R7 | Policy | `(scope: global/plugin/stage, key, value)`; plugin registers key with type and bounds; values UNSET until measured; constraint relations (e.g. statement timeout < lease) fixed in design | SRC-1 §7 |
| P-R8 | Resource | External service a plugin calls (embedder, LLM); one circuit breaker per resource | REASON |

### 5.2 Worker Protocol v1 (transport-neutral, D-15)

Every mutation carries `(task_id, lease_token, request_id)`. `request_id` is unique per task (unique index on task events); the outcome is stored atomically with the state change. Check order: lease not current → `STALE_LEASE` (no change); task terminal with same `request_id` → `REPLAY` (stored outcome, including original children for split); task terminal with a new `request_id` → `ALREADY_TERMINAL`.

| ID | Message | Transition | Outcomes |
| --- | --- | --- | --- |
| P-W1 | `claim(worker, loaded_plugins[], max)` | ready → running, new lease | tasks only for pipeline versions the worker has loaded |
| P-W2 | `heartbeat` | extends lease | `EXTENDED`, `STALE_LEASE`, `DEADLINE` |
| P-W3 | `complete` | running → succeeded | `OK`, `REPLAY`, `STALE_LEASE`, `ALREADY_TERMINAL`, `OUTPUT_INVALID` |
| P-W4 | `fail(code, retryable)` | running → retry_wait or failed (single failed-writer) | `RETRY_SCHEDULED`, `FAILED`, `REPLAY`, `STALE_LEASE`, `ALREADY_TERMINAL`, `UNKNOWN_ERROR_CODE` |
| P-W5 | `split(children[{child_key, payload}])` | running → split; children unique on `(parent, child_key)` | `SPLIT`, `REPLAY`, `STALE_LEASE`, `ALREADY_TERMINAL`, `SPLIT_LIMIT` |

Payloads are JSON. Handlers never touch runtime tables; results go through Knowledge Services contract calls. Binding 1: in-process Python → SQL contract. Binding 2 (later): same messages over HTTP at `/runtime/v1/*`, no pipeline change.

## 6. Knowledge Services (PROP)

### 6.1 Content model

`Source → Source revision → Unit → Chunk → Representation → Generation`, grouped by collection. Unit kinds and representation kinds are registered by plugins (namespaced `plugin.kind`).

| ID | Item | Design | Basis |
| --- | --- | --- | --- |
| P-K1 | Collection | Publication namespace `(project, collection)`, declared in `plugin.yaml`; a plugin publishes only into collections it declared | T 22:39, 22:41 review |
| P-K2 | Generation | Per collection: current generation pointer and publish sequence | T 22:39 |
| P-K3 | Visibility | Unit rows carry `valid_from_gen`, `valid_to_gen`; visible at g iff `valid_from ≤ g` and (`valid_to` is NULL or `g < valid_to`). One rule for full rebuild and delta publish, no copying | T 22:39 |
| P-K4 | Representation value | Shared value keyed by `(tenant, rep_kind, config_hash, input_hash)`; `config_hash` = model, revision, dims, dtype, normalization, template; `input_hash` = exact model input; each target has its own link row | T 22:37 |
| P-K5 | Attribution | Units record `created_by` (authenticated principal) and `on_behalf_of` (client-supplied user, unverified until delegation) | T 22:39 |

### 6.2 Contracts

Tenant and project always come from the job (or memberships, for search), never from the caller.

| ID | Contract | Rules | Outcomes |
| --- | --- | --- | --- |
| P-C1 | `put_unit(task_ctx, kind, key, content_hash, attrs, chunks[])` | Only registered kinds of the calling plugin; key `(job, kind, key)`; unit + chunks in one transaction, lease-fenced | `unit_id`, `REPLAY`, `CONFLICT`, `STALE_LEASE` |
| P-C2 | `put_representation(task_ctx, target, rep_kind, model_id, content_hash, value)` | Reuse per P-K4, tenant-scoped | `OK`, `REUSED`, `REPLAY`, `CONFLICT`, `DIM_MISMATCH`, `STALE_LEASE` |
| P-C3 | `publish(job)` | Final Core-owned stage. Checks coverage per representation kind; new rows get `valid_from = g_new`, deletions `valid_to = g_new` (both invisible to current readers); one publish lock per collection; under the collection row lock, publishes only if job sequence > published sequence, then swaps the pointer. A failed `g_new` never becomes current; its rows are removed and its `valid_to` marks reset | `PUBLISHED`, `NO_CHANGE`, `COVERAGE_FAILED`, `SUPERSEDED`, `STALE` |
| P-C4 | `search(principal, projects[], collections[], query, k, rep_kinds[])` | Authorize all requested projects first; any failure → `FORBIDDEN`, no results; unknown project also `FORBIDDEN`. Reads current generation per collection in the same statement as the results; RRF fusion across representation kinds; no cross-collection atomicity (documented) | results + `generation_id` per collection, `FORBIDDEN`, `GENERATION_GONE` |
| P-C5 | `delete_unit(principal, unit_id)` | Staged; disappears at the next publish. Allowed: project contributor, or the verified `created_by` principal. `on_behalf_of` grants nothing | `STAGED`, `FORBIDDEN`, `NOT_FOUND` |
| P-C6 | GC | Search holds the shared GC lock for its transaction; GC takes it exclusively (with `lock_timeout`) and deletes only generations retired longer than the grace period; a value is deleted only when no link row points to it; paging pins `generation_id` | v0.2.4 `gc_lock_shared` (D-07) |

## 7. Platform Core (PROP)

| ID | Item | Design | Basis |
| --- | --- | --- | --- |
| P-P1 | Principals | `principals(kind: human/service, status)`; `external_identities(issuer, subject)` → principal | SRC-1 §8 |
| P-P2 | Roles | reader=1 < contributor=2 < operator=3 < admin=4. Effective project role = max(project role, admin if tenant admin). Project membership never lowers tenant admin; other tenant roles do not carry to projects | T 22:40 review |
| P-P3 | Authorization | `core.authorize(principal, project, min_role)` in every contract function; only the API maps a token to a principal | I-S2, I-S3 |
| P-P4 | Service tokens | `service_clients(principal, secret hash argon2id, audiences, scopes, expires_at)`; `POST /core/v1/token` (client credentials) → short JWT, ES256 with `kid`; `/core/v1/jwks`; rotation: old key verifies until max TTL passes; `aud = coderag-core`; scope per endpoint; client status checked each request (immediate revocation); membership always re-checked. Token TTL UNSET. API keys only in dev mode | T 22:40 review |
| P-P5 | Open WebUI identity (first step) | Open WebUI is its own service principal with its own memberships; user delegation later | D-12 |
| P-P6 | Plugin registry | `plugins(id, version, contract, manifest, manifest_hash, status)` plus registered kinds, collections, policy keys, resources, permissions, schedules; several versions may coexist | T 22:40 review |
| P-P7 | Load checks (api and worker start) | contract and `requires` compatible; schema migration level; handlers exist; unique kinds; pipeline DAG valid; same version + different manifest refused. Refused plugin: its tasks are not claimed; others keep running | I-P1 |
| P-P8 | Manifest hash | YAML parsed → canonical JSON (RFC 8785 JCS) → sha256 | T 22:40 review |
| P-P9 | Audit | `core_audit` owned by `core_owner`; insert only through a `SECURITY DEFINER` function; trigger rejects UPDATE/DELETE; retention via owner-only procedure | T 22:40 review |
| P-P10 | Storage volumes | Volume registry; host disk mounted at `/data/vol/<name>`; new files go to the volume with most free space | D-09 |
| P-P11 | Storage URIs | Registered schemes: `vol://<name>/<path>`, `ext://<connector>/<id>@<version>`; external ids opaque and URL-encoded; a URI never grants access | T 22:48 |
| P-P12 | Connector secrets | `core.get_connector_secret(task_ctx, connector)`: own tenant and owning plugin only; encrypted with a host-held key; versioned rows for rotation; redacted from logs and payloads | T 22:48 |
| P-P13 | Sources | `sources(project, connector, external_id)` = logical source (branch is binding config); `source_revisions(source, revision_key, manifest_hash, storage_ref)` unique on `(source, revision_key)`; `revision_key` immutable (commit SHA, upload UUID); manifest lists `(path, sha256, size)`. Upload is connector `upload` | T 22:52, 22:55 |
| P-P14 | Source bindings | `source_bindings(project, connector, external_ref, config)`; connector-specific settings validated as config, no provider columns | T 22:52 |
| P-P15 | Input formats | Connector `produces: [file_tree@1]`, pipeline `inputs: [file_tree@^1]`; checked in `create_job` → `INPUT_FORMAT_INCOMPATIBLE` | T 22:52 |
| P-P16 | Plugin-created jobs and coalescing | Connector creates jobs as its service principal with `job.create`. Per (project, collection, source): manifest equal to last published for same pipeline version → `NO_CHANGE`, no job; queued not-started job → `SUPERSEDED` (never runs); running job keeps running, publish sequence decides (P-C3). Supersession never touches a published generation | T 22:52, 22:55 |
| P-P17 | Schedules | Plugins register periodic tasks (e.g. reconciliation, interval UNSET); Core owns execution tracking and retry | T 22:52 |
| P-P18 | Webhooks | Core gateway routes inbound webhooks to the owning plugin; plugin verifies signature; `(plugin, delivery_id)` unique (duplicates no-op); returns 202 and only enqueues discovery | T 22:52 |
| P-P19 | Change notifications | A notification is only a hint; scheduled reconciliation recovers missed, duplicated or reordered changes | T 22:48 |

## 8. `plugin.yaml` v1 (PROP)

Core reads only these fields: `id`, `version`, `contract: 1`, `requires: {core: ">=0.4,<0.5"}`, `entry` (Python module), `schema` (`plugin_<id>` + migrations folder), `handlers[]`, `pipelines[]` (stages per P-R2, `inputs[]`), `produces[]`, `collections[]`, `unit_kinds[]`, `representation_kinds[]` (model, dims), `policy_keys[]`, `resources[]`, `permissions[]`, `schedules[]`, `events: {emits, consumes}`, optional `api: {router, prefix}`.

## 9. Plugins

### 9.1 First scope (D-05)

| ID | Plugin | Design (PROP) | Basis |
| --- | --- | --- | --- |
| L-01 | code-rag (`code`) | Unit kinds `code.file`, `code.symbol` (parent file; chunks from symbol text). Representation kinds `code.embed` (bge-m3 1024), `code.summary` (LLM, summary + summary_ko), `code.summary_embed`. Collection `code`, full rebuild. Pipeline `code.analyze@1`: ingest → parse (item, split by file batch) → graph; embed after parse; knowledge after graph; knowledge_embed after knowledge; Core publish after embed and knowledge_embed; barrier all. Schema `plugin_code`: inventory, dependency/call facts, resolved edges (to symbol unit UUIDs), graph quality summaries, knowledge runs (ported from v0.2.4 DDL). Resources `embedder`, `llm`. Policy keys `code.parse.*`, `code.graph.*`, `code.knowledge.*` UNSET. Parse reuse key `(file sha256, parser version, parser config hash)` | T 22:46, 22:55 |
| L-02 | echo (validation) | Unit `echo.item`; representation `echo.vec` (deterministic 8-dim, no model; exercises reuse and `DIM_MISMATCH`); one split stage reaching depth 2 then delta publish into collection `echo`; handler over the HTTP binding; I-K3 name check must pass with both plugins | SRC-1 §9.1, T 22:46 |

Search parameters (`candidate_k`, `rrf_k`, …) belong to Knowledge Services and keep the Phase 3 values as PROP (memory 2026-10-16).

### 9.2 Registered for later development (D-16)

| ID | Plugin | Summary (PROP) | Basis |
| --- | --- | --- | --- |
| L-10 | Document | Document ingest and search; paper design used to check that Core fits | SRC-1 §9 |
| L-11 | Vision | Image representations | SRC-1 §9 |
| L-12 | CI/CD | Build and pipeline events; separate from Gitea (sources) | SRC-1 §9, T 22:50 |
| L-13 | Data ingest | Data collection plugin | SRC-1 §9 |
| L-14 | Remote DB store | Remote DB for search, long-term archive, LLM retraining data, backup; never the system of record (I-P3); federated search with timeout and partial-result flag | D-08 |
| L-15 | Chat archive (`chat`) | Units `chat.note`, `chat.file` (file identifies the attachment; extracted text is ordinary chunks); save API `POST /api/chat/v1/save` from an Open WebUI tool (optional auto-save filter); shared bge-m3; collection `chat` with delta publish; per-item delete (P-C5) | T 22:39 |
| L-16 | External storage source connector | Nextcloud/WebDAV or other storage APIs; snapshots bytes into a local volume (`produces: file_tree@1`); ETag is only a change hint, copied-bytes sha256 is the input identity; ETag change during read → retry, then `source_unstable`; connect only folders shared with the whole project team; remote deletion or revocation → staged `delete_unit` at next reconciliation; the reconciliation interval is the documented removal lag (no claim of immediate removal); storage must be inside the closed network; direct storage driver deferred | T 22:44, 22:48, 22:49 |
| L-17 | Gitea | Git source connector; revision = commit SHA; push webhook → discovery; scheduled reconciliation; feeds `code.analyze`; read-only service account; repository size limits, LFS, submodules handled in the plugin | T 22:49, 22:50 |
| L-18 | Ops / evaluation | Golden-set evaluation as a plugin; plugins register their own golden sets | SRC-1 §9.2 |

## 10. Milestones (PROP, from SRC-1 §9.2)

| Step | Output | Done when |
| --- | --- | --- |
| M0 Design | Invariants, concept model, plugin contract, tenant decision (done: D-11) | This document adopted by Dy |
| M1a | Identity, tenants, memberships, access layer, DB logins, plugin registry | Security tests pass under real logins |
| M1b | Runtime: pipeline registry, state machine, Worker Protocol, policy store | echo and code pipelines both run |
| M2 | Knowledge Services: content model, collections, representations, publish, search | Publish, search and tenant isolation verified on dummy data |
| M3 | code-rag plugin: port parser, resolver, summaries | Golden 3 repos + at least 1 real repo + more Korean queries, comparable to v0.2.4 results |
| M4 | Deploy and evaluation: compose, eval plugin, Open WebUI tool | Single server starts with `up` only |

## 11. What v0.2.4 shows vs what v0.4 needs

- Shown by v0.2.4: lease/claim/sweeper/breaker semantics, full ingest-to-publish run, partition build, Phase 3 search quality (recall@10 0.931, memory 2026-10-16).
- Not shown, needed by v0.4: generic stages (phases are hard-coded), collections and delta publish, separate DB logins, membership authorization, plugin loading, a second consumer, generic sources and connectors.

## 12. Open items

| ID | Item | Needs |
| --- | --- | --- |
| O-01 | Whether Core provides its own login and user management | Dy |
| O-02 | ADR numbering conflict in source material (ADR-01 void vs ADR-01 kept) | Dy (ADR text not available) |
| O-03 | Chat archive scope: personal vs shared project | Dy, when L-15 is built |
| O-04 | Delegation (RFC 8693) timing: first step or later | Dy, when L-15 is built |
| O-05 | pgvector speed and recall with the generation range filter (current-only vs many versions, selective vs broad, concurrent publish, index size, GC) | Measurement at implementation |
| O-06 | All UNSET values: token TTL, reconciliation interval, grace period, policy values | Measurement |
| O-07 | Prompt removal from search on remote revocation | Later design |
| O-08 | `/api/core/v1/auth/delegated-token` assumed by `miniweb_files_tool.py` is not in the minicore router list | Check (SRC-1 §11) |
