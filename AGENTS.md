# AGENTS.md

This file provides guidance to coding agents working in this repository. Claude Code, and any other
agent that reads the neutral `AGENTS.md` name, picks it up automatically.

## What this repository is

A **fork** of upstream Odysseus (`https://github.com/odysseus-dev/odysseus`, remote `upstream`, push
disabled) — a self-hosted FastAPI + no-build-SPA AI workspace. The active downstream branch
`feat/companion-continuity-mvp` adds a **Companion** layer on top: deterministic Personal Advisor,
Computer Help, and per-project primary chats with durable memory, working artifacts, a read-only Qwen
worker, and reviewed project patches.

Two consequences shape almost every decision here:

1. **Ordinary upstream Odysseus chat must keep working unchanged.** New chat, chat management,
   Chat/Agent mode, and native tools are upstream behavior. Fork UI belongs in Companion homes and
   shared capability components; reuse native components instead of building a parallel visual
   language, so future `upstream/dev` merges stay manageable.
2. **The fork's value is its containment boundary,** not its feature count. Most of the recent commit
   history is hardening. Do not widen authority to make something work.

## Read before substantial work

- `specs/_readme.md` — the DocumentMap for upstream subsystems. Read it, then only the subsystem specs
  matching your work. Specs are read-only context during ordinary work; if code and specs disagree,
  **code is ground truth** — report drift, don't edit specs unless spec work was explicitly requested.
- `ODYSSEUS_IMPLEMENTATION_PLAN.md` — the active fork plan and milestone state table.
- `ODYSSEUS_PRODUCT_SPEC.md`, `ODYSSEUS_G2D_EXECUTION_PLAN.md` (Computer Help), `ODYSSEUS_G2B_EXECUTION_PLAN.md`
  (project patches), `ODYSSEUS_G2C_EXECUTION_PLAN.md` (scoped memory / artifacts).
- `ODYSSEUS_OWNER_CANARY_GUIDE.md` — the three manual owner gates that unit tests cannot replace.
- `ODYSSEUS_AUTO_APPROVER_DESIGN.md` - the agreed terms for a second model (Jev or similar) approving
  held actions. Only the shadow length is open, and nothing is built yet.
- `*_HUGE.md` files are a **deferred idea bank only**. They are not the backlog and must never be used
  to justify starting autonomous workflows, unrestricted shell, or Codex integration.
- Treat all plan docs as living: verify claims against code and `git log` before calling a phase done.

## Commands

Always use the project virtualenv — system `python3` lacks pinned deps (import errors there are
environmental, not real failures).

```bash
./venv/bin/python -m pytest -q                       # full suite (~830 test files, several minutes)
./venv/bin/python -m pytest tests/test_qwen_supervisor.py -q
./venv/bin/python -m pytest tests/test_g2c_memory.py::test_name -q
```

Focused runs via the taxonomy markers (`tests/conftest.py` tags every test with an `area_*` and a
`sub_*` marker derived from its filename):

```bash
./venv/bin/python tests/run_focus.py --area security
./venv/bin/python tests/run_focus.py --area services --sub-area cookbook
./venv/bin/python tests/run_focus.py --fast --durations 25
./venv/bin/python tests/run_focus.py --last-failed
./venv/bin/python tests/run_order_report.py -- tests/cli/ -q   # report-only order-sensitivity check
```

Areas: `security`, `routes`, `services`, `cli`, `js`, `helpers`, `unit`, `uncategorized`.
`--fast` selects `not slow`; the `slow` marker is opt-in and requires `--durations` evidence.

Run the app (fork launcher; port 7001, sets the Qwen/sandbox env and restarts only a server belonging
to this checkout):

```bash
./run-companion.sh start     # also: reload | stop | status
```

Plain upstream-style run (port 7000):

```bash
./venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port 7000
```

Checks CI runs (mirror these before pushing):

```bash
python -m compileall -q app.py core routes src services scripts tests
python scripts/build_frontend.py --check         # committed static/js/features must match frontend/src
node --check static/js/<file-you-changed>.js
node scripts/check_frontend.mjs                  # needs `npm ci --ignore-scripts` once, for acorn
node --test tests/frontendArchitecture.test.mjs
./venv/bin/python -m pytest -q
```

Computer Help sandbox readiness / qualification (owner-run, never automated):

```bash
python scripts/qualify_computer_sandbox.py            # print safe readiness state only
```

Frontend JS tests run as pytest wrappers that shell out to `node` (`tests/*_js.py`,
`tests/streaming/*.test.mjs`). There is no bundler, but there is now one npm dev dependency
(`acorn`, for `scripts/check_frontend.mjs`): run `npm ci --ignore-scripts --no-audit --no-fund` once.

Python dependencies install from `requirements-companion.txt`, which layers on upstream's four
requirements files with `-r` lines and never edits them. The Companion layer adds no Python package
of its own, so that file's real content is the three prerequisites pip cannot install: bubblewrap,
rootless Podman, and the Playwright chromium the browser tests share.

## Architecture

### Backend shape

- `app.py` (~57k) is the composition root: MIME registration, middleware stack, auth gate, static
  mounts, exception handlers, and **module-level router registration**. Routers are built by
  `setup_<area>_routes(...)` factory functions in `routes/` that return an `APIRouter`; `app.py` calls
  the factory and `include_router`s the result. Adding a route surface means touching both files.
- `core/` — cross-cutting primitives: `auth.py`, `database.py` (SQLAlchemy models + startup
  migrations), `middleware.py` (CSP/security headers), `session_manager.py`, `atomic_io.py`,
  `log_safety.py`. `core/constants.py` **only re-exports** `src/constants.py`.
  The fork's own nine tables are **not** in `core/database.py`: they live in
  `src/companion_tables.py`, with their startup migrations in `src/companion_migrations.py`.
  `core/database.py` keeps them importable through a module `__getattr__`, so
  `from core.database import Project` still works, and `init_db` imports the table module for its
  side effect without touching a name on it. That order matters: `core/database.py` calls `init_db()`
  while it is still executing, so an eager import either way round is a cycle.
  `tests/test_companion_schema_module.py` pins both import orders and the fresh-database schema.
- `src/` — the bulk of business logic (agent loop, LLM core, chat processing, continuity, companion).
  `services/` holds a partially overlapping older layer (search, tts, stt, research, memory, shell);
  the `src/` vs `services/` duplication is transitional and documented per-subsystem in `specs/`.
- `routes/` — HTTP surface only; authorization is enforced here and in `core`, never in the browser.
- `mcp_servers/`, `integrations/` (Codex/Claude scoped APIs), `companion/` (pairing), `scripts/`
  (CLI entry points, several tested via `tests.helpers.cli_loader.load_script`).

### Frontend shape

Raw static SPA — **no Vite, React, TypeScript, or bundler**. `static/index.html` owns DOM shell and
script load order; `static/app.js` plus ~97 ES modules under `static/js/` load directly in the browser.
Script URL identity must stay aligned across `index.html` tags, imports, and `static/sw.js` precache
lists. CSP nonces are injected server-side by `app.py`; new inline/external scripts must fit the CSP
contract in `core/middleware.py`.

Two upstream changes landed in the 2026-11-02 merge and both are easy to trip over:

- **`static/style.css` is gone.** The stylesheet is an ordered cascade of numbered fragments under
  `static/css/`, with the fork's own rules last in `static/css/18-companion.css`. Every app
  stylesheet must carry the **same** `?v=` token in `index.html` and in the `PRECACHE` list in
  `static/sw.js`, or `tests/helpers/stylesheets.stylesheet_cache_version` refuses the set. A test
  asserting on a CSS rule reads `tests.helpers.stylesheets.app_css()`, never one fragment:
  `tests/test_stylesheet_test_hygiene.py` fails any test that names `static/style.css` in a quoted
  literal.
- **`frontend/src/**` is published, not bundled.** `scripts/build_frontend.py` copies the modules
  named in `frontend/modules.json` to `static/js/features/**`, and the copies are committed. Edit the
  source under `frontend/src/`, re-run the script, and commit both sides: CI runs
  `scripts/build_frontend.py --check`. The browser still loads plain ES modules, so the no-build
  rule above still holds.

**The Companion UI deliberately lives inside upstream modules** — `static/js/sessions.js` (scope
banner, capability drawer, memory/context modals, task roots), `static/js/chat.js`,
`static/js/chatRenderer.js`, `static/app.js`. Do not create a parallel companion bundle.

### Companion layer (fork-specific)

Route prefixes and their owners:

| Prefix | Module | Surface |
|---|---|---|
| `/api/g1` | `routes/g1_continuity_routes.py` | homes, projects/forks, harness + capability toggles, project turns/streaming/stop, feedback |
| `/api/companion` | `routes/companion_memory_routes.py` | scoped memory, semantic proposals/promotion, checkpoint mounts + synthesis, artifacts, context grants, legacy-memory migration |
| `/api/companion` | `routes/companion_patch_routes.py` | patch-turn streaming, patch list/apply/reject/rollback |
| `/api/companion/computer` | `routes/computer_help_routes.py` | status, observations, sandbox qualification, task roots |

Core modules:

- `src/continuity/` — `contracts.py` (versioned, schema-validated payloads; scopes
  `project|personal|computer|general`; derivation statuses `legacy_unclassified|heuristic|proposed|accepted|rejected`),
  `store.py` (owner-scoped persistence), `compiler.py`, `semantic_proposals.py`, `semantic_deriver.py`.
  Nothing becomes `accepted` home state without an explicit owner promotion.
- `src/companion_capabilities.py` — server-owned capability profiles, grouped in the UI as **Can see**
  (`workspace_read`, `web_search`, `system_observe`, `sandbox_read`) and **Can do** (`project_shell`,
  `project_write`, `memory_write`). The browser names a capability; it never supplies a command,
  mount, endpoint, or policy. Legacy global tools (`manage_memory`, `manage_skills`, `search_chats`,
  and equivalently-named MCP tools) are denied in Companion homes, and so are host `bash`, `python`
  and `manage_bg_jobs`.
- `src/project_shell.py` + `src/agent_tools/companion_tools.py`: the native agent's Companion tools.
  `project_shell` runs bash in an offline Bubblewrap overlay of the checkout. In a project chat
  `write_file`, `edit_file` and `apply_patch` are routed to `src/project_patches.py` as recorded,
  undoable changes. `update_memory` writes accepted brief revisions through `src/home_brief.py`.
  The name is `project_shell`, not `shell`: upstream `src/tool_parsing.py` aliases `shell` to `bash`.
  `request_capability` lives here too, and it refuses to draw an Enable card for a grant
  `companion_capabilities.changeable_now` says the owner could not switch on. The module's
  `companion_tool_handlers()` is the fork's whole tool registration: `src/agent_tools/__init__.py`
  folds it into `TOOL_HANDLERS` in one line, so a new Companion tool touches no upstream file.
- `src/qwen_harness.py` / `qwen_supervisor.py` / `qwen_inspection.py` — disposable, rootless, pinned
  (0.21.3) read-only Qwen Serve worker and its readiness contract.
- `src/protected_workspace.py` + `src/agent_tools/sandbox_tools.py` — tamper-evident, descriptor-bound,
  symlink-free read snapshots. `.git` is intentionally excluded, so Git inspection is honestly denied
  rather than advertised and broken.
- `src/project_patches.py` — **the only module permitted to change a project checkout.** Validates
  paths/sizes/preimage hashes/file types, generates a trusted diff, applies atomically, verifies
  applied hashes, and supports conditional rollback.
- `src/computer_sandbox.py` — rootless-Podman broker owning exact container args; accepts no
  browser-supplied command or mount. `src/computer_observe.py` — fixed-category read-only host
  diagnostics with no generic "run command" entry point.
- `src/companion_memory.py` — briefs, revisioned working artifacts, long-paste capture, Documents
  bridge, context grants.

## Non-negotiable invariants

Violating any of these is a product regression even if tests pass.

- **Qwen is physically read-only.** It never receives project writes, host shell, network, MCP/memory/
  skills/extensions, provider credentials or ModelBridge tokens, sibling projects, private home data,
  SSH agents, or desktop/container sockets. Do not "fix" a limitation by enabling Shell inside the
  networked Bubblewrap worker — that design was reviewed and rejected.
- **Never silently fall back from Qwen to a more privileged native Agent request.**
- **`project_shell` stays offline and forgetful.** It keeps `--unshare-all`, `--clearenv`, the
  synthetic passwd and hosts, the masked secret files, the credential-free `.git/config` copy, and
  the overlay that discards every write. Never bind the host home, the data directory, a socket or
  the real checkout writable into it. Real changes go through the edit tools and the transaction.
- **An owner grant replaces the approval card only while the run has read nothing but the owner's
  checkout.** Web or fetched text, outside context carried into the run, and bearer tokens put
  granted edits and memory updates back behind approval. `ToolRunSecurityContext` in
  `src/tool_capabilities.py` holds this rule.
- **Odysseus applies patches, not the model.** Proposal generation may be low-friction, but every
  filesystem mutation passes the server-owned transaction boundary. Browser-supplied content and model
  self-approval are not trust signals.
- **`computer_assist` stays disabled** until an owner selects a reviewed digest-pinned image and the
  qualification probe passes. Never select or pull an image automatically, and never give
  `ODYSSEUS_COMPUTER_SANDBOX_IMAGE` a default anywhere: the cached Qwen image is explicitly not an
  approved substitute, and `run-companion.sh` defaulting to it once made the launcher take this
  decision implicitly. Readiness reasons, in the order `readiness()` checks them:
  `podman_unavailable`, `podman_not_rootless`, `pinned_sandbox_image_required` (no image exported,
  the state after a plain `run-companion.sh start`), `sandbox_image_not_local`,
  `sandbox_report_outdated` (this image passed an earlier, weaker fixture), `containment_probe_incomplete`.
  A stale report on disk never authorizes anything: `_REPORT_VERSION` retires it.
- **The server owns workspace/project context**, not browser `localStorage` and not the old bash toggle.
- **Every owner-supplied directory goes through `src/path_identity.pin_directory`.** Checking a path
  and then resolving it reads the filesystem twice and believes both answers, which is how the same
  TOCTOU bug got fixed five separate times. `pin_directory` settles type, symlink status, and inode
  identity in one sequence; `PinnedPath.verify()` rechecks at the point of use. `tests/test_path_identity_guard.py`
  fails when a containment module resolves a path outside it, and its allowlist must say why an
  exemption is not a path grant.
- **Errors stay actionable and sanitized** — no credentials, provider URLs, absolute host paths, or raw
  internal logs in responses, persisted events, or command previews.
- **A switched-off grant stays askable.** Every Companion home offers `request_capability` as a real
  tool, and the prompt names the grant to ask for. A real-model run on 2026-09-25 showed that naming it
  in the prompt without the schema makes the model guess arguments and stall.
- **Companion follow-ups keep their earlier turns.** The continuity branch of
  `routes/chat_helpers.build_chat_context` trims against the model's window from `get_context_length`.
  Trimming against the request's own size dropped every earlier turn from 2026-08-03 to 2026-09-25.
- **Read-only operations do not require per-operation approval.** Escalation is requested once, at a
  meaningful capability boundary, in language the owner can evaluate. Disabled capabilities remain
  *known* to the assistant so it can ask the owner to enable a specific one.
- **Spelling is "Artifacts."** Legacy `/artefacts/...` routes exist only as
  `include_in_schema=False` aliases; do not add new ones.
- Artifacts open through the existing split-screen Document editor. Do not build a second editor.

## Conventions

- **No hardcoded paths, ports, or loopback URLs.** Every persisted file/dir has a named constant in
  `src/constants.py` (the single source of truth); use `internal_api_base()` from `src.constants`
  instead of `http://localhost:7000`. Never build writable paths from `Path(__file__)` into the source
  tree — it is read-only in Docker.
- **Commits:** Conventional Commits, `type(scope): summary`. Make 2–3 logical commits during a
  substantial phase rather than one enormous commit. Keep the branch mergeable with `upstream/dev`,
  resolving conflicts in favor of upstream-compatible ordinary-chat behavior.
- **Visual changes:** reuse existing CSS variables and component classes, no Unicode emoji in UI or
  code (inline monochrome SVG instead), Fira Code monospace, dark theme default. Run the app and look
  at the change; type-checks are not enough.
- **Tests:** deterministic, behavior-first (not source-text assertions), isolated from global process
  state, order-independent. See `tests/TESTING_STANDARD.md` for the standard and `tests/README.md` for
  the helper reference (`tests.helpers.cli_loader`, `tests.helpers.import_state`). Never weaken a test
  with `skip`/`xfail` to make a run pass.

## Fork environment flags

`run-companion.sh` sets `ODYSSEUS_QWEN_HARNESS` and `ODYSSEUS_QWEN_BINARY`, and forwards
`ODYSSEUS_COMPUTER_SANDBOX_IMAGE` only when the owner already exported it. None of the fork flags are
documented in `.env.example`:

- `ODYSSEUS_QWEN_HARNESS`, `ODYSSEUS_QWEN_BINARY`, `ODYSSEUS_QWEN_BRIDGE_TOKEN`
- `ODYSSEUS_QWEN_PROJECT_INSPECT`, `ODYSSEUS_QWEN_SANDBOX_IMAGE` (G2A path, rejected)
- `ODYSSEUS_COMPUTER_SANDBOX_IMAGE` (must be `registry/image@sha256:<64-hex>`)
- `ODYSSEUS_CONTINUITY_CONTEXT`, `ODYSSEUS_SEMANTIC_PROPOSALS_AUTO`

## Open decisions — do not settle these unilaterally

- Whether semantic derivation uses the selected chat model or a separate utility model.
- How legacy Odysseus memory reconciles with the Companion/AgentMemory layer.
- Which reviewed image becomes the Computer Help base.
- How the auto approver's shadow period ends, a fixed number of cards or the owner's word. The other
  terms were answered on 2026-09-29 (see `ODYSSEUS_AUTO_APPROVER_DESIGN.md`): project edits and memory
  updates, Sara's `~/.sara-gateway/jev.key`, a per-chat switch.

Settled by the owner on 2026-09-25 (see `ODYSSEUS_IMPLEMENTATION_PLAN.md`):

- Project chats use the native agent with per-chat grants, and a switched-on grant needs no
  per-action approval.
- Qwen Serve is removed, on its own branch, merged only after the owner's canary 4 passes. That also
  retires the question of brokered web search for project Qwen. The branch `feat/remove-qwen-serve`
  was built on 2026-09-29 and is not merged. Until it merges, the Qwen invariants above still hold on
  `feat/companion-continuity-mvp`.
- A second model, Jev or similar, may approve some held actions on the owner's behalf. Until the design
  is answered and built, nothing approves in the owner's place.
- `ask_teacher` stays in project chats.
