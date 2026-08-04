#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$repo_root"
if [[ -x "$repo_root/venv/bin/python" ]]; then
  app_python="$repo_root/venv/bin/python"
else
  app_python="$(command -v python3)"
fi
audit_python=""
for candidate in "$app_python" "$(command -v python 2>/dev/null || true)" "$(command -v python3 2>/dev/null || true)"; do
  if [[ -n "$candidate" ]] && "$candidate" -c 'from websockets.sync.client import connect' >/dev/null 2>&1; then
    audit_python="$candidate"
    break
  fi
done
if [[ -z "$audit_python" ]]; then
  echo "The browser audit requires a Python installation with websockets.sync.client." >&2
  exit 1
fi

# Run a rendered-browser audit without touching the live Odysseus database.
# The optional first argument is a session id from the source database. When
# omitted, prefer the most recently active primary project home so the workflow
# remains portable across installations instead of depending on one Dust UUID.
audit_session_id="${1:-}"
if [[ -z "$audit_session_id" ]]; then
  audit_session_id="$(sqlite3 data/app.db "
    SELECT id FROM sessions
    WHERE scope_kind='project' AND is_scope_primary=1 AND archived=0
      AND project_id IS NOT NULL AND endpoint_id IS NOT NULL AND model != ''
    ORDER BY COALESCE(last_message_at, updated_at, created_at) DESC
    LIMIT 1;
  ")"
fi
if [[ -z "$audit_session_id" ]]; then
  echo "No eligible primary project session exists for the browser audit." >&2
  exit 1
fi
if [[ ! "$audit_session_id" =~ ^[A-Za-z0-9_-]+$ ]]; then
  echo "The browser-audit session id contains unsupported characters." >&2
  exit 1
fi
audit_app_port="${ODYSSEUS_AUDIT_APP_PORT:-7002}"
audit_debug_port="${ODYSSEUS_AUDIT_DEBUG_PORT:-9223}"
audit_root="$(mktemp -d /tmp/odysseus-ui-audit.XXXXXX)"
audit_output="${ODYSSEUS_AUDIT_OUTPUT:-$PWD/artifacts/browser-ui-audit}"
audit_username="ui-audit"
audit_password="odysseus-browser-audit-password"
audit_live_qwen="${ODYSSEUS_AUDIT_LIVE_QWEN:-0}"
audit_synthetic="${ODYSSEUS_AUDIT_SYNTHETIC:-0}"
audit_edit_offset="${ODYSSEUS_AUDIT_EDIT_OFFSET:-0}"
audit_server_pid=""
audit_browser_pid=""

cleanup() {
  if [[ -f "$audit_root/server.log" ]]; then cp "$audit_root/server.log" "$audit_output/server.log"; fi
  if [[ -f "$audit_root/brave.log" ]]; then cp "$audit_root/brave.log" "$audit_output/brave.log"; fi
  if [[ -n "$audit_browser_pid" ]]; then kill -- "-$audit_browser_pid" 2>/dev/null || true; fi
  if [[ -n "$audit_server_pid" ]]; then kill -- "-$audit_server_pid" 2>/dev/null || true; fi
  for _ in $(seq 1 30); do
    audit_browser_alive=false
    audit_server_alive=false
    if [[ -n "$audit_browser_pid" ]] && kill -0 -- "-$audit_browser_pid" 2>/dev/null; then audit_browser_alive=true; fi
    if [[ -n "$audit_server_pid" ]] && kill -0 -- "-$audit_server_pid" 2>/dev/null; then audit_server_alive=true; fi
    if ! $audit_browser_alive && ! $audit_server_alive; then break; fi
    sleep 0.1
  done
  rm -rf -- "$audit_root"
}
trap cleanup EXIT INT TERM

mkdir -p "$audit_output"
if ss -ltn | rg -q ":($audit_app_port|$audit_debug_port)[[:space:]]"; then
  echo "Audit port $audit_app_port or $audit_debug_port is already in use" >&2
  exit 1
fi
sqlite3 data/app.db ".backup '$audit_root/app.db'"
mkdir -p "$audit_root/data"
ODYSSEUS_DATA_DIR="$audit_root/data" DEBUG=false "$app_python" -c \
  "from core.auth import AuthManager; assert AuthManager().setup('$audit_username', '$audit_password')"
if [[ "$audit_live_qwen" == "1" || "$audit_synthetic" == "1" ]]; then
  # Keep the registered provider route only inside the disposable database,
  # while replacing every piece of user/project context with a synthetic
  # fixture. Qwen receives a loopback bridge URL and ephemeral token, never
  # the provider route or the source database's private project content.
  mkdir -p "$audit_root/workspace"
  cp docs/browser-ui-audit.md "$audit_root/workspace/README.md"
  git -C "$audit_root/workspace" init --quiet
  git -C "$audit_root/workspace" add README.md
  git -C "$audit_root/workspace" -c user.name='Odysseus UI Audit' -c user.email='ui-audit@localhost' commit --quiet -m 'Synthetic browser fixture'
  if [[ "$audit_live_qwen" == "1" ]]; then
    # Model endpoint secrets in the cloned database are encrypted. Reuse the
    # source app key only inside this short-lived directory; cleanup destroys it.
    cp data/.app_key "$audit_root/data/.app_key"
    chmod 600 "$audit_root/data/.app_key"
  fi
  sqlite3 "$audit_root/app.db" "
    UPDATE model_endpoints SET owner='$audit_username'
      WHERE id=(SELECT endpoint_id FROM sessions WHERE id='$audit_session_id');
    UPDATE projects SET owner='$audit_username', name='UI audit project', workspace_root='$audit_root/workspace'
      WHERE id=(SELECT project_id FROM sessions WHERE id='$audit_session_id');
    UPDATE sessions SET owner='$audit_username', name='UI audit project', message_count=6
      WHERE id='$audit_session_id';
    DELETE FROM continuity_artifacts WHERE session_id='$audit_session_id'
      OR project_id=(SELECT project_id FROM sessions WHERE id='$audit_session_id');
    DELETE FROM chat_messages WHERE session_id='$audit_session_id';
    INSERT INTO chat_messages(id, session_id, role, content, metadata, timestamp) VALUES
      ('ui-audit-user-1', '$audit_session_id', 'user', 'What file is in this workspace?', NULL, '2026-08-04 10:00:00'),
      ('ui-audit-assistant-1', '$audit_session_id', 'assistant', 'The workspace contains README.md.', NULL, '2026-08-04 10:00:01'),
      ('ui-audit-user-2', '$audit_session_id', 'user', 'Read README.md and state its title in one short sentence.', NULL, '2026-08-04 10:00:02'),
      ('ui-audit-assistant-2', '$audit_session_id', 'assistant', 'Its title is Rendered browser UI audit.', json_object(
        'harness', 'qwen', 'qwen_read_only', json('true'), 'workspace_unchanged', json('true'),
        'model', 'ui-audit-model',
        'qwen_process', json_object('elapsed_seconds', 12, 'events', json_array(json_object(
          'operation', 'Read project file', 'tool', 'read_file', 'path', 'README.md',
          'label', 'Read: README.md', 'command', 'Read: README.md', 'status', 'done'
        ))),
        'g1_feedback', json_object('rating', 'helpful', 'note', 'Persisted synthetic feedback', 'harness', 'qwen')
      ), '2026-08-04 10:00:03'),
      ('ui-audit-user-3', '$audit_session_id', 'user', 'Does the guide use a disposable browser profile?', NULL, '2026-08-04 10:00:04'),
      ('ui-audit-assistant-3', '$audit_session_id', 'assistant', 'Yes.', NULL, '2026-08-04 10:00:05');
  "
  if [[ "$audit_live_qwen" == "1" ]]; then
    audit_qwen_enabled=1
  else
    sqlite3 "$audit_root/app.db" "
      UPDATE model_endpoints SET base_url='http://127.0.0.1:9/v1', api_key=''
        WHERE id=(SELECT endpoint_id FROM sessions WHERE id='$audit_session_id');
    "
    audit_qwen_enabled=0
  fi
else
  sqlite3 "$audit_root/app.db" "UPDATE sessions SET owner='$audit_username', endpoint_url='http://127.0.0.1:9/v1'; UPDATE projects SET owner='$audit_username'; UPDATE model_endpoints SET owner='$audit_username', base_url='http://127.0.0.1:9/v1', api_key=''; UPDATE continuity_artifacts SET owner='$audit_username';"
  audit_qwen_enabled=0
fi

# A second, credential-free endpoint lets the rendered audit verify that a
# project session persists both endpoint_id and model across a full reload.
sqlite3 "$audit_root/app.db" "
  INSERT OR REPLACE INTO model_endpoints(
    id, name, base_url, api_key, is_enabled, hidden_models, cached_models,
    pinned_models, model_type, endpoint_kind, model_refresh_mode,
    model_refresh_interval, model_refresh_timeout, supports_tools, owner,
    provider_auth_id, created_at, updated_at
  ) VALUES (
    'ui-audit-endpoint', 'UI audit inert endpoint', 'http://127.0.0.1:9/v1',
    NULL, 1, '[]', '[\"ui-audit-model\"]', '[]', 'llm', 'local', 'manual',
    NULL, NULL, 1, '$audit_username', NULL, datetime('now'), datetime('now')
  );
"

setsid env \
AUTH_ENABLED=true \
LOCALHOST_BYPASS=false \
ODYSSEUS_DATA_DIR="$audit_root/data" \
DATABASE_URL="sqlite:///$audit_root/app.db" \
APP_PORT="$audit_app_port" \
DEBUG=false \
ODYSSEUS_QWEN_HARNESS="$audit_qwen_enabled" \
ODYSSEUS_QWEN_BINARY="$PWD/data/qwen/0.21.3/node_modules/.bin/qwen" \
ODYSSEUS_STARTUP_WARMUPS=0 \
"$app_python" app.py >"$audit_root/server.log" 2>&1 &
audit_server_pid=$!

for _ in $(seq 1 200); do
  if curl --silent --fail --max-time 1 "http://127.0.0.1:$audit_app_port/" >/dev/null; then break; fi
  if ! kill -0 "$audit_server_pid" 2>/dev/null; then
    sed -n '1,240p' "$audit_root/server.log"
    exit 1
  fi
  sleep 0.1
done

setsid /usr/bin/brave-browser \
  --headless=new \
  --no-sandbox \
  --disable-gpu \
  --disable-extensions \
  --disable-background-networking \
  --remote-debugging-port="$audit_debug_port" \
  --user-data-dir="$audit_root/brave" \
  "http://127.0.0.1:$audit_app_port/#$audit_session_id" \
  >"$audit_root/brave.log" 2>&1 &
audit_browser_pid=$!

for _ in $(seq 1 200); do
  if curl --silent --fail --max-time 1 "http://127.0.0.1:$audit_debug_port/json/list" >/dev/null; then break; fi
  if ! kill -0 "$audit_browser_pid" 2>/dev/null; then
    sed -n '1,240p' "$audit_root/brave.log"
    exit 1
  fi
  sleep 0.1
done

audit_browser_args=()
if [[ "$audit_live_qwen" == "1" ]]; then
  audit_browser_args+=(--wait-for-turn 240)
fi
if [[ "$audit_synthetic" == "1" ]]; then
  audit_browser_args+=(--expect-persisted-process)
fi

timeout 300s "$audit_python" scripts/browser_ui_audit.py \
  --debug-port "$audit_debug_port" \
  --app-port "$audit_app_port" \
  --session-id "$audit_session_id" \
  --username "$audit_username" \
  --password "$audit_password" \
  --edit-user-offset "$audit_edit_offset" \
  "${audit_browser_args[@]}" \
  --output "$audit_output"
