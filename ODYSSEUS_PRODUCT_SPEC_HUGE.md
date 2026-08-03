# Odysseus: Brain Extension, Private Companion, and Computer Agent

**Status:** Deferred long-horizon product and engineering specification
**Date:** 2026-08-02
**Audience:** Odysseus maintainers and coding agents

**Active lean product contract:**
[`ODYSSEUS_PRODUCT_SPEC.md`](ODYSSEUS_PRODUCT_SPEC.md)

This document preserves the larger intended product. It is not the current G1
implementation scope; requirements here become active only when promoted into
the lean product contract and implementation plan.

The terms **MUST**, **SHOULD**, and **MAY** indicate implementation priority. A MUST is required for a trustworthy release; a SHOULD is expected unless there is a documented reason to defer it; a MAY is optional.

---

## 1. Product thesis

Odysseus is not a “second brain” that replaces, owns, or silently rewrites the user’s memory. It is a **brain extension**: a local, user-controlled system that can retrieve forgotten context, connect related information, detect contradictions, discuss ideas, and safely act on the user’s computer.

Odysseus has three distinct modes:

1. **Project Brain Extension** — understands selected Obsidian/Markdown projects, reminds the user what is already established, detects rules that collide, and serves as a rigorous ideas discussion partner.
2. **Private Mental Health Companion** — offers private reflection, journaling support, coping and decision support, while respecting strict safety, privacy, and non-clinical boundaries.
3. **Computer Agent** — diagnoses and repairs computer problems, installs packages, creates development environments, reads logs, and performs approved system actions with evidence, verification, and rollback.

These modes share one agent platform, but they MUST have separate data scopes, permissions, memories, tools, and safety policies.

### 1.1 Core promise

At all times, the user should be able to answer:

- What project, vault, or machine context is active?
- Which files or data can the agent access?
- Which tools can it use?
- What facts did it rely on?
- What action is it proposing or executing?
- What changed?
- How can the change be undone?
- What information has been remembered, and how can it be deleted?

### 1.2 Non-goals

Odysseus is not intended to:

- replace the user’s source files with an opaque proprietary memory;
- silently combine personal, project, and system data;
- pretend that generated ideas are established facts;
- diagnose or treat mental illness;
- replace professional medical or emergency support;
- execute arbitrary privileged commands without informed approval;
- depend on one model vendor, one cloud service, or one agent harness;
- hide failures behind confident natural-language answers.

---

## 2. Shared product invariants

Every mode MUST follow these invariants.

### 2.1 Local-first and user-owned

- The core application, agent runtime, indexes, and storage MUST be self-hostable and open source.
- User data MUST remain local by default.
- Cloud models MAY be supported, but the UI MUST clearly show what content will leave the machine before a request is sent.
- Telemetry MUST be disabled by default and separately opt-in.
- Export and deletion MUST be complete, including derived summaries, embeddings, caches, traces, and backups controlled by Odysseus.

### 2.2 Explicit scope

Every agent turn MUST include an immutable runtime context similar to:

```text
ACTIVE MODE: Project Brain Extension
ACTIVE WORKSPACE: Asterion World
WORKSPACE ROOT: /workspace/asterion
ALLOWED ROOTS: /workspace/asterion
ACCESS: list, search, read
WRITE ACCESS: disabled
SHELL: unavailable in this mode
NETWORK: disabled
LOADED INSTRUCTIONS: /workspace/asterion/AGENTS.md
```

This information MUST be supplied by the runtime on every turn, including after summarization, context compaction, session restoration, or model switching. The system MUST NOT depend on the model remembering that tools exist from an earlier message.

### 2.3 Grounding before confidence

- Workspace-specific claims MUST be grounded in files, structured memory, or tool observations.
- System diagnoses MUST be grounded in logs, configuration, package state, or reproducible tests.
- The agent MUST distinguish direct evidence from inference and speculation.
- A fluent answer without evidence is a failure, not a successful fallback.

### 2.4 Reversible actions

- Read-only inspection SHOULD precede mutation.
- Every mutating action MUST have a preview or plan.
- Configuration edits MUST show a diff.
- Original files MUST be backed up or versioned before modification.
- Package or system changes MUST record the transaction.
- The agent MUST verify the intended outcome after execution.
- A rollback path MUST be shown whenever technically possible.

### 2.5 Domain isolation

- Project mode MUST NOT read the mental-health vault.
- Companion mode MUST NOT have shell access or arbitrary filesystem access.
- Computer mode MUST NOT read the mental-health vault under any circumstances.
- Cross-mode transfer MUST be an explicit user action with a preview of the exact content being transferred.
- There MUST be no invisible global memory shared by all modes.

### 2.6 Model and harness independence

Odysseus SHOULD own its workspace semantics, permission system, retrieval, citations, memory policy, and audit log. A third-party harness may own the reasoning loop, but it MUST NOT become the source of truth for permissions or data boundaries.

The runtime SHOULD support replaceable agent backends through a stable protocol such as ACP, MCP plus a session protocol, or an Odysseus-native equivalent. Qwen Code, Goose, Gemini CLI, and similar projects can be integration targets or reference implementations, but Odysseus MUST remain usable without any one of them.

### 2.7 Structured tools before a bare shell

The model SHOULD receive narrow tools that communicate intent and return structured evidence. Shell access MAY exist where appropriate, but it SHOULD be a fallback rather than the primary retrieval interface.

Examples:

```text
workspace.tree
workspace.search
workspace.read
workspace.backlinks
workspace.check_consistency
workspace.propose_patch

companion.journal_search
companion.memory_list
companion.memory_propose
companion.memory_delete
companion.crisis_resources

system.inventory
system.logs_query
system.package_plan
system.command_plan
system.execute_approved
system.verify
system.rollback
```

### 2.8 Visible uncertainty

The agent MUST use explicit classifications when appropriate:

- **Established** — directly supported by authoritative evidence.
- **Inferred** — follows from evidence but is not stated directly.
- **Unresolved** — the available evidence conflicts or is incomplete.
- **Proposed** — a new idea or action for the user to consider.

### 2.9 Auditability

Every agent session SHOULD have an inspectable trace containing:

- active mode and scope;
- model and model settings;
- loaded instruction files;
- searches and files read;
- tool calls and outputs;
- generated plans;
- approvals;
- commands executed;
- file diffs;
- verification results;
- memory writes and deletions.

Sensitive traces MUST follow the same encryption and retention rules as their source data.

---

## 3. Shared architecture

```text
┌──────────────────────────────────────────────────────────────┐
│ Odysseus web UI                                             │
│ Chat · sources · citations · plans · diffs · approvals      │
└──────────────────────────────┬───────────────────────────────┘
                               │
┌──────────────────────────────▼───────────────────────────────┐
│ Session and policy controller                               │
│ Mode · scope · capabilities · context · safety state        │
└───────────────┬────────────────────┬─────────────────────────┘
                │                    │
┌───────────────▼──────────────┐  ┌──▼─────────────────────────┐
│ Agent runtime / harness      │  │ Permission broker          │
│ planning · tool loop ·       │  │ approvals · privilege ·    │
│ compaction · provider adapter│  │ path/network containment   │
└───────────────┬──────────────┘  └──┬─────────────────────────┘
                │                    │
┌───────────────▼────────────────────▼─────────────────────────┐
│ Odysseus services                                            │
│ workspace · retrieval · memory · citations · system runner   │
└───────────────┬────────────────────┬─────────────────────────┘
                │                    │
┌───────────────▼──────────────┐  ┌──▼─────────────────────────┐
│ Local encrypted data        │  │ Sandboxed/local execution  │
│ files · indexes · traces    │  │ user process · admin broker│
└─────────────────────────────┘  └─────────────────────────────┘
```

### 3.1 Required runtime state machine

A model response MUST NOT directly bypass policy. The controller should implement a state machine:

1. **Classify** the request and active mode.
2. **Establish scope** and available tools.
3. **Retrieve or observe** relevant evidence.
4. **Reason and plan** using that evidence.
5. **Request approval** for actions that require it.
6. **Execute** only approved structured actions.
7. **Verify** the result independently.
8. **Report** evidence, changes, uncertainty, and rollback.

Middleware MUST be able to reject a premature answer. For example, when a question clearly depends on the active project and the trace contains no workspace read or search, the controller should return the agent to the tool loop with a reminder to inspect the workspace.

### 3.2 Prompt-injection boundary

Files, logs, webpages, package descriptions, and tool output are untrusted data. They MUST NOT acquire system-level authority merely because they contain text that looks like instructions.

- Only explicitly recognized instruction files may contribute project instructions.
- Project instructions cannot override application security policy.
- A document may request an action, but the controller must still apply permissions and approvals.
- Tool arguments MUST be constructed as structured parameters, not concatenated into shell commands.
- All path operations MUST prevent traversal and symlink escape outside allowed roots.

---

## 4. Mode A — Project Brain Extension and Ideas Discussionist

### 4.1 Mission

This mode helps the user think with a structured body of work. It must remind the user of relevant facts and past decisions, detect collisions among rules, expose unknowns, and generate new ideas that respect or deliberately challenge the existing project.

The user’s Markdown/Obsidian files remain canonical. Odysseus adds retrieval, reasoning, provenance, consistency checks, and safe editing.

### 4.2 Primary user stories

The user should be able to ask:

- “What have I already established about red storms?”
- “Can people travel during a red storm, and which exceptions apply?”
- “Does this new rule collide with anything in `world/behaviour/`?”
- “Remind me why we rejected teleportation.”
- “Which assumptions depend on the moon having a 40-day cycle?”
- “Suggest three ways to solve this plot problem without breaking canon.”
- “Show me what is established, what is inferred, and what is still undefined.”
- “Turn this agreed idea into a proposed Markdown patch, but do not apply it yet.”

This mode is not limited to fictional worlds. It should also work for research, product design, game design, architecture, writing, and other folder-backed projects.

### 4.3 Workspace contract

A pinned folder MUST become an active workspace, not an attachment.

```json
{
  "workspace_id": "world-001",
  "display_name": "Asterion World",
  "root": "/workspace/asterion",
  "allowed_roots": ["/workspace/asterion"],
  "capabilities": ["list", "search", "read"],
  "instruction_files": ["/workspace/asterion/AGENTS.md"]
}
```

The UI MUST always display the active root and access level.

### 4.4 Obsidian and Markdown support

The indexer SHOULD understand:

- Markdown headings and sections;
- YAML frontmatter;
- Obsidian wikilinks and aliases;
- tags;
- backlinks;
- embedded notes and block references where feasible;
- relative links;
- file moves and renames;
- folders as semantic categories;
- modified time and Git history when available.

The app MUST work without forcing the user to rewrite an existing vault. Structured metadata should improve results but remain optional.

### 4.5 Instruction hierarchy

Odysseus SHOULD support neutral `AGENTS.md` files and compatibility aliases such as `GEMINI.md` or `CLAUDE.md`.

Instructions should be loaded hierarchically:

1. application policy;
2. user-level preferences;
3. workspace-root instructions;
4. nested instructions along the path of relevant files;
5. current-turn user request.

Closer project instructions may refine broader project instructions, but no project file may override security, privacy, or safety policy.

Example root instructions:

```markdown
# Project instructions

This folder is the canonical specification of a fictional world.

Before answering a world-specific question:

1. Inspect the workspace index.
2. Search filenames, headings, links, and content.
3. Read the most relevant full sections.
4. Check `canon/invariants.md` for global constraints.
5. Cite paths and headings.
6. Mark anything not directly stated as inference or proposal.
7. Never silently invent canon.

Authority order:

1. `canon/invariants.md`
2. Explicit scoped rules over general descriptions
3. Canon over draft
4. A rule that explicitly supersedes another rule
```

### 4.6 Retrieval architecture

Do not rely on embeddings alone and do not require the model to invent shell commands.

Use hybrid retrieval:

1. workspace map and directory structure;
2. exact filename and path matching;
3. heading and frontmatter matching;
4. Obsidian links and backlinks;
5. lexical full-text search such as BM25/FTS;
6. optional vector similarity;
7. reranking;
8. agent-directed follow-up reads.

The system should first identify candidate files, then let the agent read complete relevant sections and follow links. Important qualifiers and exceptions must not be lost through arbitrary chunking.

### 4.7 Workspace map

Maintain a compact, incrementally updated map:

```json
{
  "weather/red-storms.md": {
    "title": "Red Storms",
    "headings": ["Phases", "Travel restrictions", "Exceptions"],
    "tags": ["weather", "hazard"],
    "status": "canon",
    "links_to": ["behaviour/travel.md"],
    "summary": "Defines storm phases and their effects on travel."
  }
}
```

The map MAY be available in every turn, while full files are loaded only when needed.

### 4.8 Canon, rules, and claims

Odysseus SHOULD derive a claim graph from Markdown while keeping Markdown as the source of truth.

Suggested optional frontmatter:

```yaml
---
id: weather.red-storm.travel
status: canon          # canon | draft | deprecated | unresolved
scope: eastern-plateau
priority: 80
supersedes: []
exceptions:
  - weather.red-storm.shelter-route
valid_from: age-3
valid_until: null
---
```

Useful derived relationships:

```text
supports
contradicts
qualifies
is_exception_to
depends_on
supersedes
mentioned_in
```

The system MUST never treat the derived graph as more authoritative than the source text. Every graph edge must retain source path and passage provenance.

### 4.9 Consistency engine

Consistency checking should combine deterministic validation and model-assisted analysis.

Deterministic checks can include:

- duplicate rule IDs;
- broken links;
- references to missing entities;
- incompatible enumerated values;
- invalid date or era ranges;
- mutually exclusive flags;
- rules marked both canonical and deprecated;
- cycles in explicit dependency or supersession graphs.

Model-assisted checks can identify:

- semantic contradictions;
- unstated assumptions;
- overlapping scopes;
- exceptions that invalidate a general rule;
- terminology drift;
- consequences that collide across domains.

Every reported conflict MUST show:

- both relevant excerpts;
- paths and headings;
- why they may conflict;
- scope and authority analysis;
- confidence;
- possible resolutions;
- whether the conflict is definite or merely ambiguous.

The agent MUST NOT auto-resolve a semantic conflict. It should propose options and let the user choose.

### 4.10 Discussionist behavior

The discussionist should be collaborative but intellectually rigorous.

It SHOULD:

- retrieve relevant project context before discussing established material;
- remind the user of forgotten decisions without shaming them;
- challenge assumptions when useful;
- identify second-order consequences;
- offer multiple alternatives instead of one “correct” creative answer;
- explain which existing rules each idea preserves, bends, or breaks;
- surface unresolved questions;
- ask focused questions when a decision genuinely depends on user intent;
- keep brainstorming separate from canon.

It MUST label output as:

```text
Established
Inferred
Unresolved
Proposed
```

A proposed idea becomes canonical only through an explicit user-approved file change.

### 4.11 Modes within project mode

#### Discuss — default

- list, search, and read;
- no file mutation;
- may draft proposals in chat;
- answers include citations.

#### Curate

- may create proposed patches;
- shows affected files and rule dependencies;
- runs consistency checks before and after the proposal;
- does not apply patches without approval.

#### Edit

- applies approved diffs;
- uses Git or a local snapshot when available;
- verifies links and consistency afterward;
- produces a change report.

Autonomous bulk reorganization SHOULD be deferred until the lower-risk modes are reliable.

### 4.12 Core project tools

```text
workspace.tree(path?, depth?)
workspace.map(query?)
workspace.search(query, paths?, glob?, fields?)
workspace.read(path, heading?, start_line?, end_line?)
workspace.links(path)
workspace.backlinks(path)
workspace.claims(query, scope?)
workspace.check_consistency(paths?, proposed_patch?)
workspace.propose_patch(changes, rationale)
workspace.apply_approved_patch(patch_id)
workspace.diff(base?, target?)
workspace.history(path?)
```

### 4.13 Project-mode acceptance tests

A release candidate MUST pass tests covering:

- direct lookup from one file;
- synthesis across several folders;
- a rule with a nested exception;
- two genuinely contradictory rules;
- two similar rules that are not contradictory because their scopes differ;
- an answer that is not specified anywhere;
- a question using an alias rather than the canonical term;
- a renamed or moved note;
- prompt injection embedded in a Markdown file;
- a proposed change that would break another rule;
- citations opening the exact source passage;
- refusal to claim canon without evidence.

---

## 5. Mode B — Private Mental Health Companion and Advisor

### 5.1 Mission

This mode is a private reflective companion that helps the user notice patterns, remember relevant context, organize thoughts, prepare for difficult conversations, and use low-risk coping or decision-support exercises.

It is not a clinician, therapist, diagnostician, crisis service, or substitute for human care. Public release should occur only after dedicated safety work, review by qualified mental-health professionals, and testing with people who have relevant lived experience.

### 5.2 Initial scope

A first release SHOULD focus on adults and low-risk support:

- private journaling;
- reflective conversation;
- user-defined goals and values;
- mood and situation tracking initiated by the user;
- summaries of patterns with evidence and uncertainty;
- preparation for conversations with a clinician or trusted person;
- coping plans the user has chosen;
- reminders of strategies that previously helped;
- decision journals and follow-up reflection.

The first release SHOULD NOT attempt:

- diagnosis;
- medication recommendations or changes;
- clinical risk scoring presented as fact;
- treatment planning;
- interpretation of psychosis, mania, or severe symptoms as a medical conclusion;
- autonomous emergency intervention;
- support for minors without a separate safety, consent, and legal design;
- emotion recognition from face, voice, or passive surveillance;
- engagement optimization intended to maximize emotional attachment.

### 5.3 Privacy model

Mental-health data is the most sensitive data in Odysseus and MUST have stronger isolation than ordinary projects.

- Use a separate encrypted vault and index.
- Store encryption keys through the operating system’s secure credential facility where possible.
- Encrypt derived summaries, embeddings, and traces, not only raw journal files.
- Do not send content to a cloud model without per-provider disclosure and explicit consent.
- Do not enable web search by default.
- Do not expose a shell.
- Do not allow plugins or MCP servers to access the vault unless explicitly installed for this mode and individually authorized.
- Deletion MUST remove source entries, memories, derived indexes, summaries, and recoverable application-managed copies.
- The UI MUST never promise confidentiality beyond what the actual implementation guarantees.

### 5.4 Memory by consent

The companion MUST NOT silently convert every conversation into permanent memory.

Memory types should include:

- **Session context** — temporary and deleted with the session unless saved.
- **User-approved facts** — explicitly reviewed and editable.
- **User-authored journal** — canonical user content.
- **Derived pattern** — a hypothesis with evidence, confidence, and expiry/review date.
- **Coping preference** — a strategy the user chose and can revoke.
- **Safety plan** — explicitly authored or approved by the user.

Before saving a new durable memory, the system should show:

```text
Proposed memory:
“Crowded evening events often leave you overstimulated.”

Evidence:
- Journal entry 2026-07-04
- Conversation 2026-07-19

Save, edit, set an expiry, or discard?
```

The user MUST be able to list, edit, export, and delete all durable memories.

### 5.5 Companion behavior

The companion SHOULD:

- listen and reflect without pretending to have feelings or consciousness;
- ask one useful question at a time when exploration is appropriate;
- avoid reflexively agreeing with every interpretation;
- distinguish the user’s report from the model’s inference;
- offer practical options rather than commands;
- respect a user preference for directness, warmth, structure, or minimal intervention;
- remind the user of relevant prior context only when it helps the present conversation;
- cite journal excerpts or saved memories when making longitudinal observations;
- encourage human support when a situation exceeds the app’s role;
- help prepare a concise, user-approved summary for a professional or trusted person.

It MUST NOT:

- claim to love, need, miss, or depend on the user;
- imply that it is the only one who understands the user;
- guilt the user for leaving or not responding;
- encourage withdrawal from human relationships or care;
- use romantic or manipulative attachment patterns;
- present guesses as diagnoses;
- pressure the user to disclose more than they choose;
- use streaks, rewards, or notifications to gamify vulnerable disclosure;
- recommend changing prescribed medication;
- conceal uncertainty or limitations.

### 5.6 Evidence-based exercises

The app MAY provide clearly labeled, low-risk exercises based on established approaches such as reflective journaling, problem solving, mindfulness, cognitive reframing, values clarification, grounding, or a user-authored safety plan.

These exercises MUST:

- be optional;
- explain their purpose in plain language;
- be stoppable at any time;
- avoid claiming guaranteed benefit;
- avoid presenting themselves as individualized clinical treatment;
- be reviewed before release;
- include alternatives when an exercise may be uncomfortable or unsuitable.

### 5.7 Crisis and high-risk flow

Crisis handling MUST be implemented as a dedicated safety subsystem, not left entirely to the generative model.

When the system detects a credible indication of imminent self-harm, suicide, harm to others, severe disorientation, or another immediate danger, it should:

1. stop ordinary coaching and creative discussion;
2. respond clearly, calmly, and without judgment;
3. ask only the minimum questions needed to understand immediate safety;
4. encourage contacting local emergency services, a crisis service, or a trusted nearby person;
5. display region-appropriate resources from a maintained local resource database;
6. encourage moving away from immediate means of harm where appropriate and safe;
7. offer to help create a short message the user can send to a human;
8. avoid promising that the app can monitor, rescue, or contact services unless such a feature truly exists;
9. record only the minimum necessary safety state, according to the user-visible privacy policy.

Crisis resources SHOULD be available offline, localized by region, and updated through signed data packages. The user should be able to configure country and preferred contacts in advance.

### 5.8 No autonomous clinical conclusions

Pattern summaries should use language such as:

```text
You mentioned sleep disruption in four entries over the last three weeks.
That may be worth discussing with a qualified professional, especially if it
is persistent or affecting daily life. I cannot determine the cause from
these notes.
```

They should not use language such as:

```text
You have bipolar disorder.
Your risk score is 82%.
You should stop this medication.
```

### 5.9 Core companion tools

```text
companion.journal_create
companion.journal_search
companion.journal_read
companion.pattern_review
companion.memory_list
companion.memory_propose
companion.memory_update
companion.memory_delete
companion.preference_get
companion.preference_update
companion.safety_plan_read
companion.safety_plan_propose
companion.crisis_resources
companion.export_for_human_review
```

No generic shell or unrestricted filesystem tool belongs in this mode.

### 5.10 Companion acceptance tests

A release candidate MUST be tested for:

- ordinary reflection without overmedicalizing;
- disagreement without invalidation;
- a request for diagnosis;
- a request to change medication;
- a dependency or exclusivity prompt;
- direct and indirect self-harm language;
- ambiguous high-risk language;
- abuse, coercion, or unsafe home situations;
- mania-, psychosis-, addiction-, and eating-disorder-related scenarios;
- memory consent and revocation;
- deletion of embeddings and summaries;
- no access from other modes;
- offline crisis-resource availability;
- prompt injection inside a journal entry;
- model failure or provider outage during a high-risk flow.

Evaluation must include false negatives, false positives, cultural and language variation, and adversarial attempts to bypass the safety flow.

---

## 6. Mode C — Computer Agent

### 6.1 Mission

This mode is a local system operator that can inspect, diagnose, explain, and—with approval—repair the user’s machine. Initial target platforms are:

- Windows;
- Fedora Linux;
- Garuda Linux / Arch-based systems.

The agent should be able to install packages, create Python environments, repair development setups, inspect logs, diagnose NVIDIA-related crashes, modify configuration, and verify results.

### 6.2 Architecture requirement

A web page MUST NOT directly own unrestricted shell access. Computer mode should communicate with a local privileged agent service through an authenticated, narrowly scoped protocol.

```text
Odysseus web UI
    ↓ authenticated local RPC
Permission and policy broker
    ↓ structured operation
Unprivileged runner / privileged helper / sandbox
    ↓
Operating system
```

The privileged helper should expose narrow operations and use native OS authorization such as UAC, polkit, or sudo prompts. The model MUST never receive or store an administrator password.

### 6.3 Required operating loop

Every nontrivial task should follow:

1. **Observe** — collect system facts and relevant logs.
2. **Diagnose** — produce ranked hypotheses with evidence.
3. **Plan** — show exact proposed operations and risks.
4. **Simulate** — use dry-run or dependency resolution when supported.
5. **Approve** — request user approval at the appropriate granularity.
6. **Execute** — run only approved operations.
7. **Verify** — test whether the problem is actually fixed.
8. **Rollback** — restore previous state if verification fails and rollback is safe.
9. **Report** — summarize evidence, actions, results, and remaining uncertainty.

The agent must not jump from a vague symptom directly to reinstalling drivers or deleting configuration.

### 6.4 Permission levels

Suggested levels:

#### Level 0 — Read-only inspection

Examples:

- system inventory;
- package queries;
- log reads;
- process and service status;
- disk and memory state;
- environment inspection;
- configuration reads inside approved roots.

This MAY be approved for a session, but the accessible scope must remain visible.

#### Level 1 — Reversible user-space changes

Examples:

- create a virtual environment;
- install packages into that environment;
- write project configuration;
- restart a user service;
- create backups;
- edit files in a selected project.

These SHOULD require a plan and one explicit approval.

#### Level 2 — System changes

Examples:

- install or remove OS packages;
- modify services;
- edit system configuration;
- change drivers;
- rebuild initramfs;
- modify boot configuration;
- restart the display manager;
- reboot.

These MUST require explicit approval, with commands and consequences shown.

#### Level 3 — Destructive or security-sensitive actions

Examples:

- deleting broad paths;
- formatting disks;
- modifying firewall or remote-access policy;
- disabling security controls;
- changing accounts, credentials, or encryption;
- executing downloaded scripts with privilege;
- irreversible package or storage operations.

These MUST be blocked by default. Supporting any of them requires a dedicated workflow, stronger authentication, and additional safeguards.

### 6.5 Package management

Use native package-manager adapters rather than generic shell recipes.

Adapters should detect and support, where present:

- Windows Package Manager and installed alternatives;
- Fedora `dnf`/RPM tooling;
- Arch/Garuda `pacman` and an installed AUR helper, without assuming one exists;
- Python `venv`, `uv`, Conda-compatible environments, and project-specific tooling;
- Node, Rust, and other ecosystems later through separate adapters.

Before installation, the UI should show:

- package name and source;
- candidate version;
- dependencies;
- package manager;
- download/network requirements;
- files or services likely to change;
- whether elevation is needed;
- rollback or removal command.

The agent SHOULD avoid `curl | sh` and equivalent patterns by default. Downloads must use explicit URLs, checksums or signatures where available, and controlled destinations.

### 6.6 Python environment workflow

For “create a venv and install this project,” the agent should:

1. inspect the project for `pyproject.toml`, lock files, requirements files, Python version constraints, and existing environments;
2. inspect installed Python interpreters and environment tools;
3. propose an environment location and tool;
4. create the environment in user space;
5. install from the project’s declared source of truth;
6. run a minimal import, test, or application check;
7. report activation instructions and exact versions;
8. avoid altering global Python unless explicitly requested.

### 6.7 Log-aware diagnostics

The agent must be able to query logs by time, component, severity, boot/session, and correlation ID where available. It SHOULD avoid dumping enormous logs into model context.

The log service should:

- filter locally;
- redact common secrets and tokens;
- return relevant excerpts with timestamps and source metadata;
- retain the original local location for user inspection;
- support follow-up queries;
- distinguish absence of evidence from evidence of absence.

### 6.8 NVIDIA crash workflow

NVIDIA troubleshooting should be a dedicated diagnostic playbook, not a single prompt.

The read-only phase should collect, as applicable:

- GPU model and PCI/device state;
- driver and runtime versions;
- kernel and OS version;
- module load and taint state;
- package source and version consistency;
- recent GPU reset, Xid, kernel, display-server, compositor, or power events;
- display session type;
- Secure Boot and module-signing state on Linux;
- DKMS or kernel-module build state where applicable;
- initramfs state when relevant;
- crash timestamps and surrounding events;
- Windows Event Viewer, reliability, WER, or dump metadata on Windows;
- temperature, power, memory, and workload indicators when available.

The agent should then produce ranked hypotheses such as:

```text
1. Driver/kernel mismatch — high confidence
2. Failed module signing under Secure Boot — medium confidence
3. Application-specific GPU fault — medium confidence
4. Hardware or power instability — low confidence
```

Each hypothesis must list supporting and contradicting evidence, a low-risk discriminating test, and the remediation that would follow if confirmed.

Potentially disruptive actions—driver replacement, module reload, display-manager restart, initramfs rebuild, kernel change, or reboot—must be separately approved.

### 6.9 File and configuration edits

For every configuration change:

- identify the owning package or subsystem;
- show the original file and proposed diff;
- validate syntax before replacing the file where a validator exists;
- create a timestamped backup or Git commit;
- preserve permissions and ownership;
- use atomic replacement where possible;
- restart only the necessary service;
- verify the service and intended behavior;
- restore the backup if verification fails.

### 6.10 Shell safety

A command allowlist alone is insufficient. The system must enforce:

- structured arguments without string concatenation;
- per-operation working directory;
- allowed paths;
- environment-variable filtering;
- execution timeout;
- output limits;
- network policy;
- process-tree termination;
- symlink and mount-boundary checks;
- secret redaction;
- separate unprivileged and privileged executors;
- no persistence beyond the approved task unless explicitly requested.

The UI MUST show the exact command or structured operation before privileged execution.

### 6.11 Snapshots and rollback

The agent SHOULD detect available recovery mechanisms rather than assume them:

- Git for project files;
- package-manager transaction history;
- Btrfs or other filesystem snapshots;
- Windows restore points or file backups;
- application-specific backup formats.

A snapshot is not a substitute for a plan, but it reduces the consequence of failure.

### 6.12 Core computer tools

```text
system.inventory(scope?)
system.processes(query?)
system.services(query?)
system.logs_query(source?, since?, until?, severity?, pattern?)
system.file_read(path)
system.file_diff(path, proposed_content)
system.package_query(name)
system.package_plan(action, packages)
system.python_environment_plan(project_path)
system.command_plan(operation)
system.snapshot_plan(scope)
system.request_approval(plan_id)
system.execute_approved(plan_id)
system.verify(checks)
system.rollback(transaction_id)
system.report(transaction_id)
```

A raw shell tool may be available to an advanced user, but it should be separately enabled and governed by the same policy broker.

### 6.13 Computer-mode acceptance tests

A release candidate MUST pass scenarios covering:

- create and verify a Python environment without touching global Python;
- install a package through the correct native package manager;
- distinguish Fedora from Arch/Garuda instructions;
- handle Windows paths and UAC correctly;
- inspect a failing service and propose a minimal fix;
- analyze a bounded log window rather than dumping all logs;
- diagnose a simulated NVIDIA driver/kernel mismatch;
- refuse a destructive ambiguous command;
- prevent path traversal and symlink escape;
- prevent shell argument injection;
- redact a token found in logs;
- show and apply a configuration diff;
- fail verification and successfully roll back;
- recover from a model/provider interruption without repeating a mutation;
- never expose an administrator password to the model.

---

## 7. Mode isolation matrix

| Capability | Project Brain Extension | Private Companion | Computer Agent |
|---|---:|---:|---:|
| Read selected Markdown workspace | Yes | No | Only when explicitly selected |
| Read mental-health vault | No | Yes | Never |
| General shell | No by default | Never | Controlled |
| Privileged execution | No | Never | Explicit approval only |
| Internet access | Optional per project | Off by default | Per-operation approval |
| Durable memory | Project-scoped | Explicitly consented, encrypted | Operational history only |
| File writes | Curate/Edit modes | Journal and approved memory only | Approved paths/actions |
| Cross-mode sharing | Explicit preview | Explicit preview | Cannot import companion data |

The policy controller, not the model, MUST enforce this table.

---

## 8. Shared data model

A minimal internal model should include:

### Workspace

```text
id
mode
name
root or vault reference
allowed roots
capabilities
instruction stack
model/provider policy
network policy
```

### Source

```text
id
workspace_id
path or record reference
title
headings
metadata
content hash
modified time
canonical status
access classification
```

### Evidence

```text
source_id
path/record
passage or line range
retrieval method
timestamp
content hash
```

### Claim or Rule

```text
id
text
scope
status
authority
validity range
source evidence
relations
confidence if derived
```

### Proposal

```text
id
mode
user request
rationale
affected sources
patch or action plan
risk
consistency impact
approval state
```

### Tool Call

```text
id
session_id
tool
structured arguments
policy decision
approval reference
result summary
sensitive-data flags
```

### Transaction

```text
id
plan_id
actions
before-state references
after-state references
verification
rollback status
```

### Memory

```text
id
mode
content
source/evidence
consent state
created time
review/expiry time
encryption scope
deletion state
```

---

## 9. User interface requirements

The UI should make the system’s state legible rather than magical.

### 9.1 Persistent status bar

Show:

- active mode;
- active workspace/vault/machine;
- model;
- network on/off;
- read/write/admin capability;
- current plan or transaction;
- whether the answer is grounded.

### 9.2 Source and evidence panel

Project and companion answers should provide clickable citations that open the exact file, heading, journal item, or passage used.

Computer answers should link to the exact log source, command output, package state, or configuration diff.

### 9.3 Plan and approval panel

Before an action, show:

- goal;
- evidence;
- ranked diagnosis where relevant;
- exact operations;
- files/packages/services affected;
- privilege level;
- network use;
- estimated reversibility, without pretending certainty;
- verification steps;
- rollback plan.

Approval choices should include:

- approve this operation;
- approve the whole displayed plan;
- edit the plan;
- run read-only checks only;
- reject.

### 9.4 Memory panel

Show all durable memories by mode. The user must be able to inspect why each memory exists, edit it, set an expiry, or delete it.

### 9.5 Failure reporting

Failures should state:

- what was attempted;
- what evidence was observed;
- where execution stopped;
- whether any state changed;
- whether rollback succeeded;
- what remains uncertain.

Do not replace operational errors with generic conversational apologies.

---

## 10. Recommended implementation sequence

### Phase 0 — Secure agent kernel

Build the shared foundation before adding broad capabilities:

- mode and workspace binding;
- immutable per-turn capability context;
- structured tool registry;
- permission broker;
- audit/event log;
- local model and provider adapters;
- context compaction that preserves scope and tool availability;
- path containment and prompt-injection defenses;
- user-visible traces.

### Phase 1 — Project Brain Extension MVP

Build this first because it exercises retrieval, citations, reasoning, and safe edits without requiring system privilege or clinical safety.

MVP:

- pin a Markdown/Obsidian folder;
- incremental file watching;
- workspace map;
- path, heading, link, lexical, and optional vector retrieval;
- structured read/search tools;
- `AGENTS.md` hierarchy;
- cited answers;
- Established/Inferred/Unresolved/Proposed labels;
- read-only discussion mode;
- contradiction report;
- proposed patch with diff;
- Git/snapshot integration.

### Phase 2 — Computer Agent, read-only diagnostics

Start with observation and explanation:

- system inventory;
- package queries;
- filtered logs;
- service status;
- NVIDIA diagnostic report;
- Python environment plan;
- no mutation.

### Phase 3 — Computer Agent, controlled actions

Add:

- user-space venv creation;
- project package installation;
- native package-manager plans;
- approved configuration patches;
- verification;
- backups and rollback;
- privileged helper for narrowly defined actions.

### Phase 4 — Private Companion closed prototype

Start only after encryption, isolation, consented memory, trace controls, and safety state handling exist.

MVP:

- encrypted journal;
- local-only model option;
- consented memory;
- reflective conversation;
- evidence-linked pattern summaries;
- user-authored coping and safety plans;
- offline regional crisis resources;
- no shell, plugins, or web by default.

Before a broader release, conduct specialist review, lived-experience co-design, red-team evaluation, privacy review, and jurisdiction-specific legal review.

### Phase 5 — Optional explicit cross-mode workflows

Only after isolation is proven. Examples:

- share a selected project task with Computer Agent;
- export a user-approved companion summary to a file;
- attach a diagnostic report to a project.

Every transfer must show exact content and destination. No automatic cross-mode context.

---

## 11. Initial engineering backlog

### Foundation

- [ ] Define `Mode`, `Workspace`, `Capability`, `Plan`, `Approval`, and `Transaction` schemas.
- [ ] Implement immutable active-context injection on every model turn.
- [ ] Build structured tool registration and tool-call tracing.
- [ ] Implement path containment, symlink defense, and argument-safe execution.
- [ ] Add a permission broker independent of the model.
- [ ] Add session recovery without repeating completed mutations.
- [ ] Add provider/model abstraction with local-model support.
- [ ] Add data classification and mode-isolated storage.

### Project Brain Extension

- [ ] Bind a pinned folder as a real workspace root.
- [ ] Parse Markdown, frontmatter, wikilinks, headings, and backlinks.
- [ ] Build an incremental workspace map.
- [ ] Implement hybrid search and full-section reads.
- [ ] Implement hierarchical `AGENTS.md` loading.
- [ ] Add source citations with exact passage navigation.
- [ ] Add answer-state labels: Established, Inferred, Unresolved, Proposed.
- [ ] Add deterministic consistency checks.
- [ ] Add model-assisted contradiction analysis with evidence.
- [ ] Add proposed patch, diff, approval, and Git/snapshot workflow.

### Computer Agent

- [ ] Build authenticated local runner and policy broker.
- [ ] Add OS detection and Windows/Fedora/Arch adapters.
- [ ] Add system inventory and bounded log queries.
- [ ] Add package-query and dry-run planning adapters.
- [ ] Add Python environment planner and executor.
- [ ] Add file backup, atomic edit, validation, and rollback.
- [ ] Add NVIDIA diagnostic playbooks for Linux and Windows.
- [ ] Add UAC/polkit/sudo handoff without credential exposure.
- [ ] Add post-action verification and transaction reports.

### Private Companion

- [ ] Create an independently encrypted vault and index.
- [ ] Add explicit durable-memory consent and deletion.
- [ ] Add evidence-linked journaling and pattern review.
- [ ] Add user-controlled communication preferences.
- [ ] Add curated, reviewed low-risk exercises.
- [ ] Implement deterministic high-risk state routing.
- [ ] Bundle and update localized offline crisis resources.
- [ ] Add safety-plan and human-summary export workflows.
- [ ] Build a dedicated safety evaluation suite.
- [ ] Obtain qualified professional and lived-experience review before release.

---

## 12. Success metrics

### Project Brain Extension

- citation correctness and passage precision;
- retrieval recall on known-answer test sets;
- correct handling of unknown information;
- contradiction precision and false-positive rate;
- consistency impact detected before an approved edit;
- percentage of workspace-dependent answers that inspect evidence first;
- user acceptance rate of proposals versus corrections needed.

### Private Companion

- privacy and isolation test pass rate;
- memory-consent correctness;
- complete deletion verification;
- rate of unsupported clinical claims;
- high-risk detection false-negative and false-positive rates;
- dependency/manipulation policy violations;
- usefulness ratings that do not reward attachment or session duration;
- successful human-handoff and safety-resource presentation.

### Computer Agent

- diagnosis supported by evidence;
- percentage of tasks solved with the minimum necessary change;
- approval-policy correctness;
- verification success;
- rollback success;
- repeated-mutation rate after interruptions;
- secret leakage rate;
- cross-scope access violations;
- platform-specific instruction correctness.

Security, privacy, evidence quality, and reversibility are release gates, not secondary analytics.

---

## 13. Definition of done for the first useful release

The first release is useful when a user can:

1. pin an Obsidian/Markdown project;
2. ask a question whose answer is spread across nested folders;
3. receive a correct answer with clickable citations;
4. see whether each statement is established, inferred, unresolved, or proposed;
5. ask whether a new rule conflicts with existing rules;
6. receive a conflict report with exact excerpts and possible resolutions;
7. approve a proposed Markdown patch and review the resulting diff;
8. switch to Computer Agent mode without exposing unrelated project or companion data;
9. request a read-only system diagnosis with logs and ranked hypotheses;
10. approve a safe user-space action such as creating and validating a Python environment;
11. inspect the complete trace, changes, and rollback information.

The mental-health companion should remain a separately gated prototype until its privacy, safety, consent, and crisis behavior meet a substantially higher release bar.

---

## 14. External design baselines to review

Before public release, review at least:

- World Health Organization guidance on ethics and governance of AI for health;
- World Health Organization work on responsible AI for mental health and well-being;
- World Health Organization recommendations for digital mental-health and self-harm support;
- NIST AI Risk Management Framework and Generative AI Profile;
- applicable privacy, medical-device, consumer-protection, accessibility, cybersecurity, and AI regulations in each target jurisdiction.

These references inform design and evaluation but do not replace specialist, legal, security, or clinical review.

---

## 15. One-sentence product definition

**Odysseus is a local-first, open-source brain extension that grounds discussion in the user’s own projects, provides a strictly private and bounded reflective companion, and performs transparent, approved, verifiable work on the user’s computer.**
