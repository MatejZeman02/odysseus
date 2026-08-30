"""Architectural guard: owner-supplied roots go through the shared pin.

This is not a behavior test, and it is deliberately not trying to be one. The
path-identity bug class does not show up as a failing assertion in the module
that reintroduces it, because the reintroduced code is locally correct: it
lstats, it checks, it resolves. It only becomes a hole in combination with the
time that passes between those calls. Five commits on this branch fixed that
same shape one call site at a time.

So the invariant worth pinning is structural: inside the modules that turn an
owner-supplied path into filesystem authority, nothing resolves a path except
``src.path_identity``. When a new call site needs an exception, add it to
``_ALLOWED`` with the reason, which forces the question to be answered once,
in review, rather than discovered later.
"""

import ast
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]

# Modules that convert an owner-supplied path into read or write authority.
_GUARDED = (
    "src/protected_workspace.py",
    "src/qwen_harness.py",
    "src/project_patches.py",
    "routes/computer_help_routes.py",
    "routes/g1_continuity_routes.py",
)

# (module, enclosing function, receiver) -> why this one is not a path grant.
_ALLOWED = {
    ("routes/computer_help_routes.py", "_safe_task_root", "Path.home()"):
        "The home directory is server-derived, not named by the owner.",
    ("src/project_patches.py", "_safe_target", "root"):
        "root arrives already pinned by _project_root.",
    ("src/project_patches.py", "_ensure_parent_directories", "root"):
        "root arrives already pinned by _project_root.",
}


def _receiver(node: ast.Call) -> str:
    """Render the expression a .resolve()/realpath() call was made on."""
    func = node.func
    if isinstance(func, ast.Attribute):
        return ast.unparse(func.value)
    return ast.unparse(func)


def _resolution_calls(module_path: str, base: Path = REPO_ROOT):
    """Yield (function, receiver, line) for every path-resolving call."""
    tree = ast.parse((base / module_path).read_text(encoding="utf-8"))
    for parent in ast.walk(tree):
        if not isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(parent):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            resolves = (
                (isinstance(func, ast.Attribute) and func.attr in {"resolve", "realpath"})
                or (isinstance(func, ast.Name) and func.id == "realpath")
            )
            if resolves:
                yield parent.name, _receiver(node), node.lineno


@pytest.mark.parametrize("module_path", _GUARDED)
def test_containment_modules_resolve_paths_only_through_the_pin(module_path):
    unreviewed = [
        (function, receiver, line)
        for function, receiver, line in _resolution_calls(module_path)
        if (module_path, function, receiver) not in _ALLOWED
    ]

    assert not unreviewed, "\n".join(
        [
            f"{module_path} resolves an owner-supplied path outside "
            f"src.path_identity.pin_directory:",
            *(
                f"  line {line}: {receiver}.resolve(...) in {function}()"
                for function, receiver, line in unreviewed
            ),
            "",
            "Checking a path and then resolving it reads the filesystem twice "
            "and trusts both answers. Use pin_directory(), which settles type, "
            "symlink status, and inode identity in one checked sequence, or add "
            "an entry to _ALLOWED explaining why this call is not a path grant.",
        ],
    )


def test_the_guard_can_actually_see_an_unreviewed_call(tmp_path):
    """The guard is worthless if its parser silently matches nothing."""
    offender = tmp_path / "offender.py"
    offender.write_text(
        "from pathlib import Path\n"
        "def register(raw):\n"
        "    candidate = Path(raw)\n"
        "    if candidate.is_symlink():\n"
        "        raise ValueError('no')\n"
        "    return candidate.resolve(strict=True)\n",
        encoding="utf-8",
    )
    found = list(_resolution_calls("offender.py", base=tmp_path))

    assert found == [("register", "candidate", 6)]


def test_every_allowlist_entry_still_corresponds_to_real_code():
    """A stale exemption is an exemption nobody is reviewing."""
    live = {
        (module_path, function, receiver)
        for module_path in _GUARDED
        for function, receiver, _line in _resolution_calls(module_path)
    }

    assert not set(_ALLOWED) - live, (
        f"_ALLOWED exempts calls that no longer exist: {set(_ALLOWED) - live}. "
        f"Delete them so the list keeps meaning something."
    )
