# coyote/utils/config_container.py
"""
Container-aware config for paths. Defaults are derived from COYOTE_DATA_DIR
and resolve to writable paths inside the container.

- Explicit constants for commonly used files/dirs.
- Dynamic fallback: any NAME like FOOL_DB_FILE -> /app/data/fool.db, etc.
- Ensures log/data dirs exist.
"""

from __future__ import annotations
import os
from pathlib import Path

# Base data dir (mapped in compose: ${COYOTE_USER_DATA}:/app/data)
_BASE = Path(os.getenv("COYOTE_DATA_DIR", "/app/data")).resolve()

# Ensure base and logs dir exist
(_BASE).mkdir(parents=True, exist_ok=True)
(_BASE / "logs").mkdir(parents=True, exist_ok=True)

# Plain string variants for older code that expects str rather than Path
COYOTE_DATA_DIR        = str(_BASE)
COYOTE_SERVER_LOG_DIR  = str(_BASE / "logs")
COYOTE_SERVER_LOG_FILE = str(_BASE / "logs" / "coyote_server.log")
# Back-compat aliases used by older code
DATA_DIR               = COYOTE_DATA_DIR
LOGS_DIR               = COYOTE_SERVER_LOG_DIR

# ---- Canonical filenames (avoid accidental generics like state.db/event_data.db)
STATE_DB_FILE          = str(_BASE / "coyote_state.db")
EVENT_DATA_DB_FILE     = str(_BASE / "coyote_event_data.db")
EVENT_STAGING_DB_FILE  = str(_BASE / "coyote_event_staging.db")
STAGING_DB_FILE        = EVENT_STAGING_DB_FILE  # back-compat alias
WIKIDATA_CACHE_DB_FILE = str(_BASE / "wikidata_cache.db")

# Keys/secrets (preserve existing on disk if present)
KEY_FILE               = str(_BASE / "coyote_encryption_key.key")
SECRET_KEY_FILE        = str(_BASE / "coyote_secret_key.key")

# Some callers import LOG_FILE – point to the server log file
LOG_FILE               = COYOTE_SERVER_LOG_FILE

# You can optionally predefine other known DBs here, but it's not required
# because of the dynamic fallback implemented below.
# Example:
# EVENTS_DB_FILE        = str(_BASE / "events.db")
# GRAPH_DB_FILE         = str(_BASE / "graph.db")

def __getattr__(name: str):
    """
    Dynamic fallback so `from ... import FOO_DB_FILE` won't crash even if
    FOO_DB_FILE isn't explicitly declared.

    Rules:
      *_DB_FILE   -> /app/data/<lowercased stem>.db
      *_DIR       -> /app/data/<lowercased stem>  (dir created on access)
      *_LOG_FILE  -> /app/data/logs/<lowercased stem>.log
      *_FILE      -> /app/data/<lowercased stem>  (generic file path)
    """
    lower = name.lower()
    if lower.endswith("_db_file"):
        stem = lower[:-8]  # remove '_db_file'
        return str(_BASE / f"{stem}.db")
    if lower.endswith("_dir"):
        stem = lower[:-4]
        p = _BASE / stem
        p.mkdir(parents=True, exist_ok=True)
        return str(p)
    if lower.endswith("_log_file"):
        stem = lower[:-9]
        return str(_BASE / "logs" / f"{stem}.log")
    if lower.endswith("_file"):
        stem = lower[:-5]
        return str(_BASE / f"{stem}")
    raise AttributeError(name)

def __dir__():
    return sorted(list(globals().keys()) + [
        "COYOTE_DATA_DIR","COYOTE_SERVER_LOG_DIR","COYOTE_SERVER_LOG_FILE",
        "DATA_DIR","LOGS_DIR",
        "STATE_DB_FILE","EVENT_DATA_DB_FILE","EVENT_STAGING_DB_FILE",
        "STAGING_DB_FILE","WIKIDATA_CACHE_DB_FILE",
        "KEY_FILE","SECRET_KEY_FILE","LOG_FILE",
        "__getattr__"
    ])

