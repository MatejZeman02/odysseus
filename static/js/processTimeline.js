// Shared process timeline primitives. Native Agent history and Companion
// progress both use these nodes so tool calls keep one Odysseus visual and
// interaction contract.

function appendCommand(content, command) {
  const text = String(command || '').trim();
  if (!text) return;
  const pre = document.createElement('pre');
  pre.className = 'agent-thread-cmd';
  pre.textContent = text;
  content.appendChild(pre);
}

export function compactProcessLabel(value, { preserveEnd = false, limit = 160 } = {}) {
  const text = String(value || '').trim();
  if (text.length <= limit) return text;
  if (!preserveEnd) return `${text.slice(0, Math.max(1, limit - 1))}✂`;
  const left = Math.max(1, Math.floor((limit - 1) * 0.34));
  return `${text.slice(0, left)}✂${text.slice(-(limit - left - 1))}`;
}

export function createProcessThread({ streaming = false, hasTop = false, hasBottom = false } = {}) {
  const thread = document.createElement('div');
  thread.className = 'agent-thread';
  thread.classList.toggle('streaming', streaming);
  thread.classList.toggle('has-top', hasTop);
  thread.classList.toggle('has-bottom', hasBottom);
  return thread;
}

export function createProcessToolNode({
  label,
  command = '',
  running = false,
  ok = true,
  status = '',
  icon = '',
  contentHtml = '',
  open = false,
} = {}) {
  const node = document.createElement('div');
  node.className = `agent-thread-node${running ? ' running' : ''}${!running && !ok ? ' error' : ''}${open ? ' open' : ''}`;

  const dot = document.createElement('div');
  dot.className = 'agent-thread-dot';
  const header = document.createElement('div');
  header.className = 'agent-thread-header';
  const iconNode = document.createElement('span');
  iconNode.className = 'agent-thread-icon';
  iconNode.textContent = icon || (running ? '▶' : (ok ? '✓' : '✗'));
  const labelNode = document.createElement('span');
  labelNode.className = 'agent-thread-tool';
  labelNode.textContent = String(label || 'Tool');
  header.append(iconNode, labelNode);

  if (running) {
    const wave = document.createElement('span');
    wave.className = 'agent-thread-wave';
    wave.textContent = '▁▂▃';
    header.appendChild(wave);
  } else {
    const statusNode = document.createElement('span');
    statusNode.className = 'agent-thread-status';
    statusNode.textContent = String(status || (ok ? 'done' : 'failed'));
    const chevron = document.createElement('span');
    chevron.className = 'agent-thread-chevron';
    chevron.textContent = '▶';
    header.append(statusNode, chevron);
  }

  const content = document.createElement('div');
  content.className = 'agent-thread-content';
  appendCommand(content, command);
  if (contentHtml) content.insertAdjacentHTML('beforeend', contentHtml);
  node.append(dot, header, content);
  return node;
}

export function replaceProcessToolNode(node, options = {}) {
  const replacement = createProcessToolNode({
    ...options,
    open: options.open ?? node.classList.contains('open'),
  });
  node.replaceWith(replacement);
  return replacement;
}

export function appendProcessCommentary(thread, value) {
  const text = String(value || '').trim();
  if (!thread || !text) return null;
  const last = thread.lastElementChild;
  if (last?.classList.contains('qwen-process-commentary') && last.textContent === text) return last;
  const row = document.createElement('div');
  row.className = 'qwen-process-commentary';
  const body = document.createElement('p');
  body.textContent = text;
  row.appendChild(body);
  thread.appendChild(row);
  return row;
}
