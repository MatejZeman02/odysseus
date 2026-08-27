# Odysseus G2C Execution Plan — Scoped Chat Memory and Working Artifacts

**Status:** Active, partially implemented; semantic correctness and transfer
work are next

**Depends on:** the implemented Companion homes, non-destructive continuity
store/compiler, and reviewed project-patch transaction boundary.

**Parent plan:** [`ODYSSEUS_IMPLEMENTATION_PLAN.md`](ODYSSEUS_IMPLEMENTATION_PLAN.md)

## Product outcome

G2C gives every long-lived Companion home useful memory without creating one
undifferentiated memory pool.

- Every conversation keeps its own chat memory: raw history, a private thread
  checkpoint, and a recent raw tail.
- Every project keeps shared project memory that its primary chat and explicit
  project forks can use.
- Personal Advisor primarily receives its own chat history, personal home
  memory, approved personal episodic memories, and personal working artifacts.
- Personal Advisor may consult a project's memory only through a visible,
  owner-authorized context grant. It never silently searches every project.
- Both projects and Personal Advisor can keep user-visible Markdown working
  documents under a `.artifacts` namespace so long drafts, plans, TODO lists,
  and open questions do not have to be repeated in chat.

This is not a transcript-merging milestone. Conversations remain separate;
compact checkpoints, home briefs, and selected artifacts are mounted by
reference when authorized.

## Implemented foundation and correction

The branch already implements a substantial part of this plan:

- Personal and project homes compile private raw tails, scoped checkpoints,
  briefs, artifact metadata, and a small exact scope-filtered recall index.
- Personal-to-project grants are owner-scoped, time-limited, and never expose
  source transcripts, credentials, Qwen state, or arbitrary workspaces.
- Personal working artifacts are revisioned, recoverable, captured from long
  pastes, and opened through the established Documents editor; project artifact
  changes remain behind reviewed patches.
- Context manifests expose the mounted records and the local index is
  provider-independent, so retrieval outage does not erase raw history.

There is one material limitation: current automatic checkpoint derivation is a
local convenience heuristic. It selects recent user/assistant text and writes
it into fields such as objective and accepted decisions. It preserves source
hashes and does not rewrite raw history, but it is **not** a trustworthy
accepted/proposed-state extractor. Its output must remain labelled `heuristic`
until Stage C1 replaces it. It must not silently establish project canon,
personal facts, or cross-chat authority.

## Memory layers

G2C treats “memory” as five different things with different owners and rules:

| Layer | Example | Authority and lifetime |
|---|---|---|
| Raw chat history | The full Personal Advisor conversation | Existing Odysseus message database; retained until the owner edits or deletes it |
| Chat memory | Current objective, decisions, open questions, next actions | Session-private `ThreadCheckpointV1`; derived and source-linked |
| Home memory | Dust project state or Personal Advisor relationship/preferences summary | `ProjectBriefV1` for projects and a new `PersonalBriefV1` for Personal; compact, inspectable, and not a replacement for source material |
| Episodic retrieval | “We discussed this communication strategy before” | Scoped AgentMemory/native-provider index; rebuildable retrieval aid, never source truth |
| Working artifacts | `.artifacts/drafts/love-letter.md` | User-visible Markdown with revision history; durable working content, not injected in full on every turn |

One writer is assigned per layer. A conversation turn must not be independently
auto-captured by native memory and AgentMemory.

## Default context by home

### Project chat

Compile context in this order:

```text
stable companion and project policy
  -> this chat's checkpoint
  -> this project's shared brief
  -> relevant project `.artifacts` references/excerpts
  -> project-scoped episodic hits
  -> recent raw transcript tail
  -> current request
```

A project fork receives the project brief and authorized shared artifacts, but
not another project's memory or another thread's raw transcript/checkpoint.

### Personal Advisor

Compile context in this order:

```text
stable companion and personal policy
  -> this Personal chat's checkpoint
  -> PersonalBriefV1
  -> relevant personal `.artifacts` references/excerpts
  -> approved personal episodic hits
  -> explicitly granted project-memory mounts, clearly labeled
  -> recent Personal transcript tail
  -> current request
```

The default Personal prompt receives no project brief, project transcript,
project episodic hit, project working file, or project Qwen state. It may receive
a minimal owner-scoped project catalog containing project name and an optional
owner-authored one-line description so it knows which project it could ask to
consult. The catalog contains no project memory content.

## Permissioned Personal-to-project recall

Personal Advisor can ask for project context when it believes that context would
materially improve an answer. The request must name the project, explain why it
is relevant, and state what it wants to read.

Example:

```text
This sounds related to Dust. May I consult Dust's project brief and open
questions for this answer?
```

Approval creates a server-owned `ContextGrant` containing:

- owner, requesting Personal session, and source project;
- purpose and allowed information classes;
- creation, use, and expiry timestamps;
- default expiry after the current request;
- an audit reference included in the context manifest.

The first grant surface exposes only:

- the project's shared brief;
- selected `.artifacts` explicitly named by the request or approved by the
  owner;
- source references needed to understand those records.

It does not expose raw project transcripts, credentials, tool logs, patches,
Qwen state, or the whole project workspace. Repository inspection remains a
separate project capability.

If the owner directly asks Personal Advisor to “check the Dust project memory,”
that instruction counts as approval for that request; the assistant must not
ask the same question redundantly. Vague relevance inferred by the model does
not count as approval.

Denied, expired, missing, or mismatched grants produce no project context and
do not fall back to an unscoped search. Information learned through a temporary
grant is not automatically copied into Personal memory.

> [!question] Cross-project grant duration
> The safe default is one request. Should the UI also offer “allow for this
> Personal conversation” as an explicit secondary choice, or should every
> later consultation require a new request?

## `.artifacts` working documents

Working artifacts solve a different problem from episodic memory. They hold
substantial evolving content that should be referenced and revised rather than
repeated in every prompt.

Examples:

```text
.artifacts/
  todo.md
  open-questions.md
  plans/
    release-plan.md
  drafts/
    love-letter.md
  notes/
    decision-context.md
```

The directory is a namespace, not a mandatory template. Files are created only
when useful.

Required behavior:

- Artifacts are ordinary UTF-8 Markdown with stable scope-relative paths,
  content hashes, revisions, timestamps, and source-message references.
- The UI provides an Artifacts view for the active Personal or project home.
- Context normally includes artifact identity, summary, revision, and relevant
  excerpts. Exact content is read on demand; a long love-letter draft is not
  copied into every model request.
- The assistant can say “continue the love-letter draft” and resolve the
  referenced artifact without the user pasting it again.
- Personal Advisor may create and revise files only inside its Personal
  `.artifacts` root through narrow server-owned artifact operations. It receives
  no general filesystem or shell authority.
- Project Qwen remains physically read-only. Project artifact changes use the
  existing reviewed/verified patch transaction boundary rather than granting
  Qwen direct writes.
- Every write is atomic and revisioned. Previous content remains available for
  diff and Undo. Deletion is explicit and recoverable.
- Artifact text is not automatically promoted into personal facts, accepted
  project decisions, or AgentMemory records.
- Secret scanning rejects credentials, private keys, and authentication
  material before an artifact becomes retrievable memory.

> [!question] Personal artifact location
> Recommended default: an Odysseus-owned private Personal workspace with an
> optional owner-selected Markdown/Obsidian folder later. Do you instead want
> to choose the Personal `.artifacts` folder during G2C setup so drafts are
> immediately visible in your normal file editor?

## Contracts and persistence

Reuse the existing raw message, session scope, `ThreadCheckpointV1`,
`ProjectBriefV1`, `ContinuityArtifact`, and context-manifest foundations.

Add versioned contracts for:

- `PersonalBriefV1`: approved preferences, ongoing goals, commitments,
  recurring themes, open questions, artifact references, and provenance;
- `ContextGrantV1`: explicit cross-scope authorization and expiry;
- `WorkingArtifactV1`: scope, relative path, title, summary, revision, hash,
  sensitivity, source messages, and backing-store reference;
- provider-neutral scoped memory records carrying owner, home kind, project ID
  where applicable, session provenance, sensitivity, retention, and expiry.

Do not mutate existing V1 payload meanings in place. Add compatible versions or
new kinds and retain readers for existing records.

## AgentMemory and native memory

AgentMemory is a retrieval engine, not the owner of conversations, project
truth, or working documents.

Implementation sequence:

1. Inventory and back up existing `memory.json`, vector-memory, and any local
   AgentMemory data before changing the active writer.
2. Extend the provider-neutral memory interface with first-class scope filters;
   an opaque owner plus optional session metadata is not sufficient.
3. Add conformance tests proving exact owner/home/project filtering and
   post-verification of every returned hit.
4. Index only validated checkpoints, home briefs, approved memory proposals,
   and selected artifact metadata/excerpts initially.
5. Assign one writer per memory category and keep provider outage non-fatal.
6. Preserve native memory for migration, export, deletion, and degraded-mode
   compatibility until AgentMemory passes the acceptance suite.

Do not enable Qwen auto-memory or AgentMemory's unscoped free-form `remember`
path. The context compiler remains the only component that admits recalled
records into a model prompt.

## Conversation attachment, not transcript merging

G2C may add a read-only context mount from one conversation checkpoint to
another. The destination stores a reference to the immutable checkpoint and
shows its source. It does not copy or interleave the source transcript.

Separate actions remain separate:

- attach a checkpoint as read-only context;
- promote an approved item into project or personal home memory;
- reference or copy a working artifact;
- create a synthesis conversation using two attributed checkpoints.

Actual project-state branch merging remains outside G2C.

## UI

- Show the active memory scope near the composer.
- Add a compact Context disclosure listing checkpoint, home brief, episodic
  hits, artifact revisions, and temporary project grants used for the turn.
- Provide inspect/edit/pin/forget controls for memories.
- Provide browse/open/diff/history/Undo controls for `.artifacts`.
- Show project-access requests as explicit cards with Allow once and Deny.
- Never display absolute host paths, provider internals, embeddings, or raw
  hidden retrieval logs.

## Ordered implementation stages

### C0 — Reconcile existing derived records

Add a derivation status/version (`heuristic`, `proposed`, `accepted`, or
`rejected`) without mutating payload meaning or raw chats. Mark existing local
records `heuristic`, retain their provenance, and make the disclosure explain
that they are a convenience summary rather than accepted truth. Verify this
migration is additive, restart-safe, owner-isolated, and reversible by simply
ignoring the derived layer.

### C1 — Validated semantic checkpoint proposals

Replace the heuristic writer with a bounded, schema-validated derivation
request. It must emit separate fields for facts, owner-accepted decisions,
proposals, failed approaches, open questions, actions, and artifact references.
The derivation route inherits no web, shell, patch, or cross-project authority.
Invalid JSON, cancellation, provider failure, stale source spans, and concurrent
writes produce no accepted record and never alter raw messages.

An owner approval/promotion action is required before a proposed item becomes
shared `ProjectBriefV1` or `PersonalBriefV1` state. Automatic home briefs may
contain only clearly labelled derived/provisional material until promoted.

### C2 — Prove durable continuation

Prove automatic checkpoints survive restart and that a fresh primary worker and
project fork receive the automatically produced home brief—not an old raw tail.
Add explicit revision/source-span manifests and regression fixtures for invented
canon, contradictory proposals, deleted source messages, and source-hash
conflicts.

### C3 — Transfer and synthesis

Implement the user-facing alternative to transcript merging:

- attach an immutable source checkpoint to a destination chat as visible,
  read-only context;
- selectively promote chosen checkpoint entries to a Personal or project home;
- create a new synthesis chat from two attributed checkpoints;
- permit project-to-project use only as a labeled read-only mount.

The destination never receives hidden transcript rows. Expiry, detachment,
ownership, sensitivity, provenance, and context-budget limits are enforced by
the compiler.

### C4 — Provider adoption

Before changing any real writer, inventory and back up existing `memory.json`,
vector, and AgentMemory data with owner approval. Add provider conformance tests
for exact owner/home/project/sensitivity/grant filtering, export, deletion,
deduplication, outage, and rollback. Only approved semantic artifacts and
selected working-artifact metadata may be indexed. The local exact index remains
the non-fatal fallback; AgentMemory never becomes project authority or receives
raw transcript/auto-memory writes.

### C5 — Long-history and scope UX

Paginate/virtualize history, expose bounded `@project` and related-project
selection, and show a compact context inspector covering checkpoint state,
brief, mounts, episodic hits, artifacts, and grants. Add native question-callout
rendering/export only if ordinary projects use it.

## Acceptance tests

- Existing heuristic records are labelled and cannot be selected as accepted
  project or Personal state without owner promotion.
- A long Personal conversation survives restart and compaction from its own
  automatically derived checkpoint plus recent raw tail.
- A project primary and fork share the project brief and shared artifacts while
  retaining separate chat checkpoints and transcript tails.
- Personal Advisor receives no project memory before a grant.
- Personal Advisor can ask to consult Dust, denial exposes nothing, and a
  one-request approval admits only the labeled Dust brief/selected artifacts.
- An explicit owner request to consult Dust does not trigger a redundant second
  permission prompt.
- Project material obtained through a temporary grant is absent from later
  Personal turns after expiry unless deliberately promoted.
- Personal Advisor creates `.artifacts/drafts/love-letter.md`, revises it over
  multiple turns, and continues by reference without reinjecting the complete
  letter on every request.
- A project TODO, plan, and open-question artifact remain readable across
  project chats and survive server restart.
- Artifact history and Undo restore exact prior bytes without overwriting a
  concurrent owner edit.
- AgentMemory/native-provider hits are rejected when owner, home, project,
  sensitivity, or grant scope does not match.
- Deleting a memory or artifact removes its retrieval records and preserves the
  promised recoverability/audit behavior.
- AgentMemory or vector-store outage leaves exact chat checkpoints, home briefs,
  raw history, and artifact access working.
- A source checkpoint can be attached to another chat, detached again, and
  audited without copying or exposing its raw transcript.
- A synthesis chat records both source checkpoint IDs and keeps disputed claims
  attributed rather than collapsing them into a single fact.

## Deferred

- automatic access to all project memories from Personal Advisor;
- raw transcript merging or silent cross-home promotion;
- unrestricted Personal filesystem access or shell;
- Qwen auto-memory and direct Qwen writes;
- background reflection, autonomous TODO execution, reminders, or engagement
  optimization;
- Computer Help memory, diagnostics, and user-level sandboxed assistance,
  which remain G2D; see
  [`ODYSSEUS_G2D_EXECUTION_PLAN.md`](ODYSSEUS_G2D_EXECUTION_PLAN.md);
- claim graphs, transitive project graphs, and the larger `_HUGE` architecture.
