"""
connect_to_ontology.py

Module for connecting user data nodes in Neo4j to the WikiData ontology.
Uses event_queue in coyote_state.db to poll for events with status "neo4j_done",
runs the ontology-connection logic, and updates events to "ontology_processed".
"""

import json
import logging
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from neo4j import GraphDatabase, Driver, Session
from SPARQLWrapper import SPARQLWrapper, JSON

from coyote.utils.config_manager import (
    get_setting,
    get_state_read_only_connection,
    get_state_db_connection,
    connect_to_neo4j
)

logger = logging.getLogger(__name__)

# Adjust these if needed
MAX_RECURSION_DEPTH = 5 # Limit recursion depth in WikiData's ontology hierarchy
TOP_LEVEL_URI = "http://www.wikidata.org/entity/Q35120" # Top-level 'entity' node in WikiData ontology
CACHE_EXPIRATION_DAYS = 7
POLL_INTERVAL_SECONDS = 60  # how often poll_and_process_ontology checks for "neo4j_done" events
EVENTS_BATCH_SIZE = 5      # how many events to handle per cycle

################################################################################
# Caching logic
################################################################################

def initialize_cache_db(cache_db_path: Path) -> None:
    """
    Initializes the SQLite database used for caching WikiData queries.
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
        logger.error(f"SQLite error in initialize_cache_db: {e}", exc_info=True)


def get_from_cache(uri: str, cache_db_path: Path) -> Optional[Any]:
    """
    Retrieves data from the cache if available and not expired.
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
    except (sqlite3.Error, json.JSONDecodeError) as e:
        logger.error(f"Error reading cache for URI {uri}: {e}", exc_info=True)
        return None


def save_to_cache(uri: str, data: Any, cache_db_path: Path) -> None:
    """
    Saves data to the cache with the current timestamp.
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
        logger.debug(f"Cached data for URI {uri}")
    except (sqlite3.Error, Exception) as e:
        logger.error(f"Error saving cache for URI {uri}: {e}", exc_info=True)

################################################################################
# WikiData logic
################################################################################

def batch_query_wikidata(uris: List[str], cache_db_path: Path) -> Dict[str, Any]:
    """
    Queries WikiData for a batch of URIs and caches the results.
    """
    unique_uris = list(set(uris))
    cached_data: Dict[str, Any] = {}
    uncached_uris: List[str] = []

    # Check cache
    for uri in unique_uris:
        data = get_from_cache(uri, cache_db_path)
        if data:
            logger.info(f"Cache hit for URI {uri}")
            cached_data[uri] = data
        else:
            uncached_uris.append(uri)

    # Query for uncached URIs
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
            from SPARQLWrapper import SPARQLWrapper, JSON
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
                    save_to_cache(uri, data_to_cache, cache_db_path)
                    cached_data[uri] = data_to_cache
                time.sleep(1)  # Throttle for courtesy
            except Exception as e:
                logger.error(f"Error querying WikiData for URIs {batch_uris}: {e}", exc_info=True)

    return cached_data

################################################################################
# Neo4j logic
################################################################################

def _coerce_score(raw: Any) -> float:
    """Best-effort float coercion for per-item scores. Returns 0.0 on failure."""
    try:
        if raw is None:
            return 0.0
        return float(raw)
    except (TypeError, ValueError):
        return 0.0


def extract_uris_from_node_data(data: Dict[str, Any]) -> List[Tuple[str, float]]:
    """
    Extract (wikidata_uri, score) pairs from a node's NLP-output JSON fields.

    Each item in the source JSON carries its own score (TF-IDF for topics,
    NER-derived float for entities). Returning per-item pairs replaces the
    previous broadcast-score behavior, where one scalar score from the first
    entity was applied to every HAS_TOPIC edge from the node.
    """
    pairs: List[Tuple[str, float]] = []
    keys = [
        "entities", "topics", "textTopics",
        "annotationTextEntities", "highlightedTextEntities"
    ]
    for key in keys:
        raw = data.get(key, "[]")
        try:
            items = json.loads(raw)
        except json.JSONDecodeError:
            logger.debug("Bad JSON in %s", key)
            continue

        for item in items:
            # pattern 1 – browser‑extension dicts (the only shape observed in
            # current production data; carries per-item score)
            if isinstance(item, dict) and item.get("wikidata_uri"):
                uri = item["wikidata_uri"]
                if isinstance(uri, str) and uri.startswith("http"):
                    pairs.append((uri, _coerce_score(item.get("score"))))

            # pattern 2 – legacy two‑element lists; no per-item score, default to 0.0
            elif isinstance(item, list) and len(item) == 2:
                uri = item[1]
                if isinstance(uri, str) and uri.startswith("http"):
                    pairs.append((uri, 0.0))

            # pattern 3 – legacy schema using 'uri'‑array; one shared score per
            # item dict, applied to all URIs it contains
            elif isinstance(item, dict) and "uri" in item:
                shared_score = _coerce_score(item.get("score"))
                for u in item["uri"]:
                    if isinstance(u, str) and u.startswith("http"):
                        pairs.append((u, shared_score))

    return pairs

def create_or_link_wikidata_ontology_node(
    session: Session,
    node_id: int, # Previous version of this function used 'Optional[int]'
    uri: str,
    parent_data_list: List[Dict[str, str]],
    timestamp: str,
    score: float,
    depth: int,
    visited_uris: List[str],
    cache_db_path: Path
) -> None:
    """
    Recursively connects WikiData ontology nodes in Neo4j.
    """
    if depth > MAX_RECURSION_DEPTH: # Previous version was wrapped in 'try:'
        logger.debug(f"Max recursion depth reached for node_id={node_id}, uri={uri}")
        return

    for parent in parent_data_list:
        relationship = parent.get('relationship', '')
        parent_uri = parent.get('parent', '')
        parent_label = parent.get('parentLabel', '')

        if not parent_uri:
            continue
        if parent_uri in visited_uris:
            logger.debug(f"Cycle detected with {parent_uri}. Skipping.")
            continue
        visited_uris.append(parent_uri)

        # For example, link user node to ontology:
        user_to_ontology_rel_type = 'HAS_TOPIC'
        create_or_link_node(session, node_id, uri, parent_uri, parent_label, timestamp, score, user_to_ontology_rel_type)

        # Recurse upward unless top-level
        if parent_uri != TOP_LEVEL_URI:
            # get from cache or query again
            from_cache = get_from_cache(parent_uri, cache_db_path)
            if from_cache is None:
                from_cache = batch_query_wikidata([parent_uri], cache_db_path).get(parent_uri, [])
            create_or_link_wikidata_ontology_node(
                session, node_id, parent_uri, from_cache, timestamp, score, depth + 1, visited_uris, cache_db_path
            )

# Allowed relationship types for Cypher queries (prevents injection)
ALLOWED_RELATIONSHIP_TYPES = frozenset({
    'HAS_TOPIC', 'INITIATES_SEARCH', 'HAS_ANNOTATION', 'LINKS_TO', 'GENERATES_SERP', 'INITIATES'
})

def create_or_link_node(
    session: Session,
    node_id: int, # Previous version was 'node_id: Optional[int]'
    source_uri: str,
    target_uri: str,
    target_label: str,
    timestamp: str,
    score: float,
    relationship_type: str
) -> None:
    """
    Creates or links a node in Neo4j with a relationship from the user node to the ontology node.

    Note: relationship_type is validated against ALLOWED_RELATIONSHIP_TYPES to prevent Cypher injection.
    """
    # Validate relationship_type to prevent Cypher injection
    if relationship_type not in ALLOWED_RELATIONSHIP_TYPES:
        logger.error(f"Invalid relationship_type '{relationship_type}'. Must be one of {ALLOWED_RELATIONSHIP_TYPES}")
        raise ValueError(f"Invalid relationship_type: {relationship_type}")

    try:
        # Merge the wikiDataOntology node
        session.run(
            """
            MERGE (wdo:WikiDataOntology {uri: $targetUri})
            ON CREATE SET wdo.label = $targetLabel
            """,
            targetUri=target_uri,
            targetLabel=target_label
        )

        # Link from user node to wikiDataOntology
        session.run(
            f"""
            MATCH (n) WHERE id(n) = $node_id
            MERGE (wdo:WikiDataOntology {{uri: $targetUri}})
            MERGE (n)-[rel:{relationship_type} {{
                timestamp: $timestamp, 
                tfidf_score: $score
            }}]->(wdo)
            """,
            node_id=node_id,
            targetUri=target_uri,
            timestamp=timestamp,
            score=score
        )
        logger.debug(f"Linked user node {node_id} to ontology node {target_uri} with {relationship_type}")
    except Exception as e:
        logger.error(f"Error create_or_link_node: {e}", exc_info=True)

################################################################################
# The new manager class
################################################################################

class CoyoteOntologyStateManager:
    def __init__(self) -> None:
        """
        Initialize references to the Neo4j driver and 
        any local flags or concurrency states.
        """
        self._currently_processing = False
        self._neo4j_driver: Optional[Driver] = None

        # We'll also set up the local path for wikidata_cache
        self._cache_db_path: Path = Path('data/wikidata_cache.db')

        try:
            uri = get_setting('neo4j_uri')
            username = get_setting('neo4j_username')
            password = get_setting('neo4j_password', decrypt=True)

            if not all([uri, username, password]):
                logger.error("Neo4j credentials not found in the database. Ontology manager won't run properly.")
            else:
                self._neo4j_driver = GraphDatabase.driver(uri, auth=(username, password))
                logger.info("CoyoteOntologyStateManager: Connected to Neo4j.")
        except Exception as e:
            logger.error(f"CoyoteOntologyStateManager: Error connecting to Neo4j: {e}", exc_info=True)

        # Initialize wikiDataCache
        initialize_cache_db(self._cache_db_path)

    def _fetch_neo4j_done_events(self, limit: int) -> List[str]:
        """
        Query event_queue in coyote_state.db for events with status='neo4j_done'.
        Return their event_ids, and set them to 'ontology_in_progress'.
        """
        try:
            conn = get_state_db_connection()
            cursor = conn.cursor()
            cursor.execute("""
            SELECT event_id FROM event_queue
            WHERE status='neo4j_done'
            LIMIT ?
            """, (limit,))
            rows = cursor.fetchall()

            event_ids = [row[0] for row in rows]
            if event_ids:
                # Set them to 'ontology_in_progress'
                placeholders = ','.join('?' * len(event_ids))
                cursor.execute(f"""
                    UPDATE event_queue
                    SET status='ontology_in_progress'
                    WHERE event_id IN ({placeholders})
                """, event_ids)
                conn.commit()
                logger.info(f"Fetched and set to 'ontology_in_progress' {len(event_ids)} event(s).")
            else:
                logger.info("No events with status='neo4j_done' found.")

            conn.close()
            return event_ids
        except Exception as e:
            logger.error(f"_fetch_neo4j_done_events error: {e}", exc_info=True)
            return []

    def poll_and_process_ontology(self) -> None:
        """
        Main loop that periodically polls the event_queue for 'neo4j_done',
        processes them by linking to WikiData ontology, and updates to 'ontology_processed'.
        """
        logger.info("CoyoteOntologyStateManager: Starting ontology polling loop.")
        try:
            while True:
                if self._currently_processing:
                    logger.debug("CoyoteOntologyStateManager: Already processing; skipping this poll.")
                    time.sleep(POLL_INTERVAL_SECONDS)
                    continue

                # Make sure we have a Neo4j driver
                if not self._neo4j_driver:
                    logger.warning("No Neo4j driver available. Attempting reconnection.")
                    try:
                        self._neo4j_driver = connect_to_neo4j()
                    except Exception as e:
                        logger.error(f"Ontology manager: Neo4j reconnection failed: {e}", exc_info=True)
                        time.sleep(POLL_INTERVAL_SECONDS)
                        continue

                event_ids = self._fetch_neo4j_done_events(EVENTS_BATCH_SIZE)
                if not event_ids:
                    logger.debug("No 'neo4j_done' events to process. Sleeping.")
                    time.sleep(POLL_INTERVAL_SECONDS)
                    continue

                self._currently_processing = True
                try:
                    # For each event_id, find the relevant node(s) in Neo4j, link them to WikiData
                    with self._neo4j_driver.session() as session:
                        for event_id in event_ids:
                            self._process_single_event(session, event_id)
                finally:
                    self._currently_processing = False

                time.sleep(POLL_INTERVAL_SECONDS)

        except Exception as e:
            logger.exception(f"Unexpected error in poll_and_process_ontology: {e}")

    def _process_single_event(self, session: Session, event_id: str) -> None:
        """
        For a single event_id, find the relevant Neo4j nodes (usually by matching node event_id),
        do the WikiData linking, then set the event to 'ontology_processed'.
        """
        logger.info(f"Ontology manager: Processing event_id {event_id}")

        try:
            # Example: We can find user data nodes by matching node.event_id
            # If you stored the event_id property in Neo4j, do something like:
            query = """
            MATCH (n) WHERE n.event_id = $event_id
            RETURN id(n) AS node_id, n.entities AS entities, n.topics AS topics, 
                   n.textTopics AS textTopics, n.annotationTextEntities AS annotationTextEntities, 
                   n.highlightedTextEntities AS highlightedTextEntities,
                   n.timestamp AS timestamp
            """
            result = session.run(query, event_id=event_id)

            nodes_data = {}
            for record in result:
                node_id = record["node_id"]
                data_fields = ["entities","topics","textTopics","annotationTextEntities","highlightedTextEntities"]
                node_data = {f: record.get(f) or '[]' for f in data_fields}
                node_data["timestamp"] = record.get("timestamp", "")
                nodes_data[node_id] = node_data

            if not nodes_data:
                logger.info(f"No relevant nodes found in Neo4j for event_id {event_id}. Marking as ontology_processed.")
                self._update_event_queue_status(event_id, "ontology_processed")
                return

            # Process all URIs with per-item scores
            for node_id, data in nodes_data.items():
                uri_score_pairs = extract_uris_from_node_data(data)
                if not uri_score_pairs:
                    logger.info(f"No URIs found for node {node_id}")
                    continue

                timestamp = data.get('timestamp', '')
                for uri, score in uri_score_pairs:
                    # Query or retrieve from cache
                    from_cache = get_from_cache(uri, self._cache_db_path)
                    if from_cache is None:
                        # do batch query for just [uri]
                        from_cache = batch_query_wikidata([uri], self._cache_db_path).get(uri, [])
                    create_or_link_wikidata_ontology_node(
                        session, node_id, uri, from_cache, timestamp, score, 1, visited_uris=[uri], cache_db_path=self._cache_db_path
                    )

            # Once finished linking all nodes, set the event to "ontology_processed"
            self._update_event_queue_status(event_id, "ontology_processed")
            logger.info(f"Finished processing ontology for event_id={event_id}, set to 'ontology_processed'.")

        except Exception as e:
            logger.error(f"Error processing ontology for event_id={event_id}: {e}", exc_info=True)
            # Optionally set to some error status
            self._update_event_queue_status(event_id, "ontology_failed")

    def _update_event_queue_status(self, event_id: str, new_status: str) -> None:
        """
        Update the event_queue in coyote_state.db for the given event_id.
        """
        try:
            conn = get_state_db_connection()
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE event_queue SET status = ?, processed_at = CURRENT_TIMESTAMP WHERE event_id = ?",
                (new_status, event_id)
            )
            conn.commit()
            conn.close()
            logger.debug(f"event_queue status for event_id={event_id} updated to '{new_status}'.")
        except Exception as e:
            logger.error(f"Error updating event_queue status for event_id={event_id} to {new_status}: {e}", exc_info=True)

################################################################################
# If you want to run this as a standalone script
################################################################################

def main() -> None:
    """
    Example main function if you want to run 'connect_to_ontology.py' as a separate script.
    """
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    logger.info("Starting ontology connection process (standalone).")

    # spawn the manager
    manager = CoyoteOntologyStateManager()
    manager.poll_and_process_ontology()

if __name__ == '__main__':
    main()
