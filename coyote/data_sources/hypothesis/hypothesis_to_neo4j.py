"""
hypothesis_to_neo4j.py

Module for processing Hypothesis annotations and inserting them into a Neo4j database.
"""

import json
import logging
from typing import Any, Dict, Optional

from neo4j import Session

logger = logging.getLogger(__name__)


def process_annotation(
    session: Session,
    entry: Dict[str, Any]
) -> Optional[int]:
    """
    Process a Hypothesis annotation and insert it into the Neo4j database.

    Args:
        session (Session): The Neo4j database session.
        entry (Dict[str, Any]): The annotation data entry.

    Returns:
        Optional[int]: The ID of the created Annotation node, or None if an error occurs.
    """
    try:
        # Extract fields with optional handling
        timestamp = entry.get("timestamp")
        url = entry.get("url")
        page_title = entry.get("webpageTitle")
        annotation_id = entry.get("annotationID")
        annotation_text = entry.get("annotationText")
        annotation_text_topics = json.dumps(entry.get("annotationTextTopics", []))
        highlighted_text = entry.get("highlightedText")
        highlighted_text_topics = json.dumps(entry.get("highlightedTextTopics", []))
        annotation_text_entities = json.dumps(entry.get("annotationTextEntities", []))
        highlighted_text_entities = json.dumps(entry.get("highlightedTextEntities", []))
        tags = json.dumps(entry.get("tags", []))
        user_account = entry.get("userAccount")
        group = entry.get("group")
        visibility = entry.get("visibility", "private")
        data_source = entry.get("dataSource", "Hypothesis")

        # Build the Cypher query
        query = """
        MERGE (w:Webpage {url: $url})
        ON CREATE SET w.title = $page_title
        CREATE (a:Annotation {
            timestamp: $timestamp,
            dataSource: $data_source,
            annotationID: $annotation_id,
            text: $annotation_text,
            textTopics: $annotation_text_topics,
            highlightedText: $highlighted_text,
            highlightedTextTopics: $highlighted_text_topics,
            annotationTextEntities: $annotation_text_entities,
            highlightedTextEntities: $highlighted_text_entities,
            tags: $tags,
            userAccount: $user_account,
            group: $group,
            visibility: $visibility,
            isInput: false
        })
        MERGE (w)-[:HAS_ANNOTATION]->(a)
        RETURN id(a) AS annotation_id
        """

        parameters = {
            "timestamp": timestamp,
            "url": url,
            "page_title": page_title,
            "data_source": data_source,
            "annotation_id": annotation_id,
            "annotation_text": annotation_text,
            "annotation_text_topics": annotation_text_topics,
            "highlighted_text": highlighted_text,
            "highlighted_text_topics": highlighted_text_topics,
            "annotation_text_entities": annotation_text_entities,
            "highlighted_text_entities": highlighted_text_entities,
            "tags": tags,
            "user_account": user_account,
            "group": group,
            "visibility": visibility
        }

        # Execute the query
        result = session.run(query, parameters)
        record = result.single()
        if record:
            annotation_node_id = record["annotation_id"]
            logger.info(f"Processed annotation for URL: {url} at timestamp: {timestamp}")
            return annotation_node_id
        else:
            logger.warning("No annotation node was created.")
            return None

    except Exception as e:
        logger.error(f"Error processing annotation: {e}", exc_info=True)
        return None
