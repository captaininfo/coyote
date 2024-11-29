# coyote_event_writer.py

"""
coyote_event_writer.py

Handles writing event data to the database for the Coyote application.
"""

import sqlite3
import logging
import json
from pathlib import Path
from typing import Dict, Any
from coyote.utils.config_manager import get_event_data_db_connection, get_staging_db_connection

# Define base directories
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / 'data'
LOGS_DIR = DATA_DIR / 'logs'

# Ensure the data and logs directories exist
DATA_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)

# Get the logger for this module
logger = logging.getLogger(__name__)

def insert_staging_event(event_data: Dict[str, Any]) -> None:
    """
    Inserts an event into the EventStaging table in coyote_event_staging.db.

    Args:
        event_data (Dict[str, Any]): A dictionary containing event data.
    """
    try:
        # Connect to coyote_event_staging.db
        conn = get_staging_db_connection()
        cursor = conn.cursor()

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
            event_data.get('event', ''),
            event_data.get('timestamp', ''),
            event_data.get('dataSource', ''),
            event_data.get('purpose', None),
            event_data.get('search_terms', None),
            event_data.get('url', None),
            event_data.get('webpage_title', None),
            event_data.get('annotation_id', None),
            event_data.get('annotation_text', None),
            event_data.get('highlighted_text', None),
            event_data.get('tags', None),
            event_data.get('user_account', None),
            event_data.get('groups', None),
            event_data.get('visibility', None),
            event_data.get('source_url', None),
            event_data.get('destination_url', None),
            event_data.get('link_text', None),
            json.dumps(event_data.get('event_payload', {})) if event_data.get('event_payload') else None
        ))

        # Commit the transaction
        conn.commit()
        logger.debug(f"Inserted event {event_data.get('event_id')} into EventStaging table.")
    except sqlite3.Error as e:
        logger.error(f"SQLite error in insert_staging_event: {e}", exc_info=True)
    except Exception as e:
        logger.error(f"Error in insert_staging_event: {e}", exc_info=True)
    finally:
        if conn:
            conn.close()


def insert_event(event_data: Dict[str, Any]) -> None:
    """
    Inserts an event into the Events table.

    Args:
        event_data (Dict[str, Any]): A dictionary containing event data.
    """
    try:
        conn = get_event_data_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO Events (event_id, timestamp, event_type, data_source)
            VALUES (?, ?, ?, ?)
        ''', (
            event_data['event_id'],
            event_data['timestamp'],
            event_data['event_type'],
            event_data.get('data_source', 'Coyote Browser Extension')
        ))
        conn.commit()
        logger.debug(f"Inserted event {event_data['event_id']} into Events table.")
    except sqlite3.Error as e:
        logger.error(f"SQLite error in insert_event: {e}", exc_info=True)
    except Exception as e:
        logger.error(f"Error in insert_event: {e}", exc_info=True)
    finally:
        if conn:
            conn.close()

# Similar insert functions for search events, webpage loads, annotations, etc.


def insert_search_event(search_event_data: Dict[str, Any]) -> None:
    """
    Inserts a search event into the SearchEvents table.

    Args:
        search_event_data (Dict[str, Any]): A dictionary containing search event data.
    """
    try:
        conn = get_event_data_db_connection()
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
    except Exception as e:
        logger.error(f"Error in insert_search_event: {e}", exc_info=True)
    finally:
        if conn:
            conn.close()

# Additional functions for other event types (webpage loads, annotations, etc.) follow a similar pattern