"""
coyote_state_manager.py

Module for managing the state of nodes in the Coyote application,
using a SQLite database to queue and track node processing.
"""

import logging
import sqlite3
from pathlib import Path
from typing import List

from coyote.utils.config_manager import DATABASE_FILE

logger = logging.getLogger(__name__)


class CoyoteStateManager:
    """
    Manages the state of nodes in the Coyote application, including queuing nodes for processing and updating their status.
    """

    def __init__(self, db_path: Path = DATABASE_FILE) -> None:
        """
        Initializes the CoyoteStateManager with the given database path.

        Args:
            db_path (Path): The path to the SQLite database file.

        Raises:
            sqlite3.Error: If there is an error connecting to the database.
        """
        try:
            self.conn = sqlite3.connect(db_path)
            self.cursor = self.conn.cursor()
            self._initialize_db()
            logger.info("Connected to the SQLite database successfully.")
        except sqlite3.Error as e:
            logger.error(f"Error connecting to SQLite database: {e}", exc_info=True)
            raise

    def _initialize_db(self) -> None:
        """
        Initializes the database by creating necessary tables if they do not exist.

        Raises:
            sqlite3.Error: If there is an error initializing the database.
        """
        try:
            self.cursor.execute('''
                CREATE TABLE IF NOT EXISTS node_processing_queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    node_id INTEGER NOT NULL,
                    status TEXT DEFAULT 'pending',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    processed_at TIMESTAMP
                )
            ''')
            self.conn.commit()
            logger.info("Database initialized successfully.")
        except sqlite3.Error as e:
            logger.error(f"Error initializing the database: {e}", exc_info=True)
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
            self.cursor.executemany('''
                INSERT INTO node_processing_queue (node_id)
                VALUES (?)
            ''', [(node_id,) for node_id in node_ids])
            self.conn.commit()
            logger.info(f"Successfully added {len(node_ids)} node(s) to the processing queue.")
        except sqlite3.Error as e:
            logger.error(f"Error adding nodes to the processing queue: {e}", exc_info=True)
            self.conn.rollback()
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
            self.cursor.execute('''
                UPDATE node_processing_queue
                SET status = 'processed', processed_at = CURRENT_TIMESTAMP
                WHERE node_id = ?
            ''', (node_id,))
            self.conn.commit()
            logger.info(f"Node {node_id} marked as processed.")
        except sqlite3.Error as e:
            logger.error(f"Error marking node {node_id} as processed: {e}", exc_info=True)
            self.conn.rollback()
            raise

    def close(self) -> None:
        """
        Closes the database connection.

        Raises:
            sqlite3.Error: If there is an error closing the database connection.
        """
        try:
            self.conn.close()
            logger.info("SQLite database connection closed.")
        except sqlite3.Error as e:
            logger.error(f"Error closing the SQLite database connection: {e}", exc_info=True)
            raise
