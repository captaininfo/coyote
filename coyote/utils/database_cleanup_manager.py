# coyote/maintenance/database_cleanup_manager.py
import logging, threading, time
from datetime import datetime, timedelta
from pathlib import Path
import sqlite3
from coyote.utils.config_manager import (
    get_state_db_connection,
    get_state_read_only_connection,
)

logger = logging.getLogger(__name__)
CLEANUP_INTERVAL = 6 * 60  # seconds – run every 6 minutes

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
        from datetime import timedelta
        from coyote.utils.config_manager import DATA_DIR
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
        from coyote.utils.config_manager import DATA_DIR
        staging_path = (DATA_DIR / "coyote_event_staging.db").resolve()
        with sqlite3.connect(staging_path) as conn:
            cur = conn.cursor()
            cur.executemany("DELETE FROM EventStaging WHERE event_id = ?", [(i,) for i in ids])
            conn.commit()

        # 3. purge from event-data DB (ON DELETE CASCADE handles satellites)
        from coyote.utils.config_manager import (
            event_data_db_lock,
            EVENT_DATA_DB_FILE as EVENT_DATA_PATH,
        )

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
        cache_path = Path("data/wikidata_cache.db")
        thresh = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
        with sqlite3.connect(cache_path) as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM wikidata_cache WHERE timestamp < ?", (thresh,))
            purged = cur.rowcount
            conn.commit()
        logger.debug("Wikidata‑cache: purged %s expired rows", purged)

