"""
Unit tests for the WikiData circuit breaker in wikidata_lookup.

Covers:
- State transitions (closed → open → half_open → closed/open)
- Retry-After header parsing and use as cooldown
- HTTP status classification over the Action API transport (403/429 trip;
  5xx incl. maxlag-503 don't; HTTP-200 in-band error doesn't)
- Backoff sleep skip when breaker just opened
- maxlag-503 Retry-After honored for the inter-retry sleep
- Inter-call pacing (_pace) timing + reset

Transport note (Unit 7): wikidata_lookup now imports `requests` (not
SPARQLWrapper). The host has neither installed; we stub `requests` at module
level with a REAL RequestException class (it appears in an `except` clause, so
a MagicMock attribute would not be catchable). The breaker state machine and
_parse_retry_after are transport-agnostic and exercise the internal helpers
directly — unchanged from the SPARQL era.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# --- Stub `requests` BEFORE loading the target module --------------------
# wikidata_lookup imports it at module level. RequestException must be a real
# exception class because query_wikidata catches it.

class _StubRequestException(Exception):
    """Stand-in for requests.RequestException (real class, catchable)."""

_requests_stub = MagicMock()
_requests_stub.RequestException = _StubRequestException
sys.modules.setdefault("requests", _requests_stub)

sys.path.insert(
    0, str(Path(__file__).parent.parent / "images" / "core" / "core_analysis")
)

from coyote.analysis import wikidata_lookup as target  # noqa: E402


class _MockHeaders:
    """Minimal headers stand-in: only .get('Retry-After') is exercised.
    Mirrors `requests.Response.headers.get(...)`."""
    def __init__(self, retry_after=None):
        self._retry_after = retry_after

    def get(self, key, default=None):
        if key == "Retry-After":
            return self._retry_after
        return default


def _mock_response(status=200, json_body=None, retry_after=None):
    """Stand-in for a requests.Response: status_code, headers.get, json()."""
    resp = MagicMock()
    resp.status_code = status
    resp.headers = _MockHeaders(retry_after=retry_after)
    resp.json.return_value = json_body if json_body is not None else {"search": []}
    return resp


@pytest.fixture(autouse=True)
def _reset_breaker():
    """Reset breaker + pacing state before and after every test."""
    target._breaker_reset_for_tests()
    yield
    target._breaker_reset_for_tests()


# ---------------------------------------------------------------------------
# _parse_retry_after  (transport-agnostic — unchanged)
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
# Breaker state machine (direct manipulation of internal helpers — unchanged)
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
# query_wikidata integration over the Action API (mocked requests transport)
# ---------------------------------------------------------------------------

class TestQueryWikidataIntegration:
    @pytest.fixture(autouse=True)
    def _no_pacing(self, monkeypatch):
        # Disable inter-call pacing so these logic tests don't sleep the 0.6s
        # default; pacing itself is covered in TestPacing.
        monkeypatch.setattr(target, "WIKIDATA_ACTION_MIN_INTERVAL", 0)

    def test_open_state_short_circuits_without_api_call(self):
        target._breaker_record_failure()  # trip to open
        with patch.object(target.requests, "get") as mock_get:
            result = target.query_wikidata("analytics")
        assert result == []
        mock_get.assert_not_called()

    def test_403_trips_breaker(self):
        with patch.object(target.requests, "get") as mock_get, \
             patch.object(target, "time") as mock_time:
            mock_get.return_value = _mock_response(403)
            result = target.query_wikidata("analytics")
        assert result == []
        assert target._breaker_check_state() == "open"

    def test_429_trips_breaker(self):
        with patch.object(target.requests, "get") as mock_get, \
             patch.object(target, "time") as mock_time:
            mock_get.return_value = _mock_response(429, retry_after="60")
            result = target.query_wikidata("analytics")
        assert result == []
        assert target._breaker_check_state() == "open"

    def test_5xx_does_not_count_toward_breaker(self):
        with patch.object(target.requests, "get") as mock_get, \
             patch.object(target, "time") as mock_time:
            mock_get.return_value = _mock_response(503)
            result = target.query_wikidata("analytics")
        # All retries exhausted on 5xx, but breaker remains closed; returns [].
        assert result == []
        assert target._breaker_check_state() == "closed"

    def test_maxlag_503_honors_retry_after_for_sleep(self):
        # 503 + Retry-After: the inter-retry sleep should be the capped
        # Retry-After value, not the random backoff.
        with patch.object(target.requests, "get") as mock_get, \
             patch.object(target, "time") as mock_time:
            mock_get.return_value = _mock_response(503, retry_after="7")
            target.query_wikidata("analytics")
        # Every inter-retry sleep used the honored value (7s).
        slept = [c.args[0] for c in mock_time.sleep.call_args_list]
        assert slept, "expected at least one inter-retry sleep on 503"
        assert all(s == 7 for s in slept)

    def test_in_band_error_at_200_does_not_count_toward_breaker(self):
        # HTTP 200 carrying a maxlag error body is transient, not a breaker trip.
        with patch.object(target.requests, "get") as mock_get, \
             patch.object(target, "time") as mock_time:
            mock_get.return_value = _mock_response(
                200, json_body={"error": {"code": "maxlag", "info": "lag"}}
            )
            result = target.query_wikidata("analytics")
        assert result == []
        assert target._breaker_check_state() == "closed"

    def test_network_error_does_not_count_toward_breaker(self):
        with patch.object(target.requests, "get") as mock_get, \
             patch.object(target, "time") as mock_time:
            mock_get.side_effect = target.requests.RequestException("conn reset")
            result = target.query_wikidata("analytics")
        assert result == []
        assert target._breaker_check_state() == "closed"

    def test_success_returns_triples_and_closes(self):
        body = {"search": [
            {"label": "artificial intelligence",
             "concepturi": "http://www.wikidata.org/entity/Q11660",
             "description": "intelligence demonstrated by machines"},
        ]}
        with patch.object(target.requests, "get") as mock_get:
            mock_get.return_value = _mock_response(200, json_body=body)
            result = target.query_wikidata("ai")
        assert result == [(
            "artificial intelligence",
            "http://www.wikidata.org/entity/Q11660",
            "intelligence demonstrated by machines",
        )]
        assert target._breaker_check_state() == "closed"

    def test_sleep_skipped_when_breaker_just_opened(self):
        """After a 429 trips the breaker, the in-call backoff sleep should
        not fire — we short-circuit instead."""
        with patch.object(target.requests, "get") as mock_get, \
             patch.object(target, "time") as mock_time:
            mock_get.return_value = _mock_response(429)
            target.query_wikidata("analytics")
        mock_time.sleep.assert_not_called()

    def test_429_uses_retry_after_for_cooldown(self):
        from datetime import datetime
        before = datetime.utcnow()
        with patch.object(target.requests, "get") as mock_get, \
             patch.object(target, "time") as mock_time:
            mock_get.return_value = _mock_response(429, retry_after="120")
            target.query_wikidata("analytics")
        with target._BREAKER_LOCK:
            until = target._BREAKER_OPEN_UNTIL
        delta = (until - before).total_seconds()
        assert 115 <= delta <= 130


# ---------------------------------------------------------------------------
# Inter-call pacing (_pace) — Unit 7
# ---------------------------------------------------------------------------

class TestPacing:
    def test_disabled_interval_is_noop(self, monkeypatch):
        monkeypatch.setattr(target, "WIKIDATA_ACTION_MIN_INTERVAL", 0)
        with patch.object(target, "time") as mock_time:
            target._pace()
        mock_time.sleep.assert_not_called()

    def test_first_call_does_not_sleep(self, monkeypatch):
        # _last_call_monotonic starts at 0.0 (reset fixture); monotonic() is a
        # large value, so the computed wait is negative → no sleep.
        monkeypatch.setattr(target, "WIKIDATA_ACTION_MIN_INTERVAL", 0.6)
        with patch.object(target, "time") as mock_time:
            mock_time.monotonic.return_value = 1000.0
            target._pace()
        mock_time.sleep.assert_not_called()

    def test_rapid_second_call_sleeps_remaining_interval(self, monkeypatch):
        monkeypatch.setattr(target, "WIKIDATA_ACTION_MIN_INTERVAL", 0.6)
        with patch.object(target, "time") as mock_time:
            # First call lands at t=1000.0 (no sleep, sets last-call clock).
            mock_time.monotonic.return_value = 1000.0
            target._pace()
            # Second call 0.2s later → must sleep the remaining ~0.4s.
            mock_time.monotonic.return_value = 1000.2
            target._pace()
        slept = [c.args[0] for c in mock_time.sleep.call_args_list]
        assert len(slept) == 1
        assert abs(slept[0] - 0.4) < 1e-9

    def test_call_after_interval_elapsed_does_not_sleep(self, monkeypatch):
        monkeypatch.setattr(target, "WIKIDATA_ACTION_MIN_INTERVAL", 0.6)
        with patch.object(target, "time") as mock_time:
            mock_time.monotonic.return_value = 1000.0
            target._pace()
            mock_time.monotonic.return_value = 1002.0  # 2s later, > interval
            target._pace()
        mock_time.sleep.assert_not_called()

    def test_reset_clears_pacing_clock(self, monkeypatch):
        monkeypatch.setattr(target, "WIKIDATA_ACTION_MIN_INTERVAL", 0.6)
        with patch.object(target, "time") as mock_time:
            mock_time.monotonic.return_value = 1000.0
            target._pace()
        target._breaker_reset_for_tests()
        assert target._last_call_monotonic == 0.0
