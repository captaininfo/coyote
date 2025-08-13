#!/usr/bin/env python3
"""
coyote_ui_server.py - Lightweight server for Coyote UI
This runs OUTSIDE Docker and serves the UI + manages Docker containers
"""

from flask import Flask, render_template, jsonify, send_from_directory, request as flask_request
import subprocess
import json
import os
from datetime import datetime, timedelta
import logging
from pathlib import Path


# Configure template and static directories
current_dir = os.path.dirname(os.path.abspath(__file__))
template_dir = os.path.join(current_dir, 'templates')  # Look for templates/ in same directory
static_dir = os.path.join(current_dir, 'static')      # Look for static/ in same directory

app = Flask(__name__, 
           template_folder=template_dir,
           static_folder=static_dir)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

extension_heartbeats = {}
HEARTBEAT_TIMEOUT = 15
DOCKER_BIN = os.environ.get("DOCKER_BIN", "docker")

# --- Compose location ---
# Default to the repo layout: ui/ (this file) -> ../compose
BASE_DIR = Path(__file__).resolve().parents[1]
DEFAULT_COMPOSE_DIR = BASE_DIR / "compose"
# Allow override via env var, but fall back to ../compose
COMPOSE_DIR = Path(os.environ.get("COYOTE_COMPOSE_DIR", str(DEFAULT_COMPOSE_DIR))).resolve()
COMPOSE_FILE = (COMPOSE_DIR / "compose.yaml").resolve()


@app.route('/')
def index():
    """Serve the main UI"""
    return render_template('coyote_wireframe.html')

@app.route('/check_docker_status')
def check_docker_status():
    """Check if Docker containers are running"""
    try:
        logger.info("Checking Docker status...")
        # Check if docker is available
        docker_check = subprocess.run(
            ['docker', '--version'],
            capture_output=True,
            text=True,
            timeout=5
        )

        logger.info(f"Docker version check return code: {docker_check.returncode}")
        logger.info(f"Docker version output: {docker_check.stdout}")
        
        if docker_check.returncode != 0:
            return jsonify({
                'docker_available': False,
                'error': 'Docker is not installed or not running'
            })
        
        # Check specific containers
        containers_status = {}
        
        # Check Coyote container
        coyote_check = subprocess.run(
            ['docker', 'ps', '--filter', 'name=coyote_app', '--format', '{{.Status}}'],
            capture_output=True,
            text=True,
            timeout=5
        )
        containers_status['coyote_core'] = bool(coyote_check.stdout.strip())
        
        # Check Neo4j container
        neo4j_check = subprocess.run(
            ['docker', 'ps', '--filter', 'name=database', '--format', '{{.Status}}'],
            capture_output=True,
            text=True,
            timeout=5
        )
        containers_status['neo4j'] = bool(neo4j_check.stdout.strip())
        
        # Check if Coyote server is responding (if container is running)
        coyote_responding = False
        if containers_status['coyote_core']:
            try:
                import requests
                response = requests.get('http://localhost:5000/health', timeout=2)
                coyote_responding = response.status_code == 200
            except:
                pass
        
        return jsonify({
            'docker_available': True,
            'containers': containers_status,
            'coyote_responding': coyote_responding
        })
        
    except subprocess.TimeoutExpired:
        return jsonify({
            'docker_available': False,
            'error': 'Docker command timed out'
        })
    except Exception as e:
        logger.error(f"Error checking Docker status: {e}")
        return jsonify({
            'docker_available': False,
            'error': str(e)
        })

@app.route('/start_docker_containers', methods=['POST'])
def start_docker_containers():
    """
    Start Docker containers via Compose profiles.

    Body (optional JSON):
      { "profiles": ["core"] }                    # Core only (Neo4j + Coyote Core)
      { "profiles": ["core","llm","agent"] }      # Everything
    If omitted, defaults to Everything for MVP.
    """
    try:
        # Validate compose location early for clearer errors
        if not COMPOSE_DIR.exists():
            raise FileNotFoundError(f"Compose directory not found: {COMPOSE_DIR}")
        if not COMPOSE_FILE.exists():
            raise FileNotFoundError(f"Compose file not found: {COMPOSE_FILE}")

        # Parse request body (optional)
        payload = flask_request.get_json(silent=True) or {}
        requested = payload.get("profiles") or ["core", "llm", "agent"]

        # Normalize + safety filter
        allowed = ("core", "llm", "agent")
        # keep order, drop dupes, ignore unknowns
        profiles = []
        for p in requested:
            if isinstance(p, str) and p in allowed and p not in profiles:
                profiles.append(p)

        # If client passed junk and we filtered everything out, default to Everything
        if not profiles:
            profiles = ["core", "llm", "agent"]

        # Build command
        cmd = [DOCKER_BIN, 'compose', '-f', str(COMPOSE_FILE)]
        for p in profiles:
            cmd += ['--profile', p]
        cmd += ['up', '-d', '--pull=missing']

        # Longer timeout when pulling big images (llm/agent)
        needs_big_images = any(p in ("llm", "agent") for p in profiles)
        timeout_s = 420 if needs_big_images else 180

        result = subprocess.run(
            cmd,
            cwd=str(COMPOSE_DIR),
            capture_output=True,
            text=True,
            timeout=timeout_s
        )

        if result.returncode == 0:
            return jsonify({
                'status': 'success',
                'message': f'Starting profiles: {profiles}',
                'profiles': profiles,
                'output': result.stdout
            })
        else:
            return jsonify({
                'status': 'error',
                'message': 'Failed to start containers',
                'profiles': profiles,
                'error': result.stderr
            }), 500

    except Exception as e:
        logger.error(f"Error starting Docker containers: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500


@app.route('/stop_docker_containers', methods=['POST'])
def stop_docker_containers():
    """Stop Docker containers"""
    try:
        if not COMPOSE_DIR.exists():
            raise FileNotFoundError(f"Compose directory not found: {COMPOSE_DIR}")
        if not COMPOSE_FILE.exists():
            raise FileNotFoundError(f"Compose file not found: {COMPOSE_FILE}")
        
        result = subprocess.run(
            [DOCKER_BIN, 'compose', '-f', str(COMPOSE_FILE), 'down'],
            cwd=str(COMPOSE_DIR),
            capture_output=True,
            text=True,
            timeout=120
        )
        
        if result.returncode == 0:
            return jsonify({
                'status': 'success',
                'message': 'Docker containers stopped',
                'output': result.stdout
            })
        else:
            return jsonify({
                'status': 'error',
                'message': 'Failed to stop containers',
                'error': result.stderr
            }), 500
            
    except Exception as e:
        logger.error(f"Error stopping Docker containers: {e}")
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

@app.route('/extension_heartbeat', methods=['POST'])
def extension_heartbeat():
    """Accept heartbeats from the browser extension."""
    try:
        data = json.loads(flask_request.data) if flask_request.data else {}
    except Exception:
        data = {}
    # Use provided ID or a generic key
    ext_id = data.get('extensionId') or data.get('browserName') or 'unknown'
    extension_heartbeats[ext_id] = {
        'ts': datetime.utcnow(),
        'data': data,
    }
    return jsonify({'status': 'ok'})

@app.route('/extension_status')
def extension_status():
    """Return aggregated extension status based on last heartbeat."""
    now = datetime.utcnow()
    active = False
    details = []
    for ext_id, payload in list(extension_heartbeats.items()):
        age = (now - payload['ts']).total_seconds()
        if age <= HEARTBEAT_TIMEOUT:
            active = True
            details.append({'id': ext_id, 'age_sec': age})
    return jsonify({'active': active, 'details': details})

@app.route('/compose_ps')
def compose_ps():
    """Return docker compose ps as JSON for the UI."""
    try:
        if not COMPOSE_DIR.exists() or not COMPOSE_FILE.exists():
            return jsonify({'status':'error','error':'compose not found'}), 404
        result = subprocess.run(
            [DOCKER_BIN, 'compose', '-f', str(COMPOSE_FILE), 'ps', '--format', 'json'],
            cwd=str(COMPOSE_DIR),
            capture_output=True,
            text=True,
            timeout=10
        )
        if result.returncode != 0:
            return jsonify({'status': 'error','error': result.stderr}), 500
        # Compose prints a JSON array; return it directly
        return app.response_class(result.stdout, mimetype='application/json')
    except Exception as e:
        return jsonify({'status': 'error','message': str(e)}), 500


if __name__ == '__main__':
    print("Starting Coyote UI Server on http://localhost:8080")
    app.run(host='0.0.0.0', port=8080, debug=True)