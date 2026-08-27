# G1.5 acceptance report

This is the verification checklist for Companion Homes and the read-only Qwen
UI trial. The HUGE plans remain deferred until ordinary G1.5 use has produced
approximately one week of feedback.

Current release candidate: `20260804g15ui29`, verified on 2026-08-04.

## Verification summary

- Full repository suite: **4,873 passed, 3 skipped**.
- Final current-source Qwen/G1.5 focused suite: **51 passed**.
- Disposable rendered Brave audit: passed on desktop and 390×844 mobile.
- Provider-backed synthetic Qwen canary: passed through the real Qwen binary,
  Bubblewrap worker, ModelBridge, and configured provider. The completed
  Process trace, Helpful rating, and note all survived a full reload.
  Restored tool lines used the compact human-readable forms `Read: workspace`,
  `List: workspace`, `Read: README.md`, and `List: .`.
- Live test server: port 7001 serves `20260804g15ui29` with no-store HTML and
  versioned frontend/service-worker assets.

## Automated acceptance

| Area | Evidence | State |
|---|---|---|
| Deterministic Personal, Computer, and project homes | Route concurrency tests plus rendered reopen checks | Pass |
| Project creation, server-owned workspace, model, endpoint | Route tests and rendered modal/folder/model checks | Pass |
| Explicit project forks | Rendered fresh transcript, shared project, non-primary checks | Pass |
| Qwen/native switch persistence | Route tests and rendered reload checks | Pass |
| Qwen readiness without leaked paths | Component tests plus rendered unavailable state | Pass |
| Inline edit/resend | Exact rendered `.edit-save-btn`, truncate POST, completed provider-backed Qwen replacement turn | Pass |
| Message and project deletion persistence | Rendered delete, reload, database/API absence | Pass |
| Qwen Process and feedback persistence | Provider-backed turn plus full reload of Process, rating, and note | Pass |
| Native project shell denial | Actual multipart `allow_bash=false`; no `/api/shell/*` request | Pass |
| Read-only containment and workspace integrity | Harness/supervisor/service mutation tests | Pass |
| Cancellation, timeout, worker death, concurrent turns | Supervisor/service/route lifecycle tests | Pass |
| Owner and provider-route isolation | Route, endpoint, stop, and feedback owner tests | Pass |
| Sanitized events and process arguments | Qwen harness/supervisor safety tests | Pass |
| Cookbook/document execution retained | Cookbook/document regression suite | Pass |
| Runtime form labels and unique IDs | Rendered DOM audit | Pass |
| Mobile Companion navigation and project modal | 390×844 rendered audit | Pass |

## Manual acceptance before moving on

- Reload port 7001 and confirm the console reports the current build ID.
- Edit an older project prompt and confirm inline Send visibly starts a new
  replacement turn.
- Run several ordinary Dust questions, inspect the collapsed Process lines,
  stop one active turn, and submit Helpful/Wrong/Unsafe feedback.
- Compare the same project question with Qwen enabled and disabled.
- Check desktop and phone interaction for sidebar navigation, focus, streaming,
  Stop, and the disabled-Qwen explanations in Personal and Computer homes.
- Use G1.5 normally for roughly one week before promoting any HUGE-plan work.

The one-week soak is a product decision gate, not an automated release test.
It deliberately remains open after the technical G1.5 candidate is green.
