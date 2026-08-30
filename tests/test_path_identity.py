"""Behavior of the shared owner-supplied-path pin.

These tests drive the real filesystem. The one race that cannot be produced by
waiting is simulated deterministically: the swap happens inside ``resolve``,
which is exactly the window a separate ``is_symlink()`` check cannot see.
"""

import os
from pathlib import Path

import pytest

from src.path_identity import PathIdentityError, pin_directory


def test_pin_returns_the_resolved_directory_and_its_identity(tmp_path):
    real = tmp_path / "project"
    real.mkdir()

    pinned = pin_directory(real)

    assert pinned.path == real.resolve()
    assert (pinned.st_dev, pinned.st_ino) == (real.stat().st_dev, real.stat().st_ino)
    assert os.fspath(pinned) == str(real.resolve())


def test_pin_resolves_a_directory_reached_through_a_symlinked_parent(tmp_path):
    """Only the named leaf must be real. Parents may legitimately be links."""
    real = tmp_path / "real" / "project"
    real.mkdir(parents=True)
    (tmp_path / "link").symlink_to(tmp_path / "real", target_is_directory=True)

    pinned = pin_directory(tmp_path / "link" / "project")

    assert pinned.path == real.resolve()


def test_pin_rejects_a_symlinked_leaf(tmp_path):
    real = tmp_path / "elsewhere"
    real.mkdir()
    link = tmp_path / "project"
    link.symlink_to(real, target_is_directory=True)

    with pytest.raises(PathIdentityError):
        pin_directory(link)


def test_pin_rejects_a_file_and_a_missing_path(tmp_path):
    plain = tmp_path / "notes.md"
    plain.write_text("x", encoding="utf-8")

    with pytest.raises(PathIdentityError):
        pin_directory(plain)
    with pytest.raises(PathIdentityError):
        pin_directory(tmp_path / "absent")


def test_pin_rejects_a_directory_swapped_for_a_symlink_mid_check(tmp_path, monkeypatch):
    """The race a lone is_symlink() cannot catch.

    The leaf is a genuine directory when first inspected and a symlink to an
    attacker-chosen tree by the time it resolves. A caller that checked once
    and resolved afterwards would hand out the swapped target.
    """
    approved = tmp_path / "project"
    approved.mkdir()
    (approved / "owned.txt").write_text("mine", encoding="utf-8")
    other = tmp_path / "other"
    other.mkdir()
    (other / "secret.txt").write_text("not mine", encoding="utf-8")

    original_resolve = Path.resolve
    swapped = []

    def resolve_after_swap(self, *args, **kwargs):
        if not swapped:
            swapped.append(True)
            for child in approved.iterdir():
                child.unlink()
            approved.rmdir()
            approved.symlink_to(other, target_is_directory=True)
        return original_resolve(self, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve_after_swap)

    with pytest.raises(PathIdentityError):
        pin_directory(approved)


def test_verify_rejects_a_root_replaced_after_it_was_pinned(tmp_path):
    approved = tmp_path / "project"
    approved.mkdir()
    pinned = pin_directory(approved)
    pinned.verify()

    approved.rmdir()
    approved.mkdir()  # same name, new inode

    with pytest.raises(PathIdentityError):
        pinned.verify()


def test_verify_rejects_a_root_that_became_a_symlink(tmp_path):
    approved = tmp_path / "project"
    approved.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    pinned = pin_directory(approved)

    approved.rmdir()
    approved.symlink_to(other, target_is_directory=True)

    with pytest.raises(PathIdentityError):
        pinned.verify()


def test_verify_rejects_a_removed_root(tmp_path):
    approved = tmp_path / "project"
    approved.mkdir()
    pinned = pin_directory(approved)
    approved.rmdir()

    with pytest.raises(PathIdentityError):
        pinned.verify()


def test_errors_never_describe_the_host_filesystem(tmp_path):
    """Owner-facing surfaces receive these strings, so they stay generic."""
    secret = tmp_path / "very-private-directory-name"
    secret.mkdir()
    link = tmp_path / "project"
    link.symlink_to(secret, target_is_directory=True)

    with pytest.raises(PathIdentityError) as exc_info:
        pin_directory(link)

    message = str(exc_info.value)
    assert "very-private-directory-name" not in message
    assert str(tmp_path) not in message
