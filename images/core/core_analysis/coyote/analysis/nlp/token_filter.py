"""
token_filter.py

Token-quality filtering and entity mapping-cardinality control (Unit 6 of
the 0.5 refactor).

Two responsibilities:
  (A) Quality filter — drop junk tokens (single-char, pure-numeric,
      trailing-dot abbreviations like "pp.", citation fragments, lone bound
      morphemes, stopword-only phrases, non-alpha-dominant strings) from
      both the topic and entity paths, BEFORE storage.
  (B) Mapping floor — among surviving webpage entities, select only those
      mentioned >= ENTITY_MAP_MENTION_FLOOR times for WikiData mapping, with
      an optional (disabled-by-default) per-page top-K cap. This is the
      cardinality control that keeps Unit 7's Action-API request volume
      rate-safe. Applied to the WEBPAGE path only — the other event paths
      are low-volume and high-value, where a floor would suppress
      legitimate single-mention entities.

Pure and network-free (mirrors entity_scoring.py): string predicates and
collections.Counter only — no spaCy load, no DB, no network — fully host
testable. STOP_WORDS is imported from the hardened stopwords module, whose
import is itself network-free.
"""

import logging
import os
import re
from collections import Counter, defaultdict
from typing import List, Optional, Tuple

from coyote.analysis.nlp.stopwords import STOP_WORDS

logger = logging.getLogger(__name__)

# Pre-folded stopword set for O(1) membership on the all-tokens-stopword rule.
_STOP_WORDS_FOLDED = frozenset(w.casefold() for w in STOP_WORDS)

# NER labels whose entities are numeric/temporal noise — dropped entirely
# (Unit 6 decision: Neo4j is THE repository, no store-unmapped).
_DROP_NER_LABELS = frozenset({
    "CARDINAL", "ORDINAL", "QUANTITY", "PERCENT", "MONEY", "TIME", "DATE",
})

# Lone bound morphemes that surface as junk tokens.
_BOUND_MORPHEMES = frozenset({
    "pre", "anti", "non", "sub", "pro", "neo", "post", "semi", "pseudo",
    "quasi",
})

# Trailing-dot abbreviation: 1-3 letters + a single terminal dot ("pp.",
# "ed.", "vol."). "U.S." has a mid-string dot and does NOT match.
_ABBREV_RE = re.compile(r"^[A-Za-z]{1,3}\.$")

# Numeric-ish: only digits and numeric punctuation/space. Paired with a
# has-digit guard so a lone "." or "-" is not treated as numeric.
_NUMERICISH_RE = re.compile(r"^[\d.,%/\-\s]+$")


def _env_optional_int(name: str, fallback: Optional[int]) -> Optional[int]:
    """Parse an optional positive int from the environment.

    Unset / blank / unparseable / <= 0 all mean "disabled" (returns the
    fallback, normally None). A positive int enables the feature.
    """
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return fallback
    try:
        value = int(raw)
    except ValueError:
        logger.warning("Invalid int %r for %s; treating as disabled", raw, name)
        return fallback
    if value <= 0:
        return fallback
    return value


ENTITY_MAP_MENTION_FLOOR = int(os.environ.get("ENTITY_MAP_MENTION_FLOOR", "2"))
ENTITY_MAP_CAP = _env_optional_int("ENTITY_MAP_CAP", None)


def _alpha_fraction(s: str) -> float:
    """Fraction of NON-WHITESPACE characters that are alphabetic."""
    non_ws = [c for c in s if not c.isspace()]
    if not non_ws:
        return 0.0
    return sum(c.isalpha() for c in non_ws) / len(non_ws)


def is_quality_token(phrase: str) -> bool:
    """Return True to KEEP the token, False to DROP it.

    Drops, cheap-to-expensive: None/empty/whitespace; single-char;
    pure-numeric; trailing-dot abbreviation; citation fragment; lone bound
    morpheme; all-tokens-stopword; non-alpha-dominant.
    """
    if phrase is None:
        return False
    stripped = phrase.strip()
    if not stripped:
        return False
    if len(stripped) == 1:
        return False
    folded = stripped.casefold()
    # pure-numeric (only numeric chars AND at least one digit): "978", "1789"
    if _NUMERICISH_RE.match(stripped) and any(c.isdigit() for c in stripped):
        return False
    # trailing-dot abbreviation: "pp.", "ed.", "vol."
    if _ABBREV_RE.match(stripped):
        return False
    # citation fragment: "cite web", "cite news", ...
    if folded.startswith("cite "):
        return False
    # lone bound morpheme: "pre", "anti", ...
    if folded in _BOUND_MORPHEMES:
        return False
    # all whitespace-split tokens are stopwords: "more", "read more"
    tokens = folded.split()
    if tokens and all(t in _STOP_WORDS_FOLDED for t in tokens):
        return False
    # non-alpha-dominant: "!!!", "$$$", "---"
    if _alpha_fraction(stripped) < 0.5:
        return False
    return True


def filter_topics(
    topics: List[Tuple[str, float]],
) -> List[Tuple[str, float]]:
    """Drop junk-phrase topics; preserve (phrase, score) shape and order."""
    return [t for t in topics if is_quality_token(t[0])]


def filter_entities(
    entities: List[Tuple[str, str]],
) -> List[Tuple[str, str]]:
    """Drop numeric/date NER labels and junk-phrase entities.

    Preserves (text, ner_label) shape and order — one tuple per mention.
    """
    out = []
    for text, ner_label in entities:
        if ner_label in _DROP_NER_LABELS:
            continue
        if not is_quality_token(text):
            continue
        out.append((text, ner_label))
    return out


def select_mapping_entities(
    entities: List[Tuple[str, str]],
    floor: int = ENTITY_MAP_MENTION_FLOOR,
    cap: Optional[int] = ENTITY_MAP_CAP,
) -> List[str]:
    """Select which entities to map to WikiData (webpage path only).

    Args:
        entities: post-filter (text, ner_label) tuples, one per mention.
        floor: minimum case-folded mention count required to be mapped.
        cap: optional per-page top-K cap (None = disabled, the default).

    Returns the ORIGINAL-CASE surface forms of the selected entities,
    deterministically ordered (mention count desc, then case-folded asc).

    Surface case is preserved deliberately, not cosmetically: Step 17's SQLite
    `UPDATE Entities ... WHERE entity=?` (in coyote_nlp_state_manager) matches
    case-SENSITIVELY and must hit the row inserted at Step 15, so the mapped
    surface form has to be byte-identical to the stored one. (The WikiData
    lookup itself is now the Action API and case-invariant — Unit 7/8 — so the
    *lookup* no longer needs the case; the SQLite storage round-trip does.)
    Two passes: count per case-folded key, and collect surface forms per key so
    a representative (most-frequent casing, tie-broken alpha) can be returned.
    """
    if not entities:
        return []

    counts: Counter = Counter()
    surfaces = defaultdict(Counter)
    for text, _label in entities:
        key = text.casefold()
        counts[key] += 1
        surfaces[key][text] += 1

    kept = [k for k, c in counts.items() if c >= floor]
    kept.sort(key=lambda k: (-counts[k], k))
    if cap is not None:
        kept = kept[:cap]

    # representative surface form per key: most frequent casing, tie -> alpha
    return [
        min(surfaces[k].items(), key=lambda s: (-s[1], s[0]))[0]
        for k in kept
    ]
