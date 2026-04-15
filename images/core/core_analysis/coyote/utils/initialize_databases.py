# initialize_databases.py

"""
initialize_databases.py

Provides functions to initialize the databases used by the Coyote application:
- coyote_state.db
- coyote_event_staging.db
- coyote_event_data.db
- wikidata_cache.db
"""

import os
import logging
import sqlite3
from pathlib import Path
from coyote.utils.config_container import (
    DATA_DIR,
    STATE_DB_FILE,
    EVENT_STAGING_DB_FILE,
    EVENT_DATA_DB_FILE,
    WIKIDATA_CACHE_DB_FILE
)

# Get the logger for this module
logger = logging.getLogger(__name__)

logger.info(f"Database files will be created in: {DATA_DIR}")
logger.info(f"  State DB: {STATE_DB_FILE}")
logger.info(f"  Event Staging DB: {EVENT_STAGING_DB_FILE}")
logger.info(f"  Event Data DB: {EVENT_DATA_DB_FILE}")
logger.info(f"  Wikidata Cache DB: {WIKIDATA_CACHE_DB_FILE}")


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

    CREATE TABLE IF NOT EXISTS event_queue (
        event_id TEXT PRIMARY KEY,
        status TEXT DEFAULT 'pending',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        processed_at TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS node_processing_queue (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        node_id INTEGER NOT NULL,
        status TEXT DEFAULT 'pending',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        processed_at TIMESTAMP
    );
    '''
    initialize_database(STATE_DB_FILE, schema_sql)


def initialize_coyote_event_staging_db() -> None:
    """
    Initializes the coyote_event_staging.db with the required tables.
    """
    schema_sql = '''
    CREATE TABLE IF NOT EXISTS EventStaging (
        event_id TEXT PRIMARY KEY,
        event_type TEXT NOT NULL,
        timestamp TEXT NOT NULL,
        data_source TEXT NOT NULL,

        -- Event-specific fields (nullable depending on event type)
        purpose TEXT,
        search_terms TEXT,
        url TEXT,
        webpage_title TEXT,
        annotation_id TEXT,
        annotation_text TEXT,
        highlighted_text TEXT,
        tags TEXT,
        user_account TEXT,
        groups TEXT,
        visibility TEXT,
        source_url TEXT,
        destination_url TEXT,
        link_text TEXT,

        -- Generic field for any additional data (to support future event types)
        event_payload TEXT,  -- JSON-encoded key-value pairs

        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    '''
    initialize_database(EVENT_STAGING_DB_FILE, schema_sql)


def initialize_coyote_event_data_db() -> None:
    """
    Initializes the coyote_event_data.db with the required tables and enables WAL mode.
    """
    schema_sql = '''
    CREATE TABLE IF NOT EXISTS Events (
        event_id   TEXT PRIMARY KEY,          -- <── your canonical key
        timestamp  TEXT NOT NULL,
        event_type TEXT NOT NULL,
        data_source TEXT,
        processed  INTEGER DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS SearchEvents (
        id                INTEGER PRIMARY KEY AUTOINCREMENT,
        event_id          TEXT NOT NULL,
        purpose           TEXT,
        search_terms      TEXT,
        search_terms_relevance_score REAL,
        FOREIGN KEY(event_id) REFERENCES Events(event_id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS WebpageLoads (
        id                INTEGER PRIMARY KEY AUTOINCREMENT,
        event_id          TEXT NOT NULL,
        url               TEXT,
        wayback_machine_url TEXT,
        webpage_title     TEXT,
        scraped_text      TEXT,
        webpage_summary   TEXT,
        webpage_relevance_score REAL,
        embedding         TEXT,
        embedding_text    TEXT,
        embedding_generated_at TEXT,
        FOREIGN KEY(event_id) REFERENCES Events(event_id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS HyperlinkClicks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_id TEXT NOT NULL,
        source_url TEXT,
        destination_url TEXT,
        link_text TEXT,
        FOREIGN KEY(event_id) REFERENCES Events(event_id) ON DELETE CASCADE
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
        groups TEXT,
        visibility TEXT,
        embedding TEXT,
        embedding_text TEXT,
        embedding_generated_at TEXT,
        FOREIGN KEY(event_id) REFERENCES Events(event_id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS Entities (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_id TEXT NOT NULL,
        entity_context TEXT NOT NULL,
        entity TEXT NOT NULL,
        wikidata_uri TEXT,
        label TEXT,
        score REAL,
        FOREIGN KEY(event_id) REFERENCES Events(event_id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS Topics (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_id TEXT NOT NULL,
        topic_context TEXT NOT NULL,
        topic TEXT NOT NULL,
        wikidata_uri TEXT,
        label TEXT,
        score REAL,
        FOREIGN KEY(event_id) REFERENCES Events(event_id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS AnnotationTags (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_id TEXT NOT NULL,
        annotation_id TEXT NOT NULL,
        tag TEXT NOT NULL,
        FOREIGN KEY(event_id) REFERENCES Events(event_id) ON DELETE CASCADE,
        FOREIGN KEY(annotation_id) REFERENCES Annotations(annotation_id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS EventTracking (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        event_id TEXT NOT NULL,
        status TEXT NOT NULL,
        last_step TEXT DEFAULT NULL,  -- Tracks the last completed NLP step (e.g., "NER")
        error_message TEXT DEFAULT NULL,  -- Stores error details if status is "failed"
        last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY(event_id) REFERENCES Events(event_id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS CorpusDocuments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT,
        content TEXT,
        source TEXT,  -- e.g., "TEDTalk"
        inserted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE INDEX IF NOT EXISTS idx_events_event_id ON Events(event_id);
    CREATE INDEX IF NOT EXISTS idx_entities_event_id ON Entities(event_id);
    CREATE INDEX IF NOT EXISTS idx_topics_event_id ON Topics(event_id);
    '''
    initialize_database(EVENT_DATA_DB_FILE, schema_sql)
    enable_wal_mode(EVENT_DATA_DB_FILE)
    

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



def enable_wal_mode(db_file: Path) -> None:
    """
    Enables Write-Ahead Logging (WAL) mode for the specified SQLite database.

    Args:
        db_file (Path): The path to the SQLite database file.
    """
    try:
        conn = sqlite3.connect(db_file, timeout=10.0)
        cursor = conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL;")
        wal_mode = cursor.fetchone()
        if wal_mode and wal_mode[0].upper() == 'WAL':
            logger.info(f"WAL mode enabled for '{db_file}'.")
        else:
            logger.warning(f"Failed to enable WAL mode for '{db_file}'. Current mode: {wal_mode[0] if wal_mode else 'Unknown'}")
        conn.commit()
    except sqlite3.Error as e:
        logger.error(f"SQLite error while enabling WAL mode for '{db_file.name}': {e}")
    finally:
        if conn:
            conn.close()


def main() -> None:
    """
    Main function to initialize all databases.
    """
    logger.info("Starting database initialization...")
    initialize_coyote_state_db()
    initialize_coyote_event_staging_db()
    initialize_coyote_event_data_db()
    initialize_wikidata_cache_db()
    logger.info("Database initialization completed.")


if __name__ == '__main__':
    main()
