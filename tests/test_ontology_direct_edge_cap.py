"""
Gate A3.1 — per-stream direct-edge cap (Track A commit 3).

Covers the pure selection helper (select_direct_link_targets: threshold ->
per-family URI dedupe -> per-family cap with deterministic tie-break), the
ONTOLOGY_DIRECT_EDGE_CAP env parsing, and the _process_single_event wiring
(capped-out concepts never reach link_concept_and_ancestors, so they also
skip their WDQS ancestor walk).
"""
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# --- Stub heavy module-level imports BEFORE loading target module ---------
# (mirrors test_connect_to_ontology_direct_link.py)
class _StubEndPointInternalError(Exception):
    pass


_sparql_stub = MagicMock()
_sparql_stub.JSON = "json"
_sparql_stub.SPARQLExceptions = MagicMock()
_sparql_stub.SPARQLExceptions.EndPointInternalError = _StubEndPointInternalError
sys.modules.setdefault("SPARQLWrapper", _sparql_stub)
sys.modules.setdefault("neo4j", MagicMock())

sys.path.insert(
    0, str(Path(__file__).parent.parent / "images" / "core" / "core_analysis")
)

sys.modules.setdefault("coyote.utils.config_manager", MagicMock())

from coyote.neo4j_integration import connect_to_ontology as target  # noqa: E402


def _uri(n):
    return f"http://www.wikidata.org/entity/Q{n}"


def _quad(n, score, family, label=None):
    return (_uri(n), label or f"label{n}", score, family)


# ---------------------------------------------------------------------------
# select_direct_link_targets — pure helper
# ---------------------------------------------------------------------------

class TestSelectDirectLinkTargets:
    def test_threshold_applied_before_anything(self):
        quads = [_quad(1, 0.05, "topic"), _quad(2, 0.5, "topic")]
        targets, stats = target.select_direct_link_targets(quads, 0.10, 20)
        assert targets == [(_uri(2), "label2", 0.5)]
        assert stats["skipped_below_threshold"] == 1
        assert stats["capped"] == {}

    def test_mention_duplicates_deduped_within_family(self):
        # Entity JSON carries one object per mention: same URI, same score.
        quads = [_quad(1, 1.1, "entity")] * 5 + [_quad(2, 0.7, "entity")]
        targets, stats = target.select_direct_link_targets(quads, 0.10, 20)
        assert targets == [(_uri(1), "label1", 1.1), (_uri(2), "label2", 0.7)]

    def test_cap_keeps_top_n_by_score_desc(self):
        quads = [_quad(n, 0.1 * n, "entity") for n in range(1, 6)]  # 0.1..0.5
        targets, _ = target.select_direct_link_targets(quads, 0.0, 2)
        assert [u for u, _, _ in targets] == [_uri(5), _uri(4)]

    def test_cap_tie_break_is_uri_ascending(self):
        # Log-scores tie whenever mention counts tie — URI ascending decides.
        quads = [_quad(n, 1.099, "entity") for n in (30, 10, 20)]
        targets, _ = target.select_direct_link_targets(quads, 0.0, 2)
        assert [u for u, _, _ in targets] == [_uri(10), _uri(20)]

    def test_caps_are_per_family_topics_survive_entity_tail(self):
        # The score-scale trap regression: entity ln-scores (>=1.099) must
        # NOT evict topic cosines (<=1.0) — each family is capped alone.
        entities = [_quad(n, 1.5, "entity") for n in range(1, 30)]
        topics = [_quad(n, 0.3, "topic") for n in range(100, 105)]
        targets, stats = target.select_direct_link_targets(
            entities + topics, 0.10, 20
        )
        kept_topics = [u for u, _, _ in targets if u in {q[0] for q in topics}]
        kept_entities = [u for u, _, _ in targets if u in {q[0] for q in entities}]
        assert len(kept_topics) == 5      # untouched (5 < cap)
        assert len(kept_entities) == 20   # capped
        assert set(stats["capped"]) == {"entity"}
        before, kept, lo, hi = stats["capped"]["entity"]
        assert (before, kept) == (29, 20)
        assert lo == hi == 1.5  # dropped-tail score range

    def test_topic_cap_binds_independently(self):
        topics = [_quad(n, 0.2 + 0.01 * n, "topic") for n in range(1, 30)]
        targets, stats = target.select_direct_link_targets(topics, 0.10, 20)
        assert len(targets) == 20
        assert set(stats["capped"]) == {"topic"}

    def test_both_family_uri_capped_out_of_entities_links_via_topics(self):
        # Q1 is a low-scoring entity (capped out) but also an earned topic.
        entities = [_quad(n, 2.0, "entity") for n in range(10, 13)]
        entities.append(_quad(1, 1.1, "entity"))
        topics = [_quad(1, 0.4, "topic")]
        targets, _ = target.select_direct_link_targets(
            topics + entities, 0.10, 3
        )
        uris = [u for u, _, _ in targets]
        assert _uri(1) in uris  # via the topic family
        assert uris.count(_uri(1)) == 1

    def test_cap_none_disables(self):
        quads = [_quad(n, 1.0, "entity") for n in range(1, 50)]
        targets, stats = target.select_direct_link_targets(quads, 0.10, None)
        assert len(targets) == 49
        assert stats["capped"] == {}

    def test_no_cap_preserves_first_seen_order(self):
        quads = [_quad(3, 0.2, "topic"), _quad(1, 0.9, "topic")]
        targets, _ = target.select_direct_link_targets(quads, 0.10, 20)
        assert [u for u, _, _ in targets] == [_uri(3), _uri(1)]


# ---------------------------------------------------------------------------
# ONTOLOGY_DIRECT_EDGE_CAP env parsing
# ---------------------------------------------------------------------------

class TestReadCapEnv:
    def test_unset_defaults_to_20(self, monkeypatch):
        monkeypatch.delenv("ONTOLOGY_DIRECT_EDGE_CAP", raising=False)
        assert target._read_cap_env() == 20

    @pytest.mark.parametrize("raw", ["", "  ", "0", "-5", "abc"])
    def test_blank_nonpositive_unparseable_disable(self, monkeypatch, raw):
        monkeypatch.setenv("ONTOLOGY_DIRECT_EDGE_CAP", raw)
        assert target._read_cap_env() is None

    def test_explicit_value(self, monkeypatch):
        monkeypatch.setenv("ONTOLOGY_DIRECT_EDGE_CAP", "15")
        assert target._read_cap_env() == 15


# ---------------------------------------------------------------------------
# _process_single_event wiring — capped concepts skip the ancestor walk too
# ---------------------------------------------------------------------------

class TestProcessSingleEventCapWiring:
    def _make_manager(self):
        mgr = object.__new__(target.CoyoteOntologyStateManager)
        mgr._cache_db_path = Path("/unused")
        return mgr

    def _record(self, node_id, entities_json):
        # session.run record shape: supports ["node_id"] and .get(field).
        return {
            "node_id": node_id,
            "entities": entities_json,
            "topics": None, "textTopics": None,
            "annotationTextEntities": None, "highlightedTextEntities": None,
            "timestamp": "2026-07-08T00:00:00",
        }

    def test_cap_bounds_link_calls_and_wdqs_walks(self, monkeypatch):
        # 5 distinct entities (with mention duplicates), cap 2 -> exactly the
        # top-2 by score reach link_concept_and_ancestors.
        monkeypatch.setattr(target, "ONTOLOGY_DIRECT_EDGE_CAP", 2)
        items = []
        for n, score in ((1, 1.1), (2, 2.1), (3, 1.6), (4, 0.7), (5, 1.9)):
            items += [{"entity": f"e{n}", "wikidata_uri": _uri(n),
                       "label": f"L{n}", "score": score}] * 2
        session = MagicMock()
        session.run.return_value = [self._record(7, json.dumps(items))]

        mgr = self._make_manager()
        with patch.object(target, "link_concept_and_ancestors") as link, \
             patch.object(mgr, "_update_event_queue_status") as upd:
            mgr._process_single_event(session, "ev-1")

        linked = [c.args[2] for c in link.call_args_list]  # uri positional
        assert linked == [_uri(2), _uri(5)]  # top-2 by score, once each
        upd.assert_called_once_with("ev-1", "ontology_processed")

    def test_cap_disabled_links_every_distinct_uri(self, monkeypatch):
        monkeypatch.setattr(target, "ONTOLOGY_DIRECT_EDGE_CAP", None)
        items = [{"entity": f"e{n}", "wikidata_uri": _uri(n),
                  "label": f"L{n}", "score": 1.0} for n in range(1, 6)]
        session = MagicMock()
        session.run.return_value = [self._record(7, json.dumps(items))]

        mgr = self._make_manager()
        with patch.object(target, "link_concept_and_ancestors") as link, \
             patch.object(mgr, "_update_event_queue_status"):
            mgr._process_single_event(session, "ev-2")

        assert len(link.call_args_list) == 5
