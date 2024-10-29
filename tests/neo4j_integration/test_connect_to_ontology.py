"""
test_connect_to_ontology.py

Unit tests for the connect_to_ontology.py module using pytest.
"""

import json
import sqlite3
import logging
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest import mock
from unittest.mock import MagicMock, patch

import pytest
from neo4j import GraphDatabase, Driver, Session
from SPARQLWrapper import SPARQLWrapper, JSON

# Import the module to be tested
from coyote.neo4j_integration.connect_to_ontology import (
    initialize_cache_db,
    get_from_cache,
    save_to_cache,
    get_batch_of_node_ids,
    get_user_data_nodes,
    extract_uris_from_node_data,
    batch_query_wikidata,
    process_all_uris,
    get_score_from_node_data,
    create_or_link_wikidata_ontology_node,
    create_or_link_node,
    set_node_property,
    update_node_status_in_db,
    main,
    # ... other imports if needed ...
)

# Initialize logger
logger = logging.getLogger(__name__)


@pytest.fixture
def temp_cache_db(tmp_path):
    """
    Fixture to create a temporary cache database for testing.
    """
    cache_db = tmp_path / f"wikidata_cache_{uuid.uuid4()}.db"
    if cache_db.exists():
        cache_db.unlink()
    yield cache_db
    if cache_db.exists():
        cache_db.unlink()


@pytest.fixture
def temp_state_db(tmp_path):
    """
    Fixture to create a temporary state database for testing.
    """
    state_db = tmp_path / f"coyote_state_{uuid.uuid4()}.db"
    if state_db.exists():
        state_db.unlink()
    yield state_db
    if state_db.exists():
        state_db.unlink()


def test_initialize_cache_db(temp_cache_db):
    """
    Test initializing the cache database.
    """
    initialize_cache_db(cache_db_path=temp_cache_db)
    assert temp_cache_db.exists()
    # Check that the table exists
    with sqlite3.connect(temp_cache_db) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='wikidata_cache'")
        result = cursor.fetchone()
        assert result is not None


def test_get_from_cache_empty(temp_cache_db):
    """
    Test getting data from an empty cache.
    """
    initialize_cache_db(cache_db_path=temp_cache_db)

    # Verify the cache is empty
    with sqlite3.connect(temp_cache_db) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM wikidata_cache")
        count = cursor.fetchone()[0]
        assert count == 0

    result = get_from_cache("http://example.com/entity/Q1", cache_db_path=temp_cache_db)
    assert result is None


def test_save_to_cache_and_get_from_cache(temp_cache_db):
    """
    Test saving to cache and then retrieving it.
    """
    test_uri = "http://example.com/entity/Q1"
    test_data = {"key": "value"}
    initialize_cache_db(cache_db_path=temp_cache_db)
    save_to_cache(test_uri, test_data, cache_db_path=temp_cache_db)
    result = get_from_cache(test_uri, cache_db_path=temp_cache_db)
    assert result == test_data


def test_get_batch_of_node_ids(temp_state_db):
    """
    Test fetching a batch of node IDs from the state database.
    """
    # Initialize the state database
    with sqlite3.connect(temp_state_db) as conn:
        cursor = conn.cursor()
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS node_processing_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            node_id INTEGER NOT NULL,
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            processed_at TIMESTAMP
        )
        """)
        # Insert some test data
        cursor.executemany(
            "INSERT INTO node_processing_queue (node_id, status) VALUES (?, 'pending')",
            [(1,), (2,), (3,)]
        )
        conn.commit()
    # Fetch the batch
    node_ids = get_batch_of_node_ids(db_path=temp_state_db)
    assert set(node_ids) == {1, 2, 3}
    # Verify that their status is updated to 'in_progress'
    with sqlite3.connect(temp_state_db) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT node_id, status FROM node_processing_queue WHERE status = 'in_progress'")
        rows = cursor.fetchall()
        in_progress_ids = [row[0] for row in rows]
        assert set(in_progress_ids) == {1, 2, 3}


def test_update_node_status_in_db(temp_state_db):
    """
    Test updating the status of a node in the state database.
    """
    # Initialize the state database
    with sqlite3.connect(temp_state_db) as conn:
        cursor = conn.cursor()
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS node_processing_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            node_id INTEGER NOT NULL,
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            processed_at TIMESTAMP
        )
        """)
        cursor.execute(
            "INSERT INTO node_processing_queue (node_id, status) VALUES (?, 'in_progress')",
            (1,)
        )
        conn.commit()
    # Update the node status
    update_node_status_in_db(1, 'processed', db_path=temp_state_db)
    # Verify the update
    with sqlite3.connect(temp_state_db) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT status FROM node_processing_queue WHERE node_id = ?", (1,))
        status = cursor.fetchone()[0]
        assert status == 'processed'

# ... rest of the test functions ...

