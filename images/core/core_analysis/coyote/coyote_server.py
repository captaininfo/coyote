# coyote_server.py

"""
coyote_server.py

Main server script for the Coyote application.
"""

from flask import Flask, request, jsonify, render_template, redirect, url_for, flash, g
from flask_cors import CORS
import requests
import logging
import sqlite3
import uuid
import os
from threading import Thread
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

# Import necessary functions and modules
from coyote.coyote_nlp_state_manager import CoyoteNLPStateManager
from coyote.coyote_event_writer import process_hypothesis_annotations, insert_staging_event
from coyote.neo4j_integration.coyote_neo4j_state_manager import CoyoteNeo4jStateManager
from coyote.neo4j_integration.connect_to_ontology import CoyoteOntologyStateManager
from coyote.utils.database_cleanup_manager import CoyoteDatabaseCleanupManager
from coyote.utils.event_status import insert_event_status
from coyote.utils.config_manager import (
    store_setting, 
    load_setting,
    load_secret_key, 
    load_credentials,
    get_event_data_db_connection
)
from coyote.utils.initialize_databases import (
    initialize_coyote_event_staging_db,
    initialize_coyote_state_db,
    initialize_coyote_event_data_db,
    initialize_wikidata_cache_db
)
from coyote.utils.config_container import (
    DATA_DIR, 
    LOGS_DIR, 
    LOG_FILE,
    STATE_DB_FILE,
    EVENT_DATA_DB_FILE,
    STAGING_DB_FILE,
    WIKIDATA_CACHE_DB_FILE
)

# Define base directories (simplified now)
BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = BASE_DIR / 'templates'

# Configure logging globally
# Respects COYOTE_LOG_LEVEL env var (default: INFO)
_log_level_name = os.environ.get("COYOTE_LOG_LEVEL", "INFO").upper()
_log_level = getattr(logging, _log_level_name, logging.INFO)
logging.basicConfig(
    filename=str(LOG_FILE),
    level=_log_level,
    format="%(asctime)s - %(name)s - %(levelname)s - %(module)s.%(funcName)s: %(message)s"
)
logger = logging.getLogger(__name__)

# Log the configuration
logger.info("="*60)
logger.info("Coyote Server Starting")
logger.info(f"Container Mode: {bool(os.environ.get('COYOTE_DATA_DIR'))}")
logger.info(f"Base Directory: {BASE_DIR}")
logger.info(f"Data Directory: {DATA_DIR}")
logger.info(f"Template Directory: {TEMPLATE_DIR}")
logger.info(f"Log File: {LOG_FILE}")
logger.info("="*60)

# --- keep Coyote at DEBUG, quiet down noisy third‑party libs -------------
for noisy in (
        "numba",                   # catches numba.core.*, numba.parfors, …
        # "neo4j.pool",              # optional: pool debug traces
        # "neo4j.io",                # optional: wire‑level dumps
):
    logging.getLogger(noisy).setLevel(logging.INFO)   # or WARNING/ERROR

# Initialize Flask app
app = Flask(__name__, template_folder=str(TEMPLATE_DIR))
app.secret_key = load_secret_key()

# Enable CORS for browser extension communication
# SECURITY NOTE: origins="*" is acceptable because Coyote is designed for localhost-only.
# If exposing to a network, restrict to specific origins or add authentication.
CORS(app, resources={r"/*": {"origins": "*"}})

def initialize_databases() -> None:
    """
    Initializes all required databases.
    """
    try:
        logger.info("Initializing databases...")
        initialize_coyote_event_staging_db()
        initialize_coyote_state_db()
        initialize_coyote_event_data_db()
        initialize_wikidata_cache_db()
        logger.info("Database initialization completed.")
    except Exception as e:
        logger.error(f"An error occurred during database initialization: {e}", exc_info=True)
        # Depending on your application's needs, you might exit or handle the error differently
        # For example: sys.exit(1)


@app.teardown_appcontext
def close_db_connections(exception):
    """
    Close all database connections at the end of the request.
    """
    db_conns = ['state_db_conn', 'event_data_db_conn', 'wikidata_cache_db_conn', 'staging_db']
    for conn_name in db_conns:
        conn = g.pop(conn_name, None)
        if conn is not None:
            conn.close()


def start_coyote_nlp_state_manager():
    """
    Starts the CoyoteNLPStateManager in a background thread.
    """
    try:
        state_manager = CoyoteNLPStateManager()
        logger.info("Starting CoyoteNLPStateManager...")
        state_manager.poll_and_process_events()
    except Exception as e:
        logger.error(f"Error in CoyoteNLPStateManager: {e}", exc_info=True)

def start_coyote_neo4j_state_manager():
    """
    Starts the CoyoteNeo4jStateManager in a background thread.
    """
    try:
        neo4j_manager = CoyoteNeo4jStateManager()
        logger.info("Starting CoyoteNeo4jStateManager...")
        neo4j_manager.poll_and_process_neo4j_events()
    except Exception as e:
        logger.error(f"Error in CoyoteNeo4jStateManager: {e}", exc_info=True)

def start_coyote_ontology_state_manager():
    """
    Starts the CoyoteOntologyStateManager in a background thread.
    """
    try:
        ontology_manager = CoyoteOntologyStateManager()
        logger.info("Starting CoyoteOntologyStateManager...")
        ontology_manager.poll_and_process_ontology()
    except Exception as e:
        logger.error(f"Error in CoyoteOntologyStateManager: {e}", exc_info=True)


def get_latest_timestamp() -> Optional[str]:
    """
    Retrieves the latest timestamp of annotations from the coyote_event_data.db. 
    Why: so API GET calls to Hypothesis filter by timestamp and retrieve new annotations.

    Returns:
       Optional[str]: The latest timestamp in ISO format, or None if not found.
    """
    
    try:
        conn = get_event_data_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT MAX(timestamp) FROM Events
            WHERE event_type = 'User annotated webpage' AND data_source = 'Hypothesis'
        ''')
        result = cursor.fetchone()
        if result and result[0]:
            latest_timestamp = result[0]
            return latest_timestamp
        return None
    except sqlite3.Error as e:
        logger.error(f"SQLite error in get_latest_timestamp: {e}", exc_info=True)
        return None
    except Exception as e:
        logger.exception(f"Unexpected error in get_latest_timestamp: {e}")
        return None
    finally:
        if conn:
            conn.close()


def process_request_data(data: Dict[str, Any], event_description: str) -> Any:
    """
    Helper function to process incoming data and add common fields.

    Args:
        data (dict): The incoming data to process.
        event_description (str): Description of the event.

    Returns:
        Response: A Flask JSON response containing the processing result.
    """

    # Normalize key names to match database schema
    key_mapping = {
        "searchTerms": "search_terms",
        "terms": "search_terms",
        "sourceURL": "source_url",
        "destinationURL": "destination_url",
        "linkText": "link_text",
    }

    # Map old keys to new keys
    for old_key, new_key in key_mapping.items():
        if old_key in data:
            data[new_key] = data.pop(old_key)

    # Generate a unique event ID for the event
    event_id = str(uuid.uuid4())
    
    # ????? Is 'data_source' hardcoded to be "Coyote Browser Extension"?
    # Add common metadata to the data payload
    data['event_id'] = event_id
    data['event_type'] = event_description
    data['data_source'] = "Coyote Browser Extension"
    data['timestamp'] = datetime.now().isoformat()

    # Insert the data into the EventStaging table in the 'coyote_event_staging' database
    try:
        insert_staging_event(data)
        logger.debug(f"Staged event {event_id} in coyote_event_staging.db.")
        # Insert a corresponding status record in the state database
        insert_event_status(event_id, "pending")
        return jsonify({"status": "success", "message": "Data received and staged."})
    except Exception as e:
        logger.error(f"Error inserting data into staging: {e}", exc_info=True)
        return jsonify({"status": "error", "message": str(e)})


@app.route('/configure', methods=['GET', 'POST'])
def configure():
    if request.method == 'POST':
        # Get form data
        hypothesis_username = request.form.get('hypothesis_username')
        hypothesis_token = request.form.get('hypothesis_token')
        neo4j_uri = request.form.get('neo4j_uri')
        neo4j_username = request.form.get('neo4j_username')
        neo4j_password = request.form.get('neo4j_password')

        # Is the "Validate required inputs" code, below, at odds with the try:if/else below it? 
        # Inputting Hypothesis credentials should be optional
        # Validate required inputs
        if not all([neo4j_uri, neo4j_username, neo4j_password]):
            flash('Neo4j credentials are required.')
            return redirect(url_for('configure'))

        try:
            # Store Hypothes.is credentials if provided
            if hypothesis_username and hypothesis_token:
                store_setting('hypothesis_username', hypothesis_username)
                store_setting('hypothesis_token', hypothesis_token, encrypt=True)
            else:
                # Optionally, handle the case where credentials are not provided
                pass

            # Store Neo4j credentials
            store_setting('neo4j_uri', neo4j_uri)
            store_setting('neo4j_username', neo4j_username)
            store_setting('neo4j_password', neo4j_password, encrypt=True)

            flash('Configuration saved successfully!')
            return redirect(url_for('configure'))
        except Exception as e:
            logger.exception(f"Error storing configuration: {e}")
            flash('An error occurred while saving the configuration.')
            return redirect(url_for('configure'))

    else:
        return render_template('configure.html')

@app.route('/init_search', methods=['POST'])
def init_search():
    """
    Handle initial search data from the client.

    Expects JSON payload with search data.

    Returns:
        Response: JSON response with processing result.
    """
    data = request.get_json()
    logger.debug(f"Received search event payload: {data}")
    if not data:
        return jsonify({"error": "No data provided"}), 400
    return process_request_data(data, "User starts or modifies a search")

@app.route('/webpage_visit', methods=['POST'])
def webpage_visit():
    """
    Handle webpage visit data from the client.

    Expects JSON payload with visit data.

    Returns:
        Response: JSON response with processing result.
    """
    data = request.get_json()
    if not data:
        return jsonify({"error": "No data provided"}), 400

    logger.info(f"Processing webpage visit data: {data}")

    # Process the data and store the response
    response = process_request_data(data, "Webpage loads")

    # Log the response
    logger.info(f"Processed webpage visit response: {response.get_json()}")

    return response


@app.route('/hyperlink_click', methods=['POST'])
def hyperlink_click():
    """
    Handle hyperlink click data from the client.

    Expects JSON payload with click data.

    Returns:
        Response: JSON response with processing result or error.
    """
    data = request.get_json()
    logger.debug(f"Received hyperlink click event payload: {data}")
    if not data:
        return jsonify({"error": "No data provided"}), 400
    logger.info(f"Received hyperlink click data: {data}")
    # List of all required keys
    required_keys = ['sourceURL', 'destinationURL', 'linkText', 'timestamp']
    missing_keys = [key for key in required_keys if key not in data]
    if missing_keys:
        error_message = f"Missing data for keys: {missing_keys}"
        logger.error(error_message)
        return jsonify({"error": error_message}), 400
    return process_request_data(data, "User clicks hyperlink")

@app.route('/fetch_hypothesis_data', methods=['GET'])
def fetch_hypothesis_data():
    """
    Fetch annotations from Hypothes.is API and process them.

    Returns:
        Response: Redirect to the configure page with a success or error message.
    """
    # Prefer the server-returned continuation token we stored earlier.
    after = None
    try:
        after = load_setting('hypothesis_search_after')
    except (sqlite3.Error, ValueError) as e:
        logger.debug("Could not load hypothesis_search_after: %s", e)
        after = None
    if not after:
        # Fallback only if we don't yet have a search_after token.
        after = get_latest_timestamp()
    # Load credentials securely from SQLite database
    credentials = load_credentials()
    if not credentials:
        flash("Hypothes.is credentials not found.")
        return redirect(url_for('configure')), 401

    token = credentials.get('token')
    username = credentials.get('username')

    headers = {
        'Authorization': f'Bearer {token}',
        'Content-Type': 'application/json',
        'Accept': 'application/json'
    }

    params = {
        'user': f'acct:{username}@hypothes.is',
        'limit': 200,
        'sort': 'updated',    # stable pagination field
        'order': 'asc',       # oldest→newest, so search_after moves forward
    }

    if after:
        params['search_after'] = after
        logger.info("Fetching Hypothes.is data after: %s", after)
    else:
        logger.info("No previous Hypothes.is token/timestamp; fetching from the beginning.")

    try:
        response = requests.get('https://api.hypothes.is/api/search', headers=headers, params=params)
        response.raise_for_status()
        payload = response.json() or {}
        annotations = payload.get('rows', []) or []
        next_after = payload.get('search_after')
        if next_after:
            store_setting('hypothesis_search_after', next_after)  # persist continuation
            logger.debug("Stored new Hypothes.is search_after token: %s", next_after)
        logger.debug("Received %d annotations", len(annotations))

        process_hypothesis_annotations(annotations)


        flash('Hypothes.is data fetched and processed successfully!')
        return redirect(url_for('configure'))
    except requests.exceptions.HTTPError as http_err:
        logger.error(f"HTTP error occurred: {http_err}")
        flash('Failed to fetch data from Hypothes.is.')
        return redirect(url_for('configure'))
    except Exception as err:
        logger.exception(f"An error occurred: {err}")
        flash('An error occurred while fetching data from Hypothes.is.')
        return redirect(url_for('configure'))

# ─────────────────────────────────────────────────────────────────────────────
# JSON-friendly, async Hypothes.is fetch shim for the UI
# UI calls POST /api/integrations/hypothesis/fetch → UI forwards to
# POST http://127.0.0.1:5000/hypothesis/fetch (this route).
# We kick off the real fetch work in a background thread and return immediately.

from typing import Optional  # already imported above in your file; harmless to repeat in Python files

def _run_hypothesis_fetch_worker(started_at_iso: str) -> None:
    """
    Background worker that performs the Hypothes.is fetch using the same
    underlying logic/flow as the legacy GET /fetch_hypothesis_data route.
    Runs inside a Flask app context so any utils that use `flask.g` work.
    """
    with app.app_context():
        try:
            logger.info("Hypothes.is fetch worker started at %s", started_at_iso)

            after: Optional[str] = None
            try:
                after = load_setting('hypothesis_search_after')
            except (sqlite3.Error, ValueError) as e:
                logger.debug("Could not load hypothesis_search_after in worker: %s", e)
                after = None
            if not after:
                after = get_latest_timestamp()

            # Load credentials (stored via config_manager.store_setting / UI apply step).
            credentials = load_credentials()
            if not credentials:
                logger.error("Hypothes.is fetch aborted: credentials not found in state DB.")
                return

            token = credentials.get('token')
            username = credentials.get('username')
            if not token or not username:
                logger.error("Hypothes.is fetch aborted: missing token and/or username.")
                return

            headers = {
                'Authorization': f'Bearer {token}',
                'Content-Type': 'application/json',
                'Accept': 'application/json'
            }
            params = {
                'user': f'acct:{username}@hypothes.is',
                'limit': 200,
                'sort': 'updated',
                'order': 'asc',
            }
            if after:
                params['search_after'] = after
                logger.info("Fetching Hypothes.is data after: %s", after)
            else:
                logger.info("No previous Hypothes.is data found; fetching from beginning.")

            total_processed = 0
            page = 1
            # Paginate conservatively: honor Hypothes.is 'search_after' token if present, else stop when rows < limit.
            while True:
                resp = requests.get(
                    'https://api.hypothes.is/api/search',
                    headers=headers,
                    params=params,
                    timeout=30
                )
                resp.raise_for_status()
                payload = resp.json() or {}
                rows = payload.get('rows', []) or []
                if rows:
                    process_hypothesis_annotations(rows)
                    total_processed += len(rows)
                    logger.debug("Hypothes.is page %d processed %d rows (running total=%d).",
                                 page, len(rows), total_processed)
                next_after = payload.get('search_after')
                if next_after:
                    params['search_after'] = next_after
                    store_setting('hypothesis_search_after', next_after)  # persist continuation
                    page += 1
                    # Optional safety bound to avoid runaway loops in dev
                    if page > 10:
                        logger.warning("Stopping pagination after 10 pages to bound work.")
                        break
                    continue
                # No continuation token → done
                break

            logger.info("Hypothes.is fetch completed. Total annotations handled: %d.", total_processed)

        except requests.exceptions.HTTPError as http_err:
            logger.error("Hypothes.is HTTP error: %s", http_err, exc_info=True)
        except Exception as e:
            logger.exception("Hypothes.is fetch worker failed: %s", e)


@app.route('/hypothesis/fetch', methods=['POST'])
def hypothesis_fetch_post():
    """
    JSON-friendly shim (used by the UI).
    Returns immediately with ok=True; actual work runs in a background thread.
    """
    started_at_iso = datetime.utcnow().isoformat()
    # Kick off background worker
    t = Thread(target=_run_hypothesis_fetch_worker, args=(started_at_iso,), daemon=True)
    t.start()
    # Fast response the UI can toast as success
    return jsonify({
        "ok": True,
        "message": "Hypothes.is fetch started in Coyote Core.",
        "started_at": started_at_iso
    }), 200

    
# health endpoint for Compose healthcheck
@app.route('/health', methods=['GET'])
def health():
    return jsonify(status='ok'), 200


def main() -> None:
    """
    Main function to run the Coyote server application.
    Initializes databases, starts background state managers for NLP, Neo4j, and Ontology,
    and then launches the Flask server.
    """
    # Initialize databases
    initialize_databases()

    # Start the CoyoteNLPStateManager in a background thread
    state_manager_thread = Thread(target=start_coyote_nlp_state_manager, daemon=True)
    state_manager_thread.start()
    logger.info("Started CoyoteNLPStateManager thread.")

    # Start the CoyoteNeo4jStateManager in a background thread
    neo4j_thread = Thread(target=start_coyote_neo4j_state_manager, daemon=True)
    neo4j_thread.start()
    logger.info("Started CoyoteNeo4jStateManager thread.")

    # Start the CoyoteOntologyStateManager in a background thread
    ontology_thread = Thread(target=start_coyote_ontology_state_manager, daemon=True)
    ontology_thread.start()
    logger.info("Started CoyoteOntologyStateManager thread.")

    # NEW: start the cleanup janitor
    cleanup_mgr = CoyoteDatabaseCleanupManager()
    cleanup_mgr.start()

    # Start the Flask app
    # SECURITY: debug=False in production to prevent Werkzeug debugger exposure
    flask_debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    flask_port = int(os.environ.get("COYOTE_PORT", "5000"))
    logger.info("Starting the Coyote Flask server (debug=%s, port=%d)...", flask_debug, flask_port)
    try:
        app.run(host='0.0.0.0', port=flask_port, debug=flask_debug, threaded=True, use_reloader=False)
    except Exception as e:
        logger.exception(f"Flask server encountered an error: {e}")
    finally:
        logger.info("Flask server has been shut down.")

if __name__ == '__main__':
    main()



