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

# Helper: make sure pip exists in the venv (Ubuntu minimal often lacks it)
bootstrap_pip() {
  if "$VENV_DIR/bin/python" -m pip --version >/dev/null 2>&1; then
    return 0
  fi
  echo "Bootstrapping pip in venv…"
  if ! "$VENV_DIR/bin/python" -m ensurepip --upgrade >/dev/null 2>&1; then
    echo "Your Python is missing 'ensurepip' (common on minimal Ubuntu)."
    echo "Please install it and try again:  sudo apt-get install -y python3-venv"
    exit 1
  fi
  "$VENV_DIR/bin/python" -m pip install -U pip wheel
}

# Allow users to force a fresh venv (useful on new machines)
if [[ "${COYOTE_FORCE_VENV:-0}" == "1" && -d "$VENV_DIR" ]]; then
  rm -rf "$VENV_DIR"
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
  bootstrap_pip
  "$VENV_DIR/bin/pip" install -r "$UI_DIR/requirements.txt"
fi

# Self-heal: if Flask is missing, (re)install requirements; if that fails, rebuild venv
if ! "$VENV_DIR/bin/python" - <<'PY'
import importlib.util, sys
sys.exit(0 if importlib.util.find_spec("flask") else 1)
PY
then
  echo "Flask not found in venv — installing requirements…"
  bootstrap_pip
  if ! ( "$VENV_DIR/bin/python" -m pip install -U pip wheel && \
       "$VENV_DIR/bin/pip" install -r "$UI_DIR/requirements.txt"); then
    echo "Repair failed; rebuilding virtualenv…"
    rm -rf "$VENV_DIR"
    "$PY_BIN" -m venv "$VENV_DIR"
    bootstrap_pip
    "$VENV_DIR/bin/pip" install -r "$UI_DIR/requirements.txt"
  fi
fi

# Let the UI server know where compose lives (relative, no manual paths)
export COYOTE_COMPOSE_DIR="$COMPOSE_DIR"

# Ensure Python can import 'shared/*' from project root
export PYTHONPATH="$ROOT_DIR${PYTHONPATH:+:$PYTHONPATH}"

# Gently open the browser shortly after start (can be disabled)
if [[ "${NO_BROWSER:-0}" != "1" ]]; then
  (sleep 2; (command -v open >/dev/null && open "http://localhost:8080") || \
             (command -v xdg-open >/dev/null && xdg-open "http://localhost:8080") || true) &
fi

echo "Starting Coyote UI… (logs in $ROOT_DIR/data/logs)"
cd "$UI_DIR"
exec "$VENV_DIR/bin/python" coyote_ui_server.py