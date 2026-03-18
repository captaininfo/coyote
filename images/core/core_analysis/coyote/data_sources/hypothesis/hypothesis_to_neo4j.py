"""
hypothesis_to_neo4j.py

Module for processing Hypothesis annotations and inserting them into a Neo4j database.
"""

import json
import logging
import sqlite3
from typing import Any, Dict, Optional, TYPE_CHECKING
from neo4j import Session, Transaction

if TYPE_CHECKING:                                   # runtime‑safe
    from coyote.neo4j_integration.coyote_neo4j_state_manager import (
        CoyoteNeo4jStateManager,
    )

logger = logging.getLogger(__name__)

def process_annotation(
    session: Session,
    event_data: Dict[str, Any],
    state_manager: "CoyoteNeo4jStateManager",        # only type‑hint
    cursor: sqlite3.Cursor,
) -> Optional[int]:
    """Fetch annotation‑related rows from SQLite → write one Annotation node."""
    event_id = event_data.get("event_id")
    if not event_id:
        logger.warning("No event_id in event_data – skipping")
        return None

    # ── core annotation row ───────────────────────────────────────────
    cursor.execute(
        """
        SELECT annotation_id, url, webpage_title, annotation_text,
               highlighted_text, user_account, groups, visibility
        FROM   Annotations
        WHERE  event_id = ?
        """,
        (event_id,),
    )
    row = cursor.fetchone()
    if not row:
        logger.warning("No Annotations row for %s – skipping", event_id)
        return None

    (
        annotation_id,
        url,
        webpage_title,
        annotation_text,
        highlighted_text,
        user_account,
        group_name,
        visibility,
    ) = row

    # timestamp (primitive) ───────────────────────────────────────────
    cursor.execute("SELECT timestamp FROM Events WHERE event_id = ?", (event_id,))
    timestamp_row = cursor.fetchone()
    timestamp = timestamp_row[0] if timestamp_row else None

    # tags (list[str] – leave as primitives) ──────────────────────────
    cursor.execute(
        "SELECT tag FROM AnnotationTags WHERE annotation_id = ?", (annotation_id,)
    )
    tags_json = [r[0] for r in cursor.fetchall()]

    # helper to fetch + serialise a list[dict] in one go --------------
    def _json_rows(q: str, ctx: str) -> str:
        cursor.execute(q, (event_id,))
        rows = cursor.fetchall()
        return json.dumps(
            [{ ("topic"  if ctx == "topic" else "entity"): r[0],
                "wikidata_uri": r[1],
                "label": r[2]} for r in rows]
        )

    annotation_text_topics_json   = _json_rows(
        """
        SELECT topic, wikidata_uri, label FROM Topics
        WHERE event_id = ? AND topic_context = 'annotation_text'
        """,
        "topic",
    )
    highlighted_text_topics_json  = _json_rows(
        """
        SELECT topic, wikidata_uri, label FROM Topics
        WHERE event_id = ? AND topic_context = 'highlighted_text'
        """,
        "topic",
    )
    annotation_text_entities_json = _json_rows(
        """
        SELECT entity, wikidata_uri, label FROM Entities
        WHERE event_id = ? AND entity_context = 'annotation_text'
        """,
        "entity",
    )
    highlighted_text_entities_json = _json_rows(
        """
        SELECT entity, wikidata_uri, label FROM Entities
        WHERE event_id = ? AND entity_context = 'highlighted_text'
        """,
        "entity",
    )

    flat_topics   = json.dumps(
        json.loads(annotation_text_topics_json)
        + json.loads(highlighted_text_topics_json)
    )

    flat_entities = json.dumps(
        json.loads(annotation_text_entities_json)
        + json.loads(highlighted_text_entities_json)
    )

    # ── Cypher & parameters ──────────────────────────────────────────
    cypher = """
    MERGE (w:Webpage {url: $url})
      ON CREATE SET w.title = $webpage_title
    CREATE (a:Annotation {
        event_id: $event_id,
        annotation_id: $annotation_id,
        timestamp: $timestamp,
        dataSource: $data_source,
        annotation_text: $annotation_text,
        highlighted_text: $highlighted_text,
        topics: $flat_topics,
        entities: $flat_entities,
        tags: $tags,
        url: $url,
        webpage_title: $webpage_title,
        isInput: false
    })
    MERGE (w)-[:HAS_ANNOTATION]->(a)
    RETURN id(a) AS annotation_node_id
    """

    # Parameters aligned with schema: annotation_id, annotation_text, highlighted_text,
    # timestamp, url, webpage_title, entities, topics (see CLAUDE.md)
    params = {
        "event_id": event_id,
        "annotation_id": annotation_id,
        "timestamp": timestamp,
        "data_source": "Hypothesis",
        "url": url,
        "webpage_title": webpage_title,
        "annotation_text": annotation_text,
        "highlighted_text": highlighted_text,
        "flat_topics": flat_topics,
        "flat_entities": flat_entities,
        "tags": tags_json,
    }

    try:
        node_id = session.execute_write(_create_annotation, cypher, params)
        if node_id:
            logger.info("Annotation node %s created for %s", node_id, url)
        return node_id
    except Exception as exc:
        logger.error("process_annotation failed: %s", exc, exc_info=True)
        return None


def _create_annotation(
    tx: Transaction, query: str, parameters: Dict[str, Any]
) -> Optional[int]:
    """Run the query inside a write transaction and return the node id."""
    try:
        record = tx.run(query, parameters).single()
        return record["annotation_node_id"] if record else None
    except Exception as exc:
        logger.error("Cypher query execution failed: %s", exc, exc_info=True)
        raise