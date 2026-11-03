// Companion homes: the sidebar section, the scope banner, the capability
// drawer and the project-changes, task-roots and memory modals.
//
// This is fork code that used to sit in the middle of static/js/sessions.js,
// where every upstream merge had to re-resolve a thousand lines of it. It
// renders with the same native components as before (uiModule, the shared
// modal classes), so moving it changed no markup and no styling.
//
// It deliberately does not import sessions.js. The handful of things it needs
// from there - the live session list, the selected chat, and the two
// navigation calls - arrive through bindCompanionHost, which keeps the
// dependency one-way and lets sessions.js stay the only owner of that state.

import Storage from './storage.js';
import uiModule from './ui.js?v=20260916largetoolscroll1';
import workspaceModule from './workspace.js';

const API_BASE = window.location.origin;

let host = {
  sessions: () => [],
  companionProjects: () => [],
  currentSessionId: () => null,
  setCurrentSessionId: () => {},
  loadSessions: async () => {},
  selectSession: async () => {},
};

/** Hand over the session state and navigation this module borrows. */
export function bindCompanionHost(bindings) {
  host = { ...host, ...bindings };
}

function _syncCompanionScopeBanner(meta) {
  const banner = document.getElementById('companion-scope-banner');
  const title = document.getElementById('companion-scope-title');
  const detail = document.getElementById('companion-scope-detail');
  const contextButton = document.getElementById('overflow-companion-context-btn');
  const capabilitiesButton = document.getElementById('overflow-chat-capabilities-btn');
  if (!banner || !title || !detail) return;
  const scope = meta?.scope_kind;
  const closeMoreTools = () => document.getElementById('overflow-menu')?.classList.add('hidden');
  if (capabilitiesButton) {
    capabilitiesButton.hidden = !meta?.id;
    capabilitiesButton.onclick = () => { closeMoreTools(); openChatCapabilities(meta); };
  }
  if (!['project', 'personal', 'computer'].includes(scope)) {
    banner.hidden = true;
    title.textContent = '';
    detail.textContent = '';
    if (contextButton) contextButton.hidden = true;
    return;
  }
  if (scope === 'project') {
    const projectName = meta.project_name || meta.name || 'Project';
    const workspaceName = String(meta.workspace_root || '').replace(/[\\/]+$/, '').split(/[\\/]/).pop();
    title.textContent = `Project · ${projectName}`;
    detail.textContent = `${_accessSummary(meta.chat_capabilities)}${workspaceName ? ` · ${workspaceName}` : ''}`;
  } else if (scope === 'personal') {
    title.textContent = 'Personal Advisor';
    detail.textContent = meta.chat_capabilities?.memory_write?.effective
      ? 'Native · personal scope · updates memory'
      : 'Native · personal scope';
  } else {
    title.textContent = 'Computer Help';
    detail.textContent = 'Native · diagnostics';
  }
  banner.hidden = false;
  const supportsMemory = ['project', 'personal', 'computer'].includes(scope);
  if (contextButton) {
    contextButton.hidden = !supportsMemory;
    contextButton.onclick = () => {
      closeMoreTools();
      openCompanionMemory(meta, 'context');
    };
  }
}

// What the assistant can see, then what it can do. The server says which
// grants a chat offers, this only orders and explains them.
const _CAPABILITY_GROUPS = [
  ['Can see', ['workspace_read', 'web_search', 'system_observe', 'sandbox_read']],
  ['Can do', ['project_shell', 'project_write', 'memory_write']],
];
const _CAPABILITY_HELP = {
  web_search: 'Search current public information. The assistant may request this when it is off.',
  workspace_read: 'Read the project files. Nothing is changed.',
  system_observe: 'Read a sanitized snapshot of this computer. It never changes settings.',
  sandbox_read: 'Run admitted read-only commands inside the qualified Podman sandbox.',
  project_shell: 'Run commands, tests and git in an offline copy of the project. Nothing they write is kept.',
  project_write: 'Change project files. Each edit is applied, verified and listed in Project changes, where you can undo it.',
  memory_write: 'Save facts, decisions and preferences to this home\'s memory. Each change is a revision you can review in Context.',
};

function _accessSummary(caps) {
  const on = (name) => !!caps?.[name]?.effective;
  if (!caps || !Object.keys(caps).length) return 'Native';
  const parts = [];
  if (on('project_write')) parts.push('edits files');
  else if (on('workspace_read') || on('project_shell')) parts.push('read-only');
  if (on('project_shell')) parts.push('runs commands');
  if (on('memory_write')) parts.push('updates memory');
  return parts.length ? `Native · ${parts.join(' · ')}` : 'Native · no project access';
}

function _syncCapabilityControls(meta, capabilities = null) {
  const caps = capabilities || meta?.chat_capabilities || {};
  const webEnabled = !!caps.web_search?.effective;
  const mode = (Storage.loadToggleState().mode || 'chat');
  if (typeof window.__odysseusSetToolEnabled === 'function') {
    window.__odysseusSetToolEnabled('web', webEnabled, mode);
  } else {
    const web = document.getElementById('web-toggle');
    const webButton = document.getElementById('web-toggle-btn');
    if (web) web.checked = webEnabled;
    webButton?.classList.toggle('active', webEnabled);
    webButton?.setAttribute('aria-pressed', String(webEnabled));
  }
  if (meta?.id && meta.id === host.currentSessionId()) _syncCompanionScopeBanner(meta);
  const observe = document.getElementById('computer-observe-btn');
  if (observe) {
    const enabled = !!caps.system_observe?.effective;
    observe.hidden = !enabled;
    observe.style.display = enabled ? '' : 'none';
    observe.disabled = !enabled;
    observe.title = enabled ? 'Run safe, read-only system inspection' : 'Enable System inspection in Chat capabilities';
  }
}

async function _loadChatCapabilities(meta) {
  if (!meta?.id) return {};
  try {
    const response = await fetch(`/api/g1/sessions/${encodeURIComponent(meta.id)}/chat-capabilities`, {
      credentials: 'same-origin', cache: 'no-store',
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || 'Could not load chat capabilities');
    meta.chat_capabilities = data.capabilities || {};
    _syncCapabilityControls(meta, meta.chat_capabilities);
    return meta.chat_capabilities;
  } catch (error) {
    // Older server instances should not make opening ordinary chats fail.
    console.warn('Could not load chat capabilities:', error);
    _syncCapabilityControls(meta, meta.chat_capabilities || {});
    return meta.chat_capabilities || {};
  }
}

async function _persistChatCapability(meta, name, enabled) {
  const response = await fetch(`/api/g1/sessions/${encodeURIComponent(meta.id)}/chat-capabilities`, {
    method: 'PUT', credentials: 'same-origin', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({name, enabled: !!enabled}),
  });
  const raw = await response.text();
  let data = {};
  try { data = raw ? JSON.parse(raw) : {}; } catch (_) {}
  if (!response.ok) throw new Error(data.detail || `Could not update capability (server error ${response.status})`);
  meta.chat_capabilities = data.capabilities || {};
  _syncCapabilityControls(meta, meta.chat_capabilities);
  return meta.chat_capabilities;
}

async function openChatCapabilities(meta) {
  if (!meta?.id) { uiModule.showToast?.('Open a chat first', 1800); return; }
  const capabilities = await _loadChatCapabilities(meta);
  document.getElementById('chat-capabilities-modal')?.remove();
  const modal = document.createElement('div');
  modal.id = 'chat-capabilities-modal'; modal.className = 'modal';
  const content = document.createElement('div');
  content.className = 'modal-content companion-memory-modal';
  content.setAttribute('role', 'dialog'); content.setAttribute('aria-modal', 'true');
  const header = document.createElement('div'); header.className = 'modal-header';
  const title = document.createElement('h4'); title.textContent = 'Chat capabilities';
  const close = document.createElement('button'); close.type = 'button'; close.className = 'close-btn'; close.setAttribute('aria-label', 'Close'); close.textContent = '✖';
  close.onclick = () => modal.remove(); header.append(title, close); content.append(header);
  const intro = document.createElement('p');
  intro.className = 'companion-memory-help';
  intro.textContent = 'What the assistant can see and do in this chat. Anything switched on works without asking each time. Edits and memory changes are recorded, so you can review and undo them.';
  content.append(intro);
  for (const [groupLabel, names] of _CAPABILITY_GROUPS) {
    // Older servers do not send `offered`, so only an explicit false hides a row.
    const offered = names.filter((name) => capabilities[name] && capabilities[name].offered !== false);
    if (!offered.length) continue;
    const heading = document.createElement('h5');
    heading.className = 'companion-grant-group';
    heading.textContent = groupLabel;
    content.append(heading);
    for (const name of offered) {
      const item = capabilities[name];
      const row = document.createElement('div'); row.className = 'companion-grant-card';
      const label = document.createElement('strong'); label.textContent = item.label;
      const detail = document.createElement('span');
      detail.textContent = item.reason || _CAPABILITY_HELP[name] || '';
      const button = document.createElement('button'); button.type = 'button';
      const enabled = !!item.requested;
      button.textContent = enabled ? 'On' : (item.available ? 'Off' : 'Unavailable');
      button.setAttribute('aria-pressed', String(enabled));
      // A stale unavailable grant can always be switched off; only enabling is
      // blocked by readiness or a missing source.
      button.disabled = !item.available && !enabled;
      button.onclick = async () => {
        button.disabled = true; button.textContent = 'Updating…';
        try {
          await _persistChatCapability(meta, name, !enabled);
          modal.remove();
          await openChatCapabilities(meta);
        } catch (error) {
          uiModule.showError?.(error.message || 'Could not update capability');
          button.disabled = false; button.textContent = enabled ? 'On' : 'Off';
        }
      };
      row.append(label, detail, button); content.append(row);
    }
  }
  if (meta.scope_kind === 'project' && meta.project_id) {
    const changes = document.createElement('button');
    changes.type = 'button'; changes.className = 'companion-task-roots-button';
    changes.textContent = 'Review project changes';
    changes.title = 'See every recorded edit to this project and undo one';
    changes.onclick = () => { modal.remove(); openProjectChanges(meta); };
    content.append(changes);
  }
  if (meta.scope_kind === 'computer') {
    const taskRoots = document.createElement('button');
    taskRoots.type = 'button'; taskRoots.className = 'companion-task-roots-button';
    taskRoots.textContent = 'Manage task roots';
    taskRoots.title = 'Prepare dedicated folders for a future contained Computer Help task';
    taskRoots.onclick = () => { modal.remove(); openComputerTaskRoots(meta); };
    content.append(taskRoots);
  }
  modal.append(content); document.body.appendChild(modal);
}

const _CHANGE_STATUS = {
  applied: 'Applied', rolled_back: 'Undone', proposed: 'Waiting for review', rejected: 'Rejected',
  stale: 'Stale', apply_failed: 'Failed, restored', rollback_conflict: 'Undo blocked by later edits',
};

// Every change to a project checkout is a recorded change set. This list is
// where the owner reviews them and undoes one.
async function openProjectChanges(meta) {
  if (!meta?.project_id) return;
  let rows;
  try {
    const response = await fetch(`/api/companion/projects/${encodeURIComponent(meta.project_id)}/patches`, {
      credentials: 'same-origin', cache: 'no-store',
    });
    rows = await response.json().catch(() => null);
    if (!response.ok || !Array.isArray(rows)) throw new Error(rows?.detail || 'Could not load project changes');
  } catch (error) {
    uiModule.showError?.(error.message || 'Could not load project changes'); return;
  }
  document.getElementById('project-changes-modal')?.remove();
  const modal = document.createElement('div'); modal.id = 'project-changes-modal'; modal.className = 'modal';
  const content = document.createElement('div'); content.className = 'modal-content companion-memory-modal';
  content.setAttribute('role', 'dialog'); content.setAttribute('aria-modal', 'true');
  const header = document.createElement('div'); header.className = 'modal-header';
  const title = document.createElement('h4'); title.textContent = 'Project changes';
  const close = document.createElement('button'); close.type = 'button'; close.className = 'close-btn'; close.setAttribute('aria-label', 'Close'); close.textContent = '✖';
  close.onclick = () => modal.remove(); header.append(title, close); content.append(header);
  const intro = document.createElement('p'); intro.className = 'companion-memory-help';
  intro.textContent = 'Newest first. Undo restores the files exactly as they were before that change, and refuses when a later edit touched the same lines. Undo newer changes first.';
  content.append(intro);
  const list = document.createElement('div'); list.className = 'companion-task-root-list';
  if (!rows.length) {
    const empty = document.createElement('p'); empty.className = 'companion-memory-help';
    empty.textContent = 'No change has been made to this project from Odysseus yet.';
    list.append(empty);
  }
  rows.slice(0, 50).forEach((change) => {
    const row = document.createElement('div'); row.className = 'companion-grant-card';
    const label = document.createElement('strong'); label.textContent = change.summary || 'Project change';
    const files = Array.isArray(change.files) ? change.files : [];
    const when = change.applied_at || change.created_at;
    const whenText = when ? new Date(`${when}Z`).toLocaleString() : '';
    const detail = document.createElement('span');
    detail.textContent = [
      _CHANGE_STATUS[change.status] || change.status,
      whenText,
      files.map((file) => `${file.path} +${file.added || 0}/-${file.removed || 0}`).join(', '),
    ].filter(Boolean).join(' · ');
    row.append(label, detail);
    if (change.status === 'applied') {
      const undo = document.createElement('button'); undo.type = 'button'; undo.textContent = 'Undo';
      undo.onclick = async () => {
        undo.disabled = true; undo.textContent = 'Undoing…';
        try {
          const response = await fetch(`/api/companion/patches/${encodeURIComponent(change.id)}/rollback`, {
            method: 'POST', credentials: 'same-origin', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({expected_revision: Number(change.revision)}),
          });
          const result = await response.json().catch(() => ({}));
          if (!response.ok) throw new Error(result.detail || 'Could not undo this change');
          uiModule.showToast?.('Change undone', 1800);
        } catch (error) {
          uiModule.showError?.(error.message || 'Could not undo this change');
        }
        modal.remove(); openProjectChanges(meta);
      };
      row.append(undo);
    }
    list.append(row);
  });
  content.append(list);
  modal.append(content); document.body.appendChild(modal);
  modal.addEventListener('click', (event) => { if (event.target === modal) modal.remove(); });
}

async function openComputerTaskRoots(meta) {
  if (!meta?.id || meta.scope_kind !== 'computer') return;
  let payload;
  try {
    const response = await fetch(`/api/companion/computer/task-roots?session_id=${encodeURIComponent(meta.id)}`, {
      credentials: 'same-origin', cache: 'no-store',
    });
    payload = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(payload.detail || 'Could not load task roots');
  } catch (error) {
    uiModule.showError?.(error.message || 'Could not load task roots'); return;
  }
  document.getElementById('computer-task-roots-modal')?.remove();
  const modal = document.createElement('div'); modal.id = 'computer-task-roots-modal'; modal.className = 'modal';
  const content = document.createElement('div'); content.className = 'modal-content companion-memory-modal';
  content.setAttribute('role', 'dialog'); content.setAttribute('aria-modal', 'true');
  const header = document.createElement('div'); header.className = 'modal-header';
  const title = document.createElement('h4'); title.textContent = 'Computer Help task roots';
  const close = document.createElement('button'); close.type = 'button'; close.className = 'close-btn'; close.setAttribute('aria-label', 'Close'); close.textContent = '✖';
  close.onclick = () => modal.remove(); header.append(title, close); content.append(header);
  const intro = document.createElement('p'); intro.className = 'companion-memory-help';
  intro.textContent = payload.notice || 'Task roots are dedicated folders prepared for contained Computer Help work.';
  content.append(intro);
  if (!payload.execution_ready) {
    const disabled = document.createElement('p'); disabled.className = 'companion-memory-help';
    disabled.textContent = 'Contained execution is not ready yet. Saving a root will not create files, run commands, or grant filesystem access.';
    content.append(disabled);
  }
  const roots = Array.isArray(payload.task_roots) ? payload.task_roots : [];
  const list = document.createElement('div'); list.className = 'companion-task-root-list';
  if (!roots.length) {
    const empty = document.createElement('p'); empty.className = 'companion-memory-help'; empty.textContent = 'No dedicated task folder is registered.'; list.append(empty);
  }
  roots.forEach((root) => {
    const row = document.createElement('div'); row.className = 'companion-grant-card';
    const label = document.createElement('strong'); label.textContent = root.label || 'Task root';
    const detail = document.createElement('span'); detail.textContent = `Folder: ${root.directory_name || 'selected folder'}`;
    const remove = document.createElement('button'); remove.type = 'button'; remove.textContent = 'Remove';
    remove.onclick = async () => {
      if (!window.confirm(`Remove the task-root record “${root.label || 'Task root'}”? The folder and its files will not be touched.`)) return;
      remove.disabled = true;
      try {
        const response = await fetch(`/api/companion/computer/task-roots/${encodeURIComponent(root.id)}`, {
          method: 'DELETE', credentials: 'same-origin', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({session_id: meta.id, expected_revision: Number(root.revision)}),
        });
        const result = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(result.detail || 'Could not remove task root');
        modal.remove(); openComputerTaskRoots(meta);
      } catch (error) {
        uiModule.showError?.(error.message || 'Could not remove task root'); remove.disabled = false;
      }
    };
    row.append(label, detail, remove); list.append(row);
  });
  content.append(list);
  const form = document.createElement('form'); form.className = 'companion-task-root-form';
  const labelInput = document.createElement('input'); labelInput.required = true; labelInput.maxLength = 100; labelInput.placeholder = 'Label, for example Music downloads'; labelInput.setAttribute('aria-label', 'Task root label');
  const pathInput = document.createElement('input'); pathInput.required = true; pathInput.maxLength = 4096; pathInput.placeholder = 'Existing dedicated folder inside your home'; pathInput.setAttribute('aria-label', 'Task root folder');
  const save = document.createElement('button'); save.type = 'submit'; save.textContent = 'Save task root';
  form.append(labelInput, pathInput, save);
  form.onsubmit = async (event) => {
    event.preventDefault(); save.disabled = true;
    try {
      const response = await fetch('/api/companion/computer/task-roots', {
        method: 'POST', credentials: 'same-origin', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({session_id: meta.id, label: labelInput.value, path: pathInput.value}),
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result.detail || 'Could not save task root');
      uiModule.showToast?.(result.notice || 'Task root saved.', 3000);
      modal.remove(); openComputerTaskRoots(meta);
    } catch (error) {
      uiModule.showError?.(error.message || 'Could not save task root'); save.disabled = false;
    }
  };
  content.append(form); modal.append(content); document.body.append(modal);
}

window.addEventListener('odysseus:tool-toggle', (event) => {
  const detail = event?.detail || {};
  if (detail.name !== 'web_search' || !host.currentSessionId()) return;
  const meta = host.sessions().find(item => item.id === host.currentSessionId());
  if (!meta) return;
  _persistChatCapability(meta, 'web_search', !!detail.enabled).catch((error) => {
    uiModule.showError?.(error.message || 'Could not save Web search for this chat');
    _syncCapabilityControls(meta, meta.chat_capabilities || {});
  });
});

window.addEventListener('odysseus:capability-updated', (event) => {
  const detail = event?.detail || {};
  const meta = host.sessions().find(item => item.id === detail.sessionId);
  if (!meta) return;
  meta.chat_capabilities = detail.capabilities || {};
  if (host.currentSessionId() === meta.id) _syncCapabilityControls(meta, meta.chat_capabilities);
});

async function openCompanionMemory(meta, initialTab = 'context') {
  if (!meta?.id) return;
  let payload;
  try {
    const response = await fetch(`/api/companion/memory/sessions/${encodeURIComponent(meta.id)}`, {credentials: 'same-origin', cache: 'no-store'});
    const body = await response.text();
    try { payload = body ? JSON.parse(body) : {}; }
    catch (_error) {
      throw new Error(response.ok
        ? 'Companion memory returned an unreadable response.'
        : `Could not load Companion memory (server error ${response.status}).`);
    }
    if (!response.ok) throw new Error(payload.detail || 'Could not load Companion memory');
  } catch (error) { uiModule.showToast?.(error.message || 'Could not load Companion memory', 2600); return; }
  document.getElementById('companion-memory-modal')?.remove();
  const esc = uiModule.esc || ((text) => String(text));
  const modal = document.createElement('div'); modal.id = 'companion-memory-modal'; modal.className = 'modal';
  const brief = payload.personal_brief || payload.project_brief || {};
  const deviceProfile = payload.device_profile || null;
  const checkpoint = payload.thread_checkpoint || {};
  const proposalRecord = payload.semantic_proposal || null;
  const proposal = proposalRecord?.proposal || null;
  const continuityProvenance = (record, label) => {
    const status = record.derivation_status || 'legacy_unclassified';
    if (status === 'accepted') return `${label}: owner-approved home state.`;
    if (status === 'heuristic') return `${label}: automatically derived heuristic — review the source conversation before treating it as a decision or fact.`;
    if (status === 'proposed') return `${label}: proposed state — it has not been accepted yet.`;
    if (status === 'rejected') return `${label}: rejected state — do not use it as current direction.`;
    return `${label}: legacy record with unknown derivation — review the source conversation before relying on it.`;
  };
  const memoryTitle = payload.scope_kind === 'personal' ? 'Personal memory'
    : (payload.scope_kind === 'computer' ? 'Computer Help records' : 'Project memory');
  const semanticFields = [
    ['objective', 'Objective'], ['facts', 'Facts'], ['decision_candidates', 'Decision candidates'],
    ['proposals', 'Proposals'], ['failed_approaches', 'Failed approaches'],
    ['open_questions', 'Open questions'], ['next_actions', 'Next actions'], ['artifact_refs', 'Artifact references'],
  ];
  const deviceProfileHtml = deviceProfile
    ? `<section class="companion-device-profile"><h5>Verified device profile</h5><p class="companion-memory-help">Collected ${esc(deviceProfile.collected_at ? new Date(deviceProfile.collected_at).toLocaleString() : 'recently')} from the safe diagnostic broker. These facts are not model inferences or raw logs.</p><ul>${(Array.isArray(deviceProfile.facts) ? deviceProfile.facts : []).slice(0, 24).map(fact => `<li>${esc(fact)}</li>`).join('')}</ul></section>`
    : (payload.scope_kind === 'computer' ? '<section class="companion-device-profile"><h5>Verified device profile</h5><p class="companion-memory-help">Run a safe system inspection to create verified, non-secret device context for Computer Help.</p></section>' : '');
  const proposalHistory = Array.isArray(payload.semantic_proposal_history) ? payload.semantic_proposal_history : [];
  const semanticProvenance = (item, {manual = false} = {}) => {
    const model = String(item?.derivation_model || '').trim();
    const sourceCount = Array.isArray(item?.source_message_ids) ? item.source_message_ids.length : 0;
    const sourceLabel = sourceCount ? `${sourceCount} source message${sourceCount === 1 ? '' : 's'}` : 'bounded source messages';
    const runLabel = manual ? 'Manual derivation' : 'Derivation';
    return `${runLabel}: ${model ? `selected model ${model}` : 'recorded model unavailable'} · no tools · ${sourceLabel}.`;
  };
  const proposalAttempt = payload.semantic_proposal_attempt || null;
  const proposalReadiness = payload.semantic_proposal_readiness || null;
  const proposalReadinessText = proposalReadiness
    ? `${proposalReadiness.eligible ? 'Canary preflight passed' : 'Canary preflight needs attention'}: ${String(proposalReadiness.message || 'No readiness status is available.')}${proposalReadiness.eligible ? ' This check did not contact the provider.' : ''}`
    : '';
  const proposalAttemptText = proposalAttempt && proposalAttempt.outcome !== 'ready'
    ? ({
      cancelled: 'Background proposal paused because a newer turn started. No memory was changed.',
      failed: `Background proposal could not be created (${String(proposalAttempt.code || 'unknown').replaceAll('_', ' ')}). No memory was changed.`,
    }[proposalAttempt.outcome] || 'Background proposal did not complete. No memory was changed.')
    : '';
  const checkpointMounts = Array.isArray(payload.checkpoint_mounts) ? payload.checkpoint_mounts : [];
  const mountedCheckpointIds = new Set(checkpointMounts.map(mount => mount.source_checkpoint_id));
  const checkpointPromotionFields = [
    ['objective', 'Objective'], ['accepted_decisions', 'Decisions'], ['proposals', 'Proposals'],
    ['failures', 'Failed approaches'], ['open_questions', 'Open questions'],
    ['next_actions', 'Next actions'], ['artifact_refs', 'Artifact references'],
  ];
  const checkpointCandidates = (payload.checkpoint_catalog || []).filter(item => (
    item.session_id !== meta.id && !mountedCheckpointIds.has(item.id)
  ));
  const mountExpiryText = (value) => {
    const date = value ? new Date(value) : null;
    return date && !Number.isNaN(date.getTime())
      ? `expires ${date.toLocaleDateString()}` : 'no expiry metadata';
  };
  const checkpointSourceLabel = (item) => {
    const home = item.scope_kind === 'project' ? 'Project' : 'Personal';
    const status = item.derivation_status || 'legacy_unclassified';
    const count = Number(item.source_message_count || 0);
    const source = item.session_name || item.session_id || 'Companion chat';
    const objective = item.objective || 'Untitled checkpoint';
    return `${source} · ${home} · ${status} · ${count} source message${count === 1 ? '' : 's'} — ${objective}`;
  };
  const checkpointAttachControls = checkpointCandidates.length
    ? `<label>Checkpoint <select class="companion-checkpoint-source">${checkpointCandidates.map(item => `<option value="${esc(item.id)}">${esc(checkpointSourceLabel(item))}</option>`).join('')}</select></label><label>Keep mounted for <select class="companion-checkpoint-expiry"><option value="1">1 day</option><option value="7">7 days</option><option value="14" selected>14 days</option><option value="30">30 days</option></select></label><label class="companion-sensitive-checkpoint"><input type="checkbox" class="companion-checkpoint-sensitive"> Sensitive context — I understand this will be included in the destination chat until it expires or is detached.</label><button type="button" class="companion-attach-checkpoint">Attach checkpoint</button>`
    : '<p class="companion-memory-help">No other active Companion checkpoint is available to attach.</p>';
  const checkpointSynthesisControls = checkpointCandidates.length >= 2
    ? `<details class="companion-checkpoint-synthesis"><summary>Create a synthesis chat from two checkpoints</summary><p class="companion-memory-help">Review both sources below. This creates a fresh sibling chat with two labeled, immutable mounts. It does not merge transcripts or call a model until you send your synthesis request; disagreements remain source-attributed.</p><label>First checkpoint <select class="companion-synthesis-source-a">${checkpointCandidates.map(item => `<option value="${esc(item.id)}">${esc(checkpointSourceLabel(item))}</option>`).join('')}</select></label><label>Second checkpoint <select class="companion-synthesis-source-b">${checkpointCandidates.map((item, index) => `<option value="${esc(item.id)}"${index === 1 ? ' selected' : ''}>${esc(checkpointSourceLabel(item))}</option>`).join('')}</select></label><button type="button" class="companion-preview-checkpoint-synthesis">Review selected checkpoints</button><div class="companion-synthesis-preview" aria-live="polite"></div><button type="button" class="companion-create-checkpoint-synthesis">Create synthesis chat</button></details>`
    : '';
  const checkpointMountHtml = (payload.scope_kind === 'personal' || payload.scope_kind === 'project')
    ? `<section class="companion-checkpoint-mounts"><h5>Read-only checkpoint mounts</h5><p class="companion-memory-help">Attach a compact checkpoint from another one of your Companion chats. It is labeled context, never a transcript merge or shared memory write. Mounts expire automatically; sensitive mounts require acknowledgement.</p>${checkpointMounts.length ? checkpointMounts.map(mount => { const item = mount.checkpoint || {}; return `<details class="companion-checkpoint-mount" data-mount-id="${esc(mount.id)}" data-mount-revision="${esc(mount.revision)}"><summary><strong>${esc(mount.objective || 'Untitled checkpoint')}</strong> · ${esc(mount.derivation_status || 'legacy_unclassified')} · ${esc(mount.source_message_count || 0)} source messages</summary><p class="companion-memory-help">${esc(mount.sensitivity || 'standard')} context · ${esc(mountExpiryText(mount.expires_at))}. Select entries only if you want to promote them into this home’s accepted memory.</p>${checkpointPromotionFields.map(([field, label]) => { const values = field === 'objective' ? (item.objective ? [item.objective] : []) : (item[field] || []); return values.length ? `<fieldset><legend>${esc(label)}</legend>${values.map((value, index) => `<label><input type="checkbox" data-mount-field="${esc(field)}" data-mount-index="${index}"> ${esc(value)}</label>`).join('')}</fieldset>` : ''; }).join('')}<button type="button" class="companion-promote-checkpoint">Promote selected entries</button><button type="button" class="companion-detach-checkpoint">Detach</button></details>`; }).join('') : '<p class="companion-memory-help">No checkpoint is mounted.</p>'}${checkpointAttachControls}${checkpointSynthesisControls}</section>`
    : '';
  const proposalHistoryHtml = proposalHistory.length > 1 ? `<details class="companion-semantic-history"><summary>Proposal history (${proposalHistory.length})</summary>${proposalHistory.map((record) => {
    const item = record.proposal || {};
    const itemCount = semanticFields.reduce((count, [field]) => count + (field === 'objective' ? (item.objective ? 1 : 0) : (item[field] || []).length), 0);
    return `<details><summary>Revision ${esc(record.revision)} · ${esc(record.status)} · ${esc(itemCount)} entries</summary><p>${esc(item.objective || 'No objective proposed.')}</p><p class="companion-memory-help">${esc(semanticProvenance(item))} Source through message ${esc(item.source_through_message_id || 'unknown')}.</p></details>`;
  }).join('')}</details>` : '';
  const proposalHtml = proposal ? `<section class="companion-semantic-proposal" data-proposal-id="${esc(proposalRecord.id)}" data-proposal-revision="${esc(proposalRecord.revision)}"><h5>Semantic proposal</h5><p class="companion-memory-help">${esc(semanticProvenance(proposal, {manual: true}))} The model cannot use tools or write memory. Check only entries you want to promote into accepted home memory.</p>${semanticFields.map(([field, label]) => { const values = field === 'objective' ? (proposal.objective ? [proposal.objective] : []) : (proposal[field] || []); return values.length ? `<fieldset><legend>${esc(label)}</legend>${values.map((value, index) => `<label><input type="checkbox" data-proposal-field="${esc(field)}" data-proposal-index="${index}"> ${esc(value)}</label>`).join('')}</fieldset>` : ''; }).join('')}<button type="button" class="companion-promote-proposal">Promote selected entries</button></section>` : '<section class="companion-semantic-proposal"><h5>Semantic proposal</h5><p class="companion-memory-help">Create a source-linked proposal, then review and promote individual entries. It will not change memory automatically.</p></section>';
  const proposalAction = !proposal && (payload.scope_kind === 'personal' || payload.scope_kind === 'project')
    ? `<button type="button" class="companion-create-semantic-proposal"${proposalReadiness && !proposalReadiness.eligible ? ' disabled' : ''}>Create semantic proposal</button>` : '';
  const legacyInventoryHtml = payload.scope_kind === 'personal'
    ? '<details class="companion-legacy-memory-inventory"><summary>Legacy memory migration preflight</summary><p class="companion-memory-help">Run an aggregate-only inventory before any export, backup, or AgentMemory migration. It never displays memory text or changes records.</p><div class="companion-legacy-review-history" aria-live="polite"></div><button type="button" class="companion-run-legacy-inventory">Run read-only inventory</button><button type="button" class="companion-create-legacy-backup" disabled>Create owner-private backup</button><button type="button" class="companion-run-legacy-dry-run" disabled>Preview scoped migration</button><div class="companion-legacy-inventory-result" aria-live="polite"></div></details>'
    : '';
  const relatedProjectCatalog = Array.isArray(payload.related_project_catalog) ? payload.related_project_catalog : [];
  const relatedProjectChoices = relatedProjectCatalog.length
    ? `${relatedProjectCatalog.map(project => `<label><input type="checkbox" data-related-project-id="${esc(project.id)}"${project.related ? ' checked' : ''}> ${esc(project.name)}</label>`).join('')}<button type="button" class="companion-save-related-projects">Save related projects</button>`
    : '<p class="companion-memory-help">No other owner-owned project is available.</p>';
  const relatedProjectHtml = payload.scope_kind === 'project'
    ? `<details class="companion-related-projects"><summary>Related project access</summary><p class="companion-memory-help">Choose at most three projects that this project may consult. They remain private unless you explicitly mention <code>@Project Name</code> in a message; only that project’s accepted brief may then be included.</p>${relatedProjectChoices}</details>`
    : '';
  const lastCompiled = payload.last_compiled_context && typeof payload.last_compiled_context === 'object'
    ? payload.last_compiled_context : null;
  const compiledTimestamp = lastCompiled?.recorded_at ? new Date(lastCompiled.recorded_at) : null;
  const compiledWhen = compiledTimestamp && !Number.isNaN(compiledTimestamp.getTime())
    ? compiledTimestamp.toLocaleString() : 'an earlier scoped reply';
  const compiledArtifactPaths = Array.isArray(lastCompiled?.working_artifact_paths)
    ? lastCompiled.working_artifact_paths : [];
  const compiledSelectedPaths = Array.isArray(lastCompiled?.selected_working_artifact_paths)
    ? lastCompiled.selected_working_artifact_paths : [];
  const compiledHitKinds = lastCompiled?.episodic_hit_kinds && typeof lastCompiled.episodic_hit_kinds === 'object'
    ? Object.entries(lastCompiled.episodic_hit_kinds)
      .filter(([kind, count]) => typeof kind === 'string' && Number.isFinite(Number(count)) && Number(count) > 0)
      .slice(0, 8)
    : [];
  const compiledHitsLabel = compiledHitKinds.length
    ? ` (${compiledHitKinds.map(([kind, count]) => `${esc(kind)}: ${esc(Number(count))}`).join(', ')})`
    : '';
  const lastCompiledHtml = lastCompiled
    ? `<details class="companion-last-compiled-context"><summary>Last compiled context</summary><p class="companion-memory-help">Recorded ${esc(compiledWhen)} for this chat. This is an audit summary, not a transcript or recalled memory text.</p><ul><li>Thread checkpoint: ${lastCompiled.thread_checkpoint ? 'included' : 'not included'}</li><li>Home brief: ${(lastCompiled.project_brief || lastCompiled.personal_brief) ? 'included' : 'not included'}</li><li>Verified device profile: ${lastCompiled.device_profile ? 'included' : 'not included'}</li><li>Related project briefs: ${esc(lastCompiled.related_project_count || 0)}</li><li>Read-only checkpoint mounts: ${esc(lastCompiled.mounted_checkpoint_count || 0)}</li><li>Scope-verified episodic hits: ${esc(lastCompiled.episodic_hit_count || 0)}${compiledHitsLabel}</li><li>One-request project grants: ${esc(lastCompiled.context_grant_count || 0)}</li><li>Recent transcript messages: ${esc(lastCompiled.transcript_tail_count || 0)}</li></ul>${compiledArtifactPaths.length ? `<p><strong>Working artifacts:</strong> ${compiledArtifactPaths.map(path => `<code>${esc(path)}</code>`).join(', ')}</p>` : ''}${compiledSelectedPaths.length ? `<p><strong>Artifact content included:</strong> ${compiledSelectedPaths.map(path => `<code>${esc(path)}</code>`).join(', ')}</p>` : ''}</details>`
    : '<details class="companion-last-compiled-context"><summary>Last compiled context</summary><p class="companion-memory-help">No completed scoped reply has recorded a context manifest yet.</p></details>';
  modal.innerHTML = `<div class="modal-content companion-memory-modal" role="dialog" aria-modal="true" aria-labelledby="companion-memory-title">
    <div class="modal-header"><h4 id="companion-memory-title">${esc(memoryTitle)}</h4><button type="button" class="close-btn" aria-label="Close">✖</button></div>
    <div class="companion-memory-tabs"><button type="button" data-tab="context">Context</button>${payload.scope_kind === 'personal' ? '<button type="button" data-tab="projects">Project access</button>' : ''}</div>
    <section data-panel="context" class="companion-memory-panel"><h5>Thread checkpoint</h5><p>${esc(checkpoint.objective || 'No compact checkpoint yet. It is derived as the conversation grows.')}</p><p class="companion-memory-help">${esc(continuityProvenance(checkpoint, 'Thread checkpoint'))}</p><h5>Home brief</h5><p>${esc(brief.summary || 'No home brief saved yet.')}</p><p class="companion-memory-help">${esc(continuityProvenance(brief, 'Home brief'))}</p>${deviceProfileHtml}${lastCompiledHtml}${checkpointMountHtml}${relatedProjectHtml}${proposalHtml}${proposalReadinessText ? `<p class="companion-memory-help">${esc(proposalReadinessText)}</p>` : ''}${proposalAction}${proposalAttemptText ? `<p class="companion-memory-help">${esc(proposalAttemptText)}</p>` : ''}${proposalHistoryHtml}${legacyInventoryHtml}<p class="companion-memory-help">Working drafts and incident records are ordinary Documents. They are opened and edited in the shared Documents editor, not in a second artifact interface.</p><button type="button" class="companion-open-documents">Open Documents</button>${['personal', 'project'].includes(payload.scope_kind) ? `<button type="button" class="companion-edit-brief">Edit ${payload.scope_kind === 'personal' ? 'Personal' : 'Project'} brief</button>` : ''}</section>
    ${payload.scope_kind === 'personal' ? `<section data-panel="projects" class="companion-memory-panel hidden"><p class="companion-memory-help">Project material is never searched automatically. Request only the brief or named artifacts you need.</p><div class="companion-grant-list">${(payload.pending_grants || []).map(grant => `<div class="companion-grant-card" data-grant="${esc(grant.id)}"><strong>Allow ${esc((payload.project_catalog || []).find(p => p.id === grant.project_id)?.name || 'Project')} once?</strong><span>${esc(grant.purpose)}</span><button type="button" data-decision="allow">Allow once</button><button type="button" data-decision="deny">Deny</button></div>`).join('')}${(payload.grants || []).map(grant => `<p>Allowed once: ${esc((payload.project_catalog || []).find(p => p.id === grant.project_id)?.name || 'Project')}</p>`).join('') || '<p>No temporary project access is active.</p>'}</div><label>Project <select class="companion-grant-project">${(payload.project_catalog || []).map(p => `<option value="${esc(p.id)}">${esc(p.name)}</option>`).join('')}</select></label><label>Why is it needed?<input class="companion-grant-purpose" maxlength="1000" placeholder="Consult the project brief for this answer"></label><button type="button" class="companion-request-grant">Request access</button></section>` : ''}
  </div>`;
  const close = () => modal.remove(); modal.querySelector('.close-btn').onclick = close;
  let legacyBackupId = '';
  const show = (tab) => modal.querySelectorAll('[data-panel]').forEach(panel => panel.classList.toggle('hidden', panel.dataset.panel !== tab));
  modal.querySelectorAll('[data-tab]').forEach(button => button.onclick = () => show(button.dataset.tab)); show(initialTab);
  modal.querySelector('.companion-open-documents')?.addEventListener('click', () => {
    const documentApi = window.documentModule;
    if (!documentApi?.openLibrary) {
      uiModule.showError?.('The Documents editor is not ready yet. Try again in a moment.');
      return;
    }
    close();
    documentApi.openLibrary({tab: 'documents'});
  });
  const reviewHistory = modal.querySelector('.companion-legacy-review-history');
  if (reviewHistory) {
    fetch('/api/companion/memory/legacy-migration-reviews', {credentials: 'same-origin', cache: 'no-store'})
      .then(response => response.ok ? response.json() : null)
      .then(result => {
        const review = Array.isArray(result?.reviews) ? result.reviews[0] : null;
        if (!review) return;
        const summary = document.createElement('p'); summary.className = 'companion-memory-help';
        const staged = Number(review.assignment_staged_count || 0);
        summary.textContent = staged
          ? `Saved review: ${staged} exact-home assignments staged · ${review.assignment_unresolved_count || 0} remain unresolved · no migration started.`
          : `Saved review: ${review.candidate_count || 0} candidates · ${review.status || 'review ready'} · no migration started.`;
        reviewHistory.replaceChildren(summary);
        if (review.status === 'review_ready') {
          const prompt = document.createElement('p'); prompt.className = 'companion-memory-help';
          prompt.textContent = 'You may stage only records whose original owner home still matches. This creates no provider records and leaves unresolved entries untouched.';
          const button = document.createElement('button'); button.type = 'button';
          button.textContent = 'Stage exact-home assignments';
          button.addEventListener('click', async () => {
            button.disabled = true;
            try {
              const response = await fetch(`/api/companion/memory/legacy-migration-reviews/${encodeURIComponent(review.review_id)}/stage-exact-assignments`, {
                method: 'POST', credentials: 'same-origin', cache: 'no-store',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({expected_revision: Number(review.revision), accept_exact_provenance: true}),
              });
              const updated = await response.json().catch(() => ({}));
              if (!response.ok) throw new Error(updated.detail || 'Could not stage exact-home assignments');
              summary.textContent = `Saved review: ${updated.assignment_staged_count || 0} exact-home assignments staged · ${updated.assignment_unresolved_count || 0} remain unresolved · no migration started.`;
              prompt.remove(); button.remove();
              uiModule.showToast?.('Exact-home assignments were staged. No legacy memory was migrated.', 4200);
            } catch (error) {
              uiModule.showError?.(error.message || 'Could not stage exact-home assignments');
              button.disabled = false;
            }
          });
          reviewHistory.append(prompt, button);
        } else if (review.status === 'assignments_staged') {
          const prompt = document.createElement('p'); prompt.className = 'companion-memory-help';
          prompt.textContent = 'Apply adds the staged entries only to encrypted local scoped recall. Your native legacy memory remains unchanged; you can roll this local addition back while its records remain unchanged.';
          const button = document.createElement('button'); button.type = 'button';
          button.textContent = `Migrate ${review.assignment_staged_count || 0} exact-home entries`;
          button.addEventListener('click', async () => {
            if (!window.confirm('Add the staged entries to local scoped recall? Native legacy memory will not be changed.')) return;
            button.disabled = true;
            try {
              const response = await fetch(`/api/companion/memory/legacy-migration-reviews/${encodeURIComponent(review.review_id)}/apply`, {
                method: 'POST', credentials: 'same-origin', cache: 'no-store',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({expected_revision: Number(review.revision), confirm_additive_local_migration: true}),
              });
              const updated = await response.json().catch(() => ({}));
              if (!response.ok) throw new Error(updated.detail || 'Could not apply the local migration');
              summary.textContent = `Saved review: ${updated.migration_applied_count || 0} entries added to local scoped recall. Native legacy memory remains unchanged.`;
              prompt.remove(); button.remove();
              uiModule.showToast?.('Local scoped migration applied. Native legacy memory was not changed.', 4200);
            } catch (error) {
              uiModule.showError?.(error.message || 'Could not apply the local migration');
              button.disabled = false;
            }
          });
          reviewHistory.append(prompt, button);
        } else if (review.status === 'migration_applied') {
          const button = document.createElement('button'); button.type = 'button';
          button.textContent = 'Roll back local migration';
          button.addEventListener('click', async () => {
            if (!window.confirm('Remove only the local scoped records created by this migration? Native legacy memory remains unchanged.')) return;
            button.disabled = true;
            try {
              const response = await fetch(`/api/companion/memory/legacy-migration-reviews/${encodeURIComponent(review.review_id)}/rollback`, {
                method: 'POST', credentials: 'same-origin', cache: 'no-store',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({expected_revision: Number(review.revision)}),
              });
              const updated = await response.json().catch(() => ({}));
              if (!response.ok) throw new Error(updated.detail || 'Could not roll back the local migration');
              summary.textContent = 'Saved review: local scoped records were rolled back. Native legacy memory remains unchanged.';
              button.remove();
              uiModule.showToast?.('Local scoped migration rolled back. Native legacy memory was not changed.', 4200);
            } catch (error) {
              uiModule.showError?.(error.message || 'Could not roll back the local migration');
              button.disabled = false;
            }
          });
          reviewHistory.append(button);
        }
        if (
          Number(review.owner_reviewable_unresolved_count || 0) > 0
          && ['review_ready', 'assignments_staged'].includes(review.status)
        ) {
          const reviewButton = document.createElement('button'); reviewButton.type = 'button';
          reviewButton.textContent = `Review unresolved entry (${review.owner_reviewable_unresolved_count})`;
          reviewButton.addEventListener('click', async () => {
            if (!window.confirm('Reveal one of your unresolved legacy memory entries so you can choose its home?')) return;
            reviewButton.disabled = true;
            try {
              const response = await fetch(`/api/companion/memory/legacy-migration-reviews/${encodeURIComponent(review.review_id)}/reveal-next-unresolved`, {
                method: 'POST', credentials: 'same-origin', cache: 'no-store',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({expected_revision: Number(review.revision), confirm_reveal_legacy_text: true}),
              });
              const revealed = await response.json().catch(() => ({}));
              if (!response.ok) throw new Error(revealed.detail || 'Could not reveal an unresolved legacy entry');
              const card = document.createElement('section'); card.className = 'companion-legacy-entry-review';
              const title = document.createElement('p');
              title.textContent = `Unresolved legacy entry · ${revealed.category || 'fact'}${revealed.text_truncated ? ' · preview truncated' : ''}`;
              const text = document.createElement('pre'); text.textContent = String(revealed.text || '');
              const select = document.createElement('select');
              const personal = document.createElement('option'); personal.value = 'personal'; personal.textContent = 'Personal Advisor';
              select.append(personal);
              (Array.isArray(payload.project_catalog) ? payload.project_catalog : []).forEach(project => {
                if (!project?.id || !project?.name) return;
                const option = document.createElement('option'); option.value = `project:${project.id}`; option.textContent = `Project · ${project.name}`;
                select.append(option);
              });
              const assign = document.createElement('button'); assign.type = 'button'; assign.textContent = 'Stage selected home';
              assign.addEventListener('click', async () => {
                const selected = String(select.value || '');
                const [scopeKind, projectId] = selected.split(':', 2);
                assign.disabled = true;
                try {
                  const assignmentResponse = await fetch(`/api/companion/memory/legacy-migration-reviews/${encodeURIComponent(review.review_id)}/stage-owner-assignment`, {
                    method: 'POST', credentials: 'same-origin', cache: 'no-store',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                      expected_revision: Number(revealed.revision), candidate_token: revealed.candidate_token,
                      destination_scope_kind: scopeKind,
                      destination_project_id: scopeKind === 'project' ? projectId : null,
                    }),
                  });
                  const updated = await assignmentResponse.json().catch(() => ({}));
                  if (!assignmentResponse.ok) throw new Error(updated.detail || 'Could not stage this legacy assignment');
                  card.remove(); reviewButton.remove();
                  summary.textContent = `Saved review: ${updated.assignment_staged_count || 0} assignments staged · ${updated.owner_reviewable_unresolved_count || 0} entries still need a chosen home. No migration started.`;
                  uiModule.showToast?.('Owner-selected home staged. Reopen Context to continue the review or apply migration.', 4800);
                } catch (error) {
                  uiModule.showError?.(error.message || 'Could not stage this legacy assignment');
                  assign.disabled = false;
                }
              });
              card.append(title, text, select, assign);
              reviewHistory.append(card);
            } catch (error) {
              uiModule.showError?.(error.message || 'Could not reveal an unresolved legacy entry');
              reviewButton.disabled = false;
            }
          });
          reviewHistory.append(reviewButton);
        }
      })
      .catch(() => {});
  }
  modal.querySelector('.companion-run-legacy-inventory')?.addEventListener('click', async (event) => {
    const resultNode = modal.querySelector('.companion-legacy-inventory-result');
    event.currentTarget.disabled = true;
    try {
      const response = await fetch('/api/companion/memory/legacy-inventory', {credentials: 'same-origin', cache: 'no-store'});
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result.detail || 'Could not inspect legacy memory');
      if (!resultNode) return;
      resultNode.replaceChildren();
      const summary = document.createElement('p');
      if (!result.readable) {
        summary.textContent = result.next_step || 'Legacy memory could not be read safely. No data was changed.';
      } else {
        const native = result.native_memory || {};
        summary.textContent = `${native.owner_entry_count || 0} of your legacy entries · ${native.entries_with_session_provenance || 0} with chat provenance · ${native.ownerless_entry_count || 0} ownerless compatibility entries. No data was changed.`;
      }
      resultNode.append(summary);
      if (result.readable && result.native_memory?.foreign_owner_entries_present) {
        const warning = document.createElement('p'); warning.className = 'companion-memory-help';
        warning.textContent = 'Other owner-scoped legacy entries exist. They were not counted, read, or exposed.';
        resultNode.append(warning);
      }
      const next = document.createElement('p'); next.className = 'companion-memory-help';
      next.textContent = result.next_step || '';
      resultNode.append(next);
      const backupButton = modal.querySelector('.companion-create-legacy-backup');
      if (backupButton) backupButton.disabled = !result.readable;
    } catch (error) {
      uiModule.showError?.(error.message || 'Could not inspect legacy memory');
      event.currentTarget.disabled = false;
    }
  });
  modal.querySelector('.companion-create-legacy-backup')?.addEventListener('click', async (event) => {
    const resultNode = modal.querySelector('.companion-legacy-inventory-result');
    event.currentTarget.disabled = true;
    try {
      const response = await fetch('/api/companion/memory/legacy-backup', {method: 'POST', credentials: 'same-origin', cache: 'no-store'});
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result.detail || 'Could not create the owner-private backup');
      if (resultNode) {
        const summary = document.createElement('p');
        summary.textContent = `Owner-private backup created: ${result.entry_count || 0} entries · integrity digest ${String(result.sha256 || '').slice(0, 12)}…`;
        resultNode.append(summary);
        const next = document.createElement('p'); next.className = 'companion-memory-help';
        next.textContent = result.next_step || '';
        resultNode.append(next);
      }
      legacyBackupId = String(result.backup_id || '');
      const dryRunButton = modal.querySelector('.companion-run-legacy-dry-run');
      if (dryRunButton) dryRunButton.disabled = !legacyBackupId;
      uiModule.showToast?.('Owner-private backup created. No migration was started.', 3600);
    } catch (error) {
      uiModule.showError?.(error.message || 'Could not create the owner-private backup');
      event.currentTarget.disabled = false;
    }
  });
  modal.querySelector('.companion-run-legacy-dry-run')?.addEventListener('click', async (event) => {
    const resultNode = modal.querySelector('.companion-legacy-inventory-result');
    if (!legacyBackupId) {
      uiModule.showError?.('Create an owner-private backup before previewing migration.');
      return;
    }
    event.currentTarget.disabled = true;
    try {
      const response = await fetch('/api/companion/memory/legacy-dry-run', {
        method: 'POST', credentials: 'same-origin', cache: 'no-store',
        headers: {'Content-Type': 'application/json'}, body: JSON.stringify({backup_id: legacyBackupId}),
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result.detail || 'Could not preview scoped migration');
      if (resultNode) {
        const summary = document.createElement('p');
        const scopes = result.scope_candidates || {};
        summary.textContent = `${result.eligible_scope_candidates || 0} scope candidates · ${result.needs_owner_assignment || 0} need owner assignment · ${result.duplicate_candidates || 0} possible duplicates. Personal: ${scopes.personal || 0}; projects: ${scopes.project || 0}. No records were changed.`;
        resultNode.append(summary);
        const next = document.createElement('p'); next.className = 'companion-memory-help';
        next.textContent = result.next_step || '';
        resultNode.append(next);
        if (result.review_id) {
          const review = document.createElement('p'); review.className = 'companion-memory-help';
          review.textContent = 'A private migration review record is ready. It contains no copied memory text and no migration has started.';
          resultNode.append(review);
        }
      }
      uiModule.showToast?.('Migration preview completed. No records were changed.', 3600);
    } catch (error) {
      uiModule.showError?.(error.message || 'Could not preview scoped migration');
      event.currentTarget.disabled = false;
    }
  });
  modal.querySelector('.companion-save-related-projects')?.addEventListener('click', async (event) => {
    const selected = Array.from(modal.querySelectorAll('input[data-related-project-id]:checked'))
      .map(input => input.dataset.relatedProjectId)
      .filter(Boolean);
    if (selected.length > 3) {
      uiModule.showError?.('Choose at most three directly related projects.');
      return;
    }
    event.currentTarget.disabled = true;
    try {
      const response = await fetch(`/api/companion/memory/projects/${encodeURIComponent(meta.project_id)}/relations`, {method: 'PUT', credentials: 'same-origin', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({related_project_ids: selected})});
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result.detail || 'Could not save related projects');
      uiModule.showToast?.('Related-project allowlist saved. Use @Project Name when you want its brief in one message.', 3400);
      close(); openCompanionMemory(meta, 'context');
    } catch (error) {
      uiModule.showError?.(error.message || 'Could not save related projects');
      event.currentTarget.disabled = false;
    }
  });
  modal.querySelector('.companion-attach-checkpoint')?.addEventListener('click', async (event) => {
    const sourceCheckpointId = modal.querySelector('.companion-checkpoint-source')?.value;
    const expiresInDays = Number(modal.querySelector('.companion-checkpoint-expiry')?.value || 14);
    const sensitive = Boolean(modal.querySelector('.companion-checkpoint-sensitive')?.checked);
    if (!sourceCheckpointId) return;
    event.currentTarget.disabled = true;
    try {
      const response = await fetch(`/api/companion/memory/sessions/${encodeURIComponent(meta.id)}/checkpoint-mounts`, {method: 'POST', credentials: 'same-origin', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({source_checkpoint_id: sourceCheckpointId, expires_in_days: expiresInDays, sensitivity: sensitive ? 'sensitive' : 'standard', acknowledge_sensitive: sensitive})});
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result.detail || 'Could not attach checkpoint');
      uiModule.showToast?.('Checkpoint mounted as read-only context.', 2600);
      close(); openCompanionMemory(meta, 'context');
    } catch (error) {
      uiModule.showError?.(error.message || 'Could not attach checkpoint');
      event.currentTarget.disabled = false;
    }
  });
  modal.querySelector('.companion-create-checkpoint-synthesis')?.addEventListener('click', async (event) => {
    const first = modal.querySelector('.companion-synthesis-source-a')?.value;
    const second = modal.querySelector('.companion-synthesis-source-b')?.value;
    if (!first || !second || first === second) {
      uiModule.showError?.('Choose two different checkpoints for the synthesis chat.');
      return;
    }
    event.currentTarget.disabled = true;
    try {
      const response = await fetch('/api/companion/memory/checkpoint-synthesis', {method: 'POST', credentials: 'same-origin', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({destination_session_id: meta.id, source_checkpoint_ids: [first, second]})});
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result.detail || 'Could not create checkpoint synthesis chat');
      close();
      await host.loadSessions();
      await host.selectSession(result.id);
      uiModule.showToast?.('Synthesis chat created with two read-only checkpoints. Send the question you want reconciled.', 3600);
    } catch (error) {
      uiModule.showError?.(error.message || 'Could not create checkpoint synthesis chat');
      event.currentTarget.disabled = false;
    }
  });
  modal.querySelector('.companion-preview-checkpoint-synthesis')?.addEventListener('click', async (event) => {
    const first = modal.querySelector('.companion-synthesis-source-a')?.value;
    const second = modal.querySelector('.companion-synthesis-source-b')?.value;
    const preview = modal.querySelector('.companion-synthesis-preview');
    if (!first || !second || first === second) {
      uiModule.showError?.('Choose two different checkpoints to review.');
      return;
    }
    event.currentTarget.disabled = true;
    try {
      const response = await fetch('/api/companion/memory/checkpoint-synthesis/preview', {method: 'POST', credentials: 'same-origin', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({destination_session_id: meta.id, source_checkpoint_ids: [first, second]})});
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result.detail || 'Could not review checkpoint sources');
      if (!preview) return;
      preview.replaceChildren();
      const policy = document.createElement('p');
      policy.className = 'companion-memory-help';
      policy.textContent = result.policy || 'These are compact references, not transcripts.';
      preview.append(policy);
      const fields = [
        ['objective', 'Objective'], ['accepted_decisions', 'Decisions'], ['proposals', 'Proposals'],
        ['failures', 'Failed approaches'], ['open_questions', 'Open questions'],
        ['next_actions', 'Next actions'], ['artifact_refs', 'Artifact references'],
      ];
      (Array.isArray(result.sources) ? result.sources : []).slice(0, 2).forEach((source, index) => {
        const card = document.createElement('section');
        card.className = 'companion-synthesis-source-review';
        const heading = document.createElement('strong');
        const scope = source.scope_kind === 'project' ? 'Project' : 'Personal';
        heading.textContent = `Source ${index + 1}: ${source.session_name || 'Companion chat'} · ${scope} · ${source.derivation_status || 'legacy_unclassified'} · ${Number(source.source_message_count || 0)} source messages`;
        card.append(heading);
        fields.forEach(([field, label]) => {
          const values = field === 'objective' ? (source.objective ? [source.objective] : []) : (Array.isArray(source[field]) ? source[field] : []);
          if (!values.length) return;
          const line = document.createElement('p');
          line.textContent = `${label}: ${values.join(' · ')}`;
          card.append(line);
        });
        preview.append(card);
      });
      const comparison = result.comparison;
      if (comparison && typeof comparison === 'object') {
        const card = document.createElement('section');
        card.className = 'companion-synthesis-source-review companion-synthesis-comparison';
        const heading = document.createElement('strong');
        heading.textContent = 'Agreement and differences';
        card.append(heading);
        const policyLine = document.createElement('p');
        policyLine.className = 'companion-memory-help';
        policyLine.textContent = comparison.policy || 'Differences remain attributed to their source.';
        card.append(policyLine);
        const objectives = comparison.objectives || {};
        const objectiveLine = document.createElement('p');
        objectiveLine.textContent = objectives.shared
          ? `Shared objective: ${objectives.source_one || 'Untitled checkpoint'}`
          : `Objectives differ — Source 1: ${objectives.source_one || 'none'} · Source 2: ${objectives.source_two || 'none'}`;
        card.append(objectiveLine);
        const labels = {
          accepted_decisions: 'Decisions', proposals: 'Proposals', failures: 'Failed approaches',
          open_questions: 'Open questions', next_actions: 'Next actions', artifact_refs: 'Artifact references',
        };
        Object.entries(comparison.fields || {}).forEach(([field, value]) => {
          const shared = Array.isArray(value?.shared) ? value.shared : [];
          const sourceOneOnly = Array.isArray(value?.source_one_only) ? value.source_one_only : [];
          const sourceTwoOnly = Array.isArray(value?.source_two_only) ? value.source_two_only : [];
          if (!shared.length && !sourceOneOnly.length && !sourceTwoOnly.length) return;
          const line = document.createElement('p');
          const parts = [];
          if (shared.length) parts.push(`Shared: ${shared.join(' · ')}`);
          if (sourceOneOnly.length) parts.push(`Source 1 only: ${sourceOneOnly.join(' · ')}`);
          if (sourceTwoOnly.length) parts.push(`Source 2 only: ${sourceTwoOnly.join(' · ')}`);
          line.textContent = `${labels[field] || field}: ${parts.join(' | ')}`;
          card.append(line);
        });
        preview.append(card);
      }
    } catch (error) {
      uiModule.showError?.(error.message || 'Could not review checkpoint sources');
    } finally {
      event.currentTarget.disabled = false;
    }
  });
  modal.querySelectorAll('.companion-detach-checkpoint').forEach(button => button.addEventListener('click', async (event) => {
    event.currentTarget.disabled = true;
    const card = event.currentTarget.closest('[data-mount-id]');
    try {
      const response = await fetch(`/api/companion/memory/sessions/${encodeURIComponent(meta.id)}/checkpoint-mounts/${encodeURIComponent(card.dataset.mountId)}`, {method: 'DELETE', credentials: 'same-origin', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({expected_revision: Number(card.dataset.mountRevision)})});
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result.detail || 'Could not detach checkpoint');
      uiModule.showToast?.('Checkpoint detached.', 2200);
      close(); openCompanionMemory(meta, 'context');
    } catch (error) {
      uiModule.showError?.(error.message || 'Could not detach checkpoint');
      event.currentTarget.disabled = false;
    }
  }));
  modal.querySelectorAll('.companion-promote-checkpoint').forEach(button => button.addEventListener('click', async (event) => {
    const card = event.currentTarget.closest('[data-mount-id]');
    const selections = {};
    card.querySelectorAll('input[data-mount-field]:checked').forEach(input => {
      const field = input.dataset.mountField;
      (selections[field] ||= []).push(Number(input.dataset.mountIndex));
    });
    if (!Object.keys(selections).length) {
      uiModule.showError?.('Select at least one checkpoint entry to promote.');
      return;
    }
    event.currentTarget.disabled = true;
    try {
      const response = await fetch(`/api/companion/memory/sessions/${encodeURIComponent(meta.id)}/checkpoint-mounts/${encodeURIComponent(card.dataset.mountId)}/promote`, {method: 'POST', credentials: 'same-origin', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({expected_revision: Number(card.dataset.mountRevision), selections})});
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result.detail || 'Could not promote selected checkpoint entries');
      uiModule.showToast?.('Selected checkpoint entries promoted into accepted home memory.', 3000);
      close(); openCompanionMemory(meta, 'context');
    } catch (error) {
      uiModule.showError?.(error.message || 'Could not promote selected checkpoint entries');
      event.currentTarget.disabled = false;
    }
  }));
  modal.querySelector('.companion-create-semantic-proposal')?.addEventListener('click', async (event) => {
    event.currentTarget.disabled = true;
    try {
      const response = await fetch(`/api/companion/memory/sessions/${encodeURIComponent(meta.id)}/semantic-proposals`, {method: 'POST', credentials: 'same-origin'});
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result.detail || 'Could not create a semantic proposal');
      const model = String(result?.derivation?.model || result?.proposal?.derivation_model || '').trim();
      const sourceCount = Number(result?.derivation?.source_message_count || 0);
      uiModule.showToast?.(`Semantic proposal ready for review${model ? ` · ${model}` : ''}${sourceCount ? ` · ${sourceCount} source message${sourceCount === 1 ? '' : 's'}` : ''}.`, 3600);
      close(); openCompanionMemory(meta, 'context');
    } catch (error) {
      uiModule.showError?.(error.message || 'Could not create a semantic proposal');
      event.currentTarget.disabled = false;
    }
  });
  modal.querySelector('.companion-promote-proposal')?.addEventListener('click', async (event) => {
    const card = event.currentTarget.closest('[data-proposal-id]');
    const selections = {};
    card.querySelectorAll('input[data-proposal-field]:checked').forEach(input => {
      const field = input.dataset.proposalField;
      (selections[field] ||= []).push(Number(input.dataset.proposalIndex));
    });
    if (!Object.keys(selections).length) {
      uiModule.showError?.('Select at least one proposal entry to promote.');
      return;
    }
    event.currentTarget.disabled = true;
    try {
      const response = await fetch(`/api/companion/memory/semantic-proposals/${encodeURIComponent(card.dataset.proposalId)}/promote`, {method: 'POST', credentials: 'same-origin', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({expected_revision: Number(card.dataset.proposalRevision), selections})});
      const result = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(result.detail || 'Could not promote the selected entries');
      uiModule.showToast?.('Selected entries promoted into accepted home memory.', 3000);
      close(); openCompanionMemory(meta, 'context');
    } catch (error) {
      uiModule.showError?.(error.message || 'Could not promote the selected entries');
      event.currentTarget.disabled = false;
    }
  });
  modal.querySelector('.companion-request-grant')?.addEventListener('click', async () => {
    const project = modal.querySelector('.companion-grant-project')?.value; const purpose = modal.querySelector('.companion-grant-purpose')?.value;
    const response = await fetch('/api/companion/context-grants', {method: 'POST', credentials: 'same-origin', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({personal_session_id: meta.id, project_id: project, purpose})});
    const result = await response.json(); uiModule.showToast?.(response.ok ? 'Project access request created — allow it once when prompted.' : (result.detail || 'Could not request access'), 3200);
    if (response.ok) { close(); openCompanionMemory(meta, 'projects'); }
  });
  modal.querySelectorAll('.companion-grant-card button').forEach(button => button.addEventListener('click', async () => {
    const card = button.closest('[data-grant]'); const response = await fetch(`/api/companion/context-grants/${encodeURIComponent(card.dataset.grant)}/decision`, {method: 'POST', credentials: 'same-origin', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({allow: button.dataset.decision === 'allow'})});
    const result = await response.json(); if (!response.ok) { uiModule.showToast?.(result.detail || 'Could not decide project access'); return; }
    close(); openCompanionMemory(meta, 'projects');
  }));
  modal.querySelector('.companion-edit-brief')?.addEventListener('click', () => {
    const current = payload.personal_brief || payload.project_brief || {}; const label = payload.scope_kind === 'project' ? 'Project' : 'Personal'; const endpoint = payload.scope_kind === 'project' ? '/api/companion/project-brief' : '/api/companion/personal-brief'; const row = document.createElement('div'); row.className = 'companion-brief-editor'; row.innerHTML = `<textarea>${esc(current.summary || '')}</textarea><button type="button">Save ${label} brief</button>`; modal.querySelector('[data-panel="context"]').appendChild(row); row.querySelector('button').onclick = async () => {
      const response = await fetch(endpoint, {method: 'POST', credentials: 'same-origin', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({session_id: meta.id, summary: row.querySelector('textarea').value})});
      if (!response.ok) { const err = await response.json(); uiModule.showToast?.(err.detail || `Could not save ${label} brief`); return; } close(); openCompanionMemory(meta, 'context');
    };
  });
  modal.addEventListener('click', event => { if (event.target === modal) close(); }); document.body.appendChild(modal);
}

/** Render the Companion section into `frag`, and return the recent-chat list
 *  with the companion sessions taken out of it. */
export function renderCompanionHomes(orderedSessions, _frag) {
  // G1.5 companion homes are server-bound sessions, not a browser workspace
  // preference. Keep them visibly separate from the ordinary recent-chat list.
  const companion = orderedSessions.filter(s => ['personal', 'computer', 'project'].includes(s.scope_kind));
  {
    const heading = document.createElement('div');
    heading.className = 'date-section-header'; heading.textContent = 'Companion'; _frag.appendChild(heading);
    const appendHome = (session, label, suffix = '') => {
      const button = document.createElement('button'); button.type = 'button'; button.className = 'list-item companion-home';
      button.textContent = `${label}${suffix}`; button.dataset.sessionId = session.id;
      button.addEventListener('click', () => host.selectSession(session.id)); _frag.appendChild(button);
    };
    const companionRoute = async () => {
      const active = host.sessions().find(s => s.id === host.currentSessionId());
      if (active?.endpoint_id && active?.model) return active;
      // Companion homes should be reachable from a clean sidebar, just like
      // Manage Chats. Fall back to the user's configured default route.
      const fallback = window.__odysseusDefaultChat || await fetch(`${API_BASE}/api/default-chat`, { credentials: 'same-origin' }).then(r => r.ok ? r.json() : null);
      return fallback?.endpoint_id && fallback?.model ? fallback : null;
    };
    const openBuiltinHome = async (scope) => {
      const route = await companionRoute();
      if (!route) { uiModule.showToast?.('Choose a registered default model first', 2600); return; }
      const params = new URLSearchParams({ endpoint_id: route.endpoint_id, model: route.model });
      const response = await fetch(`${API_BASE}/api/g1/homes/${scope}/open?${params}`, { method: 'POST', credentials: 'same-origin' });
      const result = await response.json();
      if (!response.ok) { uiModule.showToast?.(result.detail || 'Could not open Companion home', 2500); return; }
      await host.loadSessions(); await host.selectSession(result.id);
    };
    const openProjectHome = async (project) => {
      // Reflect the durable project binding immediately. selectSession repeats
      // this from session metadata, while the backend independently enforces
      // the same Project row on every native turn.
      if (project.workspace_root) {
        workspaceModule.setWorkspace(project.workspace_root, { projectReadOnly: true });
      }
      const existing = companion.find(s => s.scope_kind === 'project' && s.project_id === project.id && s.is_scope_primary);
      if (existing) return host.selectSession(existing.id);
      const route = await companionRoute();
      if (!route) { uiModule.showToast?.('Choose a registered default model first', 2600); return; }
      const params = new URLSearchParams({ endpoint_id: route.endpoint_id, model: route.model });
      const response = await fetch(`${API_BASE}/api/g1/projects/${encodeURIComponent(project.id)}/open?${params}`, {
        method: 'POST', credentials: 'same-origin',
      });
      const result = await response.json();
      if (!response.ok) { uiModule.showToast?.(result.detail || 'Could not open project', 2600); return; }
      await host.loadSessions(); await host.selectSession(result.id);
    };
    const deleteProject = async (project) => {
      if (!await uiModule.styledConfirm(`Delete project “${project.name}” and all of its chats? The workspace files will not be deleted.`, {
        confirmText: 'Delete project', danger: true,
      })) return;
      const response = await fetch(`${API_BASE}/api/g1/projects/${encodeURIComponent(project.id)}`, {
        method: 'DELETE', credentials: 'same-origin',
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) { uiModule.showToast?.(result.detail || 'Could not delete project', 2800); return; }
      if (host.sessions().some(s => s.project_id === project.id && s.id === host.currentSessionId())) {
        host.setCurrentSessionId(null); workspaceModule.setWorkspace('');
      }
      await host.loadSessions(); uiModule.showToast?.('Project deleted; workspace files were kept', 2200);
    };
    const createProjectThread = async (project, home) => {
      const route = home?.endpoint_id && home?.model ? home : await companionRoute();
      if (!route) { uiModule.showToast?.('Choose a registered default model first', 2600); return; }
      const params = new URLSearchParams({ endpoint_id: route.endpoint_id, model: route.model });
      const response = await fetch(`${API_BASE}/api/g1/projects/${encodeURIComponent(project.id)}/fork?${params}`, {
        method: 'POST', credentials: 'same-origin',
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok) { uiModule.showToast?.(result.detail || 'Could not create project thread', 2800); return; }
      if (project.workspace_root) workspaceModule.setWorkspace(project.workspace_root, { projectReadOnly: true });
      await host.loadSessions(); await host.selectSession(result.id);
      uiModule.showToast?.('New project thread · shared brief, fresh transcript', 2600);
    };
    const personal = companion.find(s => s.scope_kind === 'personal' && s.is_scope_primary);
    const personalThreads = companion.filter(s => s.scope_kind === 'personal' && !s.is_scope_primary);
    const computer = companion.find(s => s.scope_kind === 'computer' && s.is_scope_primary);
    if (personal) appendHome(personal, 'Personal Advisor');
    else { const button = document.createElement('button'); button.className = 'list-item companion-home'; button.textContent = 'Personal Advisor'; button.onclick = () => openBuiltinHome('personal'); _frag.appendChild(button); }
    if (personalThreads.length) {
      const threadsHeading = document.createElement('div');
      threadsHeading.className = 'date-section-header'; threadsHeading.textContent = 'Personal threads'; _frag.appendChild(threadsHeading);
      personalThreads.forEach(thread => appendHome(thread, thread.name || 'Personal thread'));
    }
    if (computer) appendHome(computer, 'Computer Help', ' · diagnostics');
    else { const button = document.createElement('button'); button.className = 'list-item companion-home'; button.textContent = 'Computer Help · diagnostics'; button.onclick = () => openBuiltinHome('computer'); _frag.appendChild(button); }
    const projectsHeading = document.createElement('div');
    projectsHeading.className = 'date-section-header'; projectsHeading.textContent = 'Projects'; _frag.appendChild(projectsHeading);
    const newProject = document.createElement('button'); newProject.type = 'button'; newProject.className = 'list-item companion-home'; newProject.textContent = '+ New project';
    newProject.onclick = () => window.__odysseusCreateCompanionProject?.(); _frag.appendChild(newProject);
    host.companionProjects().forEach(project => {
      const home = companion.find(s => s.scope_kind === 'project' && s.project_id === project.id && s.is_scope_primary);
      const row = document.createElement('div'); row.className = 'list-item companion-home'; row.dataset.projectId = project.id;
      const open = document.createElement('button'); open.type = 'button'; open.className = 'grow';
      open.textContent = `${project.name}${home ? '' : ' · no chat'}`;
      open.addEventListener('click', () => openProjectHome(project));
      const thread = document.createElement('button'); thread.type = 'button'; thread.className = 'msg-action-btn';
      thread.title = 'New project thread'; thread.setAttribute('aria-label', `New thread in ${project.name}`); thread.textContent = '+';
      thread.addEventListener('click', (event) => { event.stopPropagation(); createProjectThread(project, home); });
      const remove = document.createElement('button'); remove.type = 'button'; remove.className = 'msg-action-btn';
      remove.title = 'Delete project'; remove.setAttribute('aria-label', `Delete project ${project.name}`); remove.textContent = '×';
      remove.addEventListener('click', (event) => { event.stopPropagation(); deleteProject(project); });
      row.append(open, thread, remove); _frag.appendChild(row);
    });
    orderedSessions = orderedSessions.filter(s => !['personal', 'computer'].includes(s.scope_kind) && !(s.scope_kind === 'project' && s.is_scope_primary));
  }

  return orderedSessions;
}

export {
  _syncCompanionScopeBanner as syncCompanionScopeBanner,
  _loadChatCapabilities as loadChatCapabilities,
};
