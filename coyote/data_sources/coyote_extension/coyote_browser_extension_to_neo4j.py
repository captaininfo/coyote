"""
coyote_browser_extension_to_neo4j.py

Module for processing data from the Coyote browser extension and inserting it into Neo4j.
"""

import json
import logging
from typing import Any, Dict, Optional, Tuple

from neo4j import Session, Transaction

logger = logging.getLogger(__name__)


def process_coyote_browser_extension_data(
    session: Session,
    entry: Dict[str, Any],
    state: Dict[str, Any]
) -> Tuple[Optional[int], Optional[int]]:
    """
    Process data from the Coyote browser extension and update the Neo4j database.

    Args:
        session (Session): The Neo4j database session.
        entry (Dict[str, Any]): The data entry to process.
        state (Dict[str, Any]): The state dictionary to keep track of node IDs.

    Returns:
        Tuple[Optional[int], Optional[int]]: The IDs of the created purpose and search terms nodes.
    """
    purpose_id = None
    search_terms_id = None

    try:
        event = entry.get("event")
        if event == "User starts or modifies a search":
            # Extract and set timestamp and data source
            timestamp = entry.get("timestamp")
            data_source = entry.get("dataSource", "Coyote Browser Extension")
            purpose_text = entry.get("purpose", "No Purpose")
            purpose_topics = json.dumps(entry.get("purposeTopics", []))
            purpose_entities = json.dumps(entry.get("purposeEntities", []))
            search_terms = entry.get("searchTerms", "No Search Terms")
            search_terms_topics = json.dumps(entry.get("searchTermsTopics", []))
            search_terms_entities = json.dumps(entry.get("searchTermsEntities", []))
            search_terms_relevance = json.dumps(entry.get("searchTermsRelevance", []))

            logger.info(f"Inserting Purpose and SearchTerms with timestamp: {timestamp}")
            result = session.execute_write(
                _create_purpose_and_search_terms,
                purpose_text,
                purpose_topics,
                purpose_entities,
                search_terms,
                search_terms_topics,
                search_terms_entities,
                search_terms_relevance,
                timestamp,
                data_source
            )
            if result:
                purpose_id, search_terms_id = result

            state['last_search_terms_node_id'] = search_terms_id

        elif event == "Webpage loads":
            # Extract and set timestamp and data source
            timestamp = entry.get("timestamp")
            data_source = entry.get("dataSource", "Coyote Browser Extension")
            url = entry.get("url", "No URL")
            title = entry.get("webpageTitle", "No Title")
            summary = entry.get("webpageSummary", "No Summary")
            topics = json.dumps(entry.get("webpageTopics", []))
            entities = json.dumps(entry.get("webpageNamedEntities", []))
            is_serp = "- Google Search" in title or url.startswith("https://www.google.com/search?")

            logger.info(f"Inserting Webpage with URL: {url} at timestamp: {timestamp}")
            webpage_id = session.execute_write(
                _create_and_link_webpage,
                state.get('last_webpage_node_id'),
                state['last_search_terms_node_id'],
                url,
                title,
                summary,
                topics,
                entities,
                is_serp,
                timestamp,
                data_source
            )
            logger.info(f"Webpage node created with ID: {webpage_id}")
            state['last_webpage_node_id'] = webpage_id

    except Exception as e:
        logger.error(f"Error processing entry: {e}", exc_info=True)

    return purpose_id, search_terms_id


def _create_purpose_and_search_terms(
    tx: Transaction,
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
        purpose_text (str): The text of the purpose.
        purpose_topics (str): JSON string of purpose topics.
        purpose_entities (str): JSON string of purpose entities.
        search_terms (str): The search terms.
        search_terms_topics (str): JSON string of search terms topics.
        search_terms_entities (str): JSON string of search terms entities.
        search_terms_relevance (str): JSON string of search terms relevance.
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
    topics: str,
    entities: str,
    is_serp: bool,
    timestamp: str,
    data_source: str
) -> int:
    """
    Create a Webpage node and link it to the previous node in the Neo4j database.

    Args:
        tx (Transaction): The Neo4j transaction.
        last_webpage_node_id (Optional[int]): The ID of the last webpage node.
        last_search_terms_node_id (int): The ID of the last search terms node.
        url (str): The URL of the webpage.
        title (str): The title of the webpage.
        summary (str): The summary of the webpage.
        topics (str): JSON string of webpage topics.
        entities (str): JSON string of webpage entities.
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
        # Option 1: Create a relationship to a 'User' node or a root node
        # For this example, we'll assume there's a single 'User' node with id = 0
        target_node_id = 0  # Replace with actual user node ID or handling logic
        rel_type = 'VISITS'

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
