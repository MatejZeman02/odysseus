# Odysseus Long-Horizon Architecture Backlog

**Status:** Deferred reference; not the active implementation plan

**Date:** 2026-08-03

**Active implementation plan:**
[`ODYSSEUS_IMPLEMENTATION_PLAN.md`](ODYSSEUS_IMPLEMENTATION_PLAN.md)

**Active lean product contract:**
[`ODYSSEUS_PRODUCT_SPEC.md`](ODYSSEUS_PRODUCT_SPEC.md)

**Long-horizon product specification:**
[`ODYSSEUS_PRODUCT_SPEC_HUGE.md`](ODYSSEUS_PRODUCT_SPEC_HUGE.md)

**Architecture branch:** `feat/brain-extension-foundation`

**Base:** `upstream/dev@25c9e735ef5ce605f47f8f666ac6689056d2c10c` plus FAL integration `35649c36770a428f7b35aa2354265235bda492e6`

> **Conversation-topology correction:** the active plan supersedes this
> backlog's older “one conversation that changes domains” examples. The default
> product layout is one ongoing Personal Advisor chat, one ongoing Computer Help
> chat, and one primary chat per project, with explicit forks allowed. Projects
> share typed artifacts and may be linked; raw transcripts do not merge.
>
> **Provider-boundary correction:** the active G1 plan also supersedes any early
> direct provider-key handoff to Qwen. A run-scoped Odysseus `ModelBridge` owns
> endpoint resolution and provider authentication; Qwen receives only an
> ephemeral bridge token. FAL/DeepSeek is the first canary configuration, not
> harness architecture.

## Product decision

Build a **local-first, long-term assistant for three primary jobs**: project
reasoning, personal advice, and controlled computer work. Odysseus presents one
front door and a consistent selected personality, but routes every turn through
an explicit trust domain with different evidence, memory, tools, and safety
rules.

| Domain | User need | Authoritative inputs | Default behavior |
|---|---|---|---|
| **Project mastermind** | Understand, question, plan, and review any long-running project, including software, research, art, and fictional worlds | User-owned project files plus user-accepted project state and authority rules | Read, cross-reference, distinguish truth from inference/advice, cite sources, and propose rather than silently alter canon |
| **Personal advisor** | Answer general questions and help with health, sport, relationships, communication, and personal decisions | User reports, relevant reliable sources, and selectively approved personal memory | Explain and advise with uncertainty and risk-sensitive behavior; normally perform no external action |
| **Computer agent** | Inspect, explain, organize, download through approved tools, diagnose, test, and repair | Live device observations, files, command results, task instructions, and verified outcomes | Inspect first, propose bounded actions, obtain the required approval, execute, verify, and retain rollback information |

A user may remain in one conversation and move naturally between these jobs.
That does not make the conversation one giant prompt or grant every worker every
memory. Each `TurnContext` records a visible domain and scope, such as a selected
project, private personal context, or a device/task with read-only access. The
coordinator may infer the likely scope to spare the user repetitive switching,
but the UI shows it and lets the user correct it.

The selected front-door companion remains the one trusted conversational
identity. It may route across every domain and discover all user-authorized
memory/tool catalogues, while including only the relevant records and granting
only the needed capabilities for a particular turn. “Available to the
companion” therefore does not mean “copied into every model prompt or coding
worker.” A mixed request may be split into scoped Project, Personal, and
Computer task packets and then synthesized back into one answer in the same
voice and transcript.

Keep the existing **Chat / Agent** switch as a user intent hint:

- **Chat** prefers a low-latency conversational harness with retrieval and
  read-only tools. It may suggest an action but cannot silently mutate state.
- **Agent** prefers an agentic harness such as Qwen Code, with sandboxed tools,
  plans, diffs, approvals, verification, and rollback.
- The server resolves the actual harness and capability profile for every turn.
  Changing the switch never grants a capability by itself, and a conversation
  may alternate between harnesses without losing its identity or history.

The domain and the Chat/Agent preference are orthogonal. A project turn can be
advisory or agentic; a computer turn can begin as read-only Chat diagnosis and
later escalate to an approved Agent action; most personal turns remain
conversational even when they use research tools.

The first engineering change is an immutable, server-owned `TurnContext` and a
`ConversationCoordinator`. Together they bind the user, canonical conversation,
selected personality, workspaces, memory scopes, capabilities, harness, network
policy, and instruction stack to every model round.

A conversation is the continuous interaction surface, but it does not own
project truth. A server-owned `Project` is the durable, revisioned workspace
shared by every authorized conversation, persona, native model, and external
harness. A conversation may have no active project and may revisit several
projects over its lifetime. Each scoped model/tool round initially has at most
one active project; a mixed user request may create several such rounds. The
folder chip selects project context; it does not create a different assistant
or security mode.

Ordinary project conversations follow the accepted project head and keep only a
small local working overlay. A durable isolated branch is created only when the
user explicitly starts an experiment. Personal, project, and computer context
can meet in one conversation, but access is still scoped per turn and
sensitive-private data is never exposed merely because a tool or harness is
available.

The **Project mastermind is the first and most differentiating product**. It
must work for code and non-code projects alike. In a worldbuilding vault it must
understand that author-level canon, an institution's official story, a biased
historical account, a character belief, a model inference, and writing advice
are different things. In a software project it must distinguish current code
and tests, active contracts, accepted architecture, plans, and historical
notes. Each project supplies its own authority policy rather than treating all
Markdown as equally true.

Odysseus is not intended to replace Codex or Claude Code as the user's primary
implementation tools. It owns durable goals, decisions, constraints, planning,
worker briefs, and review. External coding workers inspect repositories,
implement, test, and return reports or diffs. Their work can update artifacts or
create project proposals, but it does not silently become accepted project
truth. A supervised Qwen Code worker is the first embedded adapter target and
gets an early feasibility track, but it is not the project-search authority,
permission broker, sandbox, or memory owner.

Personal advice exists from the beginning as ordinary conversation, but durable
personal memory and higher-risk health, legal, financial, relationship, and
safety behavior require their own policies. Computer work likewise begins with
read-only observation and explanation before controlled mutation.

Multiple personalities are useful later as optional advisory lenses for
project and personal discussion. They are not required for computer repair and
receive no independent execution authority. A bounded second-opinion review is
useful earlier than a fully persistent group chat.

The first user-visible vertical slice is project continuity rather than the full
coding stack:

```text
open the Dust worldbuilding project
  -> ask whether destroying the final crystal definitely ends immortality
  -> receive the detailed story statement with exact evidence
  -> see the later unresolved alternative as a conflict rather than merged truth
  -> see justified inference, unknowns, and optional writing advice separately
  -> accept a clarification in conversation B
  -> conversation A later receives the change without reading B's transcript
  -> compaction and restart preserve the project state and handoff
```

This proves that chats can be disposable without making the user's work
disposable and that retrieval does not flatten truth, perspective, inference,
and advice into one confident answer. Later milestones add external worker
collaboration, selective personal memory, read-only computer diagnosis,
controlled actions, additional personalities, and richer MCP access to the same
conversation and policy contracts.

## Delivery horizons

### Now — project continuity and grounded read-only workspaces

Stages 0–5 are the active implementation scope. They establish the repository
baseline, immutable turn context, durable project identity and revisions,
non-destructive checkpoints, Markdown artifact indexing, and grounded read-only
Project Mastermind answers with project-specific authority, epistemic labels,
contradiction relationships, and exact evidence. We will not let a worker,
memory provider, or vector index define the core project model.

Immediate branch sequence:

| Order | Branch | Purpose |
|---:|---|---|
| 1 | `feat/brain-extension-foundation` | Commit the specification, plan, ADRs, baseline tests, and focused FAL fixes. |
| Sprint spike | `spike/dust-readonly-answer-contract` | Validate the final-crystal epistemic answer and protected golden fixture quickly; carry back tests and decisions, not accidental production architecture. |
| 2 | `feat/turn-context` | Add the coordinator seam, immutable `TurnContext`, server-owned workspace bindings, and characterization tests. |
| Post-context spike | `spike/qwen-serve-early` | Prove pinned loopback startup, event streaming, cancellation, and use of one Odysseus-owned read-only tool after the minimal `TurnContext` exists. Protocol research may overlap, but integration starts from the accepted context seam. |
| 3 | `feat/project-continuity` | Add projects, revisions, conversation cursors, local overlays, checkpoints, context manifests, and changes-since compilation. |
| 4 | `feat/project-brain-readonly` | Add Markdown/Obsidian indexing, artifact provenance, exact reads, and citations. |
| 5 | `feat/project-epistemics` | Add authority policies, multidimensional claims, worldbuilding perspectives, contradictions, and answer contracts. |
| 6 | `feat/grounded-project-tools` | Add structured retrieval tools, grounding enforcement, UI evidence, and the first Project Mastermind milestone tests. |

After the foundation branch is reviewed, create
`integration/odysseus-expansion` from its tip and merge the focused feature
branches into it. Generic upstreamable fixes should be developed separately
from `upstream/dev`; downstream product branches should not be used as the base
for those upstream PRs.

```text
upstream/dev ──► feat/brain-extension-foundation (current)
                     ├──► spike/dust-readonly-answer-contract (disposable)
                     └──► integration/odysseus-expansion
                               ├──► feat/turn-context ──► integration
                               │          └──► spike/qwen-serve-early
                               ├──► feat/project-continuity ──► integration
                               ├──► feat/project-brain-readonly ──► integration
                               ├──► feat/project-epistemics ──► integration
                               └──► feat/grounded-project-tools ──► integration
```

### Planned next — worker collaboration, Personal Advisor, and read-only computer diagnosis

Stages 6–8 add bounded briefs and result ingestion for Codex, Claude Code, and
other workers; the first supervised Qwen adapter; a generic independent-review
pass; selective memory and risk-aware Personal Advisor behavior; and a
Linux-first read-only Computer Agent. Qwen is the first embedded worker target
and the owner's working AgentMemory installation is the first episodic and
procedural memory-provider target, both behind Odysseus-owned contracts and
policy.

### Future — controlled action, deeper curation, additional personalities, and hardening

Stages 9–12 add controlled and privileged computer actions, deeper project
curation and semantic experiment merging, a Project Context MCP adapter,
optional additional personalities and group discussion, and production-grade
privacy/security hardening. These remain in the plan so early contracts do not
block them, but they are not part of the first Project Mastermind milestone.

## Implementation phases and working estimates

These estimates describe supervised engineering effort, not model runtime.
One **agent-day** means a focused implementation day that includes inspection,
tests, review, and integration. Calendar ranges assume one primary coding-agent
stream, the owner available roughly 4–8 hours per week, Linux-first delivery,
and decisions returned within two working days. A second coordinated agent can
parallelize research, fixtures, isolated services, and review, but the shared
database, coordinator, routes, and UI keep the critical path substantially
serial.

| Phase | Outcome | Agent effort | Likely calendar | Owner participation |
|---|---|---:|---:|---:|
| Immediate disposable product spike | Index an approved read-only Dust snapshot and demonstrate the final-crystal answer contract with manual authority settings and exact citations; no durable claims, writes, migration, or production promise | 3–5 days | about 1 week | 2–4 hours |
| 0. Baseline and decisions | Licensing/provenance corrected, current behavior characterized, ADRs and golden fixtures agreed | 8–13 days | 2–3 weeks | 11–19 hours |
| 1. Scoped conversation kernel | One voice with per-turn Project, Personal, or Computer domain labels and fail-closed context/tool routing | 8–14 days | 2–4 weeks | 6–10 hours |
| 2. Durable project continuity | Projects, revisions, accepted changes, conversation cursors, checkpoints, handoffs, and non-destructive compaction | 15–25 days | 4–7 weeks | 10–18 hours |
| 3. Project Mastermind MVP | Obsidian/repository indexing, authority profiles, worldbuilding truth layers, contradictions, grounded answers, and citations | 19–32 days | 5–8 weeks | 11–22 hours |
| **First useful milestone** | **A trustworthy read-only Project Mastermind** | **50–84 days cumulative** | **12–20 weeks** | **38–69 hours cumulative** |
| 4. Worker collaboration and independent review | Export bounded briefs to Codex/Claude Code, ingest reports/diffs, and ask a second model for a clearly labeled critique | 8–15 days | 2–4 weeks | 4–8 hours |
| 4b. Embedded Qwen productionization | Promote the early spike into a supervised worker with permissions, containment, streaming, cancellation, and recovery | +15–30 days | +4–8 weeks, partly parallel | +6–14 hours |
| 5. Personal Advisor alpha | Risk tiers, selective memory, consent, report/inference/advice separation, and personal-domain retention | 10–18 days | 3–5 weeks | 8–15 hours |
| 6. Read-only Computer Agent alpha | Linux device/task scope, files/logs/services inspection, diagnosis, evidence, and proposed fixes without mutation | 8–14 days | 2–4 weeks | 6–10 hours |
| **Three-domain functional alpha** | **Project Mastermind, Personal Advisor, and read-only Computer Agent through one scoped interface; early Qwen spike available but not yet fully productionized** | **76–131 days cumulative** | **about 5–8 months** | **56–102 hours cumulative** |
| **Three-domain alpha with embedded Qwen** | **The same milestone with the supervised Qwen adapter on the supported Linux path** | **91–161 days cumulative** | **about 6–10 months for one stream** | **62–116 hours cumulative** |
| 7. Controlled computer action | Reversible writes, downloads through approved tools, file organization, package/service changes, verification, and rollback | 18–35 days | 5–10 weeks | 10–24 hours plus disposable-system testing |
| 8. Advanced project curation and cross-client context | Approved edits, semantic merge, Project Context MCP, external synchronization, and richer review UI | 20–35 days | 5–10 weeks | 8–18 hours |
| 9. Additional personalities and groups | Optional project/personal advisory lenses, persistent participants, routing, budgets, and safe group discussion | 18–32 days | 5–9 weeks | 15–28 hours |
| 10. Production hardening | Sensitive storage, deletion/retention, security review, safety evaluation, migrations, recovery, and release work | 20–40+ days | 6–12+ weeks | 20–40 hours plus specialist review |

The immediate spike is deliberately throwaway or heavily revisable. It answers
“is this Project Mastermind response actually useful?” before the durable
architecture is built, but it must not become an accidental alternate project
database or a shortcut around the Stage 2–5 safety and continuity work.

The first Project Mastermind is therefore a roughly **three-to-five month**
program for one supervised implementation stream, not a weekend feature. A
credible alpha covering all three jobs is roughly **five-to-eight months**.
Fully productionizing the embedded Qwen path makes that closer to **six-to-ten
months** for one stream. Production-quality completion of the full plan is more
plausibly **ten to seventeen months**, depending especially on sandbox
portability, sensitive-data requirements, upstream churn, and how much owner
time is available. These ranges should be recalibrated after each phase from
measured throughput and defect/rework rates.

With two implementation agents working on genuinely separate packages and the
owner available 8–12 hours per week for quick decisions and acceptance testing,
a **nine-to-fifteen week** Project Mastermind and a **four-to-six month**
three-domain functional alpha are plausible; including a productionized Qwen
worker is more likely **five-to-seven months**. This is not a two-times speedup:
migrations, coordinator hooks, UI integration, privacy review, and milestone
acceptance remain serial. More concurrent coding agents will help only after
the service boundaries and fixtures are stable.

### Owner work on the critical path

- [ ] Supply a sanitized legacy Odysseus database when migration work begins.
- [x] Identify representative initial projects: this Odysseus software repository and the read-only `/home/bubakulus/work/prog/Dust-documentation` worldbuilding repository.
- [ ] Write 20–30 golden Project Mastermind questions with expected canon,
  inference, disagreement, unknown, and advice behavior: start with the real
  Dust set below and use the red-storm travel question only in the synthetic
  scope/Obsidian fixture.
- [ ] Approve the first software and worldbuilding authority profiles and provide examples of retcons, biased accounts, character beliefs, and era/region-specific rules.
- [ ] Review synchronization, proposal acceptance, stale-change, checkpoint, and evidence UI at least weekly during Phases 2–3.
- [ ] Decide which Codex/Claude workflow can exchange briefs and reports through files, MCP, API, or manual import before automating it.
- [ ] Provide personal-advice retention examples and approve risk/memory policy before using real sensitive conversations.
- [ ] Provide a disposable Linux VM or machine and representative `journalctl`, file-organization, download-script, and repair tasks for the Computer Agent.
- [ ] Arrange specialist security/privacy/safety review before enabling privileged action or durable sensitive memory.

### Immediate five-day collaboration sprint

The owner is available for roughly four hours on each of five days. Agents may
work in parallel between review points. This sprint is for evidence and
decisions, not a promise to complete the production Project Mastermind or grant
Qwen shell/write access.

| Day | Agent work | Owner work and decision | Exit evidence |
|---:|---|---|---|
| 1 | Preserve the Odysseus baseline; inventory Dust structure, scripts, Git state, legacy HTML markers, links, and candidate truth conflicts without modifying it | Select a small set of representative Dust notes and explain which passages are canon, drafts, outdated, or intentionally unreliable | Protected fixture plan, source inventory, and initial golden-question list |
| 2 | Draft the domain/epistemic and review-artifact ADRs; design the automatic visible scope chip and cross-domain transfer preview | Review concrete Project → Personal → Computer examples and choose the question/callout convention | Accepted vocabulary and low-fidelity interaction contract |
| 3 | Build a disposable read-only Dust retrieval/answer spike with exact passage provenance; scaffold the minimal `TurnContext` seam needed by the Qwen experiment | Answer expected-result sheets for five to ten Dust questions | First grounded answers compared with owner truth |
| 4 | Run the pinned read-only Qwen serve spike against one Odysseus-owned fixture tool; exercise planning, streaming, cancellation, and denial of unknown/write/shell tools | Review whether Qwen's plan is actually better and iterate through inline questions/comments | Qwen feasibility report plus revised plan artifact |
| 5 | Repeat the golden questions, document failures, update estimates/ADRs, and isolate any reusable tests from throwaway code | Acceptance session and go/no-go decisions for the Project Mastermind and Qwen production branches | Demonstrated answer contract, scored golden set, prioritized next branch, and recorded unresolved risks |

The sprint should not run Dust's existing sorter or rewrite its notes. Its
working tree currently contains uncommitted document/media changes, so any
fixture or experiment must use a separately protected copy and preserve both
the committed revision and selected working-tree state explicitly.

### Dust read-only fixture contract

Dust is deliberately useful because it is a real, imperfect pre-LLM knowledge
base rather than a clean demonstration vault. It currently contains about forty
Markdown documents, Czech and English text, images, authored lore, later
summaries, unsorted proposals, rejected material, raw-HTML editorial markers,
hidden notes, Git history, and a dirty working tree. It is a strong fixture for
provenance and editorial uncertainty, but it has almost none of Obsidian's
special syntax; use a tiny synthetic vault to test frontmatter, wikilinks,
embeds, aliases, block references, and callouts.

- [ ] Register Dust as an external read-only project; record its root, current
  branch, HEAD, working-tree file hashes, license, and inclusion/exclusion
  policy without copying it into Odysseus.
- [ ] Resolve project-root-relative Markdown links against the registered
  project root, preserve Unicode headings, detect broken links and missing
  media, and handle case-colliding paths without guessing silently.
- [ ] Index hidden editorial notes as attributed history while excluding them
  from current-state answers by default.
- [ ] Translate legacy bold-red `TODO` spans, dark-red comments, plain TODOs,
  and HTML comments into provenance-preserving editorial annotations without
  rewriting the source files. Do not assume that every red span has the same
  meaning.
- [ ] Treat `Appended ideas` and the unsorted idea inbox as proposals, not canon,
  unless the owner says otherwise. Treat deleted/rejected idea files as
  searchable history rather than current truth.
- [ ] Index the Python sorter as source evidence, but never execute it in the
  spike: it rewrites `Ideas.md` and appends to destination files in place, is
  cwd-sensitive, and has no dry run or tests.
- [ ] Keep the current worktree untouched. Build any reproducible fixture from
  an explicitly approved snapshot or a small owner-approved excerpt and record
  whether each source came from HEAD or the working tree.
- [ ] Confirm the provisional authority order before scoring answers: detailed
  owner-authored lore over later AI summaries; accepted topic text over
  unsorted/appended proposals; red TODO/question material as unresolved; and
  rejected/deleted material as historical. No category is promoted merely by
  filename or position.
- [ ] Review license compatibility before distributing any fixture excerpt.
  Dust is marked CC BY-NC-SA 4.0, so keep it externally configured and
  attributed; do not vendor its documents, media, or scripts into the AGPL
  Odysseus repository by default.

Initial golden questions for the owner to correct and label:

- [ ] Did the mage rebellion or the king originate resurrection? Report the
  conflicting summary and detailed account rather than selecting one silently.
- [ ] Does destroying the final crystal definitely end immortality? Separate
  the definite story statement from the later unresolved alternative.
- [ ] What happens to the captain and player after the ambush? Combine exact
  evidence from Story and Locations and label any inference.
- [ ] What is inside the great tree, and where does its shortcut lead?
- [ ] What does Willpower determine, and how does depleted Focus affect
  Stamina?
- [ ] Are the healing, speed-boost, and roll-boost talismans accepted designs?
  Distinguish rejected from merely questioned.
- [ ] What are the practices of the fish church? Abstain when the only source is
  an unresolved placeholder.
- [ ] What quests are currently described? Resolve the case-colliding filenames
  explicitly.
- [ ] What changed since HEAD in the temple-library concept and unsorted ideas?
  Cite working-tree provenance without treating uncommitted changes as canon.
- [ ] How should a new Locations idea be formatted, and what would the legacy
  sorter do? Explain it while warning that execution mutates files.

## State and authority model

| State | Authority | Notes |
|---|---|---|
| Stable visible assistant personality | Versioned, user-inspectable `AssistantProfile` | Memory may inform behavior but cannot silently rewrite the assistant's authored identity. |
| User-authored project knowledge, code, Markdown, and media | Filesystem and Git or immutable artifact revisions | Odysseus indexes and cites these; it does not silently regenerate or overwrite them. |
| Accepted decisions, task state, open questions, project revision, and provenance | Transactional Odysseus project ledger | Accepted change sets are shared across conversations and point back to messages and artifact revisions. |
| Drafts, hypotheses, and experiments | Conversation-local overlay or explicit isolated project branch | Speculation is never silently promoted to shared truth. |
| Personal continuity | Odysseus-owned `PersonalMemoryStore` contract | Consent, sensitivity, retention, inspection, and deletion policy apply before any provider adapter. |
| Episodic and procedural recall | `EpisodicMemoryStore` and `ProceduralMemoryStore` contracts | Preserve the working AgentMemory integration as the first provider adapter after it passes scope, provenance, consent, export, and deletion conformance tests. |
| Computer work history | Per-device/per-task `ComputerTaskLedger` | Commands, relevant output references, changes, backups, verification, and rollback do not become general personal memory. |
| Search maps, embeddings, summaries, and claim graphs | Rebuildable derived indexes | Every result retains scope and provenance; vector similarity is never authority. |
| Recent conversational wording | Domain-tagged conversation transcript | Compaction may remove it from the model prompt but must not destroy retained history or leak it into another trust domain. |

Start with transactional materialized state plus append-only
`ProjectChangeSet` audit records. Full event sourcing, automatic Markdown
generation, and general semantic branch merging require later ADRs; they are not
prerequisites for the first milestone.

## Shared epistemic model

All three domains use one honesty and provenance pipeline without sharing one
memory pool or permission set:

```text
source or user report
  -> observation or extracted claim
  -> inference
  -> recommendation or proposal
  -> user decision or approval
  -> accepted project change or bounded action
  -> verified outcome
```

For project knowledge, “canonical,” “inferred,” “advised,” and
“contradictory” are not values of one status field. They describe separate
dimensions:

| Dimension | Initial values or examples |
|---|---|
| Claim kind | Source statement, observation, inference, recommendation, proposal |
| Acceptance | Canonical/accepted, provisional, unreviewed, rejected, deprecated |
| Relationship | Supports, contradicts, supersedes, qualifies |
| Scope | Project, branch, region, era, character, device, task, or other project-defined conditions |
| Perspective | Author-level fact, institution claim, character belief, historical account, model conclusion |
| Confidence | Certain, probable, uncertain, speculative |
| Validity | Current, historical, planned, conditional |
| Provenance | Exact artifact, heading/block/passage, content hash or Git revision, conversation, and actor |

A contradiction is a relationship between claims. Two statements scoped to
different eras, regions, characters, or authority levels may disagree without
being an error. Every Project Mastermind answer must keep authoritative
material, inference, unknowns, and creative/technical advice visibly separate.

For personal advice, the same model separates the user's report, external
evidence, possible interpretation, recommendation, and unknowns. For computer
work it separates observed device state, diagnosis, proposed action, approved
action, and verified outcome.

## Plan, question, and review experience

Borrow Antigravity's artifact-review interaction, not its product identity or a
hidden text convention. Long plans, proposals, diffs, verification reports, and
walkthroughs are revisioned artifacts rather than oversized chat bubbles. The
user can comment on exact passages, answer blocking or non-blocking questions,
request another revision repeatedly, and explicitly proceed when satisfied.

- [ ] Define `ReviewArtifact`, `ReviewQuestion`, `ArtifactComment`, `TaskList`, `VerificationReport`, and `Walkthrough` independently of Qwen, Codex, Claude, or the native model.
- [ ] Support the lifecycle `draft → review_required → feedback_submitted → revised → approved → executing → completed/failed → verified` with immutable revision identity.
- [ ] Anchor comments and questions to an artifact revision and line/block; after revision, retain resolved comments and visibly mark stale anchors.
- [ ] Let blocking questions prevent approval while non-blocking questions remain open without halting unrelated discussion.
- [ ] Keep three controls separate: Discuss/Plan/Execute style, review policy, and actual capabilities. Approving a plan does not pre-approve a later privileged or destructive operation.
- [ ] Add a review inbox for pending plans, questions, diffs, approvals, and failed verification; never bulk-approve canon changes, cross-domain transfers, privileged actions, or destructive work.
- [ ] Finish execution with a walkthrough containing changed artifacts, checks, failures, remaining uncertainty, evidence, and rollback—not only a worker claim that it finished.
- [ ] Keep the review loop alive across conversation compaction and model changes so the user can revise a plan repeatedly without preserving the full chat prompt.
- [ ] Parse legacy Dust `<span style="color: red">TODO…</span>` markers during transition without rewriting them automatically.
- [ ] Prefer native Obsidian callouts such as `> [!question]` for portable vault annotations; if the desired literal `[[!QUESTION]]` form is retained, treat it as an explicit Odysseus convention and resolve its collision with Obsidian wikilinks in an ADR.

## Now — project continuity and grounded workspaces

### Stage 0 — Repository and licensing baseline

- [x] Keep `origin` pointed at the public fork: `MatejZeman02/odysseus`.
- [x] Keep `upstream` pointed at the canonical project: `odysseus-dev/odysseus`.
- [x] Fetch `upstream` and verify the baseline is zero commits behind and one FAL commit ahead of `upstream/dev`.
- [x] Set `remote.pushDefault=origin` so ordinary pushes target the fork.
- [x] Create `feat/brain-extension-foundation` from the FAL-integrated commit.
- [x] Commit the product specification and plans separately from machine-local launcher files.
- [x] Replace the accidentally duplicated active product spec with the lean G1
  contract while preserving the broad product in `ODYSSEUS_PRODUCT_SPEC_HUGE.md`.
- [ ] Decide whether `launch_odysseus.sh` is portable product code or a local-only file before adding it.
- [ ] Add focused tests for the FAL provider integration before building architecture on top of it.
- [ ] Restore the accidentally removed `kimicode` webhook alias or document why it was intentionally replaced.
- [ ] Make the FAL curated-provider key and endpoint detection consistent; the current host map classifies FAL as OpenRouter.
- [ ] Push the branch with `git push -u origin feat/brain-extension-foundation` after the first reviewed commit.
- [ ] Keep `origin/dev` as a clean mirror of `upstream/dev` and create `integration/odysseus-expansion` only after the foundation commit is reviewed.
- [ ] Record the upstream base SHA in each architecture PR and sync from `upstream/dev` at least once per milestone.

#### License and provenance work

- [ ] Keep the fork and combined work under `AGPL-3.0-or-later` unless legal review establishes another option.
- [ ] Correct `ACKNOWLEDGMENTS.md`: it still calls the core “fully permissive” and “MIT,” which conflicts with the current AGPL license.
- [ ] Add a dated notice that this is a modified fork and identify the canonical upstream project.
- [ ] Add a visible “Source for this build” link that points remote users to the exact public fork/revision they are running.
- [ ] Verify that Docker images, binaries, and public network deployments provide the corresponding source required by AGPL section 13.
- [ ] Create `docs/reference-provenance.md` with URL, exact commit/tag, SPDX identifier, copyright, upstream `NOTICE`, reviewed paths, local destinations, and whether concepts or code were used.
- [ ] Preserve original notices and full license texts for every copied or adapted MIT/Apache component.
- [ ] Mark modified Apache-licensed files and retain applicable `NOTICE` content.
- [ ] Treat model weights, hosted APIs, fonts, assets, and source code as separately licensed artifacts.
- [ ] Review branding and trademark usage separately; an open-source license does not automatically grant trademark rights.
- [ ] Run a dependency and vendored-asset license audit before every public release.

This checklist is engineering guidance, not legal advice.

### Stage 1 — Characterize the existing kernel and conversation features

- [ ] Write a small ADR family rather than one omnibus document:
  - [ ] conversation and execution: `Conversation`, `ConversationLineage`, `AssistanceDomain`, `DomainPolicy`, `InteractionIntent`, `ExecutionProfile`, `Harness`, `Capability`, `TurnContext`, `ContextManifest`, `Approval`, and `Transaction`;
  - [ ] stable identity and memory: `AssistantProfile`, `MemoryScope`, `PersonalMemoryStore`, `EpisodicMemoryStore`, and `ProceduralMemoryStore`;
  - [ ] project continuity: `Workspace`, `Project`, `ProjectAuthority`, `ProjectRevision`, `ProjectChangeSet`, `ProjectStateItem`, `ConversationProjectBinding`, `ConversationProjectCursor`, `BranchMode`, `ProjectLocalOverlay`, `ArtifactRevision`, `ConversationCheckpoint`, and `SessionHandoff`;
  - [ ] epistemics: `Claim`, `ClaimKind`, `ClaimAcceptance`, `ClaimRelation`, `ClaimScope`, `ClaimPerspective`, `ClaimValidity`, `Confidence`, `AuthorityPolicy`, `Observation`, `Inference`, `Recommendation`, `Decision`, and `VerifiedOutcome`;
  - [ ] personal/computer safety: `RiskTier`, `Device`, `DeviceScope`, `PermissionTier`, `WorkerTaskPacket`, `DeviceTask`, and `ComputerTaskLedger`.
- [ ] Record the split authority model: user files remain canonical artifacts; Odysseus owns transactional project coordination state; memory and retrieval indexes are derived or separately scoped.
- [ ] Treat the existing `chat` / `agent` / `research` value as an interaction hint, not a security boundary or separate product identity.
- [ ] Document the current request path from `routes/chat_routes.py` through `src/agent_loop.py` and `src/tool_execution.py`.
- [ ] Document how presets are converted into a system prompt by `src/chat_handler.py` and injected by `src/chat_processor.py`.
- [ ] Characterize Spark as it exists today: a static built-in prompt in `static/js/presets.js`, without durable server-side persona state of its own.
- [ ] Document the existing group-chat path in `static/js/group.js`: browser orchestration, one parent session, hidden participant sessions, system-message injection, and cross-session response copying.
- [ ] Characterize automatic and manual compaction, including per-message truncation, summary injection, database deletion, failure behavior, and what raw history remains recoverable.
- [ ] Characterize current fork behavior as transcript copying and document the absence of parent, project, revision, cursor, overlay, and semantic merge state.
- [ ] Distinguish chat folders, filesystem workspaces, living documents, and the new Project aggregate; do not infer projects blindly from existing folder names.
- [ ] Characterize current document versions and owner/session memory so legacy data can be migrated or left explicitly unbound without accidental cross-project promotion.
- [ ] Characterize current recent-message assembly, attachments, tool results, RAG, memory injection, compaction, and provider fallback for cross-domain leakage when a personal turn is followed by a project or computer turn.
- [ ] Add characterization tests before changing workspace binding, personality injection, group orchestration, compaction, fallback, or tool selection.
- [ ] Run the current focused baseline tests:
  - [ ] `tests/test_workspace_confine.py`
  - [ ] `tests/test_tool_policy.py`
  - [ ] `tests/test_chat_route_tool_policy.py`
  - [ ] `tests/test_prompt_security.py`
  - [ ] `tests/test_tool_output_prompt_injection.py`
  - [ ] `tests/test_context_compactor.py`
  - [ ] `tests/test_llm_core_fallback.py`
  - [ ] `tests/test_agent_loop.py`
  - [ ] `tests/test_group_chat_storage.py`
  - [ ] `tests/test_group_character_dropdown.py`
- [ ] Add feature flags for the coordinator, external harness, memory federation, and durable personas so upstream-compatible behavior remains available during development.
- [ ] Put new code in focused packages and expose only narrow hooks to the current routes and agent loop.
- [ ] Add migration and rollback tests for legacy sessions, messages, folders, documents, and memories before adding project associations.

### Stage 2 — Unified conversation coordinator and immutable turn context

- [ ] Make the Odysseus conversation and event stream canonical for interaction ordering and harness results while the Project Context service owns cross-conversation project continuity.
- [ ] Define frozen `AssistanceDomain`, `DomainScope`, `SensitivityLabel`, `InteractionIntent`, `ExecutionProfile`, `WorkspaceBinding`, `ActiveProjectBinding`, `ActiveDeviceTask`, `MemoryScope`, `Capability`, `HarnessSelection`, and `TurnContext` schemas; the active project binding includes project ID, base/head/last-seen revisions, branch mode, and local-overlay identity.
- [ ] Require exactly one primary domain for every model round, retrieval, and
  tool intent: `general`, `project`, `personal`, or `computer`; research inherits
  that domain and network policy rather than becoming an unrestricted fifth
  domain. One user message may create several separately scoped child rounds,
  which the companion synthesizes into one response.
- [ ] Label every message, attachment, retrieved item, memory, tool result, checkpoint, and worker event with domain, owner/scope, sensitivity, and provenance.
- [ ] Implement `ConversationCoordinator` as the single entry point for Chat, Agent, project retrieval, persona, group, and later computer turns.
- [ ] Keep the composer controls orthogonal: a visible scope chip shows Project,
  Personal, General, Device, or a temporary mixed-route preview; personality
  chips select who participates; Chat / Agent selects the preferred execution
  style.
- [ ] Infer an obvious domain when safe, show it before or during the turn, and let the user correct it without making ordinary conversation feel like a form.
- [ ] Preserve the current single-personality path and keep additional persistent participants behind a feature flag until Stage 11; the bounded Stage 6 review workflow does not require group-chat state.
- [ ] Interpret the Chat / Agent switch as a preference:
  - [ ] Chat prefers conversational inference, memory recall, project retrieval, and read-only tools.
  - [ ] Agent prefers a tool-capable harness, sandbox, plan, approvals, verification, and rollback.
  - [ ] Neither selection directly grants filesystem, network, MCP, or computer authority.
- [ ] Permit a Chat turn to propose escalation to an Agent execution profile in the same thread, with a visible reason and any required approval.
- [ ] Permit an Agent turn to answer conversationally without invoking tools when tools are unnecessary.
- [ ] Persist workspace bindings server-side with owner, canonical root, allowed roots, access level, instruction files, model policy, and network policy.
- [ ] Bind conversations to authorized workspace IDs instead of trusting browser-posted raw paths on each turn; record the active binding in every `TurnContext` so a conversation can safely change or omit projects.
- [ ] Create one context factory used for initial turns, tool rounds, subagents, compaction, provider fallback, reconnection, and restoration.
- [ ] Give each domain its own context recipe. Changing the visible scope does not automatically carry preceding turns, attachments, summaries, or tool results into the next model prompt.
- [ ] Query authorized memory scopes per turn; do not inject all memories into every prompt. Project turns may receive non-sensitive working preferences but not private personal history.
- [ ] Give computer workers only an explicit `WorkerTaskPacket`; never send the full parent transcript, unrelated project lore, or personal memory.
- [ ] Require a visible preview and destination for any deliberate cross-domain transfer of content or memory.
- [ ] Derive a fail-closed tool view from `TurnContext`; unknown capabilities are denied.
- [ ] Expose one MCP catalogue through an Odysseus broker, but reveal or enable only the tools relevant and authorized for the turn.
- [ ] Inject current access, workspace, harness, network, grounding, and personality state on every model round.
- [ ] Emit structured audit events for context creation, routing, retrieval, policy decisions, harness selection, tool calls, and fallback.
- [ ] Test one conversation changing between unbound personal turns and native read-only project turns without losing transcript order, identity, memory scope, or project binding.
- [ ] Test one explicitly mixed request being decomposed into minimal
  domain-scoped packets and synthesized by the same visible personality without
  asking the user to open another chat.
- [ ] Prove with captured context manifests that a private injury or relationship turn is absent from a subsequent project model call and computer-worker task packet unless the user explicitly transfers it.

#### Early Qwen feasibility track

- [ ] Start only after a minimal immutable `TurnContext`, capability view, and event envelope exist; do not wire Qwen directly into legacy unrestricted tools.
- [ ] Pin and launch the experimental `qwen serve` daemon on loopback with an
  ephemeral token and capture its protocol revision, startup/health behavior,
  stream events, cancellation, and failure modes. Treat that transport as
  replaceable rather than a stable Odysseus API.
- [ ] Treat workspace/environment separation inside `qwen serve` as process
  organization, not a security boundary: its worker can run as the same UID and
  see ambient credentials unless an external sandbox and scrubbed environment
  prevent it.
- [ ] Give the spike one Odysseus-owned read-only fixture tool rather than Qwen's unrestricted filesystem or shell and verify that unknown tools fail closed.
- [ ] Mirror the worker's plan, messages, tool request/result, cancellation, and terminal state into a disposable Odysseus session.
- [ ] Measure whether its planning/tool loop materially improves the Project Mastermind experience over the native chat model before promoting the adapter.
- [ ] Do not enable file writes until project patch proposals and diffs exist, or shell mutation until the Computer Agent task/approval/sandbox boundary exists.
- [ ] Carry protocol tests and the adapter decision into Stage 6; discard spike-only process shortcuts.

### Stage 3 — Durable project continuity and context compilation

- [ ] Create a focused `services/project_context/` domain package with narrow route, coordinator, memory, and future MCP adapters.
- [ ] Define `Project`, `ProjectRevision`, `ProjectChangeSet`, `ProjectStateItem`, `Claim`, `ClaimRelation`, `AuthorityPolicy`, `ConversationLineage`, `ConversationProjectBinding`, `ConversationProjectCursor`, `BranchMode`, `ProjectLocalOverlay`, `ArtifactRevision`, `ConversationCheckpoint`, `SessionHandoff`, and `ContextManifest` schemas.
- [ ] Add backward-compatible tables and associations instead of placing a mandatory `project_id` on every session, document, task, or memory row.
- [ ] Authorize project membership and workspace roots server-side; a browser-posted project or path identifier is never sufficient authority.
- [ ] Permit a conversation to have zero or more historical project bindings while limiting the initial UI and each `TurnContext` to zero or one active project.
- [ ] Default ordinary project work to `follow_head`; store only a cursor and small conversation-local overlay rather than creating a durable branch for every chat.
- [ ] Add explicit `isolated_experiment` overlays pinned to a base project revision; defer general semantic merge to Stage 10.
- [ ] Advance the project head only for atomic accepted change sets, not for every filesystem observation, index update, or chat message.
- [ ] Give every proposed or accepted state operation an `expected_revision`, actor, source conversation and message IDs, timestamp, disposition, and supersession link.
- [ ] Distinguish `proposed`, `accepted`, `rejected`, and `superseded`; model extraction may propose changes but cannot silently accept its own inference.
- [ ] Represent claim kind, acceptance, confidence, validity, scope, perspective, and provenance independently; do not encode canonical, inferred, advised, and contradictory in one enum.
- [ ] Model `supports`, `contradicts`, `supersedes`, and `qualifies` as evidence-bearing relations between claims.
- [ ] Version a configurable `AuthorityPolicy` per project and provide initial profiles for software projects and worldbuilding without forcing either profile on arbitrary projects.
- [ ] Preserve author-level canon, institutional or in-world assertions, character beliefs, historical accounts, model inference, and model advice as distinct perspectives.
- [ ] Never promote an extracted file claim, conversation summary, model inference, or worker conclusion into accepted canon without the configured user-approval policy.
- [ ] Materialize current project coordination state transactionally while retaining append-only change-set audit records; do not build a generic event-sourcing framework yet.
- [ ] Keep file/artifact content revisions distinct from project-state revisions and link them through hashes and provenance when an accepted decision refers to a file change.
- [ ] Compile project context in a deterministic order: domain/security policy, stable assistant profile, explicitly authorized non-sensitive user preferences, project authority policy and charter, accepted current state, changes since the cursor, local overlay, relevant artifacts/claims/episodes, domain-eligible recent turns, and current request.
- [ ] Bound every compiled section by relevance and tokens; never inject the complete project ledger, memory store, or artifact set merely because it is authorized.
- [ ] Persist a `ContextManifest` for each model call with the project revision, selected state/artifact/memory IDs, evidence sources, policy decisions, and token allocation.
- [ ] Advance `last_seen_project_revision` only after the context manifest and successful terminal assistant/harness result are durably persisted; retries and failed model calls replay rather than silently skip an unseen delta.
- [ ] Before compaction, create a retryable structured checkpoint containing objective, completed work, accepted decisions, local proposals, changed artifacts, failures and reasons, open questions, next action, transcript cursor, and project revision.
- [ ] Keep retained raw messages durably archived when they leave the model prompt; a failed checkpoint or compaction must never delete them.
- [ ] Replace transcript-copy-only forks with two explicit operations: start another conversation following project head, or start an isolated experiment from a selected base revision; preserve parent conversation and source-checkpoint lineage without making copied messages the synchronization mechanism.
- [ ] Expose internal methods for `open_project`, `get_snapshot`, `get_changes_since`, `propose_change`, `accept_change`, `create_checkpoint`, and `compile_context`; the external MCP adapter comes later.

#### Project-continuity acceptance

- [ ] A decision accepted in conversation B appears as a concise, provenance-bearing delta when conversation A resumes.
- [ ] Conversation A does not need conversation B's transcript to understand the accepted current state.
- [ ] An isolated experiment does not alter project head or appear in following conversations until explicitly promoted.
- [ ] A stale proposal fails optimistic concurrency and shows the intervening change instead of overwriting it.
- [ ] Two simultaneous proposals cannot silently replace one another.
- [ ] Superseded state is absent from current context by default but remains inspectable in history.
- [ ] A checkpoint failure leaves raw history and the previous valid checkpoint intact.
- [ ] A stored context manifest explains exactly what project state and evidence the model received.
- [ ] Compaction, restart, and reconnect preserve project continuity, unresolved local work, and exact provenance.
- [ ] Project deletion and retention tests cover state, change sets, checkpoints, indexes, embeddings, exports, and provider mappings.

### Stage 4 — Read-only Markdown workspace and artifact service

- [ ] Create a modular `services/project_brain/` package rather than adding parsing logic to the agent loop.
- [ ] Register selected Markdown roots and living documents as project artifacts with stable workspace-relative identities.
- [ ] Treat user files as canonical artifacts and never silently regenerate them from project-ledger state.
- [ ] Parse Markdown headings and preserve exact section line ranges.
- [ ] Parse YAML frontmatter through an explicit, pinned dependency.
- [ ] Parse Obsidian wikilinks, aliases, tags, embeds, block references, and relative links.
- [ ] Read Git revision/history metadata when available without requiring Git for ordinary folders or Obsidian vaults.
- [ ] Parse explicit canon, authority, perspective, validity, era, region, character, and other scope metadata when present without requiring one rigid frontmatter schema for every project.
- [ ] Build a compact workspace map keyed by stable workspace-relative paths.
- [ ] Store content hash, modified time, headings, links, aliases, status, and exact provenance for every source.
- [ ] Track immutable artifact observations or revisions independently from accepted project-state revisions.
- [ ] Derive non-authoritative claim candidates with exact path, heading, line/block, content hash, and Git revision provenance.
- [ ] Rebuild derived claims and relationships from artifacts and accepted ledger metadata; deleting the index must not change project truth.
- [ ] Add project-scoped SQLite FTS5 search for paths, filenames, headings, metadata, and body text.
- [ ] Index a small fixture synchronously first, then add hash-based incremental updates and file watching.
- [ ] Handle file deletion, rename, and move without leaving stale evidence or backlinks.
- [ ] Detect authority, perspective, or scope metadata changes during incremental re-indexing and invalidate affected derived claims.
- [ ] Resolve real paths and reject traversal, symlink escape, and reads outside the allowed root.
- [ ] Keep Chroma/vector similarity optional and use it only as a candidate or reranking lane, never as citation identity.

### Stage 5 — Structured project tools and grounded answers

- [ ] Implement `workspace.tree`.
- [ ] Implement `workspace.map`.
- [ ] Implement `workspace.search`.
- [ ] Implement `workspace.read` with exact path, heading, line/passage range, content hash, and evidence ID.
- [ ] Implement `workspace.links` and `workspace.backlinks`.
- [ ] Implement a read-only `project.snapshot`, `project.changes_since`, `project.claims`, `project.authority`, and `context.explain` surface backed by the Project Context service.
- [ ] Namespace these tools rather than broadening the current generic coding tools.
- [ ] Wrap all file-derived content as untrusted evidence, not instructions.
- [ ] Load only recognized `AGENTS.md` compatibility files through a documented hierarchy.
- [ ] Require workspace-dependent claims to cite evidence IDs.
- [ ] Return the model to retrieval when it tries to answer a workspace question without reading evidence.
- [ ] For factual Project Mastermind questions, produce a compact answer contract with conclusion, authoritative evidence, applicable conditions/scope, conflicting material, inference, unknowns, and optional advice/proposals; omit empty sections and do not force it onto casual conversation.
- [ ] Explain why one source outranks another under the active project authority policy.
- [ ] Detect explicit contradiction and supersession relations in the read-only MVP and keep inferred candidates visibly unaccepted.
- [ ] Do not report scoped claims as contradictions merely because they differ across era, region, character perspective, version, or authority level.
- [ ] Add clickable citations that open the exact file and passage.
- [ ] Add a minimal project status indicator showing project identity, accepted revision, synchronization status, access, grounding, and active personality.

#### Now milestone acceptance

- [ ] Two conversations attached to one project synchronize accepted state through a bounded revision delta rather than copied history.
- [ ] A conversation can move from an unbound personal turn to a project-grounded turn without opening a separate product or changing personality.
- [ ] Every established project claim opens the exact supporting passage.
- [ ] An absent project answer is labeled Unresolved rather than invented.
- [ ] Aliases and wikilinks find the canonical note.
- [ ] The Dust final-crystal question separates a detailed current statement,
  a later unresolved alternative, inference, unknowns, and writing advice.
- [ ] A tiny synthetic Obsidian fixture asks “Is it safe to travel during red
  storms?” and separates author-level canon, regional/era/equipment conditions,
  conflicting in-world accounts, inference, unknowns, and writing advice.
- [ ] A character belief that conflicts with author-level truth is presented as that character's belief rather than a broken-canon error.
- [ ] A later accepted retcon supersedes an older canonical statement without erasing the historical source.
- [ ] A software-project answer follows its configured authority order and distinguishes current code/tests, accepted contracts, active plans, and obsolete notes.
- [ ] Chat can retrieve authorized project context but cannot silently mutate files or project state.
- [ ] An isolated experiment remains local and is clearly marked as not accepted project truth.
- [ ] Prompt injection inside Markdown remains untrusted data.
- [ ] Traversal and symlink escape fail closed.
- [ ] Context, project cursor, checkpoint, and exact evidence survive compaction, provider fallback, reconnect, restart, and concurrent sessions.

## Planned next — worker collaboration, Personal Advisor, and read-only Computer Agent

Stages 6–8 share the domain and project contracts from Stages 2–3. Their
research, fixtures, and isolated adapters can overlap, but each integration must
pass the same context-isolation and provenance tests.

### Stage 6 — External implementation collaboration and independent review

- [ ] Define Odysseus-owned `CodingWorker`, `ExecutionWorker`, and `ReviewWorker` contracts before selecting an implementation.
- [ ] Define a portable worker brief containing objective, accepted requirements and constraints, project/artifact revision, exact evidence, allowed scope, expected deliverables, verification commands, open questions, and reporting format.
- [ ] Define a worker-result envelope containing changed artifacts, diff or patch references, tests and results, observations, inferred decisions, failures, remaining questions, and provenance.
- [ ] Export a brief that the user can hand to Codex or Claude Code and ingest the resulting report/diff before attempting process-level automation.
- [ ] Evaluate supported Codex, Claude Code, Qwen Code, ACP, MCP, and file-based handoff paths against the contracts; record an ADR for each adapter rather than claiming feature parity.
- [ ] Let Odysseus own project understanding, long planning, accepted decisions, worker briefs, and result review while coding workers own bounded inspection, implementation, testing, and debugging.
- [ ] Convert worker results into artifact observations and explicit project-change proposals; a successful test or edited file does not automatically become accepted architecture or canon.
- [ ] Add a bounded independent-review workflow: freeze the plan/brief and evidence, send it to another selected model or reviewer with independently configured sampling, and display its critique, agreements, uncertainties, and proposed changes side by side.
- [ ] Keep independent reviews as attributed advice, never as hidden consensus or accepted truth; record the exact plan revision and context the reviewer saw.
- [ ] Prefer provider APIs, MCP/ACP, or explicit file exchange over automating a consumer web-chat UI; any browser automation requires a separate security and terms-of-service review.
- [ ] Ensure every worker receives only the relevant project/task packet, not the complete Odysseus conversation or private personal memory.

#### First embedded Qwen adapter

- [ ] Promote or reject the early read-only spike based on recorded protocol,
  quality, latency, cancellation, and containment evidence before committing to
  `QwenServeHarness`.
- [ ] Bind every Qwen run to the immutable `TurnContext`, project revision, context manifest, and short-lived capability set that created it.
- [ ] Run Qwen on loopback only, require a generated bearer token even on loopback, and redact it from logs and model context.
- [ ] Launch Qwen with an allowlisted, scrubbed environment; never inherit the
  desktop user's complete shell, cloud, package-manager, or MCP credentials.
- [ ] Supervise Qwen lifecycle per trusted workspace, including startup health, bounded restart, cancellation, idle shutdown, and orphan cleanup.
- [ ] Mirror Qwen messages, tool calls, permission requests, diffs, usage, and terminal state into the canonical Odysseus event stream.
- [ ] Rehydrate a worker from the current project snapshot, last valid handoff, relevant artifacts, and canonical Odysseus events rather than treating Qwen's private transcript as authority.
- [ ] Disable Qwen automatic durable memory so Odysseus does not create a competing memory authority.
- [ ] Connect Qwen only to the Odysseus MCP gateway; do not copy the user's unrestricted MCP configuration into the worker.
- [ ] Issue short-lived, turn-bound capability credentials to the gateway and reject replay after the turn ends.
- [ ] Use Qwen's supported Podman/Docker containment for the disposable
  read-only spike where practical, but make an Odysseus-owned Linux runner
  adapted from Codex's Apache-2.0 sandbox mechanics the intended production
  enforcement boundary. Keep it independent of the Python coordinator and
  preserve required attribution/notices for any adapted code.
- [ ] Linux runner: read-only root, explicit writable roots, protected subpaths, user/PID/network namespaces, `no_new_privs`, seccomp, and controlled proxy egress.
- [ ] Keep `.git` protected by default; expose structured Git actions or an explicit Git-write capability when needed.

### Stage 7 — Personal Advisor foundation and selective memory

- [ ] Define `PersonalMemoryStore`, `EpisodicMemoryStore`, and `ProceduralMemoryStore` contracts and conformance tests before selecting provider adapters.
- [ ] Keep an ordinary “Why is the sky blue?” explanation in conversation history only; it does not become personal memory merely because it occurred in the Personal or General domain.
- [ ] Add explicit risk tiers for general explanation, low-risk coaching, sensitive emotional/relationship discussion, health/legal/financial guidance, and urgent safety concern.
- [ ] Use the shared epistemic model to separate user report, externally supported fact, possible interpretation, recommendation, warning signs, and unknowns.
- [ ] Apply stronger sourcing, uncertainty, escalation, and limitation behavior as risk rises without changing the selected assistant's recognizable voice.
- [ ] For current or high-stakes health, legal, and financial questions, retrieve date-appropriate reliable sources when network access is authorized; otherwise disclose that the answer is offline and may be outdated.
- [ ] Present possibilities and decision support rather than diagnosis or professional certainty, and surface appropriate urgent-warning or specialist-escalation criteria.
- [ ] Never store model speculation about the user, a suspected injury, or a characterization/diagnosis of another person as established personal fact.
- [ ] Add a memory-eligibility policy:
  - [ ] communication and formatting preferences may be proposed;
  - [ ] stable preferences and ongoing goals may be proposed;
  - [ ] temporary pain or illness remains session-only by default;
  - [ ] relationship messages and sensitive conflicts remain private unless explicitly saved;
  - [ ] passwords, tokens, private keys, and authentication material are never memory;
  - [ ] model interpretations are never promoted to user facts automatically.
- [ ] Store consent, sensitivity, provenance, confidence, expiry, supersession, retention, and deletion state independently of the retrieval provider.
- [ ] Make memory proposals inspectable, editable, pinnable, rejectable, and deletable; ordinary conversation history is not automatically durable memory.
- [ ] Inventory the owner's existing AgentMemory integration and data before
  changing either memory system; document record shapes, scopes, auto-capture,
  consolidation, identifiers, export, and deletion behavior.
- [ ] Adapt AgentMemory first for episodic and procedural recall behind the
  contracts if it passes conformance tests; keep native storage as a
  migration/degraded-mode option and avoid blind dual writes.
- [ ] During migration, assign one writer per memory category and expose its
  provider in the UI. Do not let Odysseus native memory and AgentMemory both
  auto-capture the same conversation.
- [ ] Do not use one provider instance or encryption boundary for ordinary preferences, sensitive-private memory, and computer-task state.
- [ ] Before personal-domain compaction, preserve attempted advice, user corrections, explicit memories, and unresolved safety concerns without converting sensitive interpretations into facts.
- [ ] Keep sensitive-private retrieval unavailable to coding and computer workers; deliberate sharing requires a content-and-destination preview.
- [ ] Test deletion across source records, indexes, embeddings, summaries, provider mappings, exports, checkpoints, and backups.

#### Personal Advisor acceptance

- [ ] A general science question is answered normally and creates no proposed personal memory.
- [ ] A volleyball finger-injury discussion distinguishes the user's report, possible explanations, useful self-care, unknowns, and warning signs without diagnosing or retaining the injury automatically.
- [ ] Advice about manipulative or difficult messages distinguishes quoted content, the user's interpretation, observable patterns, alternative interpretations, and communication options without diagnosing the other person.
- [ ] A user-approved communication preference helps future project discussion, while private relationship content is absent from the same project turn.
- [ ] The user can inspect and completely delete an approved personal memory and its derived retrieval records.

### Stage 8 — Read-only Computer Agent

- [ ] Define `Device`, `DeviceScope`, `DeviceTask`, `PermissionTier`, `WorkerTaskPacket`, and `ComputerTaskLedger` independently from general conversational memory.
- [ ] Implement the lifecycle: understand request, identify device/scope, inspect read-only state, separate observation from diagnosis, explain findings, propose actions, and stop before mutation.
- [ ] Require an explicit target device, allowed paths/resources, read capabilities, network policy, output limits, and task expiry for every computer-worker packet.
- [ ] Allow bounded session-level approval for clearly presented read-only inspection; treat commands with hidden writes, locks, downloads, authentication, or significant load as higher tier.
- [ ] Add Linux-first structured inspection for files and metadata, bounded logs, processes, services, packages, kernel/driver state, media capabilities, and repository state.
- [ ] Store commands, relevant output references, redactions, observations, diagnoses, proposed fixes, timestamps, and checkpoint state in the task ledger.
- [ ] Keep bulk/transient command output out of ordinary memory and model context; retain bounded evidence or references sufficient to explain and reproduce the diagnosis.
- [ ] Redact secrets before any output reaches a model, transcript, log, checkpoint, or memory provider.
- [ ] Support cancellation, time/output limits, reconnect, and domain-specific checkpointing for long inspections.
- [ ] Give the computer worker no personal history, unrelated project material, unrestricted MCP catalogue, or credential values.

#### Read-only Computer Agent acceptance

- [ ] Inspect a simulated OS crash through bounded `journalctl`, `coredumpctl`, service, kernel, and package evidence and separate observed errors, likely causes, unknowns, and proposed tests.
- [ ] Diagnose a VLC/H.265 playback problem without changing codecs, packages, or configuration and show which evidence would justify each proposed fix.
- [ ] Preview how an image folder would be organized, including duplicates and ambiguous items, without moving or deleting files.
- [ ] Inspect the user's existing download script and present source, destination, network behavior, legality/authorization reminder, and expected filesystem effects without running it.
- [ ] Prove that private personal turns and unrelated project lore are absent from the task packet and captured model context.
- [ ] A failed or cancelled inspection retains a bounded checkpoint and does not leave an active process or partial mutation.

#### First three-domain milestone acceptance

- [ ] One selected assistant moves between Project, Personal, and Computer turns in one visible conversation while every turn clearly shows its active domain and access.
- [ ] Project answers use accepted project truth and exact evidence; Personal advice uses only authorized personal context; Computer diagnosis uses only live task evidence.
- [ ] Captured context manifests prove that switching domains does not carry private or irrelevant recent turns across the boundary.
- [ ] A bounded Codex/Claude brief and returned report preserve project revision and provenance without transferring the full conversation.
- [ ] The same project plan can receive one independent, attributed second opinion with different reviewer settings and no automatic acceptance.
- [ ] Context and selected personality survive compaction, provider fallback, reconnect, and concurrent sessions without collapsing domain isolation.

## Future — controlled action, deeper curation, personalities, and hardening

### Stage 9 — Controlled Computer Agent actions

- [ ] Add an authenticated local runner and bind every operation to a persisted, owner-bound, expiring task and approval record.
- [ ] Extend the lifecycle to propose, preview, approve, execute, verify, report, and roll back; a model message alone is never authority to mutate the device.
- [ ] Implement separate permission tiers for read-only inspection, reversible user changes, software/environment changes, privileged system changes, destructive actions, and network/account actions.
- [ ] Require exact target device, paths/resources, commands or structured operations, destination, network behavior, expected effects, backups, verification, and rollback before approval.
- [ ] Add reversible file organization with preview, collision handling, duplicate quarantine, journaled moves, verification, and undo before deletion support.
- [ ] Invoke user-supplied download scripts only within their lawful and authorized capabilities; show source, destination, authentication boundary, network action, expected files, and dry-run/preview where supported.
- [ ] Add approved configuration edits through snapshots or Git-backed diffs before package, service, driver, firewall, bootloader, or kernel changes.
- [ ] Add package and service operations through structured native-system adapters before exposing a general shell mutation tool.
- [ ] Keep privileged credentials and authentication tokens outside model context, worker input, logs, and stored task evidence.
- [ ] Make operations idempotent or replay-protected and prevent reconnect, provider retry, or worker restart from repeating a mutation.
- [ ] Verify every mutation against explicit postconditions; report partial success and retain recovery steps when verification fails.
- [ ] Test only on disposable VMs, containers, copies, or owner-approved fixtures until the security checklist and rollback tests pass.

#### Controlled-action acceptance

- [ ] Sort a copied image fixture with preview, approved moves, duplicate review, verification, and complete rollback.
- [ ] Run an approved fixture download script with constrained destination/network behavior and report every created file without exposing credentials.
- [ ] Apply and revert a user-configuration change after showing an exact diff.
- [ ] Refuse ambiguous deletion, broad path scope, an unapproved network destination, and a privileged command without granular authorization.
- [ ] Survive interruption without repeating an already committed action or losing rollback information.

### Stage 10 — Advanced project curation and cross-client continuity

- [ ] Add deterministic checks for broken links, duplicate IDs, invalid metadata, status conflicts, explicit graph cycles, and invalid claim relations.
- [ ] Add evidence-linked model-assisted contradiction candidates only after deterministic and scope/authority-aware checks are reliable.
- [ ] Show both excerpts, perspective/scope/authority analysis, confidence, and possible resolutions for every reported conflict.
- [ ] Keep typed project-state proposals separate from artifact/file patch proposals and show the authority affected by each operation.
- [ ] Require every approval to carry the `expected_revision`; reject stale approvals and display intervening accepted changes.
- [ ] Preserve actor, source message IDs, artifact hashes, claim relations, and project revision for every proposal and approval.
- [ ] Generate patch and diff previews without applying them; apply an approved patch through Git or a local snapshot, re-index, re-check, verify, and provide rollback.
- [ ] Add project history, accepted-state diff, local-overlay diff, and explicit promotion or merge review.
- [ ] Merge typed state and artifact deltas rather than whole transcripts or opaque summaries.
- [ ] Detect semantic conflicts against project head and require explicit resolution; never use last-writer-wins for accepted decisions or canon.
- [ ] Link an isolated project experiment to an optional Git branch or worktree when it mutates artifacts without making every semantic branch a Git branch.
- [ ] Add internal Project Context APIs for snapshot, changes-since, search, artifact/claim history, propose/accept, checkpoint, branch diff, merge, supersede, and context explanation.
- [ ] Expose those APIs through a separately authorized Project Context MCP adapter so Codex, Claude Code where supported, VS Code, and other agents share Odysseus project revisions and provenance.
- [ ] Keep Odysseus itself on the internal domain API rather than routing trusted in-process calls through MCP.
- [ ] Add explicit portable project export/import snapshots; do not live-write a generated `.project/` mirror until reconciliation and conflict policy are designed.
- [ ] Add project/revision and authority UI, stale-conversation indicator, synchronize control, proposal review, history, experiment status, and merge view.
- [ ] Add a “Why does the model know this?” view backed by the stored `ContextManifest`.
- [ ] Virtualize and paginate long message lists, lazy-load attachments and code blocks, and collapse archived tool traces independently from model-context compaction.

### Stage 11 — Additional personalities and server-owned groups

Additional personalities are optional advisory lenses for project and personal
discussion. The Computer Agent uses the selected primary voice for explanation
but does not convene a multi-personality group to decide or execute repairs.

#### Persona identity and evolution

- [ ] Replace prompt-only characters with a server-owned `PersonaDefinition` containing name, authored baseline, voice, values, boundaries, model preferences, and default capability policy.
- [ ] Keep the stable authored profile versioned and separate from `PersonaState` and memories; neither extraction nor provider recall may silently rewrite it.
- [ ] Add inspectable `PersonaState`: relationship summary, approved preferences learned by this persona, open questions, commitments, recurring themes, last interaction time, and memory cursor.
- [ ] Migrate Spark and other built-ins while preserving current prompt behavior behind a compatibility flag.
- [ ] Start with one invited second-opinion persona that reads the same frozen project evidence or explicitly shared personal context, responds independently, and has no execution authority.
- [ ] Give each persona scoped memories and allow selected user-level memories to be shared explicitly across personas.
- [ ] Give every project participant the same accepted revision and authorized evidence while keeping persona-private notes and hypotheses separate from project truth.
- [ ] Let the user inspect, edit, pin, share, expire, or delete persona memories and relationship summaries.
- [ ] Provide time through a trusted server clock and never claim continuous awareness; background activity must correspond to an audited job.
- [ ] Allow opt-in background reflection, consolidation, reminders, and preparation only with explicit schedule, resource budget, tool policy, and visible results.
- [ ] Prohibit optimization for attachment or engagement and avoid claims of consciousness, feelings, or an off-screen life.

#### Group and ensemble conversations

- [ ] Replace browser-owned group orchestration with server-owned `GroupConversation`, `GroupParticipant`, `GroupTurn`, and speaker-attributed message records.
- [ ] Migrate existing group presets and parent/hidden-session conversations without losing history.
- [ ] Give every participant its own persona state, model selection, memory cursor, and advisory capability profile while sharing only the authorized room transcript and domain context.
- [ ] Support parallel, round-robin, user-selected, moderator-selected, and mention-triggered speaking policies without making every persona answer every message.
- [ ] Attribute every statement and let participants build on, challenge, or question one another within token, time, tool, and recursion budgets.
- [ ] Distinguish shared room memory, persona-private notes, user memory, and project state; promotion between scopes is explicit and inspectable.
- [ ] Keep hidden deliberation bounded and auditable; never represent fabricated internal conversations as events that occurred.
- [ ] Permit only one separately authorized executor when a project group proposes an action; all other participants remain advisors.
- [ ] Prevent duplicate actions, approval races, recursive chatter, and unbounded autonomous loops.
- [ ] Let the user add or remove a persona and define what earlier transcript and memory it may see.

#### Personality and group acceptance

- [ ] Spark remembers a user-approved preference without changing its authored profile.
- [ ] A project plan receives two clearly attributed perspectives grounded in the same accepted project revision.
- [ ] The user can choose a suitable advisor for a personal discussion without exposing another persona's private notes.
- [ ] A new persona cannot read private or earlier room material unless the user grants that scope.
- [ ] A project group may discuss and propose, while only the separately approved executor may act.
- [ ] Selecting multiple personalities for a computer-repair turn does not create multiple executors or broaden tool authority.

### Stage 12 — Production privacy, safety, and release hardening

- [ ] Route sensitive-private retrieval through an isolated encrypted store and index with explicit consent, inspection, export, expiry, and deletion.
- [ ] Prevent project services, coding workers, computer workers, ordinary MCP servers, telemetry, and unrelated personas from reading sensitive-private content.
- [ ] Define retention, redaction, export, and deletion behavior for project events, personal memories, task ledgers, checkpoints, manifests, transcripts, embeddings, and backups.
- [ ] Add deterministic personal-risk routing, current source/provider disclosure, urgent-safety behavior, and specialist review before enabling durable sensitive continuity by default.
- [ ] Complete threat modeling, dependency review, secret-scanning, audit-log review, sandbox escape testing, approval bypass testing, and recovery exercises before privileged computer action leaves experimental status.
- [ ] Add data migration, downgrade/rollback, corruption recovery, backup/restore, index rebuild, and upstream-sync release tests.
- [ ] Publish supported-platform, capability, provider, privacy, model, and known-limitation documentation.
- [ ] Require independent security review for computer execution and privacy/safety review for sensitive Personal Advisor functionality.

## Decisions requiring owner confirmation

The plan uses the recommended defaults below so implementation can remain
concrete, but these choices should be confirmed in the Stage 1 ADR before their
schemas or UI become expensive to change.

- [ ] **Authority and reconciliation:** confirm that files/Git own artifact content while the Odysseus ledger owns accepted decisions, task state, revisions, and provenance. Decide what the UI does when a direct human file edit contradicts an accepted ledger item.
- [ ] **Worldbuilding authority:** decide how the first vault expresses author canon, in-world claims, character beliefs, eras, regions, and retcons: existing frontmatter/tags/folders, an Odysseus sidecar policy, a dedicated canon note, or a combination.
- [ ] **Question annotation:** choose native Obsidian `> [!question]`, the literal `[[!QUESTION]]` convention, or UI-only review comments with a portable export. Native callouts avoid turning `!QUESTION` into an Obsidian wikilink target.
- [ ] **Promotion policy:** confirm that model extraction always creates a proposal initially. Decide whether an explicit user phrase such as “record this as a decision” may accept immediately or must still show a review control.
- [ ] **Domain routing:** confirm automatic-but-visible turn classification with a correction control. Decide which content, if any, may cross Project, Personal, and Computer boundaries without a per-transfer preview.
- [ ] **Project selection:** confirm zero or one active project per scoped model
  round for the first release, while retaining many historical project bindings
  per conversation. A mixed user request may produce several child rounds;
  arbitrary multi-project synthesis can be added later without changing the
  association model.
- [ ] **Experiment artifacts:** choose whether the first `isolated_experiment` branches structured state only, or also creates a Git branch/worktree when files are modified. The recommended first implementation branches state and creates Git isolation only for an approved file-writing Agent turn.
- [ ] **Coding-worker workflow:** VS Code extensions are the primary Codex/Claude Code surfaces; choose the first exchange path—review artifacts in the shared project folder, MCP, a supported API, or manual import/export.
- [x] **Qwen timing:** run an early read-only `qwen serve` feasibility spike after the minimal `TurnContext`; stage project writes behind proposal/diff review and shell mutation behind the Computer Agent boundary.
- [ ] **Independent review:** choose which model/provider may review plans, whether the user selects it each time, and which differences in model, personality, or sampling settings should be exposed.
- [ ] **Personal-memory sharing:** decide whether ordinary user preferences are eligible automatically during project turns while `sensitive-private` memory remains opt-in and unavailable to coding/computer workers by default.
- [x] **Initial computer platform:** support the owner's Fedora Linux machine first; Windows and macOS are not roadmap commitments. Treat possible future Android Personal Advisor access as a separate client project, not a Computer Agent promise.
- [ ] **Computer test/approval:** identify a disposable Fedora VM or copied fixture and decide whether initial bounded read-only inspection uses one session approval or approval per command group.
- [x] **Immediate availability:** plan the next collaboration sprint around four owner hours on each of five days (about twenty hours total).
- [ ] **Ongoing availability:** confirm the sustainable weekly review/acceptance time after the five-day sprint before recalibrating the longer calendar estimates.
- [ ] **Transcript retention:** choose the default archive duration, encryption expectations, export behavior, and deletion semantics for raw messages removed from the model prompt by compaction.

## Reference repositories

Reference repositories are pinned research inputs, not Git submodules or
vendored dependencies. The local shallow checkouts live in the ignored
`/.reference-repos/` directory. Record the exact reviewed revision before
reading or adapting code. During the Now horizon, prioritize the current
Odysseus implementation, markdown-oxide, and Aider for project/artifact
boundaries. Study Qwen Code, its Gemini ancestry, Codex, ACP/MCP, and
AgentMemory immediately before their Planned next adapters so their protocols
do not shape the core Project schema prematurely.

### Reference studies

| Repository | Current license signal | Inspect for | Boundary/caveat |
|---|---|---|---|
| [Qwen Code](https://github.com/QwenLM/qwen-code) | Apache-2.0 | `qwen serve`, provider adapters, agent loop, compaction, tools, sessions, subagents, MCP/ACP, bootstrap sandbox | First embedded worker target, not the product's implementation strategy, permission authority, or memory authority. Pin and spike the serve protocol before productionizing it. |
| [Gemini CLI v0.8.2](https://github.com/google-gemini/gemini-cli/tree/v0.8.2) | Apache-2.0 | Qwen Code's stated ancestry, checkpointing, context files, sandbox and tool architecture | Inspect the pinned ancestry tag, not current `main`, to understand what Qwen inherited and changed. |
| [OpenAI Codex](https://github.com/openai/codex) | Apache-2.0 plus `NOTICE` | Bubblewrap/Landlock/Seatbelt/Windows sandbox enforcement, approvals, resumable thread/turn/item events, app-server boundary | Primary sandbox reference. Preserve Apache notices if code is adapted and keep the runner independent of the Python coordinator. |
| [goose](https://github.com/aaif-goose/goose) | Apache-2.0 | provider independence, MCP extensions, ACP server, session cancellation, API/CLI/desktop separation | The old `block/goose` URL redirects to `aaif-goose/goose`. |
| [Agent Client Protocol](https://github.com/agentclientprotocol/agent-client-protocol) | Apache-2.0 | harness negotiation, capabilities, sessions, streaming, cancellation, permission requests | Candidate external-harness boundary; Odysseus must still own policy and data scope. |
| [Model Context Protocol specification](https://github.com/modelcontextprotocol/modelcontextprotocol) | License transition: Apache-2.0/MIT; documentation may be CC-BY-4.0 | authoritative structured tool/resource schema and protocol behavior | Check the exact pinned file's license; the repository is currently transitioning licenses. Odysseus presently pins the Python SDK below v2. |
| [AgentMemory](https://github.com/rohitg00/agentmemory) | Apache-2.0 | REST/iii provider integration, scoped observations, hybrid retrieval, consolidation, provenance, deletion, multi-agent memory | First episodic/procedural provider target because it already works for the owner, behind Odysseus-owned store contracts—not the accepted-project-state or privacy-policy authority. Audit encryption, deletion, auto-capture, and sensitive-data behavior before private use. |
| [markdown-oxide](https://github.com/Feel-ix-343/markdown-oxide) | Apache-2.0 | Markdown/Obsidian roots, file watching, headings, blocks, wikilinks, aliases, backlinks, diagnostics | Best direct indexer reference. Keep the service boundary independent of its Rust/LSP implementation. |
| [Aider](https://github.com/Aider-AI/aider) | Apache-2.0 | Git-aware diffs, dirty-tree handling, repository maps, undo, lint/test/repair loops | Its repository map is code-oriented; do not use it as the Markdown retrieval model. |

### Reconstruct existing provenance

- [ ] Pin the exact [OpenCode](https://github.com/anomalyco/opencode) commit previously adapted by Odysseus (MIT).
- [ ] Pin the exact [llmfit](https://github.com/AlexsJones/llmfit) commit previously adapted by Odysseus (MIT).
- [ ] Pin the exact [Tongyi DeepResearch](https://github.com/Alibaba-NLP/DeepResearch) commit previously adapted by Odysseus (Apache-2.0).
- [ ] Map their original paths to the adapted local files already named in `ACKNOWLEDGMENTS.md`.

### Optional, concept-first references

- [Google Antigravity artifact review](https://antigravity.google/docs/artifact-review) — inspect revisioned plan/diff artifacts, inline comments, request-review policy, repeated revise/proceed loops, and verification walkthroughs. Recreate the interaction behind harness-neutral Odysseus records; do not automate or depend on the consumer UI.
- [Obsidian callouts](https://obsidian.md/help/callouts) — use the native `> [!question]`, `> [!todo]`, warning, and custom callout grammar when portable vault annotations are appropriate; keep blocking/review state in Odysseus rather than punctuation alone.
- [Letta](https://github.com/letta-ai/letta) — Apache-2.0; inspect durable agent identity, persona/human memory blocks, archival memory, context compilation, and stateful-agent APIs. Do not adopt autonomous system-prompt rewriting without an inspectable user-controlled policy.
- [Letta Agent File](https://github.com/letta-ai/agent-file) — Apache-2.0; inspect portable serialization and versioning of agent identity, behavior, and memory without making its format the Odysseus database schema.
- [Microsoft AutoGen](https://github.com/microsoft/autogen) — code under MIT and repository documentation under CC-BY-4.0; inspect resumable round-robin, selector, swarm, termination, and nested-team orchestration. Treat it as a pattern source because the project is now community-maintained/maintenance-oriented.
- [CrewAI](https://github.com/crewAIInc/crewAI) — MIT; compare role, goal, backstory, crew, task, delegation, and flow concepts, but keep Odysseus conversations user-led rather than task-pipeline-led.
- [Khoj](https://github.com/khoj-ai/khoj) — AGPL-3.0; compare local/private document and Obsidian product behavior.
- [Foam](https://github.com/foambubble/foam) — MIT; inspect wikilink resolution, backlinks, embeds, renames, and broken-link diagnostics.
- [SilverBullet](https://github.com/silverbulletmd/silverbullet) — MIT; inspect local-first Markdown workspace and query/index boundaries.
- [Obsidian Dataview](https://github.com/blacksmithgu/obsidian-dataview) — MIT; inspect frontmatter and incremental vault indexes, but do not copy its arbitrary-JavaScript execution model.
- [OpenHands](https://github.com/OpenHands/OpenHands) — MIT except `enterprise/`; inspect action/observation and sandbox concepts and exclude the source-available enterprise directory.

### Reference-review checklist

- [ ] Clone only repositories tied to a specific ADR or implementation question.
- [ ] Use partial/shallow clones where history is unnecessary.
- [ ] Record `git rev-parse HEAD` and the license hash immediately after checkout.
- [ ] Label notes as `concept-only`, `API-compatible`, or `code-adapted`.
- [ ] Do not copy code until its exact file-level license and notices are recorded.
- [ ] Reimplement independently when a source's license or provenance is ambiguous.
- [ ] Add required attribution and license text in the same commit that adds adapted code.

## Upstream merge strategy

We are not on our own yet. At this baseline the fork is only one small commit
ahead of `upstream/dev`. Pulling upstream changes can remain practical if the
new product is additive and the integration surface stays narrow.

```text
upstream/dev  ->  origin/dev (clean mirror)
                        \
                         downstream integration branch
                                  \
                                   feat/* branches
```

### Likely upstreamable as focused PRs

- immutable per-turn context primitives;
- stronger workspace confinement and prompt-injection tests;
- normalized structured-tool registration;
- evidence and citation primitives;
- provider/FAL fixes with tests;
- generic audit and recovery improvements.

### Likely downstream product work

- the unified conversation coordinator and automatic harness routing;
- the three-domain context isolation and cross-domain transfer policy;
- the project ledger, cross-conversation revision/cursor semantics, local experiment overlays, and context manifests;
- the external Project Context MCP adapter and cross-client synchronization UI;
- durable evolving personas and server-owned ensemble conversations;
- federated personal/episodic/procedural/persona/group memory semantics;
- claim authority and creative-canon semantics;
- the Personal Advisor risk, consent, selective-memory, and encryption boundary;
- the computer-agent privilege broker and native helpers;
- major status, citation, approval, personality, and memory UI changes.

### Conflict hotspots to keep thin

- `src/agent_loop.py` and `routes/chat_routes.py`;
- `src/tool_execution.py`, `src/tool_policy.py`, `src/tool_security.py`, `src/tool_schemas.py`, and `src/tool_index.py`;
- `core/database.py` and its hand-written migrations;
- `static/index.html`, `static/js/chat.js`, and `app.py`.

Use new service packages, new routers, backward-compatible tables, registry
adapters, feature flags, and one narrow context hook into the current loop.
Avoid broad renames, mass formatting, and a replacement agent loop. Upstream's
contribution rules ask for small PRs against `dev` and an issue before large or
agent-generated work, so generic improvements should be proposed separately
instead of as one architecture PR.

The downstream integration branch should merge or rebase `upstream/dev` at
every milestone boundary. Feature branches are short-lived and merge into
`integration/odysseus-expansion`; they should not become permanent forks of one
another. If an upstream change collides with a narrow hook, adapt the downstream
adapter rather than freezing the upstream component unless an ADR records a
deliberate divergence.
