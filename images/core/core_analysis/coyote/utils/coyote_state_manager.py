# coyote_state_manager.py

"""
Module for managing the state of events and nodes in the Coyote application,
using a SQLite database to queue and track processing.
"""

import logging
import sqlite3
from typing import List

from coyote.utils.config_manager import get_state_db_connection

logger = logging.getLogger(__name__)


class CoyoteStateManager:
    """
    Manages the state of events and nodes in the Coyote application,
    including queuing for processing and updating their status.
    """

    def __init__(self) -> None:
        pass

    def get_connection(self) -> sqlite3.Connection:
        """
        Get a new connection to the state database.

        Returns:
            sqlite3.Connection: The database connection.
        """
        return get_state_db_connection()

    def process_pending_events(self) -> None:
        """
        Processes pending events from the event queue by triggering `events_to_neo4j.py`.
        The function ensures that only one event is being processed at any time.

        Raises:
            sqlite3.Error: If there is an error interacting with the database.
        """
        try:
            conn = self.get_connection()
            cursor = conn.cursor()

            # Check if there are any events already being processed
            cursor.execute('''
                SELECT event_id FROM event_queue WHERE status = 'processing'
            ''')
            processing_event = cursor.fetchone()

            if processing_event:
                logger.info(f"An event ({processing_event[0]}) is already being processed. Skipping new processing.")
                return  # Skip processing since an event is already in progress

            # Retrieve the next pending event
            cursor.execute('''
                SELECT event_id FROM event_queue WHERE status = 'pending'
                ORDER BY created_at ASC LIMIT 1
            ''')
            pending_event = cursor.fetchone()

            if pending_event:
                event_id = pending_event[0]

                # Mark the event as "processing"
                self.update_event_status(event_id, 'processing')

                # Trigger the processing logic (e.g., call `events_to_neo4j.py`)
                from coyote.neo4j_integration.events_to_neo4j import main as events_to_neo4j_main

                try:
                    events_to_neo4j_main(event_id)
                    # Mark the event as processed upon success
                    self.update_event_status(event_id, 'processed')
                    logger.info(f"Event {event_id} processed successfully.")

                except Exception as e:
                    # If processing fails, reset the status to "pending" for reprocessing later
                    self.update_event_status(event_id, 'pending')
                    logger.error(f"Error processing event {event_id}: {e}", exc_info=True)

            else:
                logger.info("No pending events to process.")

        except sqlite3.Error as e:
            logger.error(f"SQLite error during processing pending events: {e}", exc_info=True)
            raise

    def update_event_status(self, event_id: str, status: str) -> None:
        """
        Updates the status of an event.

        Args:
            event_id (str): The unique identifier of the event.
            status (str): The new status of the event.

        Raises:
            sqlite3.Error: If there is an error updating the event status.
        """
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE event_queue
                SET status = ?, processed_at = CASE WHEN ? = 'processed' THEN CURRENT_TIMESTAMP ELSE processed_at END
                WHERE event_id = ?
            ''', (status, status, event_id))
            conn.commit()
            logger.info(f"Event {event_id} updated to status '{status}'.")
        except sqlite3.Error as e:
            logger.error(f"Error updating event {event_id} to status '{status}': {e}", exc_info=True)
            raise

    def add_event_to_queue(self, event_id: str) -> None:
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR IGNORE INTO event_queue (event_id)
                VALUES (?)
            ''', (event_id,))
            conn.commit()
            logger.info(f"Event {event_id} added to the processing queue.")
        except sqlite3.Error as e:
            logger.error(f"Error adding event {event_id} to the queue: {e}", exc_info=True)
            raise

    # Other methods (is_event_processed, delete_processed_events, etc.) will follow similar patterns,
    # ensuring that the connection is obtained directly and used for each operation.



    def is_event_processed(self, event_id: str) -> bool:
        """
        Checks if an event has already been processed.

        Args:
            event_id (str): The unique identifier of the event.

        Returns:
            bool: True if the event has been processed, False otherwise.

        Raises:
            sqlite3.Error: If there is an error querying the database.
        """
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute('''
                SELECT status FROM event_queue WHERE event_id = ?
            ''', (event_id,))
            result = cursor.fetchone()
            return bool(result and result[0] == 'processed')
        except sqlite3.Error as e:
            logger.error(f"Error checking if event {event_id} is processed: {e}", exc_info=True)
            raise

    def delete_processed_events(self) -> None:
        """
        Deletes all processed events from the event_queue table.

        Raises:
            sqlite3.Error: If there is an error deleting processed events.
        """
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute('''
                DELETE FROM event_queue WHERE status = 'processed'
            ''')
            conn.commit()
            logger.info("Deleted all processed events from the queue.")
        except sqlite3.Error as e:
            logger.error(f"Error deleting processed events: {e}", exc_info=True)
            raise

    def get_pending_events(self, limit: int = 10) -> List[str]:
        """
        Retrieves a list of pending event IDs from the queue.

        Args:
            limit (int): The maximum number of event IDs to retrieve.

        Returns:
            List[str]: A list of pending event IDs.

        Raises:
            sqlite3.Error: If there is an error retrieving pending events.
        """
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute('''
                SELECT event_id FROM event_queue
                WHERE status = 'pending'
                ORDER BY created_at ASC
                LIMIT ?
            ''', (limit,))
            rows = cursor.fetchall()
            event_ids = [row[0] for row in rows]
            logger.info(f"Retrieved {len(event_ids)} pending event(s) from the queue.")
            return event_ids
        except sqlite3.Error as e:
            logger.error(f"Error retrieving pending events: {e}", exc_info=True)
            raise

    def add_node_to_queue(self, node_ids: List[int]) -> None:
        """
        Adds one or more node IDs to the processing queue.

        Args:
            node_ids (List[int]): A list of node IDs to add to the queue.

        Raises:
            sqlite3.Error: If there is an error adding nodes to the queue.
        """
        if not isinstance(node_ids, list):
            node_ids = [node_ids]

        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.executemany('''
                INSERT INTO node_processing_queue (node_id)
                VALUES (?)
            ''', [(node_id,) for node_id in node_ids])
            conn.commit()
            logger.info(f"Successfully added {len(node_ids)} node(s) to the processing queue.")
        except sqlite3.Error as e:
            logger.error(f"Error adding nodes to the processing queue: {e}", exc_info=True)
            raise

    def get_pending_nodes(self, limit: int = 10) -> List[int]:
        """
        Retrieves a list of pending node IDs from the queue.

        Args:
            limit (int): The maximum number of node IDs to retrieve.

        Returns:
            List[int]: A list of pending node IDs.

        Raises:
            sqlite3.Error: If there is an error retrieving pending nodes.
        """
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute('''
                SELECT node_id FROM node_processing_queue
                WHERE status = 'pending'
                ORDER BY created_at ASC
                LIMIT ?
            ''', (limit,))
            rows = cursor.fetchall()
            node_ids = [row[0] for row in rows]
            logger.info(f"Retrieved {len(node_ids)} pending node(s) from the queue.")
            return node_ids
        except sqlite3.Error as e:
            logger.error(f"Error retrieving pending nodes: {e}", exc_info=True)
            raise

    def mark_node_as_processed(self, node_id: int) -> None:
        """
        Marks a node as processed in the database.

        Args:
            node_id (int): The ID of the node to mark as processed.

        Raises:
            sqlite3.Error: If there is an error updating the node status.
        """
        try:
            conn = self.get_connection()
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE node_processing_queue
                SET status = 'processed', processed_at = CURRENT_TIMESTAMP
                WHERE node_id = ?
            ''', (node_id,))
            conn.commit()
            logger.info(f"Node {node_id} marked as processed.")
        except sqlite3.Error as e:
            logger.error(f"Error marking node {node_id} as processed: {e}", exc_info=True)
            raise

    def close(self) -> None:
        """
        Closes the database connection.
        """
        try:
            # No need to close anything specific since we are using a direct connection per method call.
            logger.info("No persistent connection to close.")
        except sqlite3.Error as e:
            logger.error(f"Error closing the SQLite database connection: {e}", exc_info=True)
            raise