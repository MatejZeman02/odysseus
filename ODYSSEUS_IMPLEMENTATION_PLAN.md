# Odysseus Lean MVP Implementation Plan

**Status:** Companion foundations, reviewed patches, scoped working artifacts,
and the safe read-only broker are implemented. Semantic continuity integrity,
conversation transfer, and provider-backed scoped recall are the active path.

**Last reconciled with the implementation:** 2026-08-27

**Product specification:** [`ODYSSEUS_PRODUCT_SPEC.md`](ODYSSEUS_PRODUCT_SPEC.md)

**G1 execution plan:**
[`ODYSSEUS_G1_EXECUTION_PLAN.md`](ODYSSEUS_G1_EXECUTION_PLAN.md)

**G1 evidence:**
[`ODYSSEUS_G1_LOCAL_READINESS.md`](ODYSSEUS_G1_LOCAL_READINESS.md)

**G1.5 acceptance evidence:**
[`docs/G1_5_ACCEPTANCE_REPORT.md`](docs/G1_5_ACCEPTANCE_REPORT.md)

**Proposed G2 execution plan:**
[`ODYSSEUS_G2_EXECUTION_PLAN.md`](ODYSSEUS_G2_EXECUTION_PLAN.md)

**G2B reviewed-patch implementation:**
[`ODYSSEUS_G2B_EXECUTION_PLAN.md`](ODYSSEUS_G2B_EXECUTION_PLAN.md)

**G2C scoped-memory and working-artifact plan:**
[`ODYSSEUS_G2C_EXECUTION_PLAN.md`](ODYSSEUS_G2C_EXECUTION_PLAN.md)

**Deferred architecture backlog:**
[`ODYSSEUS_ARCHITECTURE_BACKLOG.md`](ODYSSEUS_ARCHITECTURE_BACKLOG.md)

**Upstream synchronization branch:** `dev` (kept equal to `upstream/dev`)

**Active downstream branch:** `feat/companion-continuity-mvp`

## How to read this plan

This file is the active, smaller implementation plan. The `_HUGE` documents are
an idea bank only; they are not the current backlog and must not silently expand
the scope of this plan.

Progress labels are deliberately non-temporal:

- **Complete** — implemented and covered by recorded evidence.
- **Active** — useful owner testing or stabilization is happening now.
- **Deferred** — intentionally excluded from the current milestone.
- **Blocked** — cannot proceed without a named decision or dependency.

## Current state

| Milestone | State | Evidence or boundary |
|---|---|---|
| G1 persistence/compiler and read-only Qwen harness | **Complete** | Scoped projects, raw history retained during compaction, artifact contracts/store, ModelBridge, pinned Qwen Serve, Bubblewrap, protected Dust canary |
| G1.5 Companion homes and Qwen UI trial | **Complete** | Deterministic homes, projects/forks, Qwen streaming/Stop/Process/feedback, native comparison, desktop/mobile audit |
| Semantic checkpoint and project-brief derivation | **Partial; must be replaced** | Production writes a source-linked checkpoint and brief using a conservative local heuristic. It is not a validated accepted/proposed-state writer and must not be treated as canonical truth. |
| Normal-use stabilization | **Active** | Use real project conversations, record incorrect/unsafe answers and UI failures, fix regressions without broadening authority |
| G2C scoped chat memory and working artifacts | **Partial** | Local scope-filtered recall, Personal/project briefs, revisioned artifacts, long-paste capture, Documents bridge, and one-request project grants exist. Transfer/synthesis and AgentMemory migration do not. |
| Computer Help read-only diagnostics | **Partial** | Safe snapshots, private incident records, and the qualified command-only Podman broker exist. User-level task execution, filtered egress, and reversible transactions do not. |
| G2A sandboxed project inspection | **Rejected as Qwen runtime design; replacement complete** | Qwen 0.21.3 Podman cannot meet the boundary. The separate owner-controlled broker now supplies opt-in, read-only snapshot inspection to native Agent. |
| G2B reviewed project patches | **Complete; owner testing active** | Qwen proposes complete text changes while physically read-only; an owner-enabled per-project browser mode lets Odysseus validate, atomically apply, display, verify, and conditionally roll them back |
| Writable tools, Codex-grade sandbox, and the `_HUGE` plan | **Deferred** | No project writes, unrestricted shell, package/service mutation, or autonomous workflows |

## Ordered delivery roadmap

The work must now proceed in this order. Earlier stages repair the durable
state that every later feature depends on; later stages must not use an
unvalidated checkpoint as their authority.

### Stage C0 — Reconcile and contain the current heuristic

**Outcome:** existing automatically generated checkpoints/briefs remain
available for debugging but are explicitly labelled `heuristic` or
`legacy_unclassified`, never `accepted`, and cannot silently become project
truth or personal facts. **Completed in commit `4906ba3`.**

- add a derivation version/status and source span to every generated record;
- migrate existing heuristic records additively, with no transcript rewrite;
- render their provenance and limitations in the context disclosure;
- preserve the current local fallback only as an availability aid when the
  semantic derivation route is unavailable.

### Stage C1 — Validated semantic checkpoints and home briefs — implementation complete

**Outcome:** each compacted conversation can produce an immutable bounded,
schema-validated proposal separating objective, facts, decisions, proposals,
failed approaches, questions, actions, and artifact references. Promotion is
an explicit owner action that selects entries into accepted home state.

- use a narrowly scoped derivation route with no more authority than the
  originating chat;
- make source message IDs/hashes, validation, conflict handling, cancellation,
  and provider failure first-class;
- require explicit owner promotion before a record becomes `accepted` project
  or personal home state; unreviewed content remains derived/provisional;
- prove a fresh worker and a project fork continue from automatic artifacts,
  never a different chat's raw tail.

The proposal contract, bounded no-tools derivation route, owner-selected
promotion transaction, Context-panel review/history, and debounced
owner/session background admission are implemented. A newer foreground turn
cancels pending derivation; a safe persisted result reports ready, cancelled,
or a stable failure code without storing provider output. The remaining C1
operational gate is a configured-provider canary. Continuation proof is Stage
C2.

C2 additionally enforces the non-negotiable authority boundary: a newly
derived heuristic checkpoint cannot replace an owner-promoted project or
Personal brief. The reader prefers the latest accepted brief if historical
heuristic rows already superseded it.

### Stage C2 — Prove durable continuation

**Outcome:** a fresh worker and a project fork continue from source-linked
automatic artifacts, not a different chat's raw transcript tail.

- replay and restart tests prove checkpoint/brief reconstruction;
- derived records remain explicitly provisional until an owner promotion;
- source spans, cancellation, failed derivations, and deleted-source behavior
  remain visible and non-destructive.

### Stage C3 — Checkpoint transfer and synthesis, not transcript merge

**Outcome:** a finished chat can transfer selected context safely.

The initial read-only attachment primitive is implemented: a compact active
checkpoint may be mounted into another owner-owned Personal/project home with
an explicit detach action. It is bounded, attributed, and compiled separately
from the destination transcript. The owner may promote selected mount entries
into the destination home with field/index selections only. Mounts expire after
an owner-selected bounded lifetime (14 days by default), and sensitive context
requires an explicit acknowledgement before attaching. A new sibling synthesis
chat can now be created from exactly two immutable checkpoint mounts, using the
destination chat's stored scope and model route. The remaining work is richer
synthesis review UI.

- attach an immutable checkpoint to another chat as a labeled read-only mount;
- selectively promote owner-chosen items to Personal or project home state;
- create a new synthesis chat from two attributed checkpoints;
- keep raw transcript interleaving, automatic cross-home promotion, and true
  project branch merging out of scope.

### Stage C4 — Scoped retrieval provider adoption

**Outcome:** AgentMemory, if retained after inventory, becomes a replaceable
retrieval backend rather than project authority.

- inventory, export, back up, and dry-run existing native/AgentMemory data;
- add exact owner/home/project/sensitivity/grant filters and conformance tests;
- index only approved semantic artifacts and selected artifact metadata;
- retain the local exact index and make provider outage non-fatal;
- migrate no real records or writer until the owner approves the inventory.

**Preflight audit:** the current native JSON memory manager has useful
owner-aware, fail-closed mutation and compatibility/export behavior, but its
provider abstraction lacks explicit home/project/sensitivity/grant/retention/
expiry/provenance filters. An owner-confirmed, aggregate-only inventory is now
available; a separately approved backup report and scoped contract extension
remain mandatory before attempting AgentMemory recall or writes.

### Stage C5 — Continuity and project UX closure

**Outcome:** long-running work is understandable and remains fast.

- retain page-bounded chat history with the explicit **Load older messages**
  action now implemented;
- retain project home/related-project editing, explicit `@Project Name`
  resolution, and a three-project related-context cap now implemented;
- provide a concise context inspector for checkpoint, brief, mounted transfer,
  episodic hits, artifacts, and grants;
- add native `> [!question]` rendering/export only if it proves useful in
  normal use.

### Stage D3 — Computer Help task assistance

**Outcome:** begin only after C1/C2 establish trustworthy incident and task
state. Extend the already-qualified observation/broker foundation with
owner-selected task roots, filtered egress, transaction journals, rollback,
and the bounded **Approve for me** mode described in G2D. No `sudo`, raw host
shell, or system mutation is introduced by this stage.

## Goal

Build the smallest useful version of the expanded Odysseus:

> One familiar companion works through a few long-lived home conversations: one
> Personal Advisor chat, one Computer Help chat, and one primary chat for each
> project. Projects keep shared, source-linked notebooks, while unrelated
> discussions remain clean. Qwen Code can be delegated project work without
> becoming the durable memory system.

The first demonstrable flow was intentionally narrow. Its runtime, containment,
scope-isolation, and UI portions are proven; the semantic artifact-writing step
identified below still needs closure:

```text
open a conversation whose home project is Dust
  -> ask whether destroying the final crystal ends immortality
  -> Odysseus compiles a small scoped context packet
  -> a pinned Qwen Serve worker searches and reads Dust in read-only safe mode
  -> the answer cites exact files and shows the unresolved contradiction
  -> Odysseus retains the raw transcript
  -> Odysseus should derive two meaningful JSON artifacts:
       this thread's checkpoint and Dust's shared project brief
  -> after semantic derivation is closed, the primary Dust chat continues from
     its meaningful checkpoint and recent raw tail
  -> after semantic derivation is closed, an explicit Dust fork receives the
     shared brief, not the old transcript
  -> the ongoing Personal Advisor chat can answer "why is lemon acidic?" and
     receives no Dust context
  -> restarting Qwen does not destroy the durable project understanding
```

Current production turns create the checkpoint cursor but do not supply a
semantic derivation function, leaving objective/decisions/questions/actions
empty. `ProjectBriefV1` can be stored and compiled when pre-seeded, but no
production turn derives or writes it. Therefore fresh-worker raw-tail
continuation is working, while meaningful compact continuity beyond that tail
is not yet complete. This reconciliation supersedes stronger artifact-generation
wording in the earlier G1 evidence report.

G1.5 exposes the working flow through the ordinary Odysseus UI. The
terminal-shaped control is the project Qwen toggle, while Chat / Agent remains
available as an independent interaction preference. Project Qwen is read-only;
native comparison turns explicitly receive `allow_bash=false` and cannot use
heavy workspace tools.

Automatic project classification, transitive project graphs, the larger claim
graph, writable tools, a Codex-derived sandbox, group personalities, multiple
platforms, and production safety architecture remain outside the active scope.

## Simplifying decisions

- [x] Keep many purposeful conversations with one familiar companion identity.
- [x] Give every conversation one stable home: a project, Personal Advisor, or
  Computer Help. Do not silently change it from message text.
- [x] Use one ongoing Personal Advisor chat, one ongoing Computer Help chat, and
  one primary chat per project as the default layout. Permit explicit forks and
  extra chats; this is a useful default, not a storage constraint.
- [x] Share typed project artifacts across conversations, not raw transcripts.
- [x] Let directly related projects contribute explicitly requested, clearly
  labeled artifacts without merging their state into the home project.
- [x] Show a reload-safe, sanitized audit of the latest compiled scoped context
  in the Companion Context panel (categories and counts only; no prompt or
  recalled text).
- [x] Keep **Chat / Agent** as a preference, not as separate products.
- [x] Use Qwen Code early for its existing agent loop, project tools, planning,
  permissions, sessions, and compaction.
- [x] Do not reimplement Qwen's agent loop or project search in Python.
- [x] Make continuity and non-destructive compaction the main Odysseus work.
- [x] Use native Obsidian callouts such as `> [!question]`; do not invent a
  literal `[[!QUESTION]]` syntax.
- [x] Use the accepted provisional Dust authority order: detailed owner-authored
  notes outrank explicitly AI-written summaries; appended/unsorted ideas are
  proposals; red TODO/question material is unresolved; deleted/rejected material
  is history.
- [x] Target Fedora Linux only.
- [x] Do not require a VM for the read-only Project Mastermind milestone.
- [x] Keep the current GUI functional, but do not make GUI polish a prerequisite
  for the first headless/API acceptance test.
- [x] Do not build KV-cache switching. Durable continuity lives in JSON artifacts;
  any provider prompt/KV cache is disposable and only a performance detail.
- [x] Use Qwen as an optional pinned local runtime, not a Git submodule. Revisit a
  source fork only if Odysseus genuinely needs to patch Qwen.
- [x] Reuse the existing FAL OpenRouter endpoint and begin the live canary with
  `deepseek/deepseek-v4-flash`, but keep endpoint selection and credentials
  behind an Odysseus-owned provider-neutral bridge.
- [x] Keep ordinary Odysseus home chats alongside the new Companion homes.
- [x] Keep Chat / Agent visible. Project homes default to Agent with web-search
  UI enabled, but Qwen authority remains a separate read-only toggle.
- [x] Make a project's stored workspace, endpoint, model, and harness
  server-owned. Browser `localStorage` is presentation state, not authority.
- [x] Permit Qwen only in project homes during G1.5. Personal Advisor uses
  native Chat, and Computer Help explicitly says that Qwen is coming later.
- [x] Hide native chat-shell affordances without deleting `/api/shell/*`, which
  Cookbook and document execution still require.
- [x] Persist a compact, expandable Process trace and per-answer evaluation
  feedback without exposing raw tool output, absolute paths, credentials, or
  provider URLs.

## What already exists

The branch now contains the implemented G1/G1.5 runtime and UI foundation:

- additive project, scope, artifact, endpoint, harness, and primary-home state;
- deterministic Personal, Computer, and per-project primary conversations;
- explicit project forks and owner-scoped project deletion;
- raw messages retained during compaction plus `ThreadCheckpointV1` and
  `ProjectBriefV1`;
- deterministic continuity context compilation for native and Qwen turns;
- a provider-neutral, per-run ModelBridge with ephemeral credentials;
- pinned Qwen Code `0.21.3` supervised inside rootless Bubblewrap with one
  read-only project mount;
- authenticated JSON and streaming project-turn APIs with cancellation,
  concurrency admission, teardown, and post-run workspace verification;
- Companion navigation, project creation, scope disclosure, Qwen/native toggle,
  readable persisted Process traces, and Helpful/Wrong/Unsafe feedback;
- retained native chat, Cookbook, document execution, and ordinary home chats;
- rendered desktop/mobile browser auditing plus broad regression coverage.

Upstream Qwen Serve provides:

- persistent worker sessions and resume/load;
- HTTP prompt admission and SSE event streaming;
- search/read/edit/shell tools;
- plans, permission requests, cancellation, and tool events;
- MCP support, worktrees, context files, and in-session compaction;
- reconnect/replay behavior and capability negotiation.

G1.5 deliberately exposes only read/list/search operations. Safe-mode policy and
the Bubblewrap mount boundary deny the upstream runtime's edit/shell/web/memory
capabilities.

The Qwen Python SDK is not used. Odysseus owns a narrow HTTP/SSE adapter to
`qwen serve`; it does not port or duplicate Qwen's agent framework.

## Lean architecture

```text
              one companion identity + approved global profile
                                  |
             +--------------------+--------------------+
             |                    |                    |
     Dust project chat   Personal Advisor chat   Computer Help chat
        home: Dust          home: personal         home: computer
             |                    |                    |
             +--------------------+--------------------+
                                  v
                     ScopeResolver -> ContextCompiler
                                             |
                    +------------------------+-----------------------+
                    |                        |                       |
             thread checkpoint       home project brief    explicitly requested
             + recent raw tail        + source refs          related-project brief
                                             |
                                             v
                                       HarnessRouter
                              native Chat | native Agent | Qwen Serve
```

G1 introduced four continuity concepts:

1. `ScopeResolver`
2. `ArtifactStore`
3. `CheckpointCompactor`
4. `ContextCompiler`

All four are implemented. The Qwen adapter is a harness integration around
them, not a second durable state or memory system.

### ScopeResolver

Implemented/default deterministic rules:

1. An existing conversation's server-owned home binding always wins.
2. A new conversation opened from a project is bound to that project.
3. The UI reopens or creates the owner's primary Personal Advisor and Computer
   Help chats for those destinations.
4. Legacy/unclassified conversations retain a `general` compatibility scope.
5. Changing the browser's selected folder cannot silently rebind an existing
   conversation. An explicit audited move operation is deferred.
6. Looking up a related project does not change the conversation's home.

Do not build an LLM domain classifier yet. `Project`, `Personal`, and `Computer`
are context scopes, while **Chat / Agent** remains an independent harness
preference. The companion can answer any question, but the storage destination
and default context stay predictable.

### ArtifactStore

Use concrete project/session storage instead of a generic event-sourcing or
claim system:

```text
projects
  id, owner, name, workspace_root, settings_json, created_at, updated_at

sessions                         # extend the existing table
  scope_kind                     # project | personal | computer | legacy general
  project_id?                    # stable server-owned home binding

continuity_artifacts
  id, owner, project_id?, session_id?, kind, status, revision, payload_json,
  source_through_message_id?, source_hash, created_at
```

For the MVP, `projects.settings_json.related_project_ids` is an owner-configured
direct allowlist. A normalized relation table waits until actual queries require
one. Repository classes and typed dataclasses hide JSON columns from callers.

Minimal typed records:

```text
ResolvedScope
  owner_id, session_id, scope_kind, project_id?, workspace_root?

TurnContext
  turn_id, session_id, scope, interaction, capabilities,
  memory_policy, context_budget, harness

ThreadCheckpointV1
  schema_version, session_id, project_id?, objective, derived_working_state,
  accepted_decisions, proposals, open_questions, next_actions,
  artifact_refs, failures, source_message_ids, source_revision?

ProjectBriefV1
  schema_version, project_id, summary, derived_working_state,
  accepted_decisions, proposals, open_questions, current_plans,
  source_refs, source_session_ids, source_revision?, updated_at

ContextBundle
  companion_profile, scope_header, thread_checkpoint, primary_project_brief,
  related_project_bundles, episodic_hits, transcript_tail, request, manifest
```

G1 implements only `ThreadCheckpointV1` and `ProjectBriefV1`. Decisions, plans,
questions, reviews, and worker outputs can become separate artifact kinds only
when the two-record model proves insufficient.

Both records are derived, versioned, and source-linked; neither silently becomes
project canon. Files remain the source truth. A thread checkpoint continues one
conversation. A project brief is the compact shared notebook used by every
conversation bound to that project.

### Conversation, project, and related-project boundaries

- [x] Never inject another conversation's raw transcript or private tail.
- [x] A fresh project conversation loads the active `ProjectBriefV1`, not an old
  thread's `ThreadCheckpointV1`.
- [x] The Personal Advisor and Computer Help chats load no project material in
  G1.5. A future explicit project invocation must be separately designed and
  tested rather than inferred from ordinary message text.
- [x] The store/compiler admits only an explicitly supplied related-project ID
  that is present in the home project's direct allowlist.
- [ ] Resolve owner-facing project names or `@project` references to those IDs
  and expose the direct allowlist in the UI. G1/G1.5 tests currently pass IDs
  directly to the compiler.
- [x] Include only a labeled related `ProjectBriefV1` and source references in
  the initial cross-project bundle. Fetch exact cited file excerpts on demand in
  a later increment.
- [x] Exclude related projects' raw chats, personal memories, credentials,
  shell/tool transcripts, Qwen state, unlinked projects, and transitive links.
- [x] Keep related-project context ephemeral and separately labeled; never
  merge it into the primary project's brief.
- [ ] Add explicit item/token caps before exposing multi-related-project
  selection in the UI. G1 currently accepts only explicit server-validated
  related-project IDs and uses bounded transcript tails.

Future approved memories must remain discoverable only through scoped retrieval.
“Available” must not mean “inserted into every prompt.” G1.5 does not yet make
AgentMemory a continuity writer or retrieval authority.

Default owner-facing layout:

| Destination | Default conversation behavior | Shared durable context |
|---|---|---|
| Personal Advisor | Reopen one ongoing native chat for quick general and personal questions | Companion profile plus existing native personal-recall behavior; scoped AgentMemory is deferred |
| Computer Help | Reopen one ongoing native chat; show “Qwen coming next” | Its own retained chat history; a reviewed device profile is deferred |
| A project | Reopen one primary chat; allow a new thread/fork when useful | Compiled project scope and raw tail now; `ProjectBriefV1` across chats after semantic derivation closure |

These are navigation defaults, not uniqueness constraints. An intentional second
computer chat or project fork must remain possible.

### Non-destructive compaction

The current compactor eventually replaces persisted message rows with a summary.
That destroys the evidence needed for later re-compaction, auditing, forking, and
better future models.

The replacement behavior is now:

- [x] Never delete or rewrite raw persisted chat messages during compaction.
- [x] Summarize only messages after that thread's previous checkpoint cursor.
- [x] Keep tool-call/result groups atomic.
- [x] Validate `ThreadCheckpointV1` before committing it.
- [x] Store stable source message IDs and a content hash.
- [x] Compile the next prompt from the latest checkpoint plus a recent raw tail.
- [x] On summarization failure, retain everything and trim only that model call.
- [ ] Supply a production semantic derivation function so checkpoints contain
  meaningful objective, decisions, proposals, questions, actions, references,
  and failures instead of only a durable cursor/hash.
- [ ] Project only reviewed, source-linked, shareable checkpoint state into a
  new `ProjectBriefV1` revision. No production caller currently invokes
  `write_project_brief()`.
- [x] Permit a new chat bound to the same project to load its project brief
  without copying another chat's checkpoint or transcript when a brief exists.

The existing persisted compaction summaries remain historical messages. No
destructive migration is needed.

> [!QUESTION]
> Should the continuity closure use the currently selected conversation model
> for semantic checkpoint/brief derivation, or a separately configured utility
> model? Either route must remain owner-scoped, bounded, source-linked, and
> non-destructive.

### Deterministic context compiler

Compile context in this deterministic order:

```text
stable companion personality and policy
  -> conversation/home-project header
  -> this thread's checkpoint
  -> home project's shared brief
  -> explicitly invoked direct-related project brief, clearly labeled
  -> relevant scope-verified episodic hits
  -> recent raw transcript tail
  -> current request
```

Keep changing time, memory, retrieval, artifacts, and request data out of the
stable companion/policy prefix. This makes provenance and testing predictable.
If a provider happens to reuse a prompt-prefix/KV cache, that is incidental and
never part of the continuity contract.

### State and memory ownership

| Information | Initial owner | Rule |
|---|---|---|
| Project lore, notes, code, and media | Markdown/filesystem and Git | Source truth; Qwen reads and cites it |
| Project identity and direct relationships | Odysseus `projects` table | Stable owner-scoped configuration |
| Raw conversation | Odysseus message database | Retained evidence; compaction never rewrites it, while explicit owner edit/delete remains possible |
| One thread's working state | Odysseus `ThreadCheckpointV1` | Local, derived, and source-linked |
| Shared cross-chat project notebook | Odysseus `ProjectBriefV1` | Compact derived artifact; not canon |
| Companion identity and approved global profile | Existing personality/profile layer | Shared across chats; no project facts by default |
| Personal/project episodic recall | Local `ScopedMemoryIndex`; AgentMemory later | Rebuildable, exact scope-filtered index; not project truth |
| Legacy pinned/manual memories | Native `memory.json` during migration | Read/export/delete compatibility only after provider switch |
| Active worker transcript and compaction | Qwen session/JSONL | Disposable execution state |

One writer is assigned per memory class. Native memory and AgentMemory must not
both auto-capture the same turn.

### AgentMemory adapter

**State: Deferred.** The provider seam exists, but G1/G1.5 deliberately did not
inspect, migrate, or change the writer for the owner's real memory data. The
rules below remain the acceptance contract for a future memory milestone.

The existing `MemoryProviderRegistry` is initialized but normal prompt recall
still reads `memory.json` directly. A future AgentMemory milestone must wire the
provider abstraction into context assembly before calling it “integrated.”

Safety rules for the existing AgentMemory installation:

- [ ] Use it only as an episodic index over structured Odysseus artifacts and
  selected tool outcomes initially.
- [ ] Namespace new records with an opaque owner and project identifier.
- [ ] Use exact project-filtered search and post-verify every hit's scope.
- [ ] Drop unknown, legacy-unscoped, or mismatched hits.
- [ ] Do not use `memory_smart_search` as a project security boundary; its
  current hybrid result path does not reliably filter by project.
- [ ] Do not call AgentMemory's free-form `remember` or enable Qwen auto-memory
  initially.
- [ ] Keep AgentMemory outage non-fatal: exact continuity checkpoints still
  load without it.
- [ ] Inventory and back up the owner's existing AgentMemory/native data before
  changing the real writer.

G2C is broader than an AgentMemory provider switch. It must preserve private
per-chat checkpoints, add a distinct Personal home brief, permit Personal
Advisor to request narrowly scoped project-memory grants, and add revisioned
Markdown `.artifacts` for substantial working content. AgentMemory remains a
rebuildable retrieval index behind those Odysseus-owned records. The detailed
contract is in [`ODYSSEUS_G2C_EXECUTION_PLAN.md`](ODYSSEUS_G2C_EXECUTION_PLAN.md).

## Qwen Serve adapter

Odysseus implements a narrow, capability-gated client rather than mirroring the
complete Qwen API.

Implemented HTTP/SSE surface:

- `GET /health`
- `GET /capabilities`
- `POST /session` with `sessionScope: "thread"`
- `GET /session/:id/events` with `Last-Event-ID`
- `POST /session/:id/prompt`
- `POST /session/:id/cancel`

Later, after the read-only milestone:

- `GET /session/:id/transcript`
- `POST /permission/:requestId`
- `POST /session/:id/approval-mode` if an interactive writable milestone needs
  runtime mode changes; G1 enforces safe/Plan behavior in disposable settings
  instead
- explicit resume/load and file-diff review

The internal adapter tracks assistant chunks, sanitized tool updates, completion,
failure, cancellation, reconnect/replay, and worker death. The browser receives
only the G1.5 `status`, `tool`, `delta`, `done`, and `error` vocabulary. Raw tool
output and unknown daemon frames are never forwarded.

Implemented worker rules:

- [x] Pin Qwen Code `0.21.3` at the reviewed revision
  `c0196b422665aa000e9643555331baff8aef29da` and verify capabilities at
  startup.
- [x] Bind to `127.0.0.1` on an allocated port.
- [x] Generate a random bearer token and require authentication on all routes.
- [x] Disable the Qwen web UI.
- [x] Register only the exact read-only project workspace.
- [x] Use an isolated Qwen home/runtime directory and a scrubbed allowlisted
  environment.
- [x] Disable Qwen managed auto-memory and unrelated ambient MCP servers.
- [x] Start in safe/Plan mode with write, shell, network, skills, and memory
  tools unavailable.
- [x] Treat Qwen sessions as disposable: a fresh session must work from the
  Odysseus `ContextBundle`, not hidden Qwen history.

### Local installation and provider-neutral model bridge

Qwen is an optional external runtime, not vendored application source. The
implemented launch contract is:

1. Read the explicitly configured `ODYSSEUS_QWEN_BINARY` path and reject any
   version other than `0.21.3`.
2. Use the owner-approved data-local npm package at
   `data/qwen/0.21.3/node_modules/.bin/qwen` in the supplied launcher.
3. Permit another explicitly configured path only when it resolves to the same
   verified version.

Do not use `latest`, `npx`, or a Git submodule. A submodule would pin the entire
development monorepo and make every user build it from source. Keep the current
ignored clone only as a study reference. If patches become necessary, create a
small public Apache-2.0-compatible fork, contribute changes upstream, and build a
pinned release artifact; only then reconsider source pinning.

The existing encrypted, owner-scoped `ModelEndpoint` remains the sole durable
provider credential owner. G1 adds a run-scoped `ModelRoute` and a dedicated,
loopback-only OpenAI-compatible `ModelBridge`:

```text
Qwen -> bridge URL + fixed model id + short-lived bearer
     -> owner/run/endpoint/model-bound route inside Odysseus
     -> existing endpoint resolver and provider authentication
     -> configured OpenAI-chat-compatible upstream
```

Qwen's private disposable configuration contains only the bridge URL, fixed
model id, and the environment-key name holding the ephemeral bridge token. It
must never contain the upstream URL or reusable provider credential.

- [x] Resolve the route by endpoint id and owner; do not use a silent fallback
  chain or accept endpoint/model choices from Qwen.
- [x] Bind one expiring bridge token to one owner, run, endpoint, and model;
  enforce constant-time authentication plus body, request, concurrency, and
  wall-time limits.
- [x] Construct outbound provider headers from scratch inside Odysseus and never
  forward Qwen authentication, host, or arbitrary custom headers.
- [x] Support the OpenAI chat/tool protocol required by G1 and fail closed for a
  native provider protocol the bridge cannot represent honestly.
- [x] Stream ordinary JSON/SSE, usage, tool calls, errors, and cancellation in
  the exact shape the pinned Qwen client consumes.
- [x] Restrict the Qwen runtime directory to the owner, scrub all other
  credentials from its environment, and remove/revoke runtime state on teardown.
- [x] Fake-test the complete Qwen -> bridge -> provider path before one approved
  live canary through the configured FAL endpoint.
- [x] Keep the configured endpoint/model replaceable without redesigning the
  harness. The accepted live run used `deepseek/deepseek-v4-flash-0731`.

The bridge is a credential-routing boundary, while Bubblewrap is the current OS
containment boundary. Qwen gets one exact read-only project bind, a private home,
a tmpfs `/tmp`, an allowlisted environment, and no shell/write/web/memory tools.
It still shares the host kernel, user identity, and loopback networking needed
for ModelBridge, so it is not Codex-grade isolation. Stronger, separately
identified or Codex-derived containment is required before untrusted workspaces,
generic mutation, or distribution. A snapshot-capable Fedora VM is relevant
only for real systemd, package, codec, boot, or repair/rollback experiments.

## Dust fixture policy

No separate snapshot decision is required for the first local evaluation:

- run deterministic adapter/integration tests against a protected temporary
  checkout of Dust HEAD;
- run the semantic owner evaluation against the actual current working tree as
  the user's current draft workspace;
- record Git HEAD, status, content hashes, and whether each cited file is
  committed, modified, or untracked;
- use HEAD only as a comparison baseline;
- never interpret “current” or “committed” as “canon” without the authority
  rules above;
- check hashes before and after the run and fail if Plan mode changed anything;
- never execute Dust's mutating sorter;
- keep Dust external because of its CC BY-NC-SA license;
- use a small synthetic fixture for Obsidian wikilinks, callouts, frontmatter,
  aliases, embeds, and block references.

Initial golden questions:

- [x] Does destroying the final crystal definitely end immortality?
- [ ] Did the mage rebellion or the king originate resurrection?
- [x] What are the practices of the fish church?
- [ ] Are healing and speed-boost talismans accepted designs?
- [ ] What quests are currently described despite the case-colliding files?
- [ ] What changed from HEAD in the temple-library and unsorted ideas?

The final-crystal and fish-church questions formed the accepted G1 semantic
gate. The remaining questions are useful evaluation coverage, not prerequisites
for keeping G1.5 available during normal use.

Expected answers must distinguish exact source text, unresolved material,
conflict, inference, and advice. Portable unresolved questions use:

```markdown
> [!question] Does destroying the final crystal end immortality?
> The detailed story and a later unresolved proposal disagree.
```

## Borrow, wrap, or defer

| Source | Use now | Do not copy/build now |
|---|---|---|
| Current Odysseus | UI, sessions/messages, SSE, detached runs, personalities, workspace selection, native fallback, tests, memory-provider seam | A second chat system |
| Qwen Code | Install the pinned npm runtime locally and wrap `qwen serve`: agent loop, sessions, project tools, plans, permissions, MCP support, and in-session compaction | Vendor its monorepo as a submodule or port its TypeScript framework into Python |
| Existing model endpoint system | Resolve one owner-scoped endpoint/model behind the run-scoped `ModelBridge`; use FAL/DeepSeek only as the first canary selection | Expose upstream URL/key to Qwen, add a second credential store, or hard-code the harness to FAL |
| AgentMemory | Adapt scoped episodic retrieval and indexing after strict filtering tests | Make it project canon, privacy policy, or generic Odysseus KV |
| OpenAI Codex | Study/adapt Apache-2.0 Linux sandbox mechanics later, before host mutation | Build a custom sandbox for the read-only milestone |
| Antigravity | Reuse plan/review/question interaction ideas in the existing UI later | Automate or depend on its consumer application |
| Obsidian | Use native callout syntax and a synthetic compatibility fixture | Build a full Obsidian clone |
| Aider / markdown-oxide | Revisit only if Qwen's Git/search behavior fails measured tests | Add a second repository map/indexer pre-emptively |

## Branches and upstream policy

Keep branch overhead small:

```text
upstream/dev == dev                          # pristine synchronization branch
  -> feat/brain-extension-foundation        # historical foundation
      -> feat/companion-continuity-mvp       # completed G1/G1.5 integration
```

G1/G1.5 is committed on `feat/companion-continuity-mvp`. Do not push or open a
PR without explicit owner instruction. Keep local `dev` identical to
`upstream/dev`; inspect new upstream commits there, merge them first on a
temporary sync branch, test, and only then merge the sync result into the
downstream feature branch. The detailed procedure lives in
[`docs/upstream-sync.md`](docs/upstream-sync.md).

## Test-first implementation phases

### Phase 0 — Baseline and contracts — **Complete**

- [x] Freeze the approved specification, lean plan, upstream base, and feature
  branch boundary.
- [x] Record full and focused regression baselines.
- [x] Characterize FAL authentication, URL construction, model selection,
  endpoint ownership, redaction, and webhook compatibility in tests.
- [x] Encode conversation/project/artifact ownership, the Qwen boundary, and
  one-writer memory rules in the product/implementation contracts and tests.
- [x] Create fake provider/Qwen services, project-isolation fixtures, protected
  workspace checks, and Dust evaluation questions.

Separate ADR files were not created; the accepted decisions are currently
canonical in this plan, the product spec, and the executable tests.

### Phase 1A — Non-destructive continuity kernel — **Complete**

- [x] Add projects, server-owned session scope/project state, and versioned
  continuity artifacts through additive migration.
- [x] Implement owner isolation, deterministic primaries, explicit forks,
  direct project relationships, and browser-workspace non-authority.
- [x] Implement `ScopeResolver`, `ArtifactStore`, `CheckpointCompactor`, and
  `ContextCompiler` with `ThreadCheckpointV1` and `ProjectBriefV1`.
- [x] Preserve raw transcripts and always compile continuity for project-scoped
  native turns; keep the legacy unscoped path behind its compatibility flag.

### Phase 1B — ModelBridge and Qwen runtime harness — **Complete**

- [x] Implement the narrow integration as `src/model_bridge.py`,
  `src/qwen_harness.py`, `src/qwen_supervisor.py`, and
  `src/scoped_turn_service.py`; a larger generic harness package was not needed.
- [x] Verify the exact data-local Qwen version and supervise its disposable
  lifecycle outside the native agent loop.
- [x] Implement owner/run/endpoint/model-bound routing, ephemeral credentials,
  loopback authentication, bounded relay, cancellation, and teardown.
- [x] Generate private safe-mode configuration and prove upstream credentials,
  URLs, absolute project paths, and raw logs are not exposed to Qwen UI events.
- [x] Prove reconnect/replay, prompt completion, denial, timeout, cancellation,
  worker death, and process-group cleanup.

### Phase 2A — Headless Dust runtime vertical slice — **Complete**

- [x] Run a scoped project turn through Qwen, ModelBridge, and the configured
  provider using the selected personality and compiled continuity bundle.
- [x] Prove compiler isolation for seeded checkpoints/project briefs, explicit
  forks, Personal/Computer homes, and direct-related projects.
- [x] Run protected synthetic and Dust canaries with unchanged workspace hashes
  and Git status.
- [x] Pass the final-crystal and fish-church semantic gates, including conflict
  labeling and abstention on missing lore.
- [x] Record owner-readable G1 evidence without committing private project text
  or reusable credentials.

The owner kept Qwen and proceeded to the UI trial, completing the Qwen
retention decision at checkpoint G1. Automatic semantic artifact derivation was
not exercised by this slice and is deliberately tracked next.

### Phase 2B — Semantic continuity closure — **Partial; replace heuristic**

- [x] Write source-linked checkpoints and project/personal home briefs without
  rewriting raw messages. Current values are heuristic only.
- [ ] Add the C0 status/version migration so heuristic output is visible but
  cannot become accepted truth.
- [ ] Define the C1 bounded, schema-validated derivation request for
  `ThreadCheckpointV1`; retain all source message IDs/hashes and never rewrite
  raw messages if the derivation fails.
- [ ] Select the derivation route according to the `[!QUESTION]` above, resolve
  it owner-safely, and prevent the derivation call from receiving more authority
  than the corresponding chat turn.
- [ ] Derive and write a `ProjectBriefV1` only from explicitly shareable,
  source-linked checkpoint fields; retain a revision and source cursor for every
  projection.
- [ ] Prove an automatically produced checkpoint survives restart and a fresh
  project fork receives the automatically produced brief, never another thread's
  raw tail.
- [ ] Cover invalid JSON, provider failure, cancellation, concurrent projection,
  stale cursor/hash, cross-owner access, and a model that tries to invent canon.
- [ ] Add a visible but compact context disclosure that distinguishes raw tail,
  checkpoint, and project brief only after those artifacts are meaningful.

### Phase 3 — AgentMemory provider switch — **Deferred**

- [ ] Implement the strictly scoped adapter and fake-server contract tests.
- [ ] Wire normal context recall through `MemoryProviderRegistry`.
- [ ] Index only approved structured artifacts/tool outcomes in separate
  Personal, Computer, and project namespaces.
- [ ] Provide inventory, backup, dry-run migration, rollback, and duplicate
  reports for existing native and AgentMemory data.

**Checkpoint G2:** obtain explicit approval before reading the owner's real
memory stores, changing a writer, or migrating memory data.

### Phase 4A — G1.5 Companion UI trial — **Complete**

- [x] Keep ordinary chats, Chat / Agent, model selection, and the folder chip.
- [x] Add deterministic Personal Advisor, Computer Help, and project homes plus
  explicit project forks.
- [x] Persist project workspace/model/endpoint/harness state and restore it on
  launch without trusting browser `localStorage`.
- [x] Stream Qwen project turns with Stop, sanitized status/tool/delta/done/error
  events, one active turn, mutation verification, and no silent native fallback.
- [x] Show an explicit scope banner, model/personality label, read-only integrity
  badge, collapsed Process trace, compact expandable tool lines, context
  disclosure, and Helpful/Wrong/Unsafe feedback.
- [x] Persist Process and feedback across reload; fix inline edit/resend,
  deletion, duplicate primary/project records, service-worker caching, and form
  label/ID regressions.
- [x] Retain Cookbook/document shell APIs while preventing browser project chat
  from requesting native bash or heavy workspace tools.
- [x] Audit desktop and mobile behavior in a rendered browser and run a real
  provider-backed synthetic Qwen canary.

### Phase 4B — UI hardening beyond G1.5 — **Deferred**

- [ ] Paginate or virtualize very long chat history while retaining all older
  raw messages behind an explicit history action.
- [ ] Add a UI editor for stable home bindings and direct related-project
  allowlists; current bindings are created and enforced by the server.
- [ ] Add native `> [!QUESTION]` rendering/export if ordinary use demonstrates
  that callout artifacts belong in chat rather than only project files.
- [ ] Expand the context inspector only when a concrete debugging need cannot be
  met by the current manifest disclosure.
- [ ] Add explicit related-project item/token caps before exposing multi-project
  selection in the UI.

### Phase 5 — Computer Help foundation — **Partial; task assistance deferred**

- [x] Add captured safe diagnostic snapshots/private incident records and a
  separately qualified command-only Podman read broker.
- [x] Keep project and Personal context absent unless explicitly admitted.
- [ ] After C1/C2, add owner-selected task roots, scratch execution, filtered
  egress, reversible transaction journals, and bounded Approve-for-me mode.

**Checkpoint G3:** stop before any real file write, unrestricted shell,
package/service change, privilege escalation, or host repair. Decide separately
whether those experiments require a snapshot-capable Fedora VM.

### Phase 6 — Normal-use stabilization — **Active**

- [ ] Use G1.5 for real project conversations and record Helpful, Wrong, and
  Unsafe feedback with short reproducible notes.
- [ ] Exercise Qwen/native comparison, edit/resend, Stop, reload/restart,
  project forks/deletion, model changes, and desktop/mobile navigation.
- [ ] Treat correctness, containment, data loss, credential exposure, orphaned
  workers, or broken native/Cookbook behavior as release blockers.
- [ ] Keep convenience and polish requests in this plan without enabling
  writable tools or importing `_HUGE` scope.

## Milestone outcomes

| Milestone | State | Outcome |
|---|---|---|
| Continuity storage/compiler | **Complete** | Stable bindings, retained raw history, artifact contracts/store, deterministic context |
| Semantic continuity derivation | **Partial** | Heuristic checkpoints/briefs are source-linked but not accepted/proposed-state truth; C0/C1 replace them |
| Qwen + ModelBridge | **Complete** | Provider-neutral credential boundary, disposable Serve lifecycle, read-only Bubblewrap containment |
| Protected Dust runtime proof | **Complete** | Cited conflict-aware answers, read-only integrity, fresh-worker raw-tail continuation, and scope isolation |
| Companion UI trial | **Complete** | Testable homes/projects, Qwen/native comparison, Process visibility, feedback and lifecycle controls |
| Normal-use stabilization | **Active** | Gather real failures and correct regressions without adding authority |
| AgentMemory integration | **Deferred until C3** | Requires owner-approved inventory plus scope conformance and migration design |
| Computer Help diagnostics | **Partial** | Observation, incident records, and broker complete; task assistance follows C1/C2 |

The ordered C0 → C4 → D3 roadmap above supersedes the former choice among
unsequenced next milestones.

> [!QUESTION]
> In a project home, should selecting **Chat** while Qwen is enabled still use
> Qwen's read/search tools, or should **Chat** always force a tool-free native
> comparison turn? G1.5 currently treats Chat / Agent and the Qwen toggle as
> separate controls.

> [!QUESTION]
> Should a later project-Qwen milestone receive a separately brokered web-search
> capability, or remain workspace-only? G1.5 denies Qwen `WebSearch` and
> `WebFetch` even when the ordinary Odysseus web-search control is visible.

## Acceptance tests

- [x] Compaction leaves raw database row IDs, count, order, and content unchanged.
- [x] Repeating compaction at the same cursor/hash is idempotent.
- [x] A summarizer failure loses no data.
- [x] Tool calls and results remain atomic across checkpoint boundaries.
- [x] Reopening Personal, Computer, or a project returns its primary chat; an
  explicit fork remains a separate session.
- [x] The compiler can continue a primary chat from a seeded checkpoint and
  recent tail without rewriting raw history.
- [ ] An automatically derived primary checkpoint must continue a fresh worker
  after the raw tail no longer contains its source conversation.
- [x] A deliberately created Dust fork can share a seeded active
  `ProjectBriefV1` without receiving the primary chat's raw tail or
  `ThreadCheckpointV1`.
- [ ] A project fork must receive an automatically derived `ProjectBriefV1`,
  not merely a seeded fixture artifact.
- [x] The Personal Advisor “why is lemon acidic?” turn receives no Dust artifact,
  transcript, episodic hit, or worker state.
- [x] The Computer Help chat receives no project or Personal Advisor context in
  G1.5.
- [x] A browser-wide workspace selection cannot silently change a bound session.
- [x] An explicitly supplied directly linked project ID contributes a separately
  labeled brief; unlinked and transitive projects contribute nothing.
- [ ] Resolve a user-entered project name or `@project` reference to an owned,
  directly linked project ID before passing it to the compiler.
- [ ] Enforce explicit item/token caps before more than one related project can
  be selected through the UI.
- [x] A related-project bundle contains no raw chat, personal memory,
  credentials, tool transcript, or Qwen session state.
- [ ] AgentMemory mismatched and unscoped seeded hits are discarded.
- [ ] AgentMemory downtime still leaves exact checkpoint continuity working.
- [x] Native and Qwen harnesses compile the same semantic context inputs.
- [x] The disposable Qwen runtime receives only its ephemeral bridge token; its
  configuration, environment, process arguments, events, and logs contain no
  upstream URL or reusable provider credential, and runtime state is removed
  after the live test.
- [x] Missing or unsupported Qwen produces a clear capability downgrade and the
  native harness remains usable.
- [x] Qwen always requests a distinct thread rather than attaching unrelated
  Odysseus conversations to one daemon session.
- [x] Prompt admission is not mistaken for turn completion.
- [x] Fragmented/unknown/replayed SSE events do not corrupt the transcript.
- [x] Cancellation and worker death terminate the Odysseus run cleanly.
- [x] Read-only safe/Plan mode produces no Dust file, status, or hash changes.
- [x] The final-crystal answer cites both passages and labels the conflict.
- [x] The fish-church answer abstains rather than inventing missing lore.
- [x] Disabling the feature flag restores the existing native path.

G1.5 adds these accepted UI/runtime checks:

- [x] Repeated or concurrent home opens resolve to one deterministic primary.
- [x] A project restores its server-owned workspace, endpoint, model, harness,
  and primary/fork identity across reload and restart.
- [x] Qwen streams only sanitized Process events and persists the collapsed
  readable trace with the assistant message.
- [x] Inline edit/resend truncates the old suffix and starts exactly one
  replacement Qwen turn.
- [x] Stop, disconnect, timeout, worker death, and provider failure tear down the
  admitted worker and verify workspace integrity.
- [x] Native project comparison sends `allow_bash=false`, does not call
  `/api/shell/*`, and cannot recover bash state from browser storage.
- [x] Helpful/Wrong/Unsafe feedback and notes persist and export without the
  project corpus.
- [x] Form controls have unique IDs, accessible labels, and working desktop and
  mobile navigation.
- [x] Cookbook and document execution retain their shared shell APIs.

## Autonomous goal checkpoints and authority

The G1 runtime/safety and G1.5 UI checkpoints are completed. The historical
G1A1–G1E execution sequence remains in `ODYSSEUS_G1_EXECUTION_PLAN.md` as
implementation evidence, not as an active to-do list. Semantic continuity
closure is the active follow-up recorded in Phase 2B.

Once the owner explicitly starts a new contained goal, agents may inspect/edit
this repository, create local feature branches/worktrees, run tests and local
fake services, and study already approved references as normal implementation
work. They must preserve owner data and external projects, keep the stated
authority boundary, commit coherent reviewed changes, and report evidence at
the next named product decision.

The larger product decision gates remain:

1. **G1 — semantic/Qwen decision: Complete.** Qwen was retained and exposed in
   G1.5.
2. **G2 — real memory mutation:** before changing or migrating the owner's
   AgentMemory/native memory data.
3. **G3 — computer mutation:** before any write/shell/privileged host capability.

Dependency installation, live provider use, external pushes/PRs, and other
actions requiring new authority remain separate approvals; a goal does not grant
them implicitly. Provider credentials remain durably stored and resolved only
inside Odysseus. Qwen receives a short-lived route-scoped bridge token.

Suggested stabilization objective:

> Exercise and harden the committed G1.5 Companion homes and read-only Qwen UI.
> Reproduce and fix correctness, persistence, lifecycle, accessibility, and
> containment regressions. Preserve all raw messages, owner data, external
> projects, native chat, Cookbook, and document execution. Do not migrate real
> memory, enable writable Qwen tools, grant host shell authority, or begin the
> `_HUGE` plan.

## Explicitly deferred

- automatic Project/Personal/Computer classification;
- project claim graphs and automatic canon extraction;
- a separate search/index service or vector database replacement;
- a normalized/transitive project graph, automatic cross-project retrieval, and
  arbitrary multi-project synthesis;
- separate durable decision/plan/question/review artifacts beyond the initial
  thread checkpoint and project brief;
- any explicit model KV-cache switching or cache-owned state;
- protocol-neutral conversion for every native provider and production-grade
  multi-tenant model brokerage;
- Qwen auto-memory, AgentMemory graph/mesh/decay, and memory federation;
- custom MCP gateway and generic worker ecosystem;
- custom Codex-derived sandbox implementation;
- direct model file writes, unrestricted shell, and OS repair;
- persistent evolving multi-personality groups;
- Android, Windows, and macOS support;
- browser automation of consumer AI products;
- production encryption/privacy/security hardening;
- full Antigravity-style artifact/review application;
- per-file or per-hunk patch selection, delete/rename/binary patches, automatic
  formatting/tests, and automatic Git branches or commits.

These ideas are not rejected. They are allowed back into the active plan only
when a measured MVP failure or accepted next milestone requires them.

## Remaining owner inputs

- [x] Decide that related-project access must be explicit; automatic relevance
  waits until isolation is proven. Owner-facing name/`@project` resolution is
  still deferred.
- [x] Keep provider selection neutral through an Odysseus-owned `ModelBridge`;
  never hand the reusable FAL credential to Qwen.
- [x] Approve and install data-local pinned
  `@qwen-code/qwen-code@0.21.3`; the launcher uses the ignored repository-local
  installation and never requires a global Qwen process.
- [x] Judge the initial Dust answers and retain Qwen for the G1.5 UI trial.
- [ ] At G2, approve an inventory-only read of the existing AgentMemory/native
  data before any migration or writer change.
- [ ] Answer the four `[!QUESTION]` callouts above before selecting the next
  capability milestone. They do not block ordinary G1.5 stabilization.
