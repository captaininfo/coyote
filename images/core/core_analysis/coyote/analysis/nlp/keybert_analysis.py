"""
keybert_analysis.py

KeyBERT topic extraction with spaCy noun_chunks candidates (Unit 3c of
the 0.5 refactor). Replaces the BERTopic path.

Implementation contract (verified by pre-flights 1 and 2; see
MVP_REFACTOR_PLAN.md Unit 3c):
- Candidates come from spaCy noun_chunks, pre-computed ONCE per document
  from the caller-supplied parse. The CountVectorizer uses
  analyzer=<callable closing over that set> — NEVER tokenizer= (sklearn
  would re-form n-grams across boundaries) and NEVER KeyBERT's
  candidates= (0.9.0 silently re-tokenizes multi-word strings via the
  default token_pattern, dropping them).
- KeyBERT wraps the shared SentenceTransformer singleton from
  coyote_embedder.get_model(); it embeds candidate phrases even when the
  document embedding is precomputed, so it needs the model — but never a
  second copy.
- Scores are raw cosine similarity between candidate and document
  embeddings (MMR's diversity objective is used only for selection).

Heavy imports (keybert, sklearn, numpy) are deferred to call time so the
module imports cleanly where the ML stack is absent.
"""
import logging
import os
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

_keybert = None
_keybert_failed = False


def _env_float(name: str, fallback: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return fallback
    try:
        return float(raw)
    except ValueError:
        logger.warning(
            "Invalid float %r for %s; using default %s", raw, name, fallback
        )
        return fallback


DEFAULT_MMR_LAMBDA = _env_float("KEYBERT_MMR_LAMBDA", 0.6)


def _get_keybert():
    """Lazy module-level KeyBERT singleton wrapping the shared embedder."""
    global _keybert, _keybert_failed
    if _keybert_failed:
        return None
    if _keybert is None:
        try:
            from keybert import KeyBERT

            from coyote.coyote_embedder import get_model
            model = get_model()
            if model is None:
                # Embedder latches its own failure state; don't latch here
                # so a later successful model load can still serve us.
                return None
            _keybert = KeyBERT(model=model)
            logger.info("KeyBERT initialized around the shared embedding model.")
        except Exception:
            logger.exception(
                "Failed to initialize KeyBERT — topic extraction disabled "
                "for this session"
            )
            _keybert_failed = True
            return None
    return _keybert


def _reset_for_tests() -> None:
    """Reset singleton state between tests that stub the model."""
    global _keybert, _keybert_failed
    _keybert = None
    _keybert_failed = False


def _noun_chunk_candidates(doc) -> List[str]:
    """
    Candidate phrases from the dependency parse: noun_chunks with leading
    determiners/pronouns stripped, alpha tokens only, 1-4 tokens,
    lowercased and de-duplicated.
    """
    candidates = []
    seen = set()
    for chunk in doc.noun_chunks:
        tokens = [t for t in chunk if t.pos_ not in ("DET", "PRON")]
        if not tokens or len(tokens) > 4:
            continue
        if not all(t.is_alpha for t in tokens):
            continue
        phrase = " ".join(t.lower_ for t in tokens)
        if phrase not in seen:
            seen.add(phrase)
            candidates.append(phrase)
    return candidates


def extract_keywords(
    text: str,
    doc_embedding: Optional[list],
    nlp,
    top_n: int = 20,
    mmr_lambda: float = DEFAULT_MMR_LAMBDA,
) -> List[Tuple[str, float]]:
    """
    Extract up to top_n keyword phrases from RAW text (never
    stopword-stripped — the dependency parse needs grammatical text).

    Args:
        text: Raw document text.
        doc_embedding: Precomputed document embedding from
            coyote_embedder.embed_document (list of floats), or None.
        nlp: The caller-owned spaCy Language instance (full pipeline —
            noun_chunks needs the parser).
        top_n: Maximum phrases to return.
        mmr_lambda: MMR diversity (KEYBERT_MMR_LAMBDA env default).

    Returns:
        List of (phrase, cosine_score) tuples; [] on any unavailability
        (no text, no embedding, no model, no candidates).
    """
    if not text or not text.strip():
        return []
    if doc_embedding is None:
        logger.info("extract_keywords: no document embedding; returning []")
        return []
    if nlp is None:
        logger.info("extract_keywords: no spaCy instance; returning []")
        return []
    keybert_model = _get_keybert()
    if keybert_model is None:
        logger.info("extract_keywords: KeyBERT unavailable; returning []")
        return []

    if len(text) > nlp.max_length:
        logger.warning(
            "extract_keywords: text length %d exceeds nlp.max_length %d; "
            "truncating", len(text), nlp.max_length
        )
        text = text[: nlp.max_length]

    doc = nlp(text)
    candidates = _noun_chunk_candidates(doc)
    if not candidates:
        logger.info("extract_keywords: no noun-chunk candidates; returning []")
        return []

    try:
        import numpy as np
        from sklearn.feature_extraction.text import CountVectorizer

        vectorizer = CountVectorizer(analyzer=lambda _doc: candidates)
        embeddings = np.asarray(doc_embedding, dtype=np.float32).reshape(1, -1)
        results = keybert_model.extract_keywords(
            text,
            vectorizer=vectorizer,
            doc_embeddings=embeddings,
            use_mmr=True,
            diversity=mmr_lambda,
            top_n=top_n,
        )
        return [(phrase, float(score)) for phrase, score in results]
    except Exception:
        logger.exception("extract_keywords: KeyBERT extraction failed")
        return []
