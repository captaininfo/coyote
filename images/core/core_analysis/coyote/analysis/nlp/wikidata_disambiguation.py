"""
wikidata_disambiguation.py

Context-aware re-ranking of wbsearchentities candidates (Unit 8 of the 0.5
refactor).

Unit 7 returns prominence-ranked candidate triples (label, uri, description)
from the Wikidata Action API. Prominence fixes the common ambiguities for free,
but not context-dependent ones where the globally-prominent sense is wrong for
THIS page — e.g. on a French-Revolution page "robespierre" ranks the surname
above the person, and "revolution" ranks the Nintendo Wii (codename
"Revolution") above the concept. This module re-ranks by the cosine similarity
of each candidate's DESCRIPTION to the page's pooled full-document embedding,
with a margin guard that protects prominence #1 against marginal flips and a
threshold below which nothing is mapped.

Two-part split for testability (the Unit 4/6 lesson):
  - score_candidates(...) is PURE and network-free — fixed-vector numeric
    selection, fully host-testable, no model and no I/O.
  - select_best_candidate(...) is the thin wrapper that embeds candidate
    descriptions (batched, URI-keyed in-process cache) and calls the pure
    selector. Its embedding step is exercised by the Gate 8.1 integration
    test, not by the pure unit tests.

WEBPAGE PATH ONLY: only the webpage NLP path has a pooled full-document context
embedding in scope. The map functions pass context_embedding=None on every
other event path (and on a webpage whose own embedding failed), in which case
the caller keeps prominence result[0] and never calls in here. The None guard
therefore lives in the caller; select_best_candidate is given a real embedding.
"""

import logging
import math
import os
from typing import List, Optional, Tuple

from coyote.coyote_embedder import embed_texts

logger = logging.getLogger(__name__)


def _read_float_env(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning("Invalid %s=%r; falling back to %s", name, raw, default)
        return default


# Cosine floor to accept the winning candidate; below it -> no mapping (None).
# Default 0.0 = near-lossless first deploy: it still drops anti-correlated
# (negative-cosine) winners, but otherwise re-ranks without dropping, so Gate
# 8.3 can measure the cosine distribution before the floor is tuned. A value
# < 0 (e.g. -1.0) makes the first pass truly lossless. This is a DIFFERENT
# similarity space than VECTOR_SIMILARITY_THRESHOLD (description<->page-context,
# not query<->doc) — its own tuning, do not borrow 0.40.
WIKIDATA_DISAMBIG_THRESHOLD = _read_float_env("WIKIDATA_DISAMBIG_THRESHOLD", 0.0)

# A non-prominence candidate must beat prominence #0's cosine by >= this margin
# to override it. 0.0 disables the guard (pure argmax re-rank); higher values
# make overrides harder. Protects already-correct prominence-#1 mappings (Gate
# 8.2) against marginal cosine flips.
WIKIDATA_DISAMBIG_MARGIN = _read_float_env("WIKIDATA_DISAMBIG_MARGIN", 0.05)


def _cosine(a: List[float], b: List[float]) -> float:
    """
    TRUE cosine similarity — required here, NOT a raw dot product. The page
    context embedding (pooled full-doc) is L2-normalized, but candidate
    description vectors come back from embed_texts UNNORMALIZED, so we must
    divide by both norms. Returns 0.0 if either vector is degenerate.
    """
    dot = na = nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    denom = math.sqrt(na) * math.sqrt(nb)
    if denom <= 0.0:
        return 0.0
    return dot / denom


def score_candidates(
    context_embedding: List[float],
    candidate_embeddings: List[Optional[List[float]]],
    threshold: float,
    margin: float,
) -> Optional[int]:
    """
    PURE, network-free selection over pre-computed embeddings.

    `candidate_embeddings` is aligned with the prominence-ranked candidate list
    (index 0 = prominence #1). An entry may be None (its description was empty
    or failed to embed); such candidates are skipped for scoring.

    Returns the index of the winning candidate, or None when nothing clears
    `threshold`.

    Selection — margin picks the winner, threshold gates it:
      1. true cosine for every candidate that has an embedding.
      2. winner = prominence #0, UNLESS some candidate beats #0's cosine by
         >= margin (then the highest-cosine such candidate). Exact ties resolve
         to the lower index, i.e. the more-prominent candidate -> deterministic.
      3. winner.cosine >= threshold ? winner : None.
    """
    if not candidate_embeddings or not context_embedding:
        return None

    scores: List[Optional[float]] = [
        _cosine(context_embedding, emb) if emb else None
        for emb in candidate_embeddings
    ]

    prom_score = scores[0]  # prominence #0 anchors the margin guard (may be None)

    # Best (highest-cosine) scored candidate. Strict ">" while scanning
    # low->high index keeps the lowest (most prominent) index on a tie.
    best_idx: Optional[int] = None
    best_score: Optional[float] = None
    for i, s in enumerate(scores):
        if s is None:
            continue
        if best_score is None or s > best_score:
            best_score, best_idx = s, i

    if best_idx is None:
        return None  # no candidate had an embedding -> cannot disambiguate

    # Margin guard.
    if prom_score is None:
        # Prominence #0 unscoreable -> no anchor; take the best scored.
        winner_idx, winner_score = best_idx, best_score
    elif best_idx == 0 or (best_score - prom_score) < margin:
        winner_idx, winner_score = 0, prom_score
    else:
        winner_idx, winner_score = best_idx, best_score

    if winner_score is None or winner_score < threshold:
        return None
    return winner_idx


# URI(QID)-keyed in-process cache of description embeddings. Keyed by QID (not
# term or description string) to maximize reuse across terms and pages within a
# process. Acknowledged MVP tech debt: in-process only, so it dies on restart —
# fine for the single-threaded NLP drain, and a model swap (which forces a
# restart) invalidates it for free. Post-MVP upgrade: a SQLite table in
# wikidata_cache.db whose key ALSO includes the embedding-model name.
_DESC_EMBED_CACHE: dict = {}


def _qid_from_uri(uri: str) -> str:
    return uri.rsplit("/", 1)[-1] if uri else uri


def select_best_candidate(
    context_embedding: Optional[List[float]],
    candidates: List[Tuple[str, str, str]],
    threshold: float = WIKIDATA_DISAMBIG_THRESHOLD,
    margin: float = WIKIDATA_DISAMBIG_MARGIN,
    term: str = "",
) -> Optional[Tuple[str, str]]:
    """
    Re-rank Unit 7's (label, uri, description) candidates against the page
    context embedding and return the winning (label, uri), or None for "no
    mapping".

    `context_embedding` is expected non-None — the None guard lives in the
    caller (the map functions): non-webpage paths and embedding-failed webpages
    keep prominence result[0] and never reach here. If somehow empty, we fall
    back to prominence #0 rather than crash (defensive, not a contract).

    `term` is carried for the DEBUG decline log only.
    """
    if not candidates:
        return None
    if not context_embedding:
        label, uri, _ = candidates[0]
        return (label, uri)

    qids = [_qid_from_uri(uri) for (_, uri, _) in candidates]

    # Embed cache-miss descriptions in ONE batched encode; populate the cache.
    miss_idx: List[int] = []
    miss_text: List[str] = []
    for i, (qid, (_, _, desc)) in enumerate(zip(qids, candidates)):
        if qid in _DESC_EMBED_CACHE:
            continue
        if not desc or not desc.strip():
            _DESC_EMBED_CACHE[qid] = None  # no description -> unscoreable
            continue
        miss_idx.append(i)
        miss_text.append(desc)
    if miss_text:
        vectors = embed_texts(miss_text)
        for i, vec in zip(miss_idx, vectors):
            _DESC_EMBED_CACHE[qids[i]] = vec

    candidate_embeddings = [_DESC_EMBED_CACHE.get(qid) for qid in qids]

    winner = score_candidates(
        context_embedding, candidate_embeddings, threshold, margin
    )

    if winner is None:
        if logger.isEnabledFor(logging.DEBUG):
            best = max(
                (_cosine(context_embedding, e) for e in candidate_embeddings if e),
                default=None,
            )
            top_label, top_uri, _ = candidates[0]
            logger.debug(
                "WikiData disambig DECLINED %r (prominence #1 %s/%s): best cosine "
                "%s < threshold %.3f -> no mapping",
                term, top_uri, top_label,
                ("%.3f" % best) if best is not None else "n/a",
                threshold,
            )
        return None

    label, uri, _ = candidates[winner]
    return (label, uri)
