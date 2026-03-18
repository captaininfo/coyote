"""
coyote_browser_extension_to_neo4j.py

Module for processing data from the Coyote browser extension and inserting it into Neo4j.
"""

import logging
import json  # Added to perform JSON serialization
from typing import Any, Dict, Optional, Tuple, TYPE_CHECKING

from neo4j import Session, Transaction
import sqlite3

# Removed the direct import to prevent circular dependency
# from coyote.neo4j_integration.coyote_neo4j_state_manager import CoyoteNeo4jStateManager

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from coyote.neo4j_integration.coyote_neo4j_state_manager import CoyoteNeo4jStateManager


def process_coyote_browser_extension_data(
    session: Session,
    event_data: Dict[str, Any],
    state_manager: "CoyoteNeo4jStateManager",  # Use string for forward reference
    cursor: sqlite3.Cursor
) -> Tuple[Optional[int], Optional[int]]:
    """
    Process data from the Coyote browser extension and update the Neo4j database.

    Args:
        session (Session): The Neo4j database session.
        event_data (Dict[str, Any]): The data entry to process.
        state_manager (CoyoteNeo4jStateManager): The state manager for tracking node IDs.
        cursor (sqlite3.Cursor): The SQLite cursor for querying data.

    Returns:
        Tuple[Optional[int], Optional[int]]: The IDs of the created purpose and search terms nodes.
    """
    purpose_id = None
    search_terms_id = None

    try:
        logger.debug(f"Starting to process event_data: {event_data}")

        event = event_data.get("event_type")  # Updated from "event" to "event_type"
        if not event:
            logger.warning("Event type is missing in event_data. Skipping.")
            return purpose_id, search_terms_id

        logger.debug(f"Processing event: {event}")

        # Extract and set timestamp and data source
        timestamp = event_data.get("timestamp")
        data_source = event_data.get("data_source", "Coyote Browser Extension")
        logger.debug(f"Extracted timestamp: {timestamp}, data_source: {data_source}")

        # Fetch data from SQLite databases using the passed cursor
        search_event_id = event_data.get("event_id")
        if not search_event_id:
            logger.warning("No event_id found in event_data. Skipping.")
            return purpose_id, search_terms_id

        logger.debug(f"Fetching Purpose and Topics data for event_id: {search_event_id}")

        if event == "User starts or modifies a search":
            # Fetch Purpose and Search Terms from SearchEvents
            cursor.execute(
                "SELECT purpose, search_terms, search_terms_relevance_score FROM SearchEvents WHERE event_id = ?",
                (search_event_id,)
            )
            search_event = cursor.fetchone()
            if search_event:
                purpose_text = search_event[0] or "No Purpose"
                search_terms = search_event[1] or "No Search Terms"
                logger.debug(f"Search Terms: {search_terms}")
                search_terms_relevance_score = search_event[2]
            else:
                logger.warning(f"No SearchEvents data found for event_id {search_event_id}. Using defaults.")
                purpose_text = "No Purpose"
                search_terms = "No Search Terms"
                search_terms_relevance_score = 0.0  # or another default value

            # Fetch timestamp from Events
            cursor.execute(
                "SELECT timestamp FROM Events WHERE event_id = ?",
                (search_event_id,)
            )
            event_row = cursor.fetchone()
            if event_row:
                purpose_timestamp = event_row[0]
            else:
                logger.warning(f"No Events data found for event_id {search_event_id}. Using default timestamp.")
                purpose_timestamp = "Unknown Timestamp"

            logger.debug(f"Purpose Text: {purpose_text}, Search Terms: {search_terms}, Search Terms Relevance Score: {search_terms_relevance_score}, Timestamp: {timestamp}")

            # Fetch Topics related to Purpose
            cursor.execute(
                """
                SELECT topic, wikidata_uri, label FROM Topics
                WHERE event_id = ? AND topic_context = 'purpose'
                """,
                (search_event_id,)
            )
            purpose_topics = cursor.fetchall()
            logger.debug(f"Fetched Purpose Topics: {purpose_topics}")

            # Build list of dictionaries then JSON-serialize it
            purpose_topics_list = [
                {"topic": row[0], "wikidata_uri": row[1], "label": row[2]} for row in purpose_topics
            ]
            purpose_topics_json = json.dumps(purpose_topics_list)

            # Fetch Entities related to Purpose
            cursor.execute(
                """
                SELECT entity, wikidata_uri, label FROM Entities
                WHERE event_id = ? AND entity_context = 'purpose'
                """,
                (search_event_id,)
            )
            purpose_entities = cursor.fetchall()
            logger.debug(f"Fetched Purpose Entities: {purpose_entities}")

            purpose_entities_list = [
                {"entity": row[0], "wikidata_uri": row[1], "label": row[2]} for row in purpose_entities
            ]
            purpose_entities_json = json.dumps(purpose_entities_list)

            # Fetch Topics related to Search Terms
            cursor.execute(
                """
                SELECT topic, wikidata_uri, label FROM Topics
                WHERE event_id = ? AND topic_context = 'search_terms'
                """,
                (search_event_id,)
            )
            search_terms_topics = cursor.fetchall()
            logger.debug(f"Fetched Search Terms Topics: {search_terms_topics}")

            search_terms_topics_list = [
                {"topic": row[0], "wikidata_uri": row[1], "label": row[2]} for row in search_terms_topics
            ]
            search_terms_topics_json = json.dumps(search_terms_topics_list)

            # Fetch Entities related to Search Terms
            cursor.execute(
                """
                SELECT entity, wikidata_uri, label FROM Entities
                WHERE event_id = ? AND entity_context = 'search_terms'
                """,
                (search_event_id,)
            )
            search_terms_entities = cursor.fetchall()
            logger.debug(f"Fetched Search Terms Entities: {search_terms_entities}")

            search_terms_entities_list = [
                {"entity": row[0], "wikidata_uri": row[1], "label": row[2]} for row in search_terms_entities
            ]
            search_terms_entities_json = json.dumps(search_terms_entities_list)

            # Fetch Relevance related to Search Terms
            cursor.execute(
                """
                SELECT search_terms_relevance_score FROM SearchEvents
                WHERE event_id = ?
                """,
                (search_event_id,)
            )
            search_terms_relevance = cursor.fetchall()
            logger.debug(f"Fetched Search Terms Relevance: {search_terms_relevance}")

            # Convert relevance scores (list of numbers) to JSON string
            search_terms_relevance_list = [row[0] for row in search_terms_relevance]
            search_terms_relevance_json = json.dumps(search_terms_relevance_list)

            logger.info(f"Inserting Purpose and Search Terms with timestamp: {timestamp}")
            event_id = search_event_id  # Already validated above

            result = session.execute_write(
                lambda tx: _create_purpose_and_search_terms(
                    tx,
                    event_id,
                    purpose_text,
                    purpose_topics_json,
                    purpose_entities_json,
                    search_terms,
                    search_terms_topics_json,
                    search_terms_entities_json,
                    search_terms_relevance_json,
                    purpose_timestamp,
                    data_source
                )
            )

            if result:
                purpose_id, search_terms_id = result
                logger.debug(f"Created Purpose ID: {purpose_id}, Search Terms ID: {search_terms_id}")

            # Update state manager with the latest SearchTerms node ID
            state_manager.last_search_terms_node_id = search_terms_id
            logger.debug(f"Updated state manager with last_search_terms_node_id: {search_terms_id}")


        elif event == "Webpage loads":
            logger.debug(f"Processing event: {event}")

            # Extract and set timestamp and data source
            timestamp = event_data.get("timestamp")
            data_source = event_data.get("data_source", "Coyote Browser Extension")
            logger.debug(f"Extracted timestamp: {timestamp}, data_source: {data_source}")

            # Fetch data from SQLite databases using the passed cursor
            webpage_event_id = event_data.get("event_id")
            if not webpage_event_id:
                logger.warning("No event_id found in event_data. Skipping.")
                return purpose_id, search_terms_id

            # ── NEW: fetch the URL, title, summary from the WebpageLoads table ─────────
            cursor.execute(
                """
                SELECT
                    url,
                    webpage_title   AS title,
                    webpage_summary AS summary
                FROM WebpageLoads
                WHERE event_id = ?
                """,
                (webpage_event_id,)
            )
            row = cursor.fetchone()
            if row:
                url, title, summary = (row[0] or "No URL",
                                    row[1] or "No Title",
                                    row[2] or "No Summary")
            else:
                logger.warning("No WebpageLoads row for %s – using defaults", webpage_event_id)
                url, title, summary = "No URL", "No Title", "No Summary"

            # Fetch Topics related to Webpage
            cursor.execute(
                """
                SELECT topic, wikidata_uri, label, score FROM Topics
                WHERE event_id = ? AND topic_context = 'webpage'
                ORDER BY score DESC
                """,
                (webpage_event_id,)
            )
            topics = cursor.fetchall()
            topics_list = [
                {"topic": row[0], 
                 "wikidata_uri": row[1], 
                 "label": row[2], 
                 "score": row[3] if row[3] is not None else 0.0
                 } for row in topics
            ]
            topics_json = json.dumps(topics_list)

            # Fetch Entities related to Webpage
            cursor.execute(
                """
                SELECT entity, wikidata_uri, label, score FROM Entities
                WHERE event_id = ? AND entity_context = 'webpage'
                ORDER BY score DESC
                """,
                (webpage_event_id,)
            )
            entities = cursor.fetchall()
            entities_list = [
                {
                    "entity": row[0], 
                    "wikidata_uri": row[1], 
                    "label": row[2],
                    "score": row[3] if row[3] is not None else 0.0
                } 
                for row in entities
            ]
            entities_json = json.dumps(entities_list)

            # Determine if the webpage is a SERP
            is_serp = "- Google Search" in title or url.startswith("https://www.google.com/search?")

            logger.info(f"Inserting Webpage with URL: {url} at timestamp: {timestamp}")
            webpage_id = session.execute_write(
                lambda tx: _create_and_link_webpage(
                    tx,
                    webpage_event_id,
                    state_manager.last_webpage_node_id,
                    state_manager.last_search_terms_node_id,
                    url,
                    title,
                    summary,
                    topics_json,
                    entities_json,
                    is_serp,
                    timestamp,
                    data_source
                )
            )
            logger.info(f"Webpage node created with ID: {webpage_id}")
            # Update state manager with the latest Webpage node ID
            state_manager.last_webpage_node_id = webpage_id

    except Exception as e:
        logger.error(f"Error in process_coyote_browser_extension_data: {e}", exc_info=True)
        raise

    return purpose_id, search_terms_id


def _create_purpose_and_search_terms(
    tx: Transaction,
    event_id: str,
    purpose_text: str,
    purpose_topics: str,
    purpose_entities: str,
    search_terms: str,
    search_terms_topics: str,
    search_terms_entities: str,
    search_terms_relevance: str,
    timestamp: str,
    data_source: str
) -> Tuple[int, int]:
    """
    Create Purpose and SearchTerms nodes in the Neo4j database.

    Args:
        tx (Transaction): The Neo4j transaction.
        event_id (str): The ID tied to the user event. 
        purpose_text (str): The text of the purpose.
        purpose_topics (list): JSON-serialized string of purpose topics.
        purpose_entities (list): JSON-serialized string of purpose entities.
        search_terms (str): The search terms.
        search_terms_topics (list): JSON-serialized string of search terms topics.
        search_terms_entities (list): JSON-serialized string of search terms entities.
        search_terms_relevance (list): JSON-serialized string of search terms relevance.
        timestamp (str): The timestamp.
        data_source (str): The data source.

    Returns:
        Tuple[int, int]: The IDs of the created Purpose and SearchTerms nodes.
    """
    query = """
    CREATE (p:Purpose {
        event_id: $event_id,
        text: $purpose_text,
        topics: $purpose_topics,
        entities: $purpose_entities,
        timestamp: $timestamp,
        dataSource: $data_source,
        isInput: false
    })
    CREATE (st:SearchTerms {
        event_id: $event_id,
        text: $search_terms,
        topics: $search_terms_topics,
        entities: $search_terms_entities,
        relevance: $search_terms_relevance,
        timestamp: $timestamp,
        dataSource: $data_source,
        isInput: false
    })
    CREATE (p)-[:INITIATES_SEARCH]->(st)
    RETURN id(p) AS purpose_id, id(st) AS search_terms_id
    """
    result = tx.run(
        query,
        event_id=event_id,
        purpose_text=purpose_text,
        purpose_topics=purpose_topics,
        purpose_entities=purpose_entities,
        search_terms=search_terms,
        search_terms_topics=search_terms_topics,
        search_terms_entities=search_terms_entities,
        search_terms_relevance=search_terms_relevance,
        timestamp=timestamp,
        data_source=data_source
    ).single()
    return result["purpose_id"], result["search_terms_id"]


def _create_and_link_webpage(
    tx: Transaction,
    event_id: str,
    last_webpage_node_id: Optional[int],
    last_search_terms_node_id: Optional[int],
    url: str,
    title: str,
    summary: str,
    topics: str,
    entities: str,
    is_serp: bool,
    timestamp: str,
    data_source: str
) -> int:
    """
    Create a Webpage node and link it to the previous node in the Neo4j database.
    If no previous node exists, create an orphan Webpage node.

    Returns:
        int: The ID of the created Webpage node.
    """
    # Determine the target node and relationship type
    if last_webpage_node_id is not None:
        target_node_id = last_webpage_node_id
        rel_type = 'LINKS_TO'
    elif last_search_terms_node_id is not None:
        target_node_id = last_search_terms_node_id
        rel_type = 'GENERATES_SERP' if is_serp else 'INITIATES'
    else:
        # If there is literally no previous node, create an 'orphan' Webpage node
        logger.info("No previous node found; creating an orphan Webpage node.")
        create_orphan_webpage_query = """
        CREATE (w:Webpage {
            event_id: $event_id,
            url: $url,
            title: $title,
            summary: $summary,
            topics: $topics,
            entities: $entities,
            isSERP: $is_serp,
            timestamp: $timestamp,
            dataSource: $data_source,
            isInput: true
        })
        RETURN id(w) AS id
        """
        result = tx.run(
            create_orphan_webpage_query,
            event_id=event_id,
            url=url,
            title=title,
            summary=summary,
            topics=topics,
            entities=entities,
            is_serp=is_serp,
            timestamp=timestamp,
            data_source=data_source
        ).single()
        return result["id"]

    # If we do have a target node, link the new Webpage to it
    query = f"""
    MATCH (node) WHERE id(node) = $node_id
    CREATE (w:Webpage {{
        event_id: $event_id,
        url: $url,
        title: $title,
        summary: $summary,
        topics: $topics,
        entities: $entities,
        isSERP: $is_serp,
        timestamp: $timestamp,
        dataSource: $data_source,
        isInput: true
    }})
    CREATE (node)-[:{rel_type}]->(w)
    RETURN id(w) AS id
    """
    result = tx.run(
        query,
        node_id=target_node_id,
        event_id=event_id,
        url=url,
        title=title,
        summary=summary,
        topics=topics,
        entities=entities,
        is_serp=is_serp,
        timestamp=timestamp,
        data_source=data_source
    ).single()
    return result["id"]
