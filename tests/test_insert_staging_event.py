import unittest
import sqlite3
import logging
from datetime import datetime
from pathlib import Path
from coyote.coyote_event_writer import insert_staging_event
from coyote.utils.config_manager import get_staging_db_connection

# Constants for database and paths
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / 'data'
STAGING_DB_FILE = DATA_DIR / 'coyote_event_staging.db'

# Configure logging
LOG_FILE = 'test_insert_staging_event.log'
logging.basicConfig(
    filename=LOG_FILE,  # Output to a log file
    level=logging.DEBUG,  # Set log level to DEBUG
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class TestInsertStagingEvent(unittest.TestCase):

    def setUp(self) -> None:
        """
        Set up test environment, connect to the database, and ensure the table is empty before each test.
        """
        logger.info("Setting up test environment.")
        self.conn = sqlite3.connect(STAGING_DB_FILE)
        self.conn.row_factory = sqlite3.Row
        self.cursor = self.conn.cursor()

        # Clear the EventStaging table before each test to ensure a clean slate
        self.cursor.execute("DELETE FROM EventStaging")
        self.conn.commit()

    def tearDown(self) -> None:
        """
        Tear down the test environment by closing the database connection.
        """
        logger.info("Tearing down test environment.")
        if self.conn:
            self.conn.close()

    def test_basic_insertion(self):
        """
        Test if insert_staging_event successfully inserts data into the EventStaging table.
        """
        logger.info("Running test_basic_insertion.")
        # Dummy event data
        event_data = {
            "event_id": "test_event_1",
            "event": "User starts or modifies a search",
            "timestamp": datetime.now().isoformat(),
            "dataSource": "Coyote Browser Extension",
            "purpose": "Testing",
            "search_terms": "Unit Test",
            "event_payload": {"key": "value"}
        }

        try:
            # Insert the event using a direct connection
            insert_staging_event(event_data, test_conn=self.conn)
        except Exception as e:
            logger.error("Error occurred in test_basic_insertion: %s", e, exc_info=True)
            raise

        # Verify if the data was inserted correctly
        self.cursor.execute("SELECT * FROM EventStaging WHERE event_id = ?", (event_data['event_id'],))
        row = self.cursor.fetchone()
        self.assertIsNotNone(row, "Row was not found in EventStaging table.")
        self.assertEqual(row['event_id'], event_data['event_id'], "Inserted event_id does not match.")
        self.assertEqual(row['purpose'], event_data['purpose'], "Inserted purpose does not match.")

    def test_missing_fields(self):
        """
        Test if insert_staging_event handles missing optional fields.
        """
        logger.info("Running test_missing_fields.")
        # Missing optional fields
        event_data = {
            "event_id": "test_event_2",
            "event": "Webpage loads",
            "timestamp": datetime.now().isoformat(),
            "dataSource": "Coyote Browser Extension"
            # No optional fields like 'purpose', 'search_terms'
        }

        try:
            # Insert the event
            insert_staging_event(event_data, test_conn=self.conn)
        except Exception as e:
            logger.error("Error occurred in test_missing_fields: %s", e, exc_info=True)
            raise

        # Verify if the data was inserted correctly
        try:
            self.cursor.execute("SELECT * FROM EventStaging WHERE event_id = ?", (event_data['event_id'],))
            row = self.cursor.fetchone()
            self.assertIsNotNone(row, "Row was not found in EventStaging table.")
            self.assertEqual(row['event_id'], event_data['event_id'], "Inserted event_id does not match.")
            self.assertIsNone(row['purpose'], "Purpose should be None for missing optional field.")
        except sqlite3.Error as e:
            logger.error("SQLite error during verification in test_missing_fields: %s", e, exc_info=True)
            raise

    def test_incorrect_data_types(self):
        """
        Test if insert_staging_event handles incorrect data types gracefully.
        """
        logger.info("Running test_incorrect_data_types.")
        # Incorrect data type for timestamp (int instead of string)
        event_data = {
            "event_id": "test_event_3",
            "event": "User annotated webpage",
            "timestamp": 12345,  # Incorrect data type
            "dataSource": "Coyote Browser Extension"
        }

        try:
            # Insert the event and check for error handling
            with self.assertRaises(Exception):
                insert_staging_event(event_data, test_conn=self.conn)
        except Exception as e:
            logger.error("Error occurred in test_incorrect_data_types: %s", e, exc_info=True)
            raise

    def test_large_payload(self):
        """
        Test insert_staging_event with a large payload to check system robustness.
        """
        logger.info("Running test_large_payload.")
        large_text = "a" * 10000  # Large string for payload
        event_data = {
            "event_id": "test_event_4",
            "event": "User annotated webpage",
            "timestamp": datetime.now().isoformat(),
            "dataSource": "Coyote Browser Extension",
            "annotation_text": large_text,
            "event_payload": {"large_key": large_text}
        }

        try:
            # Insert the event
            insert_staging_event(event_data, test_conn=self.conn)
        except Exception as e:
            logger.error("Error occurred in test_large_payload: %s", e, exc_info=True)
            raise

        # Verify if the data was inserted correctly
        try:
            self.cursor.execute("SELECT * FROM EventStaging WHERE event_id = ?", (event_data['event_id'],))
            row = self.cursor.fetchone()
            self.assertIsNotNone(row, "Row was not found in EventStaging table.")
            self.assertEqual(row['annotation_text'], large_text, "Inserted annotation_text does not match.")
        except sqlite3.Error as e:
            logger.error("SQLite error during verification in test_large_payload: %s", e, exc_info=True)
            raise

    def test_connection_management(self):
        """
        Test if connections are being correctly opened and closed.
        """
        logger.info("Running test_connection_management.")
        event_data = {
            "event_id": "test_event_5",
            "event": "User starts or modifies a search",
            "timestamp": datetime.now().isoformat(),
            "dataSource": "Coyote Browser Extension"
        }

        try:
            # Insert event to test connection management
            insert_staging_event(event_data, test_conn=self.conn)
        except Exception as e:
            logger.error("Error occurred during insertion in test_connection_management: %s", e, exc_info=True)
            raise

        # Verify the connection can still be opened
        try:
            conn = get_staging_db_connection(test_mode=True)
            self.assertIsNotNone(conn, "Failed to establish a connection to the staging database.")
            logger.info("Connection established successfully in test_connection_management.")
        except sqlite3.Error as e:
            logger.error("Error establishing connection in test_connection_management: %s", e, exc_info=True)
            raise
        finally:
            if conn:
                conn.close()

if __name__ == '__main__':
    unittest.main()
