"""
test_config_manager.py
tests/util/test_config_manager.py

This script uses pytest to test each function and class in config_manager.py. 
This includes tests for encryption key loading, secret key loading, setting retrieval 
and storage, credential loading, and Neo4j connection handling.
"""

import pytest
import os
import sqlite3
from pathlib import Path
from unittest.mock import patch, MagicMock
from coyote.utils.config_manager import (
    load_encryption_key,
    load_secret_key,
    get_setting,
    store_setting,
    load_credentials,
    connect_to_neo4j
)
from cryptography.fernet import Fernet

# Constants
DATABASE_FILE = Path(__file__).resolve().parent.parent.parent / 'data' / 'coyote_state.db'
KEY_FILE = Path(__file__).resolve().parent.parent.parent / 'data' / 'coyote_encryption_key.key'
SECRET_KEY_FILE = Path(__file__).resolve().parent.parent.parent / 'data' / 'coyote_secret_key.key'


@pytest.fixture(scope="function")
def temp_db():
    """Creates a temporary SQLite database for testing, then removes it."""
    if DATABASE_FILE.exists():
        DATABASE_FILE.unlink()  # Remove any existing database
    conn = sqlite3.connect(DATABASE_FILE)
    conn.execute('''
        CREATE TABLE IF NOT EXISTS user_settings (
            id INTEGER PRIMARY KEY,
            setting_name TEXT NOT NULL UNIQUE,
            setting_value TEXT NOT NULL
        )
    ''')
    conn.commit()
    yield conn
    conn.close()
    if DATABASE_FILE.exists():
        DATABASE_FILE.unlink()


def test_load_encryption_key():
    """Test that the encryption key is correctly loaded or generated."""
    if KEY_FILE.exists():
        KEY_FILE.unlink()  # Start fresh for this test

    # Check if a new key is generated if none exists
    key = load_encryption_key()
    assert isinstance(key, bytes)
    assert KEY_FILE.exists()

    # Check if the existing key is loaded
    key_again = load_encryption_key()
    assert key == key_again  # Should be the same key


def test_load_secret_key():
    """Test that the secret key is correctly loaded or generated."""
    if SECRET_KEY_FILE.exists():
        SECRET_KEY_FILE.unlink()  # Start fresh for this test

    # Check if a new key is generated if none exists
    secret_key = load_secret_key()
    assert isinstance(secret_key, bytes)
    assert SECRET_KEY_FILE.exists()

    # Check if the existing key is loaded
    secret_key_again = load_secret_key()
    assert secret_key == secret_key_again  # Should be the same key


def test_store_and_get_setting(temp_db):
    """Test storing and retrieving settings, with and without encryption."""
    # Test without encryption
    store_setting('test_setting', 'test_value')
    result = get_setting('test_setting')
    assert result == 'test_value'

    # Test with encryption
    store_setting('test_encrypted', 'encrypted_value', encrypt=True)
    encrypted_result = get_setting('test_encrypted', decrypt=True)
    assert encrypted_result == 'encrypted_value'


@patch('coyote.utils.config_manager.get_setting')
def test_load_credentials(mock_get_setting):
    """Test loading credentials for Hypothes.is with mock settings."""
    # Mock settings retrieval
    mock_get_setting.side_effect = lambda setting_name, decrypt=False: {
        'hypothesis_username': 'test_user',
        'hypothesis_token': 'test_token'
    }.get(setting_name)

    credentials = load_credentials()
    assert credentials == {'username': 'test_user', 'token': 'test_token'}


@patch('coyote.utils.config_manager.get_setting')
@patch('coyote.utils.config_manager.GraphDatabase.driver')
def test_connect_to_neo4j(mock_driver, mock_get_setting):
    """Test Neo4j connection function with mocked settings and driver."""
    mock_get_setting.side_effect = lambda setting_name, decrypt=False: {
        'neo4j_uri': 'bolt://localhost:7687',
        'neo4j_username': 'neo4j',
        'neo4j_password': 'password'
    }.get(setting_name)
    mock_driver.return_value = MagicMock()

    driver = connect_to_neo4j()
    assert driver is not None
    mock_driver.assert_called_once_with('bolt://localhost:7687', auth=('neo4j', 'password'))


def test_get_setting_nonexistent(temp_db):
    """Test that None is returned when a nonexistent setting is retrieved."""
    result = get_setting('nonexistent_setting')
    assert result is None


def test_store_setting_encrypted_value(temp_db):
    """Test that storing and retrieving an encrypted setting works as expected."""
    test_value = 'secret_value'
    store_setting('encrypted_setting', test_value, encrypt=True)
    
    # Ensure the stored value is encrypted in the database
    with sqlite3.connect(DATABASE_FILE) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT setting_value FROM user_settings WHERE setting_name = 'encrypted_setting'")
        result = cursor.fetchone()
        assert result is not None
        assert result[0] != test_value  # Ensure the value is stored encrypted
    
    # Retrieve the value with decryption
    decrypted_value = get_setting('encrypted_setting', decrypt=True)
    assert decrypted_value == test_value
