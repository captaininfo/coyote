"""
events_to_neo4j.py

Module for reading pending events from the 'coyote_event_data.db' SQLite database,
inserting them into a Neo4j database, and triggering ontology processing for the related nodes.
"""

import logging
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from neo4j import GraphDatabase, Driver, Session

from coyote.data_sources.coyote_extension.coyote_browser_extension_to_neo4j import (
    process_coyote_browser_extension_data,
)
from coyote.data_sources.hypothesis.hypothesis_to_neo4j import process_annotation
from coyote.utils.coyote_state_manager import CoyoteStateManager
from coyote.utils.config_manager import get_setting, DATA_DIR

# Get logger
logger = logging.getLogger(__name__)

# Constants
POLL_INTERVAL_SECONDS = 60  # Time between polls (adjust as needed)
EVENTS_BATCH_SIZE = 1        # Process one event at a time to ensure serial processing


def main() -> None:
    """
    Main function to periodically poll 'coyote_event_data.db' for completed events,
    process them, insert data into the Neo4j database, and trigger ontology processing.
    """
    logger.info("Starting events_to_neo4j module.")

    # Get Neo4j credentials from the configuration via config_manager.py
    uri: str = get_setting('neo4j_uri')
    username: str = get_setting('neo4j_username')
    password: str = get_setting('neo4j_password', decrypt=True)

    # Log Neo4j credentials (avoid logging sensitive information in production)
    logger.debug(f"Neo4j URI: {uri}")
    logger.debug(f"Neo4j Username: {username}")
    logger.debug(f"Neo4j Password: {password}")

    # Validate that all credentials are available
    if not all([uri, username, password]):
        logger.error("Neo4j credentials not found. Please configure the application.")
        return


    # Create the Neo4j driver
    try:
        driver: Driver = GraphDatabase.driver(uri, auth=(username, password))
        logger.info("Connected to Neo4j database.")
    except Exception as e:
        logger.exception(f"Failed to connect to Neo4j: {e}")
        return

    # Initialize state managers using config_manager.py for SQLite connections
    try:
        event_data_db_path = get_setting('event_data_db_path')
        state_db_path = get_setting('state_db_path')

        event_conn = sqlite3.connect(event_data_db_path)
        event_cursor = event_conn.cursor()
        logger.debug(f"Connected to SQLite database at {event_data_db_path}")

        state_manager = CoyoteStateManager(state_db_path)
        logger.debug(f"Connected to state SQLite database at {state_db_path}")
    except Exception as e:
        logger.exception(f"Failed to connect to SQLite databases: {e}")
        driver.close()
        return

    try:
        while True:
            logger.debug("Polling for completed events.")

            # Fetch completed events (serial processing)
            completed_events = fetch_completed_events(event_cursor, EVENTS_BATCH_SIZE)

            if not completed_events:
                logger.info("No completed events found. Waiting for next poll.")
            else:
                logger.info(f"Found {len(completed_events)} completed event(s) to process.")
                process_events(driver, completed_events, state_manager, event_cursor, event_conn)

            # Sleep before next poll
            logger.debug(f"Sleeping for {POLL_INTERVAL_SECONDS} seconds before next poll.")
            time.sleep(POLL_INTERVAL_SECONDS)

    except KeyboardInterrupt:
        logger.info("Received keyboard interrupt. Shutting down events_to_neo4j module.")
    except Exception as e:
        logger.exception(f"An unexpected error occurred: {e}")
    finally:
        # Clean up resources
        driver.close()
        event_conn.close()
        state_manager.close()
        logger.info("events_to_neo4j module has been shut down.")


def fetch_completed_events(cursor: sqlite3.Cursor, limit: int) -> List[str]:
    """
    Fetches a list of event_ids with status 'completed' from the EventTracking table.

    Args:
        cursor (sqlite3.Cursor): SQLite cursor for 'coyote_event_data.db'.
        limit (int): Maximum number of events to fetch.

    Returns:
        List[str]: List of event_ids.
    """
    try:
        cursor.execute(
            "SELECT event_id FROM EventTracking WHERE status = 'completed' LIMIT ?",
            (limit,)
        )
        rows = cursor.fetchall()
        event_ids = [row[0] for row in rows]
        logger.debug(f"Fetched event_ids: {event_ids}")
        return event_ids
    except Exception as e:
        logger.exception(f"Failed to fetch completed events: {e}")
        return []


def process_events(
    driver: Driver,
    event_ids: List[str],
    state_manager: CoyoteStateManager,
    event_cursor: sqlite3.Cursor,
    event_conn: sqlite3.Connection
) -> None:
    """
    Processes a list of completed events by inserting them into Neo4j and updating progress.

    Args:
        driver (Driver): Neo4j driver instance.
        event_ids (List[str]): List of event_ids to process.
        state_manager (CoyoteStateManager): State manager for tracking progress.
        event_cursor (sqlite3.Cursor): SQLite cursor for 'coyote_event_data.db'.
        event_conn (sqlite3.Connection): SQLite connection for 'coyote_event_data.db'.
    """
    try:
        with driver.session() as session:
            for event_id in event_ids:
                logger.info(f"Processing event_id: {event_id}")

                # Fetch event data from 'Events' table
                event_data = fetch_event_data(event_cursor, event_id)
                if not event_data:
                    logger.warning(f"No data found for event_id {event_id}. Skipping.")
                    continue

                data_source = event_data.get("dataSource")
                logger.debug(f"Data source for event_id {event_id}: {data_source}")

                nodes_to_process: List[int] = []

                try:
                    if data_source == "Coyote Browser Extension":
                        # Process Coyote Browser Extension event
                        purpose_id, search_terms_id = process_coyote_browser_extension_data(session, event_data, state_manager, event_cursor)
                        if purpose_id:
                            logger.info(f"Appending Purpose node ID {purpose_id} to processing queue.")
                            nodes_to_process.append(purpose_id)
                        if search_terms_id:
                            logger.info(f"Appending SearchTerms node ID {search_terms_id} to processing queue.")
                            nodes_to_process.append(search_terms_id)
                        if state_manager.last_webpage_node_id:
                            # Check if the node is a SERP
                            is_serp = check_if_serp(session, state_manager.last_webpage_node_id)
                            if not is_serp:
                                logger.info(f"Appending Webpage node ID {state_manager.last_webpage_node_id} to processing queue.")
                                nodes_to_process.append(state_manager.last_webpage_node_id)

                    elif data_source == "Hypothesis":
                        # Process Hypothesis annotation event
                        node_id = process_annotation(session, event_data)
                        if node_id:
                            logger.info(f"Appending node ID {node_id} to processing queue.")
                            nodes_to_process.append(node_id)
                    else:
                        logger.warning(f"Unknown data source: {data_source} for event_id {event_id}")
                        continue

                    # Mark the event as processed in 'coyote_event_data.db'
                    mark_event_as_processed(event_cursor, event_conn, event_id)
                    logger.info(f"Marked event_id {event_id} as processed.")

                except Exception as e:
                    logger.exception(f"Error processing event {event_id}: {e}")
                    # Optionally, mark the event as failed or handle accordingly
                    continue

                # Add nodes to the processing queue in 'coyote_state.db'
                if nodes_to_process:
                    logger.info(f"Adding {len(nodes_to_process)} node(s) to the processing queue.")
                    state_manager.add_nodes_to_queue(nodes_to_process)
                    # Trigger connect_to_ontology.py after adding nodes to the queue
                    trigger_connect_to_ontology()
                else:
                    logger.warning(f"No nodes to add to the processing queue for event_id {event_id}.")

    except Exception as e:
        logger.exception(f"Failed to process events: {e}")


def fetch_event_data(cursor: sqlite3.Cursor, event_id: str) -> Optional[Dict[str, Any]]:
    """
    Fetches event data for a given event_id from 'Events' table.

    Args:
        cursor (sqlite3.Cursor): SQLite cursor for 'coyote_event_data.db'.
        event_id (str): The event ID to fetch.

    Returns:
        Optional[Dict[str, Any]]: Event data as a dictionary or None if not found.
    """
    try:
        cursor.execute(
            "SELECT * FROM Events WHERE event_id = ?",
            (event_id,)
        )
        row = cursor.fetchone()
        if not row:
            logger.warning(f"No event data found for event_id {event_id}.")
            return None

        # Assuming the Events table has columns: event_id, dataSource, ... (adjust as needed)
        columns = [description[0] for description in cursor.description]
        event_data = dict(zip(columns, row))
        logger.debug(f"Fetched event data for event_id {event_id}: {event_data}")
        return event_data
    except Exception as e:
        logger.exception(f"Failed to fetch event data for event_id {event_id}: {e}")
        return None


def mark_event_as_processed(cursor: sqlite3.Cursor, conn: sqlite3.Connection, event_id: str) -> None:
    """
    Marks an event as processed by updating its status in the EventTracking table.

    Args:
        cursor (sqlite3.Cursor): SQLite cursor for 'coyote_event_data.db'.
        conn (sqlite3.Connection): SQLite connection for 'coyote_event_data.db'.
        event_id (str): The event ID to mark as processed.
    """
    try:
        cursor.execute(
            "UPDATE EventTracking SET status = 'processed' WHERE event_id = ?",
            (event_id,)
        )
        conn.commit()
        logger.debug(f"Updated status to 'processed' for event_id {event_id}.")
    except Exception as e:
        logger.exception(f"Failed to mark event_id {event_id} as processed: {e}")


def check_if_serp(session: Session, node_id: int) -> bool:
    """
    Checks if a given node in Neo4j is a SERP (Search Engine Results Page).

    Args:
        session (Session): Neo4j session.
        node_id (int): Neo4j node ID.

    Returns:
        bool: True if the node is a SERP, False otherwise.
    """
    try:
        query = """
        MATCH (n) WHERE id(n) = $node_id
        RETURN n.isSERP AS isSERP
        """
        result = session.run(query, node_id=node_id)
        record = result.single()
        if record:
            is_serp = record.get("isSERP", False)
            logger.debug(f"Node ID {node_id} isSERP: {is_serp}")
            return is_serp
        else:
            logger.warning(f"No node found with ID {node_id}.")
            return False
    except Exception as e:
        logger.exception(f"Failed to check if node ID {node_id} is SERP: {e}")
        return False


def trigger_connect_to_ontology() -> None:
    """
    Trigger the connect_to_ontology.py script to process nodes in the processing queue.
    """
    try:
        from coyote.neo4j_integration.connect_to_ontology import main as connect_to_ontology_main
        connect_to_ontology_main()
        logger.info("Triggered connect_to_ontology.py to process nodes.")
    except Exception as e:
        logger.exception(f"Failed to trigger connect_to_ontology.py: {e}")


if __name__ == "__main__":
    main()