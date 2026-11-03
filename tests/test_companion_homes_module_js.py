"""The Companion sidebar and scope banner, now that they live in their own module.

``static/js/companionHomes.js`` used to be a thousand lines in the middle of
``static/js/sessions.js``. Two things became load-bearing when it moved out, and
neither existed while the code was inline:

* the render function has to hand the recent-chat list back, trimmed, because
  ``sessions.js`` no longer shares the local variable, and
* everything it used to read from ``sessions.js`` now arrives through
  ``bindCompanionHost``, so an unbound module must still render rather than
  throw.

The module is copied into a temporary directory with its three imports pointed
at stubs, which is how ``tests/test_startup_session_bootstrap_js.py`` runs the
same file. Importing it in place is not possible: ``static/js/ui.js`` reaches
``colorPicker.js``, which touches ``HTMLInputElement`` at module scope.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "static" / "js" / "companionHomes.js"

pytestmark = pytest.mark.skipif(
    not shutil.which("node"), reason="node binary not on PATH"
)

STUBS = {
    "storage.mjs": "export default { loadToggleState: () => ({ mode: 'chat' }) };\n",
    "ui.mjs": "export default { showToast() {}, showError() {} };\n",
    "workspace.mjs": "export default { setWorkspace() {} };\n",
}

# A fake DOM just wide enough for the two entry points under test. Elements
# record what was set on them so the assertions can read the rendered tree
# instead of the module's source.
DOM = r"""
function makeElement(tag) {
  const el = {
    tagName: tag.toUpperCase(),
    children: [],
    attributes: {},
    dataset: {},
    listeners: {},
    className: '',
    textContent: '',
    hidden: false,
    classList: { add() {}, remove() {}, contains: () => false },
    appendChild(child) { el.children.push(child); return child; },
    append(...kids) { kids.forEach(k => el.children.push(k)); },
    addEventListener(name, fn) { (el.listeners[name] ||= []).push(fn); },
    setAttribute(name, value) { el.attributes[name] = value; },
  };
  return el;
}

const byId = new Map();
// The module registers two window listeners at import time, for the tool
// toggle and for session updates.
globalThis.window = {
  location: { origin: 'http://localhost' },
  addEventListener() {},
  dispatchEvent() {},
};
globalThis.document = {
  createElement: makeElement,
  createDocumentFragment: () => makeElement('#fragment'),
  getElementById: (id) => byId.get(id) || null,
  body: makeElement('body'),
};
globalThis.registerElement = (id) => {
  const el = makeElement('div');
  byId.set(id, el);
  return el;
};
globalThis.flatten = (node) => node.children.flatMap(
  child => [{ tag: child.tagName, cls: child.className, text: child.textContent,
              project: child.dataset.projectId ?? null },
            ...flatten(child)]);
"""


def _run(body: str, tmp_path: Path):
    """Copy the module beside the stubs, then run `body` against it."""
    source = MODULE.read_text(encoding="utf-8")
    for name, target in (
        ("storage", "storage.mjs"),
        ("ui", "ui.mjs"),
        ("workspace", "workspace.mjs"),
    ):
        source, count = re.subn(
            rf"from '\./{name}\.js(?:[?#][^']*)?';",
            f"from './{target}';",
            source,
            count=1,
        )
        assert count == 1, f"companionHomes import changed: {name}"
    (tmp_path / "companionHomes.mjs").write_text(source, encoding="utf-8")
    for name, stub in STUBS.items():
        (tmp_path / name).write_text(stub, encoding="utf-8")

    script = (
        DOM
        + f"const mod = await import({str((tmp_path / 'companionHomes.mjs').as_uri())!r});\n"
        + body
    )
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=ROOT, check=True, capture_output=True, text=True,
    )
    return json.loads(result.stdout)


SESSIONS = """
const sessions = [
  { id: 'p1', scope_kind: 'personal', is_scope_primary: true },
  { id: 'p2', scope_kind: 'personal', is_scope_primary: false, name: 'Side thread' },
  { id: 'c1', scope_kind: 'computer', is_scope_primary: true },
  { id: 'j1', scope_kind: 'project', is_scope_primary: true, project_id: 'proj-a' },
  { id: 'j2', scope_kind: 'project', is_scope_primary: false, project_id: 'proj-a' },
  { id: 'x1', scope_kind: null },
];
"""


def test_the_recent_chat_list_comes_back_without_the_companion_homes(tmp_path):
    """sessions.js assigns the return value, so the trim has to happen there."""
    values = _run(
        SESSIONS
        + """
        mod.bindCompanionHost({
          sessions: () => sessions,
          companionProjects: () => [{ id: 'proj-a', name: 'Odysseus' }],
          currentSessionId: () => 'p1',
        });
        const frag = document.createDocumentFragment();
        const rest = mod.renderCompanionHomes(sessions, frag);
        console.log(JSON.stringify({ rest: rest.map(s => s.id) }));
        """,
        tmp_path,
    )

    # Personal and Computer sessions leave the ordinary list entirely, threads
    # included, because the Companion section lists those threads itself under
    # its own heading. Project chats are different: only the project's primary
    # chat is listed there, so the extra threads stay in the recent list.
    assert values["rest"] == ["j2", "x1"]


def test_every_home_and_project_reaches_the_sidebar_fragment(tmp_path):
    values = _run(
        SESSIONS
        + """
        mod.bindCompanionHost({
          sessions: () => sessions,
          companionProjects: () => [
            { id: 'proj-a', name: 'Odysseus' },
            { id: 'proj-b', name: 'Pigments' },
          ],
          currentSessionId: () => 'p1',
        });
        const frag = document.createDocumentFragment();
        mod.renderCompanionHomes(sessions, frag);
        console.log(JSON.stringify({ rendered: flatten(frag) }));
        """,
        tmp_path,
    )
    rendered = values["rendered"]

    headings = [n["text"] for n in rendered if n["cls"] == "date-section-header"]
    assert headings == ["Companion", "Personal threads", "Projects"]

    labels = [n["text"] for n in rendered if "companion-home" in (n["cls"] or "")]
    assert labels == [
        "Personal Advisor",
        "Side thread",
        "Computer Help · diagnostics",
        "+ New project",
        "",  # the project rows carry their label on a child button
        "",
    ]

    # A project with no primary chat says so, and one row exists per project.
    assert [n["project"] for n in rendered if n["project"]] == ["proj-a", "proj-b"]
    assert [n["text"] for n in rendered if n["cls"] == "grow"] == [
        "Odysseus",
        "Pigments · no chat",
    ]


def test_an_unbound_module_still_renders(tmp_path):
    """bindCompanionHost runs at sessions.js module scope, but order is not a
    contract. The defaults have to be callable rather than undefined."""
    values = _run(
        """
        const frag = document.createDocumentFragment();
        const rest = mod.renderCompanionHomes([{ id: 'x1', scope_kind: null }], frag);
        console.log(JSON.stringify({ rest: rest.map(s => s.id), children: frag.children.length }));
        """,
        tmp_path,
    )

    assert values["rest"] == ["x1"]
    assert values["children"] > 0


@pytest.mark.parametrize(
    "scope,expected_title",
    [
        ("personal", "Personal Advisor"),
        ("computer", "Computer Help"),
        ("project", "Project · Odysseus"),
    ],
)
def test_the_scope_banner_names_the_home_it_is_in(tmp_path, scope, expected_title):
    values = _run(
        f"""
        const banner = registerElement('companion-scope-banner');
        const title = registerElement('companion-scope-title');
        const detail = registerElement('companion-scope-detail');
        registerElement('overflow-companion-context-btn');
        registerElement('overflow-chat-capabilities-btn');
        mod.syncCompanionScopeBanner({{
          id: 's1', scope_kind: {scope!r}, project_name: 'Odysseus',
          workspace_root: '/home/owner/work/odysseus',
          chat_capabilities: {{}},
        }});
        console.log(JSON.stringify({{
          hidden: banner.hidden, title: title.textContent, detail: detail.textContent,
        }}));
        """,
        tmp_path,
    )

    assert values["hidden"] is False
    assert values["title"] == expected_title
    assert values["detail"]


def test_an_ordinary_chat_hides_the_scope_banner(tmp_path):
    values = _run(
        """
        const banner = registerElement('companion-scope-banner');
        const title = registerElement('companion-scope-title');
        registerElement('companion-scope-detail');
        const context = registerElement('overflow-companion-context-btn');
        mod.syncCompanionScopeBanner({ id: 's1', scope_kind: null });
        console.log(JSON.stringify({
          hidden: banner.hidden, title: title.textContent, context: context.hidden,
        }));
        """,
        tmp_path,
    )

    assert values == {"hidden": True, "title": "", "context": True}


def test_the_dependency_stays_one_way():
    """sessions.js imports the companion module, never the other way round.

    An import back would make the pair a cycle, which ES modules allow and
    then half-initialise, and it would also put the fork back in the position
    this move was made to leave.
    """
    specifiers = re.findall(r"^import[^;]*?from '([^']+)';", MODULE.read_text(), re.S | re.M)

    assert specifiers, "the module lost its imports"
    assert [s for s in specifiers if "sessions" in s] == []
