
import logging
import sqlite3
from typing import Any

from coyote.utils.config_manager import get_staging_read_connection

# Get the logger for this module
logger = logging.getLogger(__name__)

def fetch_next_event() -> Any:
    """
    Fetch the next unprocessed event from the staging database.
    
    Returns:
        Any: The next event to be processed or None if no events are available.
    """
    try:
        conn = get_staging_read_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM EventStaging 
            WHERE event_id NOT IN (SELECT event_id FROM EventData)
            ORDER BY created_at ASC LIMIT 1
        ''')
        event = cursor.fetchone()
        if event:
            logger.info(f"Fetched event_id {event['event_id']} from staging database.")
        else:
            logger.info("No unprocessed events available in staging database.")
        return event
    except sqlite3.Error as e:
        logger.error(f"Error fetching the next event: {e}", exc_info=True)
        raise
    finally:
        if conn:
            conn.close()
            logger.debug("Read-only connection to staging database closed.")


def insert_event(data_conn: sqlite3.Connection, event_id: str, event: dict) -> None:
    """
    Insert a new event into the data database with 'processing' status.

    Args:
        data_conn (sqlite3.Connection): The SQLite connection to the data database.
        event_id (str): The unique identifier for the event.
        event (dict): A dictionary containing event details to be inserted.
    """
    try:
        cursor = data_conn.cursor()
        cursor.execute('''
            INSERT INTO EventData (event_id, status, event_details)
            VALUES (?, 'processing', ?)
        ''', (event_id, str(event)))
        data_conn.commit()
        logger.info(f"Inserted event_id {event_id} into EventData with status 'processing'.")
    except sqlite3.Error as e:
        logger.error(f"Error inserting event_id {event_id}: {e}", exc_info=True)
        raise


def update_event_status(data_conn: sqlite3.Connection, event_id: str, status: str) -> None:
    """
    Update the status of an event in the data database.

    Args:
        data_conn (sqlite3.Connection): The SQLite connection to the data database.
        event_id (str): The unique identifier for the event.
        status (str): The new status for the event.
    """
    try:
        cursor = data_conn.cursor()
        cursor.execute('''
            UPDATE EventData SET status = ? WHERE event_id = ?
        ''', (status, event_id))
        data_conn.commit()
        logger.info(f"Updated event_id {event_id} status to {status}.")
    except sqlite3.Error as e:
        logger.error(f"Error updating status for event_id {event_id}: {e}", exc_info=True)
        raise
