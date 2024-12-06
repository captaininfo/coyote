# config_manager.py

"""
config_manager.py

Module for managing configuration settings, encryption keys, and database connections
for the Coyote application.
"""

from flask import g, Flask
import sqlite3
import logging
import os
from pathlib import Path
from typing import Any, Dict, Optional

from cryptography.fernet import Fernet
from neo4j import GraphDatabase
from neo4j import Driver

# Get the logger for this module
logger = logging.getLogger(__name__)

# Define the base directory and data directory
BASE_DIR: Path = Path(__file__).resolve().parent.parent.parent  # Correct to point to the project root
DATA_DIR: Path = BASE_DIR / 'data'
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Paths to key files
KEY_FILE: Path = DATA_DIR / 'coyote_encryption_key.key'
SECRET_KEY_FILE: Path = DATA_DIR / 'coyote_secret_key.key'

# Paths to database files
STATE_DB_FILE: Path = DATA_DIR / 'coyote_state.db'
EVENT_DATA_DB_FILE: Path = DATA_DIR / 'coyote_event_data.db'
WIKIDATA_CACHE_DB_FILE: Path = DATA_DIR / 'wikidata_cache.db'
STAGING_DB_FILE: Path = DATA_DIR / 'coyote_event_staging.db'


# Load or generate the encryption key
def load_encryption_key() -> bytes:
    try:
        if KEY_FILE.exists():
            with KEY_FILE.open('rb') as key_file:
                return key_file.read()
        else:
            key = Fernet.generate_key()
            with KEY_FILE.open('wb') as key_file:
                key_file.write(key)
            return key
    except Exception as e:
        logger.error(f"Error loading encryption key: {e}", exc_info=True)
        raise


encryption_key: bytes = load_encryption_key()
cipher_suite: Fernet = Fernet(encryption_key)


def load_secret_key() -> bytes:
    """
    Loads the secret key from a file, or generates a new one if it doesn't exist.

    Returns:
        bytes: The secret key.
    """
    try:
        if SECRET_KEY_FILE.exists():
            with SECRET_KEY_FILE.open('rb') as key_file:
                secret_key = key_file.read()
                logger.debug(f"Secret key loaded from '{SECRET_KEY_FILE}'.")
        else:
            secret_key = os.urandom(24)
            with SECRET_KEY_FILE.open('wb') as key_file:
                key_file.write(secret_key)
            logger.info(f"Generated new secret key and stored it in '{SECRET_KEY_FILE}'.")
        return secret_key
    except Exception as e:
        logger.error(f"Error loading secret key: {e}", exc_info=True)
        raise


def get_staging_db_connection() -> sqlite3.Connection:
    """
    Retrieves or initializes a SQLite database connection for the staging database.
    """
    if 'staging_db' not in g:
        try:
            g.staging_db = sqlite3.connect(STAGING_DB_FILE)
            g.staging_db.row_factory = sqlite3.Row  # Optional: better handling of query results
        except sqlite3.Error as e:
            logger.error(f"Error connecting to event staging database: {e}")
            raise
    return g.staging_db

# Ensure connection cleanup at the end of the request
app = Flask(__name__)  # Ensure your Flask app is properly initialized.

@app.teardown_appcontext
def close_staging_db_connection(exception):
    db = g.pop('staging_db', None)
    if db is not None:
        db.close()


def get_staging_read_connection() -> sqlite3.Connection:
    """
    Retrieves a read-only SQLite database connection for the staging database.

    Returns:
        sqlite3.Connection: The SQLite connection object for reading from the staging database.
    """
    try:
        conn = sqlite3.connect(STAGING_DB_FILE)
        conn.row_factory = sqlite3.Row  # Better handling of query results, enabling dictionary-like access
        logger.debug(f"Successfully established read-only connection to {STAGING_DB_FILE}")
        return conn
    except sqlite3.Error as e:
        logger.error(f"Error connecting to the staging database for read: {e}", exc_info=True)
        raise


def get_state_db_connection() -> sqlite3.Connection:
    """
    Establishes and returns a direct connection to the state database.

    Returns:
        sqlite3.Connection: The SQLite connection object.
    """
    try:
        conn = sqlite3.connect(STATE_DB_FILE)
        conn.row_factory = sqlite3.Row  # Optional for dict-like access
        logger.debug("Created direct connection to the coyote_state database.")
        return conn
    except sqlite3.Error as e:
        logger.error(f"Error connecting to state database: {e}")
        raise


def get_event_data_db_connection() -> sqlite3.Connection:
    """
    Establishes and returns a direct connection to the event data database.

    This connection does not use Flask's `g` because it may be used outside of the HTTP request
    lifecycle, such as in the state manager or other background tasks.

    Returns:
        sqlite3.Connection: The SQLite connection object.
    """
    try:
        conn = sqlite3.connect(EVENT_DATA_DB_FILE)
        conn.row_factory = sqlite3.Row
        logger.debug("Created direct connection to event data database.")
        return conn
    except sqlite3.Error as e:
        logger.error(f"Error connecting to event data database: {e}")
        raise


def get_wikidata_cache_db_connection() -> sqlite3.Connection:
    """
    Establishes and returns a direct connection to the Wikidata cache database.

    This connection does not use Flask's `g` because it is not used exclusively within
    the HTTP request lifecycle and may be accessed by background tasks.

    Returns:
        sqlite3.Connection: The SQLite connection object.
    """
    try:
        conn = sqlite3.connect(WIKIDATA_CACHE_DB_FILE)
        conn.row_factory = sqlite3.Row
        logger.debug("Created direct connection to Wikidata cache database.")
        return conn
    except sqlite3.Error as e:
        logger.error(f"Error connecting to Wikidata cache database: {e}")
        raise


def get_setting(setting_name: str, decrypt: bool = False) -> Optional[str]:
    """
    Retrieves a setting value from the state database.

    Args:
        setting_name (str): The name of the setting.
        decrypt (bool): Whether to decrypt the setting value.

    Returns:
        Optional[str]: The setting value, or None if not found.
    """
    try:
        conn = get_state_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            'SELECT setting_value FROM user_settings WHERE setting_name = ?',
            (setting_name,)
        )
        result = cursor.fetchone()
        if result:
            setting_value = result[0]
            if decrypt:
                setting_value = cipher_suite.decrypt(setting_value.encode()).decode()
            logger.debug(f"Retrieved setting '{setting_name}'.")
            return setting_value
        else:
            logger.warning(f"Setting '{setting_name}' not found in the database.")
            return None
    except sqlite3.Error as e:
        logger.error(f"SQLite error in get_setting: {e}", exc_info=True)
        return None
    except Exception as e:
        logger.error(f"Error in get_setting: {e}", exc_info=True)
        return None


def store_setting(setting_name: str, setting_value: str, encrypt: bool = False) -> None:
    """
    Stores a setting value in the state database.

    Args:
        setting_name (str): The name of the setting.
        setting_value (str): The value of the setting.
        encrypt (bool): Whether to encrypt the setting value.
    """
    try:
        conn = get_state_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_settings (
                id INTEGER PRIMARY KEY,
                setting_name TEXT NOT NULL UNIQUE,
                setting_value TEXT NOT NULL
            )
        ''')
        if encrypt:
            setting_value = cipher_suite.encrypt(setting_value.encode()).decode()
        cursor.execute('''
            INSERT OR REPLACE INTO user_settings (setting_name, setting_value)
            VALUES (?, ?)
        ''', (setting_name, setting_value))
        conn.commit()
        logger.info(f"Stored setting '{setting_name}' in the database.")
    except sqlite3.Error as e:
        logger.error(f"SQLite error in store_setting: {e}", exc_info=True)
    except Exception as e:
        logger.error(f"Error in store_setting: {e}", exc_info=True)


def load_credentials() -> Optional[Dict[str, str]]:
    """
    Loads Hypothes.is credentials from the state database.

    Returns:
        Optional[Dict[str, str]]: A dictionary with 'username' and 'token', or None if not found.
    """
    try:
        username = get_setting('hypothesis_username')
        token = get_setting('hypothesis_token', decrypt=True)
        if username and token:
            logger.debug("Hypothes.is credentials loaded successfully.")
            return {'username': username, 'token': token}
        else:
            if not username:
                logger.warning("Hypothes.is username not found in the database.")
            if not token:
                logger.warning("Hypothes.is token not found in the database.")
            return None
    except Exception as e:
        logger.error(f"Error loading Hypothes.is credentials: {e}", exc_info=True)
        return None


def connect_to_neo4j() -> Driver:
    """
    Creates a connection to the Neo4j database.

    Returns:
        Driver: The Neo4j driver instance.

    Raises:
        Exception: If Neo4j credentials are not found.
    """
    try:
        uri = get_setting('neo4j_uri')
        username = get_setting('neo4j_username')
        password = get_setting('neo4j_password', decrypt=True)
        if not uri or not username or not password:
            logger.error("Neo4j credentials not found in the database.")
            raise Exception("Neo4j credentials not found. Please configure the application.")

        driver: Driver = GraphDatabase.driver(uri, auth=(username, password))
        logger.debug("Connected to Neo4j database.")
        return driver
    except Exception as e:
        logger.error(f"Error connecting to Neo4j: {e}", exc_info=True)
        raise


def close_db_connections(exception: Optional[Exception] = None) -> None:
    """
    Close all database connections at the end of the request.
    """
    db_conns = ['state_db_conn', 'event_data_db_conn', 'wikidata_cache_db_conn']
    for conn_name in db_conns:
        conn = g.pop(conn_name, None)
        if conn is not None:
            conn.close()
            logger.debug(f"Closed {conn_name} connection.")


# Example: Registering the teardown function with the Flask app.
# app.teardown_appcontext(close_db_connections)
