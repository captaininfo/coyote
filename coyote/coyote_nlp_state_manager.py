"""
coyote_nlp_state_manager.py

State manager for managing the sequence of NLP analysis processes and orchestrating tasks for the Coyote application.
"""

import logging
import sqlite3
from time import sleep
from threading import Lock
from typing import Any
from coyote.utils.config_manager import get_event_data_db_connection
from coyote.utils.event_data_handler import (
    fetch_next_event,
    insert_event,
    insert_event_tracking,
    insert_event_specific_data,
    mark_event_ready_for_nlp,
    is_event_processing
)
from coyote.analysis.scrape_webpage import scrape_webpage
from coyote.analysis.summarize_text import summarize_text
from coyote.analysis.nlp.text_ner_analysis import get_ner_from_text
from coyote.analysis.nlp.text_bertopic_analysis import get_topic_from_text
from coyote.analysis.relevance_calculator import calculate_relevance
from coyote.utils import config_manager

# Get logger
logger = logging.getLogger(__name__)

class CoyoteNLPStateManager:
    """
    Manages the state and processing of user events in the Coyote application.
    """
    def __init__(self) -> None:
        self.data_conn = get_event_data_db_connection()
        self.data_cursor = self.data_conn.cursor()

    def poll_and_process_events(self) -> None:
        """
        Periodically poll for new events and process them.
        """
        while True:
            try:
                # Ensure no event is currently processing
                with config_manager.event_data_db_lock:
                    if is_event_processing(self.data_conn):
                        logger.info("Another event is still processing. Waiting...")
                        sleep(15)
                        continue

                # Fetch a new event from the coyote_event_staging database
                event = fetch_next_event()
                if event:
                    event_id = event['event_id']
                    event_dict = dict(event)
                    logger.info(f"Processing event_id: {event_id}.")
                    logger.debug(f"Payload of event_dict: {event_dict}.")

                    # Insert the base event into the Events table
                    with config_manager.event_data_db_lock:
                        if insert_event(self.data_conn, event_id, event_dict):
                            logger.info(f"Successfully inserted event_id {event_id} into Events table.")

                            # Track the event lifecycle in the EventTracking table
                            if insert_event_tracking(self.data_conn, event_id):
                                logger.info(f"Successfully inserted event_id {event_id} into EventTracking table.")

                                # Insert the event-type-specific data into event-type-specific tables
                                insert_event_specific_data(self.data_conn, event_dict)
                                logger.info(f"Successfully inserted event-specific data for event_id {event_id}.")

                                # Now mark the event as ready_for_nlp
                                mark_event_ready_for_nlp(self.data_conn, event_id)
                                logger.info(f"Marked event_id {event_id} as 'ready_for_nlp'.")
                            else:
                                logger.error(f"Failed to track event_id {event_id} in EventTracking.")
                                # Depending on your logic, you might continue or break here.
                        else:
                            logger.error(f"Failed to insert event_id {event_id} into Events table. Skipping processing.")
                            # Depending on your logic, you might continue or break here.

                    # Process the event
                    # self.process_event(event_id, dict(event))
                else:
                    logger.info("No new events to process.")

                sleep(15)  # Poll interval
            except Exception as e:
                logger.error(f"Error during event polling or processing: {e}", exc_info=True)



    def perform_analysis(self, event_id: str, event_type: str, event: Any) -> None:
        """
        Perform analyses on the event based on its type.
        """
        try:
            if event_type == 'User starts or modifies a search':
                purpose, search_terms = event[4], event[5]

                # Perform analyses
                purpose_topics = get_topic_from_text(purpose)
                search_terms_topics = get_topic_from_text(search_terms)
                relevance_score = calculate_relevance(purpose_topics, search_terms_topics)

                # Write results back to the data database
                with self.lock:
                    self.data_cursor.execute('''
                        UPDATE EventData
                        SET analysis_results = ?, status = 'complete'
                        WHERE event_id = ?
                    ''', (str({
                        "purpose_topics": purpose_topics,
                        "search_terms_topics": search_terms_topics,
                        "relevance_score": relevance_score
                    }), event_id))
                    self.data_conn.commit()

                logger.info(f"Analysis results for event_id {event_id} written to database.")
            else:
                logger.warning(f"Unknown event_type {event_type} for event_id {event_id}. Skipping.")

        except Exception as e:
            logger.error(f"Error during analysis for event_id {event_id}: {e}", exc_info=True)
            self.update_event_status(event_id, 'failed')

# Entry point for the script
if __name__ == '__main__':
    state_manager = CoyoteNLPStateManager()
    state_manager.process_pending_events()
