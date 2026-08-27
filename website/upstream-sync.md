# Syncing this fork with `upstream/dev`

This fork deliberately keeps its local `dev` branch as a pristine mirror of
`upstream/dev`. Feature work belongs on named branches such as
`feat/companion-continuity-mvp`; never commit directly to `dev`.

## Before starting

Run this from the intended checkout, not an older installation directory:

```bash
cd /path/to/odysseus
git status --short
git remote -v
```

The worktree must be clean. `upstream` must fetch from the official project and
must not have a push URL. `origin` is the fork you may eventually push.

## 1. Inspect upstream first

```bash
git fetch --prune upstream
git log --oneline dev..upstream/dev
git diff --stat dev..upstream/dev
git diff --name-status dev..upstream/dev
```

Read the changed paths and commits before merging. If they touch a subsystem
this fork changes, inspect the actual diff and run its focused tests after the
merge. Do not assume a small diff is harmless.

## 2. Advance the protected mirror only by fast-forward

```bash
git switch dev
git merge --ff-only upstream/dev
git rev-parse dev
git rev-parse upstream/dev
```

The final two hashes must match. If fast-forward fails, stop: `dev` has local
commits and is no longer the intended upstream mirror. Do not use `reset`,
rebase, or a merge commit to hide that fact.

## 3. Merge the mirror into a feature branch

```bash
git switch feat/companion-continuity-mvp
git merge --no-ff upstream/dev -m "merge: sync upstream dev <topic>"
```

If Git reports conflicts:

1. Read both versions and preserve behavior from both sides.
2. Prefer upstream's new canonical module when it has moved a route; port this
   fork's narrow customization into that module instead of retaining duplicate
   implementations.
3. Update source-path tests that intentionally inspect a moved module.
4. Stage only the resolved files, run the affected tests, and complete the
   merge commit.

Abort only while you are still certain the merge itself is the unwanted action:

```bash
git merge --abort
```

Do not use `git reset --hard` to recover from a sync attempt.

## 4. Verify and record the result

```bash
git diff --check
git status --short
git log --oneline --decorate -6
```

Run at least the tests for changed upstream domains plus the fork’s G1 tests.
For example, after the 2026-08-04 Vault/Webhook route move:

```bash
./venv/bin/python -m pytest -q \
  tests/test_api_chat_security.py \
  tests/test_vault_routes_shim.py \
  tests/test_webhook_routes_shim.py \
  tests/test_webhook_provider_aliases.py \
  tests/test_g1_continuity_routes.py \
  tests/test_scoped_turn_service.py
```

Keep the merge commit on the feature branch. Push or open a pull request only
after a separate owner decision. Never push to `upstream`.

## Running the correct checkout

The process working directory matters: `python -m uvicorn app:app` imports the
`app.py` beside it. Verify before starting:

```bash
pwd
test -f app.py && test -d static
./venv/bin/python -m uvicorn app:app --host 127.0.0.1 --port 7000
```

If port 7000 is occupied, identify the process and its working directory before
stopping anything. An old installation can continue serving an old checkout
even while this repository is on the correct branch.
