"""
Unit tests for coyote.analysis.nlp.token_filter (Unit 6 of the 0.5
refactor). Pure host tests — string predicates + Counter only, no
spaCy/DB/network. The stopwords import is hardened to be network-free, and
the all-stopword cases use CUSTOM_STOPWORDS members ("more"/"read") so they
pass even when the nltk corpus is absent (degraded mode).
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(
    0, str(Path(__file__).parent.parent / "images" / "core" / "core_analysis")
)

from coyote.analysis.nlp import token_filter as tf  # noqa: E402


# --- is_quality_token: DROP cases (Gate 6.1 / 6.3) --------------------------

@pytest.mark.parametrize("junk", [
    "", "   ", "x", "A",                    # empty / single-char
    "978", "1789", "12.5", "1,000",        # pure-numeric / bare years
    "pp.", "ed.", "vol.",                  # trailing-dot abbreviations
    "cite web", "cite news", "Cite Book",  # citation fragments (case-insens)
    "pre", "anti", "QUASI",                # lone bound morphemes
    "more", "read more",                   # stopword-only (CUSTOM_STOPWORDS)
    "!!!", "$$$", "---",                   # non-alpha-dominant
])
def test_is_quality_token_drops_junk(junk):
    assert tf.is_quality_token(junk) is False


# --- is_quality_token: KEEP cases -------------------------------------------

@pytest.mark.parametrize("good", [
    "AI", "ML", "OER",                     # 2-3 char acronyms (the <3 fix)
    "french revolution", "logical positivism",
    "U.S.",                                # mid-dot, 0.5 alpha -> kept
    "covid-19",                            # has letters, not pure-numeric
    "preprint",                            # not a lone morpheme
])
def test_is_quality_token_keeps_legit(good):
    assert tf.is_quality_token(good) is True


def test_none_is_dropped():
    assert tf.is_quality_token(None) is False


# --- filter_topics ----------------------------------------------------------

def test_filter_topics_drops_junk_keeps_shape_and_order():
    topics = [("french revolution", 0.8), ("978", 0.1), ("pp.", 0.2),
              ("logical positivism", 0.5)]
    out = tf.filter_topics(topics)
    assert out == [("french revolution", 0.8), ("logical positivism", 0.5)]


# --- filter_entities --------------------------------------------------------

def test_filter_entities_drops_numeric_date_labels():
    ents = [("French Revolution", "EVENT"), ("1789", "DATE"),
            ("five", "CARDINAL"), ("Robespierre", "PERSON")]
    out = tf.filter_entities(ents)
    assert out == [("French Revolution", "EVENT"), ("Robespierre", "PERSON")]


def test_filter_entities_drops_junk_text_even_with_good_label():
    ents = [("pp.", "PERSON"), ("Napoleon", "PERSON")]
    assert tf.filter_entities(ents) == [("Napoleon", "PERSON")]


# --- select_mapping_entities: floor -----------------------------------------

def _ents(*pairs):
    """Expand (text, count) pairs into one (text, "PERSON") tuple per mention."""
    out = []
    for text, count in pairs:
        out.extend([(text, "PERSON")] * count)
    return out


def test_floor_keeps_only_repeated():
    ents = _ents(("Robespierre", 3), ("Danton", 2), ("Marat", 1))
    got = tf.select_mapping_entities(ents, floor=2)
    assert set(got) == {"Robespierre", "Danton"}


def test_floor_default_is_two():
    assert tf.ENTITY_MAP_MENTION_FLOOR == 2


def test_floor_case_folded_counting():
    # "AI" x1 + "ai" x1 == 2 mentions of one case-folded entity
    ents = [("AI", "ORG"), ("ai", "ORG")]
    got = tf.select_mapping_entities(ents, floor=2)
    # surface = most frequent (1-1 tie) -> alpha: "AI" < "ai"
    assert got == ["AI"]


def test_returns_original_surface_form_not_casefolded():
    ents = _ents(("French Revolution", 3))
    assert tf.select_mapping_entities(ents, floor=2) == ["French Revolution"]


def test_surface_form_is_most_frequent_casing():
    # "NASA" x3, "nasa" x1 -> representative is the dominant casing
    ents = [("nasa", "ORG")] + [("NASA", "ORG")] * 3
    assert tf.select_mapping_entities(ents, floor=2) == ["NASA"]


def test_empty_input():
    assert tf.select_mapping_entities([], floor=2) == []


def test_result_is_distinct():
    ents = _ents(("Paris", 5))
    assert tf.select_mapping_entities(ents, floor=2) == ["Paris"]


def test_deterministic_order_count_desc_then_alpha():
    ents = _ents(("Zeta", 2), ("Alpha", 2), ("Beta", 5))
    got = tf.select_mapping_entities(ents, floor=2)
    assert got == ["Beta", "Alpha", "Zeta"]  # 5 first, then 2-ties alpha


# --- select_mapping_entities: cap (wired but disabled) ----------------------

def test_cap_none_is_passthrough():
    ents = _ents(("A", 4), ("B", 3), ("C", 2))
    got = tf.select_mapping_entities(ents, floor=2, cap=None)
    assert set(got) == {"A", "B", "C"}


def test_cap_limits_to_top_k_by_count():
    ents = _ents(("A", 4), ("B", 3), ("C", 2))
    got = tf.select_mapping_entities(ents, floor=2, cap=2)
    assert got == ["A", "B"]  # highest counts kept


def test_cap_deterministic_tie_break_at_boundary():
    # three entities tie at count 2; cap=2 keeps the casefold-alpha-first two
    ents = _ents(("Gamma", 2), ("Alpha", 2), ("Beta", 2))
    got = tf.select_mapping_entities(ents, floor=2, cap=2)
    assert got == ["Alpha", "Beta"]


def test_cap_default_disabled():
    assert tf.ENTITY_MAP_CAP is None


# --- _env_optional_int ------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    (None, None), ("", None), ("   ", None),
    ("abc", None), ("0", None), ("-1", None), ("-5", None),
    ("3", 3), ("100", 100), (" 7 ", 7),
])
def test_env_optional_int(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv("X_TEST_CAP", raising=False)
    else:
        monkeypatch.setenv("X_TEST_CAP", raw)
    assert tf._env_optional_int("X_TEST_CAP", None) == expected
