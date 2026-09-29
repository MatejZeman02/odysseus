# Auto approver design (Jev or similar)

**Status:** proposal of 2026-09-29. Nothing is built. It waits on the owner's
answers to the questions at the end.

On 2026-09-25 the owner chose to let a second model approve some actions on
their behalf, naming Jev "or similar". This page proposes what that model may
approve, how it decides, and how it is switched on.

## The problem it solves

A Companion chat with a **Can do** grant edits files and updates memory without
an approval card, but only while the run has read nothing except the owner's
checkout. Once it reads a web page, fetched text or context carried in from an
earlier run, `ToolRunSecurityContext.decision_for` in `src/tool_capabilities.py`
holds these actions behind a card:

| Held action | Tools | Effect | Can the owner undo it? |
|---|---|---|---|
| Project edit | `write_file`, `edit_file`, `apply_patch` | `write_workspace` | Yes, from **Review project changes** |
| Memory update | `update_memory` | `write_private` | A new brief revision, the older ones are kept |
| New document | `create_document` | `write_private` | Yes, delete it |
| Web fetch of a URL the owner did not type | `web_fetch` | `network_egress` | No, the request has left |
| Question to the teacher model | `ask_teacher` | `network_egress` | No, the text has left |

`project_shell`, reads and `web_search` are never held. With nobody at the
screen, a run that reads library docs and then edits code stops at the first
edit. That is the common case the owner wants to unblock.

## What Jev is

TypeSafe's "System One" model, which Sara already trials (see Sara's
`docs/specs/jev-judge.md`). It takes a block of state and a fixed set of
questions and answers each with a typed value: a yes or no given as the
probability of yes, a choice, or a score. It cannot write text. Vendor figures
of 2026-09-15: $0.042 per million input tokens, output free, 70 to 500 ms. The
state is limited to 32k tokens. Sara pins `jev-1.13.0` and saw 0.6 to 0.7 s
per call.

Sara's trial taught three things this design keeps. A probability is never the
authority, ordinary code holds the policy. Thresholds are tuned on the owner's
own decisions, not guessed. Jev reads prose better than diffs, and a diff too
large for the window is where it goes blind.

## What it may approve

The rule: **Jev may approve only an action the owner can undo, inside a grant
the owner switched on, one exact action at a time.**

- **Project edits:** eligible. They land as recorded changes with an undo.
- **Memory updates and new documents:** shadow only at first. A memory update
  feeds every later chat in that home, so a bad one costs more than a bad edit.
- **Web fetches and `ask_teacher`:** never. Data that has left cannot be
  brought back, and a URL is the classic way injected text smuggles data out.
- **`request_capability`:** never. Only the owner widens what a chat can do.
- **Chat or task scope:** never. An owner's approval on a card lifts the gate
  for the rest of the task or the whole chat. Jev approves the one action in
  front of it, and the next held action asks again.
- **Runs driven by an API token, and ordinary upstream chat:** out of scope and
  unchanged.

Jev never refuses on the owner's behalf. It approves or hands the card to the
owner, which is exactly what happens today.

## How it decides

For a held project edit the server builds the state from what it already owns:

- the owner's latest message, in their own words
- the unified diff that `src/project_patches.py` generates for the change,
  whole or not at all
- the outside sources the run read, by tool and URL, with short excerpts marked
  as untrusted

Jev gets four yes or no questions:

1. `serves_request`: the change does what the owner's latest message asks, or
   is a step toward it.
2. `follows_outside_text`: the change does something the outside text asks for
   that the owner did not.
3. `adds_egress_or_exec`: the change adds code that downloads, sends data out,
   runs commands or evaluates strings.
4. `weakens_checks`: the change disables or loosens tests, checks, validation
   or security code.

Code then decides, in this order, and every "no" hands the card to the owner:

1. The chat has the approver switched on, the grant is on, and the run is not
   token driven.
2. The tool is on the eligible list above.
3. No touched path is on a fixed sensitive list: `.git/`, `.github/`, CI files,
   Git hooks, `.env` and key files, install and build scripts. Code checks
   this, Jev is not asked.
4. The whole state fits the window. A cut diff is never judged.
5. Jev answered within 3 seconds with a well-formed reply.
6. `serves_request` is at least the high threshold and each risk question is
   at most the low threshold. Placeholders are 0.9 and 0.1, tuned in shadow.

A key that is missing, a timeout, an HTTP error, a malformed reply or a spent
daily budget all hand the card to the owner. The run is never worse off than
today.

## Where it plugs in

`src/agent_loop.py` turns a held action into a card with
`tool_approval_store.create(...)`. The approver runs just before that. When it
approves, the action runs in place in the same run, through the same
`project_patches` transaction as any granted edit. The run does not stop, and
nothing sets `approval_gate_bypassed`. When it hands over, the card is created
as today and shows Jev's answers, so the owner sees why.

A new module, `src/auto_approver.py`, holds the state builder, the policy and a
small judge interface. Jev is the first judge. "Or similar" means another typed
judge can replace it later. A chat model could too, but it can be argued into
things in prose, which is why Jev comes first.

## Owner controls and the key

- A per-chat switch under **Can do**, "Jev approves edits after web reading",
  off by default and owned by the server like the grants. It appears only when
  a key is configured.
- The key is read from a file at each call and never logged, returned or put
  in a prompt. The launcher exports the file's path only when the file exists.
  The owner puts the key there, never the assistant.
- Every judgement is stored with the chat: the action's digest, the four
  answers, the model version, the outcome and who decided. The chat shows one
  line, for example "Approved by Jev" under the change.

## Rollout

1. **Shadow.** Jev answers every held card in the background. The card still
   goes to the owner, nothing waits on Jev, and each answer is logged beside
   the owner's decision.
2. **Project edits.** Once Jev's bands agree with the owner's decisions on
   enough cards, the switch starts approving project edits with the tuned
   thresholds.
3. **Later, on evidence.** Memory updates and new documents, only after step 2
   has run a while. Network egress and grant requests stay with the owner.

At about 5,000 tokens a card, a hundred cards cost about two cents.

## Tests

- Policy tests with a fake judge: each outcome, each hand-over reason, the
  never list, and no chat or task scope ever set.
- A held edit with the switch on and an approving fake judge lands as a
  recorded change marked "Approved by Jev". With a judge that hands over, the
  card appears exactly as today.
- An owner canary: a coding task that reads a web page first, run with Jev on.

## Questions for the owner

1. After shadow, may Jev approve project edits only (recommended), or memory
   updates too?
2. Which key file: the one Sara uses, `~/.sara-gateway/jev.key` (one key, one
   bill, recommended), or a separate one for Odysseus?
3. How long a shadow: a fixed number of cards, for example 30, or until you
   say so?
4. Is the switch per chat (recommended, like the grants) or per home?
