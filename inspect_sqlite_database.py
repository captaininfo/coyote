import sqlite3
import logging

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def inspect_db(db_path):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    try:
        # Check if the table exists
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='node_processing_queue';")
        table_exists = cursor.fetchone()
        if table_exists:
            logging.info("Table 'node_processing_queue' exists.")
            # Check if the table has any rows
            cursor.execute("SELECT COUNT(*) FROM node_processing_queue")
            row_count = cursor.fetchone()[0]
            if row_count > 0:
                logging.info(f"Table 'node_processing_queue' has {row_count} rows.")
                cursor.execute("SELECT * FROM node_processing_queue")
                rows = cursor.fetchall()
                for row in rows:
                    logging.info(f"Node ID: {row[1]}, Status: {row[2]}, Created At: {row[3]}, Processed At: {row[4]}")
            else:
                logging.info("Table 'node_processing_queue' is empty.")
        else:
            logging.info("Table 'node_processing_queue' does not exist.")
    except sqlite3.OperationalError as e:
        logging.error(f"An error occurred: {e}")
    finally:
        conn.close()

inspect_db('coyote_state.db')  # Replace with your actual database path if different
