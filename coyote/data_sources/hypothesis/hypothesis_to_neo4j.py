"""
hypothesis_to_neo4j.py

Module for processing Hypothesis annotations and inserting them into a Neo4j database.
"""

import logging
from typing import Any, Dict, Optional, Tuple

from neo4j import Session, Transaction
import sqlite3

from coyote.utils.coyote_state_manager import CoyoteStateManager

logger = logging.getLogger(__name__)


def process_annotation(
    session: Session,
    event_data: Dict[str, Any],
    state_manager: CoyoteStateManager,
    cursor: sqlite3.Cursor
) -> Optional[int]:
    """
    Process a Hypothesis annotation and insert it into the Neo4j database.

    Args:
        session (Session): The Neo4j database session.
        event_data (Dict[str, Any]): The event data containing annotation information.
        state_manager (CoyoteStateManager): The state manager for tracking node IDs.
        cursor (sqlite3.Cursor): The SQLite cursor for querying data.

    Returns:
        Optional[int]: The ID of the created Annotation node, or None if an error occurs.
    """
    annotation_node_id = None

    try:
        # Extract event_id from event_data
        event_id = event_data.get("event_id")
        if not event_id:
            logger.warning("No event_id found in event_data. Skipping annotation processing.")
            return None

        # Fetch Hypothesis annotation data from SQLite
        cursor.execute(
            """
            SELECT
                annotation_id,
                timestamp,
                url,
                webpage_title,
                annotation_text,
                highlighted_text,
                tags,
                user_account,
                group_name,
                visibility
            FROM
                HypothesisAnnotations
            WHERE
                event_id = ?
            """,
            (event_id,)
        )
        annotation_data = cursor.fetchone()

        if not annotation_data:
            logger.warning(f"No Hypothesis annotation data found for event_id {event_id}. Skipping.")
            return None

        (
            annotation_id,
            timestamp,
            url,
            webpage_title,
            annotation_text,
            highlighted_text,
            tags,
            user_account,
            group_name,
            visibility
        ) = annotation_data

        # Fetch related Topics for annotation_text
        cursor.execute(
            """
            SELECT topic, wikidata_url, label FROM Topics
            WHERE event_id = ? AND related_to = 'annotation_text'
            """,
            (event_id,)
        )
        annotation_text_topics = cursor.fetchall()
        annotation_text_topics_json = [
            {"topic": row[0], "wikidata_url": row[1], "label": row[2]} for row in annotation_text_topics
        ]

        # Fetch related Topics for highlighted_text
        cursor.execute(
            """
            SELECT topic, wikidata_url, label FROM Topics
            WHERE event_id = ? AND related_to = 'highlighted_text'
            """,
            (event_id,)
        )
        highlighted_text_topics = cursor.fetchall()
        highlighted_text_topics_json = [
            {"topic": row[0], "wikidata_url": row[1], "label": row[2]} for row in highlighted_text_topics
        ]

        # Fetch related Entities for annotation_text
        cursor.execute(
            """
            SELECT entity, wikidata_url, label FROM Entities
            WHERE event_id = ? AND related_to = 'annotation_text'
            """,
            (event_id,)
        )
        annotation_text_entities = cursor.fetchall()
        annotation_text_entities_json = [
            {"entity": row[0], "wikidata_url": row[1], "label": row[2]} for row in annotation_text_entities
        ]

        # Fetch related Entities for highlighted_text
        cursor.execute(
            """
            SELECT entity, wikidata_url, label FROM Entities
            WHERE event_id = ? AND related_to = 'highlighted_text'
            """,
            (event_id,)
        )
        highlighted_text_entities = cursor.fetchall()
        highlighted_text_entities_json = [
            {"entity": row[0], "wikidata_url": row[1], "label": row[2]} for row in highlighted_text_entities
        ]

        # Convert tags to JSON
        tags_json = tags if isinstance(tags, list) else [tags]

        # Build the Cypher query
        query = """
        MERGE (w:Webpage {url: $url})
            ON CREATE SET w.title = $webpage_title
        CREATE (a:Annotation {
            annotationID: $annotation_id,
            timestamp: $timestamp,
            dataSource: $data_source,
            text: $annotation_text,
            textTopics: $annotation_text_topics,
            highlightedText: $highlighted_text,
            highlightedTextTopics: $highlighted_text_topics,
            annotationTextEntities: $annotation_text_entities,
            highlightedTextEntities: $highlighted_text_entities,
            tags: $tags,
            userAccount: $user_account,
            group: $group_name,
            visibility: $visibility,
            isInput: false
        })
        MERGE (w)-[:HAS_ANNOTATION]->(a)
        RETURN id(a) AS annotation_node_id
        """

        parameters = {
            "annotation_id": annotation_id,
            "timestamp": timestamp,
            "data_source": "Hypothesis",
            "url": url,
            "webpage_title": webpage_title,
            "annotation_text": annotation_text,
            "annotation_text_topics": annotation_text_topics_json,
            "highlighted_text": highlighted_text,
            "highlighted_text_topics": highlighted_text_topics_json,
            "annotation_text_entities": annotation_text_entities_json,
            "highlighted_text_entities": highlighted_text_entities_json,
            "tags": tags_json,
            "user_account": user_account,
            "group_name": group_name,
            "visibility": visibility
        }

        # Execute the query within a write transaction
        annotation_node_id = session.execute_write(_create_annotation, query, parameters)

        if annotation_node_id:
            logger.info(f"Processed annotation for URL: {url} at timestamp: {timestamp}")
        else:
            logger.warning(f"Failed to create Annotation node for event_id {event_id}.")

        return annotation_node_id
    
    except Exception as e:
        logger.error(f"Error processing annotation: {e}", exc_info=True)
        return None
    

def _create_annotation(tx: Transaction, query: str, parameters: Dict[str, Any]) -> Optional[int]:
    """
    Execute the Cypher query to create an Annotation node.

    Args:
        tx (Transaction): The Neo4j transaction.
        query (str): The Cypher query string.
        parameters (Dict[str, Any]): The parameters for the Cypher query.

    Returns:
        Optional[int]: The ID of the created Annotation node, or None if not created.
    """
    try:
        result = tx.run(query, parameters)
        record = result.single()
        if record and "annotation_node_id" in record:
            return record["annotation_node_id"]
        else:
            return None
    except Exception as e:
        logger.error(f"Cypher query execution failed: {e}", exc_info=True)
        return None
