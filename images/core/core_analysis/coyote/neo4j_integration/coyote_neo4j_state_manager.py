"""
coyote_neo4j_state_manager.py

A manager class responsible for periodically polling the centralized event_queue (in coyote_state.db)
for events with status "nlp_processed", writing them to Neo4j, and updating statuses.
Mirrors the architecture of CoyoteNLPStateManager, but for Neo4j.
"""

import logging
import time
import sqlite3
from typing import List, Optional, Dict, Any, Tuple

from neo4j import GraphDatabase, Driver

from coyote.utils.config_manager import (
    get_event_data_db_connection,            # For direct R/W to coyote_event_data.db (with lock)
    get_event_data_read_only_connection,       # For read access to coyote_event_data.db
    connect_to_neo4j,                          # A helper that returns a Neo4j Driver
    get_state_read_only_connection,            # For read-only access to coyote_state.db
    get_state_db_connection,                   # For write access to coyote_state.db
    event_data_db_lock
)
from coyote.data_sources.coyote_extension.coyote_browser_extension_to_neo4j import (
    process_coyote_browser_extension_data,
)
from coyote.data_sources.hypothesis.hypothesis_to_neo4j import process_annotation

logger = logging.getLogger(__name__)

# Constants
POLL_INTERVAL_SECONDS = 60
EVENTS_BATCH_SIZE = 50  # Adjust for how many events to process per cycle


class CoyoteNeo4jStateManager:
    """
    Manages the process of writing user event data to Neo4j in a background thread.
    Periodically polls the centralized event_queue in coyote_state.db for events with status "nlp_processed",
    retrieves their data from coyote_event_data.db, writes them to Neo4j,
    and then updates their status to "neo4j_done" in the centralized event_queue.
    """

    def __init__(self) -> None:
        """
        Initializes database connections and the Neo4j driver.
        """
        self._currently_processing: bool = False
        self.event_data_conn = get_event_data_db_connection()
        self.event_data_cursor = self.event_data_conn.cursor()
        self.read_only_conn = get_event_data_read_only_connection()
        self.read_only_cursor = self.read_only_conn.cursor()

        self.neo4j_driver: Optional[Driver] = None
        try:
            self.neo4j_driver = connect_to_neo4j()
            logger.info("Successfully connected to Neo4j.")
        except Exception as e:
            logger.error(f"Failed to create Neo4j driver: {e}", exc_info=True)
            self.neo4j_driver = None

        # Initialize state attributes
        self.last_search_terms_node_id: Optional[int] = None
        self.last_webpage_node_id: Optional[int] = None

    def poll_and_process_neo4j_events(self) -> None:
        """
        Main loop that periodically polls the centralized event_queue (in coyote_state.db)
        for events with status "nlp_processed", processes them by writing to Neo4j,
        and updates their status to "neo4j_done".
        """
        logger.info("Neo4j polling loop started.")
        try:
            while True:
                # If currently processing, wait and then continue
                if self._currently_processing:
                    logger.debug("Already processing; skipping this poll cycle.")
                    time.sleep(POLL_INTERVAL_SECONDS)
                    continue

                # Ensure Neo4j driver is available
                if not self.neo4j_driver:
                    logger.warning("No Neo4j driver available. Attempting reconnection.")
                    try:
                        self.neo4j_driver = connect_to_neo4j()
                    except Exception as e:
                        logger.error(f"Neo4j reconnection failed: {e}", exc_info=True)
                        time.sleep(POLL_INTERVAL_SECONDS)
                        continue

                # Step 1: Poll the centralized event_queue for events with status "nlp_processed"
                nlp_processed_events = self._fetch_nlp_processed_events(EVENTS_BATCH_SIZE)
                if not nlp_processed_events:
                    logger.info("No events with status 'nlp_processed' found. Waiting for next poll.")
                else:
                    logger.info(f"Found {len(nlp_processed_events)} event(s) with status 'nlp_processed'.")
                    self._currently_processing = True
                    try:
                        self._process_events(nlp_processed_events)
                    finally:
                        self._currently_processing = False

                time.sleep(POLL_INTERVAL_SECONDS)
        except Exception as e:
            logger.exception(f"Unexpected error in Neo4j polling loop: {e}")
        finally:
            self.close()

    def _fetch_nlp_processed_events(self, limit: int) -> List[str]:
        """
        Fetches event IDs with status 'nlp_processed' from the centralized event_queue in coyote_state.db.
        Uses a dedicated read-only connection.
        """
        try:
            conn = get_state_read_only_connection()
            cursor = conn.cursor()
            cursor.execute(
                "SELECT event_id FROM event_queue WHERE status='nlp_processed' LIMIT ?",
                (limit,)
            )
            rows = cursor.fetchall()
            conn.close()
            event_ids = [row[0] for row in rows]
            logger.debug(f"Fetched events from event_queue with status 'nlp_processed': {event_ids}")
            return event_ids
        except Exception as e:
            logger.exception(f"Error fetching events with status 'nlp_processed': {e}")
            return []

    def _process_events(self, event_ids: List[str]) -> None:
        """
        Processes the given list of event IDs by writing them to Neo4j.
        After successful processing, updates the centralized event_queue status to "neo4j_done".
        """
        if not self.neo4j_driver:
            logger.error("No Neo4j driver available; cannot process events.")
            return

        with self.neo4j_driver.session() as session:
            for event_id in event_ids:
                logger.info(f"Processing event_id {event_id} for Neo4j.")
                event_data = self._fetch_event_data(event_id)
                if not event_data:
                    logger.warning(f"No event data found for event_id {event_id}. Skipping.")
                    continue
                try:
                    if event_data["data_source"] == "Coyote Browser Extension":
                        process_coyote_browser_extension_data(session, event_data, self, self.event_data_cursor)
                    elif event_data["data_source"] == "Hypothesis":
                        process_annotation(session, event_data, self, self.event_data_cursor)
                    else:
                        logger.warning(f"Unknown data source: {event_data['data_source']}")
                        continue

                    # Mark the event as processed in the local EventTracking table
                    self._mark_event_as_processed(event_id)
                    logger.info(f"Marked event {event_id} as processed in event_data.db.")
                    
                    # Update the centralized event_queue status to "neo4j_done"
                    self.update_event_queue_status(event_id, "neo4j_done")
                    logger.info(f"Updated centralized event_queue status for event_id {event_id} to 'neo4j_done'.")
                except Exception as e:
                    logger.exception(f"Error processing event_id {event_id}: {e}")

    def _fetch_event_data(self, event_id: str) -> Optional[Dict[str, Any]]:
        """
        Fetches event data for the given event_id from coyote_event_data.db.
        """
        try:
            self.read_only_cursor.execute(
                "SELECT * FROM Events WHERE event_id = ?",
                (event_id,)
            )
            row = self.read_only_cursor.fetchone()
            if not row:
                return None
            columns = [desc[0] for desc in self.read_only_cursor.description]
            return dict(zip(columns, row))
        except Exception as e:
            logger.exception(f"Error fetching event data for {event_id}: {e}")
            return None

    def _mark_event_as_processed(self, event_id: str) -> None:
        """
        Marks an event as processed in the local EventTracking table in coyote_event_data.db.
        """
        try:
            with event_data_db_lock:
                self.event_data_cursor.execute(
                    "UPDATE EventTracking SET status='processed' WHERE event_id = ?",
                    (event_id,)
                )
                self.event_data_conn.commit()
                logger.info(f"Event {event_id} marked as processed locally.")
        except Exception as e:
            logger.error(f"Error marking event {event_id} as processed: {e}", exc_info=True)

    def update_event_queue_status(self, event_id: str, status: str) -> None:
        """
        Updates the status of an event in the centralized event_queue (in coyote_state.db).
        Uses a write connection.
        """
        try:
            conn = get_state_db_connection()
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE event_queue SET status = ?, processed_at = CURRENT_TIMESTAMP WHERE event_id = ?",
                (status, event_id)
            )
            conn.commit()
            conn.close()
            logger.debug(f"Updated event_queue status for event_id {event_id} to '{status}'.")
        except Exception as e:
            logger.error(f"Error updating event_queue status for event_id {event_id}: {e}", exc_info=True)

    """
    def _trigger_neo4j_connect_to_ontology(self) -> None:
        # Optionally triggers further ontology processing after writing to Neo4j.
        try:
            from coyote.neo4j_integration.connect_to_ontology import main as connect_to_ontology_main
            connect_to_ontology_main()
            logger.info("Triggered connect_to_ontology.py for additional node processing.")
        except Exception as e:
            logger.exception(f"Failed to trigger connect_to_ontology.py: {e}")
    """
            
    def close(self) -> None:
        """
        Cleans up resources.
        """
        if self.neo4j_driver:
            self.neo4j_driver.close()
        self.event_data_conn.close()
        self.read_only_conn.close()
        logger.info("Resources closed.")
