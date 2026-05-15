"""
connect_to_ontology.py

Module for connecting user data nodes in Neo4j to the WikiData ontology.
Uses event_queue in coyote_state.db to poll for events with status "neo4j_done",
runs the ontology-connection logic, and updates events to "ontology_processed".
"""

import json
import logging
import os
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.error import HTTPError

from neo4j import GraphDatabase, Driver, Session
from SPARQLWrapper import SPARQLWrapper, JSON, SPARQLExceptions

from coyote.utils.config_manager import (
    get_setting,
    get_state_read_only_connection,
    get_state_db_connection,
    connect_to_neo4j
)

logger = logging.getLogger(__name__)

# --- WikiData circuit breaker -------------------------------------------------
# Trips on 403/429 from query.wikidata.org. Once tripped, batch_query_wikidata()
# short-circuits without making SPARQL calls until the cooldown expires.
# A failed probe in the half_open state immediately re-trips.
#
# This is a separate breaker instance from the one in
# text_bertopic_analysis.py. Both target the same endpoint from the same client
# IP and will typically trip near-simultaneously when WDQS rate-limits. Sharing
# state across modules would be cleaner; unification is a post-MVP follow-up.
#
# Bail semantics: when the breaker trips mid-batch, URIs from the failed
# batch and any later batches are absent from the returned dict. Callers
# (`_process_single_event` line ~544, `create_or_link_wikidata_ontology_node`
# line ~321) use `.get(uri, [])` and treat the missing URI as "no parents."
# This is intentional — better to skip than extend the WDQS ban — but it
# means a breaker-bail is indistinguishable from "URI legitimately has no
# parents." Cross-reference with `WikiData circuit breaker tripped` log lines
# to recover. See CLAUDE.md "WikiData-throttled events not tagged in Neo4j."
_BREAKER_FAILURE_THRESHOLD = int(os.environ.get("WIKIDATA_BREAKER_THRESHOLD", "1"))
_BREAKER_COOLDOWN_SECONDS  = int(os.environ.get("WIKIDATA_BREAKER_COOLDOWN", "1800"))
_BREAKER_RETRY_AFTER_CAP   = 3600  # bound server-supplied cooldowns

_BREAKER_STATE: str = "closed"  # "closed" | "open" | "half_open"
_BREAKER_CONSECUTIVE_FAILURES: int = 0
_BREAKER_OPEN_UNTIL: Optional[datetime] = None
_BREAKER_LOCK = threading.Lock()


def _breaker_check_state() -> str:
    """Return effective state; transitions open→half_open if cooldown expired."""
    global _BREAKER_STATE
    with _BREAKER_LOCK:
        if _BREAKER_STATE == "open" and _BREAKER_OPEN_UNTIL is not None:
            if datetime.utcnow() >= _BREAKER_OPEN_UNTIL:
                _BREAKER_STATE = "half_open"
                logger.info("WikiData circuit breaker (ontology): open → half_open")
        return _BREAKER_STATE


def _breaker_record_success() -> None:
    """Reset to closed; logs recovery if breaker had been open/half_open."""
    global _BREAKER_STATE, _BREAKER_CONSECUTIVE_FAILURES, _BREAKER_OPEN_UNTIL
    with _BREAKER_LOCK:
        prev = _BREAKER_STATE
        _BREAKER_STATE = "closed"
        _BREAKER_CONSECUTIVE_FAILURES = 0
        _BREAKER_OPEN_UNTIL = None
        if prev != "closed":
            logger.info("WikiData circuit breaker (ontology): %s → closed (recovered)", prev)


def _breaker_record_failure(retry_after_seconds: Optional[int] = None) -> None:
    """Increment failure counter; trip if threshold reached. Half-open probe
    failure re-trips immediately regardless of threshold."""
    global _BREAKER_STATE, _BREAKER_CONSECUTIVE_FAILURES, _BREAKER_OPEN_UNTIL
    with _BREAKER_LOCK:
        cooldown = retry_after_seconds if retry_after_seconds else _BREAKER_COOLDOWN_SECONDS
        cooldown = min(cooldown, _BREAKER_RETRY_AFTER_CAP)
        if _BREAKER_STATE == "half_open":
            _BREAKER_STATE = "open"
            _BREAKER_OPEN_UNTIL = datetime.utcnow() + timedelta(seconds=cooldown)
            logger.warning(
                "WikiData circuit breaker (ontology) re-tripped from half_open, cooldown=%ds",
                cooldown,
            )
            return
        _BREAKER_CONSECUTIVE_FAILURES += 1
        if _BREAKER_CONSECUTIVE_FAILURES >= _BREAKER_FAILURE_THRESHOLD:
            _BREAKER_STATE = "open"
            _BREAKER_OPEN_UNTIL = datetime.utcnow() + timedelta(seconds=cooldown)
            logger.warning(
                "WikiData circuit breaker (ontology) tripped: %d consecutive failure(s), cooldown=%ds",
                _BREAKER_CONSECUTIVE_FAILURES, cooldown,
            )


def _breaker_reset_for_tests() -> None:
    """Test-only: reset module state. Do not call from production code."""
    global _BREAKER_STATE, _BREAKER_CONSECUTIVE_FAILURES, _BREAKER_OPEN_UNTIL
    with _BREAKER_LOCK:
        _BREAKER_STATE = "closed"
        _BREAKER_CONSECUTIVE_FAILURES = 0
        _BREAKER_OPEN_UNTIL = None


def _parse_retry_after(headers) -> Optional[int]:
    """Parse Retry-After HTTP header. Integer-seconds form only; HTTP-date
    form returns None (caller falls back to default cooldown)."""
    if headers is None:
        return None
    try:
        value = headers.get("Retry-After")
    except (AttributeError, TypeError):
        return None
    if not value:
        return None
    try:
        seconds = int(value)
    except (ValueError, TypeError):
        return None
    return seconds if seconds >= 0 else None
# -----------------------------------------------------------------------------

# Adjust these if needed
MAX_RECURSION_DEPTH = 3 # Limit recursion depth in WikiData's ontology hierarchy (Session 3: 5 -> 3)
TOP_LEVEL_URI = "http://www.wikidata.org/entity/Q35120" # Top-level 'entity' node in WikiData ontology
CACHE_EXPIRATION_DAYS = 7
POLL_INTERVAL_SECONDS = 60  # how often poll_and_process_ontology checks for "neo4j_done" events
EVENTS_BATCH_SIZE = 5      # how many events to handle per cycle

# Wikimedia infrastructure Q-items. P31 (instance-of) traversal can leak these
# even after P910 is removed (e.g., a non-category entity whose instance-of is
# a Wikimedia meta-class). Filter at cache-write time so they never persist
# and never get traversed. See Session 3 (MVP).
WIKIMEDIA_META_QIDS = frozenset({
    "Q4167836",   # Wikimedia category
    "Q15184295",  # Wikimedia administration category
    "Q4167410",   # Wikimedia disambiguation page
    "Q14204246",  # Wikimedia project page
    "Q11266439",  # Wikimedia template
    "Q13406463",  # Wikimedia list article
})
WIKIMEDIA_META_URIS = frozenset(
    f"http://www.wikidata.org/entity/{q}" for q in WIKIMEDIA_META_QIDS
)

# HAS_TOPIC edges with tfidf_score below this threshold are dropped at the
# root level of _process_single_event's URI loop. The whole recursive
# WikiData ancestor tree for a dropped root is also skipped (descendants
# inherit the root's score). Legacy URI patterns 2 and 3 default to score
# 0.0 in extract_uris_from_node_data, so any positive threshold drops them
# entirely; that is intentional — Pattern 1 is the only shape current
# production NLP writes.
def _read_threshold_env() -> float:
    raw = os.environ.get("TFIDF_TOPIC_THRESHOLD", "0.15")
    try:
        return float(raw)
    except (TypeError, ValueError):
        logger.warning(
            "Invalid TFIDF_TOPIC_THRESHOLD=%r; falling back to 0.15", raw
        )
        return 0.15

TFIDF_TOPIC_THRESHOLD = _read_threshold_env()

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

    Breaker: if open, this returns immediately with whatever was already
    cached. URIs not yet queried are absent from the returned dict; callers
    use `.get(uri, [])` and proceed with empty parent lists. See module-top
    breaker block for full bail semantics.

    Caching policy on failure:
      - 403/429: do NOT cache. Breaker handles suppression for cooldown.
      - 5xx (EndPointInternalError): do NOT cache. Retry on next event.
      - Other exceptions: do NOT cache. Logged for diagnosis.
      - Success with empty bindings for a URI: cache `[]` (legitimate
        "no parents" state, prevents redundant re-queries).
    """
    unique_uris = list(set(uris))
    cached_data: Dict[str, Any] = {}
    uncached_uris: List[str] = []

    # Check cache. NOTE: `is not None` — an empty list cached as "no parents"
    # is a valid cache hit and must not be re-queried.
    for uri in unique_uris:
        data = get_from_cache(uri, cache_db_path)
        if data is not None:
            logger.info(f"Cache hit for URI {uri}")
            cached_data[uri] = data
        else:
            uncached_uris.append(uri)

    if not uncached_uris:
        return cached_data

    # Bail before SPARQL setup if breaker is already open.
    if _breaker_check_state() == "open":
        logger.debug("WikiData circuit breaker (ontology) is open; skipping batch of %d URIs",
                     len(uncached_uris))
        return cached_data

    BATCH_SIZE = 50
    for i in range(0, len(uncached_uris), BATCH_SIZE):
        batch_uris = uncached_uris[i:i+BATCH_SIZE]
        uris_str = ' '.join(f"wd:{uri.split('/')[-1]}" for uri in batch_uris)
        # Session 3: P910 ("topic's main category") removed. P910 was the
        # gateway to "Category:X" parents and the Wikimedia-meta cascade
        # (Wikimedia category / Wikimedia administration category etc.)
        # that dominated the ancestor traversal noise.
        query = f"""
        SELECT ?item ?parent ?parentLabel ?relationship
        WHERE {{
            VALUES ?item {{ {uris_str} }}
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
        # Wikimedia explicitly blocks generic User-Agent strings.
        sparql.agent = (
            "Coyote/0.4 (https://github.com/captaininfo/coyote; "
            "mailto:lifewidelearningllc@gmail.com)"
        )
        sparql.setQuery(query)
        sparql.setReturnFormat(JSON)

        # Re-check breaker between batches — a concurrent thread (unlikely
        # given _currently_processing) could have tripped it since the
        # pre-loop check.
        if _breaker_check_state() == "open":
            logger.debug("WikiData circuit breaker (ontology) opened between batches; bailing")
            return cached_data

        try:
            logger.info(f"Querying WikiData for batch of {len(batch_uris)} URIs.")
            results = sparql.query().convert()
            _breaker_record_success()
            results_dict: Dict[str, List[Dict[str, str]]] = {}
            for result in results["results"]["bindings"]:
                item_uri = result["item"]["value"]
                parent_data = {
                    "parent": result.get("parent", {}).get("value", ""),
                    "parentLabel": result.get("parentLabel", {}).get("value", ""),
                    "relationship": result.get("relationship", {}).get("value", "")
                }
                results_dict.setdefault(item_uri, []).append(parent_data)
            # Session 3: filter Wikimedia meta-class parents pre-cache so
            # the junk never persists and never gets traversed.
            for uri in batch_uris:
                raw_parents = results_dict.get(uri, [])
                data_to_cache = [
                    p for p in raw_parents
                    if p.get("parent") not in WIKIMEDIA_META_URIS
                ]
                save_to_cache(uri, data_to_cache, cache_db_path)
                cached_data[uri] = data_to_cache
            time.sleep(1)  # Throttle for courtesy
        except HTTPError as e:
            retry_after = _parse_retry_after(e.headers)
            logger.warning(
                "WikiData HTTP %d on batch of %d URIs%s",
                e.code, len(batch_uris),
                f" (Retry-After: {retry_after}s)" if retry_after else "",
            )
            if e.code in (403, 429):
                _breaker_record_failure(retry_after_seconds=retry_after)
                # Bail entire call. Remaining batches are absent from
                # `cached_data`; callers default to [] via `.get(uri, [])`.
                # Do NOT write [] to cache here — the breaker is the
                # suppression mechanism; caching [] would persist a wrong
                # "no parents" answer for CACHE_EXPIRATION_DAYS even after
                # WDQS recovers.
                return cached_data
            # Other 4xx (e.g., 400 from malformed SPARQL) is a real bug
            # in our query construction; surface it.
            logger.error("WikiData HTTP %d (unexpected) on batch: %s",
                         e.code, batch_uris, exc_info=True)
        except SPARQLExceptions.EndPointInternalError as e:
            # 5xx is transient server-side; log and continue to next batch.
            # Does NOT count toward breaker. URI is not cached, so next event
            # will retry it (after breaker check).
            logger.warning(
                "WikiData 5xx on batch of %d URIs: %s",
                len(batch_uris), e,
            )
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
                skipped_below_threshold = 0
                for uri, score in uri_score_pairs:
                    if score < TFIDF_TOPIC_THRESHOLD:
                        skipped_below_threshold += 1
                        logger.debug(
                            "Skipping low-importance topic uri=%s score=%.4f < threshold=%.3f",
                            uri, score, TFIDF_TOPIC_THRESHOLD,
                        )
                        continue
                    # Query or retrieve from cache
                    from_cache = get_from_cache(uri, self._cache_db_path)
                    if from_cache is None:
                        # do batch query for just [uri]
                        from_cache = batch_query_wikidata([uri], self._cache_db_path).get(uri, [])
                    create_or_link_wikidata_ontology_node(
                        session, node_id, uri, from_cache, timestamp, score, 1, visited_uris=[uri], cache_db_path=self._cache_db_path
                    )
                if skipped_below_threshold:
                    logger.info(
                        "Threshold %.3f filtered %d/%d URIs for node %s",
                        TFIDF_TOPIC_THRESHOLD, skipped_below_threshold,
                        len(uri_score_pairs), node_id,
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
