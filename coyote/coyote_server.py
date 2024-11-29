# coyote_server.py

"""
coyote_server.py

Main server script for the Coyote application.
"""

from flask import Flask, request, jsonify, render_template, redirect, url_for, flash, g
from flask_cors import CORS
import requests
import json
import logging
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

# Import necessary functions and modules
from coyote.coyote_main import process_data_from_server
from coyote.utils.config_manager import (
    store_setting, 
    load_secret_key, 
    load_credentials,
    get_event_data_db_connection
)

from coyote.utils.initialize_databases import (
    initialize_coyote_state_db,
    initialize_coyote_event_data_db,
    initialize_wikidata_cache_db
)

# Define base directories
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / 'data'
TEMPLATE_DIR = BASE_DIR / 'templates'
LOGS_DIR = DATA_DIR / 'logs'
LOG_FILE = LOGS_DIR / 'coyote_server.log'

# Ensure directories exist
DATA_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)

# Configure logging globally
logging.basicConfig(filename=str(LOG_FILE), level=logging.DEBUG, format="%(asctime)s - %(name)s - %(levelname)s - %(module)s.%(funcName)s: %(message)s")
logger = logging.getLogger(__name__)

# Initialize Flask app
app = Flask(__name__, template_folder=str(TEMPLATE_DIR))
app.secret_key = load_secret_key()

# Enable CORS with restricted origins (adjust origins as needed)
CORS(app, resources={r"/*": {"origins": "*"}})  # Replace '*' with specific origins in production

def initialize_databases() -> None:
    """
    Initializes all required databases.
    """
    try:
        logger.info("Initializing databases...")
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
    db_conns = ['state_db_conn', 'event_data_db_conn', 'wikidata_cache_db_conn']
    for conn_name in db_conns:
        conn = g.pop(conn_name, None)
        if conn is not None:
            conn.close()


def get_latest_timestamp() -> Optional[str]:
    """
    Retrieve the latest timestamp of annotations from the coyote_event_data.db. 
    Use this function once 'coyote_events_data.db' is implemented.

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
    # Generate a unique event ID for the event
    event_id = str(uuid.uuid4())
    
    # Add common metadata to the data payload
    data['event_id'] = event_id
    data['event'] = event_description
    data['dataSource'] = "Coyote Browser Extension"
    data['timestamp'] = datetime.now().isoformat()

    # Insert the data into the EventStaging table in the staging database
    from coyote.coyote_event_writer import insert_staging_event
    try:
        insert_staging_event(data)
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
    last_fetch_timestamp = get_latest_timestamp()
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
        'limit': 200  # Fetch the maximum number of annotations allowed
    }

    if last_fetch_timestamp:
        params['search_after'] = last_fetch_timestamp
        logger.info(f"Fetching data after timestamp: {last_fetch_timestamp}")
    else:
        logger.info("No previous Hypothes.is data found; fetching all available data.")

    try:
        response = requests.get('https://api.hypothes.is/api/search', headers=headers, params=params)
        response.raise_for_status()
        annotations = response.json().get('rows', [])
        current_timestamp = datetime.now().isoformat()

        process_data_from_server({
            'annotations': annotations,
            'event': "User annotated webpage",
            'timestamp': current_timestamp
        })


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

def main() -> None:
    """
    Main function to run the Coyote server application.
    """
    # Initialize databases
    initialize_databases()

    # Start the Flask app
    logger.info("Starting the Coyote Flask server...")
    app.run(host='0.0.0.0', port=5000, debug=True, threaded=True, use_reloader=False)

if __name__ == '__main__':
    main()


