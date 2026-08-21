#!/usr/bin/env python3
"""
coyote_ui_server.py - Fixed version with proper project name handling
"""

from __future__ import annotations
from flask import Flask, render_template, jsonify, request as flask_request
import subprocess
import json
import os
import secrets
import re
from datetime import datetime, timedelta
import logging
from pathlib import Path
import base64
import urllib.request
import urllib.error
import shlex
import traceback
import sys
import time
import threading
import requests
current_dir = Path(__file__).parent
ROOT_DIR = current_dir.parent  # .../Coyote_0.4
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from shared.nl2cypher import (
    prompt_text, schema_for_prompts, SCHEMA_MIN,
    strip_fences_or_json,  # also used in bot.py
    looks_like_cypher,     # reuse the same heuristic
    is_read_only,
)

# Configure paths
template_dir = current_dir / 'templates'
static_dir = current_dir / 'static'

# Set up logging directory
LOG_DIR = current_dir.parent / 'data' / 'logs'
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOG_DIR / f'coyote_ui_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log'

# Force UTF-8 on the console streams. Windows consoles default to a legacy
# code page (e.g. cp1252) that cannot encode non-ASCII log characters like
# "✓" or "…", which otherwise raise UnicodeEncodeError in the StreamHandler
# and dump a logging-error traceback as the first thing a user sees.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")  # Python 3.7+
    except (AttributeError, ValueError):
        pass

# Configure logging with both file and console output
# Use COYOTE_LOG_LEVEL env var (default: INFO) to control verbosity
_log_level_name = os.environ.get("COYOTE_LOG_LEVEL", "INFO").upper()
_log_level = getattr(logging, _log_level_name, logging.INFO)
logging.basicConfig(
    level=_log_level,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)
logger.info(f"Logging to file: {LOG_FILE}")

app = Flask(__name__,
           template_folder=str(template_dir),
           static_folder=str(static_dir))

# Limit request size (16MB default - generous for local-first tool)
app.config['MAX_CONTENT_LENGTH'] = int(os.environ.get('COYOTE_MAX_REQUEST_MB', '16')) * 1024 * 1024

@app.errorhandler(413)
def handle_request_too_large(e):
    """Return JSON error for requests exceeding MAX_CONTENT_LENGTH."""
    return jsonify({'status': 'error', 'message': 'Request too large'}), 413

def _validate_string_input(value: str, max_length: int = 50000, field_name: str = "input") -> str:
    """Validate and truncate string input."""
    if not isinstance(value, str):
        raise ValueError(f"{field_name} must be a string")
    if len(value) > max_length:
        logger.warning(f"{field_name} truncated from {len(value)} to {max_length} chars")
        return value[:max_length]
    return value

try:
    from shared.nl2cypher import prompt_text, schema_for_prompts
    test_prompt = prompt_text("graph")
    if "date(datetime(" in test_prompt:
        logger.info("✓ nl2cypher.py import successful and prompt is updated")
    else:
        logger.error("✗ nl2cypher.py imported but prompt still has old syntax!")
except Exception as e:
    logger.error(f"✗ Failed to import from nl2cypher: {e}")

# ─────────────────────────────────────────────────────────────────────────────
# Allowed schema (lightweight allow‑list for schema gate)
# Labels, relationship types, and property names present in SCHEMA_MIN.
# NOTE: property->label mapping is intentionally not enforced here (MVP).
ALLOWED_LABELS = {
    "Webpage", "Annotation", "Purpose", "SearchTerms", "WikiDataOntology"
}
ALLOWED_REL_TYPES = {"HAS_TOPIC", "INITIATES_SEARCH", "HAS_ANNOTATION", "LINKS_TO", "INITIATES", "GENERATES_SERP"}
ALLOWED_PROPS = {
    # Webpage
    "event_id","url","title","summary","timestamp","isSERP","dataSource","entities","topics","active_seconds",
    # Annotation
    "annotation_id","annotation_text","highlighted_text","webpage_title",
    # Purpose
    "text","relevance",
    # SearchTerms shares text/relevance/timestamp/dataSource/entities/topics
    "uri","label"
}
# Ban legacy/demo artifacts explicitly
_BANNED_ARTIFACTS_RE = re.compile(r'(?i)\bstackoverflow\b|[:\[]\s*:?ANSWERS\b|:\s*(Question|Answer)\b')

# Extension heartbeat tracking
extension_heartbeats = {}
HEARTBEAT_TIMEOUT = 15  # seconds

# Docker configuration
DOCKER_BIN = os.environ.get("DOCKER_BIN", "docker")
PROJECT_NAME = "coyote"  # Fixed project name


class DockerUnavailable(Exception):
    """Raised when the docker CLI is missing or the engine isn't reachable.

    Carries a plain-English `user_message` safe to return straight to the UI,
    so endpoints surface actionable guidance instead of a raw
    ``[WinError 2]``/``Cannot connect to the Docker daemon`` traceback.
    """

    def __init__(self, message: str):
        super().__init__(message)
        self.user_message = message


DOCKER_MISSING_MSG = (
    "Docker not found. Install Docker Desktop (Windows/macOS) or Docker "
    "Engine + Compose (Linux) and make sure it's running, then try again."
)
DOCKER_ENGINE_DOWN_MSG = (
    "Docker is installed but the engine isn't running. Start Docker Desktop "
    "(on Windows, also confirm virtualization / WSL2 is enabled), wait for it "
    "to report \"running\", then try again."
)


def _looks_like_engine_down(text) -> bool:
    """True if `text` (a command's stderr) reads like an unreachable daemon."""
    if not text or not isinstance(text, str):
        return False
    t = text.lower()
    return (
        "cannot connect to the docker daemon" in t
        or "error during connect" in t
        or "the docker daemon is not running" in t
        or "is the docker daemon running" in t
    )


def _docker_run(cmd, **kwargs):
    """Run a docker CLI command, translating two environment failures into a
    :class:`DockerUnavailable` carrying a user-facing message:

      * missing binary (``FileNotFoundError`` / Windows ``WinError 2``)
      * engine not running (non-zero exit whose stderr names the daemon)

    Every other outcome (including ordinary non-zero exits) is returned as the
    normal ``subprocess.run`` result. All raw ``DOCKER_BIN`` calls route through
    here so the translation lives in exactly one place.
    """
    try:
        result = subprocess.run(cmd, **kwargs)
    except FileNotFoundError:
        raise DockerUnavailable(DOCKER_MISSING_MSG)
    if getattr(result, "returncode", 0) != 0 and _looks_like_engine_down(getattr(result, "stderr", None)):
        raise DockerUnavailable(DOCKER_ENGINE_DOWN_MSG)
    return result

# Compose location (ui/ -> ../compose)
BASE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_COMPOSE_DIR = BASE_DIR / "compose"
COMPOSE_DIR = Path(os.environ.get("COYOTE_COMPOSE_DIR", str(DEFAULT_COMPOSE_DIR))).resolve()
COMPOSE_FILE = COMPOSE_DIR / "compose.yaml"
ENV_FILE = COMPOSE_DIR / ".env"

def _parse_env_file():
    env = {}
    try:
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith('#') or '=' not in line: continue
            k, v = line.split('=', 1)
            env[k.strip()] = v.strip()
    except Exception:
        pass
    return env

def _write_env(updates: dict):
    """Merge key/value `updates` into compose/.env and chmod 600."""
    env = _parse_env_file()
    env.update(updates or {})
    lines = [f"{k}={env[k]}" for k in sorted(env.keys())]
    ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
    ENV_FILE.write_text("\n".join(lines) + "\n")
    try:
        os.chmod(ENV_FILE, 0o600)
    except Exception:
        logger.warning("Could not chmod 600 on .env")

def get_compose_env():
    """Get consistent environment for all compose commands"""
    env = os.environ.copy()
    # Always set project name
    env['COMPOSE_PROJECT_NAME'] = PROJECT_NAME
    # Ensure compose file is specified
    env['COMPOSE_FILE'] = str(COMPOSE_FILE)
    return env

def _compose_error_tail(result, max_len=800):
    """Return a trimmed tail of a failed compose result's output for display.

    The decisive BuildKit/compose error (``failed to solve ... exit code: 1``
    and the pip/network cause above it) sits at the END of stderr, so we tail
    rather than head. Falls back to stdout when stderr is empty. The full
    output is logged separately; this is only the short version surfaced in
    the dashboard status message.
    """
    text = (getattr(result, "stderr", "") or "").strip()
    if not text:
        text = (getattr(result, "stdout", "") or "").strip()
    if len(text) > max_len:
        text = "..." + text[-max_len:]
    return text


def run_compose_command(args, timeout=120):
    """Run a docker compose command with consistent settings"""
    cmd = [DOCKER_BIN, 'compose', '-p', PROJECT_NAME, '-f', str(COMPOSE_FILE)] + args
    
    logger.debug(f"Running command: {' '.join(cmd)}")
    logger.debug(f"Working directory: {COMPOSE_DIR}")
    
    try:
        result = _docker_run(
            cmd,
            cwd=str(COMPOSE_DIR),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=get_compose_env()
        )

        logger.debug(f"Command exit code: {result.returncode}")
        if result.stdout:
            logger.debug(f"stdout: {result.stdout}")
        if result.stderr:
            logger.debug(f"stderr: {result.stderr}")

        # On failure, surface the reason at ERROR so it lands in the log file
        # (the DEBUG lines above are invisible at the default INFO level, which
        # is why a failed "Start" previously logged only a return code).
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            if detail:
                logger.error(
                    "Compose command failed (exit %s): %s",
                    result.returncode, detail,
                )

        return result
    except DockerUnavailable as e:
        # Expected environment failure — log a one-liner, not a scary traceback.
        logger.warning(f"Docker unavailable: {e.user_message}")
        raise
    except subprocess.TimeoutExpired as e:
        logger.error(f"Command timed out after {timeout} seconds")
        raise
    except Exception as e:
        logger.error(f"Command failed: {e}", exc_info=True)
        raise

def ensure_env_file():
    """Create .env file with defaults if it doesn't exist"""
    if not ENV_FILE.exists():
        logger.info(f"Creating default .env at {ENV_FILE}")
        
        # Generate secure Neo4j password. Use token_hex, NOT token_urlsafe:
        # the URL-safe alphabet includes '-', and a password beginning with '-'
        # is parsed as a CLI flag by the Neo4j image's
        # `neo4j-admin dbms set-initial-password`, crashing the container in a
        # restart loop (exit 64). Hex (0-9a-f) is 128-bit and flag-safe.
        neo4j_password = secrets.token_hex(16)
        
        default_env = f"""# Auto-generated by Coyote UI on {datetime.now().isoformat()}
# Ports (host)
COYOTE_PORT=5000
NEO4J_HTTP_PORT=7474
NEO4J_BOLT_PORT=7687
OLLAMA_PORT=11434
BOT_PORT=8501

# Credentials
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD={neo4j_password}

# Paths (relative to compose/)
COYOTE_DATA_DIR=./volumes/neo4j
COYOTE_MODELS_DIR=./volumes/ollama
COYOTE_USER_DATA=./volumes/coyote
EMBEDDING_MODEL_DIR=./volumes/embedding_model

# Service configuration
NEO4J_URI=bolt://database:7687
OLLAMA_BASE_URL=http://llm:11434
LLM=qwen2.5-coder:3b
EMBEDDING_MODEL=sentence_transformer
"""
        ENV_FILE.write_text(default_env)
        logger.info(f"Created .env with Neo4j password: {neo4j_password[:8]}...")

@app.before_request
def before_first_request():
    """Ensure environment is set up before handling requests"""
    ensure_env_file()

@app.route('/')
def index():
    """Serve the main UI (wireframe_v2.html — Phase 5b cutover)"""
    logger.debug("Serving main UI page")
    return render_template('wireframe_v2.html')

# Legacy fallback — coyote_wireframe.html accessible at /legacy
# until wireframe_v2.html is confirmed stable in production.
# Remove this route once stability is confirmed post-MVP.
@app.route('/legacy')
def legacy_ui():
    logger.info("Legacy UI route accessed")
    return render_template('coyote_wireframe.html')

@app.route('/extension_heartbeat', methods=['POST'])
def extension_heartbeat():
    """Accept heartbeats from the browser extension"""
    try:
        data = flask_request.get_json(silent=True) or {}
        ext_id = data.get('extensionId') or data.get('browserName') or 'unknown'
        
        extension_heartbeats[ext_id] = {
            'timestamp': datetime.utcnow(),
            'data': data
        }
        
        logger.debug(f"Extension heartbeat received from: {ext_id}")
        return jsonify({'status': 'ok'})
        
    except Exception as e:
        logger.error(f"Error processing extension heartbeat: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/extension_status', methods=['GET'])
def extension_status():
    """Return extension status based on recent heartbeats"""
    now = datetime.utcnow()
    active_extensions = []
    
    for ext_id, info in extension_heartbeats.items():
        age = (now - info['timestamp']).total_seconds()
        if age <= HEARTBEAT_TIMEOUT:
            active_extensions.append({
                'id': ext_id,
                'age_seconds': age,
                'active': True
            })
    
    return jsonify({
        'active': len(active_extensions) > 0,
        'extensions': active_extensions
    })

# --- Ollama model-ensure daemon ----------------------------------------------
# A fresh install has NO LLM model in Ollama: compose pulls the ollama *image*
# (`--pull=missing`) but never the model, so Chat / NL-query fail with "model
# not found" until it is pulled. A background daemon keeps the configured model
# present whenever Ollama is up. It is DECOUPLED from `compose up` returning 0 —
# an earlier design triggered the pull only after a successful `up`, which on a
# cold install silently failed: the first `up` builds images + pulls the 5.6 GB
# ollama image and outran its timeout, so the trigger was never reached even
# though the containers came up moments later (Windows gate, 2026-08-21). The
# daemon instead self-heals across a cold-build timeout, a UI-server restart,
# Start LLM vs Start All, or a manually-removed model, and re-reads .env each
# tick so a Settings model change is picked up without a restart.
_MODEL_ENSURE_INTERVAL = 10        # seconds between ensure-loop ticks
_MODEL_ENSURE_ERROR_BACKOFF = 300  # seconds to wait after a failed pull before retrying it
_model_pull_lock = threading.Lock()
_model_pull_state = {"status": "idle", "detail": "", "model": None}  # status: idle|pulling|done|error


def _set_pull_state(status, detail="", model=None):
    with _model_pull_lock:
        _model_pull_state["status"] = status
        _model_pull_state["detail"] = detail
        if status == "error":
            # Stamp WHICH model failed and when, so the retry backoff is keyed to
            # that model — a Settings change to a different model is not throttled
            # by the old model's cooldown.
            _model_pull_state["error_at"] = time.time()
            _model_pull_state["error_model"] = model


def _ollama_base():
    port = _parse_env_file().get("OLLAMA_PORT", "11434")
    return f"http://localhost:{port}"


def _ollama_reachable(base):
    """Quick check that Ollama's HTTP server is answering (fast-fails when down)."""
    try:
        requests.get(f"{base}/api/version", timeout=2)
        return True
    except Exception:
        return False


def _model_present(base, model):
    """True if `model` is already pulled into Ollama (skip re-download)."""
    try:
        r = requests.get(f"{base}/api/tags", timeout=5)
        names = [m.get("name", "") for m in (r.json().get("models") or [])]
        return model in names
    except Exception:
        return False


def _do_model_pull(model, base):
    """Pull `model` into Ollama, streaming progress into _model_pull_state.

    Logs start / ~10%-milestones / done / error to coyote_ui_*.log — the earlier
    worker was silent, which is exactly why a fresh-install pull failure was
    invisible until Ollama's own container log was read by hand.
    """
    _set_pull_state("pulling", "starting")
    logger.info("Model-ensure: pulling '%s' from %s", model, base)
    try:
        # `timeout` is a per-read timeout, NOT a total cap: a legitimate
        # multi-minute 2 GB pull keeps bytes flowing, so it only fires on a
        # genuine stall.
        with requests.post(f"{base}/api/pull", json={"name": model},
                           stream=True, timeout=(10, 120)) as r:
            r.raise_for_status()
            last_logged = -10
            for line in r.iter_lines(decode_unicode=True):
                if not line:            # skip blank keep-alive lines
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:      # a non-JSON keep-alive: ignore, don't error
                    continue
                if obj.get("error"):
                    _set_pull_state("error", str(obj["error"]), model=model)
                    logger.error("Model-ensure: pull of '%s' failed: %s", model, obj["error"])
                    return
                status = obj.get("status", "")
                # total/completed appear ONLY on layer-download lines; status
                # lines like "pulling manifest"/"verifying sha256 digest" have
                # neither, so both are treated as optional.
                total, completed = obj.get("total"), obj.get("completed")
                if total and completed is not None:
                    pct = int(completed * 100 / total)
                    _set_pull_state("pulling", f"{status} {pct}%")
                    if pct >= last_logged + 10:   # log every ~10%
                        logger.info("Model-ensure: %s %d%%", status, pct)
                        last_logged = pct
                else:
                    _set_pull_state("pulling", status or "working")
        _set_pull_state("done", "model ready")
        logger.info("Model-ensure: '%s' ready", model)
    except Exception as e:
        _set_pull_state("error", str(e), model=model)
        logger.exception("Model-ensure: pull of '%s' errored", model)


def _model_ensure_tick():
    """One ensure iteration (extracted so tests can drive it without the loop)."""
    model = _parse_env_file().get("LLM", "qwen2.5-coder:3b")   # re-read each tick
    base = _ollama_base()
    with _model_pull_lock:
        # Keep the model reported by /api/llm/pull-status current — it is a forensic
        # field (which model the daemon is ensuring); nothing else writes it.
        _model_pull_state["model"] = model
    if not (model and _ollama_reachable(base)):
        return  # llm profile not up yet (or no model configured) — nothing to do
    if _model_present(base, model):
        # Model is present by any route (daemon pull, or a manual `ollama pull`).
        # Flip out of a stale idle/error/pulling so the UI stops showing a
        # download-in-progress / failed message while Chat actually works.
        if _model_pull_state.get("status") != "done":
            _set_pull_state("done", "model ready")
        return
    # Model absent: retry, but don't hammer /api/pull + the log every tick after a
    # recent failure of THIS model (permanent failures — bad model name, no route
    # to the registry, disk full — would otherwise spam indefinitely).
    if (_model_pull_state.get("status") == "error"
            and _model_pull_state.get("error_model") == model
            and time.time() - _model_pull_state.get("error_at", 0) < _MODEL_ENSURE_ERROR_BACKOFF):
        return
    _do_model_pull(model, base)


def _model_ensure_loop():
    """Daemon body: ensure the configured model is present whenever Ollama is up.

    One loop thread, so only one pull runs at a time (the pull blocks the tick).
    Started from __main__ (not at import) so importing this module for tests does
    not spawn a network-polling thread.
    """
    logger.info("Model-ensure daemon started (interval=%ss)", _MODEL_ENSURE_INTERVAL)
    while True:
        try:
            _model_ensure_tick()
        except Exception:
            logger.exception("Model-ensure loop tick failed")
        time.sleep(_MODEL_ENSURE_INTERVAL)


@app.route('/api/llm/pull-status', methods=['GET'])
def llm_pull_status():
    """Current background model-pull state, polled by the System Status panel."""
    with _model_pull_lock:
        return jsonify(dict(_model_pull_state))


@app.route('/api/start-core', methods=['POST'])
def start_core():
    """Start core services (Neo4j + Coyote Core)"""
    logger.info("Starting core services...")
    try:
        if not COMPOSE_FILE.exists():
            error_msg = f'Compose file not found: {COMPOSE_FILE}'
            logger.error(error_msg)
            return jsonify({'status': 'error', 'message': error_msg}), 404
        
        result = run_compose_command(
            ['--profile', 'core', 'up', '-d', '--pull=missing'],
            timeout=180
        )
        
        if result.returncode == 0:
            logger.info("Core services started successfully")
            message = 'Core services starting'
        else:
            logger.error(f"Failed to start core services. Return code: {result.returncode}")
            tail = _compose_error_tail(result)
            message = f'Failed to start core services.\n{tail}' if tail else \
                'Failed to start core services — see the Coyote UI log for details.'
        
        return jsonify({
            'status': 'success' if result.returncode == 0 else 'error',
            'message': message,
            'stdout': result.stdout,
            'stderr': result.stderr,
            'returncode': result.returncode
        })
        
    except subprocess.TimeoutExpired:
        # A cold first build can outrun the timeout while docker keeps building in
        # the background — the containers still come up. Surface a calm message
        # instead of a raw 500/traceback (the status poll reflects real state).
        logger.warning("start-core: compose up timed out; build likely still running in background")
        return jsonify({'status': 'success',
                        'message': 'Core services are still starting — the first build can take '
                                   '10–30+ minutes. Watch System Status; they will come Online when ready.'}), 200
    except DockerUnavailable as e:
        return jsonify({'status': 'error', 'message': e.user_message}), 503
    except Exception as e:
        logger.error(f"Error starting core: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/start-all', methods=['POST'])
def start_all():
    """Start all services (Core + LLM + Agent)"""
    logger.info("Starting all services...")
    try:
        if not COMPOSE_FILE.exists():
            error_msg = f'Compose file not found: {COMPOSE_FILE}'
            logger.error(error_msg)
            return jsonify({'status': 'error', 'message': error_msg}), 404
        
        result = run_compose_command(
            ['--profile', 'core', '--profile', 'llm', '--profile', 'agent', 
             'up', '-d', '--pull=missing'],
            timeout=420
        )
        
        if result.returncode == 0:
            logger.info("All services started successfully")
            message = 'All services starting'
        else:
            logger.error(f"Failed to start all services. Return code: {result.returncode}")
            tail = _compose_error_tail(result)
            message = f'Failed to start all services.\n{tail}' if tail else \
                'Failed to start all services — see the Coyote UI log for details.'

        return jsonify({
            'status': 'success' if result.returncode == 0 else 'error',
            'message': message,
            'stdout': result.stdout,
            'stderr': result.stderr,
            'returncode': result.returncode
        })

    except subprocess.TimeoutExpired:
        # Cold first build (image build + 5.6 GB ollama image pull) can outrun the
        # timeout while docker keeps building in the background — the containers
        # still come up. The model-ensure daemon fetches the LLM once Ollama is up.
        logger.warning("start-all: compose up timed out; build likely still running in background")
        return jsonify({'status': 'success',
                        'message': 'Services are still starting — the first build can take 10–30+ minutes. '
                                   'The language model downloads automatically in the background once Ollama '
                                   'is up; watch System Status.'}), 200
    except DockerUnavailable as e:
        return jsonify({'status': 'error', 'message': e.user_message}), 503
    except Exception as e:
        logger.error(f"Error starting all: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/start-llm', methods=['POST'])
def start_llm():
    """Start just the LLM profile (Ollama). The model itself is fetched by the
    background model-ensure daemon once Ollama is reachable."""
    logger.info("Starting LLM services...")
    try:
        if not COMPOSE_FILE.exists():
            error_msg = f'Compose file not found: {COMPOSE_FILE}'
            logger.error(error_msg)
            return jsonify({'status': 'error', 'message': error_msg}), 404

        result = run_compose_command(
            ['--profile', 'llm', 'up', '-d', '--pull=missing'],
            timeout=240
        )

        if result.returncode == 0:
            message = 'LLM services starting'
        else:
            logger.error(f"Failed to start LLM services. Return code: {result.returncode}")
            tail = _compose_error_tail(result)
            message = f'LLM services failed to start.\n{tail}' if tail else \
                'LLM services failed to start — see the Coyote UI log for details.'

        return jsonify({
            'status': 'success' if result.returncode == 0 else 'error',
            'message': message,
            'stdout': result.stdout,
            'stderr': result.stderr,
            'returncode': result.returncode
        })
    except subprocess.TimeoutExpired:
        # Cold-pulling the 5.6 GB ollama image can outrun the timeout on a slow
        # connection while docker keeps pulling in the background.
        logger.warning("start-llm: compose up timed out; image pull likely still running in background")
        return jsonify({'status': 'success',
                        'message': 'The LLM service is still starting — Ollama\'s image download can take a few '
                                   'minutes on a slow connection. The model then downloads automatically; '
                                   'watch System Status.'}), 200
    except DockerUnavailable as e:
        return jsonify({'status': 'error', 'message': e.user_message}), 503
    except Exception as e:
        logger.error(f"Error starting LLM: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/stop', methods=['POST'])
def stop_services():
    """Stop all Docker services with verification"""
    logger.info("Stopping all services...")
    try:
        if not COMPOSE_FILE.exists():
            error_msg = f'Compose file not found: {COMPOSE_FILE}'
            logger.error(error_msg)
            return jsonify({'status': 'error', 'message': error_msg}), 404
        
        # First try graceful stop with compose down
        result = run_compose_command(['down', '--remove-orphans'], timeout=120)
        
        # CRITICAL: Verify containers actually stopped
        # Docker Compose sometimes returns success even when containers are still running
        import time
        time.sleep(2)  # Give containers a moment to stop
        
        # Check if any containers are still running
        check_result = run_compose_command(['ps', '-q'], timeout=5)
        containers_still_running = bool(check_result.stdout and check_result.stdout.strip())
        
        if containers_still_running:
            logger.warning("Compose down reported success but containers still running! Using force cleanup...")
            
            # Get container names for force cleanup
            list_cmd = [DOCKER_BIN, 'ps', '-a', '--filter', f'label=com.docker.compose.project={PROJECT_NAME}', '--format', '{{.Names}}']
            list_result = _docker_run(list_cmd, capture_output=True, text=True, timeout=5)

            if list_result.returncode == 0 and list_result.stdout:
                containers = list_result.stdout.strip().split('\n')
                logger.info(f"Force stopping containers: {containers}")

                for container in containers:
                    if container:
                        # Force stop with short timeout
                        stop_cmd = [DOCKER_BIN, 'stop', '-t', '5', container]
                        stop_result = _docker_run(stop_cmd, capture_output=True, text=True, timeout=10)

                        if stop_result.returncode != 0:
                            # If stop fails, kill it
                            kill_cmd = [DOCKER_BIN, 'kill', container]
                            _docker_run(kill_cmd, capture_output=True, text=True, timeout=5)

                        # Force remove
                        rm_cmd = [DOCKER_BIN, 'rm', '-f', container]
                        _docker_run(rm_cmd, capture_output=True, text=True, timeout=5)
                        
                        logger.info(f"Force stopped container: {container}")
                
                message = 'Services stopped (force cleanup was required)'
                status = 'success'
            else:
                message = 'Containers may still be running - use Force Cleanup button'
                status = 'partial'
        else:
            # Containers stopped normally
            logger.info("Services stopped successfully")
            message = 'Services stopped successfully'
            status = 'success'
        
        return jsonify({
            'status': status,
            'message': message,
            'stdout': result.stdout,
            'stderr': result.stderr
        })
        
    except DockerUnavailable as e:
        return jsonify({'status': 'error', 'message': e.user_message}), 503
    except Exception as e:
        logger.error(f"Error stopping services: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/force-cleanup', methods=['POST'])
def force_cleanup():
    """Force cleanup of all Coyote containers"""
    logger.info("Force cleanup of all Coyote containers...")
    try:
        # Get all containers with the project name
        list_cmd = [DOCKER_BIN, 'ps', '-a', '--filter', f'label=com.docker.compose.project={PROJECT_NAME}', '--format', '{{.Names}}']
        list_result = _docker_run(list_cmd, capture_output=True, text=True)
        
        if list_result.returncode == 0 and list_result.stdout:
            containers = list_result.stdout.strip().split('\n')
            logger.info(f"Found containers to clean up: {containers}")
            
            for container in containers:
                if container:
                    # Force stop
                    stop_cmd = [DOCKER_BIN, 'stop', '-t', '5', container]
                    _docker_run(stop_cmd, capture_output=True, text=True)

                    # Force remove
                    rm_cmd = [DOCKER_BIN, 'rm', '-f', container]
                    _docker_run(rm_cmd, capture_output=True, text=True)
                    
                    logger.info(f"Cleaned up container: {container}")
            
            return jsonify({
                'status': 'success',
                'message': f'Cleaned up {len(containers)} containers',
                'containers': containers
            })
        else:
            return jsonify({
                'status': 'success',
                'message': 'No containers to clean up'
            })
            
    except DockerUnavailable as e:
        return jsonify({'status': 'error', 'message': e.user_message}), 503
    except Exception as e:
        logger.error(f"Error in force cleanup: {e}", exc_info=True)
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/status', methods=['GET'])
def get_status():
    """Get status of all containers"""
    try:
        # Check if Docker is available (missing binary -> DockerUnavailable below).
        # Engine-down is caught later when `compose ps` reaches the daemon.
        docker_check = _docker_run(
            [DOCKER_BIN, '--version'],
            capture_output=True,
            text=True,
            timeout=5
        )

        if docker_check.returncode != 0:
            logger.warning("Docker not available")
            return jsonify({
                'status': 'error',
                'message': DOCKER_MISSING_MSG,
                'services': []
            }), 503
        
        if not COMPOSE_FILE.exists():
            logger.warning(f"Compose file not found: {COMPOSE_FILE}")
            return jsonify({
                'status': 'error',
                'message': 'Compose file not found',
                'services': []
            })
        
        # Get container status
        result = run_compose_command(['ps', '--format', 'json'], timeout=10)
        
        if result.returncode == 0 and result.stdout:
            try:
                # Parse the JSON output
                if result.stdout.strip().startswith('['):
                    services = json.loads(result.stdout)
                else:
                    # Line-by-line JSON
                    services = []
                    for line in result.stdout.strip().split('\n'):
                        if line:
                            try:
                                services.append(json.loads(line))
                            except json.JSONDecodeError:
                                logger.warning(f"Could not parse line: {line}")
                
                logger.debug(f"Found {len(services)} services")
                return jsonify({
                    'status': 'success',
                    'services': services,
                    'project': PROJECT_NAME
                })
            except json.JSONDecodeError as e:
                logger.error(f"JSON decode error: {e}")
                return jsonify({
                    'status': 'success',
                    'services': [],
                    'raw': result.stdout
                })
        else:
            return jsonify({
                'status': 'success',
                'services': [],
                'message': 'No services running'
            })
            
    except DockerUnavailable as e:
        return jsonify({
            'status': 'error',
            'message': e.user_message,
            'services': []
        }), 503
    except Exception as e:
        logger.error(f"Error getting status: {e}", exc_info=True)
        return jsonify({
            'status': 'error',
            'message': str(e),
            'services': []
        })

@app.route('/api/health-check/<service_name>', methods=['GET'])
def health_check(service_name):
    """Check if a specific service is healthy"""
    try:
        result = run_compose_command(['ps', '--format', 'json', service_name], timeout=5)
        
        if result.returncode == 0 and result.stdout:
            service_info = json.loads(result.stdout.strip())
            if isinstance(service_info, list) and len(service_info) > 0:
                service_info = service_info[0]
            
            health = service_info.get('Health', 'unknown')
            status = service_info.get('Status', 'unknown')
            
            return jsonify({
                'service': service_name,
                'health': health,
                'status': status,
                'running': 'Up' in status
            })
        else:
            return jsonify({
                'service': service_name,
                'health': 'not found',
                'status': 'not running',
                'running': False
            })
            
    except Exception as e:
        logger.error(f"Error checking health for {service_name}: {e}")
        return jsonify({
            'service': service_name,
            'health': 'error',
            'status': str(e),
            'running': False
        })

@app.route('/api/logs/<service_name>', methods=['GET'])
def get_service_logs(service_name):
    """Get logs for a specific service"""
    try:
        result = run_compose_command(['logs', '--tail=100', service_name], timeout=10)
        
        return jsonify({
            'status': 'success',
            'logs': result.stdout,
            'stderr': result.stderr
        })
        
    except Exception as e:
        logger.error(f"Error getting logs for {service_name}: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/api/neo4j-browser-url', methods=['GET'])
def neo4j_browser_url():
    env = _parse_env_file()
    port = env.get('NEO4J_HTTP_PORT', '7474')
    return jsonify({"url": f"http://localhost:{port}"})

@app.route('/api/bot-url', methods=['GET'])
def bot_url():
    env = _parse_env_file()
    port = env.get('BOT_PORT', '8501')   # same default you use in .env
    # Streamlit supports ?embed=true to reduce outer chrome
    return jsonify({"url": f"http://localhost:{port}/?embed=true"})

@app.route('/api/search/init', methods=['POST'])
def api_search_init():
    """
    MVP: DISABLED. The Coyote Search box is hidden for the MVP because it minted
    Purpose/SearchTerms even when the extension was paused or in a private window
    (privacy-expectation violation — see Known Issue). This endpoint no longer
    forwards to Core, so a direct POST (e.g. a bookmarked /static/new_tab.html)
    records nothing. Restore post-MVP behind a shared pause/consent gate the UI
    server can honor.

    RESTORE: delete the two lines below (the log + the 410 return). The original
    forwarding behavior is preserved intact underneath and becomes reachable again.
    """
    logger.info("Search init received but DISABLED for MVP; nothing recorded.")
    return jsonify({"ok": False, "disabled": True}), 410

    # --- ORIGINAL BEHAVIOR (preserved for post-MVP restore; unreachable while disabled) ---
    try:
        payload = flask_request.get_json(silent=True) or {}
        data = {
            "timestamp": payload.get("timestamp") or datetime.utcnow().isoformat(),
            "event": "User starts or modifies a search",
            "purpose": (payload.get("purpose") or "").strip(),
            "searchTerms": (payload.get("searchTerms") or "").strip(),
        }

        forwarded = False
        try:
            req = urllib.request.Request(
                "http://127.0.0.1:5000/init_search",
                data=json.dumps(data).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=2.5) as resp:
                _ = resp.read()
            forwarded = True
        except Exception:
            # Core may be down; that's fine for COY-20 acceptance
            forwarded = False

        logger.info("Search init (forwarded=%s): purpose=%r terms=%r",
                    forwarded, data["purpose"], data["searchTerms"])
        return jsonify({"ok": True, "forwarded": forwarded})
    except Exception as e:
        logger.exception("api_search_init failed")
        return jsonify({"ok": False, "message": str(e)}), 400

def _neo4j_http_base_and_auth_override(user: str, pwd: str):
    """HTTP base URL from .env port, but build Basic auth from provided user/pwd."""
    env = _parse_env_file()
    http_port = env.get('NEO4J_HTTP_PORT', '7474')
    base = f"http://localhost:{http_port}"
    auth = base64.b64encode(f"{user}:{pwd}".encode('utf-8')).decode('ascii')
    return base, auth

@app.route('/api/config/test-neo4j', methods=['POST'])
def api_config_test_neo4j():
    """
    Test credentials against Neo4j's HTTP endpoint.
    Does not persist anything.
    """
    try:
        payload = flask_request.get_json(silent=True) or {}
        user = (payload.get('user') or 'neo4j').strip()
        pwd  = (payload.get('pass') or '').strip()
        if not user or not pwd:
            return jsonify({"ok": False, "message": "Username and password are required."}), 400
        base, auth = _neo4j_http_base_and_auth_override(user, pwd)
        url  = f"{base}/db/neo4j/tx/commit"
        body = json.dumps({"statements":[{"statement":"RETURN 1 AS ok"}]}).encode('utf-8')
        req = urllib.request.Request(url, data=body, method="POST",
            headers={"Content-Type":"application/json", "Authorization": f"Basic {auth}"})
        with urllib.request.urlopen(req, timeout=120.0) as resp:
            _ = resp.read()
        return jsonify({"ok": True, "message": "Neo4j connection succeeded."})
    except urllib.error.HTTPError as e:
        if e.code == 401:
            return jsonify({"ok": False, "message": "Unauthorized: username/password rejected by Neo4j."}), 401
        logger.exception("Neo4j test HTTPError")
        return jsonify({"ok": False, "message": f"Neo4j test failed: HTTP {e.code}"}), 400
    except Exception as e:
        logger.exception("Neo4j test failed")
        return jsonify({"ok": False, "message": f"Neo4j test failed: {e}"}), 400

def _get_core_container_name() -> str | None:
    """
    Use `docker compose ps --format json coyote_app` to resolve the running container name.
    Falls back to common default if parsing fails.
    """
    try:
        res = run_compose_command(['ps', '--format', 'json', 'coyote_app'], timeout=5)
        if res.returncode == 0 and res.stdout:
            try:
                # output may be one JSON object or one-per-line
                data = json.loads(res.stdout.strip()) if res.stdout.strip().startswith('[') else [json.loads(res.stdout)]
                if data:
                    name = (data[0].get('Name') or data[0].get('Names') or '').strip()
                    return name or None
            except json.JSONDecodeError:
                pass
    except Exception:
        pass
    # Fallback that matches the logs you shared
    return "coyote-coyote_app-1"

def _apply_creds_inside_core(uri_container: str, user: str, pwd: str) -> bool:
    """
    Best-effort: write settings into Core's state DB using its own encryption path.
    """
    name = _get_core_container_name()
    if not name:
        return False
    py = f"""
from coyote.utils.config_manager import store_setting
store_setting('neo4j_uri', {json.dumps(uri_container)}, encrypt=False)
store_setting('neo4j_username', {json.dumps(user)}, encrypt=False)
store_setting('neo4j_password', {json.dumps(pwd)}, encrypt=True)
print('saved')
"""
    try:
        cp = subprocess.run(
            [DOCKER_BIN, 'exec', '-i', name, 'python', '-'],
            input=py.encode('utf-8'),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10
        )
        if cp.returncode == 0 and (cp.stdout.decode(errors='ignore').strip().endswith('saved')):
            return True
        logger.info("Apply creds inside core returned code %s; stdout=%r stderr=%r",
                    cp.returncode, cp.stdout.decode(errors='ignore'), cp.stderr.decode(errors='ignore'))
        return False
    except Exception:
        logger.info("Core not running or exec failed:\n%s", traceback.format_exc())
        return False
    

@app.route('/api/config/save-neo4j', methods=['POST'])
def api_config_save_neo4j():
    """
    Persist creds to compose/.env (plaintext .env, chmod 600) and
    best-effort apply into Core's state DB (with encrypted password).
    """
    try:
        p = flask_request.get_json(silent=True) or {}
        user = (p.get('user') or 'neo4j').strip()
        pwd  = (p.get('pass') or '').strip()
        uri_host = (p.get('uri')  or 'bolt://localhost:7687').strip()
        if not user or not pwd:
            return jsonify({"ok": False, "message": "Username and password are required."}), 400
        # container-internal URI is stable in your compose (service name: database)
        uri_container = 'bolt://database:7687'

        # 1) Write compose/.env
        _write_env({
            "NEO4J_USERNAME": user,
            "NEO4J_PASSWORD": pwd,
            "NEO4J_AUTH": f"{user}/{pwd}",
            "NEO4J_URI": uri_container,              # used by Core/compose defaults
            "COYOTE_NEO4J_URI_HOST": uri_host        # optional: what user typed (for display)
        })

        # 2) Best-effort apply into Core now (so password gets encrypted in state DB)
        applied = _apply_creds_inside_core(uri_container, user, pwd)
        msg = "Saved. " + ("Applied to running Core." if applied else "Start/Restart Core to take effect inside app.")
        return jsonify({"ok": True, "applied": applied, "message": msg})
    except Exception as e:
        logger.exception("save-neo4j failed")
        return jsonify({"ok": False, "message": f"Failed to save: {e}"}), 500

@app.route('/api/config/status', methods=['GET'])
def api_config_status():
    """Return saved credential state. Never exposes secret values."""
    env = _parse_env_file()
    neo4j_configured = bool(env.get('NEO4J_PASSWORD', '').strip())
    hyp_configured = bool(env.get('HYPOTHESIS_TOKEN', '').strip())
    return jsonify({
        'neo4j': {
            'configured': neo4j_configured,
            'uri': env.get('COYOTE_NEO4J_URI_HOST',
                           env.get('NEO4J_URI', 'bolt://localhost:7687')),
            'username': env.get('NEO4J_USERNAME', 'neo4j')
        },
        'hypothesis': {
            'configured': hyp_configured,
            'username': env.get('HYPOTHESIS_USERNAME', '')
        }
    })

# --- ADD: generic values runner (keeps _run_cypher_http intact) ----------------
def _run_values_http(statement: str, parameters: dict | None = None, timeout=6.0):
    """
    Run a read-only Cypher and return list[dict] rows (columns -> values).
    """
    base, auth = _neo4j_http_info()
    url = f"{base}/db/neo4j/tx/commit"
    body = json.dumps({"statements": [{"statement": statement, "parameters": parameters or {}}]}).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Basic {auth}"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if data.get("errors"):
        raise RuntimeError(data["errors"][0].get("message", "Cypher error"))
    res = data.get("results", [])
    if not res:
        return []
    cols = res[0].get("columns", [])
    rows = []
    for r in res[0].get("data", []):
        arr = r.get("row", [])
        rows.append({cols[i]: arr[i] for i in range(min(len(cols), len(arr)))})
    return rows

# --- ADD: /api/insights/new-topics --------------------------------------------
@app.route('/api/insights/new-topics', methods=['GET'])
def api_insights_new_topics():
    """
    New topics whose first connection (Webpage/Annotation) occurred in the last N days.
    Falls back to interaction counts when active_seconds is absent.
    """
    try:
        days  = int(flask_request.args.get('days', '7'))
        limit = int(flask_request.args.get('limit', '12'))
        cypher = """
        MATCH (n)-[:HAS_TOPIC]->(t:WikiDataOntology)
        WHERE (n:Webpage OR n:Annotation) AND n.timestamp IS NOT NULL
        WITH t, datetime(n.timestamp) AS ts,
             CASE WHEN n:Webpage THEN coalesce(n.active_seconds, 0) ELSE 0 END AS secs,
             1 AS interaction
        WITH t, min(ts) AS first_ts, sum(secs) AS active_seconds, sum(interaction) AS interactions
        WHERE first_ts >= datetime() - duration({days: $days})
        RETURN t.label AS topic, toString(date(first_ts)) AS first_seen,
               active_seconds, interactions
        ORDER BY interactions DESC
        LIMIT $limit
        """
        rows = _run_values_http(cypher, {"days": days, "limit": limit})
        return jsonify({"ok": True, "data": rows})
    except Exception as e:
        logger.exception("/api/insights/new-topics failed")
        return jsonify({"ok": False, "message": str(e)}), 500

# --- ADD: /api/insights/searches -----------------------------------------------
@app.route('/api/insights/searches', methods=['GET'])
def api_insights_searches():
    """Recent search terms with frequency counts."""
    try:
        days  = int(flask_request.args.get('days', '7'))
        limit = int(flask_request.args.get('limit', '12'))
        cypher = """
        MATCH (p:Purpose)-[:INITIATES_SEARCH]->(s:SearchTerms)
        WHERE p.timestamp IS NOT NULL
          AND datetime(p.timestamp) >= datetime() - duration({days: $days})
        RETURN s.text AS term, count(*) AS frequency,
               toString(date(max(datetime(p.timestamp)))) AS last_used
        ORDER BY frequency DESC, last_used DESC
        LIMIT $limit
        """
        rows = _run_values_http(cypher, {"days": days, "limit": limit})
        return jsonify({"ok": True, "data": rows})
    except Exception as e:
        logger.exception("/api/insights/searches failed")
        return jsonify({"ok": False, "message": str(e)}), 500

# --- ADD: /api/insights/sensemaking-rate --------------------------------------
@app.route('/api/insights/sensemaking-rate', methods=['GET'])
def api_insights_sensemaking_rate():
    """
    By day for last N days: SERP count vs annotations within +window_min of SERP timestamp.
    Requires Webpage.isSERP = true and Webpage->HAS_ANNOTATION->Annotation.
    """
    try:
        days = int(flask_request.args.get('days', '30'))
        window_min = int(flask_request.args.get('window', '30'))
        cypher = """
        MATCH (serp:Webpage)
        WHERE serp.isSERP = true
          AND serp.timestamp IS NOT NULL
          AND datetime(serp.timestamp) >= datetime() - duration({days: $days})
        OPTIONAL MATCH (serp)-[:LINKS_TO]->(p:Webpage)
        OPTIONAL MATCH (p)-[:HAS_ANNOTATION]->(a:Annotation)
        WHERE a.timestamp IS NOT NULL
          AND datetime(a.timestamp) <= datetime(serp.timestamp) + duration({minutes: $window_min})
        WITH date(datetime(serp.timestamp)) AS d, count(DISTINCT serp) AS searches, count(DISTINCT a) AS annos
        RETURN toString(d) AS d, searches, annos,
               (CASE WHEN searches = 0 THEN 0.0 ELSE toFloat(annos)/toFloat(searches) END) AS rate
        ORDER BY d ASC
        """
        rows = _run_values_http(cypher, {"days": days, "window_min": window_min})
        return jsonify({"ok": True, "data": rows})
    except Exception as e:
        logger.exception("/api/insights/sensemaking-rate failed")
        return jsonify({"ok": False, "message": str(e)}), 500

# --- ADD: /api/insights/rhythms -----------------------------------------------
@app.route('/api/insights/rhythms', methods=['GET'])
def api_insights_rhythms():
    """
    Hour-of-day activity (sum weight) over last N days.
    Uses active_seconds if present; otherwise weight=1 per Webpage.
    """
    try:
        days = int(flask_request.args.get('days', '7'))
        cypher = """
        MATCH (w:Webpage)
        WHERE w.timestamp IS NOT NULL
          AND datetime(w.timestamp) >= datetime() - duration({days: $days})
        WITH datetime(w.timestamp) AS dt, coalesce(w.active_seconds, 1) AS weight
        WITH dt.hour AS hour, sum(weight) AS value
        RETURN hour, value
        ORDER BY hour ASC
        """
        rows = _run_values_http(cypher, {"days": days})
        return jsonify({"ok": True, "data": rows})
    except Exception as e:
        logger.exception("/api/insights/rhythms failed")
        return jsonify({"ok": False, "message": str(e)}), 500


@app.route('/api/graph/recent', methods=['GET'])
def graph_recent():
    seed = int(flask_request.args.get('seedLimit', 60))
    node_limit = int(flask_request.args.get('nodeLimit', 120))
    rel_limit  = int(flask_request.args.get('relLimit', 240))
    cypher = """
CALL {
  MATCH (n)
  WHERE (n:Webpage OR n:Annotation OR n:Purpose OR n:SearchTerms)
    AND n.timestamp IS NOT NULL
  RETURN n
  ORDER BY datetime(n.timestamp) DESC
  LIMIT $seedLimit
}
WITH collect(n) AS seeds
UNWIND seeds AS s
OPTIONAL MATCH (s)-[r]-(m)
WITH collect(DISTINCT s) + collect(DISTINCT m) AS allNodes, collect(DISTINCT r) AS rels
WITH allNodes[..$nodeLimit] AS nodes, rels[..$relLimit] AS rs
RETURN
  [x IN nodes | {id:id(x), labels:labels(x), props:properties(x)}] AS nodes,
  [x IN rs    | {id:id(x), type:type(x), s:id(startNode(x)), t:id(endNode(x)), props:properties(x)}] AS rels
"""
    try:
        row = _run_cypher_http(cypher, {"seedLimit": seed, "nodeLimit": node_limit, "relLimit": rel_limit})
        counts = _to_cytoscape_counts(row)
        return jsonify({"status":"success", **row, "counts": counts})
    except Exception as e:
        logger.error(f"/api/graph/recent error: {e}")
        return jsonify({"status":"error","message":str(e)}), 500


def _validate_and_execute(cypher: str, nl: str, params: dict) -> dict:
    """Guards + execute pipeline. Raises ValueError or RuntimeError on failure."""
    _sanitize_readonly(cypher)
    if nl and not _has_nodes_rels_return(cypher):
        raise ValueError("Shape guard: NL→Cypher must RETURN both `nodes` and `rels` for visualization.")
    _schema_gate(cypher)
    _explain_http(cypher, params)
    return _run_cypher_http(cypher, params)


@app.route('/api/graph/run', methods=['POST'])
def graph_run():
    payload = flask_request.get_json(silent=True) or {}
    cypher = (payload.get('cypher') or "").strip()
    nl     = (payload.get('nl') or "").strip()
    params = payload.get('params') or {}
    if nl and not cypher:
        gen, diag = _nl_to_cypher(nl)
        if not gen:
            return jsonify({"status":"unavailable","message": diag or "LLM could not translate NL→Cypher"}), 200
        cypher = gen
    if not cypher:
        return jsonify({"status":"error","message":"No query provided"}), 400

    retried = False
    try:
        try:
            row = _validate_and_execute(cypher, nl, params)
        except (ValueError, RuntimeError) as first_err:
            if not nl:
                raise  # manual Cypher — no retry
            # Single retry with error correction for NL-generated queries
            logger.info("NL→Cypher attempt 1 failed (%s): %s", type(first_err).__name__, str(first_err)[:200])
            logger.info("NL→Cypher attempt 1 Cypher: %s", cypher[:300])
            gen2, diag2 = _nl_to_cypher(nl, prior_error=str(first_err))
            if not gen2:
                raise  # retry generation failed — surface original error
            logger.info("NL→Cypher attempt 2 Cypher: %s", gen2)
            row = _validate_and_execute(gen2, nl, params)
            cypher = gen2
            retried = True
            logger.info("NL→Cypher attempt 2 succeeded")

        counts = _to_cytoscape_counts(row if isinstance(row, dict) else {"nodes":[],"rels":[]})
        result = {"status":"success", **row, "counts": counts}
        if retried:
            result["retried"] = True
        return jsonify(result)
    except ValueError as ve:
        return jsonify({"status":"error","message":str(ve)}), 400
    except Exception as e:
        logger.error(f"/api/graph/run error: {e}")
        return jsonify({"status":"error","message":str(e)}), 500

def _apply_hypothesis_creds_inside_core(username: str, token: str) -> bool:
    """Write Hypothes.is creds into Core's state DB via its config_manager (encrypt token)."""
    name = _get_core_container_name()
    if not name:
        return False
    py = f"""
from coyote.utils.config_manager import store_setting
store_setting('hypothesis_username', {json.dumps(username)}, encrypt=False)
store_setting('hypothesis_token', {json.dumps(token)}, encrypt=True)
print('saved')
"""
    try:
        cp = subprocess.run(
            [DOCKER_BIN, 'exec', '-i', name, 'python', '-'],
            input=py.encode('utf-8'),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10
        )
        return cp.returncode == 0 and cp.stdout.decode(errors='ignore').strip().endswith('saved')
    except Exception:
        logger.info("Apply Hypothes.is creds inside core failed:\n%s", traceback.format_exc())
        return False

@app.route('/api/integrations/hypothesis/test', methods=['POST'])
def api_hypothesis_test():
    p = flask_request.get_json(silent=True) or {}
    token = (p.get('token') or '').strip()
    if not token:
        return jsonify({"ok": False, "message": "API token required."}), 400
    try:
        req = urllib.request.Request(
            "https://api.hypothes.is/api/profile",
            headers={"Authorization": f"Bearer {token}"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            prof = json.loads(resp.read().decode('utf-8'))
        return jsonify({"ok": True, "userid": prof.get('userid')})
    except urllib.error.HTTPError as e:
        if e.code == 401:
            return jsonify({"ok": False, "message": "Unauthorized: token rejected by Hypothes.is."}), 401
        return jsonify({"ok": False, "message": f"Hypothes.is error: HTTP {e.code}"}), 400
    except Exception as e:
        logger.exception("Hypothes.is test failed")
        return jsonify({"ok": False, "message": f"Test failed: {e}"}), 400

@app.route('/api/integrations/hypothesis/save', methods=['POST'])
def api_hypothesis_save():
    p = flask_request.get_json(silent=True) or {}
    username = (p.get('username') or '').strip()
    token    = (p.get('token') or '').strip()
    if not token:
        return jsonify({"ok": False, "message": "Token required."}), 400

    _write_env({
        "HYPOTHESIS_USERNAME": username,
        "HYPOTHESIS_TOKEN": token
    })
    applied = _apply_hypothesis_creds_inside_core(username, token)
    msg = "Saved. " + ("Applied to running Core." if applied else "Start/Restart Core to take effect inside app.")
    return jsonify({"ok": True, "applied": applied, "message": msg})

@app.route('/api/integrations/hypothesis/fetch', methods=['POST'])
def api_hypothesis_fetch():
    """Forward a fetch request to Core if available; otherwise succeed with a helpful message."""
    try:
        req = urllib.request.Request(
            "http://127.0.0.1:5000/hypothesis/fetch",
            data=json.dumps({}).encode('utf-8'),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=3) as resp:
            _ = resp.read()
        return jsonify({"ok": True, "forwarded": True, "message": "Fetch started in Coyote Core."})
    except Exception:
        return jsonify({"ok": True, "forwarded": False, "message": "Core not reachable; request was not forwarded."})

def _neo4j_http_info():
    env = _parse_env_file()
    http_port = env.get('NEO4J_HTTP_PORT', '7474')
    user = env.get('NEO4J_USERNAME', 'neo4j')
    pwd  = env.get('NEO4J_PASSWORD', 'password')
    base = f"http://localhost:{http_port}"
    auth = base64.b64encode(f"{user}:{pwd}".encode('utf-8')).decode('ascii')
    return base, auth

def _run_cypher_http(statement:str, parameters:dict=None, timeout=6.0):
    base, auth = _neo4j_http_info()
    url  = f"{base}/db/neo4j/tx/commit"
    body = json.dumps({
        "statements": [{"statement": statement, "parameters": parameters or {}}]
    }).encode('utf-8')
    req = urllib.request.Request(url, data=body, method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Basic {auth}"
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            if data.get('errors'):
                raise RuntimeError(data['errors'][0].get('message','Cypher error'))
            results = data.get('results', [])
            if not results:
                return {"nodes": [], "rels": []}
            res = results[0]
            cols = res.get('columns', [])
            rows = res.get('data', [])
            if not rows:
                return {"nodes": [], "rels": []}
            row = rows[0].get('row', [])
            # Common case: RETURN nodes, rels
            if isinstance(row, list) and len(cols) >= 2 and cols[0].lower() == 'nodes' and cols[1].lower() == 'rels':
                return {"nodes": row[0] or [], "rels": row[1] or []}
            # If user returns a single map like {nodes:..., rels:...}
            if isinstance(row, list) and row and isinstance(row[0], dict):
                m = row[0]
                return {"nodes": m.get('nodes', []), "rels": m.get('rels', [])}
            if isinstance(row, dict):
                return {"nodes": row.get('nodes', []), "rels": row.get('rels', [])}
            return {"nodes": [], "rels": []}
    except urllib.error.URLError as e:
        raise RuntimeError(f"Neo4j HTTP unavailable: {e}")
    except Exception as e:
        raise

def _explain_http(statement: str, parameters: dict | None = None, timeout=6.0):
    """
    Preflight: run EXPLAIN to catch syntax/schema errors early and
    return Neo4j's error text verbatim on failure.
    """
    base, auth = _neo4j_http_info()
    url  = f"{base}/db/neo4j/tx/commit"
    body = json.dumps({
        "statements": [{"statement": "EXPLAIN " + statement, "parameters": parameters or {}}]
    }).encode('utf-8')
    req = urllib.request.Request(url, data=body, method="POST",
        headers={"Content-Type":"application/json","Authorization": f"Basic {auth}"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode('utf-8'))
    if data.get("errors"):
        # Return the message verbatim (UI will show this string)
        msg = data["errors"][0].get("message","Cypher error")
        raise RuntimeError(msg)
    return True

def _has_nodes_rels_return(cypher: str) -> bool:
    """
    Shape guard (for NL→Cypher only): require a RETURN that includes both nodes and rels.
    Accept either aliases "... AS nodes, ... AS rels" or a returned map with keys.
    """
    low = " ".join((cypher or "").lower().split())
    if re.search(r'\bas\s+nodes\b', low) and re.search(r'\bas\s+rels\b', low):
        return True
    if re.search(r'\breturn\s+nodes\s*,\s*rels\b', low):
        return True
    # map-style return
    if "return" in low and ("{nodes" in low or "nodes:" in low) and ("{rels" in low or "rels:" in low):
        return True
    return False

def _schema_gate(cypher: str):
    """
    Ban unknown labels/relationship types/property names and any leftover
    StackOverflow demo artifacts. Fail fast with precise messages.
    """
    if _BANNED_ARTIFACTS_RE.search(cypher):
        raise ValueError("Schema gate: Query references banned artifacts (StackOverflow/Q&A demo).")

    # Extract labels in node patterns like (n:Label) or (:Label)
    labels = set(re.findall(r'\(\s*[A-Za-z_][A-Za-z0-9_]*?\s*:(?:\s*([A-Za-z_][A-Za-z0-9_]*))', cypher))
    # Extract relationship types like [:TYPE]
    rels   = set(re.findall(r'\[\s*[A-Za-z_][A-Za-z0-9_]*?\s*:\s*([A-Za-z_][A-Za-z0-9_]*)', cypher))
    # Extract property names var.prop (second group)
    props  = set(p for (_v, p) in re.findall(r'\b([A-Za-z_][A-Za-z0-9_]*)\.(\w+)\b', cypher))

    unknown_labels = {x for x in labels if x not in ALLOWED_LABELS}
    unknown_rels   = {x for x in rels   if x not in ALLOWED_REL_TYPES}
    unknown_props  = {x for x in props  if x not in ALLOWED_PROPS}

    errs = []
    if unknown_labels:
        errs.append(f"unknown label(s): {', '.join(sorted(unknown_labels))}")
    if unknown_rels:
        errs.append(f"unknown relationship type(s): {', '.join(sorted(unknown_rels))}")
    if unknown_props:
        errs.append(f"unknown property name(s): {', '.join(sorted(unknown_props))}")
    if errs:
        raise ValueError("Schema gate: " + "; ".join(errs))
    return True

def _sanitize_readonly(cypher:str):
    if not is_read_only(cypher):
        raise ValueError("Write/DDL operations are not allowed from the UI.")
    return cypher

def _to_cytoscape_counts(payload):
    nodes = payload.get('nodes') if isinstance(payload, dict) else []
    rels  = payload.get('rels')  if isinstance(payload, dict) else []
    return {'nodes': len(nodes or []), 'rels': len(rels or [])}

def _nl_to_cypher(nl_question:str, prior_error:str|None=None) -> tuple[str|None, str|None]:
    """NL→Cypher via local Ollama. Returns (cypher, error_message).
    If prior_error is set, appends a correction signal to the prompt (retry path)."""
    env = _parse_env_file()
    port = env.get('OLLAMA_PORT','11434')
    model = env.get('LLM','qwen2.5-coder:3b')
    url = f"http://localhost:{port}/api/generate"

    using_fallback = False
    try:
        schema = schema_for_prompts(None)
        prompt = prompt_text("graph").format(schema=schema, question=nl_question)
        logger.info("Using imported prompt from nl2cypher")
    except Exception as e:
        using_fallback = True
        logger.warning(f"Import failed, using fallback prompt: {e}")
        # Hard fallback: embed a working prompt if shared import ever fails
        # NOTE: Use DOUBLE braces {{  }} in f-string to produce SINGLE braces { } in output
        prompt = f"""You translate user questions into ONE read-only Cypher query over the schema below.

SCHEMA:
{SCHEMA_MIN}

CRITICAL RULES:
1. Use ONLY the listed labels/properties/relationships
2. For time ranges: datetime(node.timestamp) >= datetime() - duration({{days: N}})
3. For "today": date(datetime(node.timestamp)) = date()
4. ALWAYS collect nodes first with WITH:

MATCH <pattern>
WHERE <filters>
WITH collect(DISTINCT <node>) AS matchedNodes
RETURN
  [x IN matchedNodes | {{id:id(x), labels:labels(x), props:properties(x)}}] AS nodes,
  [] AS rels

EXAMPLE:
{{
  "cypher": "MATCH (w:Webpage {{isSERP: false}}) WHERE date(datetime(w.timestamp)) = date() WITH collect(DISTINCT w) AS matchedNodes RETURN [x IN matchedNodes | {{id:id(x), labels:labels(x), props:properties(x)}}] AS nodes, [] AS rels"
}}

Output STRICT JSON: {{"cypher":"<query>"}}

USER QUESTION: {nl_question}
"""

    if prior_error:
        err_truncated = prior_error[:200]
        prompt += f'\n\nCORRECTION REQUIRED: Your previous Cypher attempt failed with this error: "{err_truncated}". Do not repeat this pattern. Generate a corrected query.'

    logger.info(f"Prompt source: {'FALLBACK' if using_fallback else 'IMPORTED'}{' (RETRY)' if prior_error else ''}")
    logger.debug(f"Prompt preview: {prompt[:500]}...")
    
    payload = json.dumps({
        "model": model,
        "prompt": prompt,
        "stream": False,
        "keep_alive": "10m",
        "options": {"temperature": 0, "num_predict": 256}
    }).encode("utf-8")
    
    try:
        req = urllib.request.Request(url, data=payload, method="POST", headers={"Content-Type":"application/json"})
        timeout_s = float(os.environ.get("OLLAMA_TIMEOUT", "90"))
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            out = json.loads(resp.read().decode('utf-8'))
        
        text = out.get('response','') or ''
        text = text.strip()
        if not text:
            logger.warning("NL→Cypher: empty LLM response")
            return None, "LLM returned empty response"

        # First try strict JSON extraction
        start = text.find('{'); end = text.rfind('}')
        if start >= 0 and end > start:
            blob = json.loads(text[start:end+1])
            cypher = blob.get('cypher','').strip()
            if cypher:
                logger.info("NL→Cypher SUCCESS: %s → %s", nl_question, cypher)
                return cypher, None

        # Next, strip code fences
        clean = strip_fences_or_json(text).strip()
        if looks_like_cypher(clean):
            logger.info("NL→Cypher SUCCESS (stripped): %s → %s", nl_question, clean[:200])
            return clean, None

        logger.warning("NL→Cypher: could not parse JSON or detect Cypher. head=%r", text[:120])
        return None, "Model did not return parseable JSON or Cypher"
    except Exception as e:
        logger.exception("NL→Cypher call failed")
        msg = f"Ollama call failed: {getattr(e,'reason',e)}"
        if isinstance(e, urllib.error.URLError) and "timed out" in str(e).lower():
            msg = "LLM timed out before replying"
        return None, msg


if __name__ == '__main__':
    # Local-first by default: bind to loopback so the dashboard is not exposed
    # on the LAN (it is unauthenticated and orchestrates Docker). Windows
    # Firewall does not filter loopback, so localhost access still works and no
    # firewall prompt appears. Power users can opt into a LAN bind via
    # COYOTE_UI_HOST. Debug (Werkzeug interactive debugger + reloader) is OFF
    # unless FLASK_DEBUG=1 — never ship an interactive debugger on by default.
    _ui_host = os.environ.get("COYOTE_UI_HOST", "127.0.0.1")
    _ui_port = int(os.environ.get("COYOTE_UI_PORT", "8080"))
    _ui_debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    print(f"Starting Coyote UI Server on http://localhost:{_ui_port}")
    print(f"Compose directory: {COMPOSE_DIR}")
    print(f"Compose file: {COMPOSE_FILE}")
    print(f"Project name: {PROJECT_NAME}")
    print(f"Log file: {LOG_FILE}")
    
    # Ensure compose directory and file exist
    if not COMPOSE_DIR.exists():
        print(f"WARNING: Compose directory not found: {COMPOSE_DIR}")
        logger.warning(f"Compose directory not found: {COMPOSE_DIR}")
    if not COMPOSE_FILE.exists():
        print(f"WARNING: Compose file not found: {COMPOSE_FILE}")
        logger.warning(f"Compose file not found: {COMPOSE_FILE}")
    
    # Clean up any orphaned containers on startup
    logger.info("Checking for orphaned containers...")
    try:
        run_compose_command(['ps', '--format', 'json'], timeout=5)
    except Exception as e:
        logger.debug("Pre-startup container check skipped: %s", e)

    # Keep the configured LLM model present in Ollama whenever it is up. Started
    # here (not at import) so tests importing this module don't spawn the thread.
    threading.Thread(target=_model_ensure_loop, daemon=True).start()

    app.run(host=_ui_host, port=_ui_port, debug=_ui_debug)