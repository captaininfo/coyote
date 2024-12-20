"""
coyote_nlp_state_manager.py

State manager for managing the sequence of NLP analysis processes and orchestrating tasks for the Coyote application.
"""

import logging
import sqlite3
from time import sleep
from threading import Lock
from typing import Any, Dict, List, Optional
from nltk.corpus import stopwords
from coyote.utils.config_manager import get_event_data_db_connection
from coyote.utils.event_data_handler import (
    fetch_next_event,
    insert_event,
    insert_event_tracking,
    insert_event_specific_data,
    mark_event_ready_for_nlp,
    is_event_processing,
    update_entities_with_wikidata
)
from coyote.analysis.scrape_webpage import scrape_webpage, should_exempt_url
from coyote.analysis.summarize_text import summarize_text
from coyote.analysis.nlp.extract_topics_with_rake import extract_topics_with_rake
from coyote.analysis.nlp.text_ner_analysis import extract_entities, map_ner_to_wikidata, replace_named_entities_in_text
from coyote.analysis.nlp.text_bertopic_analysis import (
    get_topic_from_text, map_topics_to_wikidata, 
    calculate_tfidf_on_phrases, 
    extract_and_replace_topics
)
from coyote.analysis.nlp.bertopic_analysis import analyze_topics
from coyote.analysis.relevance_calculator import calculate_relevance
from coyote.utils import config_manager

# Get logger
logger = logging.getLogger(__name__)

# Define stop words
custom_stopwords = [
    'page', 'click', 'link', 'comment', 'username', 'password', 'login',
    'subscribe', 'share', 'like', 'read', 'more', 'article', 'posted', 'said'
]

try:
    stop_words_list = list(set(stopwords.words('english')).union(set(custom_stopwords)))
except LookupError:
    import nltk
    nltk.download('stopwords')
    stop_words_list = list(set(stopwords.words('english')).union(set(custom_stopwords)))


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
                        elif event_type == 'Webpage loads':
                            self.process_webpage_loads_event(ready_event_id)
                        elif event_type == 'User clicks hyperlink':
                            self.process_hyperlink_event(ready_event_id)
                        elif event_type == 'User annotated webpage':
                            self.process_annotation_event(ready_event_id)
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


    def update_topics_with_wikidata(self, mapped_topics_records: List[tuple]) -> None:
        """
        Update the Topics table with WikiData URIs and labels for the given records.
        
        Each record is expected to be a tuple of (uri, label, event_id, topic).
        """
        try:
            # Bulk update each topic record with the corresponding URI and label
            # We use executemany with an UPDATE statement. Since UPDATE doesn't support multiple bindings
            # at once by default, we do a loop. Alternatively, we can do a loop of execute() calls.
            
            # For better performance, you might consider doing these updates one by one.
            # Another approach is using a loop:
            for (uri, label, event_id, topic) in mapped_topics_records:
                self.data_cursor.execute(
                    "UPDATE Topics SET wikidata_uri=?, label=? WHERE event_id=? AND topic=?",
                    (uri, label, event_id, topic)
                )
            
            # If you prefer a single transaction, it's already encompassed by the main function's transaction.
            # Just commit at the end of process_search_event().
            
        except Exception as e:
            logger.exception(f"Error updating topics with WikiData: {e}")
            raise  # re-raise so calling function can handle rollback if needed


    def update_entities_with_wikidata(self, mapped_entities_records: List[tuple]) -> None:
        """
        Update the Entities table with WikiData URIs and labels for the given records.
        
        Each record is expected to be a tuple of (uri, label, event_id, entity).
        """
        try:
            # Similarly update each entity record
            for (uri, label, event_id, entity) in mapped_entities_records:
                self.data_cursor.execute(
                    "UPDATE Entities SET wikidata_uri=?, label=? WHERE event_id=? AND entity=?",
                    (uri, label, event_id, entity)
                )

        except Exception as e:
            logger.exception(f"Error updating entities with WikiData: {e}")
            raise  # re-raise so calling function can handle rollback if needed



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
                    mapped_topics_records.append((uri, label, event_id, topic))
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
                    mapped_entities_records.append((uri, label, event_id, entity))
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


    def process_webpage_loads_event(self, event_id: str) -> None:
        """
        Processes a webpage loads event by scraping the webpage's main text, extracting topics and 
        entities, mapping them to WikiData, and updating the database accordingly.
        
        Steps:
            1. Begin transaction
            2. Fetch webpage URL
            3. Check if URL is exempt from NLP processing
            4. Scrape webpage main text
            5. Record scraped text to database
            6. Create summary of scraped text
            7. Record webpage summary
            8. Extract topics using BERTopic (analyze_topics)
            9. Insert extracted topics into Topics table
            10. Map topics to WikiData
            11. Update mapped topics in Topics table
            12. Replace topics in text
            13. Calculate TF-IDF scores and update Topics table
            14. Extract entities from scraped text
            15. Insert entities into Entities table
            16. Map entities to WikiData
            17. Update mapped entities in Entities table
            18. Replace entities in text
            19. Calculate TF-IDF scores for entities
            20. Write 'entities_scored' to the 'score' field of the 'Entities' table
        """
        logger.info(f"Starting NLP processing for webpage loads event_id: {event_id}")

        try:
            # Step 1: Begin transaction
            self.data_conn.execute('BEGIN')
            logger.debug("Transaction started.")

            # Step 2: Fetch webpage URL
            self.data_cursor.execute(
                "SELECT url FROM WebpageLoads WHERE event_id=?",
                (event_id,)
            )
            row = self.data_cursor.fetchone()
            if not row:
                logger.error(f"No WebpageLoads data found for event_id {event_id}. Rolling back.")
                self.data_conn.rollback()
                return
            webpage_url = row[0]
            logger.debug(f"Fetched URL for event_id {event_id}: {webpage_url}")

            # Step 3: Check if URL is exempt
            if should_exempt_url(webpage_url):
                logger.info(f"URL {webpage_url} is exempt from NLP processing.")
                scraped_text = ''
                webpage_summary = ''
                detailed_topics = []
                extracted_entities = []
            else:
                # Step 4: Scrape webpage main text
                scraped_text = scrape_webpage(webpage_url)
                logger.debug(f"Scraped main text from {webpage_url}, length={len(scraped_text)} chars")

                # Step 5: Record scraped text
                self.data_cursor.execute(
                    "UPDATE WebpageLoads SET scraped_text=? WHERE event_id=?",
                    (scraped_text, event_id)
                )
                logger.debug(f"Scraped text recorded for event_id {event_id}")

                # Step 6: Create summary
                webpage_summary = summarize_text(scraped_text) or ""
                logger.debug(f"Created webpage summary for event_id {event_id}: {webpage_summary[:100]}...")

                # Step 7: Record webpage summary
                self.data_cursor.execute(
                    "UPDATE WebpageLoads SET webpage_summary=? WHERE event_id=?",
                    (webpage_summary, event_id)
                )
                logger.debug(f"Webpage summary recorded for event_id {event_id}")

                # Step 8: Extract topics with BERTopic
                processed_text = ' '.join(
                    [word for word in scraped_text.split() if word.lower() not in stop_words_list]
                )
                logger.debug("Stopwords removed from scraped text.")

                topic_info, detailed_topics = analyze_topics(processed_text)
                if not detailed_topics:
                    logger.warning(f"No topics extracted from the webpage text for event_id {event_id}.")
                    detailed_topics = []
                else:
                    logger.debug(f"Extracted topics for event_id {event_id}: {detailed_topics}")

                # Step 9: Insert extracted topics into Topics table
                topics_records = []
                for (topic_str, topic_score) in detailed_topics:
                    topics_records.append((event_id, 'webpage', topic_str, None, None, topic_score))

                if topics_records:
                    self.data_cursor.executemany(
                        "INSERT INTO Topics (event_id, topic_context, topic, wikidata_uri, label, score) VALUES (?, ?, ?, ?, ?, ?)",
                        topics_records
                    )
                    logger.info(f"Inserted {len(topics_records)} webpage topics into Topics table for event_id {event_id}.")
                else:
                    logger.warning(f"No topics inserted into Topics table for event_id {event_id}.")

                # Step 10: Map topics to WikiData
                topic_strings = [t[2] for t in topics_records]
                mapped_topics = {}
                if topic_strings:
                    mapped_topics = map_topics_to_wikidata(topic_strings) or {}
                    logger.debug(f"Mapped webpage topics to WikiData for event_id {event_id}: {mapped_topics}")

                # Step 11: Update mapped topics in Topics table
                if mapped_topics:
                    mapped_topics_records = []
                    for topic_str, data in mapped_topics.items():
                        uri = data.get('uri', 'UNKNOWN')
                        label = data.get('label', 'UNKNOWN')
                        mapped_topics_records.append((uri, label, event_id, topic_str))
                    self.update_topics_with_wikidata(mapped_topics_records)
                    logger.info(f"Updated {len(mapped_topics_records)} webpage topics with WikiData URIs for event_id {event_id}.")
                else:
                    logger.warning(f"No mapped webpage topics to update in Topics table for event_id {event_id}.")

                # Step 12: Replace topics in text
                processed_text = extract_and_replace_topics(processed_text, mapped_topics)
                logger.debug(f"Processed text after replacing topics for event_id {event_id}: {processed_text[:100]}...")

                # Step 13: Calculate TF-IDF scores for topics
                # Fetch a representative sample of documents from CorpusDocuments
                self.data_cursor.execute("SELECT content FROM CorpusDocuments WHERE source='TEDTalk' LIMIT 500")
                rows = self.data_cursor.fetchall()
                corpus = [r[0] for r in rows]
                topics_scored = calculate_tfidf_on_phrases(processed_text, corpus=corpus, threshold=0.07)
                logger.debug(f"TF-IDF scores for topics (event_id={event_id}): {topics_scored}")

                # Update the 'score' field in Topics table
                for term, tfidf_score in topics_scored.items():
                    # Convert underscores back to spaces to match the original topic in the database
                    original_topic = term.replace('_', ' ')
                    self.data_cursor.execute(
                        "UPDATE Topics SET score=? WHERE event_id=? AND topic=?",
                        (tfidf_score, event_id, original_topic)
                    )
                logger.info(f"Updated Topics table with TF-IDF scores for event_id {event_id}.")

                # Step 14: Extract entities
                extracted_entities = extract_entities(scraped_text)
                logger.debug(f"Extracted webpage entities for event_id {event_id}: {extracted_entities}")

                # Step 15: Insert extracted entities into Entities table
                entities_records = []
                for (entity_text, ner_label) in extracted_entities:
                    entities_records.append((event_id, 'webpage', entity_text, None, ner_label, None))

                if entities_records:
                    self.data_cursor.executemany(
                        "INSERT INTO Entities (event_id, entity_context, entity, wikidata_uri, label, score) VALUES (?, ?, ?, ?, ?, ?)",
                        entities_records
                    )
                    logger.info(f"Inserted {len(entities_records)} webpage entities into Entities table for event_id {event_id}.")
                else:
                    logger.warning(f"No entities inserted into Entities table for event_id {event_id}.")

                # Step 16: Map entities to WikiData
                entity_texts = [r[2] for r in entities_records]
                mapped_entities = {}
                if entity_texts:
                    mapped_entities = map_ner_to_wikidata(entity_texts) or {}
                    logger.debug(f"Mapped webpage entities to WikiData for event_id {event_id}: {mapped_entities}")

                # Step 17: Update mapped entities back to database
                if mapped_entities:
                    mapped_entities_records = []
                    for entity_text, data in mapped_entities.items():
                        uri = data.get('uri', 'UNKNOWN')
                        label = data.get('label', 'UNKNOWN')
                        mapped_entities_records.append((uri, label, event_id, entity_text))
                    self.update_entities_with_wikidata(mapped_entities_records)
                    logger.info(f"Updated {len(mapped_entities_records)} webpage entities with WikiData URIs for event_id {event_id}.")
                else:
                    logger.warning(f"No mapped webpage entities to update in Entities table for event_id {event_id}.")

                # Step 18: Replace entities in text
                processed_text = replace_named_entities_in_text(processed_text, mapped_entities)
                logger.debug(f"Processed Text after replacing entities: {processed_text}")

                # Step 19: Calculate TF-IDF scores
                entities_scored = calculate_tfidf_on_phrases(processed_text, corpus=corpus, threshold=0.07)
                logger.debug(f"TF-IDF scores for entities (event_id={event_id}): {entities_scored}")

                # Step 20: Write 'entities_scored' to the 'score' field of the 'Entities' table
                for term, tfidf_score in entities_scored.items():
                    # Similarly, if entity terms had underscores, convert them back
                    original_entity = term.replace('_', ' ')
                    self.data_cursor.execute(
                        "UPDATE Entities SET score=? WHERE event_id=? AND entity=?",
                        (tfidf_score, event_id, original_entity)
                    )
                logger.info(f"Updated Entities table with TF-IDF scores for event_id {event_id}.")

            # Commit transaction
            self.data_conn.commit()
            logger.info(f"Completed NLP processing for webpage loads event_id {event_id}.")

        except Exception as e:
            logger.exception(f"An error occurred while processing webpage loads event_id {event_id}: {e}")
            self.data_conn.rollback()


    def process_hyperlink_event(self, event_id: str) -> None:
        """
        Processes a hyperlink event by extracting topics and entities from the hyperlink text,
        mapping them to WikiData, and updating the database accordingly.
        
        Steps:
            1. Begin transaction
            2. Fetch source_url, destination_url, and link_text
            3. Extract topics with RAKE from link_text
            4. Extract entities with NER from link_text
            5. Insert extracted topics into Topics table (topic_context='hyperlink')
            6. Insert extracted entities into Entities table (entity_context='hyperlink')
            7. Map topics to WikiData using map_topics_to_wikidata()
            8. Update mapped topics in Topics table
            9. Map entities to WikiData using map_ner_to_wikidata()
            10. Update mapped entities in Entities table
        """
        logger.info(f"Starting NLP processing for hyperlink event_id: {event_id}")

        try:
            # Step 1: Begin transaction
            self.data_conn.execute('BEGIN')
            logger.debug("Transaction started for hyperlink event.")

            # Step 2: Fetch source_url, destination_url, and link_text
            self.data_cursor.execute(
                "SELECT source_url, destination_url, link_text FROM HyperlinkClicks WHERE event_id=?",
                (event_id,)
            )
            row = self.data_cursor.fetchone()
            if not row:
                logger.error(f"No HyperlinkClicks data found for event_id {event_id}. Rolling back.")
                self.data_conn.rollback()
                return
            source_url, destination_url, link_text = row
            logger.debug(f"Fetched hyperlink data for event_id {event_id}: source_url={source_url}, destination_url={destination_url}, link_text={link_text[:50]}...")

            # Step 3: Extract topics with RAKE from link_text
            link_topics_data = extract_topics_with_rake(link_text)
            logger.debug(f"Extracted hyperlink topics (event_id={event_id}): {link_topics_data}")

            # Step 4: Extract entities with NER from link_text
            link_entities = extract_entities(link_text)
            logger.debug(f"Extracted hyperlink entities (event_id={event_id}): {link_entities}")

            # Step 5: Insert extracted topics into Topics table
            topics_records = []
            for (topic, score) in link_topics_data.get("topics_with_weights", []):
                # Insert topic with wikidata_uri=None, label=None for now
                topics_records.append((event_id, 'hyperlink', topic, None, None, score))

            if topics_records:
                self.data_cursor.executemany(
                    "INSERT INTO Topics (event_id, topic_context, topic, wikidata_uri, label, score) VALUES (?, ?, ?, ?, ?, ?)",
                    topics_records
                )
                logger.info(f"Inserted {len(topics_records)} hyperlink topics into Topics table for event_id {event_id}.")
            else:
                logger.warning(f"No topics extracted to insert into Topics table for hyperlink event_id {event_id}.")

            # Step 6: Insert extracted entities into Entities table
            entities_records = []
            for (entity_text, ner_label) in link_entities:
                # Insert entity with original NER label, wikidata_uri=None for now
                entities_records.append((event_id, 'hyperlink', entity_text, None, ner_label, None))

            if entities_records:
                self.data_cursor.executemany(
                    "INSERT INTO Entities (event_id, entity_context, entity, wikidata_uri, label, score) VALUES (?, ?, ?, ?, ?, ?)",
                    entities_records
                )
                logger.info(f"Inserted {len(entities_records)} hyperlink entities into Entities table for event_id {event_id}.")
            else:
                logger.warning(f"No entities extracted to insert into Entities table for hyperlink event_id {event_id}.")

            # Step 7: Map topics to WikiData
            hyperlink_topics = [record[2] for record in topics_records]  # Extract topic strings
            mapped_topics = {}
            if hyperlink_topics:
                mapped_topics = map_topics_to_wikidata(hyperlink_topics) or {}
                logger.debug(f"Mapped hyperlink topics to WikiData (event_id={event_id}): {mapped_topics}")

            # Step 8: Update mapped topics in Topics table
            if mapped_topics:
                mapped_topics_records = []
                for topic_str, data in mapped_topics.items():
                    uri = data.get('uri', 'UNKNOWN')
                    label = data.get('label', 'UNKNOWN')
                    mapped_topics_records.append((uri, label, event_id, topic_str))
                self.update_topics_with_wikidata(mapped_topics_records)
                logger.info(f"Updated {len(mapped_topics_records)} hyperlink topics with WikiData URIs for event_id {event_id}.")
            else:
                logger.warning(f"No mapped hyperlink topics to update in Topics table for event_id {event_id}.")

            # Step 9: Map entities to WikiData
            hyperlink_entities = [record[2] for record in entities_records]
            mapped_entities = {}
            if hyperlink_entities:
                mapped_entities = map_ner_to_wikidata(hyperlink_entities) or {}
                logger.debug(f"Mapped hyperlink entities to WikiData (event_id={event_id}): {mapped_entities}")

            # Step 10: Update mapped entities in Entities table
            if mapped_entities:
                mapped_entities_records = []
                for entity_text, data in mapped_entities.items():
                    uri = data.get('uri', 'UNKNOWN')
                    label = data.get('label', 'UNKNOWN')
                    mapped_entities_records.append((uri, label, event_id, entity_text))
                self.update_entities_with_wikidata(mapped_entities_records)
                logger.info(f"Updated {len(mapped_entities_records)} hyperlink entities with WikiData URIs for event_id {event_id}.")
            else:
                logger.warning(f"No mapped hyperlink entities to update in Entities table for event_id {event_id}.")

            # Commit transaction
            self.data_conn.commit()
            logger.info(f"Completed NLP processing for hyperlink event_id {event_id}.")

        except Exception as e:
            logger.exception(f"An error occurred while processing hyperlink event_id {event_id}: {e}")
            self.data_conn.rollback()


    def process_annotation_event(self, event_id: str) -> None:
        """
        Processes a user-annotated webpage event by extracting topics and entities,
        mapping them to WikiData, and updating the database accordingly.

        Logic for topic extraction:
            - If the combined annotation_text and highlighted_text have fewer than 50 words,
              use RAKE for topic extraction.
            - If 50 words or more, use BERTopic (analyze_topics).

        Steps:
            1. Begin transaction
            2. Fetch annotation data (annotation_id, annotation_text, highlighted_text)
            3. Determine word count and choose RAKE or BERTopic
            4. Extract entities with NER
            5. Insert extracted topics into Topics table (topic_context='annotation_text' or 'highlighted_text')
            6. Insert extracted entities into Entities table (entity_context='annotation_text' or 'highlighted_text')
            7. Map topics to WikiData
            8. Update mapped topics in Topics table
            9. Map entities to WikiData
            10. Update mapped entities in Entities table
        """
        logger.info(f"Starting processing for annotation event_id: {event_id}")

        try:
            # Step 1: Begin transaction
            self.data_conn.execute('BEGIN')
            logger.debug("Transaction started for annotation event.")

            # Step 2: Fetch annotation data
            # Assume a single annotation event references a single annotation record
            self.data_cursor.execute(
                "SELECT annotation_id, annotation_text, highlighted_text FROM Annotations WHERE event_id=?",
                (event_id,)
            )
            row = self.data_cursor.fetchone()
            if not row:
                logger.error(f"No annotation data found for event_id {event_id}. Rolling back.")
                self.data_conn.rollback()
                return
            annotation_id, annotation_text, highlighted_text = row
            logger.debug(f"Fetched annotation data for event_id={event_id}: annotation_id={annotation_id}, "
                        f"annotation_text length={len(annotation_text.split())}, highlighted_text length={len(highlighted_text.split())}")

            # Combine texts for word count check
            full_text = (annotation_text or "") + " " + (highlighted_text or "")
            word_count = len(full_text.split())

            # Step 3: Determine method for topic extraction
            if word_count >= 50:
                # Use BERTopic (analyze_topics)
                logger.debug(f"Using BERTopic for event_id={event_id}, word_count={word_count}")
                # Remove stopwords if needed and analyze topics
                processed_text = ' '.join([w for w in full_text.split() if w.lower() not in stop_words_list])
                topic_info, detailed_topics = analyze_topics(processed_text)
                if not detailed_topics:
                    logger.warning(f"No topics extracted via BERTopic for event_id {event_id}.")
                    detailed_topics = []
                else:
                    logger.debug(f"Extracted BERTopic topics for event_id={event_id}: {detailed_topics}")

                # Convert detailed_topics to RAKE-like format if needed
                # Assume detailed_topics is list of (topic_str, score) tuples
                topics_data_annotation = {"topics_with_weights": detailed_topics}
            else:
                # Use RAKE
                logger.debug(f"Using RAKE for event_id={event_id}, word_count={word_count}")
                annotation_topics_data = extract_topics_with_rake(annotation_text or "")
                highlighted_topics_data = extract_topics_with_rake(highlighted_text or "")

                # Merge results from annotation_text and highlighted_text
                # We'll treat them as separate contexts (annotation_text, highlighted_text)
                # If you prefer, you can combine them under a single context.
                # For consistency with search events, let's create separate topic contexts.
                topics_data_annotation = {"annotation_text": annotation_topics_data, "highlighted_text": highlighted_topics_data}

            # Step 4: Extract entities with NER
            annotation_entities = extract_entities(annotation_text or "")
            highlighted_entities = extract_entities(highlighted_text or "")
            logger.debug(f"Extracted annotation entities for event_id={event_id}: {annotation_entities}")
            logger.debug(f"Extracted highlighted entities for event_id={event_id}: {highlighted_entities}")

            # Step 5: Insert extracted topics into Topics table
            topics_records = []
            if word_count >= 50:
                # Using BERTopic, single context 'annotation_text' for entire combined text or separate?
                # Let's just use 'annotation_text' context for simplicity.
                for (topic_str, score) in topics_data_annotation.get("topics_with_weights", []):
                    topics_records.append((event_id, 'annotation_text', topic_str, None, None, score))
            else:
                # Using RAKE, we have two sets of topics
                # annotation_text
                for (topic_str, score) in topics_data_annotation["annotation_text"].get("topics_with_weights", []):
                    topics_records.append((event_id, 'annotation_text', topic_str, None, None, score))
                # highlighted_text
                for (topic_str, score) in topics_data_annotation["highlighted_text"].get("topics_with_weights", []):
                    topics_records.append((event_id, 'highlighted_text', topic_str, None, None, score))

            if topics_records:
                self.data_cursor.executemany(
                    "INSERT INTO Topics (event_id, topic_context, topic, wikidata_uri, label, score) VALUES (?, ?, ?, ?, ?, ?)",
                    topics_records
                )
                logger.info(f"Inserted {len(topics_records)} annotation topics into Topics table for event_id {event_id}.")
            else:
                logger.warning(f"No topics extracted to insert into Topics table for annotation event_id {event_id}.")

            # Step 6: Insert extracted entities into Entities table
            entities_records = []
            for (entity_text, ner_label) in annotation_entities:
                entities_records.append((event_id, 'annotation_text', entity_text, None, ner_label, None))
            for (entity_text, ner_label) in highlighted_entities:
                entities_records.append((event_id, 'highlighted_text', entity_text, None, ner_label, None))

            if entities_records:
                self.data_cursor.executemany(
                    "INSERT INTO Entities (event_id, entity_context, entity, wikidata_uri, label, score) VALUES (?, ?, ?, ?, ?, ?)",
                    entities_records
                )
                logger.info(f"Inserted {len(entities_records)} annotation entities into Entities table for event_id {event_id}.")
            else:
                logger.warning(f"No entities extracted to insert into Entities table for annotation event_id {event_id}.")

            # Step 7: Map topics to WikiData
            annotation_topics = [r[2] for r in topics_records]
            mapped_topics = {}
            if annotation_topics:
                mapped_topics = map_topics_to_wikidata(annotation_topics) or {}
                logger.debug(f"Mapped annotation topics to WikiData (event_id={event_id}): {mapped_topics}")

            # Step 8: Update mapped topics in Topics table
            if mapped_topics:
                mapped_topics_records = []
                for topic_str, data in mapped_topics.items():
                    uri = data.get('uri', 'UNKNOWN')
                    label = data.get('label', 'UNKNOWN')
                    mapped_topics_records.append((uri, label, event_id, topic_str))
                self.update_topics_with_wikidata(mapped_topics_records)
                logger.info(f"Updated {len(mapped_topics_records)} annotation topics with WikiData URIs for event_id {event_id}.")
            else:
                logger.warning(f"No mapped annotation topics to update in Topics table for event_id {event_id}.")

            # Step 9: Map entities to WikiData
            annotation_entities_text = [r[2] for r in entities_records]
            mapped_entities = {}
            if annotation_entities_text:
                mapped_entities = map_ner_to_wikidata(annotation_entities_text) or {}
                logger.debug(f"Mapped annotation entities to WikiData (event_id={event_id}): {mapped_entities}")

            # Step 10: Update mapped entities back to database
            if mapped_entities:
                mapped_entities_records = []
                for entity_text, data in mapped_entities.items():
                    uri = data.get('uri', 'UNKNOWN')
                    label = data.get('label', 'UNKNOWN')
                    mapped_entities_records.append((uri, label, event_id, entity_text))
                self.update_entities_with_wikidata(mapped_entities_records)
                logger.info(f"Updated {len(mapped_entities_records)} annotation entities with WikiData URIs for event_id {event_id}.")
            else:
                logger.warning(f"No mapped annotation entities to update in Entities table for event_id {event_id}.")

            # Commit transaction
            self.data_conn.commit()
            logger.info(f"Completed NLP processing for annotation event_id {event_id}.")

        except Exception as e:
            logger.exception(f"An error occurred while processing annotation event_id {event_id}: {e}")
            self.data_conn.rollback()



# Entry point for the script
if __name__ == '__main__':
    state_manager = CoyoteNLPStateManager()
    state_manager.process_pending_events()
