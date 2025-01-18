"""
coyote_browser_extension_to_neo4j.py

Module for processing data from the Coyote browser extension and inserting it into Neo4j.
"""

import logging
from typing import Any, Dict, Optional, Tuple

from neo4j import Session, Transaction
import sqlite3

from coyote.utils.coyote_state_manager import CoyoteStateManager

logger = logging.getLogger(__name__)


def process_coyote_browser_extension_data(
    session: Session,
    event_data: Dict[str, Any],
    state_manager: CoyoteStateManager,
    cursor: sqlite3.Cursor
) -> Tuple[Optional[int], Optional[int]]:
    """
    Process data from the Coyote browser extension and update the Neo4j database.

    Args:
        session (Session): The Neo4j database session.
        event_data (Dict[str, Any]): The data entry to process.
        state_manager (CoyoteStateManager): The state manager for tracking node IDs.
        cursor (sqlite3.Cursor): The SQLite cursor for querying data.

    Returns:
        Tuple[Optional[int], Optional[int]]: The IDs of the created purpose and search terms nodes.
    """
    purpose_id = None
    search_terms_id = None

    try:
        event = event_data.get("event")
        if event == "User starts or modifies a search":
            # Extract and set timestamp and data source
            timestamp = event_data.get("timestamp")
            data_source = event_data.get("dataSource", "Coyote Browser Extension")
            
            # Fetch data from SQLite databases using the passed cursor
            search_event_id = event_data.get("event_id")
            if not search_event_id:
                logger.warning("No event_id found in event_data. Skipping.")
                return purpose_id, search_terms_id

            # Fetch Purpose data from SearchEvents
            cursor.execute(
                "SELECT purpose, timestamp FROM SearchEvents WHERE event_id = ?",
                (search_event_id,)
            )
            search_event = cursor.fetchone()
            if search_event:
                purpose_text = search_event[0] or "No Purpose"
                purpose_timestamp = search_event[1]
            else:
                logger.warning(f"No SearchEvents data found for event_id {search_event_id}. Using defaults.")
                purpose_text = "No Purpose"
                purpose_timestamp = timestamp or "Unknown Timestamp"

            # Fetch Topics related to Purpose
            cursor.execute(
                """
                SELECT topic, wikidata_url, label FROM Topics
                WHERE event_id = ? AND related_to = 'purpose'
                """,
                (search_event_id,)
            )
            purpose_topics = cursor.fetchall()
            purpose_topics_json = [
                {"topic": row[0], "wikidata_url": row[1], "label": row[2]} for row in purpose_topics
            ]

            # Fetch Entities related to Purpose
            cursor.execute(
                """
                SELECT entity, wikidata_url, label FROM Entities
                WHERE event_id = ? AND related_to = 'purpose'
                """,
                (search_event_id,)
            )
            purpose_entities = cursor.fetchall()
            purpose_entities_json = [
                {"entity": row[0], "wikidata_url": row[1], "label": row[2]} for row in purpose_entities
            ]

            # Fetch Search Terms data
            search_terms = event_data.get("searchTerms", "No Search Terms")

            # Fetch Topics related to Search Terms
            cursor.execute(
                """
                SELECT topic, wikidata_url, label FROM Topics
                WHERE event_id = ? AND related_to = 'search_terms'
                """,
                (search_event_id,)
            )
            search_terms_topics = cursor.fetchall()
            search_terms_topics_json = [
                {"topic": row[0], "wikidata_url": row[1], "label": row[2]} for row in search_terms_topics
            ]

            # Fetch Entities related to Search Terms
            cursor.execute(
                """
                SELECT entity, wikidata_url, label FROM Entities
                WHERE event_id = ? AND related_to = 'search_terms'
                """,
                (search_event_id,)
            )
            search_terms_entities = cursor.fetchall()
            search_terms_entities_json = [
                {"entity": row[0], "wikidata_url": row[1], "label": row[2]} for row in search_terms_entities
            ]

            # Fetch Relevance related to Search Terms
            cursor.execute(
                """
                SELECT relevance FROM Relevance
                WHERE event_id = ? AND related_to = 'search_terms'
                """,
                (search_event_id,)
            )
            search_terms_relevance = cursor.fetchall()
            search_terms_relevance_json = [row[0] for row in search_terms_relevance]

            logger.info(f"Inserting Purpose and SearchTerms with timestamp: {timestamp}")
            result = session.execute_write(
                _create_purpose_and_search_terms,
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
            if result:
                purpose_id, search_terms_id = result

            # Update state manager with the latest SearchTerms node ID
            state_manager.last_search_terms_node_id = search_terms_id

        elif event == "Webpage loads":
            # Extract and set timestamp and data source
            timestamp = event_data.get("timestamp")
            data_source = event_data.get("dataSource", "Coyote Browser Extension")
            url = event_data.get("url", "No URL")
            title = event_data.get("webpageTitle", "No Title")
            summary = event_data.get("webpageSummary", "No Summary")
            
            # Fetch data from SQLite databases using the passed cursor
            webpage_event_id = event_data.get("event_id")
            if not webpage_event_id:
                logger.warning("No event_id found in event_data. Skipping.")
                return purpose_id, search_terms_id

            # Fetch Topics related to Webpage
            cursor.execute(
                """
                SELECT topic, wikidata_url, label FROM Topics
                WHERE event_id = ? AND related_to = 'webpage'
                """,
                (webpage_event_id,)
            )
            topics = cursor.fetchall()
            topics_json = [
                {"topic": row[0], "wikidata_url": row[1], "label": row[2]} for row in topics
            ]

            # Fetch Entities related to Webpage
            cursor.execute(
                """
                SELECT entity, wikidata_url, label FROM Entities
                WHERE event_id = ? AND related_to = 'webpage'
                """,
                (webpage_event_id,)
            )
            entities = cursor.fetchall()
            entities_json = [
                {"entity": row[0], "wikidata_url": row[1], "label": row[2]} for row in entities
            ]

            # Determine if the webpage is a SERP
            is_serp = "- Google Search" in title or url.startswith("https://www.google.com/search?")

            logger.info(f"Inserting Webpage with URL: {url} at timestamp: {timestamp}")
            webpage_id = session.execute_write(
                _create_and_link_webpage,
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
            logger.info(f"Webpage node created with ID: {webpage_id}")
            # Update state manager with the latest Webpage node ID
            state_manager.last_webpage_node_id = webpage_id
    
    except Exception as e:
        logger.error(f"Error processing entry: {e}", exc_info=True)

    return purpose_id, search_terms_id

def _create_purpose_and_search_terms(
    tx: Transaction,
    purpose_text: str,
    purpose_topics: list,
    purpose_entities: list,
    search_terms: str,
    search_terms_topics: list,
    search_terms_entities: list,
    search_terms_relevance: list,
    timestamp: str,
    data_source: str
) -> Tuple[int, int]:
    """
    Create Purpose and SearchTerms nodes in the Neo4j database.

    Args:
        tx (Transaction): The Neo4j transaction.
        purpose_text (str): The text of the purpose.
        purpose_topics (list): List of purpose topics dictionaries.
        purpose_entities (list): List of purpose entities dictionaries.
        search_terms (str): The search terms.
        search_terms_topics (list): List of search terms topics dictionaries.
        search_terms_entities (list): List of search terms entities dictionaries.
        search_terms_relevance (list): List of search terms relevance.
        timestamp (str): The timestamp.
        data_source (str): The data source.

    Returns:
        Tuple[int, int]: The IDs of the created Purpose and SearchTerms nodes.
    """
    query = """
    CREATE (p:Purpose {
        text: $purpose_text,
        topics: $purpose_topics,
        entities: $purpose_entities,
        timestamp: $timestamp,
        dataSource: $data_source,
        isInput: false
    })
    CREATE (st:SearchTerms {
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
    last_webpage_node_id: Optional[int],
    last_search_terms_node_id: Optional[int],
    url: str,
    title: str,
    summary: str,
    topics: list,
    entities: list,
    is_serp: bool,
    timestamp: str,
    data_source: str
) -> int:
    """
    Create a Webpage node and link it to the previous node in the Neo4j database.

    Args:
        tx (Transaction): The Neo4j transaction.
        last_webpage_node_id (Optional[int]): The ID of the last webpage node.
        last_search_terms_node_id (Optional[int]): The ID of the last search terms node.
        url (str): The URL of the webpage.
        title (str): The title of the webpage.
        summary (str): The summary of the webpage.
        topics (list): List of webpage topics dictionaries.
        entities (list): List of webpage entities dictionaries.
        is_serp (bool): Whether the webpage is a search engine results page.
        timestamp (str): The timestamp.
        data_source (str): The data source.

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
        # Handle the case where there is no previous node
        # For this example, we'll assume there's a single 'User' node
        user_node = tx.run("MATCH (u:User) RETURN id(u) AS user_id LIMIT 1").single()
        if user_node:
            target_node_id = user_node["user_id"]
            rel_type = 'VISITS'
        else:
            logger.error("No User node found in Neo4j. Cannot link Webpage node.")
            raise Exception("User node not found in Neo4j.")

    query = f"""
    MATCH (node) WHERE id(node) = $node_id
    CREATE (w:Webpage {{
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
