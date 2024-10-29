import pytest
import json
from unittest.mock import patch, MagicMock
from datetime import datetime
from coyote.coyote_server import app, get_latest_timestamp

# Create a test client using Flask's testing functionality
@pytest.fixture
def client():
    app.config['TESTING'] = True
    with app.test_client() as client:
        yield client

# Test /configure endpoint (GET request)
def test_configure_get(client):
    response = client.get('/configure')
    assert response.status_code == 200
    assert b"Configure" in response.data  # Check if 'Configure' text is in the returned HTML

# Test /configure endpoint (POST request with missing fields)
def test_configure_post_missing_fields(client):
    response = client.post('/configure', data={})
    followup_response = client.get('/configure', follow_redirects=True)
    assert response.status_code == 302  # Expect redirect
    assert b'Neo4j credentials are required.' in followup_response.data

# Test /configure endpoint (POST request with complete data)
@patch("coyote.coyote_server.store_setting")  # Ensure correct path to patch
def test_configure_post_complete_data(mock_store_setting, client):
    form_data = {
        "hypothesis_username": "test_user",
        "hypothesis_token": "test_token",
        "neo4j_uri": "neo4j://localhost:7687",
        "neo4j_username": "neo4j",
        "neo4j_password": "password"
    }
    response = client.post('/configure', data=form_data)
    assert response.status_code == 302  # Check for redirection
    mock_store_setting.assert_any_call('neo4j_uri', "neo4j://localhost:7687")  # Ensure setting was stored

# Test get_latest_timestamp with various cases
@patch("coyote.coyote_server.DATA_DIR")
def test_get_latest_timestamp(mock_data_dir):
    mock_open = MagicMock()
    mock_data_dir.__truediv__.return_value.open = mock_open
    
    # Case 1: Simulate file missing
    mock_open.side_effect = FileNotFoundError
    assert get_latest_timestamp() is None
    
    # Reset side effect and case 2: Empty content
    mock_open.side_effect = None
    mock_open.return_value.__enter__.return_value.read.return_value = "[]"
    assert get_latest_timestamp() is None

    # Case 3: Valid content with timestamps
    mock_open.return_value.__enter__.return_value.read.return_value = json.dumps([
        {"dataSource": "Hypothesis", "timestamp": "2023-01-01T12:00:00"},
        {"dataSource": "Hypothesis", "timestamp": "2023-01-02T12:00:00"}
    ])
    assert get_latest_timestamp() == "2023-01-02T12:00:00"


# Test process_request_data helper function
@patch("coyote.coyote_server.process_data_from_server", return_value={"status": "success"})
def test_process_request_data(mock_process_data_from_server, client):
    data = {"test": "data"}
    expected_data = {"test": "data", "event": "User starts or modifies a search", "dataSource": "Coyote Browser Extension"}
    
    response = client.post("/init_search", json=data)
    assert response.status_code == 200
    mock_process_data_from_server.assert_called_once_with(expected_data)

# Test /init_search endpoint
def test_init_search(client):
    response = client.post('/init_search', json={"searchTerms": "pytest", "timestamp": datetime.now().isoformat()})
    assert response.status_code == 200

# Test /webpage_visit endpoint
def test_webpage_visit(client):
    response = client.post('/webpage_visit', json={"url": "http://example.com", "timestamp": datetime.now().isoformat()})
    assert response.status_code == 200

# Test /hyperlink_click endpoint with valid data
def test_hyperlink_click_valid(client):
    data = {
        "sourceURL": "http://example.com/source",
        "destinationURL": "http://example.com/destination",
        "linkText": "example link",
        "timestamp": datetime.now().isoformat()
    }
    response = client.post('/hyperlink_click', json=data)
    assert response.status_code == 200

# Test /hyperlink_click endpoint with missing fields
def test_hyperlink_click_missing_fields(client):
    data = {"sourceURL": "http://example.com/source"}
    response = client.post('/hyperlink_click', json=data)
    assert response.status_code == 400
    assert b"Missing data for keys" in response.data

# Test /fetch_hypothesis_data (successful fetch)
@patch("coyote.coyote_server.requests.get")
@patch("coyote.coyote_server.process_data_from_server")
@patch("coyote.coyote_server.get_latest_timestamp", return_value=None)
@patch("coyote.coyote_server.load_credentials", return_value={"username": "test_user", "token": "test_token"})
def test_fetch_hypothesis_data_success(_, __, mock_process_data_from_server, mock_requests_get, client):
    mock_response = MagicMock()
    mock_response.json.return_value = {"rows": [{"annotation": "test"}]}
    mock_response.status_code = 200
    mock_requests_get.return_value = mock_response

    response = client.get('/fetch_hypothesis_data')
    assert response.status_code == 302  # Redirect after successful fetch
    assert mock_process_data_from_server.called
    mock_requests_get.assert_called_once_with(
        'https://api.hypothes.is/api/search',
        headers={'Authorization': 'Bearer test_token', 'Content-Type': 'application/json', 'Accept': 'application/json'},
        params={'user': 'acct:test_user@hypothes.is', 'limit': 200}
    )

# Test /fetch_hypothesis_data with missing credentials
@patch("coyote.coyote_server.load_credentials", return_value=None)
def test_fetch_hypothesis_data_missing_credentials(mock_load_credentials, client):
    response = client.get('/fetch_hypothesis_data', follow_redirects=True)  # Follow the redirect
    assert response.status_code == 200  # Expect OK after redirection to /configure
    assert b"Hypothes.is credentials not found." in response.data  # Check for expected message

