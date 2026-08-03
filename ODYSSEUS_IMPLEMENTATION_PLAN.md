# Odysseus Lean MVP Implementation Plan

**Status:** Active; G1 direction approved, checkpoint plan ready

**Date:** 2026-08-03

**Product specification:** [`ODYSSEUS_PRODUCT_SPEC.md`](ODYSSEUS_PRODUCT_SPEC.md)

**G1 execution plan:**
[`ODYSSEUS_G1_EXECUTION_PLAN.md`](ODYSSEUS_G1_EXECUTION_PLAN.md)

**Deferred architecture backlog:**
[`ODYSSEUS_ARCHITECTURE_BACKLOG.md`](ODYSSEUS_ARCHITECTURE_BACKLOG.md)

**Foundation branch:** `feat/brain-extension-foundation`

**G1 target branch:** `feat/companion-continuity-mvp`

## Goal

Build the smallest useful version of the expanded Odysseus:

> One familiar companion works through a few long-lived home conversations: one
> Personal Advisor chat, one Computer Help chat, and one primary chat for each
> project. Projects keep shared, source-linked notebooks, while unrelated
> discussions remain clean. Qwen Code can be delegated project work without
> becoming the durable memory system.

The first demonstrable flow is intentionally narrow:

```text
open a conversation whose home project is Dust
  -> ask whether destroying the final crystal ends immortality
  -> Odysseus compiles a small scoped context packet
  -> a pinned Qwen Serve worker searches and reads Dust in read-only Plan mode
  -> the answer cites exact files and shows the unresolved contradiction
  -> Odysseus retains the raw transcript and updates two derived JSON artifacts:
       this thread's checkpoint and Dust's shared project brief
  -> the primary Dust chat continues from its checkpoint and recent raw tail
  -> an explicit Dust fork receives the shared brief, not the old transcript
  -> the ongoing Personal Advisor chat can answer "why is lemon acidic?" and
     receives no Dust context
  -> restarting Qwen does not destroy the durable project understanding
```

This is the active implementation plan. Automatic project classification,
transitive project graphs, the larger claim graph, custom sandbox, group
personalities, multiple platforms, and production safety architecture are
parked in the long-horizon backlog until this flow proves useful.

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

## What already exists

Odysseus already has more useful infrastructure than the long plan assumed:

- a mature chat route and existing native agent loop;
- persistent sessions and messages;
- SSE streaming and detached agent runs;
- personality/preset injection;
- workspace selection and tool policy;
- a provider-neutral memory interface, although normal recall still bypasses it;
- a tested context compactor, although its persistence behavior must be fixed;
- extensive chat, memory, compaction, security, and tool regression tests.

Qwen Serve already provides:

- persistent worker sessions and resume/load;
- HTTP prompt admission and SSE event streaming;
- search/read/edit/shell tools;
- plans, permission requests, cancellation, and tool events;
- MCP support, worktrees, context files, and in-session compaction;
- reconnect/replay behavior and capability negotiation.

The Qwen Python SDK is a subprocess/JSON-lines client, not a client for
`qwen serve`. The MVP therefore needs a narrow Python HTTP/SSE adapter, not a
ported agent framework.

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

Only four new concepts are required for the first milestone:

1. `ScopeResolver`
2. `ArtifactStore`
3. `CheckpointCompactor`
4. `ContextCompiler`

The Qwen adapter is a harness integration around those concepts, not a fifth
state or memory system.

### ScopeResolver

Start with deterministic rules:

1. An existing conversation's server-owned home binding always wins.
2. A new conversation opened from a project is bound to that project.
3. The UI reopens or creates the owner's primary Personal Advisor and Computer
   Help chats for those destinations.
4. Legacy/unclassified conversations retain a `general` compatibility scope.
5. Moving an existing conversation is an explicit user action and is audited;
   changing the browser's currently selected folder cannot silently rebind it.
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

Implement only `ThreadCheckpointV1` and `ProjectBriefV1` initially. Decisions,
plans, questions, reviews, and worker outputs can become separate artifact kinds
later only when the two-record model proves insufficient.

Both records are derived, versioned, and source-linked; neither silently becomes
project canon. Files remain the source truth. A thread checkpoint continues one
conversation. A project brief is the compact shared notebook used by every
conversation bound to that project.

### Conversation, project, and related-project boundaries

- [ ] Never inject another conversation's raw transcript or private tail.
- [ ] A fresh project conversation loads the active `ProjectBriefV1`, not an old
  thread's `ThreadCheckpointV1`.
- [ ] The Personal Advisor and Computer Help chats load no project material
  unless the user explicitly names or invokes a project.
- [ ] For the first MVP, cross-project access requires a direct project relation
  plus an explicit project name or `@project` reference in the request.
- [ ] Include only a labeled related `ProjectBriefV1` and source references in
  the initial cross-project bundle. Fetch exact cited file excerpts on demand in
  a later increment.
- [ ] Exclude related projects' raw chats, personal memories, credentials,
  shell/tool transcripts, Qwen state, unlinked projects, and transitive links.
- [ ] Keep related-project context ephemeral, separately labeled, and subject to
  strict item/token limits; never merge it into the primary project's brief.

All approved memories remain discoverable through scoped retrieval. “Available”
does not mean “inserted into every prompt.” That distinction provides continuity
without recreating the long, mixed web-chat problem.

Default owner-facing layout:

| Destination | Default conversation behavior | Shared durable context |
|---|---|---|
| Personal Advisor | Reopen one ongoing chat for quick general and personal questions | Companion profile plus scoped personal recall |
| Computer Help | Reopen one ongoing chat because only one repair/diagnosis is normally active | That thread's checkpoint; later a reviewed device profile |
| A project | Reopen one primary chat; allow a new thread/fork when useful | The project's `ProjectBriefV1` across all its chats |

These are navigation defaults, not uniqueness constraints. An intentional second
computer chat or project fork must remain possible.

### Non-destructive compaction

The current compactor eventually replaces persisted message rows with a summary.
That destroys the evidence needed for later re-compaction, auditing, forking, and
better future models.

The replacement behavior must become:

- [ ] Never delete or rewrite raw persisted chat messages during compaction.
- [ ] Summarize only messages after that thread's previous checkpoint cursor.
- [ ] Keep tool-call/result groups atomic.
- [ ] Validate `ThreadCheckpointV1` before committing it.
- [ ] Store stable source message IDs and a content hash.
- [ ] Compile the next prompt from the latest checkpoint plus a recent raw tail.
- [ ] On summarization failure, retain everything and trim only that model call.
- [ ] Project only source-linked, shareable state into `ProjectBriefV1`; keep
  conversation-local details in `ThreadCheckpointV1`.
- [ ] Permit a new chat bound to the same project to load its project brief
  without copying another chat's checkpoint or transcript.

The existing persisted compaction summaries remain historical messages. No
destructive migration is needed.

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
| Raw conversation | Odysseus message database | Immutable retained evidence |
| One thread's working state | Odysseus `ThreadCheckpointV1` | Local, derived, and source-linked |
| Shared cross-chat project notebook | Odysseus `ProjectBriefV1` | Compact derived artifact; not canon |
| Companion identity and approved global profile | Existing personality/profile layer | Shared across chats; no project facts by default |
| Personal/episodic recall | AgentMemory adapter | Rebuildable recall index, not project truth |
| Legacy pinned/manual memories | Native `memory.json` during migration | Read/export/delete compatibility only after provider switch |
| Active worker transcript and compaction | Qwen session/JSONL | Disposable execution state |

One writer is assigned per memory class. Native memory and AgentMemory must not
both auto-capture the same turn.

### AgentMemory adapter

The existing `MemoryProviderRegistry` is initialized but normal prompt recall
still reads `memory.json` directly. The MVP must wire the provider abstraction
into context assembly before calling AgentMemory “integrated.”

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

## Qwen Serve adapter

Implement a narrow, capability-gated client rather than mirroring the complete
Qwen API.

Initial HTTP/SSE surface:

- `GET /health`
- `GET /capabilities`
- `POST /session` with `sessionScope: "thread"`
- `GET /session/:id/events` with `Last-Event-ID`
- `POST /session/:id/prompt`
- `POST /session/:id/cancel`
- `POST /session/:id/approval-mode` with non-persistent `plan`

Later, after the read-only milestone:

- `GET /session/:id/transcript`
- `POST /permission/:requestId`
- explicit resume/load and file-diff review

The adapter normalizes Qwen events into the existing Odysseus SSE vocabulary:
assistant/thought deltas, tool start/update/output, plan updates, questions,
permissions, completion, failure, cancellation, reconnect/resync, and worker
death. Unknown events are logged and ignored safely.

Process rules for the first real spike:

- [ ] Pin Qwen Code `0.21.3` at the reviewed revision
  `c0196b422665aa000e9643555331baff8aef29da` and verify capabilities at
  startup.
- [ ] Bind to `127.0.0.1` on an allocated port.
- [ ] Generate a random bearer token and require authentication on all routes.
- [ ] Disable the Qwen web UI.
- [ ] Register only the exact read-only Dust workspace.
- [ ] Use an isolated Qwen home/runtime directory and a scrubbed allowlisted
  environment.
- [ ] Disable Qwen managed auto-memory and unrelated ambient MCP servers.
- [ ] Start in Plan mode with write and shell tools unavailable.
- [ ] Treat Qwen sessions as disposable: a fresh session must work from the
  Odysseus `ContextBundle`, not hidden Qwen history.

### Local installation and provider-neutral model bridge

Qwen is an optional external runtime, not vendored application source:

1. Detect a user-provided `qwen` binary and reject unsupported versions.
2. Otherwise offer an explicit data-local install of the exact npm package
   `@qwen-code/qwen-code@0.21.3`, with a lockfile and no global install.
3. Allow an advanced custom binary path.

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

- [ ] Resolve the route by endpoint id and owner; do not use a silent fallback
  chain or accept endpoint/model choices from Qwen.
- [ ] Bind one expiring bridge token to one owner, run, endpoint, and model;
  enforce constant-time authentication plus body, request, concurrency, and
  wall-time limits.
- [ ] Construct outbound provider headers from scratch inside Odysseus and never
  forward Qwen authentication, host, or arbitrary custom headers.
- [ ] Support the OpenAI chat/tool protocol required by G1 and fail closed for a
  native provider protocol the bridge cannot represent honestly.
- [ ] Stream ordinary JSON/SSE, usage, tool calls, errors, and cancellation in
  the exact shape the pinned Qwen client consumes.
- [ ] Restrict the Qwen runtime directory to the owner, scrub all other
  credentials from its environment, and remove/revoke runtime state on teardown.
- [ ] Fake-test the complete Qwen -> bridge -> provider path before one approved
  live canary through the configured FAL endpoint.
- [ ] If `deepseek/deepseek-v4-flash` is inadequate, select another compatible
  configured endpoint/model without redesigning the harness.

The bridge is a credential-routing boundary, not an operating-system sandbox.
Qwen Serve documents that a same-UID worker can reach ambient files and
credentials. G1 disables shell/write tools, uses an allowlisted environment and
an exact read-only workspace, and must state this residual same-UID risk in the
report. Prefer rootless containment for the live canary if it can be added
without weakening loopback authentication; separate-identity containment is
required before untrusted workspaces, mutation, or distribution.

Qwen Serve is not a sandbox. A VM is unnecessary for this read-only milestone.
Rootless Podman is the preferred containment experiment for the canary and is
required before generic mutation; a snapshot-capable Fedora VM is needed only
for real systemd, package, codec, boot, or repair/rollback experiments.

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

- [ ] Does destroying the final crystal definitely end immortality?
- [ ] Did the mage rebellion or the king originate resurrection?
- [ ] What are the practices of the fish church?
- [ ] Are healing and speed-boost talismans accepted designs?
- [ ] What quests are currently described despite the case-colliding files?
- [ ] What changed from HEAD in the temple-library and unsorted ideas?

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

## Branches

Keep branch overhead small:

```text
upstream/dev
  -> feat/brain-extension-foundation        # current docs/baseline
      -> feat/companion-continuity-mvp       # one integration branch for the goal
          -> feat/artifact-continuity        # short-lived; only for parallel work
          -> feat/qwen-runtime-harness       # short-lived; only for parallel work
          -> feat/agentmemory-adapter        # begins after the real-data checkpoint
```

The goal may use short-lived child branches/worktrees for genuinely parallel
work, but the MVP should finish on `feat/companion-continuity-mvp`; the child
branches separate concurrent edits, not product modes or permanent architectures.
Do not push or open a PR without explicit user instruction. Generic fixes can
still be prepared separately from `upstream/dev`; the project/artifact/Qwen
integration remains a downstream feature. Regular upstream merges happen first
on a temporary sync branch and are tested before entering the integration branch.

## Test-first implementation phases

### Phase 0 — Baseline and contracts (0.5–1 agent day)

- [ ] Commit or otherwise freeze the approved specification and lean plan.
- [ ] Record current test baseline and focused chat/memory/compaction tests.
- [ ] Characterize FAL Key authentication, URL construction, key redaction,
  exact model selection, and the existing `kimicode` alias in regression tests.
- [ ] Add ADRs for conversation/project/artifact ownership, the Qwen boundary,
  and one-writer memory policy.
- [ ] Create fake Qwen/model-upstream fixtures, a two-project relation fixture,
  and the Dust evaluation manifest.

### Phase 1A — Non-destructive continuity kernel (2–3 agent days)

- [ ] Add `projects`, stable session scope/project fields, and
  `continuity_artifacts`, with typed repositories and migrations.
- [ ] Write failing preservation, idempotency, binding, artifact-isolation, and
  explicit related-project tests first.
- [ ] Implement `ScopeResolver`, `ArtifactStore`, `CheckpointCompactor`, and
  `ContextCompiler` with only `ThreadCheckpointV1` and `ProjectBriefV1`.
- [ ] Make session binding server-owned so a changed browser workspace cannot
  silently move an existing conversation.
- [ ] Reopen/create the owner's primary Personal and Computer chats and primary
  per-project chat deterministically; extra/forked project chats stay explicit.
- [ ] Route native chat context through the compiler behind a feature flag.
- [ ] Preserve the complete raw transcript and support rollback by disabling the
  flag.

### Phase 1B — ModelBridge and Qwen runtime harness (3–4 agent days, parallel with 1A)

- [ ] Add `src/harnesses/base.py`, `native.py`, `qwen_serve.py`, and `router.py`.
- [ ] Add a small Qwen lifecycle supervisor outside the existing giant agent
  loop.
- [ ] Implement binary discovery/version checks and a data-local pinned-install
  contract; do not perform a network install until approved.
- [ ] Implement the run-scoped `ModelRoute` and loopback `ModelBridge`, including
  owner-safe resolution, authentication translation, limits, redaction,
  streaming, cancellation, and teardown against a fake upstream.
- [ ] Generate private Qwen configuration containing only the bridge URL, fixed
  model, and ephemeral token env-key; prove the upstream URL/key never reaches
  Qwen configuration, environment, events, or logs.
- [ ] Implement the minimum capability/HTTP/SSE surface against a fake daemon.
- [ ] Map events into existing Odysseus streaming events.
- [ ] Prove cancellation, reconnect, prompt/completion correlation, capability
  downgrade, default denial, and process failure.

### Phase 2 — Headless Dust vertical slice (1–2 agent days)

- [ ] Route a scoped read-only project turn to the Qwen harness.
- [ ] Inject the same selected personality and the compact `ContextBundle`.
- [ ] Install the approved pinned Qwen runtime and run one approved live
  Qwen -> `ModelBridge` -> configured FAL endpoint canary with
  `deepseek/deepseek-v4-flash`.
- [ ] Run the golden Dust questions without changing its worktree.
- [ ] Persist a thread checkpoint and project brief; prove the primary Dust chat
  can continue compactly and an explicit test fork gets the brief but not the
  primary chat's raw tail or checkpoint.
- [ ] Prove the Personal Advisor and Computer Help contexts receive no Dust data.
- [ ] Prove an explicitly invoked, directly linked project brief is labeled and
  available while unlinked and transitive projects remain absent.
- [ ] Compare the native and Qwen answers for usefulness, evidence, latency, and
  failure behavior.

**Checkpoint G1:** show the owner the test report and several Dust answers. The
owner decides whether Qwen is useful enough to keep and corrects semantic errors.

### Phase 3 — AgentMemory provider switch (2–4 agent days)

- [ ] Implement the strictly scoped adapter and fake-server contract tests.
- [ ] Wire normal context recall through `MemoryProviderRegistry`.
- [ ] Index only new structured artifacts/tool outcomes initially, with separate
  Personal, Computer, and project namespaces.
- [ ] Provide inventory, backup, dry-run migration, rollback, and duplicate
  reports for existing native and AgentMemory data.

**Checkpoint G2:** obtain explicit approval before touching the owner's real
AgentMemory data, disabling an existing writer, or migrating native memories.

### Phase 4 — Thin GUI and long-history integration (3–5 agent days)

- [ ] Keep the current Chat / Agent control and folder chip.
- [ ] Add persistent Personal Advisor and Computer Help destinations plus one
  primary chat entry per project, without forbidding extra chats or forks.
- [ ] Paginate or virtualize long chat history: load the checkpoint and recent
  tail by default, while retaining older raw messages behind a history action.
- [ ] Show and edit the stable home binding and direct related-project allowlist.
- [ ] Show harness, workspace, read-only status, plan, tool activity, questions,
  citations, and failure/reconnect status using existing UI patterns.
- [ ] Add native `> [!question]` rendering/export.
- [ ] Add a small “context used” inspector only if debugging shows it is needed.

### Phase 5 — Read-only Fedora diagnosis (later, 2–4 agent days)

- [ ] Begin with captured `journalctl`/service/package fixtures.
- [ ] Add allowlisted read-only commands in rootless containment.
- [ ] Verify that project and personal context are absent from the worker packet
  unless explicitly relevant.
- [ ] Harden the G1 `ModelBridge` and worker containment for longer-running
  diagnostic tasks; retain per-worker tokens, one endpoint/model, bounded
  requests, transparent streaming, and redacted logs.

**Checkpoint G3:** stop before enabling any real file write, unrestricted shell,
package/service change, privilege escalation, or host repair. Decide then whether
to create a Fedora VM with snapshots.

## Effort and likely outcome

| Milestone | Agent effort | Likely result |
|---|---:|---|
| Fake-tested artifact continuity | 2–3 days | Stable bindings, raw transcripts preserved, thread checkpoints and shared project briefs |
| Fake-tested Qwen + ModelBridge | 3–4 days | Provider-neutral credential boundary, runtime lifecycle, Serve protocol, streaming, and cancellation |
| First read-only Dust proof | 1–2 days | Cited answer, compact primary-chat continuation, explicit fork isolation, and related-project context |
| **Useful API-level prototype** | **6–9 agent days, with 1A/1B parallel** | **Enough evidence for G1; no production write/shell** |
| Robust first integration including AgentMemory | 9–13 days | Scoped recall, migration plan, failure handling, regression coverage |
| Thin GUI exposure and long-history loading | +3–5 days | Default rooms, project bindings, worker inspection, and non-lagging history access |

The owner's twenty available hours are more than enough for decisions and answer
review during this first run; they are not expected to perform twenty hours of
manual testing. Agents can implement and test most backend work autonomously.

## Acceptance tests

- [ ] Compaction leaves raw database row IDs, count, order, and content unchanged.
- [ ] Repeating compaction at the same cursor/hash is idempotent.
- [ ] A summarizer failure loses no data.
- [ ] Tool calls and results remain atomic across checkpoint boundaries.
- [ ] Reopening Personal, Computer, or a project returns its primary chat; an
  explicit fork remains a separate session.
- [ ] The primary Dust chat continues from its own checkpoint and recent tail
  after compaction.
- [ ] A deliberately created Dust fork shares the active `ProjectBriefV1` but
  never the primary chat's raw tail or `ThreadCheckpointV1`.
- [ ] The Personal Advisor “why is lemon acidic?” turn receives no Dust artifact,
  transcript, episodic hit, or worker state.
- [ ] The Computer Help chat receives no project/personal context unless the user
  explicitly invokes it.
- [ ] A browser-wide workspace selection cannot silently change a bound session.
- [ ] A named directly linked project contributes only a separately labeled,
  capped brief; unlinked and transitive projects contribute nothing.
- [ ] A related-project bundle contains no raw chat, personal memory,
  credentials, tool transcript, or Qwen session state.
- [ ] AgentMemory mismatched and unscoped seeded hits are discarded.
- [ ] AgentMemory downtime still leaves exact checkpoint continuity working.
- [ ] Native and Qwen harnesses compile the same semantic context inputs.
- [ ] The disposable Qwen runtime receives only its ephemeral bridge token; its
  configuration, environment, process arguments, events, and logs contain no
  upstream URL or reusable provider credential, and runtime state is removed
  after the live test.
- [ ] Missing or unsupported Qwen produces a clear capability downgrade and the
  native harness remains usable.
- [ ] Qwen always requests a distinct thread rather than attaching unrelated
  Odysseus conversations to one daemon session.
- [ ] Prompt admission is not mistaken for turn completion.
- [ ] Fragmented/unknown/replayed SSE events do not corrupt the transcript.
- [ ] Cancellation and worker death terminate the Odysseus run cleanly.
- [ ] Read-only Plan mode produces no Dust file, status, or hash changes.
- [ ] The final-crystal answer cites both passages and labels the conflict.
- [ ] The fish-church answer abstains rather than inventing missing lore.
- [ ] Disabling the feature flag restores the existing native path.

## Autonomous goal checkpoints and authority

Once the owner explicitly starts a goal, agents can implement autonomously
inside each G1 sub-checkpoint. They may inspect/edit this repository, create
local feature branches/worktrees, run tests and local fake services, and study
the already cloned reference repositories as normal implementation work. The
agent commits, reports, and stops at every G1A1–G1E boundary defined in
`ODYSSEUS_G1_EXECUTION_PLAN.md`; the owner resumes it for the next increment.

The larger product decision gates remain:

1. **G1 — semantic/Qwen decision:** after the headless read-only Dust demo.
2. **G2 — real memory mutation:** before changing or migrating the owner's
   AgentMemory/native memory data.
3. **G3 — computer mutation:** before any write/shell/privileged host capability.

Dependency installation, live provider use, external pushes/PRs, and other
actions requiring new authority remain separate approvals; the goal itself does
not grant them. The configured FAL endpoint is the intended first live provider,
but its credential remains durably stored and resolved only inside Odysseus.
Qwen receives a short-lived route-scoped bridge token. If live access is
unavailable, complete the fake provider/adapter and continuity work, report the
live-test blocker at G1, and do not stop earlier.

Suggested goal objective:

> Implement the active lean MVP in `ODYSSEUS_IMPLEMENTATION_PLAN.md` through
> Phase 2, test it autonomously, preserve Dust and all existing user data, and
> stop at checkpoint G1 with a headless read-only Dust demonstration and full
> test report. Do not migrate real memory or enable write/shell capabilities.

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
- direct file writes, unrestricted shell, OS repair, and rollback automation;
- persistent evolving multi-personality groups;
- Android, Windows, and macOS support;
- browser automation of consumer AI products;
- production encryption/privacy/security hardening;
- full Antigravity-style artifact/review application.

These ideas are not rejected. They are allowed back into the active plan only
when a measured MVP failure or accepted next milestone requires them.

## Remaining owner inputs

- [x] Use an explicit project name or `@project` reference for the first directly
  related-project lookup; automatic relevance waits until isolation is proven.
- [x] Keep provider selection neutral through an Odysseus-owned `ModelBridge`;
  never hand the reusable FAL credential to Qwen.
- [ ] Approve the data-local installation of pinned
  `@qwen-code/qwen-code@0.21.3` when the live G1 test begins. Node 22, npm, and
  Podman are already present; `qwen` itself is not currently installed.
- [ ] At G1, judge several Dust answers for usefulness and correct the expected
  labels rather than manually testing the GUI.
- [ ] At G2, approve an inventory-only read of the existing AgentMemory/native
  data before any migration or writer change.
- [ ] After this intensive week, state whether future checkpoints can assume a
  few review hours per week or should be fully asynchronous.
