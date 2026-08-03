# Odysseus Lean Continuity MVP Product Specification

**Status:** Active contract for G1 implementation

**Date:** 2026-08-03

**Implementation plan:**
[`ODYSSEUS_IMPLEMENTATION_PLAN.md`](ODYSSEUS_IMPLEMENTATION_PLAN.md)

**G1 execution plan:**
[`ODYSSEUS_G1_EXECUTION_PLAN.md`](ODYSSEUS_G1_EXECUTION_PLAN.md)

**Long-horizon product specification:**
[`ODYSSEUS_PRODUCT_SPEC_HUGE.md`](ODYSSEUS_PRODUCT_SPEC_HUGE.md)

This document defines the deliberately small product being implemented now. The
long-horizon specification remains the direction for a trustworthy public
release, but its claim graph, computer mutation, advanced Personal Advisor,
multi-personality, and production-hardening requirements are not G1 scope.

## 1. Product outcome

Odysseus should feel like one familiar companion without turning every subject
into one enormous conversation or one shared memory pool.

The default layout is:

- one ongoing Personal Advisor chat for general and personal questions;
- one ongoing Computer Help chat;
- one primary chat for each user-created project;
- optional explicit project forks when a second thread is genuinely useful.

The G1 product proves that a long-running project chat can be compacted,
restarted, and explicitly forked without losing its durable working context,
copying another thread's raw transcript, or leaking project context into the
Personal Advisor or Computer Help chats.

## 2. G1 demonstration

The first vertical slice uses the external Dust Markdown repository in strictly
read-only mode:

```text
open the primary Dust conversation
  -> ask whether destroying the final crystal ends immortality
  -> compile a bounded, inspectable context bundle
  -> delegate read-only search and file reading to pinned Qwen Serve
  -> answer with exact source references and expose the unresolved conflict
  -> retain every raw Odysseus message
  -> write a derived thread checkpoint and a derived Dust project brief
  -> restart the disposable Qwen session
  -> continue the primary chat from its checkpoint and recent raw tail
  -> open an explicit Dust fork from the shared project brief, not the old chat
  -> ask "why is lemon acidic?" in Personal Advisor with no Dust context
```

The demonstration is headless/API-level. GUI polish is not a G1 prerequisite.

## 3. Conversation and project boundaries

Every conversation has one stable, server-owned home binding:

- `project` with one project identifier;
- `personal`;
- `computer`;
- `general` for legacy compatibility.

Changing the browser's selected folder MUST NOT silently rebind an existing
conversation. Moving a conversation is an explicit, audited action.

A project is named by the user and contains user-approved workspace roots. G1
supports direct project relations only. Cross-project consultation requires
both:

1. an owner-configured direct relation; and
2. an explicit project name or `@project` reference in the request.

Only the related project's labeled, size-limited shared brief and source
references may be included. Its raw messages, thread checkpoint, personal
memory, credentials, tool history, and Qwen state MUST remain absent. Unlinked
and transitively related projects MUST remain absent.

## 4. Durable authority

G1 uses four clear authorities:

| Information | Authority | G1 rule |
|---|---|---|
| Project lore, rules, notes, code, and media | User files and Git | Source truth; read and cite, never silently rewrite |
| Raw conversation | Existing Odysseus message database | Immutable retained evidence |
| One thread's derived working state | `ThreadCheckpointV1` | Private to that thread and linked to source messages |
| Shared derived project notebook | `ProjectBriefV1` | Available to chats in that project; never silently canon |

Only two new continuity artifact payloads are allowed in G1:

```text
ThreadCheckpointV1
  schema_version, session_id, project_id?, objective,
  derived_working_state, accepted_decisions, proposals, open_questions,
  next_actions, artifact_refs, failures, source_message_ids,
  source_through_message_id, source_hash

ProjectBriefV1
  schema_version, project_id, summary, derived_working_state,
  accepted_decisions, proposals, open_questions, current_plans,
  source_refs, source_session_ids, source_revision?, updated_at
```

Both are versioned, validated, derived, and source-linked. A generated brief is
not automatically an accepted project fact. Files remain canonical.

## 5. Non-destructive compaction

Compaction MUST NOT delete, replace, reorder, or rewrite persisted raw messages.
It creates a new checkpoint from messages after the prior checkpoint cursor.

The compactor MUST:

- keep tool-call/result groups atomic;
- validate a checkpoint before storing it;
- store stable source message identifiers and a content hash;
- be idempotent at the same cursor and hash;
- lose no data when summarization fails;
- compile the next model prompt from the checkpoint plus a recent raw tail;
- project only source-linked, shareable state into the project brief.

Existing historical compaction summaries remain valid historical messages. G1
does not destructively migrate them.

## 6. Deterministic context compilation

Context is assembled in this order:

```text
companion personality and policy
  -> conversation and home-project header
  -> this thread's checkpoint
  -> home project's shared brief
  -> explicitly invoked direct-related project brief, separately labeled
  -> scope-verified episodic hits, when enabled later
  -> recent raw transcript tail
  -> current request
```

The compiler emits a manifest suitable for tests and debugging. Provider prompt
or KV caches are disposable performance details and never continuity authority.

## 7. Qwen worker boundary

Qwen Code supplies an optional mature agent loop, planning, file search/read
tools, sessions, and temporary in-worker compaction. Odysseus owns project
scope, durable artifacts, provider selection, credentials, and audit policy.

G1 MUST:

- use the exact approved data-local Qwen npm version; no Git submodule, `latest`,
  `npx`, global install, or vendored Qwen source;
- bind Qwen Serve to loopback with a random daemon bearer token;
- use a private, disposable Qwen home and an allowlisted environment;
- register only the exact approved read-only workspace;
- disable shell, file writes, ambient MCP servers, Qwen auto-memory, and web UI;
- start in Plan/read-only mode and fail closed when capabilities do not match;
- treat Qwen sessions and transcripts as disposable execution state.

Missing, incompatible, or failed Qwen MUST degrade clearly to the existing
native path. A fresh Qwen session must work from an Odysseus context bundle,
not hidden worker history.

## 8. Provider-neutral model access

Qwen MUST NOT receive a reusable upstream provider credential, upstream URL, or
owner endpoint configuration.

For G1, Odysseus provides a minimal run-scoped `ModelBridge`:

```text
Qwen OpenAI-compatible client
  -> loopback-only /v1/chat/completions
  -> short-lived bearer bound to one owner, run, endpoint id, and model id
  -> owner-scoped Odysseus endpoint resolution and auth construction
  -> configured OpenAI-chat-compatible upstream
```

The bridge is provider-neutral at the Odysseus configuration boundary: the
selected endpoint and model come from the existing `ModelEndpoint` system, and
FAL/DeepSeek is only the first canary configuration. G1 does not promise
protocol conversion for every native provider. It may fail closed for an
endpoint that cannot support the required OpenAI chat/tool protocol.

The bridge MUST:

- bind only to loopback and never join the ordinary public application router;
- use a constant-time token check and a distinct, expiring token per worker;
- expose one fixed model route and reject owner, endpoint, model, or run mismatch;
- enforce request-size, concurrency, count, and wall-time limits;
- propagate cancellation and client disconnects;
- construct upstream authentication only inside Odysseus;
- never forward arbitrary inbound authentication or host headers;
- redact URLs and secrets and avoid logging prompt bodies;
- leave no reusable credential in Qwen settings, environment, events, or logs.

## 9. Dust fixture and answer contract

Dust remains an external, user-owned repository and is never copied into this
repository. Deterministic tests use a protected checkout of its committed HEAD;
the owner evaluation reads the current working tree without mutation. Every run
records HEAD, status, hashes, and whether cited files are committed, modified,
or untracked, then verifies they are unchanged afterward. Dust's mutating sorter
must never run.

The provisional authority order is:

1. detailed owner-authored notes;
2. explicitly AI-written summaries;
3. appended or unsorted ideas as proposals;
4. red TODO/question material as unresolved;
5. deleted or explicitly rejected ideas as history.

Answers distinguish:

- **Established** — stated by the applicable authoritative source;
- **Inferred** — derived from evidence but not stated directly;
- **Unresolved** — incomplete or conflicting evidence;
- **Proposed** — new advice or an idea to consider.

Contradictions are relationships between statements, not a replacement for
those labels. Portable questions use native Obsidian callouts:

```markdown
> [!question] Does destroying the final crystal end immortality?
> The detailed story and a later unresolved proposal disagree.
```

## 10. G1 acceptance

G1 is accepted only when automated tests and the headless demonstration prove:

- compaction preserves raw message IDs, order, count, and content;
- repeated compaction at the same source cursor/hash is idempotent;
- a primary project chat resumes from its checkpoint and recent tail;
- an explicit project fork receives the project brief but not another thread's
  checkpoint or raw tail;
- Personal Advisor and Computer Help receive no Dust context by default;
- explicit direct-related project lookup is labeled and bounded;
- unlinked and transitive project context remains absent;
- the same semantic context can route to native or Qwen harnesses;
- Qwen reconnect, cancellation, replay, unknown events, and worker death fail
  without corrupting the Odysseus transcript;
- the Qwen runtime contains no upstream URL or reusable provider credential;
- the read-only run leaves Dust byte-for-byte and Git-status unchanged;
- the final-crystal answer cites both relevant sources and labels the conflict;
- the fish-church question abstains when the notes contain insufficient lore;
- disabling the feature flag restores the existing native behavior.

The owner then reviews several Dust answers and decides whether Qwen contributes
enough value to continue beyond G1.

## 11. Explicit non-goals

G1 does not implement:

- automatic Project/Personal/Computer classification;
- automatic cross-project relevance or a transitive project graph;
- claim graphs, automatic canon extraction, or a separate project indexer;
- AgentMemory migration or changes to the owner's real memory data;
- extra durable artifact types beyond checkpoint and project brief;
- Qwen auto-memory or a generic worker/MCP ecosystem;
- file writes, unrestricted shell, package/service changes, or computer repair;
- a custom sandbox or snapshot/rollback system;
- advanced Personal Advisor or mental-health safety behavior;
- persistent additional personalities or group conversations;
- Android, Windows, or macOS support;
- production privacy/security hardening or a complete review GUI.

## 12. Definition of done

The lean continuity MVP is done at G1 when the tested headless Dust flow works
through the provider-neutral bridge, raw histories remain intact, thread and
project scopes remain isolated, all involved repositories are proven unchanged,
and an owner-readable report records evidence, failures, and limitations.

**In one sentence:** Odysseus G1 is a non-destructive, scoped continuity layer
that lets an optional read-only Qwen worker reason over a user project without
owning its memory, credentials, or source truth.
