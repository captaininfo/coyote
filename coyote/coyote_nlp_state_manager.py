"""
coyote_nlp_state_manager.py

State manager for managing the sequence of NLP analysis processes and orchestrating tasks for the Coyote application.
"""

import logging
import sqlite3
from time import sleep
from threading import Lock
from typing import Any, List, Optional
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
from coyote.analysis.nlp.extract_topics_with_rake import extract_topics_with_rake
from coyote.analysis.nlp.text_ner_analysis import extract_entities, map_ner_to_wikidata
from coyote.analysis.nlp.text_bertopic_analysis import get_topic_from_text, map_topics_to_wikidata
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
        Also checks for events that are 'ready_for_nlp' and dispatches them to appropriate NLP functions.
        """
        while True:
            try:
                # Ensure no event is currently processing for data insertion phase
                with config_manager.event_data_db_lock:
                    if is_event_processing(self.data_conn):
                        logger.info("Another event is still processing. Waiting...")
                        sleep(15)
                        continue

                # Step 1: Fetch and process a new event from the coyote_event_staging.db
                event = fetch_next_event()
                if event:
                    event_id = event['event_id']
                    event_dict = dict(event)
                    logger.info(f"Processing event_id: {event_id} from staging.")
                    logger.debug(f"Payload of event_dict: {event_dict}.")

                    # Insert the event into the Events and EventTracking tables
                    with config_manager.event_data_db_lock:
                        if insert_event(self.data_conn, event_id, event_dict):
                            logger.info(f"Successfully inserted event_id {event_id} into Events table.")

                            # Track the event lifecycle in the EventTracking table
                            if insert_event_tracking(self.data_conn, event_id):
                                logger.info(f"Successfully inserted event_id {event_id} into EventTracking table.")

                                # Insert event-type-specific data
                                insert_event_specific_data(self.data_conn, event_dict)
                                logger.info(f"Successfully inserted event-specific data for event_id {event_id}.")

                                # Mark event as ready_for_nlp
                                mark_event_ready_for_nlp(self.data_conn, event_id)
                                logger.info(f"Marked event_id {event_id} as 'ready_for_nlp'.")
                            else:
                                logger.error(f"Failed to track event_id {event_id} in EventTracking.")
                        else:
                            logger.error(f"Failed to insert event_id {event_id} into Events table. Skipping processing.")
                else:
                    logger.info("No new events to process.")

                # Step 2: Check for events ready for NLP
                with config_manager.event_data_db_lock:
                    ready_events = self.fetch_ready_for_nlp_events()
                    for ready_event_id in ready_events:
                        event_type = self.get_event_type(ready_event_id)
                        if event_type == 'User starts or modifies a search':
                            self.process_search_event(ready_event_id)
                        # Add additional elif blocks for other event types as you implement them
                        else:
                            logger.warning(f"No NLP handler implemented for event_type: {event_type}")

                sleep(15)  # Poll interval
            except Exception as e:
                logger.error(f"Error during event polling or processing: {e}", exc_info=True)


    def fetch_ready_for_nlp_events(self) -> List[str]:
        """
        Fetch a list of event_ids from EventTracking that have status 'ready_for_nlp'.
        """
        self.data_cursor.execute("SELECT event_id FROM EventTracking WHERE status='ready_for_nlp'")
        rows = self.data_cursor.fetchall()
        return [r[0] for r in rows] if rows else []


    def get_event_type(self, event_id: str) -> Optional[str]:
        """
        Retrieve the event_type for a given event_id from the Events table.
        """
        self.data_cursor.execute("SELECT event_type FROM Events WHERE event_id=?", (event_id,))
        row = self.data_cursor.fetchone()
        return row[0] if row else None


    def process_search_event(self, event_id: str) -> None:
        """
        Processes a search event by extracting topics and entities, mapping them to WikiData,
        and updating the database accordingly.

        Steps:
            1. Begin transaction
            2. Fetch purpose and search terms
            3. Extract topics with RAKE
            4. Extract entities with NER
            5. Prepare bulk insert for Topics
            6. Prepare bulk insert for Entities
            7. Map topics to WikiData using map_topics_to_wikidata()
            8. Write mapped topics back to database
            9. Map entities to WikiData using map_ner_to_wikidata()
            10. Write mapped entities back to database
        """
        logger.info(f"Starting processing for search event_id: {event_id}")
        
        try:
            # Step 1: Begin transaction
            self.data_conn.execute('BEGIN')
            
            # Step 2: Fetch purpose and search terms
            self.data_cursor.execute(
                "SELECT purpose, search_terms FROM SearchEvents WHERE event_id=?",
                (event_id,)
            )
            row = self.data_cursor.fetchone()
            if not row:
                logger.error(f"No SearchEvents data found for event_id {event_id}.")
                self.data_conn.rollback()
                return
            purpose, search_terms = row
            logger.debug(f"Fetched purpose: {purpose}")
            logger.debug(f"Fetched search_terms: {search_terms}")
            
            # Step 3: Extract topics with RAKE
            purpose_topics_data = self.extract_topics_with_rake(purpose)
            search_terms_topics_data = self.extract_topics_with_rake(search_terms)
            logger.debug(f"Extracted purpose topics: {purpose_topics_data}")
            logger.debug(f"Extracted search terms topics: {search_terms_topics_data}")
            
            # Step 4: Extract entities with NER
            purpose_entities = extract_entities(purpose)
            search_terms_entities = extract_entities(search_terms)
            logger.debug(f"Extracted purpose entities: {purpose_entities}")
            logger.debug(f"Extracted search terms entities: {search_terms_entities}")
            
            # Step 5: Insert extracted topics into Topics table
            # (topic_context, topic, score are known; label, wikidata_uri are None for now)
            topics_records = []
            for context, topics_data in [('purpose', purpose_topics_data), ('search_terms', search_terms_topics_data)]:
                for topic, score in topics_data.get("topics_with_weights", []):
                    topics_records.append((event_id, context, topic, None, None, score))

            if topics_records:
                self.data_cursor.executemany(
                    "INSERT INTO Topics (event_id, topic_context, topic, wikidata_uri, label, score) VALUES (?, ?, ?, ?, ?, ?)",
                    topics_records
                )
                logger.info(f"Inserted {len(topics_records)} topics into Topics table.")
            else:
                logger.warning("No topics extracted to insert into Topics table.")

            # Step 6: Insert extracted entities into Entities table
            # We have (entity, label) from extract_entities(). We'll store the original NER label now.
            entities_records = []
            for context, entities_list in [('purpose', purpose_entities), ('search_terms', search_terms_entities)]:
                for (entity, ner_label) in entities_list:
                    # Insert entity with original NER label, wikidata_uri=None for now
                    entities_records.append((event_id, context, entity, None, ner_label, None))

            if entities_records:
                self.data_cursor.executemany(
                    "INSERT INTO Entities (event_id, entity_context, entity, wikidata_uri, label, score) VALUES (?, ?, ?, ?, ?, ?)",
                    entities_records
                )
                logger.info(f"Inserted {len(entities_records)} entities into Entities table.")
            else:
                logger.warning("No entities extracted to insert into Entities table.")

            
            # Step 7: Map topics to WikiData
            topics = [record[2] for record in topics_records]  # Extract topics from records
            mapped_topics = map_topics_to_wikidata(topics)  # Use the correct mapping function
            logger.debug(f"Mapped Topics to WikiData: {mapped_topics}")
            
            # Step 8: Write mapped topics back to database
            if mapped_topics:
                mapped_topics_records = []
                for topic, data in mapped_topics.items():
                    uri = data.get('uri', 'UNKNOWN')
                    label = data.get('label', 'UNKNOWN')
                    mapped_topics_records.append((uri, event_id, topic))
                self.update_topics_with_wikidata(mapped_topics_records)
                logger.info(f"Updated {len(mapped_topics_records)} records in Topics with WikiData URIs.")
            else:
                logger.warning("No mapped topics to update in Topics table.")
            
            # Step 9: Map entities to WikiData
            entities = [record[2] for record in entities_records]  # Extract entities from records
            mapped_entities = map_ner_to_wikidata(entities)  # Use the correct mapping function
            logger.debug(f"Mapped Entities to WikiData: {mapped_entities}")
            
            # Step 10: Write mapped entities back to database
            if mapped_entities:
                mapped_entities_records = []
                for entity, data in mapped_entities.items():
                    uri = data.get('uri', 'UNKNOWN')
                    label = data.get('label', 'UNKNOWN')
                    mapped_entities_records.append((uri, event_id, entity))
                self.update_entities_with_wikidata(mapped_entities_records)
                logger.info(f"Updated {len(mapped_entities_records)} records in Entities with WikiData URIs.")
            else:
                logger.warning("No mapped entities to update in Entities table.")
            
            # Commit transaction
            self.data_conn.commit()
            logger.info(f"Completed NLP processing for search event_id {event_id}.")
        
        except Exception as e:
            logger.exception(f"An error occurred while processing event_id {event_id}: {e}")
            self.data_conn.rollback()



# Entry point for the script
if __name__ == '__main__':
    state_manager = CoyoteNLPStateManager()
    state_manager.process_pending_events()
