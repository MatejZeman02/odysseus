# G1 local readiness evidence

**Status:** local implementation and fake-service verification complete;
live canary intentionally not run.

## Implemented, committed checkpoints

- Scoped projects, stable session-home bindings, and versioned derived artifacts.
- Non-destructive checkpointing and deterministic context bundles behind
  `ODYSSEUS_CONTINUITY_CONTEXT=1` (default off).
- Owner-scoped, ephemeral ModelBridge with fixed model routes and loopback,
  size, expiry, and request-budget controls.
- Disposable Qwen Serve settings/launch contract, protocol client, capability
  gate, prompt/cancel flow, event normalization, and fake-daemon tests.
- Protected external-workspace snapshot runner for read-only evaluations.

## Verified locally

The focused regression command completed with **53 passed** tests covering new
continuity persistence, additive migration, compiler isolation, native flag
rollback, ModelBridge, Qwen harness, protected-workspace detection, legacy
compaction, and session behavior.

The repository-specific secret scan found only the deliberately supported FAL
environment-variable *prefix names* and synthetic test input; no credential
values are tracked by this work.

## Deliberately pending owner authorization

The live demonstration requires all of the following, none of which has been
performed:

1. install or approve a precise data-local Qwen Serve binary;
2. start the disposable loopback worker;
3. select an existing provider endpoint/model and send bounded project context
   through ModelBridge; and
4. run the read-only external-project canary and retain its before/after
   snapshot as local evidence.

The protected runner accepts an already-dirty checkout and proves that the
operation did not change its pre-existing state. It never invokes project
commands or the project's sorter.
