"""
Unit tests for coyote.analysis.nlp.entity_scoring (Unit 4 of the 0.5
refactor). Pure host tests — the scoring function takes integer counts
directly, so there is no spaCy/DB/network dependency and the results are
fully deterministic (unlike a raw-text NER fixture, whose mention counts
vary with the spaCy model version and context).
"""
import importlib
import math
import sys
from pathlib import Path

import pytest

sys.path.insert(
    0, str(Path(__file__).parent.parent / "images" / "core" / "core_analysis")
)

from coyote.analysis.nlp import entity_scoring as es  # noqa: E402

score = es.mention_frequency_score


# --- log (default) ----------------------------------------------------------

def test_log_exact_values():
    assert score(1, "log") == pytest.approx(math.log1p(1))   # ~0.6931
    assert score(2, "log") == pytest.approx(math.log1p(2))   # ~1.0986
    assert score(5, "log") == pytest.approx(math.log1p(5))   # ~1.7918
    assert score(10, "log") == pytest.approx(math.log1p(10))  # ~2.3979


def test_log_strictly_monotonic_and_positive():
    vals = [score(c, "log") for c in range(1, 12)]
    assert all(v > 0 for v in vals)
    assert all(b > a for a, b in zip(vals, vals[1:]))


def test_log_is_default_formula():
    # Unrecognized / omitted formula falls back to log.
    assert score(5) == pytest.approx(score(5, "log"))
    assert score(5, "nonsense") == pytest.approx(score(5, "log"))


# --- freq_normalized --------------------------------------------------------

def test_freq_normalized_shares_sum_to_one():
    # A page whose entity multiset is {A:5, B:3, C:2}; total = 10.
    counts = {"A": 5, "B": 3, "C": 2}
    total = sum(counts.values())
    shares = {e: score(c, "freq_normalized", total=total) for e, c in counts.items()}
    assert shares["A"] == pytest.approx(0.5)
    assert shares["B"] == pytest.approx(0.3)
    assert shares["C"] == pytest.approx(0.2)
    assert sum(shares.values()) == pytest.approx(1.0)


def test_freq_normalized_monotonic_in_count():
    total = 20
    assert score(1, "freq_normalized", total=total) < score(5, "freq_normalized", total=total)


def test_freq_normalized_zero_total_guard():
    assert score(3, "freq_normalized", total=0) == 0.0
    assert score(3, "freq_normalized", total=None) == 0.0


# --- saturated --------------------------------------------------------------

def test_saturated_exact_values_default_k():
    assert score(1, "saturated") == pytest.approx(0.5)          # 1/(1+1)
    assert score(2, "saturated") == pytest.approx(2 / 3)
    assert score(5, "saturated") == pytest.approx(5 / 6)
    assert score(10, "saturated") == pytest.approx(10 / 11)


def test_saturated_bounded_below_one_and_monotonic():
    vals = [score(c, "saturated") for c in range(1, 50)]
    assert all(0 < v < 1 for v in vals)
    assert all(b > a for a, b in zip(vals, vals[1:]))


# --- env-var selection ------------------------------------------------------

def test_env_valid_selection(monkeypatch):
    monkeypatch.setenv("NER_SCORE_FORMULA", "saturated")
    importlib.reload(es)
    assert es.NER_SCORE_FORMULA == "saturated"


def test_env_invalid_falls_back_to_log(monkeypatch):
    monkeypatch.setenv("NER_SCORE_FORMULA", "bogus")
    importlib.reload(es)
    assert es.NER_SCORE_FORMULA == "log"


def test_env_default_is_log(monkeypatch):
    monkeypatch.delenv("NER_SCORE_FORMULA", raising=False)
    importlib.reload(es)
    assert es.NER_SCORE_FORMULA == "log"


@pytest.fixture(autouse=True)
def _restore_module():
    """Reload after env-mutating tests so module state doesn't leak."""
    yield
    importlib.reload(es)
