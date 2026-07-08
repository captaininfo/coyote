"""
Unit tests for the A1 junk-candidate filter (Gate A1.1).

Pure module — no stubs needed (stdlib only). Covers:
- word-boundary pattern behavior (the measure_a4.py A2 substring-over-fire
  defect as a regression class: patterns must not fire inside longer words);
- exact-QID blocklist, including empty-description meta items (the Gate B
  direct-path hole: Q4167410);
- class semantics: meta #1 -> fallback allowed (survivors keep order);
  name-marker #1 -> top1_class flags the term for no-context drop;
  disambiguation #1 -> same drop semantics (Fable nit, adopted);
- junk at non-#1 positions is filtered from survivors WITHOUT flagging the
  term (the F5 #1-only keying: AI/Africa/Berlin keep their clean #1);
- empty descriptions survive; all-junk lists yield empty survivors;
- the secondary pattern set is deliberately NOT matched (episode of ...).
"""
import sys
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).parent.parent / "images" / "core" / "core_analysis")
)

from coyote.analysis.nlp.wikidata_candidate_filter import (  # noqa: E402
    NO_CONTEXT_DROP_CLASSES,
    WIKIMEDIA_META_QIDS,
    WIKIMEDIA_META_URIS,
    FilterResult,
    classify_candidate,
    filter_candidates,
)


def _uri(qid):
    return f"http://www.wikidata.org/entity/{qid}"


def _clean(qid="Q11660", label="artificial intelligence",
           desc="branch of computer science"):
    return (label, _uri(qid), desc)


# ---------------------------------------------------------------------------
# classify_candidate — patterns and word boundaries
# ---------------------------------------------------------------------------

class TestClassify:
    def test_meta_description_patterns_flag(self):
        for desc in (
            "scholarly article published in Nature",
            "scientific article",
            "article published in 2019",
            "research article by Chen et al.",
            "academic journal",
            "peer-reviewed website",
            "Wikimedia list article",
            "article on Wikipedia",
            "grammatical category",
            "MediaWiki template",
            "list of paintings by Monet",
        ):
            verdict = classify_candidate(_uri("Q999"), desc)
            assert verdict is not None and verdict[0] == "meta", desc

    def test_name_marker_patterns_flag(self):
        for desc in ("family name", "Chinese family name",
                     "female given name", "surname of Dutch origin"):
            verdict = classify_candidate(_uri("Q999"), desc)
            assert verdict is not None and verdict[0] == "name_marker", desc

    def test_disambiguation_description_is_its_own_class(self):
        verdict = classify_candidate(_uri("Q999"), "disambiguation page")
        assert verdict is not None and verdict[0] == "disambig"

    def test_word_boundaries_do_not_fire_inside_longer_words(self):
        # The measure_a4.py A2 defect as a regression class: bare-substring
        # matching over-fires. None of these may flag.
        for desc in (
            "categorical imperative in Kant's ethics",   # not "category"
            "subcategory of machine learning",           # not "category"
            "templated rendering engine",                # not "template"
            "notable Wikipedian and editor",             # not "wikipedia"
            "study of linguistics",                      # A2 lesson verbatim
            "colonial authorities in West Africa",       # A2 lesson verbatim
        ):
            assert classify_candidate(_uri("Q999"), desc) is None, desc

    def test_secondary_set_deliberately_not_matched(self):
        # Registered-primary only in v1: episode-of / preprint / thesis junk
        # survives A1 (documented accepted cost — plan section 1.3).
        for desc in ("episode of Madam Secretary", "preprint on arXiv",
                     "doctoral thesis"):
            assert classify_candidate(_uri("Q999"), desc) is None, desc

    def test_empty_description_is_clean_unless_qid_blocked(self):
        assert classify_candidate(_uri("Q42"), "") is None
        assert classify_candidate(_uri("Q42"), None) is None
        verdict = classify_candidate(_uri("Q13406463"), "")
        assert verdict == ("meta", "qid:Q13406463")

    def test_qid_blocklist_all_six(self):
        for qid in WIKIMEDIA_META_QIDS:
            verdict = classify_candidate(_uri(qid), "anything at all")
            assert verdict is not None, qid
            expected = "disambig" if qid == "Q4167410" else "meta"
            assert verdict[0] == expected, qid

    def test_gate_b_edge_is_caught(self):
        # The one A4 Gate B failure: Q4167410 landed as a DIRECT concept.
        verdict = classify_candidate(
            _uri("Q4167410"), "Wikimedia disambiguation page"
        )
        assert verdict == ("disambig", "qid:Q4167410")

    def test_name_marker_takes_precedence_over_meta_in_description(self):
        # "surname disambiguation on Wikipedia" is name evidence first.
        verdict = classify_candidate(
            _uri("Q999"), "surname disambiguation page on Wikipedia"
        )
        assert verdict is not None and verdict[0] == "name_marker"


# ---------------------------------------------------------------------------
# filter_candidates — survivor lists and term-level class
# ---------------------------------------------------------------------------

class TestFilterCandidates:
    def test_clean_list_passes_through_unchanged(self):
        cands = [_clean(), _clean("Q2", "Earth", "third planet from the Sun")]
        fr = filter_candidates(cands, term="ai")
        assert fr == FilterResult(survivors=cands, top1_class=None)

    def test_meta_top1_filtered_with_fallback_order_preserved(self):
        # The French-Revolution-episode shape: junk #1, real concept #2.
        junk = ("The French Revolution", _uri("Q114495590"),
                "scholarly article published in a journal")
        real = ("French Revolution", _uri("Q6534"),
                "revolution in France from 1789 to 1799")
        real2 = _clean("Q7", "other", "some concept")
        fr = filter_candidates([junk, real, real2], term="the french revolution")
        assert fr.survivors == [real, real2]
        assert fr.top1_class == "meta"
        assert fr.top1_class not in NO_CONTEXT_DROP_CLASSES  # fallback allowed

    def test_name_marker_top1_flags_term_and_filters_all_name_candidates(self):
        surname = ("Dewey", _uri("Q1"), "family name")
        person = ("John Dewey", _uri("Q131805"),
                  "American philosopher and educational reformer")
        surname2 = ("Dewey", _uri("Q3"), "surname")
        fr = filter_candidates([surname, person, surname2], term="dewey")
        assert fr.survivors == [person]  # Unit 8 picks among persons
        assert fr.top1_class == "name_marker"
        assert fr.top1_class in NO_CONTEXT_DROP_CLASSES

    def test_junk_in_tail_does_not_flag_a_clean_term(self):
        # F5 #1-only keying: AI has a surname item somewhere in top-7 but its
        # #1 is clean — the term must NOT be drop-flagged.
        ai = _clean()
        tail_name = ("Ai", _uri("Q5"), "given name")
        tail_meta = ("AI", _uri("Q6"), "Wikimedia disambiguation page")
        fr = filter_candidates([ai, tail_name, tail_meta], term="ai")
        assert fr.survivors == [ai]
        assert fr.top1_class is None

    def test_disambig_top1_gets_drop_semantics(self):
        disambig = ("Smith", _uri("Q999"), "disambiguation page")
        real = _clean("Q2", "smith", "metalworker")
        fr = filter_candidates([disambig, real], term="smith")
        assert fr.survivors == [real]
        assert fr.top1_class == "disambig"
        assert fr.top1_class in NO_CONTEXT_DROP_CLASSES

    def test_all_junk_yields_empty_survivors(self):
        fr = filter_candidates([
            ("x", _uri("Q13406463"), "Wikimedia list article"),
            ("y", _uri("Q9"), "family name"),
        ], term="x")
        assert fr.survivors == []
        assert fr.top1_class == "meta"

    def test_empty_input(self):
        fr = filter_candidates([], term="nothing")
        assert fr.survivors == []
        assert fr.top1_class is None

    def test_empty_description_candidates_survive(self):
        cand = ("mystery", _uri("Q77"), "")
        fr = filter_candidates([cand], term="mystery")
        assert fr.survivors == [cand]
        assert fr.top1_class is None


# ---------------------------------------------------------------------------
# canonical META set — parity with the ancestor-path consumer
# ---------------------------------------------------------------------------

def test_meta_uris_derive_from_qids():
    assert WIKIMEDIA_META_URIS == frozenset(
        f"http://www.wikidata.org/entity/{q}" for q in WIKIMEDIA_META_QIDS
    )
    assert len(WIKIMEDIA_META_QIDS) == 6
