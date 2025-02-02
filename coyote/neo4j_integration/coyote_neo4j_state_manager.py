"""
coyote_neo4j_state_manager.py

A manager class responsible for periodically polling 'coyote_event_data.db'
for completed events, writing them to Neo4j, and updating statuses.
Mirrors the architecture of CoyoteNLPStateManager, but for Neo4j.
"""

import logging
import time
import sqlite3
from typing import List, Optional, Dict, Any, Tuple

from neo4j import GraphDatabase, Driver

from coyote.utils.config_manager import (
    get_event_data_db_connection,  # For direct R/W to coyote_event_data.db (with lock)
    get_event_data_read_only_connection, # For read access to coyote_event_data.db
    connect_to_neo4j,              # A helper that returns a Neo4j Driver
    event_data_db_lock
)
from coyote.data_sources.coyote_extension.coyote_browser_extension_to_neo4j import (
    process_coyote_browser_extension_data,
)
from coyote.data_sources.hypothesis.hypothesis_to_neo4j import process_annotation

logger = logging.getLogger(__name__)

# Constants
POLL_INTERVAL_SECONDS = 60
EVENTS_BATCH_SIZE = 5  # Adjust for how many events to process per cycle



class CoyoteNeo4jStateManager:
    """
    Manages the process of writing user event data to Neo4j in a background thread.
    Periodically polls 'coyote_event_data.db' for 'completed' events, writes them to Neo4j,
    and tracks node statuses directly in Neo4j.
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
        Main loop that periodically polls 'coyote_event_data.db' for completed events,
        processes them by writing to Neo4j, and updates statuses.
        """
        logger.info("Polling loop started.")
        try:
            while True:
                # Checks if this function is already processing event. If true, restarts timer then checks again.
                if self._currently_processing:
                    logger.debug("Already processing; skipping this poll cycle.")
                    time.sleep(POLL_INTERVAL_SECONDS)
                    continue

                # Ensure Neo4j driver is present
                if not self.neo4j_driver:
                    logger.warning("No Neo4j driver available. Attempting reconnection.")
                    try:
                        self.neo4j_driver = connect_to_neo4j()
                    except Exception as e:
                        logger.error(f"Neo4j reconnection failed: {e}", exc_info=True)
                        time.sleep(POLL_INTERVAL_SECONDS)
                        continue

                # Fetch completed events from coyote_event_data.db
                completed_events = self._fetch_completed_events(EVENTS_BATCH_SIZE)
                if not completed_events:
                    logger.info("No completed events found. Waiting for next poll.")
                else:
                    logger.info(f"Found {len(completed_events)} completed event(s).")
                    self._currently_processing = True
                    try:
                        self._process_events(completed_events)
                    finally:
                        self._currently_processing = False

                time.sleep(POLL_INTERVAL_SECONDS)

        except Exception as e:
            logger.exception(f"Unexpected error in polling loop: {e}")
        finally:
            self.close()

    def _fetch_completed_events(self, limit: int) -> List[str]:
        """
        Fetches events with status='completed' from coyote_event_data.db.
        """
        try:
            self.read_only_cursor.execute(
                "SELECT event_id FROM EventTracking WHERE status='completed' LIMIT ?",
                (limit,)
            )
            rows = self.read_only_cursor.fetchall()
            event_ids = [row[0] for row in rows]
            logger.debug(f"Fetched completed events: {event_ids}")
            return event_ids
        except Exception as e:
            logger.exception(f"Error fetching completed events: {e}")
            return []

    def _process_events(self, event_ids: List[str]) -> None:
        """
        Processes the given list of event IDs by writing them to Neo4j.
        """
        if not self.neo4j_driver:
            logger.error("No Neo4j driver available; cannot process events.")
            return

        with self.neo4j_driver.session() as session:
            for event_id in event_ids:
                logger.info(f"Processing event_id={event_id} for Neo4j.")
                event_data = self._fetch_event_data(event_id)

                if not event_data:
                    logger.warning(f"No event data found for event_id={event_id}. Skipping.")
                    continue

                try:
                    if event_data["data_source"] == "Coyote Browser Extension":
                        process_coyote_browser_extension_data(session, event_data, self, self.event_data_cursor)
                    elif event_data["data_source"] == "Hypothesis":
                        process_annotation(session, event_data)
                    else:
                        logger.warning(f"Unknown data source: {event_data['data_source']}")
                        continue

                    self._mark_event_as_processed(event_id)
                    logger.info(f"Marked event {event_id} as processed.")

                except Exception as e:
                    logger.exception(f"Error processing event_id {event_id}: {e}")

    def _fetch_event_data(self, event_id: str) -> Optional[Dict[str, Any]]:
        """
        Fetches event data for the given event_id from coyote_event_data.db.
        """
        try:
            self.read_only_cursor.execute(
                "SELECT * FROM Events WHERE event_id = ?", (event_id,)
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
        Marks an event as processed in coyote_event_data.db.
        """
        try:
            with event_data_db_lock:
                self.event_data_cursor.execute(
                    "UPDATE EventTracking SET status='processed' WHERE event_id = ?",
                    (event_id,)
                )
                self.event_data_conn.commit()
                logger.info(f"Event {event_id} marked as processed.")
        except Exception as e:
            logger.error(f"Error marking event {event_id} as processed: {e}", exc_info=True)


    def _trigger_neo4j_connect_to_ontology(self) -> None:
        """
        Optionally triggers further ontology processing after writing to Neo4j.
        """
        try:
            from coyote.neo4j_integration.connect_to_ontology import main as connect_to_ontology_main
            connect_to_ontology_main()
            logger.info("Triggered connect_to_ontology.py for additional node processing.")
        except Exception as e:
            logger.exception(f"Failed to trigger connect_to_ontology.py: {e}")


    def close(self) -> None:
        """
        Cleans up resources.
        """
        if self.neo4j_driver:
            self.neo4j_driver.close()
        self.event_data_conn.close()
        self.read_only_conn.close()
        logger.info("Resources closed.")
