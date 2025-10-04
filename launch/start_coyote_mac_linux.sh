#!/usr/bin/env bash
# Start Coyote UI (macOS/Linux) — creates venv if needed, opens browser.
set -Eeuo pipefail

# Resolve dirs consistently even when double-clicked
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]:-$0}")" && pwd)"
ROOT_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
UI_DIR="$ROOT_DIR/ui"
COMPOSE_DIR="$ROOT_DIR/compose"

# Finder/desktop PATHs are often sparse; include common locations
export PATH="/usr/local/bin:/opt/homebrew/bin:$HOME/.local/bin:$PATH"
export PYTHONUTF8=1

# Prefer the packaged venv name if present, fallback to .venv
VENV_DIR="$UI_DIR/.venv-04"
if [[ ! -x "$VENV_DIR/bin/python" && -x "$UI_DIR/.venv/bin/python" ]]; then
  VENV_DIR="$UI_DIR/.venv"
fi

# Create venv on first run
PY_BIN="$(command -v python3 || true)"
if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
  if [[ -z "$PY_BIN" ]]; then
    echo "Error: python3 not found. Please install Python 3.10+ and try again."
    exit 1
  fi
  echo "Creating virtualenv at ${VENV_DIR} …"
  "$PY_BIN" -m venv "$VENV_DIR"
  "$VENV_DIR/bin/python" -m pip install -U pip
  "$VENV_DIR/bin/python" -m pip install -r "$UI_DIR/requirements.txt"
fi

# Let the UI server know where compose lives (relative, no manual paths)
export COYOTE_COMPOSE_DIR="$COMPOSE_DIR"

# Gently open the browser shortly after start (can be disabled)
if [[ "${NO_BROWSER:-0}" != "1" ]]; then
  (sleep 2; (command -v open >/dev/null && open "http://localhost:8080") || \
             (command -v xdg-open >/dev/null && xdg-open "http://localhost:8080") || true) &
fi

echo "Starting Coyote UI… (logs in $ROOT_DIR/data/logs)"
cd "$UI_DIR"
exec "$VENV_DIR/bin/python" coyote_ui_server.py
