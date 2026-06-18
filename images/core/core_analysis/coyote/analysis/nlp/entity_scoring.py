"""
entity_scoring.py

Mention-frequency scoring for named entities (Unit 4 of the 0.5 refactor).
Replaces the TED-Talk-corpus TF-IDF entity scoring (deleted with
text_bertopic_analysis.py): that path ran TfidfVectorizer against a
wrong-domain reference corpus that is empty in practice, producing a
degenerate IDF. Entity importance on a single page is far more honestly
captured by how often the entity is mentioned.

The score is a pure function of the per-entity mention count (and, for the
freq_normalized variant, the page's total mention count). The formula is
selected at module load via NER_SCORE_FORMULA, mirroring the
TOPIC_SCORE_THRESHOLD env-read pattern in connect_to_ontology.py.
"""

import logging
import math
import os

logger = logging.getLogger(__name__)

_VALID_FORMULAS = ("log", "freq_normalized", "saturated")

# Saturation constant for the "saturated" formula. Hard-coded (Unit 4
# decision): count/(count+k) with k=1.0 gives count=1 -> 0.5, asymptote 1.0.
# Promoted to an env var only if empirical tuning ever calls for it.
_SATURATION_K = 1.0


def _read_formula_env() -> str:
    raw = os.environ.get("NER_SCORE_FORMULA", "log")
    if raw not in _VALID_FORMULAS:
        logger.warning(
            "Invalid NER_SCORE_FORMULA=%r; falling back to 'log'", raw
        )
        return "log"
    return raw


NER_SCORE_FORMULA = _read_formula_env()


def mention_frequency_score(
    count: int,
    formula: str = "log",
    total: int = None,
    k: float = _SATURATION_K,
) -> float:
    """
    Score a named entity from its mention count.

    Args:
        count: Number of recognized mentions of this entity on the page
            (>= 1 in the production call path).
        formula: One of "log" (default), "freq_normalized", "saturated".
            Unrecognized values fall back to "log".
        total: Total recognized mentions across all entities on the page.
            Required only by "freq_normalized" (the denominator); ignored
            otherwise. Guards against a zero/None denominator -> 0.0.
        k: Saturation constant for "saturated".

    Returns:
        float: The entity score. Strictly positive for count >= 1 under
        "log" and "saturated"; in [0, 1] for "freq_normalized".
    """
    if formula == "freq_normalized":
        return count / total if total else 0.0
    if formula == "saturated":
        return count / (count + k)
    # "log" (default and fallback)
    return math.log1p(count)
