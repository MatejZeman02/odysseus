# Odysseus iteration report — 2026-08-28

## Executive summary

The Companion foundation is substantially more robust. Project Qwen remains read-only and workspace-scoped; Computer Help now has an honest, inspectable readiness boundary rather than pretending that privileged assistance is available. The work is committed through `b1522376` and the checkout was clean before this report.

The next work should be guided by real owner canaries, then by choosing and qualifying a reviewed Computer Help sandbox image. No general host shell, project write authority, or automatic privilege escalation has been enabled.

## Implemented in this iteration

| Area | Result |
| --- | --- |
| Protected project roots | Rejects symlinked roots and launch workspaces; revalidates the resolved workspace identity before Qwen runs. |
| Read-tool boundary | Snapshot roots are descriptor-bound with no-follow handling; tool aliases that could escape the intended read profile are rejected. |
| Qwen lifecycle | Process events are persisted in sanitized form. Workspace-integrity failures become a stable `workspace_changed` result instead of an opaque server error. |
| Process disclosure | Command previews are safely quoted, clamped, and do not advertise access that the worker does not have. |
| Git honesty | `.git` is excluded from the snapshot, so Git inspection is denied rather than presented as a capability that cannot work. |
| Computer Help task roots | Creation and retirement are atomic under races; roots must be visible home directories and cannot be hidden configuration, credential, symlinked, or protected locations. |
| Capability UX | Readiness responses give safe explanations for unavailable capabilities, including the missing reviewed sandbox image. |

Notable commits include `79f4bcda` (protected roots), `8ffe84a1` (descriptor-bound snapshots), `545ab541` / `fcaa06ae` (task-root races), `2a66e873` (persisted integrity failures), and `b1522376` (documented current snapshot boundary).

## Verification performed

- Full host suite: **6135 passed, 3 skipped, 124 warnings** in **183.90 s**.
- Focused protection, Qwen lifecycle, Computer Help, and capability tests were included in that run.
- Browser audit of Personal Advisor confirmed the ordinary **New chat** control and capabilities drawer render correctly without console errors.
- No model prompt was submitted during the browser audit, so it did not send user content to a provider.

## Current readiness

| Capability | Status | Notes |
| --- | --- | --- |
| Project Qwen | Ready for its existing read-only profile | Project-only, snapshot-scoped tools; no general shell or project write authority. |
| Project patches | Available through the review/apply path | Odysseus, not Qwen, applies approved changes. |
| Computer observation | Available | Explicitly scoped, read-only diagnostics. |
| Computer assistance | **Not enabled** | Rootless Podman is available, but no reviewed digest-pinned sandbox image is configured locally. |
| Git inspection in Qwen snapshots | Intentionally unavailable | `.git` is not copied into the snapshot. |

The safe readiness state for Computer Help is currently `pinned_sandbox_image_required`. The current Qwen image is not accepted as a Computer Help broker image because it lacks required inspection utilities (`fd` and `file`).

## Required owner gates

1. Run the semantic-continuity canary: Personal and project homes, semantic proposal/promotion, reload, and context isolation.
2. Run the project-patch browser canary using a throwaway Git Markdown file: propose, inspect diff, apply, reload, review, and rollback.
3. Choose a reviewed, digest-pinned Computer Help sandbox image. Then configure it explicitly and run the qualification probe:

   ```bash
   export ODYSSEUS_COMPUTER_SANDBOX_IMAGE='registry.example/image@sha256:<64-hex-digest>'
   python scripts/qualify_computer_sandbox.py --run
   ```

   This must be an owner decision: no image has been selected, pulled, or configured automatically.

## Deliberately deferred

- General host shell access.
- Automatic sudo, partitioning, or other privileged system operations.
- Project Qwen Shell / inspection profile: the earlier Qwen sandbox qualification was rejected, so read-only structured tools remain the safe profile.
- Project-Qwen web browsing; it remains workspace-only.
- AgentMemory migration and a policy for duplicate legacy memory.
- A decision between the currently selected chat model and a dedicated utility model for semantic derivation.

## Recommended next order

1. Complete the three owner gates above and record their results in the canary guide.
2. Fix any canary regressions before expanding capabilities.
3. Design the unified capability model: tools should be discoverable while disabled, and the assistant should request a specific opt-in rather than inventing a special-purpose workaround.
4. Only after Podman qualification, implement G2D Computer Help assistance behind explicit capability grants and the brokered sandbox boundary.

Reference documents: [implementation plan](ODYSSEUS_IMPLEMENTATION_PLAN.md), [G2D execution plan](ODYSSEUS_G2D_EXECUTION_PLAN.md), and [owner canary guide](ODYSSEUS_OWNER_CANARY_GUIDE.md).
