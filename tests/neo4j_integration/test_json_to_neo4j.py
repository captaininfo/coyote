# test_json_to_neo4j.py

import pytest
import json
from unittest.mock import patch, MagicMock, mock_open
from pathlib import Path
from coyote.neo4j_integration.json_to_neo4j import read_json, main


# Test for `read_json`
def test_read_json_success():
    # Simulate JSON content
    mock_json_content = {"key": "value"}
    mock_open_instance = mock_open(read_data=json.dumps(mock_json_content))
    
    with patch("coyote.neo4j_integration.json_to_neo4j.Path.open", mock_open_instance):
        file_path = Path("dummy_path.json")
        result = read_json(file_path)
        
        assert result == mock_json_content


def test_read_json_file_not_found():
    # Test for FileNotFoundError handling
    file_path = Path("non_existent_file.json")
    with patch("coyote.neo4j_integration.json_to_neo4j.Path.open", side_effect=FileNotFoundError):
        with pytest.raises(FileNotFoundError):
            read_json(file_path)


def test_read_json_invalid_format():
    # Test for JSONDecodeError handling
    mock_open_instance = mock_open(read_data="Invalid JSON")
    file_path = Path("invalid_json.json")

    with patch("coyote.neo4j_integration.json_to_neo4j.Path.open", mock_open_instance):
        with pytest.raises(json.JSONDecodeError):
            read_json(file_path)


# Test for `main`
@patch("coyote.neo4j_integration.json_to_neo4j.read_json", return_value=[
    {"dataSource": "Coyote Browser Extension", "field1": "value1"},
    {"dataSource": "Hypothesis", "field2": "value2"}
])
@patch("coyote.neo4j_integration.json_to_neo4j.process_coyote_browser_extension_data", return_value=(123, 456))
@patch("coyote.neo4j_integration.json_to_neo4j.process_annotation", return_value=789)
@patch("coyote.neo4j_integration.json_to_neo4j.CoyoteStateManager")
@patch("coyote.neo4j_integration.json_to_neo4j.GraphDatabase.driver")
@patch("coyote.neo4j_integration.json_to_neo4j.get_setting")
def test_main(
    mock_get_setting,
    mock_graph_database_driver,
    mock_coyote_state_manager,
    mock_process_annotation,
    mock_process_coyote_browser_extension_data,
    mock_read_json
):
    # Mock configuration settings
    mock_get_setting.side_effect = ["neo4j://localhost:7687", "neo4j_user", "neo4j_password"]

    # Mock database session and state manager
    mock_driver_instance = MagicMock()
    mock_graph_database_driver.return_value = mock_driver_instance
    mock_session = mock_driver_instance.session.return_value.__enter__.return_value

    # Mock state manager instance
    mock_state_manager_instance = mock_coyote_state_manager.return_value

    # Run the main function
    main()

    # Check that the Neo4j driver was initialized
    mock_graph_database_driver.assert_called_once_with("neo4j://localhost:7687", auth=("neo4j_user", "neo4j_password"))

    # Check that nodes were processed
    mock_process_coyote_browser_extension_data.assert_called_once_with(mock_session, {"dataSource": "Coyote Browser Extension", "field1": "value1"}, {"last_webpage_node_id": None, "last_search_terms_node_id": None})
    mock_process_annotation.assert_called_once_with(mock_session, {"dataSource": "Hypothesis", "field2": "value2"})

    # Check that nodes were added to the queue
    mock_state_manager_instance.add_node_to_queue.assert_called_once_with([123, 456, 789])

    # Check that the Neo4j driver was closed
    mock_driver_instance.close.assert_called_once()

    # Check that the state manager was closed
    mock_state_manager_instance.close.assert_called_once()
