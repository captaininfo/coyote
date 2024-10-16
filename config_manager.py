import sqlite3
from cryptography.fernet import Fernet
import os
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Paths to key files
KEY_FILE = 'coyote_encryption_key.key'
SECRET_KEY_FILE = 'coyote_secret_key.key'

def load_encryption_key():
    if os.path.exists(KEY_FILE):
        with open(KEY_FILE, 'rb') as key_file:
            encryption_key = key_file.read()
    else:
        # Generate a new key
        encryption_key = Fernet.generate_key()
        with open(KEY_FILE, 'wb') as key_file:
            key_file.write(encryption_key)
        print("Generated new encryption key and stored it in 'coyote_encryption_key.key'.")
    return encryption_key

# Load or generate the encryption key
encryption_key = load_encryption_key()
cipher_suite = Fernet(encryption_key)

def load_secret_key():
    if os.path.exists(SECRET_KEY_FILE):
        with open(SECRET_KEY_FILE, 'rb') as key_file:
            secret_key = key_file.read()
    else:
        # Generate a new secret key
        secret_key = os.urandom(24)
        with open(SECRET_KEY_FILE, 'wb') as key_file:
            key_file.write(secret_key)
        print("Generated new secret key and stored it in 'coyote_secret_key.key'.")
    return secret_key

def get_setting(setting_name, decrypt=False):
    conn = sqlite3.connect('coyote_state.db')
    cursor = conn.cursor()
    cursor.execute('SELECT setting_value FROM user_settings WHERE setting_name = ?', (setting_name,))
    result = cursor.fetchone()
    conn.close()
    if result:
        setting_value = result[0]
        if decrypt:
            setting_value = cipher_suite.decrypt(setting_value.encode()).decode()
        return setting_value
    else:
        return None

def store_setting(setting_name, setting_value, encrypt=False):
    conn = sqlite3.connect('coyote_state.db')
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
    conn.close()

def load_credentials():
    """
    Load Hypothes.is credentials from the SQLite database.

    Returns:
        dict: A dictionary containing 'username' and 'token', or None if not found.
    """
    try:
        username = get_setting('hypothesis_username')
        token = get_setting('hypothesis_token', decrypt=True)
        if username and token:
            return {'username': username, 'token': token}
        else:
            if not username:
                logger.warning("Hypothes.is username not found in the database.")
            if not token:
                logger.warning("Hypothes.is token not found in the database.")
            return None
    except Exception as e:
        logger.exception(f"Error loading Hypothes.is credentials: {e}")
        return None
    
def connect_to_neo4j():
    from neo4j import GraphDatabase

    uri = get_setting('neo4j_uri')
    username = get_setting('neo4j_username')
    password = get_setting('neo4j_password', decrypt=True)
    if not uri or not username or not password:
        raise Exception("Neo4j credentials not found. Please configure the application.")

    # Create the Neo4j driver
    driver = GraphDatabase.driver(uri, auth=(username, password))
    return driver