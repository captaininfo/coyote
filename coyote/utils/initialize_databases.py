# initialize_databases.py

"""
initialize_databases.py

Provides functions to initialize the databases used by the Coyote application:
- coyote_state.db
- coyote_event_data.db
- wikidata_cache.db
"""

import logging
import sqlite3
from pathlib import Path
from typing import Optional

# Configure logging
logger = logging.getLogger(__name__)

# Define the base directory and data directory
BASE_DIR: Path = Path(__file__).resolve().parent
DATA_DIR: Path = BASE_DIR / 'data'
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Paths to database files
STATE_DB_FILE: Path = DATA_DIR / 'coyote_state.db'
EVENT_DATA_DB_FILE: Path = DATA_DIR / 'coyote_event_data.db'
WIKIDATA_CACHE_DB_FILE: Path = DATA_DIR / 'wikidata_cache.db'


def initialize_database(db_file: Path, schema_sql: str) -> None:
    """
    Initializes a SQLite database with the given schema.

    Args:
        db_file (Path): The path to the database file.
        schema_sql (str): The SQL schema to create tables.
    """
    try:
        conn = sqlite3.connect(db_file)
        cursor = conn.cursor()
        cursor.executescript(schema_sql)
        conn.commit()
        logger.info(f"Initialized database at '{db_file}'.")
    except sqlite3.Error as e:
        logger.error(f"SQLite error during '{db_file.name}' initialization: {e}")
    finally:
        if conn:
            conn.close()


def initialize_coyote_state_db() -> None:
    """
    Initializes the coyote_state.db with the required tables.
    """
    schema_sql = '''
    CREATE TABLE IF NOT EXISTS user_settings (
        id INTEGER PRIMARY KEY,
        setting_name TEXT NOT NULL UNIQUE,
        setting_value TEXT NOT NULL
    );
    '''
    initialize_database(STATE_DB_FILE, schema_sql)


def initialize_coyote_event_data_db() -> None:
    """
    Initializes the coyote_event_data.db with the required tables.
    """
    schema_sql = '''
    CREATE TABLE IF NOT EXISTS Events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_id TEXT UNIQUE NOT NULL,
        timestamp TEXT NOT NULL,
        event_type TEXT NOT NULL,
        data_source TEXT,
        processed INTEGER DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS SearchEvents (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_id TEXT NOT NULL,
        purpose TEXT,
        search_terms TEXT,
        search_terms_relevance_score REAL,
        FOREIGN KEY(event_id) REFERENCES Events(event_id)
    );

    CREATE TABLE IF NOT EXISTS WebpageLoads (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_id TEXT NOT NULL,
        url TEXT,
        webpage_title TEXT,
        webpage_summary TEXT,
        webpage_relevance_score REAL,
        FOREIGN KEY(event_id) REFERENCES Events(event_id)
    );

    CREATE TABLE IF NOT EXISTS HyperlinkClicks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_id TEXT NOT NULL,
        source_url TEXT,
        destination_url TEXT,
        link_text TEXT,
        FOREIGN KEY(event_id) REFERENCES Events(event_id)
    );

    CREATE TABLE IF NOT EXISTS Annotations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_id TEXT NOT NULL,
        url TEXT NOT NULL,
        webpage_title TEXT,
        annotation_id TEXT UNIQUE NOT NULL,
        annotation_text TEXT,
        highlighted_text TEXT,
        user_account TEXT,
        group_id TEXT,
        visibility TEXT,
        FOREIGN KEY(event_id) REFERENCES Events(event_id)
    );

    CREATE TABLE IF NOT EXISTS Entities (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_id TEXT NOT NULL,
        entity_context TEXT NOT NULL,
        entity TEXT NOT NULL,
        uri TEXT,
        score REAL,
        FOREIGN KEY(event_id) REFERENCES Events(event_id)
    );

    CREATE TABLE IF NOT EXISTS Topics (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_id TEXT NOT NULL,
        topic_context TEXT NOT NULL,
        topic TEXT NOT NULL,
        uri TEXT,
        score REAL,
        FOREIGN KEY(event_id) REFERENCES Events(event_id)
    );

    CREATE TABLE IF NOT EXISTS AnnotationTags (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_id TEXT NOT NULL,
        annotation_id TEXT NOT NULL,
        tag TEXT NOT NULL,
        FOREIGN KEY(event_id) REFERENCES Events(event_id),
        FOREIGN KEY(annotation_id) REFERENCES Annotations(annotation_id)
    );

    CREATE INDEX IF NOT EXISTS idx_events_event_id ON Events(event_id);
    CREATE INDEX IF NOT EXISTS idx_entities_event_id ON Entities(event_id);
    CREATE INDEX IF NOT EXISTS idx_topics_event_id ON Topics(event_id);
    '''
    initialize_database(EVENT_DATA_DB_FILE, schema_sql)


def initialize_wikidata_cache_db() -> None:
    """
    Initializes the wikidata_cache.db with the required tables.
    """
    schema_sql = '''
    CREATE TABLE IF NOT EXISTS WikidataCache (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        entity TEXT NOT NULL UNIQUE,
        data TEXT NOT NULL,
        timestamp TEXT
    );

    CREATE INDEX IF NOT EXISTS idx_wikidata_cache_entity ON WikidataCache(entity);
    '''
    initialize_database(WIKIDATA_CACHE_DB_FILE, schema_sql)


def main() -> None:
    """
    Main function to initialize all databases.
    """
    logger.info("Starting database initialization...")
    initialize_coyote_state_db()
    initialize_coyote_event_data_db()
    initialize_wikidata_cache_db()
    logger.info("Database initialization completed.")


if __name__ == '__main__':
    main()
