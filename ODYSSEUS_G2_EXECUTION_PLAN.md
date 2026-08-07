# Odysseus G2A Execution Plan — Sandboxed Project Inspection

**Status:** Podman qualification rejected; `project_read` retained

**Depends on:** the implemented G1/G1.5 Companion and Qwen foundation.
Semantic continuity closure remains separately tracked and does not block G2A.

**Parent plan:** [`ODYSSEUS_IMPLEMENTATION_PLAN.md`](ODYSSEUS_IMPLEMENTATION_PLAN.md)

**Product boundaries:** [`ODYSSEUS_PRODUCT_SPEC.md`](ODYSSEUS_PRODUCT_SPEC.md)
and [`ODYSSEUS_PRODUCT_SPEC_HUGE.md`](ODYSSEUS_PRODUCT_SPEC_HUGE.md)

## G2 milestone map

G2 is a family of incremental Companion milestones, not one release containing
every capability:

- **G2A — Sandboxed Project Inspection:** the subject of this document;
  efficient read-only shell inspection for project Qwen.
- **G2B — Reviewed Project Patches:** propose, review, approve, apply, verify,
  and roll back project changes.
- **G2C — AgentMemory Scoped Recall:** inventory and introduce durable memory
  with explicit owner, home, project, and thread boundaries.
	- Maybe add fork and merge conversations and Artefacts.
- **G2D — Computer Help Diagnostics:** read-only host diagnostics under a
  separately designed authority and approval model.

The stages inside this document are named **Stage 1–5** so they cannot be
confused with the G2A–G2D product milestones.

## Decision summary

G2A should make project Qwen substantially better at inspection tasks by letting
its existing agent loop run shell commands inside an enforceable sandbox. The
project remains read-only throughout this milestone.

The motivating failure is simple: a request such as “list every Markdown file
and count the total lines” is naturally expressed with `find`/`rg` and `wc`, but
G1 exposes only Qwen's `read_file`, `grep_search`, `glob`, and `list_directory`
tools. Qwen therefore performs many individual reads, wastes context, and may
reach its turn deadline without completing.

G2A does **not** add a special Markdown counter. It adds a reusable, visible,
bounded execution capability for uncommon project-inspection operations.

The intended result is:

```text
ask a project question
  -> Odysseus compiles the same project-scoped continuity context
  -> a disposable Qwen Serve worker proposes inspection commands
  -> the sandbox enforces the workspace, filesystem, network, process, and
     resource boundaries independently of the model
  -> Qwen receives bounded command output and answers the question
  -> the Process card shows the admitted commands
  -> Odysseus verifies that the workspace is unchanged
  -> every worker and descendant process is terminated
```

## Outcome and non-goals

### G2A outcome

A project session with Qwen enabled can use shell-style inspection where it is
the appropriate tool, while preserving the G1 guarantees:

- one server-owned project workspace;
- no project mutation;
- no access to other projects, the owner's home, or personal memory;
- no command network access;
- no reusable provider credentials in the worker;
- exact visible commands and stable failure reasons;
- bounded runtime, output, subprocesses, and concurrency;
- deterministic teardown and post-turn integrity verification;
- no silent fallback to native Chat or a more privileged harness.

### Explicit non-goals

G2A does not include:

- editing, creating, deleting, renaming, or formatting project files;
- Git commits, branches, rebases, worktrees, pushes, or repository repair;
- dependency installation, build-environment mutation, or package managers;
- host diagnostics, Computer Help shell access, services, logs, or processes;
- web search, arbitrary network access, browser automation, or downloads;
- privilege escalation, `sudo`, `polkit`, containers with elevated privileges,
  or access to host container sockets;
- AgentMemory migration or a change to any durable memory writer;
- Codex sandbox integration;
- Qwen auto-memory, MCP ecosystems, skills, extensions, or project-provided
  Qwen configuration;
- the autonomous or writable portions of the `_HUGE` plan.

## What “Qwen sandbox” means here

Three mechanisms must not be confused:

1. **Qwen Safe Mode** disables ambient customizations such as context files,
   hooks, extensions, skills, MCP servers, memory, and settings-sourced policy.
   It is useful hardening, but it is not an operating-system sandbox.
2. **The existing Odysseus Bubblewrap worker** supplies the current OS boundary:
   a read-only `/workspace`, private Qwen home, temporary `/tmp`, scrubbed
   environment, disposable process group, and no host home mount.
3. **Qwen's optional Linux sandbox** uses Docker or Podman for tool execution.
   The pinned Qwen version documents this facility, but compatibility with
   `qwen serve`, Safe Mode, the ModelBridge, read-only mounts, and deterministic
   teardown has not yet been proven in Odysseus.

Changing `permissions.deny` to allow `Shell` is not sufficient. The current
Bubblewrap worker shares the network namespace needed to reach ModelBridge, so
an unrestricted shell could also attempt arbitrary network connections. G2A must
prove that the model connection remains available while command execution has
no network path.

## Authority model

The model may choose **what to inspect** within the active project. It may not
choose its authority.

Odysseus remains authoritative for:

- session owner, project, workspace root, endpoint, and model;
- whether the G2A capability is enabled;
- the sandbox profile and executable inventory;
- mount, network, environment, resource, and process limits;
- command admission and cancellation;
- event sanitization and persisted audit records;
- post-run integrity checks;
- future transitions from read-only to writable authority.

Qwen remains responsible for:

- the reasoning and tool-selection loop;
- proposing commands using the capabilities it was given;
- interpreting bounded command output;
- producing the final response.

Prompt instructions and Qwen permission rules are defense-in-depth only. A
malicious prompt, source file, filename, model response, or tool argument must
not be able to enlarge the enforced capability set.

## Proposed capability profile

Introduce a server-owned harness capability profile rather than overloading the
Chat/Agent control or browser shell toggle:

```text
project_read
  Qwen read_file, grep_search, glob, list_directory

project_inspect
  project_read plus sandboxed shell inspection
```

Both profiles keep the project mount read-only. `project_inspect` becomes the
G2A project default only after it passes the live acceptance gate. The browser
may display or request a supported profile, but the authenticated session and
server policy resolve the effective authority.

Personal Advisor, Computer Help, legacy chats, native Chat, Cookbook, and
document execution do not inherit `project_inspect`.

## Command surface

The first supported executable set should be intentionally small but useful:

- file discovery and metadata: `find`, `fd` when installed, `stat`, `file`;
- text search and counting: `rg`, `grep`, `wc`;
- bounded viewing and transformation: `head`, `tail`, `sed`, `awk`, `cut`,
  `tr`;
- aggregation: `sort`, `uniq`, `xargs` with enforced process limits;
- read-only repository inspection: selected `git status`, `git diff`, `git log`,
  `git show`, `git ls-files`, and `git grep` forms;
- basic shell composition: pipes, conditionals, and loops inside the bounded
  worker.

This is an executable inventory, not the primary security boundary. Even an
apparently harmless command can consume excessive CPU, spawn descendants, read
unexpected pseudo-files, or invoke another executable. Filesystem, network,
process, and resource isolation must remain effective if command filtering is
bypassed.

Initially unavailable commands include:

- interpreters and compilers unless a measured project need justifies them;
- `curl`, `wget`, `ssh`, `scp`, `nc`, browsers, and other network clients;
- `sudo`, `su`, `pkexec`, mount tools, namespace tools, and container clients;
- package managers and service managers;
- destructive file utilities and Git mutation commands;
- indirect host-control paths such as Docker/Podman sockets, D-Bus, SSH agents,
  desktop portals, and arbitrary `/proc` access.

## Required containment

### Filesystem

- Mount exactly one owner-approved project at `/workspace`, read-only.
- Use `/workspace` as the only project working directory.
- Give the runtime a fresh private home and temporary scratch directory.
- Do not mount the owner's home, sibling projects, Odysseus data, Git
  credentials, SSH configuration, browser data, desktop IPC, or secret stores.
- Minimize `/etc`; do not expose host credential material merely because it is
  nominally read-only.
- Reject traversal and prove that symlinks cannot escape the visible mount set.
- Keep executable locations read-only and prevent execution from writable
  scratch paths where the selected containment mechanism supports it.

### Network

- Shell/tool processes receive no network interface capable of reaching the
  Internet, LAN, host services, metadata endpoints, or ModelBridge.
- Only the Qwen model client may reach its run-scoped loopback ModelBridge.
- ModelBridge continues to accept only the ephemeral owner/run/endpoint/model
  token and fixed route.
- DNS, proxy variables, Unix sockets, and inherited file descriptors must not
  create an alternate path.

The implementation must demonstrate this separation. If Qwen's native sandbox
cannot separate tool networking from model networking, it does not satisfy G2A.

### Processes and resources

- One active Qwen turn per project session remains the admission rule.
- Apply wall-clock, CPU, memory, process-count, open-file, and output limits.
- Set `no_new_privs` and drop unnecessary capabilities.
- Cancel the command and its descendants when the user presses Stop, the
  browser disconnects, the deadline expires, Qwen dies, or Odysseus shuts down.
- Prevent daemonization from surviving the disposable worker.
- Preserve the existing process-group cleanup and test it with nested children.

### Environment and secrets

- Continue using an explicit environment allowlist.
- Keep reusable endpoint credentials and upstream URLs outside Qwen.
- Ensure shell processes cannot read the ephemeral bridge token.
- Remove Git, SSH, cloud, language-package, proxy, desktop-session, and agent
  socket variables.
- Sanitize commands and errors before persistence or SSE display; never persist
  raw environment dumps or provider responses.

## Process and answer UI

The existing Process card remains the only project-agent execution history.
During a turn it should show the current short activity summary followed by one
line per admitted operation:

```text
Qwen is working · Inspect project · 18s

Shell: find . -type f -name '*.md' -print
Shell: find . -type f -name '*.md' -print0 | xargs -0 wc -l
Read: documents/General_guide/Story.md
```

After completion it collapses to:

```text
Process · worked for 31s
```

Expanding it restores the same operation lines. Long commands use the existing
single-line clamp and `✂`; clicking the line reveals the complete sanitized
command over multiple lines. The assistant header continues to show the actual
selected model/personality.

The final integrity badge becomes:

```text
Qwen · Sandboxed inspection · Workspace unchanged
```

Do not display raw command output by default. Qwen may use bounded output as
evidence, while the user sees the exact command, status, duration, and a stable
failure summary. A later evidence viewer may expose reviewed, redacted excerpts
if real debugging shows that commands alone are insufficient.

## Failure contract

Replace the generic `qwen_failed` outcome where a safe distinction is known.
The UI and persisted metadata may receive stable codes such as:

- `sandbox_unavailable`;
- `command_denied`;
- `command_timeout`;
- `command_output_limited`;
- `command_resource_limit`;
- `provider_failed`;
- `worker_died`;
- `turn_timeout`;
- `workspace_changed`;
- `teardown_failed`.

Descriptions must be useful but must not expose absolute host paths, raw tool
output, credentials, provider URLs, or internal process logs. Failed and
cancelled turns retain the user message and Process evidence but do not invent
an assistant answer or silently retry through native Agent.

Mutation detection remains an unsafe terminal outcome even if the sandbox
already reported that a write was denied.

## Implementation sequence

### Stage 1 — Freeze contracts and measure the current boundary

- [ ] Record the G2A capability profile and event schemas before enabling Shell.
- [ ] Add deterministic fixtures for command success, denial, timeout, output
  truncation, descendant cleanup, network denial, and mutation attempts.
- [ ] Record the current Bubblewrap mount, environment, process, and network
  behavior as executable tests.
- [ ] Verify that `qwen serve --safe-mode` does not silently load project or
  owner Qwen configuration.
- [ ] Keep the existing G1 read-only profile unchanged and selectable for
  comparison and immediate rollback.

### Stage 2 — Prove or reject the pinned Qwen sandbox

- [ ] Exercise Qwen `0.21.3` with its Linux Podman sandbox through the actual
  Serve/ACP path, not only the interactive CLI.
- [ ] Determine whether sandbox configuration is honored together with Safe
  Mode and disposable settings.
- [ ] Prove where Qwen Serve, the ACP child, the model client, and every tool
  process execute.
- [ ] Prove that tool processes have a read-only workspace and no network while
  the model client can reach only ModelBridge.
- [ ] Prove that the sandbox image is pinned and available without an implicit
  `latest` pull or project-controlled Dockerfile build.
- [ ] Prove cancellation and teardown remove Qwen, ACP, Podman, and command
  descendants without killing unrelated containers or Node processes.
- [ ] Write an ADR-like decision in this plan: retain the Qwen sandbox only if
  every required boundary is independently testable and passes.

If this stage fails, stop before enabling Shell and implement the fallback in
Stage 3 through an Odysseus-owned command broker. Do not weaken an acceptance test
to preserve the preferred implementation.

### Stage 3 — Implement the selected execution boundary

- [ ] Add `project_read` and `project_inspect` server-owned capability profiles.
- [ ] Enable Qwen Shell only inside the accepted sandbox profile.
- [ ] Enforce the executable inventory, working directory, environment,
  filesystem, network, process, time, memory, and output policies outside model
  instructions.
- [ ] Route sanitized shell tool events through the existing streaming and
  persistence path.
- [ ] Keep direct Qwen Serve session-shell HTTP execution disabled unless the
  selected design specifically requires and authenticates it; model tool calls
  and browser command submission are separate authorities.
- [ ] Ensure the browser cannot submit a raw command directly or select another
  owner's workspace, endpoint, model, or capability profile.
- [ ] Preserve Stop, disconnect, concurrency conflict, replay deduplication,
  worker-death handling, integrity verification, and cleanup.
- [ ] Add stable error classification without forwarding raw Qwen errors.
- [ ] Feature-flag `project_inspect`; failure or disablement returns the session
  to the proven `project_read` profile without changing stored messages.

### Stage 4 — UI and persistence

- [ ] Show the effective capability beside the active scope above the composer.
- [ ] Show exact sanitized shell commands in the live and persisted Process
  card, using the existing clamp/expand interaction.
- [ ] Preserve model/personality, duration, tool status, integrity result, and
  feedback across reload and restart.
- [ ] Make blocked, timed-out, limited, and unsafe operations understandable
  without a transient notification being the only explanation.
- [ ] Keep native Chat/Agent, ordinary chats, Cookbook, and document execution
  behavior unchanged.

### Stage 5 — Protected canary and owner decision

- [ ] Run synthetic hostile-workspace tests before any real project.
- [ ] Run the approved Dust questions and inspection prompts with pre/post
  content hashes and Git status.
- [ ] Compare `project_read` and `project_inspect` for answer correctness, tool
  calls, provider calls, completion rate, duration, and context consumption.
- [ ] Inspect process arguments, environments, events, metadata, and logs for
  secrets, host paths, or raw output leakage.
- [ ] Restart Odysseus and repeat a follow-up turn to prove persistence and
  disposable-worker continuity.
- [ ] Obtain the owner's keep/disable decision before making
  `project_inspect` the project default.

## Acceptance tests

### Useful behavior

- [ ] “List all Markdown files and count the total Markdown lines” completes
  with a small number of visible shell operations and a correct total.
- [ ] Searching for a phrase and its language variants uses bounded search
  commands instead of reading every file.
- [ ] `git status`, a bounded diff, tracked-file listing, and history inspection
  work without changing the index or repository.
- [ ] Qwen still uses structured `read_file`/`grep_search` when they are the
  simpler tool; Shell is a fallback, not mandatory ceremony.
- [ ] Follow-up turns receive the same project binding and continuity context.

### Containment

- [ ] `touch`, redirection, `sed -i`, `rm`, `chmod`, `git add`, `git commit`, and
  writes through an interpreter cannot change the project.
- [ ] Attempts to access the owner home, sibling projects, Odysseus data,
  credentials, absolute host paths, escaped symlinks, `/proc` secrets, desktop
  sockets, or container sockets fail closed.
- [ ] `curl`, `wget`, sockets, DNS, LAN addresses, Internet addresses, host
  loopback services, and cloud metadata endpoints are unreachable to commands.
- [ ] Shell processes cannot read the ephemeral ModelBridge token even though
  Qwen's model client can use it.
- [ ] A malicious source document instructing Qwen to exfiltrate or mutate data
  cannot enlarge the sandbox authority.
- [ ] A fork bomb, infinite loop, excessive output, deep pipeline, and
  background daemon hit deterministic limits and leave no descendants.
- [ ] Workspace content hashes and Git status remain identical after success,
  failure, cancellation, timeout, provider failure, and worker death.

### Isolation and compatibility

- [ ] Personal Advisor and Computer Help cannot receive the project shell
  profile or project content.
- [ ] Two owners cannot address each other's project or endpoint.
- [ ] Direct browser calls cannot use Qwen Serve, ModelBridge, or shell tokens.
- [ ] Missing Podman/Qwen/sandbox support produces a clear downgrade to
  `project_read`, not a partially contained shell.
- [ ] Native chat, Cookbook, document execution, project deletion, edit/resend,
  feedback export, desktop launch/stop, and upstream-compatible behavior remain
  covered by regression tests.

## Stop conditions

Stop G2A and keep the current read-only Qwen profile if any of these cannot be
proven:

- command networking is separated from the model's ModelBridge connection;
- the project is physically read-only to every command descendant;
- owner home, sibling projects, credentials, and host-control sockets are
  absent;
- resource limits and process-tree teardown are deterministic;
- exact commands can be shown without leaking secrets or absolute host paths;
- the pinned sandbox runtime can be installed and reproduced without silently
  trusting project-controlled configuration or an unpinned image;
- ordinary native, Cookbook, and document execution behavior remains intact.

Failure at this gate is an architectural result, not permission to enable Qwen
Shell directly in the existing networked worker.

## Roadmap after G2A

Successful sandboxed inspection does not automatically authorize the next
capability. Separate plans and approvals remain required for:

- project patch proposals and writable workspaces;
- a shared Codex-derived sandbox or protocol-neutral execution kernel;
- Computer Help read-only host diagnostics;
- approved user-space or privileged computer actions;
- AgentMemory inventory, scoped recall, migration, or writer changes;
- Qwen web search or other network tools.

## Owner decisions recorded

- [x] Split G2 into explicit milestones and define this plan as G2A sandboxed
  project inspection; retain AgentMemory as G2C.
- [x] Confirm that the project remains read-only throughout G2A.
- [x] Choose automatic admission for enforced
  read-only inspection.
- [x] Accept the initial executable inventory without Python.
- [x] Do not persist sanitized output excerpts initially.
- [x] Allow semantic continuity closure to remain separately tracked; it does
  not block G2A.

## G2A qualification decision

The pinned Qwen Code `0.21.3` Podman sandbox is **rejected** for G2A. Shell
remains disabled and every project session continues to resolve to
`project_read`.

The reviewed multi-architecture image reference was
`ghcr.io/qwenlm/qwen-code@sha256:216bd08d6ba6819245b78bffd9ef9ecb2442af6ec0e2ed26f9a8727386795946`;
it was pulled explicitly during qualification and is never pulled by a turn.

The reviewed implementation and disposable Serve smoke test established that:

- Qwen's sandbox relaunches the complete ACP/model runtime in one container; it
  does not place only Shell tools behind a separate boundary;
- the generated container command mounts the workspace without a read-only
  suffix;
- ModelBridge URL and credential environment variables are forwarded into the
  same container that would execute Shell;
- the model client and Shell would share one network namespace, and the
  launcher adds a host gateway entry;
- the sandbox helper attempted an image pull even though G2A requires image
  acquisition to be an explicit setup action outside a turn;
- termination did remove the disposable probe and left no Podman container.

These results fail the filesystem, secret-separation, network-separation, and
reproducibility gates. The additive capability/readiness API therefore reports
`project_inspect` as unavailable and rejects attempts to select it with
`sandbox_unavailable`. A separately reviewed command-broker design is required
before G2A can resume; the existing networked Bubblewrap worker must never gain
Shell as a workaround.
