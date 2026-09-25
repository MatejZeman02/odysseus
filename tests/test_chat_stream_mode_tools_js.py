"""A model-driven switch to Agent mode keeps hidden composer controls hidden.

The Qwen and Patch buttons carry `hidden` in chats where Qwen is not set up,
but their `.input-icon-btn` class sets `display: flex`, which beats the
attribute. Clearing the inline `display: none` would show them anyway.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest


_REPO = Path(__file__).resolve().parents[1]
_MODULE = (_REPO / "static" / "js" / "chatStream.js").as_uri()

_SCRIPT = """
import { registerHooks } from 'node:module';

// chatStream.js imports the whole UI. Every sibling module is replaced by a
// stub whose members are no-op functions, so only the handler itself runs.
const STUB = 'data:text/javascript,' + encodeURIComponent(
  'export default new Proxy({}, { get: (_target, key) => key === "getJSON" ? () => ({}) : () => {} });'
);
registerHooks({
  resolve(specifier, context, next) {
    if (specifier.startsWith('./')) return { url: STUB, shortCircuit: true };
    return next(specifier, context);
  },
});

function button(id, hidden) {
  return { id, hidden, style: { display: hidden ? 'none' : '' }, classList: { toggle() {} } };
}
const buttons = [
  button('web-toggle-btn', false),
  button('qwen-toggle-btn', true),
  button('project-patch-btn', true),
];
globalThis.document = {
  addEventListener() {},
  getElementById: (id) => buttons.find((item) => item.id === id) || null,
  querySelectorAll: (selector) => (selector === '[data-mode-tool]' ? buttons : []),
  querySelector: () => null,
};

const { handleUIControl } = await import(MODULE);
const seen = {};
for (const mode of ['agent', 'chat', 'agent']) {
  handleUIControl({ ui_event: 'set_mode', mode });
  seen[mode] = Object.fromEntries(buttons.map((item) => [item.id, item.style.display]));
}
console.log(JSON.stringify(seen));
"""


def test_switching_to_agent_mode_leaves_hidden_controls_hidden():
    if not shutil.which("node"):
        pytest.skip("node is not installed")

    result = subprocess.run(
        ["node", "--input-type=module"],
        input=_SCRIPT.replace("MODULE", json.dumps(_MODULE)),
        capture_output=True,
        text=True,
        cwd=_REPO,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    seen = json.loads(result.stdout)
    assert seen["agent"] == {"web-toggle-btn": "", "qwen-toggle-btn": "none", "project-patch-btn": "none"}
    assert seen["chat"] == {"web-toggle-btn": "none", "qwen-toggle-btn": "none", "project-patch-btn": "none"}
