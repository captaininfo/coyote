from flask import Flask, request, jsonify, render_template, redirect, url_for, flash
from flask_cors import CORS
import requests
import json
import logging
from datetime import datetime
from pathlib import Path
from coyote.coyote_main import process_data_from_server

# Import configuration functions
from coyote.utils.config_manager import (
    store_setting,
    load_secret_key,
    load_credentials,
)

# Get the project root directory (one level up from this file)
BASE_DIR = Path(__file__).resolve().parent.parent

# Define the data directory
DATA_DIR = BASE_DIR / 'data'

# Define the templates directory
TEMPLATE_DIR = BASE_DIR / 'templates'

app = Flask(__name__, template_folder=str(TEMPLATE_DIR))
app.secret_key = load_secret_key()

# Configure logging
LOGS_DIR = DATA_DIR / 'logs'
LOG_FILE = LOGS_DIR / 'coyote_server.log'
LOGS_DIR.mkdir(parents=True, exist_ok=True)  # Ensure logs directory exists
logging.basicConfig(filename=str(LOG_FILE), level=logging.INFO)
logger = logging.getLogger(__name__)

# Enable CORS with restricted origins (adjust origins as needed)
CORS(app, resources={r"/*": {"origins": "*"}})  # Replace '*' with specific origins in production

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

def get_latest_timestamp():
    """
    Retrieve the latest timestamp from analysis_result.json for Hypothes.is data.

    Returns:
        str or None: The latest timestamp in ISO format, or None if not found.
    """
    analysis_file = DATA_DIR / 'analysis_result.json'
    try:
        with analysis_file.open('r') as file:
            data = json.load(file)
        # Filter entries with dataSource set to "Hypothesis" and collect timestamps
        hypothesis_timestamps = [
            item['timestamp'] for item in data
            if item.get('dataSource') == "Hypothesis" and 'timestamp' in item
        ]
        if hypothesis_timestamps:
            latest_timestamp = max(hypothesis_timestamps)
            return latest_timestamp
        return None
    except FileNotFoundError:
        logger.warning("File 'analysis_result.json' not found.")
        return None
    except json.JSONDecodeError as e:
        logger.error(f"Error parsing JSON file 'analysis_result.json': {e}")
        return None
    except Exception as e:
        logger.exception(f"Unexpected error in get_latest_timestamp: {e}")
        return None

def process_request_data(data, event_description):
    """
    Helper function to process incoming data and add common fields.

    Args:
        data (dict): The incoming data to process.
        event_description (str): Description of the event.

    Returns:
        Response: A Flask JSON response containing the processing result.
    """
    data['event'] = event_description
    data['dataSource'] = "Coyote Browser Extension"
    response = process_data_from_server(data)
    return jsonify(response)

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

from flask import redirect, url_for, flash

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

# Start the Flask app
app.run(host='0.0.0.0', port=5000, debug=True, threaded=True, use_reloader=False)


