# coyote_nlp_state_manager.py

"""
coyote_nlp_state_manager.py

State manager for managing the sequence of NLP analysis processes and orchestrating tasks for the Coyote application.
"""

import logging
import sqlite3
import uuid
from time import sleep
from typing import Any
from coyote.utils.config_manager import get_event_data_db_connection
from coyote.analysis.scrape_webpage import scrape_webpage
from coyote.analysis.summarize_text import summarize_text
from coyote.analysis.nlp.text_ner_analysis import get_ner_from_text
from coyote.analysis.nlp.text_bertopic_analysis import get_topic_from_text
from coyote.analysis.relevance_calculator import calculate_relevance

# Get the logger for this module
logger = logging.getLogger(__name__)

class CoyoteNLPStateManager:
    """
    Manages the state and processing of user events in the Coyote application.
    """
    def __init__(self) -> None:
        self.conn = get_event_data_db_connection()
        self.cursor = self.conn.cursor()

    def process_pending_events(self) -> None:
        """
        Process pending events from the 'coyote_event_staging.db' to the 'coyote_event_data.db'.
        Perform NLP analysis and update statuses.
        """
        try:
            # Get pending events from staging
            self.cursor.execute('''
                SELECT * FROM Events WHERE status = 'pending'
                ORDER BY created_at ASC LIMIT 1
            ''')
            event = self.cursor.fetchone()
            if event:
                event_id, event_type = event[0], event[2]
                # Mark as processing
                self.update_event_status(event_id, 'processing')
                # Call appropriate NLP tasks based on event type
                self.perform_analysis(event_id, event_type, event)
                # Mark as processed
                self.update_event_status(event_id, 'processed')
            else:
                logger.info("No pending events to process.")
        except sqlite3.Error as e:
            logger.error(f"SQLite error during processing events: {e}", exc_info=True)
        finally:
            sleep(5)  # Poll every 5 seconds
            self.conn.commit()

    def update_event_status(self, event_id: str, status: str) -> None:
        """
        Update the status of an event in the database.
        """
        try:
            self.cursor.execute('''
                UPDATE Events SET status = ? WHERE event_id = ?
            ''', (status, event_id))
            self.conn.commit()
            logger.info(f"Updated event {event_id} status to {status}.")
        except sqlite3.Error as e:
            logger.error(f"SQLite error while updating status for event {event_id}: {e}", exc_info=True)

    def perform_analysis(self, event_id: str, event_type: str, event: Any) -> None:
        """
        Perform NLP analysis based on event type and update the database.
        """
        if event_type == 'User starts or modifies a search':
            purpose, search_terms = event[4], event[5]
            purpose_topics_data = get_topic_from_text(purpose)
            search_terms_topics_data = get_topic_from_text(search_terms)
            search_terms_relevance_score = calculate_relevance(purpose_topics_data, search_terms_topics_data)
            # Insert analysis results to coyote_event_data.db here

# Script Entry Point for Polling
if __name__ == '__main__':
    state_manager = CoyoteNLPStateManager()
    while True:
        state_manager.process_pending_events()