# Rendered browser UI audit

This workflow checks Odysseus in a real headless Brave renderer. It is meant
for bugs that source inspection and HTTP tests cannot prove, such as a button
whose click handler silently returns, duplicate runtime form IDs, or labels
created by JavaScript.

## What it does

`scripts/run-browser-ui-audit.sh`:

1. makes a SQLite backup of `data/app.db` in a temporary directory;
2. rewrites only that copy to a synthetic `ui-audit` owner and inert local
   model endpoint;
3. starts an isolated Odysseus server on port 7002;
4. starts a fresh headless Brave profile with DevTools on port 9223;
5. logs in with a disposable account;
6. checks the current UI build, expanded Chats section, Qwen readiness text,
   and absence of legacy shell controls;
7. opens the requested session, deletes a persisted message pair, reloads it,
   edits an older user message, and clicks the exact inline
   `button.edit-save-btn`;
8. creates a project fork, toggles it to native Agent, submits a synthetic
   prompt, and inspects the real multipart request for `allow_bash=false` and
   the absence of `/api/shell/*` traffic;
9. switches model/endpoint and reloads, opens Personal and Computer homes
   twice, deletes the project, and verifies the durable state after each step;
10. verifies a persisted Qwen Process trace and feedback fixture when synthetic
    mode is enabled; and
11. repeats Companion navigation and project-modal checks at a 390×844 mobile
    viewport before deleting the temporary database and browser profile.

The live database, the normal Brave profile, port 7001, saved credentials, and
model providers are not used. Qwen is disabled in the default isolated server,
so the audit also proves that setup status remains visible and an unavailable
Qwen harness cannot be enabled. The native request uses an inert loopback
endpoint and is stopped after its request policy is captured.

## Run it

From the repository root:

```bash
./scripts/run-browser-ui-audit.sh SESSION_ID
```

The most recently active eligible primary project home is selected when the
argument is omitted:

```bash
./scripts/run-browser-ui-audit.sh
```

Use the fully synthetic persisted Process/feedback fixture for the broad G1.5
acceptance run:

```bash
ODYSSEUS_AUDIT_SYNTHETIC=1 ./scripts/run-browser-ui-audit.sh
```

`ODYSSEUS_AUDIT_LIVE_QWEN=1` exists for an explicitly approved provider canary.
It copies no source browser profile, but it does perform a real model call. Do
not enable it with private repository content unless exporting that content to
the configured provider has been separately approved.

Live mode still replaces the source transcript and workspace with synthetic
fixtures. It uses the cloned endpoint route only long enough to exercise the
real Qwen → ModelBridge → configured-provider path, then removes the temporary
database, key copy, browser profile, worker, and bridge during cleanup.

Requirements are `brave-browser`, `sqlite3`, `curl`, `rg`, Python, and the
Python `websockets` package. Ports can be changed when needed:

```bash
ODYSSEUS_AUDIT_APP_PORT=7012 \
ODYSSEUS_AUDIT_DEBUG_PORT=9233 \
./scripts/run-browser-ui-audit.sh SESSION_ID
```

## Results

The command exits successfully only when the complete rendered acceptance set
passes. It prints a compact summary and writes detailed evidence to:

- `artifacts/browser-ui-audit/report.json`
- `artifacts/browser-ui-audit/inline-edit.png`
- `artifacts/browser-ui-audit/server.log`
- `artifacts/browser-ui-audit/brave.log`

The JSON report includes inline-button state, API methods/statuses, native form
policy, console messages, Qwen readiness, Process/feedback replay, session and
project persistence, desktop/mobile geometry, duplicate form-field IDs, fields
without identity or accessible labels, and unassociated `<label>` elements.
