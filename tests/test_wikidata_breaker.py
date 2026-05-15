"""
Unit tests for the WikiData circuit breaker in text_bertopic_analysis.

Covers:
- State transitions (closed → open → half_open → closed/open)
- Retry-After header parsing and use as cooldown
- HTTP error classification (403/429 trip; 5xx don't; other 4xx raise)
- Backoff sleep skip when breaker just opened
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

import pytest

# --- Stub heavy module-level imports BEFORE loading target module ---------
# text_bertopic_analysis loads spaCy / NLTK / sklearn at import time. Those
# packages aren't required for testing the breaker, so stub them out.

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

# Only stub the leaf module (transitively imports the heavy `bertopic` package).
# Letting Python load the real coyote.* parent packages ensures the
# `from coyote.analysis.nlp import text_bertopic_analysis` import below
# returns the real module rather than a MagicMock attribute.
_bertopic_stub = MagicMock()
_bertopic_stub.analyze_topics = MagicMock(return_value=([], []))
sys.modules.setdefault("coyote.analysis.nlp.bertopic_analysis", _bertopic_stub)

from coyote.analysis.nlp import text_bertopic_analysis as target  # noqa: E402


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
    """Reset breaker state before every test."""
    target._breaker_reset_for_tests()
    yield
    target._breaker_reset_for_tests()


# ---------------------------------------------------------------------------
# _parse_retry_after
# ---------------------------------------------------------------------------

class TestParseRetryAfter:
    def test_integer_seconds(self):
        assert target._parse_retry_after(_MockHeaders("120")) == 120

    def test_missing_header(self):
        assert target._parse_retry_after(_MockHeaders(None)) is None

    def test_none_headers_object(self):
        assert target._parse_retry_after(None) is None

    def test_garbage_value(self):
        assert target._parse_retry_after(_MockHeaders("not-a-number")) is None

    def test_http_date_form_returns_none(self):
        # HTTP-date form is too rare to parse for MVP; should fall back to None.
        assert target._parse_retry_after(_MockHeaders("Wed, 21 Oct 2015 07:28:00 GMT")) is None

    def test_negative_value_returns_none(self):
        assert target._parse_retry_after(_MockHeaders("-5")) is None


# ---------------------------------------------------------------------------
# Breaker state machine (direct manipulation of internal helpers)
# ---------------------------------------------------------------------------

class TestBreakerStateMachine:
    def test_starts_closed(self):
        assert target._breaker_check_state() == "closed"

    def test_closed_to_open_after_one_failure(self):
        # Threshold defaults to 1; one failure trips immediately.
        target._breaker_record_failure(retry_after_seconds=None)
        assert target._breaker_check_state() == "open"

    def test_open_to_half_open_after_cooldown(self):
        from datetime import datetime, timedelta
        target._breaker_record_failure(retry_after_seconds=None)
        # Backdate the cooldown expiry to simulate the wait.
        with target._BREAKER_LOCK:
            target._BREAKER_OPEN_UNTIL = datetime.utcnow() - timedelta(seconds=1)
        assert target._breaker_check_state() == "half_open"

    def test_half_open_to_open_on_probe_failure(self):
        from datetime import datetime, timedelta
        target._breaker_record_failure(retry_after_seconds=None)
        with target._BREAKER_LOCK:
            target._BREAKER_OPEN_UNTIL = datetime.utcnow() - timedelta(seconds=1)
        assert target._breaker_check_state() == "half_open"
        target._breaker_record_failure(retry_after_seconds=None)
        assert target._breaker_check_state() == "open"

    def test_half_open_to_closed_on_probe_success(self):
        from datetime import datetime, timedelta
        target._breaker_record_failure(retry_after_seconds=None)
        with target._BREAKER_LOCK:
            target._BREAKER_OPEN_UNTIL = datetime.utcnow() - timedelta(seconds=1)
        assert target._breaker_check_state() == "half_open"
        target._breaker_record_success()
        assert target._breaker_check_state() == "closed"

    def test_retry_after_used_as_cooldown(self):
        from datetime import datetime
        before = datetime.utcnow()
        target._breaker_record_failure(retry_after_seconds=600)
        with target._BREAKER_LOCK:
            until = target._BREAKER_OPEN_UNTIL
        delta = (until - before).total_seconds()
        # Allow a few seconds of test-execution slack.
        assert 595 <= delta <= 610

    def test_retry_after_capped(self):
        from datetime import datetime
        before = datetime.utcnow()
        target._breaker_record_failure(retry_after_seconds=99999)
        with target._BREAKER_LOCK:
            until = target._BREAKER_OPEN_UNTIL
        delta = (until - before).total_seconds()
        assert delta <= target._BREAKER_RETRY_AFTER_CAP + 5
        assert delta >= target._BREAKER_RETRY_AFTER_CAP - 5

    def test_default_cooldown_used_when_retry_after_missing(self):
        from datetime import datetime
        before = datetime.utcnow()
        target._breaker_record_failure(retry_after_seconds=None)
        with target._BREAKER_LOCK:
            until = target._BREAKER_OPEN_UNTIL
        delta = (until - before).total_seconds()
        assert abs(delta - target._BREAKER_COOLDOWN_SECONDS) <= 5


# ---------------------------------------------------------------------------
# query_wikidata integration with mocked SPARQLWrapper
# ---------------------------------------------------------------------------

class TestQueryWikidataIntegration:
    def test_open_state_short_circuits_without_sparql_call(self):
        target._breaker_record_failure()  # trip to open
        with patch.object(target, "SPARQLWrapper") as mock_sw:
            result = target.query_wikidata("analytics")
        assert result == []
        mock_sw.assert_not_called()

    def test_403_trips_breaker(self):
        with patch.object(target, "SPARQLWrapper") as mock_sw, \
             patch.object(target, "time") as mock_time:
            mock_sw.return_value.query.side_effect = _make_http_error(403)
            result = target.query_wikidata("analytics")
        assert result == []
        assert target._breaker_check_state() == "open"

    def test_429_trips_breaker(self):
        with patch.object(target, "SPARQLWrapper") as mock_sw, \
             patch.object(target, "time") as mock_time:
            mock_sw.return_value.query.side_effect = _make_http_error(429, retry_after="60")
            result = target.query_wikidata("analytics")
        assert result == []
        assert target._breaker_check_state() == "open"

    def test_5xx_does_not_count_toward_breaker(self):
        with patch.object(target, "SPARQLWrapper") as mock_sw, \
             patch.object(target, "time") as mock_time:
            mock_sw.return_value.query.side_effect = _StubEndPointInternalError("502 Bad Gateway")
            target.query_wikidata("analytics")
        # All retries exhausted on 5xx, but breaker remains closed.
        assert target._breaker_check_state() == "closed"

    def test_unexpected_4xx_does_not_trip_breaker(self):
        with patch.object(target, "SPARQLWrapper") as mock_sw, \
             patch.object(target, "time") as mock_time:
            mock_sw.return_value.query.side_effect = _make_http_error(400)
            result = target.query_wikidata("analytics")
        # Unexpected 4xx re-raises; outer try/except returns [] and logs error.
        assert result == []
        # But: should NOT have tripped the breaker (no record_failure called).
        assert target._breaker_check_state() == "closed"

    def test_sleep_skipped_when_breaker_just_opened(self):
        """After a 403 trips the breaker, the in-call backoff sleep should
        not fire — we short-circuit instead."""
        with patch.object(target, "SPARQLWrapper") as mock_sw, \
             patch.object(target, "time") as mock_time:
            mock_sw.return_value.query.side_effect = _make_http_error(429)
            target.query_wikidata("analytics")
        mock_time.sleep.assert_not_called()

    def test_429_uses_retry_after_for_cooldown(self):
        from datetime import datetime
        before = datetime.utcnow()
        with patch.object(target, "SPARQLWrapper") as mock_sw, \
             patch.object(target, "time") as mock_time:
            mock_sw.return_value.query.side_effect = _make_http_error(429, retry_after="120")
            target.query_wikidata("analytics")
        with target._BREAKER_LOCK:
            until = target._BREAKER_OPEN_UNTIL
        delta = (until - before).total_seconds()
        assert 115 <= delta <= 130
