"""
json_to_neo4j.py

Module for reading JSON data and inserting it into a Neo4j database.
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List

from neo4j import GraphDatabase, Driver

from coyote.data_sources.coyote_extension.coyote_browser_extension_to_neo4j import (
    process_coyote_browser_extension_data,
)
from coyote.data_sources.hypothesis.hypothesis_to_neo4j import process_annotation
from coyote.utils.coyote_state_manager import CoyoteStateManager
from coyote.utils.config_manager import get_setting, DATA_DIR

# Initialize logger
logger = logging.getLogger(__name__)


def read_json(file_path: Path) -> Any:
    """
    Reads JSON data from a file and returns it.

    Args:
        file_path (Path): The path to the JSON file.

    Returns:
        Any: The JSON data loaded from the file.

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


def main() -> None:
    """
    Main function to read JSON data and insert it into the Neo4j database.
    """
    # Configure logging
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

    # Get Neo4j credentials from the configuration
    uri = get_setting('neo4j_uri')
    username = get_setting('neo4j_username')
    password = get_setting('neo4j_password', decrypt=True)

    # Validate that all credentials are available
    if not all([uri, username, password]):
        logger.error("Neo4j credentials not found. Please configure the application.")
        return

    # Create the Neo4j driver
    driver: Driver = GraphDatabase.driver(uri, auth=(username, password))

    state_manager = CoyoteStateManager()
    nodes_to_process: List[int] = []

    # Define the path to the JSON file using DATA_DIR from config_manager.py
    json_file_path = DATA_DIR / 'analysis_result.json'

    try:
        with driver.session() as session:
            json_data = read_json(json_file_path)
            state: Dict[str, Any] = {
                "last_webpage_node_id": None,
                "last_search_terms_node_id": None
            }  # State dictionary to track IDs across calls

            for entry in json_data:
                data_source = entry.get("dataSource")
                if data_source == "Coyote Browser Extension":
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
                elif data_source == "Hypothesis":
                    node_id = process_annotation(session, entry)  # Assuming no state needed for Hypothesis for now
                    if node_id:
                        logger.info(f"Appending node ID {node_id} to processing queue.")
                        nodes_to_process.append(node_id)
                else:
                    logger.warning(f"Unknown data source: {data_source}")

            # Log the nodes that are being added to the queue
            if nodes_to_process:
                logger.info(f"Adding {len(nodes_to_process)} nodes to the processing queue.")
                state_manager.add_node_to_queue(nodes_to_process)
            else:
                logger.info("No nodes to add to the processing queue.")

    except Exception as e:
        logger.exception(f"An error occurred during processing: {e}")
    finally:
        driver.close()
        state_manager.close()


if __name__ == "__main__":
    main()
