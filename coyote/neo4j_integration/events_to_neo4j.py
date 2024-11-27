"""
json_to_neo4j.py

Module for reading pending events from a JSON file, inserting them into a Neo4j database,
and triggering ontology processing for the related nodes.
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from neo4j import GraphDatabase, Driver, Session

from coyote.data_sources.coyote_extension.coyote_browser_extension_to_neo4j import (
    process_coyote_browser_extension_data,
)
from coyote.data_sources.hypothesis.hypothesis_to_neo4j import process_annotation
from coyote.utils.coyote_state_manager import CoyoteStateManager
from coyote.utils.config_manager import get_setting, DATA_DIR

# Configure logging
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')


def main() -> None:
    """
    Main function to read pending events from the event queue,
    process them, insert data into the Neo4j database, and trigger ontology processing.
    """
    # Get Neo4j credentials from the configuration
    uri: str = get_setting('neo4j_uri')
    username: str = get_setting('neo4j_username')
    password: str = get_setting('neo4j_password', decrypt=True)

    # Add logging to verify credentials and confirm 'json_to_neo4j' is started
    logger.info(f"Neo4j URI: {uri}")
    logger.info(f"Neo4j Username: {username}")
    logger.info(f"Neo4j Password: {password}")
    logger.info(f"json_to_neo4j's main() is started")

    # Validate that all credentials are available
    if not all([uri, username, password]):
        logger.error("Neo4j credentials not found. Please configure the application.")
        return

    # Create the Neo4j driver
    driver: Driver = GraphDatabase.driver(uri, auth=(username, password))

    state_manager = CoyoteStateManager()
    nodes_to_process: List[int] = []

    # Define the path to the JSON file using DATA_DIR from config_manager.py
    json_file_path: Path = DATA_DIR / 'analysis_result.json'

    try:
        # Get pending event IDs from the event queue
        pending_event_ids: List[str] = state_manager.get_pending_events(limit=10)

        if not pending_event_ids:
            logger.info("No pending events to process.")
            return

        # Load analysis_result.json
        json_data: List[Dict[str, Any]] = read_json(json_file_path)

        # Create a mapping of event_id to event data
        event_data_mapping: Dict[str, Dict[str, Any]] = {event['event_id']: event for event in json_data}

        with driver.session() as session:
            state: Dict[str, Any] = {
                "last_webpage_node_id": None,
                "last_search_terms_node_id": None
            }  # State dictionary to track IDs across calls

            for event_id in pending_event_ids:
                entry: Optional[Dict[str, Any]] = event_data_mapping.get(event_id)
                if not entry:
                    logger.warning(f"Event data for event_id {event_id} not found.")
                    continue

                data_source: Optional[str] = entry.get("dataSource")
                try:
                    if data_source == "Coyote Browser Extension":
                        process_coyote_browser_extension_event(session, entry, state, nodes_to_process)
                    elif data_source == "Hypothesis":
                        process_hypothesis_event(session, entry, nodes_to_process)
                    else:
                        logger.warning(f"Unknown data source: {data_source} for event_id {event_id}")
                        continue

                    # Mark the event as processed only if processing was successful
                    logger.debug(f"Attempting to mark event_id {event_id} as processed")
                    state_manager.mark_event_as_processed(event_id)
                    logger.info(f"Processed event {event_id}")

                except Exception as e:
                    logger.error(f"Error processing event {event_id}: {e}", exc_info=True)

            # Add nodes to the processing queue
            if nodes_to_process:
                logger.info(f"Adding {len(nodes_to_process)} node(s) to the processing queue.")
                state_manager.add_node_to_queue(nodes_to_process)
                # Trigger connect_to_ontology.py after adding nodes to the queue
                trigger_connect_to_ontology()
            else:
                logger.info("No nodes to add to the processing queue.")

    except Exception as e:
        logger.exception(f"An error occurred during processing: {e}")
    finally:
        driver.close()
        state_manager.close()


def read_json(file_path: Path) -> List[Dict[str, Any]]:
    """
    Reads JSON data from a file and returns it.

    Args:
        file_path (Path): The path to the JSON file.

    Returns:
        List[Dict[str, Any]]: The list of JSON objects loaded from the file.

    Raises:
        FileNotFoundError: If the file does not exist.
        json.JSONDecodeError: If the file is not valid JSON.
    """
    try:
        with file_path.open('r', encoding='utf-8') as file:
            data = json.load(file)
            logger.debug(f"Loaded JSON data from {file_path}")
            return data
    except FileNotFoundError:
        logger.error(f"JSON file not found: {file_path}")
        raise
    except json.JSONDecodeError:
        logger.error(f"Invalid JSON format in file: {file_path}")
        raise


def process_coyote_browser_extension_event(
    session: Session,
    entry: Dict[str, Any],
    state: Dict[str, Any],
    nodes_to_process: List[int]
) -> None:
    """
    Processes a Coyote Browser Extension event and updates the nodes to process.

    Args:
        session (Session): Neo4j session.
        entry (Dict[str, Any]): Event data entry.
        state (Dict[str, Any]): State dictionary to track IDs across calls.
        nodes_to_process (List[int]): List of node IDs to process.

    Raises:
        Exception: If processing fails.
    """
    purpose_id, search_terms_id = process_coyote_browser_extension_data(session, entry, state)
    if purpose_id:
        logger.info(f"Appending Purpose node ID {purpose_id} to processing queue.")
        nodes_to_process.append(purpose_id)
    if search_terms_id:
        logger.info(f"Appending SearchTerms node ID {search_terms_id} to processing queue.")
        nodes_to_process.append(search_terms_id)
    if state.get('last_webpage_node_id'):
        # Check if the node is a SERP
        is_serp_query = """
        MATCH (n) WHERE id(n) = $node_id RETURN n.isSERP AS isSERP
        """
        result = session.run(is_serp_query, node_id=state['last_webpage_node_id'])
        record = result.single()
        if record:
            is_serp = record.get("isSERP", False)
            if not is_serp:
                logger.info(f"Appending Webpage node ID {state['last_webpage_node_id']} to processing queue.")
                nodes_to_process.append(state['last_webpage_node_id'])
        else:
            logger.warning(f"No 'isSERP' field found for node ID {state['last_webpage_node_id']}")


def process_hypothesis_event(
    session: Session,
    entry: Dict[str, Any],
    nodes_to_process: List[int]
) -> None:
    """
    Processes a Hypothesis annotation event and updates the nodes to process.

    Args:
        session (Session): Neo4j session.
        entry (Dict[str, Any]): Event data entry.
        nodes_to_process (List[int]): List of node IDs to process.

    Raises:
        Exception: If processing fails.
    """
    node_id = process_annotation(session, entry)
    if node_id:
        logger.info(f"Appending node ID {node_id} to processing queue.")
        nodes_to_process.append(node_id)


def trigger_connect_to_ontology() -> None:
    """
    Trigger the connect_to_ontology.py script to process nodes in the processing queue.
    """
    from coyote.neo4j_integration.connect_to_ontology import main as connect_to_ontology_main
    connect_to_ontology_main()
    logger.info("Triggered connect_to_ontology.py to process nodes.")

if __name__ == "__main__":
    main()