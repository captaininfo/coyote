"""
connect_to_ontology.py

Module for connecting user data nodes in Neo4j to the WikiData ontology.
Includes caching of WikiData queries and recursive traversal to link ontology nodes.
"""

import json
import logging
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from neo4j import GraphDatabase, Driver, Session
from SPARQLWrapper import SPARQLWrapper, JSON

from coyote.utils.config_manager import get_setting

# Configure logging
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Global configuration
MAX_RECURSION_DEPTH = 5  # Limit recursion depth
TOP_LEVEL_URI = "http://www.wikidata.org/entity/Q35120"  # Top-level 'entity' node
STATE_DB_PATH = Path('data/coyote_state.db')
CACHE_DB_PATH = Path('data/wikidata_cache.db')
CACHE_EXPIRATION_DAYS = 7  # Cache expiration time in days


def initialize_cache_db(cache_db_path: Path = CACHE_DB_PATH) -> None:
    """
    Initializes the SQLite database used for caching WikiData queries.

    Args:
        cache_db_path (Path): The path to the cache database file.
    """
    try:
        with sqlite3.connect(cache_db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS wikidata_cache (
                uri TEXT PRIMARY KEY,
                data TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
            """)
            conn.commit()
        logger.info("Cache database initialized.")
    except sqlite3.Error as e:
        logger.error(f"SQLite error in initialize_cache_db: {e}")


def get_from_cache(uri: str, cache_db_path: Path = CACHE_DB_PATH) -> Optional[Any]:
    """
    Retrieves data from the cache if available and not expired.

    Args:
        uri (str): The URI to look up in the cache.
        cache_db_path (Path): The path to the cache database file.

    Returns:
        Optional[Any]: The cached data if available and not expired, else None.
    """
    try:
        with sqlite3.connect(cache_db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT data, timestamp FROM wikidata_cache WHERE uri=?', (uri,))
            result = cursor.fetchone()
            if result:
                data, timestamp_str = result
                cache_time = datetime.strptime(timestamp_str, '%Y-%m-%d %H:%M:%S')
                if (datetime.now() - cache_time).days < CACHE_EXPIRATION_DAYS:
                    return json.loads(data)
                else:
                    logger.info(f"Cache expired for URI {uri}")
        return None
    except sqlite3.Error as e:
        logger.error(f"SQLite error in get_from_cache: {e}")
        return None
    except json.JSONDecodeError as e:
        logger.error(f"JSON decode error in get_from_cache for URI {uri}: {e}")
        return None


def save_to_cache(uri: str, data: Any, cache_db_path: Path = CACHE_DB_PATH) -> None:
    """
    Saves data to the cache with the current timestamp.

    Args:
        uri (str): The URI to cache.
        data (Any): The data to cache.
        cache_db_path (Path): The path to the cache database file.
    """
    try:
        with sqlite3.connect(cache_db_path) as conn:
            cursor = conn.cursor()
            timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            cursor.execute(
                'INSERT OR REPLACE INTO wikidata_cache (uri, data, timestamp) VALUES (?, ?, ?)',
                (uri, json.dumps(data), timestamp)
            )
            conn.commit()
        logger.info(f"Data cached for URI {uri}")
    except sqlite3.Error as e:
        logger.error(f"SQLite error in save_to_cache: {e}")
    except Exception as e:
        logger.error(f"Error in save_to_cache for URI {uri}: {e}")


def get_batch_of_node_ids(
    db_path: Path = STATE_DB_PATH,
    batch_size: int = 10
) -> List[int]:
    """
    Fetches a batch of node IDs from the SQLite database that are pending processing.

    Args:
        db_path (Path): The path to the state database file.
        batch_size (int): The number of node IDs to fetch.

    Returns:
        List[int]: A list of node IDs.
    """
    try:
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
            SELECT node_id FROM node_processing_queue
            WHERE status = 'pending'
            ORDER BY created_at ASC
            LIMIT ?
            """, (batch_size,))
            rows = cursor.fetchall()
            node_ids = [row[0] for row in rows]

            if node_ids:
                cursor.execute("""
                UPDATE node_processing_queue
                SET status = 'in_progress'
                WHERE node_id IN ({})
                """.format(','.join('?' * len(node_ids))), node_ids)
                conn.commit()
                logger.info(f"Fetched and set to 'in_progress' batch of {len(node_ids)} node IDs.")
            else:
                logger.info("No pending node IDs found.")

            return node_ids

    except sqlite3.Error as e:
        logger.error(f"SQLite error in get_batch_of_node_ids: {e}")
        return []


def get_user_data_nodes(session: Session, node_ids: List[int]) -> Dict[int, Dict[str, Any]]:
    """
    Fetches user data nodes from Neo4j using a list of node IDs.

    Args:
        session (Session): The Neo4j session.
        node_ids (List[int]): The list of node IDs to fetch.

    Returns:
        Dict[int, Dict[str, Any]]: A dictionary mapping node IDs to their data.
    """
    try:
        query = """
        MATCH (n) WHERE id(n) IN $node_ids
        RETURN id(n) AS node_id, n.entities AS entities, n.topics AS topics, n.textTopics AS textTopics,
               n.annotationTextEntities AS annotationTextEntities, n.highlightedTextEntities AS highlightedTextEntities,
               n.timestamp AS timestamp
        """
        result = session.run(query, node_ids=node_ids)

        nodes_data: Dict[int, Dict[str, Any]] = {}

        for record in result:
            node_id = record["node_id"]
            data_fields = ["entities", "topics", "textTopics", "annotationTextEntities", "highlightedTextEntities"]
            node_data = {field: record.get(field) or '[]' for field in data_fields}  # Default to '[]' if None
            node_data["timestamp"] = record.get("timestamp", '')

            # Only add nodes with non-empty data
            if any(node_data[field] != '[]' for field in data_fields):
                nodes_data[node_id] = node_data
            else:
                logger.info(f"No valid data found for node {node_id}. Skipping.")

        logger.info(f"Fetched and processed {len(nodes_data)} relevant nodes from Neo4j.")
        return nodes_data

    except Exception as e:
        logger.error(f"Error querying Neo4j in get_user_data_nodes: {e}")
        return {}


def extract_uris_from_node_data(data: Dict[str, Any]) -> List[str]:
    """
    Extracts URIs from the node data.

    Args:
        data (Dict[str, Any]): The node data.

    Returns:
        List[str]: A list of extracted URIs.
    """
    uris: List[str] = []
    keys = ['entities', 'topics', 'textTopics', 'annotationTextEntities', 'highlightedTextEntities']
    for key in keys:
        value = data.get(key, '[]')
        try:
            items = json.loads(value)
            for item in items:
                if isinstance(item, list) and len(item) == 2 and item[1].startswith("http"):
                    uris.append(item[1])
                elif isinstance(item, dict) and 'uri' in item and item['uri']:
                    uris.extend([uri for uri in item['uri'] if uri.startswith("http")])
        except json.JSONDecodeError as e:
            logger.error(f"JSON decode error for key '{key}': {e}")
    return [uri for uri in uris if uri]


def batch_query_wikidata(uris: List[str]) -> Dict[str, Any]:
    """
    Queries WikiData for a batch of URIs and caches the results.

    Args:
        uris (List[str]): The list of URIs to query.

    Returns:
        Dict[str, Any]: A mapping from URI to parent data.
    """
    unique_uris = list(set(uris))
    cached_data: Dict[str, Any] = {}
    uncached_uris: List[str] = []

    for uri in unique_uris:
        data = get_from_cache(uri)
        if data:
            logger.info(f"Cache hit for URI {uri}")
            cached_data[uri] = data
        else:
            uncached_uris.append(uri)

    if uncached_uris:
        BATCH_SIZE = 50
        for i in range(0, len(uncached_uris), BATCH_SIZE):
            batch_uris = uncached_uris[i:i+BATCH_SIZE]
            uris_str = ' '.join(f"wd:{uri.split('/')[-1]}" for uri in batch_uris)
            query = f"""
            SELECT ?item ?parent ?parentLabel ?relationship
            WHERE {{
                VALUES ?item {{ {uris_str} }}
                OPTIONAL {{
                    ?item wdt:P910 ?parent .
                    BIND("topic's main category" AS ?relationship)
                }}
                OPTIONAL {{
                    ?item wdt:P460 ?parent .
                    BIND("said to be the same as" AS ?relationship)
                }}
                OPTIONAL {{
                    ?item wdt:P279 ?parent .
                    BIND("subclass of" AS ?relationship)
                }}
                OPTIONAL {{
                    ?item wdt:P31 ?parent .
                    BIND("instance of" AS ?relationship)
                }}
                OPTIONAL {{
                    ?item wdt:P361 ?parent .
                    BIND("part of" AS ?relationship)
                }}
                SERVICE wikibase:label {{ bd:serviceParam wikibase:language "[AUTO_LANGUAGE],en". }}
            }}
            """
            sparql = SPARQLWrapper("https://query.wikidata.org/sparql")
            sparql.setQuery(query)
            sparql.setReturnFormat(JSON)

            try:
                logger.info(f"Querying WikiData for batch of {len(batch_uris)} URIs.")
                results = sparql.query().convert()
                results_dict: Dict[str, List[Dict[str, str]]] = {}
                for result in results["results"]["bindings"]:
                    item_uri = result["item"]["value"]
                    parent_data = {
                        "parent": result.get("parent", {}).get("value", ""),
                        "parentLabel": result.get("parentLabel", {}).get("value", ""),
                        "relationship": result.get("relationship", {}).get("value", "")
                    }
                    results_dict.setdefault(item_uri, []).append(parent_data)
                for uri in batch_uris:
                    data_to_cache = results_dict.get(uri, [])
                    save_to_cache(uri, data_to_cache)
                    cached_data[uri] = data_to_cache
                time.sleep(1)  # Adjust as necessary
            except Exception as e:
                logger.error(f"Error querying WikiData: {e}")
    return cached_data


def process_all_uris(
    session: Session,
    nodes_data: Dict[int, Dict[str, Any]],
    update_db_func: Any
) -> None:
    """
    Processes all URIs, querying WikiData, and creates relationships in Neo4j.

    Args:
        session (Session): The Neo4j session.
        nodes_data (Dict[int, Dict[str, Any]]): The node data.
        update_db_func (Any): The function to update node status in the database.
    """
    all_uris: List[str] = []
    node_uri_map: Dict[int, List[str]] = {}

    for node_id, data in nodes_data.items():
        uris = extract_uris_from_node_data(data)
        if uris:
            node_uri_map[node_id] = uris
            all_uris.extend(uris)
        else:
            logger.info(f"No URIs found for node {node_id}, marking it as processed.")
            update_db_func(node_id, 'processed')

    uri_parent_data_map = batch_query_wikidata(all_uris)

    for node_id, uris in node_uri_map.items():
        data = nodes_data[node_id]
        timestamp = data.get('timestamp', '')
        score = get_score_from_node_data(data)
        for uri in uris:
            parent_data_list = uri_parent_data_map.get(uri, [])
            if parent_data_list:
                create_or_link_wikidata_ontology_node(
                    session, node_id, uri, parent_data_list, timestamp, score, 1, [uri]
                )
            else:
                logger.warning(f"No parent data found for URI {uri}")
        update_db_func(node_id, 'processed')


def get_score_from_node_data(data: Dict[str, Any]) -> float:
    """
    Extracts the score from node data if available.

    Args:
        data (Dict[str, Any]): The node data.

    Returns:
        float: The score, defaulting to 0 if not found.
    """
    score = 0.0
    try:
        entities = json.loads(data.get('entities', '[]'))
        for entity in entities:
            if isinstance(entity, dict) and 'score' in entity:
                score = float(entity['score'])
                break
    except json.JSONDecodeError as e:
        logger.error(f"JSON decode error in get_score_from_node_data: {e}")
    except ValueError as e:
        logger.error(f"Value error in get_score_from_node_data: {e}")
    return score


def create_or_link_wikidata_ontology_node(
    session: Session,
    node_id: Optional[int],
    uri: str,
    parent_data_list: List[Dict[str, str]],
    timestamp: str,
    score: float,
    depth: int,
    visited_uris: List[str]
) -> None:
    """
    Creates or links WikiData ontology nodes and relationships in Neo4j.

    Args:
        session (Session): The Neo4j session.
        node_id (Optional[int]): The user data node ID.
        uri (str): The current URI.
        parent_data_list (List[Dict[str, str]]): The parent data list.
        timestamp (str): The timestamp.
        score (float): The TF-IDF score.
        depth (int): The current recursion depth.
        visited_uris (List[str]): The list of visited URIs to prevent cycles.
    """
    try:
        if depth > MAX_RECURSION_DEPTH:
            logger.info(f"Maximum recursion depth reached for node {node_id}. Stopping recursion.")
            return

        for parent in parent_data_list:
            relationship = parent.get('relationship', '')
            parent_uri = parent.get('parent', '')
            parent_label = parent.get('parentLabel', '')

            if not parent_uri:
                continue

            if parent_uri in visited_uris:
                logger.warning(f"Cycle detected with URI {parent_uri}. Stopping recursion.")
                continue

            visited_uris.append(parent_uri)

            if node_id is not None:
                user_to_ontology_rel_type = 'HAS_TOPIC'
                create_or_link_node(
                    session, node_id, uri, parent_uri, parent_label, timestamp, score, user_to_ontology_rel_type
                )
            else:
                rel_type = {
                    "topic's main category": 'TOPIC_MAIN_CATEGORY',
                    "said to be the same as": 'SAME_AS',
                    "subclass of": 'SUBCLASS_OF',
                    "instance of": 'INSTANCE_OF',
                    "part of": 'PART_OF'
                }.get(relationship, None)

                if rel_type:
                    create_or_link_node(
                        session, node_id, uri, parent_uri, parent_label, timestamp, score, rel_type
                    )
                    if relationship == "topic's main category":
                        set_node_property(session, parent_uri, {'is_main_category': True})
                else:
                    logger.info(f"Unhandled relationship type: {relationship}")
                    continue

            if parent_uri != TOP_LEVEL_URI:
                parent_parent_data = get_from_cache(parent_uri) or batch_query_wikidata([parent_uri]).get(parent_uri, [])
                create_or_link_wikidata_ontology_node(
                    session, None, parent_uri, parent_parent_data, timestamp, score, depth + 1, visited_uris
                )
            else:
                logger.info(f"Top-level entity node reached for {parent_uri}. Stopping recursion.")

    except Exception as e:
        logger.error(f"Error in create_or_link_wikidata_ontology_node for node ID {node_id}: {e}", exc_info=True)


def create_or_link_node(
    session: Session,
    node_id: Optional[int],
    source_uri: str,
    target_uri: str,
    target_label: str,
    timestamp: str,
    score: float,
    relationship_type: str
) -> None:
    """
    Creates or links nodes and relationships in Neo4j.

    Args:
        session (Session): The Neo4j session.
        node_id (Optional[int]): The user data node ID.
        source_uri (str): The source URI.
        target_uri (str): The target URI.
        target_label (str): The label for the target node.
        timestamp (str): The timestamp.
        score (float): The TF-IDF score.
        relationship_type (str): The type of the relationship.
    """
    try:
        query = """
        MERGE (wdo:WikiDataOntology {uri: $targetUri})
        ON CREATE SET wdo.label = $targetLabel
        """
        session.run(query, targetUri=target_uri, targetLabel=target_label)

        if node_id is not None:
            query = f"""
            MATCH (n) WHERE id(n) = $node_id
            MATCH (wdo:WikiDataOntology {{uri: $targetUri}})
            MERGE (n)-[rel:{relationship_type} {{timestamp: $timestamp, tfidf_score: $score}}]->(wdo)
            """
            session.run(query, node_id=node_id, targetUri=target_uri, timestamp=timestamp, score=score)
            logger.info(f"Linked user node {node_id} to ontology node {target_uri} with relationship {relationship_type}.")
        else:
            query = f"""
            MATCH (source:WikiDataOntology {{uri: $sourceUri}})
            MATCH (target:WikiDataOntology {{uri: $targetUri}})
            MERGE (source)-[rel:{relationship_type}]->(target)
            """
            session.run(query, sourceUri=source_uri, targetUri=target_uri)
            logger.info(f"Linked ontology node {source_uri} to {target_uri} with relationship {relationship_type}.")
    except Exception as e:
        logger.error(f"Error in create_or_link_node: {e}", exc_info=True)


def set_node_property(session: Session, uri: str, properties: Dict[str, Any]) -> None:
    """
    Sets properties on a node in Neo4j.

    Args:
        session (Session): The Neo4j session.
        uri (str): The URI of the node.
        properties (Dict[str, Any]): The properties to set.
    """
    try:
        query = """
        MATCH (wdo:WikiDataOntology {uri: $uri})
        SET wdo += $properties
        """
        session.run(query, uri=uri, properties=properties)
        logger.info(f"Set properties {properties} on node {uri}.")
    except Exception as e:
        logger.error(f"Error setting properties on node {uri}: {e}", exc_info=True)


def update_node_status_in_db(node_id: int, status: str, db_path: Path = STATE_DB_PATH) -> None:
    """
    Updates the status of a node in the SQLite database.

    Args:
        node_id (int): The node ID.
        status (str): The new status.
        db_path (Path): The path to the state database file.
    """
    try:
        query = "UPDATE node_processing_queue SET status = ? WHERE node_id = ?"
        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(query, (status, node_id))
            conn.commit()
            logger.info(f"Node ID {node_id} marked as {status}.")
    except sqlite3.Error as e:
        logger.error(f"SQLite error while updating node status: {e}")


def main() -> None:
    """
    Main function to initialize the cache and process nodes.
    """
    # Configure logging
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

    # Log that connect_to_ontology is started
    logger.info("Starting ontology connection process.")

    initialize_cache_db()

    uri = get_setting('neo4j_uri')
    username = get_setting('neo4j_username')
    password = get_setting('neo4j_password', decrypt=True)

    if not all([uri, username, password]):
        logger.error("Neo4j credentials not found. Please configure the application.")
        return

    driver: Driver = GraphDatabase.driver(uri, auth=(username, password))

    try:
        with driver.session() as session:
            while True:
                node_ids = get_batch_of_node_ids(db_path=STATE_DB_PATH)
                if node_ids:
                    logger.info(f"Node IDs fetched: {node_ids}")
                    nodes_data = get_user_data_nodes(session, node_ids)
                    if nodes_data:
                        process_all_uris(session, nodes_data, update_node_status_in_db)
                    else:
                        logger.info("No user data nodes to process.")
                else:
                    logger.info("No node IDs fetched from the queue. Processing complete.")
                    break
    except Exception as e:
        logger.error(f"An error occurred in the main loop: {e}", exc_info=True)
    finally:
        driver.close()
        logger.info("Neo4j driver closed.")


