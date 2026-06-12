"""
coyote_embedder.py

Singleton embedding service for Coyote Core.
Loads all-MiniLM-L6-v2 once at first call; subsequent calls reuse
the loaded model.

IMPORTANT: Model name defined in shared/embedding_config.py.
Both this module and chains.py must use the same model.
Changing the model requires rebuilding both images, recreating
vector indexes, and re-embedding all nodes. See CLAUDE.md.
"""

import logging
import math
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from shared.embedding_config import EMBEDDING_MODEL_NAME

from coyote.analysis.nlp.chunking import chunk_text

logger = logging.getLogger(__name__)

_model = None
_model_load_failed = False

# Cap on chunks pooled per document; bounds pathological pages
# (~25k tokens at MiniLM's 254-token chunks before it triggers).
MAX_CHUNKS = 100


def _get_model():
    global _model, _model_load_failed
    if _model_load_failed:
        return None
    if _model is None:
        try:
            from sentence_transformers import SentenceTransformer
            logger.info("Loading embedding model: %s", EMBEDDING_MODEL_NAME)
            _model = SentenceTransformer(EMBEDDING_MODEL_NAME)
            logger.info("Embedding model loaded.")
        except Exception:
            logger.exception(
                "Failed to load embedding model %s — "
                "embeddings will be null for this session",
                EMBEDDING_MODEL_NAME
            )
            _model_load_failed = True
            return None
    return _model


def get_model():
    """
    Public accessor for the shared SentenceTransformer singleton.
    Returns None if the model failed to load. Consumers (KeyBERT, chunking)
    must wrap this instance rather than loading a second copy.
    """
    return _get_model()


def _normalize(vec: List[float]) -> List[float]:
    norm = math.sqrt(sum(x * x for x in vec))
    return [x / max(norm, 1e-12) for x in vec]


def embed_document_with_text(text: str) -> Optional[Tuple[list, str]]:
    """
    Chunk-and-pool embedding for documents of any length.

    Chunks at the model's token budget, encodes each chunk, then
    L2-normalize -> mean-pool -> re-normalize (normalize-before-mean so
    high-norm chunks don't dominate the pool).

    Returns (embedding, embedded_text) or None on empty/failure.
    embedded_text is the exact text the embedding represents: the input
    verbatim, or the pooled-chunk prefix when MAX_CHUNKS truncates —
    callers persisting embedding_text must store this value, not their
    original input.
    """
    if not text or not text.strip():
        logger.debug("embed_document called with empty text; returning None")
        return None
    model = _get_model()
    if model is None:
        return None
    try:
        def count_tokens(t: str) -> int:
            # verbose=False: counting oversized text is expected here;
            # the tokenizer's max-length warning would spam the logs.
            return len(
                model.tokenizer(t, add_special_tokens=False, verbose=False)["input_ids"]
            )

        chunks = chunk_text(
            text,
            max_tokens=model.max_seq_length - 2,
            count_tokens=count_tokens,
        )
        if not chunks:
            return None
        truncated = len(chunks) > MAX_CHUNKS
        if truncated:
            logger.warning(
                "embed_document: %d chunks exceeds MAX_CHUNKS=%d; "
                "pooling the first %d only",
                len(chunks), MAX_CHUNKS, MAX_CHUNKS
            )
            chunks = chunks[:MAX_CHUNKS]
        encoded = model.encode(chunks, convert_to_numpy=True)
        vectors = [_normalize([float(x) for x in row]) for row in encoded]
        dim = len(vectors[0])
        mean = [sum(v[i] for v in vectors) / len(vectors) for i in range(dim)]
        if math.sqrt(sum(x * x for x in mean)) <= 1e-12:
            # Zero-norm pool has no direction; a zero embedding would be
            # meaningless in a cosine index. None is the null-embedding signal.
            logger.warning("embed_document: degenerate zero-norm pool; returning None")
            return None
        pooled = _normalize(mean)
        embedded_text = "\n\n".join(chunks) if truncated else text
        return pooled, embedded_text
    except Exception:
        logger.exception("embed_document failed; returning None")
        return None


def embed_document(text: str) -> Optional[list]:
    """
    Embed a document via chunk-and-pool. Returns List[float] or None on
    empty/failure, matching embed_text semantics. Use
    embed_document_with_text when the embedded text must be persisted.
    """
    result = embed_document_with_text(text)
    return None if result is None else result[0]


def embed_text(text: str) -> Optional[list]:
    """
    Embed a text string. Returns List[float] or None on failure.
    None is valid — callers treat it as a null embedding.
    """
    if not text or not text.strip():
        logger.debug("embed_text called with empty text; returning None")
        return None
    model = _get_model()
    if model is None:
        return None
    try:
        return model.encode(text, convert_to_numpy=True).tolist()
    except Exception:
        logger.exception("embed_text encode failed; returning None")
        return None


def build_webpage_embedding_text(
    title: str,
    summary: str,
    entity_texts: list,
    topic_labels: list
) -> str:
    """
    Assemble the structured text string for Webpage embedding.
    Entity NER label types (PERSON, ORG, etc.) are excluded —
    entity text only.
    The returned string is stored verbatim as embedding_text on the
    Neo4j node.
    """
    parts = []
    if title:
        parts.append(f"Title: {title}")
    if summary:
        parts.append(f"Summary: {summary}")
    if entity_texts:
        parts.append(f"Entities: {', '.join(entity_texts)}")
    if topic_labels:
        parts.append(f"Topics: {', '.join(topic_labels)}")
    return "\n".join(parts)


def build_annotation_embedding_text(
    annotation_text: str,
    highlighted_text: str,
    entity_texts: list
) -> str:
    """
    Assemble the structured text string for Annotation embedding.
    webpage_title is intentionally excluded.
    The returned string is stored verbatim as embedding_text on the
    Neo4j node.
    """
    parts = []
    if annotation_text:
        parts.append(f"Annotation: {annotation_text.strip()}")
    if highlighted_text:
        parts.append(f"Highlighted Text: {highlighted_text.strip()}")
    if entity_texts:
        parts.append(f"Entities: {', '.join(entity_texts)}")
    return "\n".join(parts)


def embedding_timestamp() -> str:
    """Return current UTC time as ISO string for embedding_generated_at."""
    return datetime.now(timezone.utc).isoformat()
