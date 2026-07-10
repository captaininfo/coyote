"""
Unit tests for the pure functions of the source-inference demo (Track B, B1).

The demo module keeps graph/embedder imports out of module top-level
precisely so these functions are testable host-side with stdlib only.
build_evidence is covered only on its pointer-note branch (the one path
that returns before touching the embedder).
"""
import math
import sys
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).parent.parent / "images" / "core" / "core_analysis")
)

from coyote.demos.source_inference import (  # noqa: E402
    PROSE_MIN_CHARS,
    blind_rank_of_source,
    build_evidence,
    cosine,
    dedupe_pages_by_url,
    is_pointer_note,
    rank_pages,
)


def _page(url, ts="2026-01-01T00:00:00", emb=(1.0, 0.0), title=None):
    return {"url": url, "title": title or url, "timestamp": ts, "embedding": list(emb)}


# ── cosine ────────────────────────────────────────────────────────────────

def test_cosine_identical_is_one():
    assert math.isclose(cosine([0.6, 0.8], [0.6, 0.8]), 1.0, abs_tol=1e-9)


def test_cosine_orthogonal_is_zero():
    assert math.isclose(cosine([1.0, 0.0], [0.0, 1.0]), 0.0, abs_tol=1e-9)


def test_cosine_opposite_is_minus_one():
    assert math.isclose(cosine([1.0, 0.0], [-1.0, 0.0]), -1.0, abs_tol=1e-9)


def test_cosine_zero_vector_degenerates_to_zero():
    assert cosine([0.0, 0.0], [1.0, 0.0]) == 0.0


def test_cosine_unnormalized_inputs():
    # magnitudes must not matter
    assert math.isclose(cosine([2.0, 0.0], [7.5, 0.0]), 1.0, abs_tol=1e-9)


# ── dedupe_pages_by_url ───────────────────────────────────────────────────

def test_dedupe_keeps_most_recent_visit():
    old = _page("https://a", ts="2026-01-01T00:00:00", emb=(1.0, 0.0))
    new = _page("https://a", ts="2026-02-01T00:00:00", emb=(0.0, 1.0))
    out = dedupe_pages_by_url([old, new])
    assert len(out) == 1
    assert out[0]["embedding"] == [0.0, 1.0]


def test_dedupe_order_of_input_irrelevant():
    old = _page("https://a", ts="2026-01-01T00:00:00", emb=(1.0, 0.0))
    new = _page("https://a", ts="2026-02-01T00:00:00", emb=(0.0, 1.0))
    assert dedupe_pages_by_url([new, old]) == dedupe_pages_by_url([old, new])


def test_dedupe_drops_missing_url_and_missing_embedding():
    pages = [
        {"url": None, "timestamp": "x", "embedding": [1.0]},
        {"url": "https://a", "timestamp": "x", "embedding": None},
        _page("https://b"),
    ]
    out = dedupe_pages_by_url(pages)
    assert [p["url"] for p in out] == ["https://b"]


def test_dedupe_none_timestamp_loses_to_any_timestamp():
    untimed = dict(_page("https://a", emb=(1.0, 0.0)), timestamp=None)
    timed = _page("https://a", ts="2026-01-01T00:00:00", emb=(0.0, 1.0))
    out = dedupe_pages_by_url([timed, untimed])
    assert out[0]["embedding"] == [0.0, 1.0]


def test_dedupe_output_sorted_by_url():
    out = dedupe_pages_by_url([_page("https://c"), _page("https://a"), _page("https://b")])
    assert [p["url"] for p in out] == ["https://a", "https://b", "https://c"]


# ── rank_pages / blind_rank_of_source ────────────────────────────────────

def test_rank_orders_by_cosine_descending():
    query = [1.0, 0.0]
    pages = [
        _page("https://far", emb=(0.0, 1.0)),
        _page("https://near", emb=(1.0, 0.0)),
        _page("https://mid", emb=(1.0, 1.0)),
    ]
    ranked = rank_pages(query, pages)
    assert [p["url"] for _, p in ranked] == ["https://near", "https://mid", "https://far"]


def test_rank_tie_break_is_deterministic_by_url():
    query = [1.0, 0.0]
    pages = [_page("https://z", emb=(1.0, 0.0)), _page("https://a", emb=(2.0, 0.0))]
    ranked = rank_pages(query, pages)
    assert [p["url"] for _, p in ranked] == ["https://a", "https://z"]


def test_blind_rank_finds_source():
    query = [1.0, 0.0]
    pages = [_page("https://src", emb=(1.0, 1.0)), _page("https://other", emb=(1.0, 0.0))]
    ranked = rank_pages(query, pages)
    assert blind_rank_of_source(ranked, "https://src") == 2


def test_blind_rank_absent_source_is_none():
    ranked = rank_pages([1.0, 0.0], [_page("https://a")])
    assert blind_rank_of_source(ranked, "https://missing") is None


# ── pointer-note boundary ────────────────────────────────────────────────

def test_pointer_note_thin_prose():
    assert is_pointer_note("what?")
    assert is_pointer_note("")
    assert is_pointer_note(None)
    assert is_pointer_note("  " + "x" * (PROSE_MIN_CHARS - 1) + "  ")


def test_pointer_note_substantive_prose():
    assert not is_pointer_note("x" * PROSE_MIN_CHARS)


def test_build_evidence_pointer_branch_never_ranks():
    annotation = {
        "annotation_id": "a1",
        "prose": "huh?",
        "timestamp": "2026-01-01T00:00:00",
        "source_url": "https://src",
        "source_title": "Source",
        "source_embedding": [1.0, 0.0],
    }
    ev = build_evidence(annotation, [_page("https://src")], top_k=5)
    assert ev["pointer_note"] is True
    assert "nearest_inputs" not in ev
    assert "divergence_from_source" not in ev
    assert "provenance" in ev["note"] or "annotation link" in ev["note"]
    assert ev["known_source"]["url"] == "https://src"
