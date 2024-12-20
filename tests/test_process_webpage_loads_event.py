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

class TestProcessWebpageLoadsEvent(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Create an in-memory SQLite database for testing
        cls.conn = sqlite3.connect(':memory:')
        cls.cursor = cls.conn.cursor()

        # Minimal schema setup required for process_webpage_loads_event to run
        schema_sql = '''
        CREATE TABLE Events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT UNIQUE NOT NULL,
            timestamp TEXT NOT NULL,
            event_type TEXT NOT NULL,
            data_source TEXT,
            processed INTEGER DEFAULT 0
        );

        CREATE TABLE WebpageLoads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT NOT NULL,
            url TEXT,
            wayback_machine_url TEXT,
            webpage_title TEXT,
            scraped_text TEXT,
            webpage_summary TEXT,
            webpage_relevance_score REAL,
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

        CREATE TABLE CorpusDocuments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            content TEXT,
            source TEXT,
            inserted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
        # Insert a mock event and webpage load event record
        self.event_id = 'test-webpage-event-123'
        self.cursor.execute("INSERT INTO Events (event_id, timestamp, event_type, data_source) VALUES (?, ?, ?, ?)",
                            (self.event_id, '2024-12-15T12:00:00Z', 'Webpage loads', 'TestSource'))
        self.cursor.execute("INSERT INTO WebpageLoads (event_id, url) VALUES (?, ?)",
                            (self.event_id, 'https://example.com/testpage'))
        self.cursor.execute("INSERT INTO EventTracking (event_id, status) VALUES (?, ?)",
                            (self.event_id, 'ready_for_nlp'))

        # Insert some corpus documents for TF-IDF testing
        corpus_docs = [
            ("Doc1", "This is a sample document for TF-IDF testing.", "TEDTalk"),
            ("Doc2", "Another sample text corpus entry for the TF-IDF calculations.", "TEDTalk"),
            ("Doc3", "More documents are needed to make TF-IDF meaningful.", "TEDTalk")
        ]
        self.cursor.executemany("INSERT INTO CorpusDocuments (title, content, source) VALUES (?, ?, ?)", corpus_docs)
        self.conn.commit()

        # Create a state manager with our test connection
        self.state_manager = CoyoteNLPStateManager()
        # Override the data_conn and data_cursor with our in-memory DB
        self.state_manager.data_conn = self.conn
        self.state_manager.data_cursor = self.conn.cursor()

    @patch('coyote.coyote_nlp_state_manager.scrape_webpage')
    @patch('coyote.coyote_nlp_state_manager.summarize_text')
    @patch('coyote.coyote_nlp_state_manager.analyze_topics')
    @patch('coyote.coyote_nlp_state_manager.extract_entities')
    @patch('coyote.coyote_nlp_state_manager.map_topics_to_wikidata')
    @patch('coyote.coyote_nlp_state_manager.map_ner_to_wikidata')
    @patch('coyote.coyote_nlp_state_manager.calculate_tfidf_on_phrases')
    @patch('coyote.coyote_nlp_state_manager.extract_and_replace_topics')
    @patch('coyote.coyote_nlp_state_manager.replace_named_entities_in_text')
    @patch('coyote.coyote_nlp_state_manager.CoyoteNLPStateManager.update_topics_with_wikidata')
    @patch('coyote.coyote_nlp_state_manager.CoyoteNLPStateManager.update_entities_with_wikidata')
    def test_process_webpage_loads_event(
        self,
        mock_update_entities,
        mock_update_topics,
        mock_replace_entities,
        mock_replace_topics,
        mock_calculate_tfidf,
        mock_map_ner,
        mock_map_topics,
        mock_extract_entities,
        mock_analyze_topics,
        mock_summarize_text,
        mock_scrape_webpage
    ):
        # Mock return values
        mock_scrape_webpage.return_value = "This is the scraped webpage main text about mental categories and theories."
        mock_summarize_text.return_value = "A short summary of the webpage content."
        mock_analyze_topics.return_value = (None, [("mental_categories", 1.0), ("theories", 0.8)])
        mock_extract_entities.return_value = [("mental categories", "NOUNPHRASE"), ("theories", "NOUN")]
        mock_map_topics.return_value = {
            "mental_categories": {"uri": "http://wikidata.org/Q1111", "label": "Concept"},
            "theories": {"uri": "http://wikidata.org/Q2222", "label": "Concept"}
        }
        mock_map_ner.return_value = {
            "mental_categories": {"uri": "http://wikidata.org/Q3333", "label": "Concept"},
            "theories": {"uri": "http://wikidata.org/Q4444", "label": "Concept"}
        }

        # TF-IDF mock returns scored terms
        mock_calculate_tfidf.return_value = {"mental_categories": 0.5, "theories": 0.4}
        mock_replace_topics.return_value = "This is the processed text with underscores."
        mock_replace_entities.return_value = "This is the processed text with entities replaced."

        # Mock update functions do nothing
        mock_update_topics.return_value = None
        mock_update_entities.return_value = None

        # Call the function under test
        self.state_manager.process_webpage_loads_event(self.event_id)

        # Verify that topics and entities were inserted
        self.cursor.execute("SELECT topic, wikidata_uri FROM Topics WHERE event_id=?", (self.event_id,))
        topics_rows = self.cursor.fetchall()
        self.assertGreater(len(topics_rows), 0, "No topics found after processing webpage loads event.")

        self.cursor.execute("SELECT entity, wikidata_uri FROM Entities WHERE event_id=?", (self.event_id,))
        entities_rows = self.cursor.fetchall()
        self.assertGreater(len(entities_rows), 0, "No entities found after processing webpage loads event.")

        # Check that mock update functions were called
        mock_update_topics.assert_called()
        mock_update_entities.assert_called()

        # Check TF-IDF calls and ensure that corpus was fetched
        mock_calculate_tfidf.assert_called()

        # Optionally verify TF-IDF scores were updated in database
        # Since we replaced underscores with spaces before updating, we expect "mental categories" and "theories" in DB
        self.cursor.execute("SELECT topic, score FROM Topics WHERE event_id=?", (self.event_id,))
        updated_topics = self.cursor.fetchall()
        # Expect some non-null scores if updates occurred
        self.assertTrue(any(r[1] is not None for r in updated_topics), "No TF-IDF scores updated for topics.")

        self.cursor.execute("SELECT entity, score FROM Entities WHERE event_id=?", (self.event_id,))
        updated_entities = self.cursor.fetchall()
        # Expect some non-null scores for entities if that step was implemented
        self.assertTrue(any(r[1] is not None for r in updated_entities), "No TF-IDF scores updated for entities.")

if __name__ == '__main__':
    unittest.main()
