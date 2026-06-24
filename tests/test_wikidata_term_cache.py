"""
Unit tests for the WikiData term→QID cache in wikidata_lookup.

Covers:
- Cache miss → Action API called → row inserted
- Fresh hit → API NOT called → cached data returned
- Cached empty ('[]') → returns [], API NOT called
- Expired row (older than TTL) → API called, row updated
- Breaker open → returns [], no cache write
- 3-tuple (label, uri, description) round-trip through JSON serialization

Transport note (Unit 7): wikidata_lookup hits the Wikibase Action API via
`requests` (not SPARQLWrapper). The host has neither installed; we stub
`requests` at module level with a REAL RequestException class. The cache shape
widened to 3-tuples (label, concepturi, description) — `[tuple(item) for item
in raw]` reconstructs them unchanged.
"""
import json
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# --- Stub `requests` BEFORE loading the target module --------------------

class _StubRequestException(Exception):
    """Stand-in for requests.RequestException (real class, catchable)."""


_requests_stub = MagicMock()
_requests_stub.RequestException = _StubRequestException
sys.modules.setdefault("requests", _requests_stub)

sys.path.insert(
    0, str(Path(__file__).parent.parent / "images" / "core" / "core_analysis")
)

from coyote.analysis import wikidata_lookup as target  # noqa: E402


# --- Helpers --------------------------------------------------------------

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS wikidata_term_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity TEXT NOT NULL UNIQUE,
    data TEXT NOT NULL,
    timestamp TEXT
);
CREATE INDEX IF NOT EXISTS idx_wikidata_term_cache_entity ON wikidata_term_cache(entity);
"""


class _MockHeaders:
    def __init__(self, retry_after=None):
        self._retry_after = retry_after

    def get(self, key, default=None):
        if key == "Retry-After":
            return self._retry_after
        return default


def _search_item(label, uri, description=""):
    return {"label": label, "concepturi": uri, "description": description}


def _mock_response(status=200, json_body=None, retry_after=None):
    """Stand-in for a requests.Response."""
    resp = MagicMock()
    resp.status_code = status
    resp.headers = _MockHeaders(retry_after=retry_after)
    resp.json.return_value = json_body if json_body is not None else {"search": []}
    return resp


# --- Fixtures -------------------------------------------------------------

@pytest.fixture
def cache_db(tmp_path, monkeypatch):
    """Fresh temp DB with the wikidata_term_cache schema; patch
    WIKIDATA_CACHE_DB_FILE in the target to point at it; reset breaker/pacing
    and cache-stats counters between tests for isolation. Pacing is disabled so
    the cache-logic tests don't sleep the 0.6s default."""
    db_path = tmp_path / "wikidata_cache.db"
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(_SCHEMA_SQL)
        conn.commit()
    monkeypatch.setattr(target, "WIKIDATA_CACHE_DB_FILE", str(db_path))
    monkeypatch.setattr(target, "_cache_hits", 0)
    monkeypatch.setattr(target, "_cache_misses", 0)
    monkeypatch.setattr(target, "WIKIDATA_ACTION_MIN_INTERVAL", 0)
    target._breaker_reset_for_tests()
    return db_path


# --- Tests ----------------------------------------------------------------

class TestTermCacheBehavior:
    def test_cache_miss_triggers_api_and_inserts_row(self, cache_db):
        body = {"search": [_search_item(
            "Artificial intelligence",
            "http://www.wikidata.org/entity/Q11660",
            "intelligence demonstrated by machines",
        )]}
        with patch.object(target.requests, "get") as mock_get:
            mock_get.return_value = _mock_response(200, json_body=body)
            result = target.query_wikidata("AI")
        assert result == [(
            "Artificial intelligence",
            "http://www.wikidata.org/entity/Q11660",
            "intelligence demonstrated by machines",
        )]
        mock_get.assert_called_once()
        with sqlite3.connect(str(cache_db)) as conn:
            row = conn.execute(
                "SELECT data FROM wikidata_term_cache WHERE entity = ?", ("AI",)
            ).fetchone()
        assert row is not None
        cached = json.loads(row[0])
        assert cached == [[
            "Artificial intelligence",
            "http://www.wikidata.org/entity/Q11660",
            "intelligence demonstrated by machines",
        ]]

    def test_fresh_hit_skips_api_and_returns_cached(self, cache_db):
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        data = json.dumps([[
            "GPT", "http://www.wikidata.org/entity/Q105434500", "language model",
        ]])
        with sqlite3.connect(str(cache_db)) as conn:
            conn.execute(
                "INSERT INTO wikidata_term_cache (entity, data, timestamp) VALUES (?, ?, ?)",
                ("GPT", data, ts),
            )
            conn.commit()
        with patch.object(target.requests, "get") as mock_get:
            result = target.query_wikidata("GPT")
        assert result == [(
            "GPT", "http://www.wikidata.org/entity/Q105434500", "language model",
        )]
        mock_get.assert_not_called()

    def test_cached_empty_returns_empty_and_skips_api(self, cache_db):
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with sqlite3.connect(str(cache_db)) as conn:
            conn.execute(
                "INSERT INTO wikidata_term_cache (entity, data, timestamp) VALUES (?, ?, ?)",
                ("no-match-term", "[]", ts),
            )
            conn.commit()
        with patch.object(target.requests, "get") as mock_get:
            result = target.query_wikidata("no-match-term")
        assert result == []
        mock_get.assert_not_called()

    def test_expired_row_triggers_api_and_updates_row(self, cache_db):
        expired_ts = (datetime.now() - timedelta(
            days=target.WIKIDATA_TERM_CACHE_TTL_DAYS + 1
        )).strftime("%Y-%m-%d %H:%M:%S")
        stale_data = json.dumps([["Stale", "http://www.wikidata.org/entity/Q999", "old"]])
        with sqlite3.connect(str(cache_db)) as conn:
            conn.execute(
                "INSERT INTO wikidata_term_cache (entity, data, timestamp) VALUES (?, ?, ?)",
                ("Python", stale_data, expired_ts),
            )
            conn.commit()
        body = {"search": [_search_item(
            "Python", "http://www.wikidata.org/entity/Q28865",
            "general-purpose programming language",
        )]}
        with patch.object(target.requests, "get") as mock_get:
            mock_get.return_value = _mock_response(200, json_body=body)
            result = target.query_wikidata("Python")
        assert result == [(
            "Python", "http://www.wikidata.org/entity/Q28865",
            "general-purpose programming language",
        )]
        mock_get.assert_called_once()
        with sqlite3.connect(str(cache_db)) as conn:
            row = conn.execute(
                "SELECT data, timestamp FROM wikidata_term_cache WHERE entity = ?", ("Python",)
            ).fetchone()
        assert row is not None
        cached = json.loads(row[0])
        assert cached == [[
            "Python", "http://www.wikidata.org/entity/Q28865",
            "general-purpose programming language",
        ]]
        assert row[1] > expired_ts  # newer timestamp

    def test_breaker_open_returns_empty_without_cache_write(self, cache_db):
        target._breaker_record_failure()  # trip to open
        assert target._breaker_check_state() == "open"
        with patch.object(target.requests, "get") as mock_get:
            result = target.query_wikidata("anything")
        assert result == []
        mock_get.assert_not_called()
        with sqlite3.connect(str(cache_db)) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM wikidata_term_cache WHERE entity = ?", ("anything",)
            ).fetchone()
        assert count[0] == 0

    def test_three_tuple_round_trip(self, cache_db):
        """_cache_store(list[3-tuple]) then _cache_lookup reconstructs 3-tuples
        (not lists) so downstream `label, uri, _ = result[0]` keeps working."""
        triples = [
            ("French Revolution", "http://www.wikidata.org/entity/Q6534", "1789 revolution"),
            ("Jacobin", "http://www.wikidata.org/entity/Q179885", "political club"),
        ]
        target._cache_store("french revolution", triples)
        loaded = target._cache_lookup("french revolution")
        assert loaded == triples
        assert all(isinstance(t, tuple) and len(t) == 3 for t in loaded)
