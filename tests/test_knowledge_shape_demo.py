"""
Unit tests for the pure functions of the knowledge-shape (coverage-map) demo.

Like the source-inference demo, all graph/cache/embedder access is kept out
of module top-level so these functions are testable host-side with stdlib
only. The tests exercise the reconstruction logic on hand-built fixtures
(no Neo4j, no wikidata_cache.db).
"""
import json
import sys
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).parent.parent / "images" / "core" / "core_analysis")
)

from coyote.demos.knowledge_shape import (  # noqa: E402
    aggregate_touched,
    build_dag,
    build_graph_payload,
    compute_depths,
    coverage_summary,
    extract_leaf_rows,
    find_roots,
    frontier_concepts,
    normalize_url,
    parse_parent_map,
    rank_hubs,
    render_html,
    subtree_page_coverage,
)


# ── extract_leaf_rows ──────────────────────────────────────────────────────

def test_extract_leaf_rows_pulls_resolved_uris_from_both_fields():
    records = [{
        "url": "http://a.com",
        "topics": json.dumps([
            {"topic": "heutagogy", "wikidata_uri": "Q5", "label": "heutagogy", "score": 0.4},
            {"topic": "loading annotation", "wikidata_uri": None, "label": None},  # skipped
        ]),
        "entities": json.dumps([
            {"entity": "Dewey", "wikidata_uri": "Q131805", "label": "John Dewey"},
        ]),
    }]
    rows = extract_leaf_rows(records)
    assert {r["uri"] for r in rows} == {"Q5", "Q131805"}
    assert all(r["url"] == "http://a.com" for r in rows)
    assert next(r["label"] for r in rows if r["uri"] == "Q131805") == "John Dewey"


def test_extract_leaf_rows_survives_malformed_and_empty_json():
    records = [
        {"url": "http://a.com", "topics": "not json", "entities": None},
        {"url": "http://b.com", "topics": json.dumps({"not": "a list"}), "entities": "[]"},
        {"url": "http://c.com", "topics": json.dumps([{"wikidata_uri": "Q9", "label": "x"}]),
         "entities": None},
    ]
    rows = extract_leaf_rows(records)
    assert [r["uri"] for r in rows] == ["Q9"]


# ── aggregate_touched ──────────────────────────────────────────────────────

def test_aggregate_counts_distinct_normalized_pages():
    rows = [
        {"uri": "Q1", "label": "learning", "url": "http://a.com/x"},
        {"uri": "Q1", "label": "learning", "url": "http://a.com/x#section"},  # dup (fragment)
        {"uri": "Q1", "label": "learning", "url": "http://a.com/x?utm_source=t"},  # dup (utm)
        {"uri": "Q1", "label": "learning", "url": "http://a.com/y"},          # distinct
    ]
    touched = aggregate_touched(rows)
    assert touched["Q1"]["pages"] == 2
    assert touched["Q1"]["label"] == "learning"


def test_aggregate_skips_missing_uri_and_keeps_first_label():
    rows = [
        {"uri": None, "label": "junk", "url": "http://a.com"},
        {"uri": "Q2", "label": "first", "url": "http://b.com"},
        {"uri": "Q2", "label": "", "url": "http://c.com"},
    ]
    touched = aggregate_touched(rows)
    assert "Q2" in touched and None not in touched
    assert touched["Q2"]["label"] == "first"


def test_normalize_url_strips_fragment_and_trackers():
    assert normalize_url("http://x.com/p?utm_medium=q&g=7#top") == "http://x.com/p?g=7"


# ── parse_parent_map ───────────────────────────────────────────────────────

def _cache_row(uri, parents):
    return (uri, json.dumps(parents))


def test_parse_parent_map_keeps_only_hierarchical_relations():
    rows = [
        _cache_row("Q1", [
            {"parent": "Q10", "parentLabel": "field", "relationship": "subclass of"},
            {"parent": "Q11", "parentLabel": "whole", "relationship": "part of"},  # excluded
        ]),
        _cache_row("Q2", [
            {"parent": "Q20", "parentLabel": "kind", "relationship": "instance of"},
        ]),
    ]
    pm = parse_parent_map(rows)
    assert pm["Q1"] == [("Q10", "field")]
    assert pm["Q2"] == [("Q20", "kind")]


def test_parse_parent_map_survives_malformed_rows():
    rows = [
        ("Q1", "not json"),
        ("Q2", None),
        ("Q3", json.dumps(["a string, not a dict"])),
        _cache_row("Q4", [{"parent": "Q40", "parentLabel": "p", "relationship": "subclass of"}]),
    ]
    pm = parse_parent_map(rows)
    assert pm == {"Q4": [("Q40", "p")]}


# ── build_dag / roots / depths ─────────────────────────────────────────────

def _touched(*specs):
    return {uri: {"label": label, "pages": pages} for uri, label, pages in specs}


def test_build_dag_walks_up_and_marks_structural_ancestors():
    touched = _touched(("Q1", "deep learning", 3))
    parent_map = {"Q1": [("Q2", "machine learning")], "Q2": [("Q3", "AI")]}
    nodes = build_dag(touched, parent_map)
    assert nodes["Q1"]["touched"] and nodes["Q1"]["pages"] == 3
    assert not nodes["Q2"]["touched"] and nodes["Q2"]["pages"] == 0
    assert nodes["Q3"]["children"] == {"Q2"}
    assert nodes["Q1"]["parents"] == {"Q2"}


def test_build_dag_is_cycle_safe():
    touched = _touched(("Q1", "a", 1))
    parent_map = {"Q1": [("Q2", "b")], "Q2": [("Q1", "a")]}  # 2-cycle
    nodes = build_dag(touched, parent_map)
    assert set(nodes) == {"Q1", "Q2"}  # terminates, both present


def test_find_roots_and_depths():
    touched = _touched(("Q1", "deep learning", 1))
    parent_map = {"Q1": [("Q2", "ml")], "Q2": [("Q3", "ai")]}
    nodes = build_dag(touched, parent_map)
    roots = find_roots(nodes)
    assert roots == ["Q3"]
    depths = compute_depths(nodes, roots)
    assert depths == {"Q3": 0, "Q2": 1, "Q1": 2}


def test_multiple_parents_are_preserved():
    touched = _touched(("Q1", "biophysics", 1))
    parent_map = {"Q1": [("Q2", "biology"), ("Q3", "physics")]}
    nodes = build_dag(touched, parent_map)
    assert nodes["Q1"]["parents"] == {"Q2", "Q3"}
    assert set(find_roots(nodes)) == {"Q2", "Q3"}


# ── coverage / hubs / frontier ─────────────────────────────────────────────

def test_subtree_coverage_counts_touched_descendants():
    touched = _touched(("Q1", "cnn", 2), ("Q2", "rnn", 1))
    parent_map = {"Q1": [("Q9", "nn")], "Q2": [("Q9", "nn")]}
    nodes = build_dag(touched, parent_map)
    cov = subtree_page_coverage(nodes)
    assert cov["Q9"] == 2   # both touched concepts under the ancestor
    assert cov["Q1"] == 1   # itself


def test_rank_hubs_orders_by_pages_then_label():
    touched = _touched(("Q1", "b", 5), ("Q2", "a", 5), ("Q3", "c", 9))
    ranked = rank_hubs(touched)
    assert [h["uri"] for h in ranked] == ["Q3", "Q2", "Q1"]  # 9, then 5/a, then 5/b


def test_frontier_is_single_page_leaves_only():
    # Q1 (2 pages) is not frontier; Q2 (1 page leaf) is; Q3 (1 page but has a
    # touched child) is not.
    touched = _touched(("Q1", "hub", 2), ("Q2", "edge", 1), ("Q3", "mid", 1),
                       ("Q4", "under-mid", 1))
    parent_map = {"Q4": [("Q3", "mid")]}
    nodes = build_dag(touched, parent_map)
    fr_labels = {f["label"] for f in frontier_concepts(nodes)}
    assert "edge" in fr_labels
    assert "under-mid" in fr_labels
    assert "hub" not in fr_labels
    assert "mid" not in fr_labels  # has a touched child


def test_coverage_summary_shape():
    touched = _touched(("Q1", "deep learning", 3), ("Q2", "ethics", 1))
    parent_map = {"Q1": [("Q9", "ai")]}
    nodes = build_dag(touched, parent_map)
    roots = find_roots(nodes)
    depths = compute_depths(nodes, roots)
    s = coverage_summary(nodes, touched, roots, depths, distinct_pages=3)
    assert s["concepts_touched"] == 2
    assert s["distinct_pages"] == 3        # passed in, not summed per-concept
    assert s["structural_ancestors"] == 1  # Q9
    assert s["max_depth"] == 1             # Q1 under Q9; Q2 is its own root


# ── browser visual payload / html ──────────────────────────────────────────

def _bundle():
    touched = _touched(("Q1", "deep learning", 3), ("Q2", "ethics", 1))
    parent_map = {"Q1": [("Q9", "artificial intelligence")]}
    nodes = build_dag(touched, parent_map)
    roots = find_roots(nodes)
    depths = compute_depths(nodes, roots)
    return {"touched": touched, "nodes": nodes, "roots": roots,
            "depths": depths, "distinct_pages": 3}


def test_build_graph_payload_indices_and_flags():
    p = build_graph_payload(_bundle())
    # one node per reconstructed uri (2 touched + 1 ancestor)
    assert len(p["nodes"]) == 3
    # links reference nodes by integer index and stay in range
    assert all(0 <= a < len(p["nodes"]) and 0 <= b < len(p["nodes"])
               for a, b in p["links"])
    labels = {n["label"]: n for n in p["nodes"]}
    assert labels["deep learning"]["t"] == 1 and labels["deep learning"]["pages"] == 3
    assert labels["artificial intelligence"]["t"] == 0  # structural ancestor
    # the ancestor's subtree coverage includes the touched concept under it
    assert labels["artificial intelligence"]["cov"] == 1
    assert p["summary"]["concepts_touched"] == 2


def test_render_html_is_self_contained_and_embeds_payload():
    html = render_html(_bundle(), title="My shape")
    assert html.startswith("<!doctype html>")
    assert "My shape" in html
    assert "__PAYLOAD__" not in html and "__TITLE__" not in html  # placeholders filled
    assert "const DATA = " in html
    # no external assets (offline / shareable)
    assert "http://" not in html and "https://" not in html
    assert "src=" not in html
