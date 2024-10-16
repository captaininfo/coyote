import sqlite3
import logging

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class CoyoteStateManager:
    def __init__(self, db_path='coyote_state.db'):
        try:
            self.conn = sqlite3.connect(db_path)
            self.cursor = self.conn.cursor()
            self._initialize_db()
            logging.info("Connected to the SQLite database successfully.")
        except sqlite3.Error as e:
            logging.error(f"Error connecting to SQLite database: {e}")
            raise

    def _initialize_db(self):
        try:
            # Create the table if it doesn't exist
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
            logging.info("Database initialized successfully.")
        except sqlite3.Error as e:
            logging.error(f"Error initializing the database: {e}")
            raise

    def add_node_to_queue(self, node_ids):
        if not isinstance(node_ids, list):
            node_ids = [node_ids]
        
        try:
            for node_id in node_ids:
                self.cursor.execute('''
                    INSERT INTO node_processing_queue (node_id)
                    VALUES (?)
                ''', (node_id,))
            self.conn.commit()
            logging.info(f"Successfully added {len(node_ids)} node(s) to the processing queue.")
        except sqlite3.Error as e:
            logging.error(f"Error adding nodes to the processing queue: {e}")
            self.conn.rollback()


    def get_pending_nodes(self, limit=10):
        try:
            self.cursor.execute('''
                SELECT node_id FROM node_processing_queue
                WHERE status = 'pending'
                ORDER BY created_at ASC
                LIMIT ?
            ''', (limit,))
            nodes = self.cursor.fetchall()
            logging.info(f"Retrieved {len(nodes)} pending nodes from the queue.")
            return nodes
        except sqlite3.Error as e:
            logging.error(f"Error retrieving pending nodes: {e}")
            raise

    def mark_node_as_processed(self, node_id):
        try:
            self.cursor.execute('''
                UPDATE node_processing_queue
                SET status = 'processed', processed_at = CURRENT_TIMESTAMP
                WHERE node_id = ?
            ''', (node_id,))
            self.conn.commit()
            logging.info(f"Node {node_id} marked as processed.")
        except sqlite3.Error as e:
            logging.error(f"Error marking node {node_id} as processed: {e}")
            raise

    def close(self):
        try:
            self.conn.close()
            logging.info("SQLite database connection closed.")
        except sqlite3.Error as e:
            logging.error(f"Error closing the SQLite database connection: {e}")
            raise
