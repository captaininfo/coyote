"""
wikidata_candidate_filter.py

A1 junk-candidate filter (Track A, 2026-07-08 — plan Fable-ratified).

Pure, stdlib-only, zero-network. Applied at the NLP mapping stage in both map
functions (map_topics_to_wikidata / map_ner_to_wikidata), AFTER query_wikidata
returns the K=7 prominence-ranked (label, uri, description) candidates and
BEFORE any selection (Unit 8 re-rank or prominence top-1). Junk candidates
therefore never enter the Webpage.topics/entities JSON record or the graph.
The wikidata_term_cache is NEVER filtered — it stores raw Action-API ground
truth, so pattern updates here take effect immediately on cached terms.

Candidate classes carry different evidence about the TERM, so they get
different term-level semantics (applied by the callers via `top1_class`):

  - "meta"        — Wikimedia-infrastructure / scholarly-article boilerplate
                    ("Wikimedia list article", "scholarly article published
                    in..."). A meta #1 says nothing about the term; the
                    candidate is dropped and the next survivor may be a
                    perfectly good mapping (fallback allowed).
  - "name_marker" — bare-name items ("family name", "given name", "surname").
                    A name-marker #1 is strong evidence the term IS a bare
                    name: with a context embedding, Unit 8 may still pick the
                    right person from the survivors (Dewey -> John Dewey); with
                    no context, callers must DROP THE TERM — blind fallback to
                    #2 would promote an arbitrary prominent person named Wang,
                    converting flaggable name-junk into unflaggable
                    wrong-person junk.
  - "disambig"    — disambiguation pages. Name-marker-grade ambiguity evidence
                    about the term, so it shares the no-context term-drop
                    semantics (see NO_CONTEXT_DROP_CLASSES).

The #1-only keying of the term-drop is deliberate and measured (Fable F5): a
junk candidate ANYWHERE in top-K is common for legitimate terms (AI, Africa,
Berlin, Darwin... all carry a surname item somewhere in top-7) — those are
merely filtered from the survivor list; only a junk #1 speaks about the term.

Matching is word-boundary regex on the lowercased description — never bare
substring (the measure_a4.py A2 lesson: "linguist" must not fire inside
"linguistics", "author" must not fire inside "authorities").

This module is also the CANONICAL home of the Wikimedia meta Q-item set
(moved from connect_to_ontology, which now imports it from here — direction
neo4j_integration -> analysis.nlp, acyclic since this module imports nothing
from coyote). The QID blocklist catches meta items even when their
description is empty or oddly worded, and closes the Gate B direct-path hole
(Q4167410 landed as a direct concept in the A4 baseline because the old
META filter guarded only the WDQS ancestor walk).
"""

import logging
import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)


# Wikimedia infrastructure Q-items. Canonical set (moved here from
# connect_to_ontology in Track A commit 2). Consumed two ways:
#   1. here, as an exact-QID blocklist on mapping candidates (direct path);
#   2. in connect_to_ontology's ancestor-walk post-filter (defense-in-depth:
#      A1 stops meta QIDs entering the record; the ancestor filter stops WDQS
#      traversal re-introducing them as parents).
WIKIMEDIA_META_QIDS = frozenset({
    "Q4167836",   # Wikimedia category
    "Q15184295",  # Wikimedia administration category
    "Q4167410",   # Wikimedia disambiguation page
    "Q14204246",  # Wikimedia project page
    "Q11266439",  # Wikimedia template
    "Q13406463",  # Wikimedia list article
})
WIKIMEDIA_META_URIS = frozenset(
    f"http://www.wikidata.org/entity/{q}" for q in WIKIMEDIA_META_QIDS
)

# Q4167410 is ambiguity evidence about the term (class "disambig"), not mere
# meta noise; the other five stay class "meta".
_DISAMBIG_QID = "Q4167410"

# Production description patterns = exactly the A4 REGISTERED PRIMARY set
# (plus the name markers below). The A4 *secondary* set (doctoral thesis,
# episode of, preprint, ...) is deliberately excluded: it bought only +1.3%
# in A4 and carries the highest false-positive risk — a specific book, paper,
# or episode can be a genuine object of study in a learning record. Keeping
# production == registered also keeps Gate A5 comparable to the A4 numbers.
_META_PATTERNS = (
    "scholarly article",
    "scientific article",
    "article published in",
    "research article",
    "academic journal",
    # Gate A1.0 adjudication (2026-07-08): the registered bare "peer-reviewed"
    # flagged the Stanford Encyclopedia of Philosophy ("peer-reviewed website"),
    # a genuinely high-value resource on this philosophy-heavy corpus. Narrowed
    # to the journal phrasings per Fable's pre-registered F4 remedy — still
    # catches British Journal of Haematology / Diabetes Care ("peer-reviewed
    # scientific journal"), no other losses. SEP therefore becomes a documented
    # A5 "expected survivor": measure_a4.py measures with the REGISTERED bare
    # pattern, so it will still flag SEP while production keeps it.
    "peer-reviewed journal",
    "peer-reviewed scientific journal",
    "wikimedia",
    "wikipedia",
    "category",
    "template",
    "list of",
)
_NAME_MARKER_PATTERNS = (
    "family name",   # word-boundary still catches "Chinese family name" etc.
    "given name",    # also catches "female/male/unisex given name"
    "surname",
)
_DISAMBIG_PATTERNS = (
    "disambiguation",
)


def _compile(patterns: Tuple[str, ...]) -> List[Tuple[str, "re.Pattern"]]:
    return [
        (p, re.compile(r"\b" + re.escape(p) + r"\b"))
        for p in patterns
    ]


_META_RES = _compile(_META_PATTERNS)
_NAME_MARKER_RES = _compile(_NAME_MARKER_PATTERNS)
_DISAMBIG_RES = _compile(_DISAMBIG_PATTERNS)

# Classes whose top-1 means "drop the term" on the no-context paths
# (annotation/search/hyperlink, or a webpage whose own embedding failed).
# Callers own that decision; this set defines its scope.
NO_CONTEXT_DROP_CLASSES = frozenset({"name_marker", "disambig"})


@dataclass(frozen=True)
class FilterResult:
    """
    survivors:  (label, uri, description) triples with junk candidates
                removed, prominence order preserved. May be empty.
    top1_class: class of the ORIGINAL prominence #1 candidate — None (clean)
                or "meta" / "name_marker" / "disambig". Callers on no-context
                paths must drop the term when this is in
                NO_CONTEXT_DROP_CLASSES.
    """
    survivors: List[Tuple[str, str, str]]
    top1_class: Optional[str]


def _qid_from_uri(uri: str) -> str:
    return uri.rsplit("/", 1)[-1] if uri else uri


def classify_candidate(uri: str, description: str) -> Optional[Tuple[str, str]]:
    """
    Classify one candidate. Returns (class, reason) for junk, None for clean.

    Precedence: exact QID blocklist first (works on empty descriptions),
    then description patterns — name_marker before disambig before meta,
    most-specific term-evidence first (a "surname disambiguation page"
    description is name evidence, not mere meta noise).
    """
    qid = _qid_from_uri(uri)
    if qid in WIKIMEDIA_META_QIDS:
        cls = "disambig" if qid == _DISAMBIG_QID else "meta"
        return (cls, f"qid:{qid}")

    if not description:
        return None
    desc = description.lower()
    for patterns, cls in (
        (_NAME_MARKER_RES, "name_marker"),
        (_DISAMBIG_RES, "disambig"),
        (_META_RES, "meta"),
    ):
        for text, rx in patterns:
            if rx.search(desc):
                return (cls, f"pattern:{text}")
    return None


def filter_candidates(
    candidates: List[Tuple[str, str, str]],
    term: str = "",
) -> FilterResult:
    """
    Filter a prominence-ranked (label, uri, description) candidate list.

    Junk candidates at ANY position are removed from `survivors`; the class
    of the ORIGINAL #1 is reported as `top1_class` so callers can apply the
    term-level semantics (see module docstring / NO_CONTEXT_DROP_CLASSES).

    `term` is carried for the DEBUG drop log only (forensics parity with
    Unit 8's decline log).
    """
    survivors: List[Tuple[str, str, str]] = []
    top1_class: Optional[str] = None

    for i, cand in enumerate(candidates):
        label, uri, description = cand
        verdict = classify_candidate(uri, description)
        if verdict is None:
            survivors.append(cand)
            continue
        cls, reason = verdict
        if i == 0:
            top1_class = cls
        if logger.isEnabledFor(logging.DEBUG):
            logger.debug(
                "A1 filtered candidate for term %r: #%d %s/%s class=%s (%s)",
                term, i, _qid_from_uri(uri), label, cls, reason,
            )

    return FilterResult(survivors=survivors, top1_class=top1_class)
