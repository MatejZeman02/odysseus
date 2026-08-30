"""One audited way to turn an owner-supplied path into a pinned directory.

Every path an owner names is a *path grant*: the owner authorized a directory,
not whatever the name happens to point at when the code finally opens it. The
gap between checking a path and using it is where this fork has repeatedly
leaked authority, and the fixes for it accumulated one call site at a time:
symlinked protected roots, swapped workspace roots, symlinked launch
workspaces, symlinked project roots, descriptor-bound snapshots. Same bug,
five times, because each caller wrote its own ``lstat``/``resolve``/``stat``
sequence and each sequence had a different hole.

The check that closes the class is not "is this a symlink" but "is the thing I
resolved still the same inode I approved". A lone ``is_symlink()`` before a
``resolve()`` proves nothing: the two calls read the filesystem at different
moments, so a swap in between is invisible to both.

``pin_directory`` performs the whole sequence once, correctly:

1. ``lstat`` the named leaf and reject a symlink or a non-directory outright,
   so a path grant can never be redirected by its own final component.
2. ``resolve(strict=True)`` to get the real location.
3. ``lstat`` the leaf *again* and the resolved path, and require all three to
   report one identical ``(st_dev, st_ino)``. Anything that moved during the
   sequence fails here rather than being used.

``PinnedPath.verify`` re-runs step 3 against the recorded identity, for callers
that validate early and act later. Holding a pin is not a substitute for
re-verifying at the moment of use.

The errors are deliberately vague: they reach owner-facing surfaces, and a
message that distinguished "not a directory" from "changed underneath you"
would describe the host filesystem to whoever triggered it.
"""

from __future__ import annotations

import stat
from dataclasses import dataclass
from pathlib import Path


class PathIdentityError(ValueError):
    """An owner-supplied path is unusable, or did not stay itself."""


@dataclass(frozen=True)
class PinnedPath:
    """A resolved directory plus the identity it was approved as.

    ``st_uid`` comes from the same inspection that established the identity, so
    a caller enforcing an ownership policy does not have to re-stat the path
    and reopen the window this type exists to close.
    """

    path: Path
    st_dev: int
    st_ino: int
    st_uid: int

    def verify(self) -> None:
        """Re-check that the pinned path is still the approved inode.

        Callers that resolve a root once and use it later must call this at
        the point of use. A pin records what was approved, it does not stop
        the filesystem from changing afterwards.
        """
        _identify(self.path, self.st_dev, self.st_ino)

    def __fspath__(self) -> str:
        return str(self.path)

    def __str__(self) -> str:
        return str(self.path)


def _identify(path: Path, expect_dev: int, expect_ino: int) -> None:
    try:
        info = path.lstat()
    except OSError as exc:
        raise PathIdentityError("the selected folder is unavailable") from exc
    if (
        stat.S_ISLNK(info.st_mode)
        or not stat.S_ISDIR(info.st_mode)
        or (info.st_dev, info.st_ino) != (expect_dev, expect_ino)
    ):
        raise PathIdentityError("the selected folder changed while it was being prepared")


def pin_directory(path: Path | str) -> PinnedPath:
    """Resolve an owner-supplied directory and pin the identity it resolved to.

    Raises ``PathIdentityError`` when the leaf is a symlink, is not a
    directory, does not exist, or does not hold one stable identity across the
    whole check.
    """
    candidate = Path(str(path))
    try:
        before = candidate.lstat()
    except OSError as exc:
        raise PathIdentityError("the selected folder is unavailable") from exc
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISDIR(before.st_mode):
        raise PathIdentityError("the selected folder must be a regular directory")
    try:
        resolved = candidate.resolve(strict=True)
        after = candidate.lstat()
        target = resolved.lstat()
    except OSError as exc:
        raise PathIdentityError("the selected folder is unavailable") from exc
    approved = (before.st_dev, before.st_ino)
    if (
        stat.S_ISLNK(after.st_mode)
        or not stat.S_ISDIR(target.st_mode)
        or (after.st_dev, after.st_ino) != approved
        or (target.st_dev, target.st_ino) != approved
    ):
        raise PathIdentityError("the selected folder changed while it was being prepared")
    return PinnedPath(resolved, before.st_dev, before.st_ino, target.st_uid)
