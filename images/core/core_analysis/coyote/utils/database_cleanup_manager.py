# coyote/maintenance/database_cleanup_manager.py
import logging, os, threading, time
from datetime import datetime, timedelta
from pathlib import Path
import sqlite3
from coyote.utils.config_container import DATA_DIR as _DATA_DIR
from coyote.utils.config_manager import (
    get_state_db_connection,
    get_state_read_only_connection,
    event_data_db_lock,
    EVENT_DATA_DB_FILE as EVENT_DATA_PATH,
    WIKIDATA_CACHE_DB_FILE,
)

logger = logging.getLogger(__name__)
CLEANUP_INTERVAL = 6 * 60  # seconds – run every 6 minutes
# Normalize to Path for safety even if older code passes str
DATA_DIR = _DATA_DIR if isinstance(_DATA_DIR, Path) else Path(_DATA_DIR)

class CoyoteDatabaseCleanupManager(threading.Thread):
    """Background janitor that keeps the SQLite files and Neo4j tidy."""

    daemon = True  # dies with the main process
    def __init__(self) -> None:
        super().__init__(name="CoyoteCleanup")
        self._stop = threading.Event()

    # ───────────────────────── public API ──────────────────────────
    def stop(self) -> None:
        self._stop.set()

    # ───────────────────────── main loop ───────────────────────────
    def run(self) -> None:
        logger.info("Cleanup manager started (every %ss).", CLEANUP_INTERVAL)
        while not self._stop.is_set():
            try:
                self._cleanup_staging_db()
                self._cleanup_fully_processed_events()
                self._cleanup_wikidata_cache()
                # optional: self._cleanup_neo4j()
            except Exception:  # never let an exception kill the thread
                logger.exception("Unexpected error in cleanup manager")
            self._stop.wait(CLEANUP_INTERVAL)

    # ───────────────────────── helpers ─────────────────────────────
    def _cleanup_staging_db(self) -> None:
        path = (DATA_DIR / "coyote_event_staging.db").resolve()
        threshold = (datetime.now() - timedelta(days=7)).isoformat()

        with sqlite3.connect(path) as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM EventStaging WHERE timestamp < ?", (threshold,))
            deleted = cur.rowcount
            conn.commit()
        logger.debug("Staging‑DB: removed %s rows older than 7 days", deleted)

    # ───────────────── TERMINAL-EVENT CLEANER ──────────────────────
    def _cleanup_fully_processed_events(self) -> None:
        """
        Remove events that reached a terminal status from **all** local DBs
        (staging, event-data and state).  The cascade FKs in event-data
        take care of satellite tables.
        """
        TERMINAL = ("ontology_processed", "ontology_failed")

        # 1. collect terminal IDs
        with get_state_read_only_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT event_id FROM event_queue WHERE status IN (?, ?)",
                TERMINAL,
            )
            ids = [row[0] for row in cur.fetchall()]

        if not ids:
            return

        # 2. purge from staging DB
        staging_path = (DATA_DIR / "coyote_event_staging.db").resolve()
        with sqlite3.connect(staging_path) as conn:
            cur = conn.cursor()
            cur.executemany("DELETE FROM EventStaging WHERE event_id = ?", [(i,) for i in ids])
            conn.commit()

        # 3. purge from event-data DB (ON DELETE CASCADE handles satellites)
        with event_data_db_lock, sqlite3.connect(EVENT_DATA_PATH) as conn:
            cur = conn.cursor()
            cur.executemany("DELETE FROM Events WHERE event_id = ?", [(i,) for i in ids])
            conn.commit()

        # 4. finally delete from event_queue itself
        with get_state_db_connection() as conn:
            cur = conn.cursor()
            cur.executemany("DELETE FROM event_queue WHERE event_id = ?", [(i,) for i in ids])
            conn.commit()

        logger.debug("Full cleanup: purged %s terminal events from all DBs", len(ids))

    def _cleanup_wikidata_cache(self) -> None:
        # wikidata_cache.db holds the URI cache and term cache as two tables; both purged here.
        uri_thresh = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
        term_ttl_days = int(os.environ.get("WIKIDATA_TERM_CACHE_TTL_DAYS", "30"))
        term_thresh = (datetime.now() - timedelta(days=term_ttl_days)).strftime("%Y-%m-%d %H:%M:%S")
        with sqlite3.connect(WIKIDATA_CACHE_DB_FILE) as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM wikidata_cache WHERE timestamp < ?", (uri_thresh,))
            uri_purged = cur.rowcount
            cur.execute("DELETE FROM wikidata_term_cache WHERE timestamp < ?", (term_thresh,))
            term_purged = cur.rowcount
            conn.commit()
        logger.debug("Wikidata URI-cache: purged %s expired rows", uri_purged)
        logger.debug("Wikidata term-cache: purged %s expired rows (TTL %dd)", term_purged, term_ttl_days)

