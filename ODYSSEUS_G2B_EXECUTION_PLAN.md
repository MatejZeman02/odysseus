# G2B — Reviewed Project Patches

**State:** Implemented; owner acceptance testing active

**Authority boundary:** Qwen remains on `project_read` and never receives file
write, Shell, network, approval, or patch-application authority. The
authenticated owner authorizes automatic application by enabling **Patch** for
the current project session; that permission does not transfer to another
project and clears on browser reload.

## Implemented flow

1. In a Qwen project home, the owner enables **Patch** once for that browser
   session and sends change requests. The control remains active until disabled
   or the owner switches projects.
2. The normal read-only Qwen worker inspects project files and returns one
   versioned, machine-readable proposal containing complete UTF-8 file contents.
3. Odysseus removes that envelope from assistant prose, validates every path and
   operation, calculates trusted preimage hashes and unified diffs, and stores an
   encrypted owner-scoped change set.
4. Odysseus automatically submits the validated proposal to its transaction
   engine using only the server-issued patch ID and expected revision.
5. A persistent Patch card shows the summary, rationale, file list, line counts,
   and expandable exact diffs. Reloading or restarting reconstructs it from the
   saved assistant metadata and change-set record.
6. The server
   acquires a project lock, rejects stale or dirty affected paths, journals
   preimages, replaces all files atomically, verifies hashes and unrelated-file
   integrity, and restores prior contents after a partial failure.
7. **Roll back** restores
   an applied patch only while every affected file still matches its applied
   hash. **Review applied change** prepares a separate ordinary read-only Qwen
   review turn. A proposal left pending by a transport or validation failure can
   still be applied or rejected from its card without an extra confirmation.

## Supported patch scope

- `update` an existing regular UTF-8 text file;
- `create` a regular UTF-8 text file inside an existing project directory;
- at most 20 files, 512 KiB per file, and 1 MiB total proposed content;
- atomic whole-patch application only.

Absolute paths, traversal, `.git`, symlinks, special/binary files, deletion,
rename, mode changes, executable creation, commands, hooks, formatters, tests,
package managers, project settings, and browser-supplied replacement content
are rejected.

## Persistence and recovery

`ProjectChangeSet` stores the owner/project/session/source references, model
route, monotonic revision, trusted hashes, encrypted proposal and rollback
material, transaction state, timestamps, integrity outcome, and sanitized
failure code. Interrupted `applying` transactions are recovered during startup
when targets still match a known preimage or proposed hash; otherwise they are
left in a safe actionable failure state.

Deleting a project cascades its proposals and rollback material without
deleting external workspace files. Deleting a source chat or message detaches
the optional audit reference instead of deleting an already-applied record.

## Acceptance checks

- [x] Existing-file update and new-file creation apply with exact reviewed bytes.
- [x] New files are non-executable and existing file modes are preserved.
- [x] Traversal, absolute paths, `.git`, symlinks, binary content, invalid shape,
  oversize proposals, duplicate targets, and unsupported operations are denied.
- [x] Changed targets become stale without being overwritten.
- [x] Dirty affected files are rejected; unrelated dirty files survive unchanged.
- [x] Apply failures restore already-replaced targets from the durable journal.
- [x] Rollback refuses to overwrite later owner work.
- [x] Mutation requests accept only `expected_revision`; proposal requests accept
  only `session_id` and the owner message.
- [x] Patch reads and mutations are owner scoped, revision guarded, and share the
  existing one-Qwen-turn admission and Stop lifecycle.
- [x] Proposal Process history and Patch cards persist across transcript reload.
- [ ] Owner canary: propose, inspect, apply, reload, review, and roll back a real
  harmless Dust documentation edit from the browser.
- [ ] Manual desktop and mobile layout/focus check.

## Deferred beyond G2B

Per-file/per-hunk approval, user editing of proposed content, delete/rename and
binary operations, automated tests/formatters, Git branch or commit creation,
writable Qwen tools, a command broker, AgentMemory, and Computer Help mutation
remain separate milestones.
