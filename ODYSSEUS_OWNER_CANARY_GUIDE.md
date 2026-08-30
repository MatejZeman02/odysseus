# Owner Canary Guide

This guide records the three checks that cannot be truthfully completed by
automated tests alone. Run them only from the normal owner account, using a
disposable project or harmless documentation fixture. None of the checks
authorizes broader tools when it fails.

## 1. Semantic continuity canary

Use a Personal Advisor or project home with a configured model. Add a few
short, factual messages that establish an objective, decision, and an open
question; avoid real secrets or sensitive personal material.

1. Open **More tools → Companion context**.
2. Confirm **Create semantic proposal** is enabled, then create one.
3. Inspect the source attribution and proposed entries. Confirm that no home
   brief changed merely because the proposal was created.
4. Select only one or two correct entries and use **Promote selected entries**.
5. Reload Odysseus, create a project fork or reopen a fresh home worker, and
   inspect **Last compiled context**. It must contain the owner-promoted brief,
   not the original chat tail or another chat's history.
6. If the model call fails or is cancelled, verify the prior accepted brief is
   unchanged and the Context panel reports a safe failure instead of invented
   memory.

Record only the outcome and any safe failure code in a normal bug report; do
not paste raw conversation data into Git.

## 2. Reviewed project patch canary

Use a throwaway Git project with one harmless Markdown file. Project **Patch**
mode is an owner-scoped browser-session choice: after it is enabled, a clear
edit request is proposed by read-only Qwen and automatically applied only
through Odysseus's validated transaction. Read-only questions remain read-only.

1. Open that project in a Qwen Companion chat, select **Agent**, and enable
   **Patch**.
2. Ask for one small new Markdown file such as `.artifacts/canary.md` with a
   fixed one-sentence body.
3. Inspect the persistent Patch card: exact paths, diff, summary, and
   transaction record must match the request. Confirm the change is the only
   working-tree difference.
4. Reload the browser and confirm the same Process and Patch card reconstruct.
5. Use **Review applied change**; the new read-only turn may report concerns
   but must not silently create another patch.
6. Use **Roll back**. Confirm the file and its parent directory disappear when
   they were created by the patch, and the working tree returns to its original
   state.

If a path is stale, dirty, a symlink, or outside the registered project, the
transaction must fail without changing any workspace file. Do not retry by
turning on native shell access.

## 3. Computer Help containment qualification

The Computer Help command broker stays unavailable until an owner chooses and
reviews a digest-pinned image, pulls it deliberately ahead of time, and passes
the fixed hostile fixture. The cached Qwen image is not an approved substitute.

From a normal terminal in this repository, first inspect the safe readiness
state:

```bash
python scripts/qualify_computer_sandbox.py
```

After independently reviewing an image and ensuring its exact immutable digest
is already present locally, set it only for the current shell and run the fixed
fixture:

```bash
export ODYSSEUS_COMPUTER_SANDBOX_IMAGE='registry.example/image@sha256:<64-hex-digest>'
python scripts/qualify_computer_sandbox.py --run
```

The command accepts no user-supplied command, path, mount, or network option.
It prints only the public readiness state and the fixture's safe checks. A
non-zero result means containment did not qualify; leave `computer_assist`
disabled and investigate the report before changing any other setting.

On success, restart Odysseus from the same environment and verify that the
Computer Help capability panel reports sandboxed read-only commands as
available. This authorizes neither host shell, writes, network egress, package
installation, nor **Approve for me**.

> [!WARNING]
> `ODYSSEUS_COMPUTER_SANDBOX_IMAGE` must have no default anywhere. Until
> 2026-08-30 `run-companion.sh` defaulted it to the cached Qwen image, so a
> plain `./run-companion.sh start` reported `containment_probe_incomplete`
> instead of `pinned_sandbox_image_required` and a passing report for that
> rejected image was written to `data/`. Containment held only because the
> report version gate retired it. A launcher default makes this canary's
> decision on the owner's behalf.

## Recorded results

| Canary | Last run | Result |
| --- | --- | --- |
| 1. Semantic continuity | never | Not run. Needs an owner: a configured model, browser interaction, and a real provider call. |
| 2. Reviewed project patch | never | Not run. Needs an owner: a throwaway Git checkout and a Qwen turn. |
| 3. Computer Help containment | probe run 2026-08-27 against the cached Qwen image | Does not count. That image is rejected for Computer Help, and the report predates the `command_inventory` gate. Readiness for it now reads `sandbox_report_outdated`. No approved image has been chosen. |

Gates 1 and 2 have not run at any point on this branch. Automated coverage is
not a substitute: 6135 passing tests say the parts behave, not that the product
flow works end to end.
