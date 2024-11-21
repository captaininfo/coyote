"""
'test_coyote_main_rw_sqlite.py'

This script's purpose is to verify that coyote_main.py can write data to the coyote_event_data.db
"""
from coyote.coyote_server import app  # Import the Flask app from your server script
from coyote.coyote_main import process_data_from_server
from datetime import datetime

# Use the Flask application context to test the function
with app.app_context():

    # Mock data for testing
    mock_data = {
        'timestamp': datetime.now().isoformat(),
        'event': 'User starts or modifies a search',
        'purpose': 'Learn Python',
        'searchTerms': 'Python tutorial for beginners',
        'dataSource': 'Coyote Browser Extension'
    }
    
    # Call the function and print the result
    result = process_data_from_server(mock_data)
    print(result)

