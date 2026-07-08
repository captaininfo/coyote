"""
Wiring tests for the map functions: Unit 8 (context re-rank) + A1 junk filter
(Track A commit 2, Gate A1.2).

map_topics_to_wikidata / map_ner_to_wikidata now pipe every term's candidates
through filter_candidates (A1) before selection:
  - context path (webpage): Unit 8's select_best_candidate re-ranks the
    SURVIVORS (its margin guard anchors on the post-filter #1 — intended);
  - no-context path: prominence top-1 SURVIVOR, except a name-marker /
    disambiguation #1 drops the term entirely (NO_CONTEXT_DROP_CLASSES).

Behavior delta from the pre-A1 wiring, deliberate and plan-ratified: a
name-marker #1 on the no-context path used to map verbatim (bare-name junk);
it now maps to NOTHING. The Gate A1.2 regression list (dewey-shaped
surname-#1-filtered-person-#2-wins; clean-#1 terms unaffected) lives here.

These are integration-of-the-wiring tests, not the pure selection/filter
tests (test_wikidata_disambiguation.py / test_wikidata_candidate_filter.py).
We stub `requests` before import (the host lacks it; same pattern as the
other wikidata tests), then patch query_wikidata + embed_texts so no network
and no model are needed.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest


class _StubRequestException(Exception):
    pass


_requests_stub = MagicMock()
_requests_stub.RequestException = _StubRequestException
sys.modules.setdefault("requests", _requests_stub)

sys.path.insert(
    0, str(Path(__file__).parent.parent / "images" / "core" / "core_analysis")
)

from coyote.analysis import wikidata_lookup as wl  # noqa: E402
from coyote.analysis.nlp import text_ner_analysis as tna  # noqa: E402
from coyote.analysis.nlp import wikidata_disambiguation as wd  # noqa: E402


def _uri(qid):
    return f"http://www.wikidata.org/entity/{qid}"


# Dewey-shaped: prominence #1 is a name-marker item (A1-filtered), #2 is the
# contextual person. Pre-A1 these were the Unit 8 fixtures verbatim.
_NAME_FIRST = [
    ("Surname", _uri("Q1"), "family name"),
    ("Person", _uri("Q2"), "revolutionary politician"),
]
# ai-shaped: clean #1, junk in the tail (F5: the term must keep its #1).
_CLEAN_FIRST = [
    ("Concept", _uri("Q11660"), "branch of computer science"),
    ("Ai", _uri("Q5"), "given name"),
]
# French-Revolution-episode-shaped: meta junk #1, real concept #2.
_META_FIRST = [
    ("Episode", _uri("Q9"), "scholarly article published in a journal"),
    ("Concept", _uri("Q6534"), "revolution in France"),
]
CTX = [1.0, 0.0]

_DESC_VECS = {
    "family name": [0.0, 1.0],
    "revolutionary politician": [1.0, 0.0],
    "branch of computer science": [1.0, 0.0],
    "given name": [0.0, 1.0],
    "scholarly article published in a journal": [0.0, 1.0],
    "revolution in France": [1.0, 0.0],
}


def _patch_candidates(monkeypatch, candidates):
    monkeypatch.setattr(wl, "query_wikidata", lambda term: list(candidates))
    monkeypatch.setattr(tna, "query_wikidata", lambda term: list(candidates))


@pytest.fixture(autouse=True)
def _patch_embeds(monkeypatch):
    monkeypatch.setattr(
        wd, "embed_texts", lambda texts: [_DESC_VECS.get(t) for t in texts]
    )
    wd._DESC_EMBED_CACHE.clear()
    yield
    wd._DESC_EMBED_CACHE.clear()


# --- context path: Unit 8 re-rank over A1 survivors --------------------------

def test_topics_with_context_dewey_shape_person_wins(monkeypatch):
    """Gate A1.2 keystone: surname #1 filtered by A1, person #2 wins via
    context — no margin fight against the junk #1 (it's gone)."""
    _patch_candidates(monkeypatch, _NAME_FIRST)
    out = wl.map_topics_to_wikidata(["dewey"], context_embedding=CTX)
    assert out["dewey"] == {"uri": _uri("Q2"), "label": "Person"}


def test_entities_with_context_dewey_shape_person_wins(monkeypatch):
    _patch_candidates(monkeypatch, _NAME_FIRST)
    out = tna.map_ner_to_wikidata(["Dewey"], context_embedding=CTX)
    assert out["Dewey"]["uri"] == _uri("Q2")
    assert out["Dewey"]["label"] == "Person"
    assert out["Dewey"]["replacement"] == "Dewey"


def test_topics_with_context_clean_top1_unaffected(monkeypatch):
    """ai -> Q11660-shaped regression: clean #1 keeps winning; the junk tail
    candidate is filtered, not promoted."""
    _patch_candidates(monkeypatch, _CLEAN_FIRST)
    out = wl.map_topics_to_wikidata(["ai"], context_embedding=CTX)
    assert out["ai"] == {"uri": _uri("Q11660"), "label": "Concept"}


def test_topics_with_context_declines_when_anticorrelated(monkeypatch):
    # Survivor descriptions point AWAY from the context (cosine -1) so even
    # the best is below the default 0.0 floor -> declined. (Triggered via the
    # embeddings, not by patching the threshold: that default is bound at
    # import, the correct deploy-time-knob behavior.)
    _patch_candidates(monkeypatch, _NAME_FIRST)
    monkeypatch.setattr(wd, "embed_texts", lambda texts: [[-1.0, 0.0] for _ in texts])
    out = wl.map_topics_to_wikidata(["dewey"], context_embedding=CTX)
    assert "dewey" not in out  # declined -> no mapping


def test_entities_with_context_declines_when_anticorrelated(monkeypatch):
    _patch_candidates(monkeypatch, _NAME_FIRST)
    monkeypatch.setattr(wd, "embed_texts", lambda texts: [[-1.0, 0.0] for _ in texts])
    out = tna.map_ner_to_wikidata(["Dewey"], context_embedding=CTX)
    assert "Dewey" not in out


def test_with_context_all_junk_maps_nothing(monkeypatch):
    all_junk = [("x", _uri("Q13406463"), "Wikimedia list article"),
                ("y", _uri("Q8"), "family name")]
    _patch_candidates(monkeypatch, all_junk)
    assert wl.map_topics_to_wikidata(["x"], context_embedding=CTX) == {}
    assert tna.map_ner_to_wikidata(["x"], context_embedding=CTX) == {}


# --- no-context path: prominence top-1 survivor + term-drop classes ----------

def test_topics_no_context_clean_top1(monkeypatch):
    _patch_candidates(monkeypatch, _CLEAN_FIRST)
    out = wl.map_topics_to_wikidata(["ai"])  # context_embedding=None
    assert out["ai"] == {"uri": _uri("Q11660"), "label": "Concept"}


def test_entities_no_context_clean_top1(monkeypatch):
    _patch_candidates(monkeypatch, _CLEAN_FIRST)
    out = tna.map_ner_to_wikidata(["AI"])
    assert out["AI"]["uri"] == _uri("Q11660")


def test_no_context_name_marker_top1_drops_term(monkeypatch):
    """The A1 behavior delta: pre-A1 this mapped the surname item verbatim;
    now the term maps to NOTHING (blind #2 fallback would mint wrong-person
    junk on exactly the no-context paths)."""
    _patch_candidates(monkeypatch, _NAME_FIRST)
    assert "wang" not in wl.map_topics_to_wikidata(["wang"])
    assert "Wang" not in tna.map_ner_to_wikidata(["Wang"])


def test_no_context_meta_top1_falls_back_to_next_survivor(monkeypatch):
    """Class M keeps fallback: a junk #1 says nothing about the term."""
    _patch_candidates(monkeypatch, _META_FIRST)
    out = wl.map_topics_to_wikidata(["the french revolution"])
    assert out["the french revolution"] == {"uri": _uri("Q6534"), "label": "Concept"}
    out = tna.map_ner_to_wikidata(["The French Revolution"])
    assert out["The French Revolution"]["uri"] == _uri("Q6534")


def test_no_context_all_junk_maps_nothing(monkeypatch):
    all_junk = [("x", _uri("Q13406463"), "Wikimedia list article")]
    _patch_candidates(monkeypatch, all_junk)
    assert wl.map_topics_to_wikidata(["x"]) == {}
    assert tna.map_ner_to_wikidata(["x"]) == {}


def test_no_candidates_maps_nothing(monkeypatch):
    _patch_candidates(monkeypatch, [])
    assert wl.map_topics_to_wikidata(["x"], context_embedding=CTX) == {}
    assert tna.map_ner_to_wikidata(["x"], context_embedding=CTX) == {}
    assert wl.map_topics_to_wikidata(["x"]) == {}
    assert tna.map_ner_to_wikidata(["x"]) == {}
