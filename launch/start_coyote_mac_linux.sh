#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../ui"
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
  .venv/bin/python -m pip install -U pip
  .venv/bin/python -m pip install -r requirements.txt
fi
export COYOTE_COMPOSE_DIR="$(cd ../compose && pwd)"
exec .venv/bin/python coyote_ui_server.py
