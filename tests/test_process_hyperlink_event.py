# tests/test_process_hyperlink_event.py

import unittest
import sqlite3
import logging
import sys
import os
from unittest.mock import patch, MagicMock

# Add the parent directory to sys.path to locate the 'coyote' module
parent_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if parent_dir not in sys.path:
    sys.path.insert(0, parent_dir)

from coyote.coyote_nlp_state_manager import CoyoteNLPStateManager

# Setup basic logging for the test
logging.basicConfig(level=logging.DEBUG)

class TestProcessHyperlinkEvent(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Create an in-memory SQLite database for testing
        cls.conn = sqlite3.connect(':memory:')
        cls.cursor = cls.conn.cursor()

        # Minimal schema setup required for process_hyperlink_event to run
        schema_sql = '''
        CREATE TABLE Events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT UNIQUE NOT NULL,
            timestamp TEXT NOT NULL,
            event_type TEXT NOT NULL,
            data_source TEXT,
            processed INTEGER DEFAULT 0
        );

        CREATE TABLE HyperlinkClicks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT NOT NULL,
            source_url TEXT,
            destination_url TEXT,
            link_text TEXT,
            FOREIGN KEY(event_id) REFERENCES Events(event_id)
        );

        CREATE TABLE Entities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT NOT NULL,
            entity_context TEXT NOT NULL,
            entity TEXT NOT NULL,
            wikidata_uri TEXT,
            label TEXT,
            score REAL,
            FOREIGN KEY(event_id) REFERENCES Events(event_id)
        );

        CREATE TABLE Topics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT NOT NULL,
            topic_context TEXT NOT NULL,
            topic TEXT NOT NULL,
            wikidata_uri TEXT,
            label TEXT,
            score REAL,
            FOREIGN KEY(event_id) REFERENCES Events(event_id)
        );

        CREATE TABLE EventTracking (
            event_id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            last_step TEXT DEFAULT NULL,
            error_message TEXT DEFAULT NULL,
            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX idx_events_event_id ON Events(event_id);
        '''
        cls.cursor.executescript(schema_sql)
        cls.conn.commit()

    def setUp(self):
        # Insert a mock event and hyperlink event record
        self.event_id = 'test-hyperlink-event-123'
        self.cursor.execute("INSERT INTO Events (event_id, timestamp, event_type, data_source) VALUES (?, ?, ?, ?)",
                            (self.event_id, '2024-12-15T12:00:00Z', 'User clicks hyperlink', 'TestSource'))
        self.cursor.execute("INSERT INTO HyperlinkClicks (event_id, source_url, destination_url, link_text) VALUES (?, ?, ?, ?)",
                            (self.event_id, 'https://example.com/source', 'https://example.com/dest', 'theories mental categories link'))
        self.cursor.execute("INSERT INTO EventTracking (event_id, status) VALUES (?, ?)",
                            (self.event_id, 'ready_for_nlp'))
        self.conn.commit()

        # Create a state manager with our test connection
        self.state_manager = CoyoteNLPStateManager()
        # Override the data_conn and data_cursor with our in-memory DB
        self.state_manager.data_conn = self.conn
        self.state_manager.data_cursor = self.conn.cursor()

    # Correctly patch 'extract_topics_with_rake' as a standalone function
    @patch('coyote.coyote_nlp_state_manager.extract_topics_with_rake')
    @patch('coyote.coyote_nlp_state_manager.extract_entities')
    @patch('coyote.coyote_nlp_state_manager.map_topics_to_wikidata')
    @patch('coyote.coyote_nlp_state_manager.map_ner_to_wikidata')
    @patch('coyote.coyote_nlp_state_manager.CoyoteNLPStateManager.update_topics_with_wikidata')
    @patch('coyote.coyote_nlp_state_manager.CoyoteNLPStateManager.update_entities_with_wikidata')
    def test_process_hyperlink_event(
        self,
        mock_update_entities,
        mock_update_topics,
        mock_map_ner,
        mock_map_topics,
        mock_extract_entities,
        mock_extract_topics
    ):
        # Mock the NLP functions to return predictable results
        mock_extract_topics.return_value = {"topics_with_weights": [("theories", 1.0), ("mental categories", 0.8)]}

        # extract_entities returns List[Tuple[str, str]]
        mock_extract_entities.return_value = [("theories", "NOUN"), ("mental categories", "NOUNPHRASE")]

        mock_map_topics.return_value = {
            "theories": {"uri": "http://wikidata.org/Q1234", "label": "Concept"},
            "mental categories": {"uri": "http://wikidata.org/Q2345", "label": "Concept"}
        }

        mock_map_ner.return_value = {
            "theories": {"replacement": "theories", "uri": "http://wikidata.org/Q9999", "label": "Concept"},
            "mental categories": {"replacement": "mental_categories", "uri": "http://wikidata.org/Q8888", "label": "Concept"}
        }

        # Mock update functions do nothing
        mock_update_topics.return_value = None
        mock_update_entities.return_value = None

        # Call the function under test
        self.state_manager.process_hyperlink_event(self.event_id)

        # Verify that topics and entities were inserted
        self.cursor.execute("SELECT topic, wikidata_uri FROM Topics WHERE event_id=?", (self.event_id,))
        topics_rows = self.cursor.fetchall()
        self.assertGreater(len(topics_rows), 0, "No topics found after processing hyperlink event.")

        self.cursor.execute("SELECT entity, wikidata_uri FROM Entities WHERE event_id=?", (self.event_id,))
        entities_rows = self.cursor.fetchall()
        self.assertGreater(len(entities_rows), 0, "No entities found after processing hyperlink event.")

        # Check that mock update functions were called
        mock_update_topics.assert_called()
        mock_update_entities.assert_called()

        # Optionally verify database updates as needed

if __name__ == '__main__':
    unittest.main()
