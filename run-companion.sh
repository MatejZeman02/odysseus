#!/usr/bin/env bash
# Launch the Companion server from this checkout, not an older installation.
set -euo pipefail
repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
cd "$repo_root"
export ODYSSEUS_QWEN_HARNESS=1
export ODYSSEUS_QWEN_BINARY="${ODYSSEUS_QWEN_BINARY:-$repo_root/data/qwen/0.21.3/node_modules/.bin/qwen}"
export ODYSSEUS_COMPUTER_SANDBOX_IMAGE="${ODYSSEUS_COMPUTER_SANDBOX_IMAGE:-ghcr.io/qwenlm/qwen-code@sha256:216bd08d6ba6819245b78bffd9ef9ecb2442af6ec0e2ed26f9a8727386795946}"
export APP_PORT=7001
# Some desktop/editor environments export DEBUG=release. AppConfig correctly
# expects a boolean, so make this launcher deterministic instead of inheriting
# an unrelated ambient DEBUG value.
export DEBUG=false

action="${1:-start}"
if [[ $# -gt 1 ]]; then
  echo "Usage: $0 [start|reload|stop|status]" >&2
  exit 2
fi

if [[ -x "$repo_root/venv/bin/python" ]]; then
  python_bin="$repo_root/venv/bin/python"
else
  python_bin="$(command -v python3)"
fi

if ! command -v fuser >/dev/null 2>&1; then
  echo "The Companion launcher needs fuser to inspect port $APP_PORT." >&2
  exit 1
fi

port_pids() {
  fuser -n tcp "$APP_PORT" 2>/dev/null || true
}

is_this_checkout() {
  local pid="$1"
  local process_cwd process_cmd
  process_cwd=$(readlink -f "/proc/$pid/cwd" 2>/dev/null || true)
  process_cmd=$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null || true)
  [[ "$process_cwd" == "$repo_root" && "$process_cmd" == *"app.py"* ]]
}

process_is_running() {
  local pid="$1" state
  kill -0 "$pid" 2>/dev/null || return 1
  # A process that has exited but has not yet been reaped still answers
  # `kill -0`. Treat that zombie state as stopped so a normal restart never
  # reports a false failure or leaves port 7001 without a replacement server.
  state=$(awk '{print $3}' "/proc/$pid/stat" 2>/dev/null || true)
  [[ "$state" != "Z" ]]
}

stop_this_checkout() {
  local existing_pid stopped=false
  for existing_pid in $(port_pids); do
    if ! is_this_checkout "$existing_pid"; then
      echo "Port $APP_PORT is used by another process (PID $existing_pid); refusing to stop it." >&2
      return 1
    fi
    echo "Stopping Odysseus Companion (PID $existing_pid)..."
    kill -TERM "$existing_pid"
    for _attempt in {1..50}; do
      process_is_running "$existing_pid" || break
      sleep 0.1
    done
    if process_is_running "$existing_pid"; then
      echo "Odysseus Companion did not stop cleanly (PID $existing_pid)." >&2
      return 1
    fi
    stopped=true
  done
  if [[ "$stopped" == false ]]; then
    echo "Odysseus Companion is not running on port $APP_PORT."
  else
    echo "Odysseus Companion stopped."
  fi
}

case "$action" in
  status)
    pids=$(port_pids)
    if [[ -z "$pids" ]]; then
      echo "Odysseus Companion is not running on port $APP_PORT."
      exit 0
    fi
    for pid in $pids; do
      if is_this_checkout "$pid"; then
        echo "Odysseus Companion is running on http://127.0.0.1:$APP_PORT (PID $pid)."
      else
        echo "Port $APP_PORT is used by another process (PID $pid), not this checkout." >&2
        exit 1
      fi
    done
    exit 0
    ;;
  stop)
    stop_this_checkout
    exit $?
    ;;
  start|reload)
    ;;
  *)
    echo "Usage: $0 [start|reload|stop|status]" >&2
    exit 2
    ;;
esac

# A second launch used to spend several seconds initializing and then fail at
# bind time, leaving the browser connected to stale Python modules. Start
# therefore restarts an existing server only when it belongs to this checkout.
if [[ -n "$(port_pids)" ]]; then
  if [[ "$action" == "reload" ]]; then
    echo "Reloading the existing Companion server..."
  else
    echo "Restarting the existing Companion server before launch..."
  fi
  stop_this_checkout
elif [[ "$action" == "reload" ]]; then
  echo "Odysseus Companion is not running; starting it instead."
fi

exec "$python_bin" app.py
