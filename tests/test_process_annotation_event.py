# tests/test_process_annotation_event.py

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

class TestProcessAnnotationEvent(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Create an in-memory SQLite database for testing
        cls.conn = sqlite3.connect(':memory:')
        cls.cursor = cls.conn.cursor()

        # Minimal schema setup required for process_annotation_event to run
        schema_sql = '''
        CREATE TABLE Events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT UNIQUE NOT NULL,
            timestamp TEXT NOT NULL,
            event_type TEXT NOT NULL,
            data_source TEXT,
            processed INTEGER DEFAULT 0
        );

        CREATE TABLE Annotations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT NOT NULL,
            url TEXT NOT NULL,
            webpage_title TEXT,
            annotation_id TEXT UNIQUE NOT NULL,
            annotation_text TEXT,
            highlighted_text TEXT,
            user_account TEXT,
            group_id TEXT,
            visibility TEXT,
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
        # Insert a mock event and annotation event record
        self.event_id = 'test-annotation-event-123'
        self.cursor.execute("INSERT INTO Events (event_id, timestamp, event_type, data_source) VALUES (?, ?, ?, ?)",
                            (self.event_id, '2024-12-15T12:00:00Z', 'User annotated webpage', 'TestSource'))

        # Provide a short annotation text (less than 50 words) to trigger RAKE
        annotation_text = "mental categories theories"
        highlighted_text = "theories mental"
        self.cursor.execute("""
            INSERT INTO Annotations (event_id, url, annotation_id, annotation_text, highlighted_text) 
            VALUES (?, ?, ?, ?, ?)
        """, (self.event_id, 'https://example.com/page', 'anno-123', annotation_text, highlighted_text))
        self.cursor.execute("INSERT INTO EventTracking (event_id, status) VALUES (?, ?)",
                            (self.event_id, 'ready_for_nlp'))
        self.conn.commit()

        # Create a state manager with our test connection
        self.state_manager = CoyoteNLPStateManager()
        # Override the data_conn and data_cursor with our in-memory DB
        self.state_manager.data_conn = self.conn
        self.state_manager.data_cursor = self.conn.cursor()

    # Correctly patch 'extract_topics_with_rake' as a standalone function
    @patch('coyote.coyote_nlp_state_manager.extract_entities')
    @patch('coyote.coyote_nlp_state_manager.map_topics_to_wikidata')
    @patch('coyote.coyote_nlp_state_manager.map_ner_to_wikidata')
    @patch('coyote.coyote_nlp_state_manager.CoyoteNLPStateManager.update_topics_with_wikidata')
    @patch('coyote.coyote_nlp_state_manager.CoyoteNLPStateManager.update_entities_with_wikidata')
    @patch('coyote.coyote_nlp_state_manager.analyze_topics')
    @patch('coyote.coyote_nlp_state_manager.extract_and_replace_topics')
    @patch('coyote.coyote_nlp_state_manager.replace_named_entities_in_text')
    def test_process_annotation_event_rake(
        self,
        mock_replace_entities,
        mock_replace_topics,
        mock_analyze_topics,
        mock_update_entities,
        mock_update_topics,
        mock_map_ner,
        mock_map_topics,
        mock_extract_entities
    ):
        # For RAKE scenario, we never reach analyze_topics because less than 50 words are present
        # But mock_analyze_topics just in case
        mock_analyze_topics.return_value = (None, [])

        # Mock extract_entities returns a simple list of entities
        mock_extract_entities.return_value = [("mental categories", "NOUNPHRASE"), ("theories", "NOUN")]

        # Mock map_topics_to_wikidata
        mock_map_topics.return_value = {
            "mental categories": {"uri": "http://wikidata.org/Q2345", "label": "Concept"},
            "theories": {"uri": "http://wikidata.org/Q1234", "label": "Concept"}
        }

        # Mock map_ner_to_wikidata
        mock_map_ner.return_value = {
            "mental categories": {"replacement": "mental_categories", "uri": "http://wikidata.org/Q8888", "label": "Concept"},
            "theories": {"replacement": "theories", "uri": "http://wikidata.org/Q9999", "label": "Concept"}
        }

        # Mock update functions do nothing
        mock_update_topics.return_value = None
        mock_update_entities.return_value = None

        # For RAKE extraction, mock 'extract_topics_with_rake' correctly
        with patch('coyote.coyote_nlp_state_manager.extract_topics_with_rake') as mock_extract_topics_rake:
            mock_extract_topics_rake.return_value = {
                "topics_with_weights": [("mental categories", 1.0), ("theories", 0.8)]
            }

            # Call the function under test
            self.state_manager.process_annotation_event(self.event_id)

        # Verify that topics and entities were inserted
        self.cursor.execute("SELECT topic, wikidata_uri FROM Topics WHERE event_id=?", (self.event_id,))
        topics_rows = self.cursor.fetchall()
        self.assertGreater(len(topics_rows), 0, "No topics found after processing annotation event (RAKE).")

        self.cursor.execute("SELECT entity, wikidata_uri FROM Entities WHERE event_id=?", (self.event_id,))
        entities_rows = self.cursor.fetchall()
        self.assertGreater(len(entities_rows), 0, "No entities found after processing annotation event (RAKE).")

        # Check that mock update functions were called
        mock_update_topics.assert_called()
        mock_update_entities.assert_called()

        # Optionally, verify the contents of the inserted topics and entities
        expected_topics = [
            ("mental categories", "http://wikidata.org/Q2345"),
            ("theories", "http://wikidata.org/Q1234")
        ]
        for topic, uri in expected_topics:
            self.cursor.execute(
                "SELECT wikidata_uri FROM Topics WHERE event_id=? AND topic=?",
                (self.event_id, topic)
            )
            result = self.cursor.fetchone()
            self.assertIsNotNone(result, f"Topic '{topic}' not found in Topics table.")
            self.assertEqual(result[0], uri, f"Incorrect URI for topic '{topic}'. Expected: {uri}, Found: {result[0]}")

        expected_entities = [
            ("mental_categories", "http://wikidata.org/Q8888"),
            ("theories", "http://wikidata.org/Q9999")
        ]
        for entity, uri in expected_entities:
            self.cursor.execute(
                "SELECT wikidata_uri FROM Entities WHERE event_id=? AND entity=?",
                (self.event_id, entity)
            )
            result = self.cursor.fetchone()
            self.assertIsNotNone(result, f"Entity '{entity}' not found in Entities table.")
            self.assertEqual(result[0], uri, f"Incorrect URI for entity '{entity}'. Expected: {uri}, Found: {result[0]}")

    if __name__ == '__main__':
        unittest.main()
