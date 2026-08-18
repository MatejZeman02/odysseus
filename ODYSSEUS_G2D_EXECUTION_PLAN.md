# Odysseus G2D Execution Plan — Sandboxed Computer Help

**Status:** Draft for owner review

**Depends on:** G2C scoped memory/artifacts and a new protocol-neutral
execution broker. The rejected Qwen `0.21.3` Podman sandbox is not reused.

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

`Approve for me` is not blanket host approval. Every admitted operation must
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

Add a `DeviceProfileV1` home brief containing verified, non-secret, relatively
stable facts such as GPU model, driver family, OS version, filesystem layout,
and recurring constraints. Incident conclusions may be promoted into this
profile only when source-linked and verified. Raw logs, credentials, entire
command outputs, and transient guesses are not durable memory. This lets a
later NVIDIA problem reuse relevant machine facts without silently merging
unrelated conversations.

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

## Implementation sequence

1. Freeze capability, task-root, network, transaction, Process, incident, and
   error contracts.
2. Build hostile sandbox/broker probes before exposing execution in the UI.
3. Implement the read-only host observation broker and `computer_observe`.
4. Add incident artifacts, `DeviceProfileV1`, restart-safe task state, and
   long-paste references.
5. Implement `computer_assist` in a protocol-neutral sandbox with scratch-only
   execution first.
6. Add filtered web/Git/download access and isolated venv/package workflows.
7. Add reviewed transaction roots, rollback journals, and **Approve for me**.
8. Run protected canaries for diagnostics, repository setup, venv creation,
   media download, cancellation, reboot handoff, and repeated incidents.

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
