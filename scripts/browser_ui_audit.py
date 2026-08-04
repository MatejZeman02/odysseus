#!/usr/bin/env python3
"""Audit the rendered Odysseus UI through Brave's DevTools protocol.

This script is intentionally dependency-light: Fedora's Brave plus the
already-installed ``websockets`` package are enough.  It is normally invoked
by ``run-browser-ui-audit.sh``, which supplies a disposable database/server.
"""

from __future__ import annotations

import argparse
import base64
import json
import time
import urllib.request
from pathlib import Path

from websockets.sync.client import connect


class Cdp:
    def __init__(self, websocket_url: str) -> None:
        self.ws = connect(websocket_url, open_timeout=10, max_size=16 * 1024 * 1024)
        self.sequence = 0
        self.events: list[dict] = []

    def close(self) -> None:
        self.ws.close()

    def call(self, method: str, params: dict | None = None, timeout: float = 15) -> dict:
        self.sequence += 1
        call_id = self.sequence
        self.ws.send(json.dumps({"id": call_id, "method": method, "params": params or {}}))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            raw = self.ws.recv(timeout=max(0.05, deadline - time.monotonic()))
            message = json.loads(raw)
            if message.get("id") == call_id:
                if "error" in message:
                    raise RuntimeError(f"CDP {method} failed: {message['error']}")
                return message.get("result", {})
            self.events.append(message)
        raise TimeoutError(f"Timed out waiting for CDP {method}")

    def evaluate(self, expression: str, timeout: float = 45):
        result = self.call(
            "Runtime.evaluate",
            {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": True,
                "userGesture": True,
            },
            timeout=timeout,
        ).get("result", {})
        if result.get("subtype") == "error":
            raise RuntimeError(result.get("description") or "JavaScript evaluation failed")
        return result.get("value")

    def drain(self, seconds: float = 0.2) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            try:
                self.events.append(json.loads(self.ws.recv(timeout=0.05)))
            except TimeoutError:
                pass


def _json_page(debug_port: int, expected_port: int) -> dict:
    with urllib.request.urlopen(f"http://127.0.0.1:{debug_port}/json/list", timeout=5) as response:
        pages = json.load(response)
    for page in pages:
        if page.get("type") == "page" and f":{expected_port}/" in page.get("url", ""):
            return page
    raise RuntimeError(f"No Brave page for port {expected_port}; found {[p.get('url') for p in pages]}")


def _wait_for(cdp: Cdp, expression: str, timeout: float = 20) -> object:
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        last = cdp.evaluate(expression)
        if last:
            return last
        time.sleep(0.1)
    raise TimeoutError(f"Browser condition did not become true: {expression}; last={last!r}")


DOM_AUDIT = r"""
(() => {
  const fields = [...document.querySelectorAll('input, textarea, select')];
  const labels = [...document.querySelectorAll('label')];
  const short = el => {
    const html = el.outerHTML.replace(/\s+/g, ' ');
    return html.length > 320 ? html.slice(0, 319) + '✂' : html;
  };
  const ids = new Map();
  fields.forEach(el => {
    if (!el.id) return;
    if (!ids.has(el.id)) ids.set(el.id, []);
    ids.get(el.id).push(short(el));
  });
  const duplicateFieldIds = [...ids.entries()]
    .filter(([, nodes]) => nodes.length > 1)
    .map(([id, nodes]) => ({id, count: nodes.length, nodes}));
  const missingIdentity = fields
    .filter(el => !el.id && !el.name)
    .map(short);
  const unlabeledFields = fields.filter(el => {
    if (el.disabled || el.getAttribute('aria-hidden') === 'true') return false;
    if (el.closest('label')) return false;
    if (el.getAttribute('aria-label') || el.getAttribute('aria-labelledby')) return false;
    return !(el.id && document.querySelector(`label[for="${CSS.escape(el.id)}"]`));
  }).map(short);
  const unassociatedLabels = labels.filter(label => {
    const target = label.getAttribute('for');
    if (!target) return !label.querySelector('input, textarea, select');
    return document.querySelectorAll(`#${CSS.escape(target)}`).length !== 1;
  }).map(short);
  return {
    fieldCount: fields.length,
    labelCount: labels.length,
    duplicateFieldIds,
    missingIdentity,
    unlabeledFields,
    unassociatedLabels,
  };
})()
"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--debug-port", type=int, required=True)
    parser.add_argument("--app-port", type=int, required=True)
    parser.add_argument("--session-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--username", default="ui-audit")
    parser.add_argument("--password", default="odysseus-browser-audit-password")
    parser.add_argument(
        "--wait-for-turn",
        type=float,
        default=0,
        help="After inline Send, wait this many seconds for the replacement Qwen turn to finish.",
    )
    parser.add_argument(
        "--edit-user-offset",
        type=int,
        default=0,
        help="Zero-based user-message offset from the end (0 edits the newest user message).",
    )
    parser.add_argument(
        "--expect-persisted-process",
        action="store_true",
        help="Require the synthetic persisted Process card and feedback fixture after reload.",
    )
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    page = _json_page(args.debug_port, args.app_port)
    cdp = Cdp(page["webSocketDebuggerUrl"])
    try:
        for domain in ("Page", "Runtime", "Network", "Log"):
            cdp.call(f"{domain}.enable")
        login_url = f"http://127.0.0.1:{args.app_port}/login"
        cdp.call("Page.navigate", {"url": login_url})
        _wait_for(cdp, f"location.href === {json.dumps(login_url)}")
        _wait_for(cdp, "document.readyState === 'complete'")
        login = cdp.evaluate(
            f"fetch('http://127.0.0.1:{args.app_port}/api/auth/login', {{method: 'POST', headers: {{'Content-Type': 'application/json'}}, "
            f"body: JSON.stringify({{username: {json.dumps(args.username)}, password: {json.dumps(args.password)}, remember: false}})"  # noqa: E501
            "}).then(async response => ({status: response.status, body: await response.json().catch(() => ({}))}))"
        )
        if login.get("status") != 200 or not login.get("body", {}).get("ok"):
            raise RuntimeError(f"Disposable UI login failed: {login}")
        cdp.call(
            "Page.navigate",
            {"url": f"http://127.0.0.1:{args.app_port}/#{args.session_id}"},
        )
        _wait_for(cdp, "document.readyState === 'complete'")
        _wait_for(cdp, "document.querySelectorAll('#chat-history .msg-user').length > 0", timeout=30)
        _wait_for(
            cdp,
            "[...document.querySelectorAll('#chat-history .msg-user')].at(-1)?.querySelector('.msg-footer') !== null",
            timeout=20,
        )
        _wait_for(
            cdp,
            "document.getElementById('companion-qwen-readiness') && !document.getElementById('companion-qwen-readiness').textContent.includes('checking')",
            timeout=20,
        )
        runtime = cdp.evaluate(r"""
(() => ({
  buildId: window.__ODYSSEUS_BUILD_ID || null,
  htmlBuildId: document.documentElement.dataset.odysseusBuild || null,
  appScript: document.querySelector('script[type="module"][src*="/static/app.js"]')?.src || null,
  qwenTogglePresent: !!document.getElementById('qwen-toggle-btn'),
  chatsExpanded: !document.getElementById('sessions-section')?.classList.contains('collapsed'),
  legacyShellControlCount: document.querySelectorAll(
    '#bash-toggle, #bash-toggle-btn, [data-ui-key="bash-toggle-btn"]'
  ).length,
  qwenReadiness: document.getElementById('companion-qwen-readiness')?.textContent?.trim() || null,
  scopeBanner: document.getElementById('companion-scope-banner')?.textContent?.trim() || null,
}))()
""")

        _wait_for(cdp, "typeof window.__odysseusCreateCompanionProject === 'function'", timeout=20)
        project_modal_open = cdp.evaluate(r"""
(() => {
  const button = document.getElementById('companion-new-project-btn');
  if (!button) return {ok: false, reason: 'new project button missing'};
  button.click();
  return {ok: true};
})()
""")
        _wait_for(cdp, "document.getElementById('companion-project-modal') !== null", timeout=20)
        project_modal = cdp.evaluate(r"""
(() => {
  const modal = document.getElementById('companion-project-modal');
  const fields = [...modal.querySelectorAll('input')];
  return {
    visible: getComputedStyle(modal).display !== 'none',
    title: modal.querySelector('h4')?.textContent?.trim() || null,
    fields: fields.map(field => ({id: field.id, name: field.name, readOnly: field.readOnly})),
    labels: [...modal.querySelectorAll('label')].map(label => ({for: label.htmlFor, text: label.textContent.trim()})),
    model: modal.querySelector('.companion-project-route strong')?.textContent?.trim() || null,
  };
})()
""")
        project_modal_dom = cdp.evaluate(DOM_AUDIT)
        cdp.evaluate("document.querySelector('#companion-project-modal .companion-project-browse').click()")
        _wait_for(cdp, "getComputedStyle(document.getElementById('workspace-modal')).display !== 'none'", timeout=20)
        workspace_picker = cdp.evaluate(r"""
(() => ({
  visible: getComputedStyle(document.getElementById('workspace-modal')).display !== 'none',
  title: document.querySelector('#workspace-modal h4')?.textContent?.trim() || null,
  useText: document.querySelector('#workspace-use')?.textContent?.trim() || null,
  note: document.querySelector('#workspace-modal .workspace-note')?.textContent?.trim() || null,
}))()
""")
        cdp.evaluate("document.getElementById('workspace-cancel').click()")
        _wait_for(cdp, "getComputedStyle(document.getElementById('companion-project-modal')).display !== 'none'", timeout=10)
        cdp.evaluate("document.querySelector('#companion-project-modal .close-btn').click()")

        message_delete_target = cdp.evaluate(r"""
(() => {
  const assistant = document.querySelector('#chat-history .msg-ai');
  const button = assistant?.querySelector('button[title="Delete message"]');
  if (!assistant || !button) return {opened: false};
  const all = [...document.querySelectorAll('#chat-history .msg')];
  const assistantIndex = all.indexOf(assistant);
  const user = [...all.slice(0, assistantIndex)].reverse().find(node => node.classList.contains('msg-user'));
  const result = {
    opened: true,
    assistantId: assistant.dataset.dbId || null,
    userId: user?.dataset.dbId || null,
  };
  button.click();
  return result;
})()
""")
        message_delete = {"completed": False, **message_delete_target}
        if message_delete_target.get("opened"):
            _wait_for(
                cdp,
                "getComputedStyle(document.getElementById('styled-confirm-overlay')).display !== 'none'",
                timeout=10,
            )
            cdp.evaluate("document.getElementById('styled-confirm-ok').click()")
            target_ids = [
                value for value in (
                    message_delete_target.get("userId"),
                    message_delete_target.get("assistantId"),
                ) if value
            ]
            _wait_for(
                cdp,
                "(() => %s.every(id => !document.querySelector(`[data-db-id=\"${CSS.escape(id)}\"]`)))()"
                % json.dumps(target_ids),
                timeout=10,
            )
            reloaded = cdp.evaluate(r"""
(async () => {
  const id = window.sessionModule?.getCurrentSessionId?.();
  await window.sessionModule?.selectSession?.(id);
  return id;
})()
""")
            time.sleep(0.5)
            message_delete = cdp.evaluate(r"""
(() => {
  const ids = %s;
  return {
    completed: true,
    sessionReloaded: window.sessionModule?.getCurrentSessionId?.() === %s,
    deletedIds: ids,
    absentAfterReload: ids.every(id => !document.querySelector(`[data-db-id="${CSS.escape(id)}"]`)),
  };
})()
""" % (json.dumps(target_ids), json.dumps(reloaded)))

        persisted_process_fixture = cdp.evaluate(r"""
(() => {
  const card = [...document.querySelectorAll('.qwen-process-card.complete')].at(-1);
  const panel = [...document.querySelectorAll('.qwen-feedback')].at(-1);
  return {
    present: !!card,
    title: card?.querySelector('.qwen-process-title')?.textContent?.trim() || null,
    toolLines: [...(card?.querySelectorAll('.qwen-process-tool-label') || [])]
      .map(node => node.textContent.trim()),
    selectedFeedback: panel?.querySelector('.qwen-feedback-choice.active')?.textContent?.trim() || null,
    feedbackNote: panel?.querySelector('.qwen-feedback-note')?.value || null,
  };
})()
""")

        before = cdp.evaluate(DOM_AUDIT)
        edit_opened = cdp.evaluate(r"""
(() => {
  const messages = [...document.querySelectorAll('#chat-history .msg-user')];
  const offset = %d;
  const message = messages[messages.length - 1 - offset];
  const edit = message && message.querySelector('button[title="Edit message"]');
  if (!edit) return {ok: false, reason: 'edit button not found', messages: messages.length};
  message.dataset.browserAuditEditTarget = 'true';
  edit.click();
  return {ok: true, messages: messages.length, offset};
})()
""" % args.edit_user_offset)
        _wait_for(
            cdp,
            "document.querySelector('[data-browser-audit-edit-target] .edit-save-btn') !== null",
        )
        editor = cdp.evaluate(r"""
(() => {
  const message = document.querySelector('[data-browser-audit-edit-target]');
  const field = message.querySelector('.edit-textarea');
  const button = message.querySelector('.edit-save-btn');
  field.value = field.value.replace(/\s*\[browser audit\]$/, '') + ' [browser audit]';
  field.dispatchEvent(new Event('input', {bubbles: true}));
  const result = {text: field.value, buttonBefore: button.textContent, disabledBefore: button.disabled};
  button.click();
  result.buttonImmediatelyAfter = button.textContent;
  result.disabledImmediatelyAfter = button.disabled;
  result.sendStateImmediatelyAfter = button.dataset.editSendState || null;
  result.sendDetailImmediatelyAfter = button.dataset.editSendDetail || null;
  result.toastImmediatelyAfter = document.getElementById('toast')?.textContent?.trim() || null;
  result.toastClassImmediatelyAfter = document.getElementById('toast')?.className || null;
  return result;
})()
""")

        turn_result = None
        feedback_result = None
        process_reload = None
        if args.wait_for_turn:
            turn_deadline = time.monotonic() + args.wait_for_turn
            last_turn_state = None
            while time.monotonic() < turn_deadline:
                cdp.drain(0.15)
                last_turn_state = cdp.evaluate(r"""
(() => {
  const cards = [...document.querySelectorAll('.qwen-process-card')];
  const lastCard = cards.at(-1) || null;
  const messages = [...document.querySelectorAll('#chat-history .msg')];
  const lastMessage = messages.at(-1) || null;
  return {
    editorPresent: !!document.querySelector('.edit-textarea'),
    processCards: cards.length,
    processComplete: !!lastCard?.classList.contains('complete'),
    processTitle: lastCard?.querySelector('.qwen-process-title')?.textContent?.trim() || null,
    lastRole: lastMessage?.classList.contains('msg-ai') ? 'assistant' :
      (lastMessage?.classList.contains('msg-user') ? 'user' : null),
    lastAssistantText: [...document.querySelectorAll('#chat-history .msg-ai .body')]
      .at(-1)?.textContent?.trim()?.slice(0, 240) || null,
    qwenError: [...document.querySelectorAll('#chat-history .msg-ai .body')]
      .some(el => el.textContent.includes('Qwen Companion error:')),
  };
})()
""")
                if (
                    last_turn_state.get("processComplete")
                    and last_turn_state.get("lastRole") == "assistant"
                ):
                    turn_result = {"completed": not last_turn_state.get("qwenError"), **last_turn_state}
                    break
                time.sleep(0.1)
            if turn_result is None:
                turn_result = {"completed": False, "timedOut": True, **(last_turn_state or {})}
            if turn_result.get("completed"):
                _wait_for(cdp, "document.querySelector('.qwen-feedback') !== null", timeout=10)
                cdp.evaluate(r"""
(() => {
  const panel = [...document.querySelectorAll('.qwen-feedback')].at(-1);
  const note = panel.querySelector('.qwen-feedback-note');
  note.value = 'Synthetic rendered-browser acceptance check';
  note.dispatchEvent(new Event('input', {bubbles: true}));
  [...panel.querySelectorAll('.qwen-feedback-choice')].find(button => button.textContent.trim() === 'Helpful').click();
  return true;
})()
""")
                _wait_for(
                    cdp,
                    "[...document.querySelectorAll('.qwen-feedback-status')].at(-1)?.textContent.trim() === 'Saved'",
                    timeout=20,
                )
                feedback_result = cdp.evaluate(r"""
(async () => {
  const panel = [...document.querySelectorAll('.qwen-feedback')].at(-1);
  const sessions = await fetch('/api/sessions', {credentials: 'same-origin'}).then(response => response.json());
  const current = sessions.find(session => session.id === window.sessionModule?.getCurrentSessionId?.());
  const evaluation = current?.project_id
    ? await fetch(`/api/g1/projects/${encodeURIComponent(current.project_id)}/evaluation`, {credentials: 'same-origin'}).then(response => response.json())
    : null;
  return {
    choices: [...panel.querySelectorAll('.qwen-feedback-choice')].map(button => button.textContent.trim()),
    selected: panel.querySelector('.qwen-feedback-choice.active')?.textContent?.trim() || null,
    note: panel.querySelector('.qwen-feedback-note')?.value || null,
    status: panel.querySelector('.qwen-feedback-status')?.textContent?.trim() || null,
    evaluationCounts: evaluation?.counts || null,
    evaluationEntries: evaluation?.entries?.length ?? null,
  };
})()
""")
                cdp.call("Page.reload", {"ignoreCache": False})
                _wait_for(cdp, "document.readyState === 'complete'", timeout=30)
                _wait_for(
                    cdp,
                    f"window.sessionModule?.getCurrentSessionId?.() === {json.dumps(args.session_id)}",
                    timeout=30,
                )
                _wait_for(cdp, "document.querySelector('.qwen-process-card.complete') !== null", timeout=20)
                process_reload = cdp.evaluate(r"""
(() => {
  const card = [...document.querySelectorAll('.qwen-process-card.complete')].at(-1);
  const panel = [...document.querySelectorAll('.qwen-feedback')].at(-1);
  return {
    persisted: !!card,
    title: card?.querySelector('.qwen-process-title')?.textContent?.trim() || null,
    toolLines: [...(card?.querySelectorAll('.qwen-process-tool-label') || [])]
      .map(node => node.textContent.trim()),
    selectedFeedback: panel?.querySelector('.qwen-feedback-choice.active')?.textContent?.trim() || null,
    feedbackNote: panel?.querySelector('.qwen-feedback-note')?.value || null,
  };
})()
""")

        # Pump CDP events while the async click handler crosses the network.
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            cdp.drain(0.15)
            requests = [
                event for event in cdp.events
                if event.get("method") == "Network.requestWillBeSent"
                and "/truncate" in event.get("params", {}).get("request", {}).get("url", "")
            ]
            if requests:
                break
            cdp.evaluate("true")

        cdp.drain(0.5)
        after = cdp.evaluate(DOM_AUDIT)
        state = cdp.evaluate(r"""
(() => ({
  currentSessionId: window.sessionModule?.getCurrentSessionId?.() || null,
  hash: location.hash,
  editButtonText: document.querySelector('.edit-save-btn')?.textContent || null,
  editButtonDisabled: document.querySelector('.edit-save-btn')?.disabled ?? null,
  editSendState: document.querySelector('.edit-save-btn')?.dataset?.editSendState || null,
  editSendDetail: document.querySelector('.edit-save-btn')?.dataset?.editSendDetail || null,
  editorPresent: !!document.querySelector('.edit-textarea'),
  userMessages: document.querySelectorAll('#chat-history .msg-user').length,
  aiMessages: document.querySelectorAll('#chat-history .msg-ai').length,
  visibleErrors: [...document.querySelectorAll('.error-toast, .toast.error, .notification.error')]
    .map(el => el.textContent.trim()).filter(Boolean),
  toast: document.getElementById('toast')?.textContent?.trim() || null,
  toastClass: document.getElementById('toast')?.className || null,
}))()
""")

        original_session_id = state.get("currentSessionId")
        project_fork_click = cdp.evaluate(r"""
(() => {
  const button = document.querySelector('[data-project-id] button[aria-label^="New thread in "]');
  if (!button) return {ok: false, reason: 'project thread button missing'};
  button.click();
  return {ok: true};
})()
""")
        project_fork = {"completed": False, **project_fork_click}
        if project_fork_click.get("ok"):
            _wait_for(
                cdp,
                f"window.sessionModule?.getCurrentSessionId?.() && window.sessionModule.getCurrentSessionId() !== {json.dumps(original_session_id)} && document.querySelectorAll('#chat-history .msg').length === 0",
                timeout=20,
            )
            project_fork = cdp.evaluate(r"""
(async () => {
  const currentId = window.sessionModule?.getCurrentSessionId?.();
  const sessions = await fetch('/api/sessions', {credentials: 'same-origin'}).then(response => response.json());
  const current = sessions.find(session => session.id === currentId);
  const original = sessions.find(session => session.id === %s);
  return {
    completed: !!current,
    idChanged: currentId !== %s,
    scopeKind: current?.scope_kind || null,
    projectId: current?.project_id || null,
    primary: current?.is_scope_primary ?? null,
    harness: current?.harness_kind || null,
    messageCount: current?.message_count ?? null,
    originalStillPresent: !!original,
    renderedMessages: document.querySelectorAll('#chat-history .msg').length,
    workspaceVisible: getComputedStyle(document.getElementById('workspace-indicator-btn')).display !== 'none',
  };
})()
""" % (json.dumps(original_session_id), json.dumps(original_session_id)))

        route_persistence = {"completed": False}
        fork_session_id = cdp.evaluate("window.sessionModule?.getCurrentSessionId?.() || null")
        if project_fork.get("completed") and fork_session_id:
            cdp.evaluate("document.getElementById('qwen-toggle-btn').click()")
            _wait_for(
                cdp,
                r"""
(async () => {
  const id = %s;
  const rows = await fetch('/api/sessions', {credentials: 'same-origin'}).then(response => response.json());
  return rows.find(session => session.id === id)?.harness_kind === 'native';
})()
""" % json.dumps(fork_session_id),
                timeout=20,
            )
            native_state = cdp.evaluate(r"""
(() => ({
  qwenActive: document.getElementById('qwen-toggle-btn')?.classList.contains('active') || false,
  agentMode: document.getElementById('mode-agent-btn')?.classList.contains('active') || false,
}))()
""")
            native_send = cdp.evaluate(r"""
(() => {
  const input = document.getElementById('message');
  const form = document.getElementById('chat-form');
  if (!input || !form) return {started: false, reason: 'composer missing'};
  input.value = 'Synthetic native project routing audit.';
  input.dispatchEvent(new Event('input', {bubbles: true}));
  if (form.requestSubmit) form.requestSubmit();
  else form.dispatchEvent(new Event('submit', {bubbles: true, cancelable: true}));
  return {started: true};
})()
""")
            native_request = None
            native_deadline = time.monotonic() + 20
            while time.monotonic() < native_deadline:
                cdp.drain(0.15)
                matches = [
                    event for event in cdp.events
                    if event.get("method") == "Network.requestWillBeSent"
                    and "/api/chat_stream" in event.get("params", {}).get("request", {}).get("url", "")
                ]
                if matches:
                    request = matches[-1].get("params", {}).get("request", {})
                    native_request = {
                        "method": request.get("method"),
                        "url": request.get("url"),
                        "postData": request.get("postData", ""),
                    }
                    break
            cdp.evaluate("window.chatModule?.abortCurrentRequest?.(true)")
            try:
                _wait_for(cdp, "document.querySelector('.send-btn')?.dataset?.mode !== 'streaming'", timeout=20)
            except TimeoutError:
                pass
            native_send.update({
                "requestSeen": native_request is not None,
                "allowBashFalse": bool(native_request and 'name=\"allow_bash\"' in native_request["postData"] and "false" in native_request["postData"]),
            })
            _wait_for(cdp, "document.getElementById('qwen-toggle-btn')?.disabled === false", timeout=20)
            readiness = cdp.evaluate(r"""
(async () => {
  const response = await fetch('/api/g1/status', {credentials: 'same-origin', cache: 'no-store'});
  return response.ok ? await response.json() : {qwen_ready: false};
})()
""")
            cdp.evaluate("document.getElementById('qwen-toggle-btn').click()")
            if readiness.get("qwen_ready"):
                _wait_for(
                    cdp,
                    r"""
(async () => {
  const id = %s;
  const rows = await fetch('/api/sessions', {credentials: 'same-origin'}).then(response => response.json());
  return rows.find(session => session.id === id)?.harness_kind === 'qwen';
})()
""" % json.dumps(fork_session_id),
                    timeout=20,
                )
            else:
                _wait_for(cdp, "document.getElementById('qwen-toggle-btn')?.disabled === false", timeout=20)
                blocked_state = cdp.evaluate(r"""
(async () => {
  const id = %s;
  const rows = await fetch('/api/sessions', {credentials: 'same-origin'}).then(response => response.json());
  return {
    harness: rows.find(session => session.id === id)?.harness_kind || null,
    toast: document.getElementById('toast')?.textContent?.trim() || null,
  };
})()
""" % json.dumps(fork_session_id))
            model_switch = cdp.evaluate(r"""
(async () => {
  const id = %s;
  const body = new FormData();
  body.append('model', 'ui-audit-model');
  body.append('endpoint_url', 'http://127.0.0.1:9/v1');
  body.append('endpoint_id', 'ui-audit-endpoint');
  const response = await fetch(`/api/session/${encodeURIComponent(id)}`, {
    method: 'PATCH', body, credentials: 'same-origin',
  });
  return {status: response.status, body: await response.json().catch(() => ({}))};
})()
""" % json.dumps(fork_session_id))
            cdp.call("Page.reload", {"ignoreCache": False})
            _wait_for(cdp, "document.readyState === 'complete'", timeout=30)
            _wait_for(
                cdp,
                f"window.sessionModule?.getCurrentSessionId?.() === {json.dumps(fork_session_id)}",
                timeout=30,
            )
            route_persistence = cdp.evaluate(r"""
(async () => {
  const id = %s;
  const rows = await fetch('/api/sessions', {credentials: 'same-origin'}).then(response => response.json());
  const current = rows.find(session => session.id === id);
  return {
    completed: true,
    nativeToggleState: %s,
    nativeSend: %s,
    readiness: %s,
    blockedEnableState: %s,
    modelSwitch: %s,
    sessionId: current?.id || null,
    model: current?.model || null,
    endpointId: current?.endpoint_id || null,
    harness: current?.harness_kind || null,
    projectId: current?.project_id || null,
    agentMode: document.getElementById('mode-agent-btn')?.classList.contains('active') || false,
    qwenActive: document.getElementById('qwen-toggle-btn')?.classList.contains('active') || false,
    workspaceVisible: getComputedStyle(document.getElementById('workspace-indicator-btn')).display !== 'none',
  };
})()
""" % (
                    json.dumps(fork_session_id),
                    json.dumps(native_state),
                    json.dumps(native_send),
                    json.dumps(readiness),
                    json.dumps(blocked_state if not readiness.get("qwen_ready") else None),
                    json.dumps(model_switch),
                ))

        companion_homes = {}
        for scope, label in (("personal", "Personal Advisor"), ("computer", "Computer Help")):
            clicked = cdp.evaluate(r"""
(() => {
  const label = %s;
  const button = [...document.querySelectorAll('#session-list button.companion-home')]
    .find(node => node.textContent.trim().startsWith(label));
  if (!button) return false;
  button.click();
  return true;
})()
""" % json.dumps(label))
            if not clicked:
                companion_homes[scope] = {"opened": False, "reason": "sidebar home missing"}
                continue
            home_wait_expression = r"""
(async () => {
  const rows = await fetch('/api/sessions', {credentials: 'same-origin'}).then(response => response.json());
  const current = rows.find(session => session.id === window.sessionModule?.getCurrentSessionId?.());
  return current?.scope_kind === %s;
})()
""" % json.dumps(scope)
            try:
                _wait_for(cdp, home_wait_expression, timeout=20)
            except TimeoutError as error:
                diagnostic = cdp.evaluate(r"""
(async () => {
  const rows = await fetch('/api/sessions', {credentials: 'same-origin'}).then(response => response.json());
  const currentId = window.sessionModule?.getCurrentSessionId?.();
  return {
    currentId,
    current: rows.find(session => session.id === currentId) || null,
    requestedScopeHomes: rows.filter(session => session.scope_kind === %s),
    hash: location.hash,
    toast: document.getElementById('toast')?.textContent?.trim() || null,
  };
})()
""" % json.dumps(scope))
                raise RuntimeError(f"Companion home {scope} was created but not selected: {diagnostic}") from error
            first_home = cdp.evaluate(r"""
(async () => {
  const rows = await fetch('/api/sessions', {credentials: 'same-origin'}).then(response => response.json());
  const currentId = window.sessionModule?.getCurrentSessionId?.();
  const current = rows.find(session => session.id === currentId);
  return {
    id: currentId,
    scopeKind: current?.scope_kind || null,
    primary: current?.is_scope_primary ?? null,
    harness: current?.harness_kind || null,
    chatMode: document.getElementById('mode-chat-btn')?.classList.contains('active') || false,
    qwenActive: document.getElementById('qwen-toggle-btn')?.classList.contains('active') || false,
    workspaceVisible: getComputedStyle(document.getElementById('workspace-indicator-btn')).display !== 'none',
  };
})()
""")
            cdp.evaluate(r"""
(() => {
  const label = %s;
  const button = [...document.querySelectorAll('#session-list button.companion-home')]
    .find(node => node.textContent.trim().startsWith(label));
  button?.click();
})()
""" % json.dumps(label))
            time.sleep(0.3)
            second_id = cdp.evaluate("window.sessionModule?.getCurrentSessionId?.() || null")
            companion_homes[scope] = {
                "opened": True,
                **first_home,
                "reopenSameId": first_home.get("id") == second_id,
            }

        project_delete = {"completed": False}
        deleted_project_id = project_fork.get("projectId")
        if deleted_project_id:
            delete_opened = cdp.evaluate(r"""
(() => {
  const row = document.querySelector(`[data-project-id="${CSS.escape(%s)}"]`);
  const button = row?.querySelector('button[aria-label^="Delete project "]');
  if (!button) return false;
  button.click();
  return true;
})()
""" % json.dumps(deleted_project_id))
            if delete_opened:
                _wait_for(
                    cdp,
                    "getComputedStyle(document.getElementById('styled-confirm-overlay')).display !== 'none'",
                    timeout=10,
                )
                confirmation = cdp.evaluate(r"""
(() => ({
  message: document.getElementById('styled-confirm-msg')?.textContent?.trim() || null,
  confirmText: document.getElementById('styled-confirm-ok')?.textContent?.trim() || null,
}))()
""")
                cdp.evaluate("document.getElementById('styled-confirm-ok').click()")
                _wait_for(
                    cdp,
                    r"""
(async () => {
  const projectId = %s;
  const projects = await fetch('/api/g1/projects', {credentials: 'same-origin'}).then(response => response.json());
  const sessions = await fetch('/api/sessions', {credentials: 'same-origin'}).then(response => response.json());
  return !projects.some(project => project.id === projectId)
    && !sessions.some(session => session.project_id === projectId)
    && !document.querySelector(`[data-project-id="${CSS.escape(projectId)}"]`);
})()
""" % json.dumps(deleted_project_id),
                    timeout=20,
                )
                project_delete = cdp.evaluate(r"""
(async () => {
  const projectId = %s;
  const projects = await fetch('/api/g1/projects', {credentials: 'same-origin'}).then(response => response.json());
  const sessions = await fetch('/api/sessions', {credentials: 'same-origin'}).then(response => response.json());
  return {
    completed: true,
    confirmation: %s,
    projectAbsent: !projects.some(project => project.id === projectId),
    projectSessionsAbsent: !sessions.some(session => session.project_id === projectId),
    sidebarRowAbsent: !document.querySelector(`[data-project-id="${CSS.escape(projectId)}"]`),
  };
})()
""" % (json.dumps(deleted_project_id), json.dumps(confirmation)))

        # Re-run the core navigation/modal geometry at a phone viewport. This
        # uses the same rendered app and disposable identity, not a DOM-only
        # approximation of the responsive CSS.
        cdp.call("Emulation.setDeviceMetricsOverride", {
            "width": 390, "height": 844, "deviceScaleFactor": 1, "mobile": True,
        })
        time.sleep(0.25)
        cdp.evaluate(r"""
(() => {
  const sidebar = document.getElementById('sidebar');
  if (sidebar && (sidebar.classList.contains('hidden') || document.documentElement.classList.contains('ody-sidebar-off'))) {
    document.getElementById('hamburger-btn')?.click();
  }
})()
""")
        _wait_for(
            cdp,
            "!document.getElementById('sidebar')?.classList.contains('hidden') && !document.documentElement.classList.contains('ody-sidebar-off')",
            timeout=10,
        )
        mobile_navigation = cdp.evaluate(r"""
(() => {
  const sidebar = document.getElementById('sidebar');
  const rect = sidebar?.getBoundingClientRect();
  return {
    sidebarVisible: !!rect && rect.width > 0 && rect.right > 0 && rect.left < innerWidth,
    sidebarWithinViewport: !!rect && rect.left >= -1 && rect.right <= innerWidth + 1,
    companionVisible: !!document.getElementById('companion-qwen-readiness'),
  };
})()
""")
        cdp.evaluate("document.getElementById('companion-new-project-btn')?.click()")
        _wait_for(cdp, "document.getElementById('companion-project-modal') !== null", timeout=20)
        mobile_layout = cdp.evaluate(r"""
(() => {
  const sidebar = document.getElementById('sidebar');
  const modal = document.querySelector('#companion-project-modal .modal-content');
  const name = document.getElementById('companion-project-name');
  const sidebarRect = sidebar?.getBoundingClientRect();
  const modalRect = modal?.getBoundingClientRect();
  return {
    viewport: {width: innerWidth, height: innerHeight},
    navigation: %s,
    modalVisible: !!modal && getComputedStyle(modal).display !== 'none',
    modalWithinViewport: !!modalRect && modalRect.left >= -1 && modalRect.right <= innerWidth + 1,
    sidebarWithinViewport: !!sidebarRect && sidebarRect.left >= -1 && sidebarRect.right <= innerWidth + 1,
    focusedField: document.activeElement?.id || null,
    horizontalOverflow: document.documentElement.scrollWidth > innerWidth + 1,
  };
})()
""" % json.dumps(mobile_navigation))
        cdp.evaluate("document.querySelector('#companion-project-modal .companion-project-cancel')?.click()")

        request_rows = []
        response_by_id = {}
        console_rows = []
        for event in cdp.events:
            method = event.get("method")
            params = event.get("params", {})
            if method == "Network.responseReceived":
                response_by_id[params.get("requestId")] = params.get("response", {}).get("status")
            elif method == "Runtime.consoleAPICalled":
                console_rows.append({
                    "type": params.get("type"),
                    "values": [arg.get("value", arg.get("description", "")) for arg in params.get("args", [])],
                })
            elif method == "Runtime.exceptionThrown":
                console_rows.append({"type": "exception", "values": [params.get("exceptionDetails", {}).get("text", "")]})
        for event in cdp.events:
            if event.get("method") != "Network.requestWillBeSent":
                continue
            params = event.get("params", {})
            request = params.get("request", {})
            url = request.get("url", "")
            if f":{args.app_port}/api/" not in url:
                continue
            request_rows.append({
                "method": request.get("method"),
                "url": url,
                "status": response_by_id.get(params.get("requestId")),
            })

        screenshot = cdp.call("Page.captureScreenshot", {"format": "png", "captureBeyondViewport": False})
        (args.output / "inline-edit.png").write_bytes(base64.b64decode(screenshot["data"]))
        report = {
            "page": page.get("url"),
            "runtime": runtime,
            "projectModalOpen": project_modal_open,
            "projectModal": project_modal,
            "projectModalDom": project_modal_dom,
            "workspacePicker": workspace_picker,
            "messageDelete": message_delete,
            "persistedProcessFixture": persisted_process_fixture,
            "editOpened": edit_opened,
            "editor": editor,
            "turn": turn_result,
            "feedback": feedback_result,
            "processReload": process_reload,
            "state": state,
            "projectFork": project_fork,
            "routePersistence": route_persistence,
            "companionHomes": companion_homes,
            "projectDelete": project_delete,
            "mobileLayout": mobile_layout,
            "apiRequests": request_rows,
            "console": console_rows,
            "domBeforeEdit": before,
            "domAfterEdit": after,
        }
        (args.output / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
        truncate_seen = any("/truncate" in row["url"] and row["method"] == "POST" for row in request_rows)
        qwen_turn_seen = any("/api/g1/project-turn/stream" in row["url"] and row["method"] == "POST" for row in request_rows)
        shell_api_seen = any("/api/shell/" in row["url"] for row in request_rows)
        summary = {
            "runtime": runtime,
            "inlineEdit": {
                "opened": bool(edit_opened.get("ok")),
                "sendState": editor.get("sendStateImmediatelyAfter"),
                "truncatePostSeen": truncate_seen,
                "buttonImmediatelyAfter": editor.get("buttonImmediatelyAfter"),
                "toastImmediatelyAfter": editor.get("toastImmediatelyAfter"),
                "qwenTurnPostSeen": qwen_turn_seen,
                "shellApiSeen": shell_api_seen,
                "turn": turn_result,
                "feedback": feedback_result,
                "processReload": process_reload,
            },
            "messageDelete": message_delete,
            "persistedProcessFixture": persisted_process_fixture,
            "renderedDom": {
                "duplicateFieldIds": [row["id"] for row in before["duplicateFieldIds"]],
                "missingIdentityCount": len(before["missingIdentity"]),
                "unlabeledFieldCount": len(before["unlabeledFields"]),
                "unassociatedLabelCount": len(before["unassociatedLabels"]),
            },
            "projectCreation": {
                "modalOpened": bool(project_modal_open.get("ok") and project_modal.get("visible")),
                "fieldIds": [field["id"] for field in project_modal.get("fields", [])],
                "folderPickerOpened": bool(workspace_picker.get("visible")),
                "folderPickerTitle": workspace_picker.get("title"),
                "duplicateFieldIds": [row["id"] for row in project_modal_dom["duplicateFieldIds"]],
                "unlabeledFieldCount": len(project_modal_dom["unlabeledFields"]),
                "unassociatedLabelCount": len(project_modal_dom["unassociatedLabels"]),
            },
            "projectFork": project_fork,
            "routePersistence": route_persistence,
            "companionHomes": companion_homes,
            "projectDelete": project_delete,
            "mobileLayout": mobile_layout,
            "report": str(args.output / "report.json"),
            "screenshot": str(args.output / "inline-edit.png"),
        }
        print(json.dumps(summary, indent=2, ensure_ascii=False))
        passed = truncate_seen
        passed = passed and message_delete.get("completed") is True
        passed = passed and message_delete.get("sessionReloaded") is True
        passed = passed and message_delete.get("absentAfterReload") is True
        if args.expect_persisted_process:
            passed = passed and persisted_process_fixture.get("present") is True
            passed = passed and persisted_process_fixture.get("title") == "Process · worked for 12s"
            passed = passed and persisted_process_fixture.get("toolLines") == ["Read: README.md"]
            passed = passed and persisted_process_fixture.get("selectedFeedback") == "Helpful"
            passed = passed and persisted_process_fixture.get("feedbackNote") == "Persisted synthetic feedback"
        passed = passed and runtime.get("buildId") == "20260804g15ui29"
        passed = passed and runtime.get("htmlBuildId") == runtime.get("buildId")
        passed = passed and runtime.get("qwenTogglePresent") is True
        passed = passed and runtime.get("legacyShellControlCount") == 0
        passed = passed and runtime.get("chatsExpanded") is True
        passed = passed and bool(runtime.get("qwenReadiness"))
        passed = passed and runtime.get("scopeBanner", "").startswith("Project · UI audit project")
        passed = passed and bool(project_fork.get("completed"))
        passed = passed and project_fork.get("scopeKind") == "project" and project_fork.get("primary") is False
        passed = passed and project_fork.get("messageCount") == 0 and project_fork.get("originalStillPresent") is True
        passed = passed and route_persistence.get("completed") is True
        passed = passed and route_persistence.get("nativeToggleState", {}).get("qwenActive") is False
        passed = passed and route_persistence.get("nativeToggleState", {}).get("agentMode") is True
        passed = passed and route_persistence.get("nativeSend", {}).get("started") is True
        passed = passed and route_persistence.get("nativeSend", {}).get("requestSeen") is True
        passed = passed and route_persistence.get("nativeSend", {}).get("allowBashFalse") is True
        passed = passed and shell_api_seen is False
        passed = passed and route_persistence.get("modelSwitch", {}).get("status") == 200
        passed = passed and route_persistence.get("model") == "ui-audit-model"
        passed = passed and route_persistence.get("endpointId") == "ui-audit-endpoint"
        route_ready = route_persistence.get("readiness", {}).get("qwen_ready") is True
        passed = passed and route_persistence.get("harness") == ("qwen" if route_ready else "native")
        passed = passed and route_persistence.get("agentMode") is True
        passed = passed and route_persistence.get("qwenActive") is route_ready
        if not route_ready:
            passed = passed and route_persistence.get("blockedEnableState", {}).get("harness") == "native"
            passed = passed and "Qwen setup needed" in route_persistence.get("blockedEnableState", {}).get("toast", "")
        passed = passed and route_persistence.get("workspaceVisible") is True
        for home in companion_homes.values():
            passed = passed and home.get("opened") is True and home.get("primary") is True
            passed = passed and home.get("reopenSameId") is True and home.get("harness") == "native"
            passed = passed and home.get("chatMode") is True and home.get("qwenActive") is False
            passed = passed and home.get("workspaceVisible") is False
        passed = passed and project_delete.get("completed") is True
        passed = passed and project_delete.get("projectAbsent") is True
        passed = passed and project_delete.get("projectSessionsAbsent") is True
        passed = passed and project_delete.get("sidebarRowAbsent") is True
        passed = passed and mobile_layout.get("viewport", {}).get("width") == 390
        passed = passed and mobile_layout.get("navigation", {}).get("sidebarVisible") is True
        passed = passed and mobile_layout.get("navigation", {}).get("companionVisible") is True
        passed = passed and mobile_layout.get("modalVisible") is True
        passed = passed and mobile_layout.get("modalWithinViewport") is True
        passed = passed and mobile_layout.get("navigation", {}).get("sidebarWithinViewport") is True
        passed = passed and mobile_layout.get("focusedField") == "companion-project-name"
        passed = passed and mobile_layout.get("horizontalOverflow") is False
        if args.wait_for_turn:
            passed = passed and qwen_turn_seen and bool(turn_result and turn_result.get("completed"))
            passed = passed and bool(feedback_result and feedback_result.get("status") == "Saved")
            passed = passed and feedback_result.get("evaluationCounts", {}).get("helpful", 0) >= 1
            passed = passed and bool(process_reload and process_reload.get("persisted"))
            passed = passed and bool(process_reload.get("toolLines"))
            passed = passed and all(
                line.startswith(("Read: ", "Search: ", "List: ", "Inspect: ", "Shell: "))
                for line in process_reload.get("toolLines", [])
            )
            passed = passed and process_reload.get("selectedFeedback") == "Helpful"
        return 0 if passed else 2
    finally:
        cdp.close()


if __name__ == "__main__":
    raise SystemExit(main())
