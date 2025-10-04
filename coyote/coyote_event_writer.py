# coyote_event_writer.py

"""
coyote_event_writer.py

Handles writing event data to the database for the Coyote application.
"""

import sqlite3
import logging
import json
import uuid
import inspect, os, time
from pathlib import Path
from typing import Dict, Any, List
from coyote.utils.config_container import DATA_DIR, LOGS_DIR
from coyote.utils.event_status import insert_event_status
from coyote.utils.config_manager import (
    get_staging_db_connection,
    get_event_data_db_connection,
)

# Get the logger for this module
logger = logging.getLogger(__name__)
logger.info("insert_annotation_event variant: OR IGNORE enabled")
logger.info("Loaded %s (mtime=%s)", __file__, time.ctime(os.path.getmtime(__file__)))


def _annotation_already_seen(annotation_id: str) -> bool:
    """Return True if this annotation_id is already staged or already in event_data."""
    if not annotation_id:
        return False
    try:
        # Check event_data.Annotations
        ed_conn = get_event_data_db_connection()
        cur = ed_conn.cursor()
        cur.execute("SELECT 1 FROM Annotations WHERE annotation_id=? LIMIT 1", (annotation_id,))
        if cur.fetchone():
            return True
    except Exception:
        logger.exception("Failed checking event_data for annotation_id=%s", annotation_id)
    finally:
        try:
            ed_conn.close()
        except Exception:
            pass

    try:
        # Check staging.EventStaging (in-flight)
        st_conn = get_staging_db_connection()
        cur = st_conn.cursor()
        cur.execute("""
            SELECT 1 FROM EventStaging
             WHERE annotation_id=? AND data_source='Hypothesis'
             LIMIT 1
        """, (annotation_id,))
        return cur.fetchone() is not None
    except Exception:
        logger.exception("Failed checking staging for annotation_id=%s", annotation_id)
        return False

def insert_staging_event(event_data: Dict[str, Any]) -> None:
    """
    Inserts an event into the EventStaging table in coyote_event_staging.db.

    Args:
        event_data (Dict[str, Any]): A dictionary containing event details.
    """
    try:
        # Use the connection from Flask's g
        conn = get_staging_db_connection()
        cursor = conn.cursor()
        
        # Process specific fields to ensure SQLite-compatible types
        highlighted_text = event_data.get('highlighted_text', None)
        highlighted_text = highlighted_text if isinstance(highlighted_text, str) else None

        tags = event_data.get('tags', None)
        tags = json.dumps(tags) if tags else None  # Convert list to JSON string

        event_payload = event_data.get('event_payload', {})
        event_payload = json.dumps(event_payload) if event_payload else None

        # Log the relevant fields before insertion
        logger.debug(f"Preparing to insert event into staging: "
                     f"event_id={event_data.get('event_id')}, "
                     f"event_type={event_data.get('event_type')}, "
                     f"data_source={event_data.get('data_source')}")
        logger.debug(f"Full event_data payload: {event_data}")

        # Insert the event data into the EventStaging table
        cursor.execute('''
            INSERT INTO EventStaging (
                event_id, event_type, timestamp, data_source, purpose, search_terms,
                url, webpage_title, annotation_id, annotation_text,
                highlighted_text, tags, user_account, groups, visibility,
                source_url, destination_url, link_text, event_payload
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            event_data.get('event_id', ''),
            event_data.get('event_type', ''),
            event_data.get('timestamp', ''),
            event_data.get('data_source', ''),
            event_data.get('purpose', None),
            event_data.get('search_terms', None),
            event_data.get('url', None),
            event_data.get('webpage_title', None),
            event_data.get('annotation_id', None),
            event_data.get('annotation_text', None),
            highlighted_text,  # Processed highlighted_text
            tags,              # Processed tags
            event_data.get('user_account', None),
            event_data.get('groups', None),
            event_data.get('visibility', None),
            event_data.get('source_url', None),
            event_data.get('destination_url', None),
            event_data.get('link_text', None),
            event_payload       # Processed event_payload
        ))

        # Commit the transaction
        conn.commit()
        logger.debug(f"Inserted event {event_data.get('event_id')} into EventStaging table.")
    except sqlite3.Error as e:
        logger.error(f"SQLite error in insert_staging_event: {e}", exc_info=True)
    except Exception as e:
        logger.error(f"Error in insert_staging_event: {e}", exc_info=True)



def process_hypothesis_annotations(annotations: List[Dict[str, Any]]) -> None:
    """
    Process Hypothesis annotations and stage them in the staging database.

    Args:
        annotations (List[Dict[str, Any]]): A list of annotations from Hypothesis.
    """
    for annotation in annotations:
        try:
            # Idempotency at the DB boundary
            anno_id = annotation.get('id')
            if _annotation_already_seen(anno_id):
                logger.info("Hypothesis annotation already seen; skipping. annotation_id=%s", anno_id)
                continue

            event_id = str(uuid.uuid4())  # Generate a UUID for each annotation
            text = annotation.get('text', '')

            highlighted_text = _safe_highlighted_text(annotation)
            webpage_title   = _safe_title(annotation)
            visibility      = _safe_visibility(annotation)

            annotation_data = {
                "event_id": event_id,
                "timestamp": annotation['created'],
                "event_type": "User annotated webpage",
                "data_source": "Hypothesis",
                "url": annotation['uri'],
                "webpage_title": webpage_title,
                "annotation_id": annotation['id'],
                "annotation_text": text,
                "highlighted_text": highlighted_text,
                "tags": annotation.get('tags', []),
                "user_account": annotation['user'],
                "groups": annotation['group'],
                "visibility": visibility,
                # Add default values for missing fields
                "purpose": None,
                "search_terms": None,
                "source_url": None,
                "destination_url": None,
                "link_text": None,
                "event_payload": annotation 
            }

            # Log the data being passed to insert_staging_event
            logger.debug(f"Annotation ID: {annotation['id']} - Data passed to insert_staging_event: {annotation_data}")

            # Insert into the staging database
            insert_staging_event(annotation_data)
            insert_event_status(event_id, "pending")
            logger.debug(f"Successfully processed annotation ID: {annotation['id']} with event_id: {event_id}")
        except Exception as e:
            logger.error(f"Error processing annotation ID {annotation.get('id', 'Unknown')} - Exception: {e}", exc_info=True)


def insert_search_event(conn: sqlite3.Connection, search_event_data: Dict[str, Any]) -> None:
    """
    Inserts a search event into the SearchEvents table.

    Args:
        conn (sqlite3.Connection): The SQLite connection to the coyote_event_data database.
        search_event_data (Dict[str, Any]): A dictionary containing search event data.
    """
    try:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO SearchEvents (event_id, purpose, search_terms, search_terms_relevance_score)
            VALUES (?, ?, ?, ?)
        ''', (
            search_event_data['event_id'],
            search_event_data.get('purpose', ''),
            search_event_data.get('search_terms', ''),
            search_event_data.get('search_terms_relevance_score', 0.0)
        ))
        conn.commit()
        logger.debug(f"Inserted search event {search_event_data['event_id']} into SearchEvents table.")
    except sqlite3.Error as e:
        logger.error(f"SQLite error in insert_search_event: {e}", exc_info=True)


def insert_webpage_loads_event(conn: sqlite3.Connection, webpage_loads_event_data: Dict[str, Any]) -> None:
    """
    Inserts a webpage load event into the WebpageLoads table in coyote_event_data.db.

    Args:
        conn (sqlite3.Connection): The SQLite connection to the coyote_event_data database.
        webpage_loads_event_data (Dict[str, Any]): A dictionary containing webpage load event data.
    """
    try:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO WebpageLoads (event_id, url, webpage_title, webpage_summary, webpage_relevance_score)
            VALUES (?, ?, ?, ?, ?)
        ''', (
            webpage_loads_event_data['event_id'],
            webpage_loads_event_data.get('url', ''),
            webpage_loads_event_data.get('webpage_title', ''),
            webpage_loads_event_data.get('webpage_summary', ''),
            webpage_loads_event_data.get('webpage_relevance_score', 0.0)
        ))
        conn.commit()
        logger.debug(f"Inserted webpage load event {webpage_loads_event_data['event_id']} into WebpageLoads table.")
    except sqlite3.Error as e:
        logger.error(f"SQLite error in insert_webpage_loads_event: {e}", exc_info=True)


def insert_hyperlink_click_event(conn: sqlite3.Connection, hyperlink_click_event_data: Dict[str, Any]) -> None:
    """
    Inserts a hyperlink click event into the HyperlinkClicks table in coyote_event_data.db.

    Args:
        conn (sqlite3.Connection): The SQLite connection to the coyote_event_data database.
        hyperlink_click_event_data (Dict[str, Any]): A dictionary containing hyperlink click event data.
    """
    try:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO HyperlinkClicks (event_id, source_url, destination_url, link_text)
            VALUES (?, ?, ?, ?)
        ''', (
            hyperlink_click_event_data['event_id'],
            hyperlink_click_event_data.get('source_url', ''),
            hyperlink_click_event_data.get('destination_url', ''),
            hyperlink_click_event_data.get('link_text', '')
        ))
        conn.commit()
        logger.debug(f"Inserted hyperlink click event {hyperlink_click_event_data['event_id']} into HyperlinkClicks table.")
    except sqlite3.Error as e:
        logger.error(f"SQLite error in insert_hyperlink_click_event: {e}", exc_info=True)


def insert_annotation_event(conn: sqlite3.Connection, annotation_event_data: Dict[str, Any]) -> None:
    """
    Inserts an annotation event into the Annotations table in coyote_event_data.db.

    Args:
        conn (sqlite3.Connection): The SQLite connection to the coyote_event_data database.
        annotation_event_data (Dict[str, Any]): A dictionary containing annotation event data.
    """
    try:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR IGNORE INTO Annotations (
                event_id,
                url,
                webpage_title,
                annotation_id,
                annotation_text,
                highlighted_text,
                user_account,
                groups,
                visibility
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            annotation_event_data['event_id'],
            annotation_event_data.get('url', ''),
            annotation_event_data.get('webpage_title', ''),
            annotation_event_data.get('annotation_id', ''),
            annotation_event_data.get('annotation_text', ''),
            annotation_event_data.get('highlighted_text', ''),
            annotation_event_data.get('user_account', ''),
            annotation_event_data.get('groups', ''),
            annotation_event_data.get('visibility', '')
        ))
        conn.commit()
        if cursor.rowcount == 0:
            # Duplicate (unique constraint hit and ignored)
            logger.info(
                "Duplicate Hypothesis annotation ignored (already in Annotations). "
                "annotation_id=%s event_id=%s",
                annotation_event_data.get('annotation_id'),
                annotation_event_data.get('event_id'),
            )
            return False
        logger.debug(
            "Inserted annotation event %s (annotation_id=%s) into Annotations.",
            annotation_event_data['event_id'],
            annotation_event_data.get('annotation_id'),
        )
        return True
    except sqlite3.Error as e:
        logger.error(f"SQLite error in insert_annotation_event: {e}", exc_info=True)
        return False


def insert_entity(conn: sqlite3.Connection, entity_data: Dict[str, Any]) -> None:
    """
    Inserts an entity into the Entities table in coyote_event_data.db.

    Args:
        conn (sqlite3.Connection): The SQLite connection to the coyote_event_data database.
        entity_data (Dict[str, Any]): A dictionary containing entity data.
    """
    try:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO Entities (event_id, entity_context, entity, wikidata_uri, label, score)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (
            entity_data['event_id'],
            entity_data.get('entity_context', ''),
            entity_data.get('entity', ''),
            entity_data.get('wikidata_uri', ''),
            entity_data.get('label', ''),
            entity_data.get('score', 0.0),
        ))
        conn.commit()
        logger.debug(f"Inserted entity '{entity_data.get('entity', '')}' for event {entity_data['event_id']} into Entities table.")
    except sqlite3.Error as e:
        logger.error(f"SQLite error in insert_entity: {e}", exc_info=True)


def insert_topic(conn: sqlite3.Connection, topic_data: Dict[str, Any]) -> None:
    """
    Inserts a topic into the Topics table in coyote_event_data.db.

    Args:
        conn (sqlite3.Connection): The SQLite connection to the coyote_event_data database.
        topic_data (Dict[str, Any]): A dictionary containing topic data.
    """
    try:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO Topics (event_id, topic_context, topic, wikidata_uri, label, score)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (
            topic_data['event_id'],
            topic_data.get('topic_context', ''),
            topic_data.get('topic', ''),
            topic_data.get('wikidata_uri', ''),
            topic_data.get('label', ''),
            topic_data.get('score', 0.0),
        ))
        conn.commit()
        logger.debug(f"Inserted topic '{topic_data.get('topic', '')}' for event {topic_data['event_id']} into Topics table.")
    except sqlite3.Error as e:
        logger.error(f"SQLite error in insert_topic: {e}", exc_info=True)


def insert_annotation_tag(conn: sqlite3.Connection, annotation_tag_data: Dict[str, Any]) -> None:
    """
    Inserts an annotation tag into the AnnotationTags table in coyote_event_data.db.

    Args:
        conn (sqlite3.Connection): The SQLite connection to the coyote_event_data database.
        annotation_tag_data (Dict[str, Any]): A dictionary containing annotation tag data.
    """
    try:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO AnnotationTags (event_id, annotation_id, tag)
            VALUES (?, ?, ?)
        ''', (
            annotation_tag_data['event_id'],
            annotation_tag_data.get('annotation_id', ''),
            annotation_tag_data.get('tag', '')
        ))
        conn.commit()
        logger.debug(f"Inserted annotation tag '{annotation_tag_data.get('tag', '')}' for event {annotation_tag_data['event_id']} into AnnotationTags table.")
    except sqlite3.Error as e:
        logger.error(f"SQLite error in insert_annotation_tag: {e}", exc_info=True)


def insert_topics_and_entities(event_id: str, context: str, topics_data: Dict[str, Any], entities_data: Dict[str, Any]) -> None:
    """
    Inserts topics and entities into the database.

    Args:
        event_id (str): The event ID.
        context (str): The context of the topics/entities (e.g., 'purpose', 'search_terms').
        topics_data (Dict[str, Any]): The topics data.
        entities_data (Dict[str, Any]): The entities data.
    """
    # Insert topics
    for topic, details in topics_data.get('topics_with_weights', {}).items():
        topic_data = {
            'event_id': event_id,
            'topic_context': context,
            'topic': topic,
            'uri': details.get('uri', ''),
            'score': details.get('score', 0.0)
        }
        insert_topic(topic_data)

    # Insert entities
    for entity, details in entities_data.get('topics_with_weights', {}).items():
        entity_data = {
            'event_id': event_id,
            'entity_context': context,
            'entity': entity,
            'uri': details.get('uri', ''),
            'score': details.get('score', 0.0)
        }
        insert_entity(entity_data)

def _safe_highlighted_text(anno: Dict[str, Any]) -> str:
    try:
        target = (anno.get('target') or [])
        if target and isinstance(target[0], dict):
            for sel in target[0].get('selector', []) or []:
                if sel.get('type') == 'TextQuoteSelector':
                    return sel.get('exact') or ''
    except Exception:
        logger.debug("No TextQuoteSelector for annotation_id=%s", anno.get('id'))
    return ''

def _safe_title(anno: Dict[str, Any]) -> str:
    titles = (anno.get('document') or {}).get('title') or []
    return titles[0] if titles else ''

def _safe_visibility(anno: Dict[str, Any]) -> str:
    read = (anno.get('permissions') or {}).get('read') or []
    return 'public' if 'group:__world__' in read else 'private'