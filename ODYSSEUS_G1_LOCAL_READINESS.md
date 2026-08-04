# G1 read-only acceptance evidence

**Status:** G1 implementation and approved live canary complete; owner decision
pending.

**Date:** 2026-08-04

## Delivered boundary

- Scoped projects, stable conversation-home bindings, and the two permitted
  versioned derived artifacts: `ThreadCheckpointV1` and `ProjectBriefV1`.
- Non-destructive checkpointing and deterministic context compilation. The
  existing native chat integration remains behind
  `ODYSSEUS_CONTINUITY_CONTEXT=1` and defaults off.
- A provider-neutral, run-scoped ModelBridge with a dedicated loopback listener,
  random expiring bearer, fixed owner/endpoint/model route, request budget,
  body limit, and upstream-header isolation.
- A pinned Qwen Code `0.21.3` supervisor using a private home and a rootless
  Bubblewrap boundary. It exposes exactly one project at `/workspace` through a
  read-only bind and does not expose the rest of the owner's home.
- An authenticated headless project-turn API at `/api/g1/project-turn`. It is
  absent by default and requires both `ODYSSEUS_QWEN_HARNESS=1` and an explicit
  `ODYSSEUS_QWEN_BINARY` path.

The approved package is installed in ignored owner-local application data at:

```text
data/qwen/0.21.3/node_modules/.bin/qwen
```

Dust is not copied into Odysseus. The existing owner-approved checkout is
mounted directly and read-only only for the lifetime of a worker.

## Automated evidence

The final focused regression command completed with **284 passed** tests and
one existing SQLAlchemy 2.x deprecation warning. Coverage includes persistence,
additive migration, raw-history preservation, cursor idempotency, primary/fork/
Personal/Computer isolation, native rollback, bridge authorization and relay,
Qwen configuration/protocol/replay/cancellation/death, protected-workspace
mutation detection, and the default-off API seam.

`git diff --check` passes. The tracked diff contains no provider credential,
private Dust content, copied Dust file, Qwen package, or raw live answer.

## Approved live evidence

- Qwen package/version: exact `@qwen-code/qwen-code@0.21.3`; no global install,
  `npx`, vendored source, or Git submodule.
- Provider route: the existing owner-scoped FAL endpoint with fixed model
  `deepseek/deepseek-v4-flash-0731`, reached only through ModelBridge.
- Synthetic vertical slice: real Qwen -> ModelBridge -> configured provider ->
  Qwen read-only `read_file`; terminal state was `turn_complete/end_turn`.
- Dust snapshot: HEAD
  `141765ac135b7a77931697d4a9a0f8eedd10c97a`, 69 non-Git files, and pre-existing
  status fingerprint
  `b4ece1becc2ecdd6f5a8420c84b0c603a1e84788f97b9170af9b789cc37e9177`.
  HEAD, status, file count, paths, and file hashes were identical after both
  semantic questions and the fresh-worker restart.
- Semantic gate 1 passed: the final-crystal response cited both relevant source
  files, distinguished the established premise from the proposed ending and
  later unresolved contradiction, and did not declare either ending canon.
- Semantic gate 2 passed: the fish-church response found the unresolved
  placeholder, distinguished surrounding established facts from inference,
  and abstained from inventing missing practices.
- Restart gate passed: a new Qwen process with no previous worker state resumed
  from the explicit Odysseus checkpoint, shared project brief, recent tail, and
  current request, then re-verified the source files without choosing canon.
- Runtime inventory confirmed that Qwen received a loopback bridge URL, fixed
  model ID, and ephemeral bridge token only. The reusable provider credential
  and upstream provider URL were absent from Qwen arguments, environment,
  settings, events, and retained logs.
- The bridge and Qwen processes were torn down after every run. Live raw answers
  remain only in ignored local application data/evidence, not Git.

## Active-spec acceptance matrix

| Acceptance item | Result | Evidence |
| --- | --- | --- |
| Raw IDs/order/count/content survive compaction | Pass | Snapshot tests and live four-row preservation check |
| Same cursor/hash is idempotent | Pass | Artifact revision/idempotency tests |
| Primary resumes from checkpoint and tail | Pass | Live fresh-worker restart |
| Explicit fork gets brief, not primary checkpoint/tail | Pass | Compiler isolation test and live manifests |
| Personal and Computer receive no Dust by default | Pass | Scope-isolation tests and live Personal manifest |
| Direct-related lookup is explicit, labeled, bounded | Pass | Direct/unlinked/transitive tests and rendered section labels |
| Native and Qwen share the semantic bundle | Pass | One `ContextCompiler`/`ContextBundle` contract feeds both seams |
| Replay/cancel/unknown/death fail without transcript corruption | Pass | Cursor replay, deduplication, cancellation, death, teardown, and raw-message tests |
| Qwen lacks reusable provider material | Pass | Live process/config/event/log inventory |
| Dust remains byte/Git-status unchanged | Pass | Protected pre/post snapshots on live and restart runs |
| Final-crystal answer cites and labels the conflict | Pass | Owner-local live evidence |
| Fish-church answer abstains on missing lore | Pass | Owner-local live evidence |
| Feature disable restores existing behavior | Pass | Default-off native and API-gate tests |

## Failures found and corrected during acceptance

1. Initial Serve smoke flags included global options unsupported by Qwen
   `0.21.3`; the launch contract now uses only verified `serve` options.
2. The first protected snapshot attempt used an unbounded whole-file read and
   exceeded the orchestration window; hashing now streams fixed-size chunks.
3. A later checkpoint initially reconsidered an old source prefix; compaction
   now advances strictly after the previous durable source cursor.
4. The bridge route constructor had reversed run-ID/token positions. The bearer
   was still provider-specific and short-lived, but insufficiently random. The
   field order is corrected, tested, and a corrected live synthetic canary
   passed end to end.

## Remaining G1 limitations and owner decision

- This is a Linux-only, local, headless prototype. The new endpoint returns the
  completed answer rather than exposing Qwen's full event stream to the GUI.
- Qwen safe mode advertises built-in skill metadata, but the OS boundary exposes
  only a read-only project and no shell/write authority. No ambient owner MCP or
  memory configuration is mounted.
- Context uses bounded raw-tail counts and bounded bridge requests; richer
  token-aware per-section budgeting and name-to-`@project` UI resolution remain
  future hardening, not exercised by the live canary.
- Existing owner data contains only the explicitly created G1 project, test
  sessions, raw messages, checkpoint, and project-brief revisions. No real
  memory store was read or migrated.

The remaining G1 decision belongs to the owner: retain the optional Qwen
harness based on the measured answer quality, adjust the continuity semantics,
or leave `ODYSSEUS_QWEN_HARNESS` disabled. No AgentMemory, GUI, write/shell, or
larger `_HUGE` plan work begins from this checkpoint.
