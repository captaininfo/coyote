"""
coyote_state_manager.py

Module for managing the state of events and nodes in the Coyote application,
using a SQLite database to queue and track processing.
"""

import logging
import sqlite3
from flask import g
from pathlib import Path
from typing import List

from coyote.utils.config_manager import get_event_data_db_connection, get_state_db_connection

logger = logging.getLogger(__name__)


class CoyoteStateManager:
    """
    Manages the state of events and nodes in the Coyote application,
    including queuing for processing and updating their status.
    """

    def __init__(self) -> None:
        """
        Initializes the CoyoteStateManager.
        """
        pass

    def get_connection(self):
        if 'state_db_conn' not in g:
            g.state_db_conn = get_state_db_connection()
        return g.state_db_conn


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
            with self._lock:
                self.cursor.execute('''
                    SELECT status FROM event_queue WHERE event_id = ?
                ''', (event_id,))
                result = self.cursor.fetchone()
                if result and result[0] == 'processed':
                    return True
                return False
        except sqlite3.Error as e:
            logger.error(f"Error checking if event {event_id} is processed: {e}", exc_info=True)
            raise

    def mark_event_as_processing(self, event_id: str) -> None:
        """
        Marks an event as being processed.
        """
        try:
            with self.conn:
                self.cursor.execute('''
                    UPDATE event_queue
                    SET status = 'processing'
                    WHERE event_id = ?
                ''', (event_id,))
            logger.info(f"Event {event_id} marked as processing.")
        except sqlite3.Error as e:
            logger.error(f"Error marking event {event_id} as processing: {e}", exc_info=True)
            raise


    def mark_event_as_processed(self, event_id: str) -> None:
        """
        Marks an event as processed.
        """
        try:
            with self.conn:
                self.cursor.execute('''
                    UPDATE event_queue
                    SET status = 'processed', processed_at = CURRENT_TIMESTAMP
                    WHERE event_id = ?
                ''', (event_id,))
            logger.info(f"Event {event_id} marked as processed.")
        except sqlite3.Error as e:
            logger.error(f"Error marking event {event_id} as processed: {e}", exc_info=True)
            raise


    def delete_processed_events(self) -> None:
        """
        Deletes all processed events from the event_queue table.

        Raises:
            sqlite3.Error: If there is an error deleting processed events.
        """
        try:
            with self._lock, self.conn:
                self.cursor.execute('''
                    DELETE FROM event_queue WHERE status = 'processed'
                ''')
            logger.info("Deleted all processed events from the queue.")
        except sqlite3.Error as e:
            logger.error(f"Error deleting processed events: {e}", exc_info=True)
            raise

    def get_pending_events(self, limit: int = 10) -> List[str]:
        """
        Retrieves a list of pending event IDs from the queue.
        """
        try:
            self.cursor.execute('''
                SELECT event_id FROM event_queue
                WHERE status = 'pending'
                ORDER BY created_at ASC
                LIMIT ?
            ''', (limit,))
            rows = self.cursor.fetchall()
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
            with self._lock, self.conn:
                self.cursor.executemany('''
                    INSERT INTO node_processing_queue (node_id)
                    VALUES (?)
                ''', [(node_id,) for node_id in node_ids])
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
            with self._lock:
                self.cursor.execute('''
                    SELECT node_id FROM node_processing_queue
                    WHERE status = 'pending'
                    ORDER BY created_at ASC
                    LIMIT ?
                ''', (limit,))
                rows = self.cursor.fetchall()
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
            with self._lock, self.conn:
                self.cursor.execute('''
                    UPDATE node_processing_queue
                    SET status = 'processed', processed_at = CURRENT_TIMESTAMP
                    WHERE node_id = ?
                ''', (node_id,))
            logger.info(f"Node {node_id} marked as processed.")
        except sqlite3.Error as e:
            logger.error(f"Error marking node {node_id} as processed: {e}", exc_info=True)
            raise

    def close(self) -> None:
        """
        Closes the database connection.
        """
        try:
            self.conn.close()
            logger.info("SQLite database connection closed.")
        except sqlite3.Error as e:
            logger.error(f"Error closing the SQLite database connection: {e}", exc_info=True)
            raise
