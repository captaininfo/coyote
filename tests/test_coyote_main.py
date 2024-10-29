# test_coyote_main.py

import pytest
import json
from pathlib import Path
from unittest.mock import patch, mock_open
from coyote.coyote_main import (
    append_to_json_file,
    process_hypothesis_annotations,
    is_google_serp,
    process_data_from_server,
)

# Define test data
TEST_ANALYSIS_FILE = Path("data/test_analysis_result.json")

@pytest.fixture
def mock_logger():
    with patch("coyote.coyote_main.logger") as mock_logger:
        yield mock_logger


# Test append_to_json_file
def test_append_to_json_file(mock_logger):
    data = {"test": "value"}

    # Use mock_open to simulate file handling with Path.open
    m = mock_open(read_data="[]")
    with patch("pathlib.Path.open", m), patch("json.dump") as mock_json_dump:
        append_to_json_file(TEST_ANALYSIS_FILE, data)

        # Verify that json.dump is called with the correct data
        mock_json_dump.assert_called_once_with([data], m(), indent=4)

    # Now, test appending data to an existing file
    m = mock_open(read_data=json.dumps([{"existing": "data"}]))
    with patch("pathlib.Path.open", m), patch("json.dump") as mock_json_dump:
        append_to_json_file(TEST_ANALYSIS_FILE, data)
        
        # Check that data is appended correctly
        mock_json_dump.assert_called_once_with(
            [{"existing": "data"}, data], m(), indent=4
        )


# Test process_hypothesis_annotations
@patch("coyote.coyote_main.append_to_json_file")
@patch("coyote.coyote_main.get_topic_from_text", return_value={"topics_with_weights": {}, "mapped_topics": []})
@patch("coyote.coyote_main.get_ner_from_text", return_value={"topics_with_weights": {}, "mapped_topics": []})
def test_process_hypothesis_annotations(mock_get_ner, mock_get_topic, mock_append, mock_logger):
    # Sample Hypothesis annotation data
    annotations = [{
        "created": "2023-01-01T12:00:00Z",
        "uri": "https://example.com",
        "document": {"title": ["Example Title"]},
        "id": "annotation_id_1",
        "text": "Sample annotation text",
        "target": [{"selector": [{"type": "TextQuoteSelector", "exact": "Sample highlight"}]}],
        "permissions": {"read": ["group:__world__"]},
        "tags": ["test"],
        "user": "test_user",
        "group": "test_group",
    }]

    process_hypothesis_annotations(annotations)

    # Check the append_to_json_file call
    mock_append.assert_called_once()
    args, _ = mock_append.call_args
    data = args[1]

    # Validate the fields in the appended data
    assert data["timestamp"] == "2023-01-01T12:00:00Z"
    assert data["url"] == "https://example.com"
    assert data["webpageTitle"] == "Example Title"
    assert data["annotationID"] == "annotation_id_1"
    assert data["annotationText"] == "Sample annotation text"
    assert data["highlightedText"] == "Sample highlight"
    assert data["visibility"] == "public"


# Test is_google_serp
@pytest.mark.parametrize("url, expected", [
    ("https://www.google.com/search?q=pytest", True),
    ("https://example.com", False),
    ("https://www.google.com/search?query=test", True),
])
def test_is_google_serp(url, expected):
    assert is_google_serp(url) == expected


# Test process_data_from_server with different events
@patch("coyote.coyote_main.append_to_json_file")
@patch("coyote.coyote_main.get_topic_from_text", return_value={"topics_with_weights": {}, "mapped_topics": []})
@patch("coyote.coyote_main.get_ner_from_text", return_value={"topics_with_weights": {}, "mapped_topics": []})
@patch("coyote.coyote_main.calculate_relevance", return_value=0.5)
@patch("coyote.coyote_main.scrape_webpage", return_value="Sample webpage content")
@patch("coyote.coyote_main.summarize_text", return_value="Sample summary")
def test_process_data_from_server(
    mock_summarize, mock_scrape, mock_relevance, mock_get_ner, mock_get_topic, mock_append, mock_logger
):
    # Test with "User starts or modifies a search" event
    search_event = {
        "timestamp": "2023-01-01T12:00:00Z",
        "event": "User starts or modifies a search",
        "purpose": "Learning pytest",
        "searchTerms": "pytest tutorial",
        "dataSource": "Coyote Browser Extension"
    }

    response = process_data_from_server(search_event)
    assert response["status"] == "success"
    assert response["message"] == "Data processed and stored."
    mock_append.assert_called_once()

    # Test with "Webpage loads" event that is not a SERP
    mock_append.reset_mock()
    webpage_event = {
        "timestamp": "2023-01-01T12:00:00Z",
        "event": "Webpage loads",
        "url": "https://example.com",
        "title": "Example Title",
    }

    response = process_data_from_server(webpage_event)
    assert response["status"] == "success"
    assert response["message"] == "Data processed and stored."
    mock_append.assert_called_once()

    # Test with "user clicks hyperlink" event
    mock_append.reset_mock()
    hyperlink_event = {
        "timestamp": "2023-01-01T12:00:00Z",
        "event": "user clicks hyperlink",
        "sourceURL": "https://source.com",
        "destinationURL": "https://destination.com",
        "linkText": "Click here"
    }

    response = process_data_from_server(hyperlink_event)
    assert response["status"] == "success"
    assert response["message"] == "Data processed and stored."
    mock_append.assert_called_once()

    # Check data structure in appended data for hyperlink event
    args, _ = mock_append.call_args
    data = args[1]
    assert data["sourceURL"] == "https://source.com"
    assert data["destinationURL"] == "https://destination.com"
    assert data["linkText"] == "Click here"
