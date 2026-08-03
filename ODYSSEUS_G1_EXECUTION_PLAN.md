# Odysseus G1 Autonomous Execution Plan

**Status:** Ready for a persistent implementation-agent goal

**Date:** 2026-08-03

**Product contract:**
[`ODYSSEUS_PRODUCT_SPEC.md`](ODYSSEUS_PRODUCT_SPEC.md)

**Architecture and estimates:**
[`ODYSSEUS_IMPLEMENTATION_PLAN.md`](ODYSSEUS_IMPLEMENTATION_PLAN.md)

**Target branch:** `feat/companion-continuity-mvp`

## Goal objective

Use this objective when starting the persistent goal:

> Implement the active lean continuity MVP through G1. Preserve all raw
> Odysseus messages, all existing owner data, and the Dust repository. Build
> stable conversation/project bindings, `ThreadCheckpointV1`,
> `ProjectBriefV1`, deterministic context compilation, a provider-neutral
> run-scoped model bridge, and a fake-tested Qwen Serve harness. Finish with a
> headless read-only Dust demonstration and an evidence report. Commit and stop
> at every checkpoint below. Do not push, migrate real memory, expose reusable
> provider credentials, or enable file-write, shell, package, service,
> privilege, or host-repair capabilities.

The implementation agent may work autonomously inside each checkpoint. At the
checkpoint boundary it must run the listed gates, commit only that checkpoint's
coherent changes, report the evidence and remaining risk, and stop. The owner
resumes the goal for the next checkpoint.

## Authority and hard stops

The goal authorizes normal local implementation work in this repository:

- inspect and edit tracked project files;
- create tests, fixtures, migrations, ADRs, and local feature worktrees;
- run local tests and fake loopback services;
- inspect the already cloned, ignored reference repositories;
- read the approved Dust paths for fixture design without modifying them.

The goal does not itself authorize:

- installing or downloading Qwen or any other dependency;
- sending project content to a live provider;
- pushing branches, opening pull requests, or writing to upstream;
- reading, migrating, deleting, or changing the owner's real memory stores;
- enabling Qwen shell/file-write tools or any host mutation;
- changing Dust or running its sorter.

Stop and request the specific missing authority if an implementation step truly
requires one of those actions. Fake-tested work should continue as far as
possible before reporting a live-test blocker.

Immediately stop without committing generated damage if any of these occur:

- an existing owner-data or external-project file changes unexpectedly;
- a raw chat message is deleted, replaced, or reordered by a new path;
- a reusable provider key or upstream URL appears in Qwen configuration, its
  environment, emitted events, logs, test snapshots, or Git diff;
- a migration cannot be made additive and backward-compatible;
- read-only Qwen capability cannot be proven before the Dust run;
- the feature flag cannot restore the existing native behavior.

## Repository and commit rules

The canonical base is `upstream/dev`. The local `dev` branch is a pristine
upstream synchronization branch; it is never used for downstream commits.
`origin` is the user's fork and the default push target. No push occurs during
G1 without a separate instruction.

The prepared branch chain is:

```text
upstream/dev
  -> feat/brain-extension-foundation       # FAL commit + frozen planning
      -> feat/companion-continuity-mvp      # all G1 implementation
```

After G1A2, G1B and G1C may run in parallel on short-lived worktrees when that
materially reduces time:

```text
feat/companion-continuity-mvp
  |-> feat/artifact-continuity
  `-> feat/qwen-runtime-harness
```

Merge those branches only at G1D1. Do not create the long-horizon
`integration/odysseus-expansion` branch. Do not copy `.reference-repos/`, Dust,
runtime data, provider secrets, test databases, or the machine-local
`launch_odysseus.sh` into a commit.

Every checkpoint commit must:

1. contain one coherent behavior or contract;
2. use a Conventional Commit subject;
3. include tests or explicit characterization evidence for code behavior;
4. pass `git diff --check` and a secret/path scan;
5. leave a clean tracked worktree before the agent stops;
6. avoid amend/rebase of previously reviewed checkpoints unless the owner asks.

If pre-existing unrelated tests fail, record their exact identity and reproduce
them from the checkpoint base. Do not weaken, skip, or xfail tests to create a
green result.

## Dependency graph

```text
G1A1 -> G1A2 ->+-> G1B1 -> G1B2 ->+
               |                    |
               `-> G1C1 -> G1C2 ----+-> G1D1 -> G1D2 -> G1E
```

G1B and G1C deliberately touch separate packages until G1D1. Database/session
ownership remains with G1B. Worker lifecycle, model bridge, and Serve protocol
remain with G1C. Shared route or application-initializer edits wait for G1D1.

## G1A1 — Freeze contracts and record the baseline

**Outcome:** the active/deferred document boundary, repository state, and test
baseline are explicit enough that later changes can be measured.

Work:

- confirm the regular product spec and implementation plan are active and the
  `_HUGE` documents/backlog are deferred references;
- verify `dev` tracks cached `upstream/dev`, the feature branch contains the FAL
  prerequisite, and no downstream work exists on `dev`;
- record the exact upstream base and foundation commit in a small G1 baseline
  report;
- run the full feasible test suite once, plus focused chat, session, compaction,
  memory-provider, endpoint-resolver, and webhook tests;
- record command, Python/Node versions, pass/fail/skip counts, duration, and any
  reproducible pre-existing failures; do not commit raw voluminous logs;
- add ADRs for conversation/project/artifact ownership, the Qwen/ModelBridge
  boundary, and the one-writer memory rule;
- inventory the exact source modules expected to become narrow integration
  seams and name their rollback feature flags.

Required gates:

- Markdown links and fences validate;
- `git diff --check` passes;
- the baseline contains no secret values or private Dust content;
- focused current-behavior tests reproduce on the unchanged foundation.

Checkpoint commit:

```text
docs(g1): freeze continuity contracts and baseline
```

Stop after reporting the commit, test counts, existing failures, and any plan
correction required by observed code.

## G1A2 — Characterize provider seams and build deterministic fixtures

**Outcome:** known provider behavior and all non-live G1 fixtures are pinned
before persistence or worker code is written.

Work:

- add regression tests for FAL host detection, `Key` authorization, pasted key
  prefix normalization, URL construction, model selection, redaction, and
  hostname false positives;
- make the curated FAL provider classification internally consistent;
- restore the accidentally replaced `kimicode` webhook alias unless repository
  evidence proves removal intentional;
- add a fake OpenAI-compatible upstream supporting streaming text, tool calls,
  fragmented SSE, `[DONE]`, provider errors, slow responses, and disconnects;
- add a fake Qwen Serve daemon covering capabilities, session creation, event
  replay, prompt admission versus completion, cancellation, and worker death;
- add synthetic Personal, Computer, and two directly related project fixtures,
  including an unlinked and a transitively related project;
- add a synthetic Obsidian fixture for wikilinks, aliases, frontmatter, embeds,
  block references, and `> [!question]` callouts;
- add a Dust evaluation manifest containing paths, questions, expected evidence
  categories, Git/hash checks, and the prohibition on running its sorter. Keep
  Dust content itself outside this repository.

Required gates:

- endpoint/webhook regression tests pass;
- fixtures are deterministic, owner-isolated, network-free, and contain no
  reusable credential;
- fake services bind only to loopback/ephemeral ports;
- no test relies on the owner's real Odysseus database or memory files.

Checkpoint commit:

```text
test(g1): pin provider and worker boundary fixtures
```

Stop after reporting fixture coverage and any upstream-generic fixes that should
later be split into a clean branch from `dev`.

## G1B1 — Additive persistence and stable scope binding

**Outcome:** projects, server-owned conversation homes, and versioned artifacts
exist behind typed repositories without changing current chat behavior.

Work:

- add backward-compatible schema/migrations for `projects`, session scope and
  project binding, and `continuity_artifacts`;
- define typed `ResolvedScope`, `TurnContext`, `ThreadCheckpointV1`,
  `ProjectBriefV1`, and artifact metadata contracts;
- implement owner-scoped repositories with unique/index constraints,
  monotonic per-stream revisions, optimistic conflict handling, and explicit
  transaction boundaries;
- make primary Personal, Computer, and per-project chat selection deterministic
  and concurrency-safe while still allowing explicit extra chats/forks;
- implement `ScopeResolver`; a changed browser workspace must not rebind a
  session;
- implement direct related-project allowlists and explicit name/`@project`
  resolution without transitive expansion;
- keep all new reads/writes unreachable from normal chat until the feature flag
  is enabled later.

Required gates:

- fresh database, upgraded database, rollback-by-unused-columns, ownership,
  concurrency, and foreign-key/index tests pass;
- legacy sessions resolve to `general` without data loss;
- cross-owner project/artifact lookup fails closed;
- running old/native chat behavior creates no continuity artifact.

Checkpoint commit:

```text
feat(continuity): add scoped project artifact storage
```

Stop after reporting the migration matrix and schema rollback story.

## G1B2 — Non-destructive checkpoints and deterministic context

**Outcome:** continuity artifacts can replace destructive compaction as prompt
input while the complete raw transcript remains unchanged.

Work:

- implement `ArtifactStore`, `CheckpointCompactor`, and `ContextCompiler`;
- create validated artifacts in a transaction without calling
  `replace_messages()` or mutating raw messages;
- compact only source messages after the prior cursor and keep tool call/result
  groups atomic;
- make identical source cursor/hash requests idempotent and reject stale or
  conflicting writes predictably;
- project only source-linked, shareable state from a thread checkpoint into its
  home project's brief;
- compile the exact order in the active product spec with per-section budgets
  and a testable manifest;
- enable the native chat path through the compiler behind one default-off flag;
- make flag disablement restore the unchanged native path.

Required gates:

- snapshot message IDs, order, role, content, attachments, tool records, and
  count before/after successful, repeated, failed, and cancelled compaction;
- primary/fork/Personal/Computer/direct-related/unlinked/transitive isolation
  tests pass;
- summarizer failure and database conflict lose no raw or accepted artifact;
- relevant legacy compaction and session tests still pass.

Checkpoint commit:

```text
feat(continuity): preserve raw history with typed checkpoints
```

Stop after reporting byte/row preservation evidence and the feature-flag
rollback command.

## G1C1 — Provider-neutral run-scoped ModelBridge

**Outcome:** an external worker can call one selected model through Odysseus
without learning the configured upstream URL or reusable credential.

Work:

- implement a dedicated loopback-only bridge service, not a public app route;
- bind a short-lived random bearer to one owner, run, endpoint id, model id,
  expiry, request budget, and concurrency budget;
- expose only the minimum OpenAI-compatible surface needed by Qwen:
  `POST /v1/chat/completions`, plus tightly gated health/model discovery only if
  the pinned client requires them;
- resolve the endpoint owner-safely through existing endpoint resolver/runtime
  credential helpers and construct upstream auth inside Odysseus;
- support the OpenAI-chat-compatible endpoints required for G1 and fail closed
  for unsupported native protocols;
- translate or transparently relay streaming/non-streaming responses in the
  exact shape Qwen consumes, preserving tool calls, finish reasons, errors,
  usage, cancellation, and `[DONE]`;
- enforce constant-time auth checks, loopback peer validation, body/time/count
  limits, header allowlisting, redaction, and teardown;
- ensure inbound Qwen authorization, host, and arbitrary custom headers are
  never forwarded upstream.

Required gates:

- missing, wrong, expired, replayed/exhausted, cross-owner, cross-run,
  endpoint-disabled, and model-mismatch requests fail closed;
- fake Qwen-facing `Bearer <ephemeral>` becomes fake-upstream provider auth
  without exposing the upstream secret in either direction;
- fragmented stream, tool call, non-stream, error, timeout, cancellation, and
  client-disconnect tests pass;
- scans of process args, settings, environment, events, logs, and Git diff find
  neither the fake reusable key nor upstream URL on the Qwen side.

Checkpoint commit:

```text
feat(harness): add run-scoped model bridge
```

Stop after reporting the threat-boundary tests and supported protocol limits.

## G1C2 — Pinned Qwen Serve supervisor and adapter

**Outcome:** Odysseus can supervise and communicate with a fake or explicitly
installed supported Qwen runtime, with no durable state delegated to it.

Work:

- add the small harness base/native/Qwen/router modules and lifecycle supervisor;
- detect a user-provided Qwen binary, verify exact version/capabilities, and
  implement—but do not execute without approval—the data-local pinned npm
  installation contract;
- generate owner-only disposable Qwen settings containing only the loopback
  bridge URL, fixed bridge model id, and ephemeral token environment-key name;
- scrub the worker environment, isolate its home, disable web UI, auto-memory,
  ambient MCP, file writes, and shell, and register exactly one read-only root;
- implement health/capability/session/prompt/event/cancel operations against the
  pinned Serve protocol;
- subscribe/replay correctly so prompt admission is never confused with turn
  completion and model-switch failure cannot be missed;
- normalize assistant/thought/tool/plan/question/permission/completion/failure/
  cancellation/reconnect/resync/death events into Odysseus events;
- ignore unknown events safely and keep the native harness available.

Required gates:

- all behavior passes against the fake daemon with fragmented/replayed SSE;
- missing binary, wrong version, missing capability, worker crash, cancellation,
  reconnect beyond replay, and teardown produce explicit bounded failures;
- generated configuration is mode `0600`, contains no upstream URL/key, and is
  removed on normal exit and recovery cleanup;
- Qwen cannot start if read-only/no-shell/no-write capability is unproven.

Checkpoint commit:

```text
feat(harness): supervise pinned read-only qwen serve
```

Stop after reporting the capability matrix. If Qwen is not installed, this
checkpoint still completes using the fake daemon.

## G1D1 — Integrate the headless scoped turn

**Outcome:** the continuity and worker tracks meet through one narrow,
feature-flagged route without replacing the existing agent loop.

Work:

- integrate the G1B and G1C checkpoint branches into the target branch;
- add one application lifecycle hook for bridge/supervisor ownership and one
  narrow context/harness selection seam;
- route an explicitly selected read-only project turn through the same semantic
  `ContextBundle` used by the native harness;
- pass the selected companion personality, scope manifest, and bounded project
  context without granting the worker authority over artifacts;
- persist worker output through normal Odysseus message ownership, then create
  checkpoint/brief artifacts only through the continuity services;
- preserve native fallback, cancellation, streaming, and error behavior;
- keep GUI behavior unchanged.

Required gates:

- end-to-end fake Qwen -> ModelBridge -> fake upstream flow passes;
- native and Qwen routes receive semantically identical context inputs;
- cancellation and failure leave raw messages/artifacts consistent;
- default-off flag and missing runtime preserve current native behavior;
- full focused session/chat/agent/compaction/provider suites pass.

Checkpoint commit:

```text
feat(g1): connect scoped continuity to qwen harness
```

Stop after reporting integration conflicts, event traces, and rollback behavior.

## G1D2 — Synthetic vertical slice and protected Dust runner

**Outcome:** every safety and isolation claim is proven without a live model or
owner workspace mutation.

Work:

- execute the vertical slice against synthetic projects and fake services;
- prove primary-chat continuation and explicit-fork project-brief sharing;
- prove Personal/Computer, cross-owner, unlinked, and transitive isolation;
- implement the protected Dust runner: resolve exact roots, record repository
  HEAD/status/hashes, use read-only capabilities, and verify identical state on
  success, failure, timeout, and cancellation;
- run deterministic evaluation against a protected Dust HEAD checkout when
  available; never copy it into this repo and never invoke its sorter;
- produce an owner-readable, secret-free test report format with citations,
  label scoring, latency, failures, feature state, and before/after proof.

Required gates:

- all acceptance tests that do not require a live Qwen/provider pass;
- protected-runner self-tests deliberately detect a mutation;
- no report contains private content, reusable secrets, or copied Dust corpus;
- broader/full feasible suite passes, with reproducible baseline deltas listed.

Checkpoint commit:

```text
test(g1): prove synthetic continuity vertical slice
```

Stop with a readiness checklist for the separately approved live canary.

## G1E — Approved live read-only Dust demonstration

**Outcome:** measured evidence answers whether pinned Qwen plus the lean
continuity layer is useful enough to retain.

Prerequisites requiring explicit owner approval:

- install the exact pinned Qwen npm package data-locally if no supported binary
  already exists;
- send the bounded Dust context/questions to the owner-selected configured
  provider through the ModelBridge.

FAL OpenRouter with `deepseek/deepseek-v4-flash` is the intended first canary,
but it is configuration, not architecture. If unavailable, the owner may choose
another existing OpenAI-chat-compatible endpoint/model without a code change.

Work:

- verify all fake gates again before resolving any real endpoint credential;
- start a fresh disposable Qwen runtime and run the golden Dust questions;
- compare native and Qwen answers for evidence, epistemic labels, usefulness,
  latency, failure behavior, and abstention;
- restart Qwen and prove the primary Dust chat continues from Odysseus artifacts;
- create an explicit test fork and prove it gets the project brief but no raw
  tail or thread checkpoint from the primary chat;
- run the Personal Advisor lemon question and prove its context manifest has no
  Dust data;
- search Qwen files, environment inventory, process arguments, events, and logs
  for the upstream URL/key and confirm absence;
- tear down and remove the disposable worker/bridge runtime;
- verify Odysseus owner data was only changed by the intended test sessions and
  continuity artifacts, and verify Dust HEAD/status/hashes are unchanged;
- record a concise non-sensitive G1 report. Keep raw owner/project output in
  local application data, not Git.

Required gates:

- every acceptance item in the active product spec is reported pass/fail with
  evidence; no item is silently omitted;
- failures are represented honestly and do not cause relaxed permissions;
- provider/Qwen/Dust state is cleaned up and reusable credentials remain only in
  Odysseus's existing encrypted store;
- the final commit contains no secret or private project content.

Checkpoint commit:

```text
docs(g1): record read-only acceptance evidence
```

Stop at **G1**. Present several answers and the report to the owner. Do not begin
AgentMemory work, GUI work, write/shell enablement, or Fedora repair work.

## G1 owner decision

The owner decides:

1. whether Qwen answers are useful enough to keep the harness;
2. whether the checkpoint and project-brief semantics feel correct;
3. which Dust authority/label expectations need correction;
4. whether to proceed to scoped AgentMemory work (G2) or thin GUI work;
5. whether future checkpoint reviews remain per-commit or become less frequent.

An unsuccessful semantic evaluation is still a valid G1 result. Preserve the
continuity kernel if its tests are useful, disable the Qwen feature flag, and
record the measured reason before proposing a replacement architecture.
