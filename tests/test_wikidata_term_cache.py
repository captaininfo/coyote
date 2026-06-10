"""
Unit tests for the WikiData term→QID cache in text_bertopic_analysis.

Covers:
- Cache miss → SPARQL called → row inserted
- Fresh hit → SPARQL NOT called → cached data returned
- Cached empty ('[]') → returns [], SPARQL NOT called
- Expired row (older than TTL) → SPARQL called, row updated
- Breaker open → returns [], no cache write
"""
import json
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# --- Stub heavy module-level imports BEFORE loading target module ---------
# Match the pattern in test_wikidata_breaker.py.

class _StubEndPointInternalError(Exception):
    """Stand-in for SPARQLExceptions.EndPointInternalError."""


_sparql_stub = MagicMock()
_sparql_stub.JSON = "json"
_sparql_stub.SPARQLExceptions = MagicMock()
_sparql_stub.SPARQLExceptions.EndPointInternalError = _StubEndPointInternalError
sys.modules.setdefault("SPARQLWrapper", _sparql_stub)

_spacy_stub = MagicMock()
_spacy_stub.load.return_value = MagicMock()
sys.modules.setdefault("spacy", _spacy_stub)

sys.modules.setdefault("nltk", MagicMock())
_nltk_corpus_stub = MagicMock()
_nltk_corpus_stub.stopwords.words = MagicMock(return_value=["the", "and", "a"])
sys.modules.setdefault("nltk.corpus", _nltk_corpus_stub)

sys.modules.setdefault("sklearn", MagicMock())
sys.modules.setdefault("sklearn.feature_extraction", MagicMock())
sys.modules.setdefault("sklearn.feature_extraction.text", MagicMock())

sys.path.insert(
    0, str(Path(__file__).parent.parent / "images" / "core" / "core_analysis")
)

_bertopic_stub = MagicMock()
_bertopic_stub.analyze_topics = MagicMock(return_value=([], []))
sys.modules.setdefault("coyote.analysis.nlp.bertopic_analysis", _bertopic_stub)

from coyote.analysis.nlp import text_bertopic_analysis as target  # noqa: E402


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


def _binding(label, uri):
    return {"itemLabel": {"value": label}, "item": {"value": uri}}


def _sparql_response(bindings):
    return {"results": {"bindings": bindings}}


# --- Fixtures -------------------------------------------------------------

@pytest.fixture
def cache_db(tmp_path, monkeypatch):
    """Fresh temp DB with the wikidata_term_cache schema; patch
    WIKIDATA_CACHE_DB_FILE in the target to point at it; reset breaker
    and cache-stats counters between tests for isolation."""
    db_path = tmp_path / "wikidata_cache.db"
    with sqlite3.connect(str(db_path)) as conn:
        conn.executescript(_SCHEMA_SQL)
        conn.commit()
    monkeypatch.setattr(target, "WIKIDATA_CACHE_DB_FILE", str(db_path))
    monkeypatch.setattr(target, "_cache_hits", 0)
    monkeypatch.setattr(target, "_cache_misses", 0)
    target._breaker_reset_for_tests()
    return db_path


# --- Tests ----------------------------------------------------------------

class TestTermCacheBehavior:
    def test_cache_miss_triggers_sparql_and_inserts_row(self, cache_db):
        with patch.object(target, "SPARQLWrapper") as mock_sw:
            mock_sw.return_value.query.return_value.convert.return_value = _sparql_response(
                [_binding("Artificial intelligence", "http://www.wikidata.org/entity/Q11660")]
            )
            result = target.query_wikidata("AI")
        assert result == [("Artificial intelligence", "http://www.wikidata.org/entity/Q11660")]
        mock_sw.assert_called_once()
        with sqlite3.connect(str(cache_db)) as conn:
            row = conn.execute(
                "SELECT data FROM wikidata_term_cache WHERE entity = ?", ("AI",)
            ).fetchone()
        assert row is not None
        cached = json.loads(row[0])
        assert cached == [["Artificial intelligence", "http://www.wikidata.org/entity/Q11660"]]

    def test_fresh_hit_skips_sparql_and_returns_cached(self, cache_db):
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        data = json.dumps([["GPT", "http://www.wikidata.org/entity/Q105434500"]])
        with sqlite3.connect(str(cache_db)) as conn:
            conn.execute(
                "INSERT INTO wikidata_term_cache (entity, data, timestamp) VALUES (?, ?, ?)",
                ("GPT", data, ts),
            )
            conn.commit()
        with patch.object(target, "SPARQLWrapper") as mock_sw:
            result = target.query_wikidata("GPT")
        assert result == [("GPT", "http://www.wikidata.org/entity/Q105434500")]
        mock_sw.assert_not_called()

    def test_cached_empty_returns_empty_and_skips_sparql(self, cache_db):
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with sqlite3.connect(str(cache_db)) as conn:
            conn.execute(
                "INSERT INTO wikidata_term_cache (entity, data, timestamp) VALUES (?, ?, ?)",
                ("no-match-term", "[]", ts),
            )
            conn.commit()
        with patch.object(target, "SPARQLWrapper") as mock_sw:
            result = target.query_wikidata("no-match-term")
        assert result == []
        mock_sw.assert_not_called()

    def test_expired_row_triggers_sparql_and_updates_row(self, cache_db):
        expired_ts = (datetime.now() - timedelta(
            days=target.WIKIDATA_TERM_CACHE_TTL_DAYS + 1
        )).strftime("%Y-%m-%d %H:%M:%S")
        stale_data = json.dumps([["Stale Label", "http://www.wikidata.org/entity/Q999"]])
        with sqlite3.connect(str(cache_db)) as conn:
            conn.execute(
                "INSERT INTO wikidata_term_cache (entity, data, timestamp) VALUES (?, ?, ?)",
                ("Python", stale_data, expired_ts),
            )
            conn.commit()
        with patch.object(target, "SPARQLWrapper") as mock_sw:
            mock_sw.return_value.query.return_value.convert.return_value = _sparql_response(
                [_binding("Python", "http://www.wikidata.org/entity/Q28865")]
            )
            result = target.query_wikidata("Python")
        assert result == [("Python", "http://www.wikidata.org/entity/Q28865")]
        mock_sw.assert_called_once()
        with sqlite3.connect(str(cache_db)) as conn:
            row = conn.execute(
                "SELECT data, timestamp FROM wikidata_term_cache WHERE entity = ?", ("Python",)
            ).fetchone()
        assert row is not None
        cached = json.loads(row[0])
        assert cached == [["Python", "http://www.wikidata.org/entity/Q28865"]]
        assert row[1] > expired_ts  # newer timestamp

    def test_breaker_open_returns_empty_without_cache_write(self, cache_db):
        target._breaker_record_failure()  # trip to open
        assert target._breaker_check_state() == "open"
        with patch.object(target, "SPARQLWrapper") as mock_sw:
            result = target.query_wikidata("anything")
        assert result == []
        mock_sw.assert_not_called()
        with sqlite3.connect(str(cache_db)) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM wikidata_term_cache WHERE entity = ?", ("anything",)
            ).fetchone()
        assert count[0] == 0
