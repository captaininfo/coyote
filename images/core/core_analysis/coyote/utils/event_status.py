# event_status.py

import logging
from coyote.utils.config_manager import get_state_db_connection


# Get the logger for this module
logger = logging.getLogger(__name__)

def insert_event_status(event_id: str, status: str = "pending") -> None:
    """
    Inserts (or updates) a record in the event_queue table in coyote_state.db.
    
    Args:
        event_id (str): The unique ID of the event.
        status (str): The status to assign (default is "pending").
    """
    try:
        conn = get_state_db_connection()
        cursor = conn.cursor()
        # Insert a new record or replace an existing record for this event
        cursor.execute(
            "INSERT OR REPLACE INTO event_queue (event_id, status, created_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
            (event_id, status)
        )
        conn.commit()
        conn.close()
        logger.debug(f"Inserted/updated event status for event_id {event_id} with status '{status}'.")
    except Exception as e:
        logger.error(f"Error inserting event status for event_id {event_id}: {e}", exc_info=True)
