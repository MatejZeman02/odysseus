#!/usr/bin/env bash
# Launch the G1.5 test server from this checkout, not an older installation.
set -euo pipefail
repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
cd "$repo_root"
export ODYSSEUS_QWEN_HARNESS=1
export ODYSSEUS_QWEN_BINARY="${ODYSSEUS_QWEN_BINARY:-$repo_root/data/qwen/0.21.3/node_modules/.bin/qwen}"
export APP_PORT=7001
# Some desktop/editor environments export DEBUG=release. AppConfig correctly
# expects a boolean, so make this launcher deterministic instead of inheriting
# an unrelated ambient DEBUG value.
export DEBUG=false

if [[ -x "$repo_root/venv/bin/python" ]]; then
  python_bin="$repo_root/venv/bin/python"
else
  python_bin="$(command -v python3)"
fi

# A second launch used to spend several seconds initializing and then fail at
# bind time, leaving the browser connected to stale Python modules. Restart an
# existing G1.5 process only when it demonstrably belongs to this checkout.
if command -v fuser >/dev/null 2>&1; then
  existing_pids=$(fuser -n tcp "$APP_PORT" 2>/dev/null || true)
  for existing_pid in $existing_pids; do
    process_cwd=$(readlink -f "/proc/$existing_pid/cwd" 2>/dev/null || true)
    process_cmd=$(tr '\0' ' ' < "/proc/$existing_pid/cmdline" 2>/dev/null || true)
    if [[ "$process_cwd" != "$repo_root" || "$process_cmd" != *"app.py"* ]]; then
      echo "Port $APP_PORT is already used by another process (PID $existing_pid)." >&2
      echo "Odysseus G1.5 was not started." >&2
      exit 1
    fi
    echo "Restarting existing Odysseus G1.5 process (PID $existing_pid)..."
    kill -TERM "$existing_pid"
    for _attempt in {1..50}; do
      kill -0 "$existing_pid" 2>/dev/null || break
      sleep 0.1
    done
    if kill -0 "$existing_pid" 2>/dev/null; then
      echo "Existing Odysseus G1.5 process did not stop cleanly." >&2
      exit 1
    fi
  done
fi

exec "$python_bin" app.py
