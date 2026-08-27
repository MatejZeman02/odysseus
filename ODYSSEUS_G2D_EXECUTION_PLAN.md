# Odysseus G2D Execution Plan — Sandboxed Computer Help

**Status:** Foundation implemented; task assistance waits for semantic
continuity C1/C2

**Depends on:** G2C C1/C2 validated checkpoints and transfer semantics, plus a
command-only, rootless Podman execution broker. The rejected Qwen `0.21.3`
Podman sandbox is not reused:
Podman is only the candidate containment backend, never the Qwen runtime.

**Parent plan:** [`ODYSSEUS_IMPLEMENTATION_PLAN.md`](ODYSSEUS_IMPLEMENTATION_PLAN.md)

## Product outcome

Computer Help becomes a persistent, capable user-level computer operator. It
may diagnose the host, research solutions, download files and repositories,
create isolated development environments, and carry out reversible work in
explicit owner-writable locations. It never receives `sudo`, silently changes
the operating system, or runs unrestricted commands on the host.

The intended experience is closer to Codex than a command-confirmation loop:

- read-only diagnostics run without approval;
- commands confined to the disposable sandbox run without approval;
- reversible writes inside an owner-selected task root may run automatically
  in **Approve for me** mode after deterministic admission checks;
- authority expansion, destructive operations, secret access, and host/system
  mutation are denied or require a separate explicit approval surface;
- steps that require `sudo`, reboot, firmware UI, repartitioning, or physical
  action become persistent owner TODOs rather than repeated chat instructions.

## Current implementation and sequencing

G2D-0/1 are complete: Odysseus has a server-owned safe diagnostic snapshot and
a qualified rootless-Podman, command-only read broker. G2D-2 private incident
records and its basic UI are also present. The first D3 configuration primitive
is now present: Computer Help can register owner-selected, dedicated directories
as encrypted task-root records. Registration validates an existing current-user
folder under the home directory, rejects broad/protected credential locations,
returns no absolute path to the browser, and neither creates files nor enables
execution. These capabilities remain read-only/contained and do not grant
native host shell access.

The next Computer Help stage is intentionally **after G2C C1/C2**. A task plan,
incident summary, completed-step record, and escalation TODO must be backed by
validated, attributed semantic state—not the current heuristic checkpoint.
After that dependency passes, implement in order:

1. server-owned task roots and bounded task artifacts; **the non-executable
   task-root registry is implemented; task admission must revalidate it**;
2. scratch-only command execution plus deterministic process/output limits;
3. separately qualified filtered egress for public repositories, package
   indexes, and user-requested downloads;
4. transaction-journaled, reversible writes inside selected roots;
5. Review changes / Approve for me modes and persistent completed-step/TODO
   records.

No stage enables `sudo`, system-package installation, arbitrary host shell,
protected-path writes, raw device access, or automatic reboot.

## Why a sandbox is mandatory

Computer Help handles adversarial logs, downloaded repositories, web pages,
media tools, package metadata, and model-generated commands. Prompt policy and
an executable allowlist are not sufficient boundaries.

G2D therefore separates three authorities:

1. **Host observation broker** runs a reviewed set of read-only diagnostic
   operations and returns bounded, sanitized results.
2. **Disposable task sandbox** runs Qwen/tool commands with resource limits,
   filtered network access, a private home, and no ambient credentials.
3. **Transaction broker** performs admitted user-level writes only inside
   explicit roots and verifies the resulting filesystem changes.

The model proposes operations but cannot choose mounts, credentials, network
destinations, approval mode, or host authority. Failure of the sandbox or
broker readiness gate disables execution; native unrestricted shell is not a
fallback.

## G2D-0: concrete containment gate

The concrete candidate is **rootless Podman running a server-owned,
command-only task container**. Qwen remains outside that container and receives
only sanitized broker results. No Qwen Serve/ACP process, project mount, or
ModelBridge route is reused inside it.

Before any `computer_assist` UI or task is admitted, an automated qualification
probe must prove all of the following with the actual local Podman runtime:

- rootless Podman works with a reviewed digest-pinned image already present
  locally; no image pull occurs during a task;
- task commands have no host home, sibling project, Odysseus data, desktop,
  SSH agent, container socket, host loopback, or ModelBridge access;
- the sandbox has an isolated private home, explicit read-only inputs, and only
  its declared scratch/task-root mount writable;
- network is off for the initial scratch profile; later egress is introduced
  only through a separate brokered, destination-filtered gate;
- process, CPU, memory, file-descriptor, output, command-timeout, cancellation,
  and descendant-cleanup limits hold under hostile fixtures;
- a failed probe leaves `computer_assist` unavailable. There is no Bubblewrap
  shell fallback and no less-contained “temporary” executor.

The qualification report is persisted as evidence, not inferred from a CLI
version string. Admission also rechecks that the currently active Podman
runtime is still rootless; a historic passing report never authorizes a later
rootful configuration. Until this gate passes, G2D exposes `computer_observe`
only.

## Capability profiles

### `computer_observe`

Default profile. It may inspect server-owned snapshots of:

- OS, kernel, CPU, memory, GPU, displays, storage, mounts, and temperatures;
- running processes and bounded process metadata;
- systemd unit status and journal excerpts;
- network interfaces, routes, DNS configuration, and listening-port metadata;
- installed package inventory and available user-level tools;
- selected configuration files after path and secret filtering.

These reads require no per-command approval. The Process UI still records what
was inspected and from which safe source class.

### `computer_assist`

Adds the disposable task sandbox and filtered Internet access. It may:

- clone public repositories into sandbox scratch or an owner-selected
  development root;
- create Python virtual environments and install packages inside those venvs;
- run downloaded code inside the sandbox;
- use user-level package/cache directories created for the task;
- download ordinary files and media into an explicit output root;
- transform, inspect, and verify those outputs;
- make reversible user-level configuration changes through the transaction
  broker.

It may not use `sudo`, `su`, `pkexec`, D-Bus system mutation, raw devices,
mount operations, kernel interfaces, container sockets, SSH agents, browser
profiles, password stores, or arbitrary host filesystem writes.

## Approval model

Computer Help exposes two owner choices:

- **Review changes:** read-only and sandbox scratch work are automatic; every
  transaction into an owner directory is reviewed as one operation bundle.
- **Approve for me:** read-only, sandbox work, downloads, venv changes, and
  reversible writes inside the task's preselected roots run automatically
  after policy checks and are shown live in Process.

`Approve for me` is not blanket host approval. It is introduced only after the
observation, incident, scratch, and reviewed-transaction canaries pass. Every
admitted operation must
still satisfy all of the following:

- destination is inside a server-resolved task root;
- no symlink or traversal escape exists;
- the operation does not request privilege escalation or protected paths;
- overwrites have a recorded preimage and rollback journal;
- command, network, process, output, CPU, memory, and time limits apply;
- post-operation verification matches the declared result;
- failure or cancellation tears down every descendant process.

New roots, destructive deletion, replacement of unrelated existing files,
credential access, and expansion from user to system scope require explicit
review. Commands denied by policy cannot be converted into approval by the
model or by text contained in a downloaded repository.

## Filesystem and network boundary

Each task receives:

- a fresh private home and temporary directory;
- read-only access to explicitly selected input files or scripts;
- read/write access only to declared task roots such as a new checkout,
  dedicated venv, or `~/Music` output directory;
- no general home mount and no sibling task/project access;
- scrubbed Git, SSH, cloud, proxy, desktop, package-manager, and agent
  credentials;
- resource and process limits with deterministic teardown.

Network access is brokered and logged by destination class. HTTP(S), Git, and
media downloads are available where needed; LAN, metadata services, arbitrary
Unix sockets, inbound listeners, and the model bridge are unavailable to tool
processes. Downloads are size-limited, type-checked, and saved under unique
names unless an overwrite is separately admitted. G2D does not bypass DRM,
paywalls, authentication, or site restrictions.

## Example: finding and downloading a song

For a request such as:

> “I always knew you held my heart” is part of a song I heard; can you download
> it for me?

Computer Help may:

1. search the web and present the likely song/source;
2. inspect whether `yt-dlp`, `ffmpeg`, or an owner script already exists;
3. mount a selected existing script read-only, or create a task venv and
   install `yt-dlp` there without `sudo`;
4. download from the selected public source into the approved `~/Music` root;
5. verify media type, duration, size, filename, and that no unrelated path
   changed;
6. show the saved filename and source in Process and the incident artifact.

Executing an existing script does not grant that script host authority: it
runs inside the same sandbox with only the declared Music output mount.

## Persistent incident artifact and memory

Every non-trivial Computer Help task receives an Odysseus-owned Markdown
artifact such as:

```text
computer/incidents/nvidia-black-screen.md
```

It contains structured, editable state:

- symptom and desired outcome;
- relevant verified device facts;
- current hypothesis;
- checklist with `pending`, `completed`, `failed`, and `owner action` states;
- commands/actions already attempted and their safe outcomes;
- refined next steps;
- blockers such as `sudo`, reboot, firmware, repartitioning, or physical work;
- post-reboot or final verification criteria.

When the user reports “step 3 failed with this message,” Computer Help updates
the same artifact: it preserves completed steps, records the failure, replaces
only the invalid remaining plan, and continues from the correct checkpoint.
Long pasted logs use the automatic paste-artifact capture path and are linked
from the incident instead of being copied into every turn.

Add a `DeviceProfileV1` record containing verified, non-secret, relatively
stable facts such as GPU model, driver family, OS version, filesystem layout,
and recurring constraints. The first persistence slice is complete: every
safe diagnostic refresh writes both the existing private Markdown document and
this typed, hash-linked record, and the continuity compiler injects it only
into that Computer Help home. It is explicitly labelled as a server
observation, not model-authored memory. Incident conclusions may be promoted
into this profile only when source-linked and verified. Raw logs, credentials,
entire command outputs, and transient guesses are not durable memory. This
lets a later NVIDIA problem reuse relevant machine facts without silently
merging unrelated conversations.

## Host/system operations

System installation and privileged repair are outside the automatic executor.
Computer Help may determine that a `dnf`, partitioning, bootloader, kernel,
driver, firmware, or service change is necessary, but it must place the exact
owner-run step and verification procedure in the incident artifact.

No-sudo alternatives are preferred when technically sound: venvs, user-local
applications, Flatpak, per-user systemd units, and task-local binaries. The
assistant must not pretend a user-local workaround is equivalent when the
actual fix requires system authority.

## Process UI

Reuse the shared Odysseus Process timeline with interleaved commentary and
sanitized exact operations:

```text
Inspect: NVIDIA GPU and loaded driver
Read journal: kernel · current boot · NVIDIA errors
Search web: driver regression for Fedora version
Create venv: computer/tasks/audio-download/.venv
Download: public media → Music/Artist - Song.mp3
Verify: audio file · 4m 12s · workspace unchanged outside Music
Update incident: nvidia-black-screen.md
```

The completed card collapses to its duration and outcome. The incident artifact
remains the readable source of truth for the evolving plan, including owner
steps that the executor cannot perform.

## Delivery gates

1. **G2D-0 — implement and qualify containment first.** Build the
   server-owned rootless Podman command broker, then run hostile probes against
   that actual broker. A failure ends before any Computer Help execution; it
   does not produce a weaker executor. The Codex Desktop sandbox is a product
   implementation rather than an embeddable Odysseus dependency, so the
   compatible boundary is specified, owned, and tested here.
2. **G2D-1 — observe.** After G2D-0 has a passing qualification report,
   implement the read-only host observation broker,
   `computer_observe`, Process records, and safe diagnostic summaries. This is
   the first independently useful release.
3. **G2D-2 — remember the work.** Add incident artifacts, `DeviceProfileV1`,
   restart-safe task state, and long-paste references. A failed owner step
   updates the same incident instead of restarting the conversation.
   The first persistence slice is now implemented: safe diagnostics refresh
   the private `computer/device-profile.md` artifact and a typed
   `DeviceProfileV1` compiled only into the same Computer Help home; Computer
   Help can create/revise private incident Markdown records in the existing
   Documents editor. Long-paste capture accepts the Computer Help scope. Automatic
   model-authored incident updates wait for the controlled task/turn broker;
   they must not be bolted onto the unrestricted native chat route.
4. **G2D-3 — scratch only.** Enable `computer_assist` only after G2D-0 passes,
   with no network and no owner-root writes.
5. **G2D-4 — bounded egress and transactions.** Add filtered web/Git/media
   downloads, venv/package workflows, reviewed task roots, and rollback
   journals.
6. **G2D-5 — automatic approval canary.** Enable **Approve for me** only for
   protected canaries that have passed every preceding gate. The song-download
   flow is a late end-to-end canary, not the first test.

## Acceptance tests

- Read-only diagnostics run without prompts and cannot mutate host state.
- A public repository can be cloned, inspected, and run in a bounded task
  sandbox without exposing home or credentials.
- A Python venv and dependencies can be created without touching system Python.
- The song-fragment workflow identifies a source, uses sandboxed `yt-dlp`, and
  writes only the verified media file into the approved Music root.
- Existing owner scripts execute with read-only script access and only the
  declared output root writable.
- `Approve for me` completes safe bundles without per-command questions while
  rejecting authority expansion, destructive writes, and protected paths.
- `sudo`, raw device, repartitioning, system package, firmware, and reboot work
  become precise persistent owner TODOs.
- A failed checklist step updates the same incident artifact and preserves
  completed steps across chat, reload, server restart, and reboot.
- A later NVIDIA incident receives verified relevant device facts but no
  unrelated raw transcript or secret-bearing logs.
- Prompt injection in logs, repos, filenames, web pages, and downloaded scripts
  cannot alter mounts, egress, approval mode, or transaction authority.
- Stop, disconnect, timeout, resource exhaustion, worker death, and server
  shutdown leave no descendants or partial unverified writes.

## Deferred

- unattended root/system administration;
- automatic `sudo`, partition, bootloader, kernel, firmware, and BIOS changes;
- unrestricted host shell or whole-home write access;
- background autonomous maintenance without an owner-started task;
- silent execution of credentialed downloads or private repositories;
- cross-user device-memory sharing.
