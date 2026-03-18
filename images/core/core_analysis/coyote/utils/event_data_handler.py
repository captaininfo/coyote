
import logging
import sqlite3
from typing import Any, Dict, List

from coyote.utils.config_manager import get_staging_read_connection, get_event_data_db_connection
from coyote.coyote_event_writer import (
    insert_search_event,
    insert_webpage_loads_event,
    insert_hyperlink_click_event,
    insert_annotation_event,
    insert_annotation_tag
)

# Get the logger for this module
logger = logging.getLogger(__name__)

def fetch_next_event() -> Any:
    """
    Fetch the next unprocessed event from the staging database.

    Returns:
        Any: The next event to be processed or None if no events are available.
    """
    try:
        # Connect to both databases
        staging_conn = get_staging_read_connection()
        event_data_conn = get_event_data_db_connection()
        staging_cursor = staging_conn.cursor()
        event_data_cursor = event_data_conn.cursor()

        # Retrieve all event IDs from the Events table
        event_data_cursor.execute('SELECT event_id FROM Events')
        processed_event_ids = {row[0] for row in event_data_cursor.fetchall()}

        # Retrieve the next unprocessed event from EventStaging
        staging_cursor.execute('SELECT * FROM EventStaging ORDER BY created_at ASC')
        for row in staging_cursor.fetchall():
            if row['event_id'] not in processed_event_ids:
                return dict(row)

        # No unprocessed events found
        return None
    except sqlite3.Error as e:
        logger.error(f"Error fetching the next event: {e}", exc_info=True)
        raise
    finally:
        # Close database connections
        if staging_conn:
            staging_conn.close()
        if event_data_conn:
            event_data_conn.close()


def is_event_processing(data_conn: sqlite3.Connection) -> bool:
    """
    Check if there is an event currently being processed.

    Args:
        data_conn (sqlite3.Connection): The SQLite connection to the data database.

    Returns:
        bool: True if an event is currently processing (i.e., not completed or failed), False otherwise.
    """
    try:
        cursor = data_conn.cursor()
        cursor.execute('''
            SELECT COUNT(*) 
            FROM EventTracking 
            WHERE status NOT IN ('completed', 'failed', 'processed', 'ready_for_nlp')
        ''')
        processing_count = cursor.fetchone()[0]
        return processing_count > 0
    except sqlite3.Error as e:
        logger.error(f"Error checking processing status: {e}", exc_info=True)
        return False


def insert_event_tracking(data_conn: sqlite3.Connection, event_id: str) -> bool:
    """
    Insert a new event into the EventTracking table with an initial status.

    Args:
        data_conn (sqlite3.Connection): The SQLite connection to the coyote_event_data database.
        event_id (str): The unique identifier for the event.

    Returns:
        bool: True if the event was successfully inserted, False otherwise.
    """
    try:
        logger.debug(f"[insert_event_tracking] BEFORE write: in_transaction={data_conn.in_transaction}")
        cursor = data_conn.cursor()
        cursor.execute('''
            INSERT INTO EventTracking (event_id, status, last_updated)
            VALUES (?, 'new', CURRENT_TIMESTAMP)
        ''', (event_id,))
        data_conn.commit()
        logger.debug(f"[insert_event_tracking] AFTER commit: in_transaction={data_conn.in_transaction}")
        logger.info(f"Inserted event_id {event_id} into EventTracking with status 'new'.")
        return True
    except sqlite3.Error as e:
        logger.error(f"Error inserting event_id {event_id}: {e}", exc_info=True)
        return False


def insert_event(data_conn: sqlite3.Connection, event_id: str, event: dict) -> bool:
    """
    Insert common event data into the Events table.

    Args:
        data_conn (sqlite3.Connection): The SQLite connection to the database.
        event_id (str): The unique identifier for the event.
        event (dict): A dictionary containing event details like timestamp, event_type, and data_source.

    Returns:
        bool: True if the event was successfully inserted, False otherwise.
    """
    try:
        logger.debug(f"[insert_event] BEFORE write: in_transaction={data_conn.in_transaction}")
        cursor = data_conn.cursor()
        cursor.execute('''
            INSERT INTO Events (event_id, timestamp, event_type, data_source)
            VALUES (?, ?, ?, ?)
        ''', (
            event_id,
            event.get('timestamp', ''),
            event.get('event_type', ''),
            event.get('data_source', 'Coyote')
        ))
        data_conn.commit()
        logger.debug(f"[insert_event] AFTER commit: in_transaction={data_conn.in_transaction}")

        logger.info(f"Inserted event_id {event_id} into Events table.")
        return True
    except sqlite3.Error as e:
        logger.error(f"Error inserting event_id {event_id}: {e}", exc_info=True)
        return False



def insert_event_specific_data(conn: sqlite3.Connection, event_data: Dict[str, Any]) -> bool:
    """
    Inserts the event-specific data based on the event type.

    Args:
        conn (sqlite3.Connection): The SQLite connection to the coyote_event_data database.
        event_data (Dict[str, Any]): A dictionary containing the event data.
    """
    try:
        with conn:  # Using a context manager automatically begins a transaction
            # Another quick check, to see that we are definitely in a transaction
            logger.debug(f"[insert_event_specific_data] INSIDE context: in_transaction={conn.in_transaction}")

            event_type = event_data.get('event_type', '')

            if event_type == 'User starts or modifies a search':
                insert_search_event(conn, event_data)
                return True
            elif event_type == 'User clicks hyperlink':
                insert_hyperlink_click_event(conn, event_data)
                return True
            elif event_type == 'Webpage loads':
                insert_webpage_loads_event(conn, event_data)
                return True
            elif event_type == 'User annotated webpage':
                ok = insert_annotation_event(conn, event_data)
                if not ok:
                    # Duplicate (annotation_id already exists) or insert ignored.
                    logger.info(
                        "Annotation insert skipped (duplicate). event_id=%s annotation_id=%s",
                        event_data.get('event_id'), event_data.get('annotation_id')
                    )
                    return False
                # Only add tags when the annotation row was actually created for this event_id
                if 'tags' in event_data and isinstance(event_data['tags'], list):
                    for tag in event_data['tags']:
                        annotation_tag_data = {
                            'event_id': event_data['event_id'],
                            'annotation_id': event_data.get('annotation_id', ''),
                            'tag': tag
                        }
                        insert_annotation_tag(conn, annotation_tag_data)
                return True
            else:
                logger.warning(f"Unknown event type: {event_type}. Skipping event-specific data insertion.")

                return True

        logger.debug(f"[insert_event_specific_data] AFTER context manager: in_transaction={conn.in_transaction}")
        return True

    except sqlite3.Error as e:
        logger.error(f"SQLite error while inserting event-specific data: {e}", exc_info=True)
        return False


def mark_event_ready_for_nlp(conn: sqlite3.Connection, event_id: str) -> None:
    logger.debug(f"[mark_event_ready_for_nlp] BEFORE any writes: in_transaction={conn.in_transaction}")
    try:
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE EventTracking
            SET status = 'ready_for_nlp', last_updated = CURRENT_TIMESTAMP
            WHERE event_id = ?
        ''', (event_id,))
        conn.commit()
        logger.info(f"Marked event_id {event_id} as 'ready_for_nlp'.")
        logger.debug(f"[mark_event_ready_for_nlp] AFTER context manager: in_transaction={conn.in_transaction}")
    except sqlite3.Error as e:
        logger.error(f"Error updating event_id {event_id} to 'ready_for_nlp': {e}", exc_info=True)
        raise


def update_topics_with_wikidata(self, mapped_topics_records: List[tuple]) -> None:
    """
    Update the Topics table with WikiData URIs and labels for the given records.
    
    Each record is expected to be a tuple of (uri, label, event_id, topic).
    """
    try:
        # Bulk update each topic record with the corresponding URI and label
        # We use executemany with an UPDATE statement. Since UPDATE doesn't support multiple bindings
        # at once by default, we do a loop. Alternatively, we can do a loop of execute() calls.
        
        # For better performance, you might consider doing these updates one by one.
        # Another approach is using a loop:
        for (uri, label, event_id, topic) in mapped_topics_records:
            self.data_cursor.execute(
                "UPDATE Topics SET wikidata_uri=?, label=? WHERE event_id=? AND topic=?",
                (uri, label, event_id, topic)
            )
        
        # If you prefer a single transaction, it's already encompassed by the main function's transaction.
        # Just commit at the end of process_search_event().
        
    except Exception as e:
        logger.exception(f"Error updating topics with WikiData: {e}")
        raise  # re-raise so calling function can handle rollback if needed


def update_entities_with_wikidata(self, mapped_entities_records: List[tuple]) -> None:
    """
    Update the Entities table with WikiData URIs and labels for the given records.
    
    Each record is expected to be a tuple of (uri, label, event_id, entity).
    """
    try:
        # Similarly update each entity record
        for (uri, label, event_id, entity) in mapped_entities_records:
            self.data_cursor.execute(
                "UPDATE Entities SET wikidata_uri=?, label=? WHERE event_id=? AND entity=?",
                (uri, label, event_id, entity)
            )

    except Exception as e:
        logger.exception(f"Error updating entities with WikiData: {e}")
        raise  # re-raise so calling function can handle rollback if needed
