"""
config_manager.py

Module for managing configuration settings, encryption keys, and database connections
for the Coyote application.
"""

import logging
import os
import sqlite3
from pathlib import Path
from typing import Any, Dict, Optional

from cryptography.fernet import Fernet
from neo4j import GraphDatabase, Driver

logger = logging.getLogger(__name__)

# Get the project root directory (two levels up from this file)
BASE_DIR = Path(__file__).resolve().parents[2]

# Define the data directory
DATA_DIR = BASE_DIR / 'data'

# Paths to key files
KEY_FILE = DATA_DIR / 'coyote_encryption_key.key'
SECRET_KEY_FILE = DATA_DIR / 'coyote_secret_key.key'
DATABASE_FILE = DATA_DIR / 'coyote_state.db'


def load_encryption_key() -> bytes:
    """
    Loads the encryption key from a file, or generates a new one if it doesn't exist.

    Returns:
        bytes: The encryption key.
    """
    try:
        if KEY_FILE.exists():
            with KEY_FILE.open('rb') as key_file:
                encryption_key = key_file.read()
                logger.debug(f"Encryption key loaded from '{KEY_FILE}'.")
        else:
            encryption_key = Fernet.generate_key()
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            with KEY_FILE.open('wb') as key_file:
                key_file.write(encryption_key)
            logger.info(f"Generated new encryption key and stored it in '{KEY_FILE}'.")
        return encryption_key
    except Exception as e:
        logger.error(f"Error loading encryption key: {e}", exc_info=True)
        raise


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
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            with SECRET_KEY_FILE.open('wb') as key_file:
                key_file.write(secret_key)
            logger.info(f"Generated new secret key and stored it in '{SECRET_KEY_FILE}'.")
        return secret_key
    except Exception as e:
        logger.error(f"Error loading secret key: {e}", exc_info=True)
        raise


# Load or generate the encryption key
encryption_key: bytes = load_encryption_key()
cipher_suite = Fernet(encryption_key)


def get_setting(setting_name: str, decrypt: bool = False) -> Optional[str]:
    """
    Retrieves a setting value from the database.

    Args:
        setting_name (str): The name of the setting.
        decrypt (bool): Whether to decrypt the setting value.

    Returns:
        Optional[str]: The setting value, or None if not found.
    """
    try:
        with sqlite3.connect(DATABASE_FILE) as conn:
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
    Stores a setting value in the database.

    Args:
        setting_name (str): The name of the setting.
        setting_value (str): The value of the setting.
        encrypt (bool): Whether to encrypt the setting value.
    """
    try:
        with sqlite3.connect(DATABASE_FILE) as conn:
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
    Loads Hypothes.is credentials from the database.

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
