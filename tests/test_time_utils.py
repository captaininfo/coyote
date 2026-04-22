"""Unit tests for shared/time_utils.py.

Canonical location: shared/time_utils.py at repo root. Agent copy at
images/agent/app/shared/time_utils.py is kept in sync by ``make sync-shared``.
Core does not consume time parsing, so it is not synced to the core copy.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from shared.time_utils import days_from_text, days_from_text_maybe


class TestDaysFromTextMaybe:
    """Sentinel form: returns None when no time signal present."""

    def test_no_signal_returns_none(self):
        assert days_from_text_maybe("what have I read about deep learning") is None

    def test_empty_string_returns_none(self):
        assert days_from_text_maybe("") is None

    def test_none_input_returns_none(self):
        assert days_from_text_maybe(None) is None

    def test_numeric_past_days(self):
        assert days_from_text_maybe("what did I read in the past 3 days") == 3

    def test_numeric_last_weeks(self):
        assert days_from_text_maybe("show me the last 2 weeks") == 14

    def test_spelled_number(self):
        assert days_from_text_maybe("past three weeks of reading") == 21

    def test_yesterday(self):
        assert days_from_text_maybe("what did I see yesterday") == 2

    def test_today(self):
        assert days_from_text_maybe("today's browsing") == 1

    def test_this_year(self):
        assert days_from_text_maybe("what did I read this year") == 365


class TestDaysFromTextDefault:
    """Wrapper form: returns default (90) when no signal present."""

    def test_no_signal_returns_default(self):
        assert days_from_text("what have I read about deep learning") == 90

    def test_custom_default(self):
        assert days_from_text("no time signal here", default=30) == 30

    def test_signal_overrides_default(self):
        assert days_from_text("in the past 3 days", default=999) == 3

    def test_yesterday_not_default(self):
        assert days_from_text("what about yesterday") == 2
