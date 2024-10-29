
"""
coyote_state_manager.py
tests/util/test_coyote_state_manager.py

This script uses  pytest to cover each method in the CoyoteStateManager class. 
The tests are designed to ensure correct behavior when adding nodes, retrieving pending nodes, 
marking nodes as processed, and handling database initialization and connection.
"""

import pytest
import sys
from pathlib import Path
from coyote.utils.coyote_state_manager import CoyoteStateManager

# Constants
DATABASE_FILE = Path(__file__).resolve().parent.parent.parent / 'data' / 'coyote_state.db'

# Add the project root directory to the PYTHONPATH
project_root = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(project_root))

@pytest.fixture(scope="function")
def state_manager():
    """
    Fixture to initialize the CoyoteStateManager and ensure the database is fresh for each test.
    """
    if DATABASE_FILE.exists():
        DATABASE_FILE.unlink()  # Remove any existing database
    manager = CoyoteStateManager(db_path=DATABASE_FILE)
    yield manager
    manager.close()
    if DATABASE_FILE.exists():
        DATABASE_FILE.unlink()


def test_initialization(state_manager):
    """
    Test that the CoyoteStateManager initializes the database correctly.
    """
    # Check that the node_processing_queue table is created
    state_manager.cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='node_processing_queue'")
    table = state_manager.cursor.fetchone()
    assert table is not None, "The node_processing_queue table should be created on initialization."


def test_add_node_to_queue(state_manager):
    """
    Test adding a node to the processing queue.
    """
    node_ids = [101, 102, 103]
    state_manager.add_node_to_queue(node_ids)

    # Verify the nodes are added with the 'pending' status
    state_manager.cursor.execute("SELECT node_id, status FROM node_processing_queue")
    rows = state_manager.cursor.fetchall()
    added_node_ids = [row[0] for row in rows]
    assert added_node_ids == node_ids, f"Expected nodes {node_ids} but got {added_node_ids}"
    for row in rows:
        assert row[1] == 'pending', f"Node {row[0]} should have 'pending' status."


def test_get_pending_nodes(state_manager):
    """
    Test retrieving pending nodes from the queue with a limit.
    """
    node_ids = [201, 202, 203, 204]
    state_manager.add_node_to_queue(node_ids)

    # Retrieve pending nodes with a limit of 2
    pending_nodes = state_manager.get_pending_nodes(limit=2)
    assert pending_nodes == node_ids[:2], f"Expected first two nodes {node_ids[:2]} but got {pending_nodes}"

    # Retrieve pending nodes with a limit higher than available
    pending_nodes_all = state_manager.get_pending_nodes(limit=10)
    assert pending_nodes_all == node_ids, f"Expected all nodes {node_ids} but got {pending_nodes_all}"


def test_mark_node_as_processed(state_manager):
    """
    Test marking a node as processed.
    """
    node_ids = [301]
    state_manager.add_node_to_queue(node_ids)

    # Mark the node as processed
    state_manager.mark_node_as_processed(node_ids[0])

    # Verify the node's status is updated to 'processed'
    state_manager.cursor.execute("SELECT status, processed_at FROM node_processing_queue WHERE node_id = ?", (node_ids[0],))
    row = state_manager.cursor.fetchone()
    assert row[0] == 'processed', f"Node {node_ids[0]} should be marked as 'processed'."
    assert row[1] is not None, f"Node {node_ids[0]} should have a processed_at timestamp."


def test_close_connection(state_manager):
    """
    Test that the database connection is closed without errors.
    """
    try:
        state_manager.close()
        assert True  # If no exception is raised, the test passes
    except Exception as e:
        pytest.fail(f"Closing the database connection should not raise an exception, but got: {e}")


def test_add_and_retrieve_pending_nodes(state_manager):
    """
    Test adding nodes to the queue and retrieving only the pending ones.
    """
    node_ids = [401, 402, 403]
    state_manager.add_node_to_queue(node_ids)

    # Mark one node as processed
    state_manager.mark_node_as_processed(node_ids[1])

    # Retrieve pending nodes and ensure only unprocessed ones are returned
    pending_nodes = state_manager.get_pending_nodes()
    assert pending_nodes == [node_ids[0], node_ids[2]], f"Expected pending nodes {[node_ids[0], node_ids[2]]} but got {pending_nodes}"


def test_reopen_state_manager():
    """
    Test that the state manager correctly reopens an existing database.
    """
    if DATABASE_FILE.exists():
        DATABASE_FILE.unlink()  # Ensure no pre-existing database file
    # First initialize and add nodes
    first_manager = CoyoteStateManager(db_path=DATABASE_FILE)
    first_manager.add_node_to_queue([501])
    first_manager.close()

    # Reopen the state manager and verify the data is still present
    second_manager = CoyoteStateManager(db_path=DATABASE_FILE)
    pending_nodes = second_manager.get_pending_nodes()
    assert pending_nodes == [501], f"Expected node [501] in reopened database, but got {pending_nodes}"
    second_manager.close()
