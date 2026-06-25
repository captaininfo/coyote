"""
Wiring tests for Unit 8 (commit 4): map_topics_to_wikidata /
map_ner_to_wikidata route through select_best_candidate ONLY when a
context_embedding is supplied (webpage path), and otherwise preserve the
prior prominence-top-1 behavior verbatim.

These are integration-of-the-wiring tests, not the pure selection tests
(those live in test_wikidata_disambiguation.py). We stub `requests` before
import (the host lacks it; same pattern as the other wikidata tests), then
patch query_wikidata + embed_texts so no network and no model are needed.
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


# Two candidates: prominence #0 is the WRONG sense, #1 is the contextual one.
_CANDIDATES = [
    ("Surname", _uri("Q1"), "family name"),
    ("Person", _uri("Q2"), "revolutionary politician"),
]
CTX = [1.0, 0.0]


@pytest.fixture(autouse=True)
def _patch_lookup(monkeypatch):
    # Every term resolves to the same 2-candidate list.
    monkeypatch.setattr(wl, "query_wikidata", lambda term: list(_CANDIDATES))
    monkeypatch.setattr(tna, "query_wikidata", lambda term: list(_CANDIDATES))
    # Description embeddings: the contextual sense aligns with CTX, the surname
    # is orthogonal. embed_texts is called by the wrapper for cache-misses.
    vecs = {"family name": [0.0, 1.0], "revolutionary politician": [1.0, 0.0]}
    monkeypatch.setattr(wd, "embed_texts", lambda texts: [vecs.get(t) for t in texts])
    wd._DESC_EMBED_CACHE.clear()
    yield
    wd._DESC_EMBED_CACHE.clear()


# --- topics -----------------------------------------------------------------

def test_topics_no_context_uses_prominence_top1():
    out = wl.map_topics_to_wikidata(["robespierre"])  # context_embedding=None
    assert out["robespierre"] == {"uri": _uri("Q1"), "label": "Surname"}


def test_topics_with_context_rerank_picks_contextual_sense():
    out = wl.map_topics_to_wikidata(["robespierre"], context_embedding=CTX)
    assert out["robespierre"] == {"uri": _uri("Q2"), "label": "Person"}


def test_topics_with_context_declines_when_anticorrelated(monkeypatch):
    # Both candidate descriptions point AWAY from the context (cosine -1) so
    # even the best is below the default 0.0 floor -> declined. (We trigger the
    # decline via the embeddings, not by patching the threshold: that default
    # is bound at import, the correct deploy-time-knob behavior.)
    monkeypatch.setattr(wd, "embed_texts", lambda texts: [[-1.0, 0.0] for _ in texts])
    out = wl.map_topics_to_wikidata(["robespierre"], context_embedding=CTX)
    assert "robespierre" not in out  # declined -> no mapping


# --- entities ---------------------------------------------------------------

def test_entities_no_context_uses_prominence_top1():
    out = tna.map_ner_to_wikidata(["Robespierre"])  # context_embedding=None
    assert out["Robespierre"]["uri"] == _uri("Q1")
    assert out["Robespierre"]["label"] == "Surname"
    assert out["Robespierre"]["replacement"] == "Robespierre"


def test_entities_with_context_rerank_picks_contextual_sense():
    out = tna.map_ner_to_wikidata(["Robespierre"], context_embedding=CTX)
    assert out["Robespierre"]["uri"] == _uri("Q2")
    assert out["Robespierre"]["label"] == "Person"


def test_entities_with_context_declines_when_anticorrelated(monkeypatch):
    monkeypatch.setattr(wd, "embed_texts", lambda texts: [[-1.0, 0.0] for _ in texts])
    out = tna.map_ner_to_wikidata(["Robespierre"], context_embedding=CTX)
    assert "Robespierre" not in out


def test_no_candidates_maps_nothing(monkeypatch):
    monkeypatch.setattr(wl, "query_wikidata", lambda term: [])
    monkeypatch.setattr(tna, "query_wikidata", lambda term: [])
    assert wl.map_topics_to_wikidata(["x"], context_embedding=CTX) == {}
    assert tna.map_ner_to_wikidata(["x"], context_embedding=CTX) == {}
