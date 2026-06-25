"""
Unit tests for coyote.analysis.nlp.wikidata_disambiguation (Unit 8 of the
0.5 refactor).

The bulk are PURE tests of score_candidates on fixed vectors — no model, no
network (the Unit 4/6 testability lesson). A small set exercises the
select_best_candidate wrapper with embed_texts monkeypatched, so the cache /
batching / fallback logic is covered without loading the embedder. The
real-embedding behavior is the Gate 8.1 integration test, not here.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(
    0, str(Path(__file__).parent.parent / "images" / "core" / "core_analysis")
)

from coyote.analysis.nlp import wikidata_disambiguation as wd  # noqa: E402


# NOTE: always pass `threshold` and `margin` explicitly in these tests. The
# module-level defaults (WIKIDATA_DISAMBIG_THRESHOLD / _MARGIN) are read from
# env vars at import and are NOT reset between tests; relying on them would make
# a test's outcome depend on the ambient environment.
CTX = [1.0, 0.0]  # all cosines below are taken against this context


# --- score_candidates: margin guard -----------------------------------------

def test_margin_override_picks_contextual_winner():
    # prominence #0 cos 0.0, candidate #1 cos 1.0 -> override (1.0 - 0.0 >= margin)
    assert wd.score_candidates(CTX, [[0.0, 1.0], [1.0, 0.0]], 0.0, 0.05) == 1


def test_margin_protects_prominence_on_marginal_flip():
    # prom#0 = [3,1] cos ~0.9487, cand#1 cos 1.0 -> diff ~0.0513.
    cands = [[3.0, 1.0], [1.0, 0.0]]
    # margin 0.1: 0.0513 < 0.1 -> keep prominence #0
    assert wd.score_candidates(CTX, cands, 0.0, 0.1) == 0
    # margin 0.05: 0.0513 >= 0.05 -> override to #1 (same vectors, only margin changes)
    assert wd.score_candidates(CTX, cands, 0.0, 0.05) == 1


def test_margin_boundary_is_inclusive():
    # Exact equality: prom#0 cos 0.0, cand#1 cos 1.0 -> diff == 1.0. With
    # margin == 1.0 the guard is `diff >= margin` (code: `diff < margin` keeps
    # prominence), so equality OVERRIDES. Cosines 0.0/1.0 compute exactly here,
    # so the boundary is hit precisely (no float fuzz).
    assert wd.score_candidates(CTX, [[0.0, 1.0], [1.0, 0.0]], 0.0, 1.0) == 1


def test_margin_zero_is_pure_argmax():
    # margin 0 -> any strictly-better candidate overrides
    assert wd.score_candidates(CTX, [[0.9, 0.4359], [1.0, 0.0]], 0.0, 0.0) == 1


# --- score_candidates: threshold gate ---------------------------------------

def test_threshold_drops_when_best_below_floor():
    # only candidate cos 0.6; threshold 0.7 -> None
    assert wd.score_candidates(CTX, [[0.6, 0.8]], 0.7, 0.05) is None


def test_threshold_inclusive_lower_bound():
    # cos exactly 0.6, threshold 0.6 -> maps (>=)
    assert wd.score_candidates(CTX, [[0.6, 0.8]], 0.6, 0.05) == 0


def test_zero_threshold_drops_anticorrelated_winner():
    # cos -1.0, default-style threshold 0.0 -> dropped (near-lossless semantics)
    assert wd.score_candidates(CTX, [[-1.0, 0.0]], 0.0, 0.05) is None


def test_negative_threshold_is_truly_lossless():
    # threshold -1.0 admits the anti-correlated winner
    assert wd.score_candidates(CTX, [[-1.0, 0.0]], -1.0, 0.05) == 0


# --- score_candidates: determinism ------------------------------------------

def test_exact_tie_resolves_to_more_prominent_index():
    # two candidates both cos 1.0 (one scaled) -> lower index wins
    assert wd.score_candidates(CTX, [[1.0, 0.0], [2.0, 0.0]], 0.0, 0.05) == 0


def test_tie_among_non_prominence_takes_lower_index():
    # prom#0 cos ~0.707; #1 and #2 both cos 1.0 -> override to the lower (#1)
    assert wd.score_candidates(CTX, [[1.0, 1.0], [1.0, 0.0], [1.0, 0.0]], 0.0, 0.05) == 1


# --- score_candidates: None embeddings & true-cosine ------------------------

def test_none_prominence_falls_back_to_best_scored():
    # prom#0 unscoreable (None); best scored is #1
    assert wd.score_candidates(CTX, [None, [1.0, 0.0], [0.0, 1.0]], 0.0, 0.05) == 1


def test_all_none_embeddings_returns_none():
    assert wd.score_candidates(CTX, [None, None], 0.0, 0.05) is None


def test_true_cosine_ignores_magnitude_on_both_sides():
    # unnormalized context AND candidate -> still cosine 1.0 (proves we divide
    # by both norms, not a raw dot product). Override a cos-0 prominence.
    assert wd.score_candidates([2.0, 0.0], [[0.0, 3.0], [5.0, 0.0]], 0.0, 0.05) == 1


def test_empty_inputs_return_none():
    assert wd.score_candidates(CTX, [], 0.0, 0.05) is None
    assert wd.score_candidates([], [[1.0, 0.0]], 0.0, 0.05) is None


# --- select_best_candidate wrapper (embed_texts monkeypatched) --------------

def _uri(qid):
    return f"http://www.wikidata.org/entity/{qid}"


@pytest.fixture(autouse=True)
def _clear_cache():
    wd._DESC_EMBED_CACHE.clear()
    yield
    wd._DESC_EMBED_CACHE.clear()


def _fake_embed(desc_to_vec, counter):
    def _fn(texts):
        counter["calls"] += 1
        counter["n_texts"] += len(texts)
        return [desc_to_vec.get(t) for t in texts]
    return _fn


def test_wrapper_returns_winning_label_and_uri(monkeypatch):
    counter = {"calls": 0, "n_texts": 0}
    monkeypatch.setattr(
        wd, "embed_texts",
        _fake_embed({"surname": [0.0, 1.0], "person": [1.0, 0.0]}, counter),
    )
    candidates = [
        ("Surname", _uri("Q1"), "surname"),
        ("Person", _uri("Q2"), "person"),
    ]
    out = wd.select_best_candidate(CTX, candidates, threshold=0.0, margin=0.05)
    assert out == ("Person", _uri("Q2"))


def test_wrapper_uri_cache_avoids_reembedding(monkeypatch):
    counter = {"calls": 0, "n_texts": 0}
    monkeypatch.setattr(
        wd, "embed_texts",
        _fake_embed({"surname": [0.0, 1.0], "person": [1.0, 0.0]}, counter),
    )
    candidates = [
        ("Surname", _uri("Q1"), "surname"),
        ("Person", _uri("Q2"), "person"),
    ]
    wd.select_best_candidate(CTX, candidates, threshold=0.0, margin=0.05)
    wd.select_best_candidate(CTX, candidates, threshold=0.0, margin=0.05)
    # Two QIDs embedded once; the second call is a pure cache hit.
    assert counter["n_texts"] == 2


def test_wrapper_none_context_falls_back_to_prominence(monkeypatch):
    # Should not even call embed_texts.
    called = {"n": 0}
    monkeypatch.setattr(wd, "embed_texts",
                        lambda t: called.__setitem__("n", called["n"] + 1) or [None] * len(t))
    candidates = [("Surname", _uri("Q1"), "surname"), ("Person", _uri("Q2"), "person")]
    assert wd.select_best_candidate(None, candidates) == ("Surname", _uri("Q1"))
    assert called["n"] == 0


def test_wrapper_declines_below_threshold(monkeypatch):
    counter = {"calls": 0, "n_texts": 0}
    monkeypatch.setattr(
        wd, "embed_texts",
        _fake_embed({"surname": [0.0, 1.0]}, counter),
    )
    # single candidate cos 0.0; threshold 0.5 -> None
    candidates = [("Surname", _uri("Q1"), "surname")]
    assert wd.select_best_candidate(CTX, candidates, threshold=0.5, margin=0.05) is None


def test_wrapper_missing_description_is_unscoreable(monkeypatch):
    counter = {"calls": 0, "n_texts": 0}
    monkeypatch.setattr(
        wd, "embed_texts",
        _fake_embed({"person": [1.0, 0.0]}, counter),
    )
    # #0 has no description (cached None, never sent to embed_texts); #1 wins
    candidates = [("Blank", _uri("Q1"), ""), ("Person", _uri("Q2"), "person")]
    out = wd.select_best_candidate(CTX, candidates, threshold=0.0, margin=0.05)
    assert out == ("Person", _uri("Q2"))
    assert counter["n_texts"] == 1  # only "person" was embedded


def test_wrapper_empty_candidates_returns_none():
    assert wd.select_best_candidate(CTX, []) is None
