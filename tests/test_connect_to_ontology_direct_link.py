"""
Unit tests for the option-(a) restoration in connect_to_ontology:
the direct webpage->concept HAS_TOPIC edge.

Covers:
- extract_uris_from_node_data now returns (uri, label, score) triples for the
  live dict shape, plus the legacy list/uri-array shapes.
- link_concept_and_ancestors MERGEs the disambiguated concept itself (uri +
  label) as the edge target, and does so EVEN WHEN WDQS returns no parents
  (breaker open) — the load-bearing WDQS-independence guarantee.
- The ancestor enrichment is still attempted (best-effort) after the direct
  edge.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# --- Stub heavy module-level imports BEFORE loading target module ---------
# connect_to_ontology imports neo4j and SPARQLWrapper at module load; neither
# is needed (nor installed on the host) for these tests. Mirrors the stub
# preamble in test_connect_to_ontology_breaker.py.
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


CONCEPT_URI = "http://www.wikidata.org/entity/Q131805"   # John Dewey
CONCEPT_LABEL = "John Dewey"
PARENT_URI = "http://www.wikidata.org/entity/Q5"          # human
TS = "2026-06-26T01:00:00"


# ---------------------------------------------------------------------------
# extract_uris_from_node_data — (uri, label, score, family) quads (A3)
# ---------------------------------------------------------------------------

class TestExtractQuads:
    def test_pattern1_dict_carries_label_score_and_topic_family(self):
        data = {
            "topics": '[{"topic": "pragmatism", "wikidata_uri": "%s", '
                      '"label": "%s", "score": 0.42}]' % (CONCEPT_URI, CONCEPT_LABEL),
        }
        out = target.extract_uris_from_node_data(data)
        assert out == [(CONCEPT_URI, CONCEPT_LABEL, 0.42, "topic")]

    def test_entities_field_tagged_entity_family(self):
        data = {
            "entities": '[{"entity": "Dewey", "wikidata_uri": "%s", '
                        '"label": "%s", "score": 1.1}]' % (CONCEPT_URI, CONCEPT_LABEL),
        }
        out = target.extract_uris_from_node_data(data)
        assert out == [(CONCEPT_URI, CONCEPT_LABEL, 1.1, "entity")]

    def test_all_five_fields_map_to_their_family(self):
        item = '[{"wikidata_uri": "%s", "label": "x", "score": 0.5}]' % CONCEPT_URI
        families = {
            "topics": "topic", "textTopics": "topic",
            "entities": "entity", "annotationTextEntities": "entity",
            "highlightedTextEntities": "entity",
        }
        for key, family in families.items():
            out = target.extract_uris_from_node_data({key: item})
            assert out == [(CONCEPT_URI, "x", 0.5, family)], key

    def test_missing_label_defaults_to_empty_string(self):
        data = {"topics": '[{"wikidata_uri": "%s", "score": 0.3}]' % CONCEPT_URI}
        out = target.extract_uris_from_node_data(data)
        assert out == [(CONCEPT_URI, "", 0.3, "topic")]

    def test_legacy_two_element_list_empty_label_zero_score(self):
        # pattern 2 — dropped by any positive threshold downstream.
        data = {"topics": '[["pragmatism", "%s"]]' % CONCEPT_URI}
        out = target.extract_uris_from_node_data(data)
        assert out == [(CONCEPT_URI, "", 0.0, "topic")]

    def test_legacy_uri_array_shares_label_and_score(self):
        # pattern 3 — shared label/score across the uri array.
        data = {
            "topics": '[{"uri": ["%s", "%s"], "label": "shared", "score": 0.5}]'
                      % (CONCEPT_URI, PARENT_URI),
        }
        out = target.extract_uris_from_node_data(data)
        assert out == [
            (CONCEPT_URI, "shared", 0.5, "topic"),
            (PARENT_URI, "shared", 0.5, "topic"),
        ]

    def test_bad_json_skipped(self):
        out = target.extract_uris_from_node_data({"topics": "{not json"})
        assert out == []


# ---------------------------------------------------------------------------
# link_concept_and_ancestors — direct concept edge, WDQS-independent
# ---------------------------------------------------------------------------

class TestDirectConceptEdge:
    def _run_with_wdqs_empty(self, session, cache_db=Path("/unused")):
        """Call the helper with WDQS forced to return no parents."""
        with patch.object(target, "get_from_cache", return_value=None), \
             patch.object(target, "batch_query_wikidata", return_value={}) as bq:
            target.link_concept_and_ancestors(
                session, 7, CONCEPT_URI, CONCEPT_LABEL, TS, 0.42, cache_db
            )
        return bq

    def test_concept_node_merged_even_when_wdqs_empty(self):
        """The load-bearing test: with WDQS returning nothing (breaker open),
        the disambiguated concept itself is still MERGEd and edged."""
        session = MagicMock()
        self._run_with_wdqs_empty(session)

        # Among session.run calls, one MERGEs the concept node with its label
        # (targetUri == the concept, targetLabel == its label) and one MERGEs
        # the HAS_TOPIC edge to it (targetUri == the concept). Both prove the
        # concept — not just an ancestor — lands in the graph.
        run_kwargs = [c.kwargs for c in session.run.call_args_list]
        assert any(
            kw.get("targetUri") == CONCEPT_URI and kw.get("targetLabel") == CONCEPT_LABEL
            for kw in run_kwargs
        ), "concept node MERGE with label missing"
        assert any(
            kw.get("targetUri") == CONCEPT_URI and "score" in kw
            for kw in run_kwargs
        ), "HAS_TOPIC edge to the concept missing"

    def test_ancestor_enrichment_still_attempted(self):
        """Direct edge does not short-circuit the (best-effort) ancestor walk:
        with a cache miss, WDQS is still queried for the concept's parents."""
        session = MagicMock()
        bq = self._run_with_wdqs_empty(session)
        bq.assert_called_once_with([CONCEPT_URI], Path("/unused"))

    def test_direct_edge_uses_has_topic_relationship(self):
        session = MagicMock()
        self._run_with_wdqs_empty(session)
        # create_or_link_node injects the relationship type into the edge MERGE
        # via an f-string; the rendered query must contain HAS_TOPIC.
        queries = [c.args[0] for c in session.run.call_args_list if c.args]
        assert any("HAS_TOPIC" in q for q in queries)
