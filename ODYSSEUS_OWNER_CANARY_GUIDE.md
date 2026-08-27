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
