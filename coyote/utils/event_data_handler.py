
import logging
import sqlite3
from typing import Any, Dict

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
            WHERE status NOT IN ('completed', 'failed')
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
        cursor = data_conn.cursor()
        cursor.execute('''
            INSERT INTO EventTracking (event_id, status, last_updated)
            VALUES (?, 'new', CURRENT_TIMESTAMP)
        ''', (event_id,))
        data_conn.commit()
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
        cursor = data_conn.cursor()
        cursor.execute('''
            INSERT INTO Events (event_id, timestamp, event_type, data_source)
            VALUES (?, ?, ?, ?)
        ''', (
            event_id,  # Use the event_id argument directly
            event.get('timestamp', ''),  # Provide a default value if missing
            event.get('event_type', ''),
            event.get('data_source', 'Coyote')  # Default to 'Coyote' if not provided
        ))
        data_conn.commit()
        logger.info(f"Inserted event_id {event_id} into Events table.")
        return True
    except sqlite3.Error as e:
        logger.error(f"Error inserting event_id {event_id}: {e}", exc_info=True)
        return False



def insert_event_specific_data(conn: sqlite3.Connection, event_data: Dict[str, Any]) -> None:
    """
    Inserts the event-specific data based on the event type.

    Args:
        conn (sqlite3.Connection): The SQLite connection to the coyote_event_data database.
        event_data (Dict[str, Any]): A dictionary containing the event data.
    """
    try:
        with conn:  # Using the context manager to handle transactions
            event_type = event_data.get('event_type', '')

            # Handle different event types and route data to specific tables
            if event_type == 'User starts or modifies a search':
                insert_search_event(conn, event_data)
            elif event_type == 'User clicks hyperlink':
                insert_hyperlink_click_event(conn, event_data)
            elif event_type == 'Webpage loads':
                insert_webpage_loads_event(conn, event_data)
            elif event_type == 'User annotated webpage':
                insert_annotation_event(conn, event_data)
                
                # Write each tag to the AnnotationTags table
                if 'tags' in event_data and isinstance(event_data['tags'], list):
                    for tag in event_data['tags']:
                        annotation_tag_data = {
                            'event_id': event_data['event_id'],
                            'annotation_id': event_data.get('annotation_id', ''),
                            'tag': tag
                        }
                        insert_annotation_tag(conn, annotation_tag_data)
            else:
                logger.warning(f"Unknown event type: {event_type}. Skipping event-specific data insertion.")

    except sqlite3.Error as e:
        logger.error(f"SQLite error while inserting event-specific data: {e}", exc_info=True)
        raise


def mark_event_ready_for_nlp(conn: sqlite3.Connection, event_id: str) -> None:
    try:
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE EventTracking
            SET status = 'ready_for_nlp', last_updated = CURRENT_TIMESTAMP
            WHERE event_id = ?
        ''', (event_id,))
        conn.commit()
        logger.info(f"Marked event_id {event_id} as 'ready_for_nlp'.")
    except sqlite3.Error as e:
        logger.error(f"Error updating event_id {event_id} to 'ready_for_nlp': {e}", exc_info=True)
        raise
