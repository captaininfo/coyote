"""
Unit tests for the WikiData circuit breaker in
connect_to_ontology.batch_query_wikidata.

Covers:
- 403/429 trip the breaker
- Open state short-circuits without SPARQL call
- 5xx (EndPointInternalError) does NOT trip the breaker
- 429 does NOT pollute the cache with empty entries
- Pre-cached empty list [] is treated as a cache hit (the `if data:` bugfix)
- Explicit User-Agent string is set on SPARQLWrapper
"""
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

import pytest


# --- Stub heavy module-level imports BEFORE loading target module ---------
# connect_to_ontology imports neo4j and SPARQLWrapper at module load; both
# are unnecessary for testing the breaker. coyote.utils.config_manager is
# only used inside the manager class, not in batch_query_wikidata.

class _StubEndPointInternalError(Exception):
    """Stand-in for SPARQLExceptions.EndPointInternalError."""


_sparql_stub = MagicMock()
_sparql_stub.JSON = "json"
_sparql_stub.SPARQLExceptions = MagicMock()
_sparql_stub.SPARQLExceptions.EndPointInternalError = _StubEndPointInternalError
sys.modules.setdefault("SPARQLWrapper", _sparql_stub)

_neo4j_stub = MagicMock()
sys.modules.setdefault("neo4j", _neo4j_stub)

sys.path.insert(
    0, str(Path(__file__).parent.parent / "images" / "core" / "core_analysis")
)

sys.modules.setdefault("coyote.utils.config_manager", MagicMock())

from coyote.neo4j_integration import connect_to_ontology as target  # noqa: E402


class _MockHeaders:
    """Minimal HTTPMessage stand-in: only .get('Retry-After') is exercised."""
    def __init__(self, retry_after=None):
        self._retry_after = retry_after

    def get(self, key, default=None):
        if key == "Retry-After":
            return self._retry_after
        return default


def _make_http_error(code: int, retry_after=None) -> HTTPError:
    return HTTPError(
        url="https://query.wikidata.org/sparql",
        code=code,
        msg=f"HTTP {code}",
        hdrs=_MockHeaders(retry_after=retry_after),
        fp=None,
    )


@pytest.fixture(autouse=True)
def _reset_breaker():
    target._breaker_reset_for_tests()
    yield
    target._breaker_reset_for_tests()


@pytest.fixture
def tmp_cache_db(tmp_path):
    db_path = tmp_path / "wikidata_cache.db"
    target.initialize_cache_db(db_path)
    return db_path


def _seed_cache(db_path: Path, uri: str, data) -> None:
    """Write directly into the cache, bypassing save_to_cache for control."""
    with sqlite3.connect(db_path) as conn:
        c = conn.cursor()
        c.execute(
            "INSERT OR REPLACE INTO wikidata_cache (uri, data, timestamp) VALUES (?, ?, ?)",
            (uri, json.dumps(data), datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        )
        conn.commit()


def _count_cache_rows(db_path: Path, uri: str) -> int:
    with sqlite3.connect(db_path) as conn:
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM wikidata_cache WHERE uri = ?", (uri,))
        return c.fetchone()[0]


URI = "http://www.wikidata.org/entity/Q42"


# ---------------------------------------------------------------------------
# Breaker integration with batch_query_wikidata
# ---------------------------------------------------------------------------

class TestBatchQueryBreaker:
    def test_429_trips_breaker(self, tmp_cache_db):
        with patch.object(target, "SPARQLWrapper") as mock_sw:
            mock_sw.return_value.query.side_effect = _make_http_error(429, retry_after="60")
            result = target.batch_query_wikidata([URI], tmp_cache_db)
        assert target._breaker_check_state() == "open"
        # URI is absent from the returned dict — callers default to [] via .get.
        assert URI not in result

    def test_403_trips_breaker(self, tmp_cache_db):
        with patch.object(target, "SPARQLWrapper") as mock_sw:
            mock_sw.return_value.query.side_effect = _make_http_error(403)
            target.batch_query_wikidata([URI], tmp_cache_db)
        assert target._breaker_check_state() == "open"

    def test_open_state_short_circuits_without_sparql_call(self, tmp_cache_db):
        target._breaker_record_failure()  # trip to open
        with patch.object(target, "SPARQLWrapper") as mock_sw:
            result = target.batch_query_wikidata([URI], tmp_cache_db)
        mock_sw.assert_not_called()
        assert result == {}

    def test_5xx_does_not_trip_breaker(self, tmp_cache_db):
        with patch.object(target, "SPARQLWrapper") as mock_sw:
            mock_sw.return_value.query.side_effect = _StubEndPointInternalError("503 Service Unavailable")
            target.batch_query_wikidata([URI], tmp_cache_db)
        # 5xx is transient server-side; breaker stays closed.
        assert target._breaker_check_state() == "closed"

    def test_429_does_not_write_to_cache(self, tmp_cache_db):
        """Sonnet's correction: caching [] on 429 would suppress edges for
        CACHE_EXPIRATION_DAYS even after WDQS recovers. The breaker handles
        suppression instead."""
        with patch.object(target, "SPARQLWrapper") as mock_sw:
            mock_sw.return_value.query.side_effect = _make_http_error(429)
            target.batch_query_wikidata([URI], tmp_cache_db)
        assert _count_cache_rows(tmp_cache_db, URI) == 0

    def test_empty_list_cache_hit_skips_sparql(self, tmp_cache_db):
        """The `if data:` → `if data is not None:` bugfix.
        Pre-cached `[]` is a legitimate "no parents" cache hit and must
        not trigger a re-query.

        Exercises batch_query_wikidata's cache-check path directly. The
        mock asserts SPARQLWrapper is never even instantiated, which is
        the load-bearing assertion: a successful query returning empty
        bindings would also return {URI: []}, so checking the return
        value alone cannot distinguish cache-hit from cache-miss."""
        _seed_cache(tmp_cache_db, URI, [])
        with patch.object(target, "SPARQLWrapper") as mock_sw:
            result = target.batch_query_wikidata([URI], tmp_cache_db)
        assert result == {URI: []}
        mock_sw.assert_not_called()

    def test_user_agent_set_before_query(self, tmp_cache_db):
        """Wikimedia blocks generic User-Agent strings. Verify ours is set
        on the SPARQLWrapper instance before .query() is called."""
        captured_agents = []

        class _Capturer:
            def __init__(self, url):
                self.url = url
                self._agent = None

            @property
            def agent(self):
                return self._agent

            @agent.setter
            def agent(self, value):
                self._agent = value
                captured_agents.append(value)

            def setQuery(self, q):
                pass

            def setReturnFormat(self, f):
                pass

            def query(self):
                # By the time query() runs, agent must already be set.
                assert self._agent is not None, "User-Agent not set before query()"
                m = MagicMock()
                m.convert.return_value = {"results": {"bindings": []}}
                return m

        with patch.object(target, "SPARQLWrapper", _Capturer):
            target.batch_query_wikidata([URI], tmp_cache_db)

        assert captured_agents, "SPARQLWrapper was not instantiated"
        ua = captured_agents[0]
        assert "Coyote" in ua
        assert "mailto:" in ua
