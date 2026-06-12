"""
chunking.py

Generic text chunking for embedding pipelines (Unit 3a of the 0.5 refactor).

Stdlib-only by design: coyote_embedder calls into this module, so it must
not import the embedder, spaCy, or any model stack at module level. Token
counting is injected by the caller (production passes the embedder's
wordpiece counter); the default whitespace counter is a rough fallback for
callers without a tokenizer.

Packing accounts tokens as the sum of unit counts. This assumes the
counter is whitespace-stable (count(a + sep + b) == count(a) + count(b)
for whitespace separators), which holds for BERT-style wordpiece
tokenizers and the whitespace default.
"""
import re
from typing import Callable, List, Optional

# Same pattern as coyote/analysis/summarize_text.py
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")
_PARAGRAPH_RE = re.compile(r"\n\s*\n")

_BOUNDARIES = ("paragraph_aware",)

_CHUNK_SEPARATOR = "\n\n"


def _default_count_tokens(text: str) -> int:
    return len(text.split())


def _embedder_max_tokens() -> int:
    # Lazy import: coyote_embedder imports this module, so a top-level
    # import here would be circular.
    from coyote.coyote_embedder import _get_model
    model = _get_model()
    if model is None:
        raise RuntimeError(
            "max_tokens not provided and the embedding model is "
            "unavailable; pass max_tokens explicitly"
        )
    return model.max_seq_length - 2


def _split_oversized(
    text: str,
    max_tokens: int,
    count_tokens: Callable[[str], int],
) -> List[str]:
    """Hard-split text that exceeds max_tokens even as a single sentence."""
    if count_tokens(text) <= max_tokens:
        return [text]
    words = text.split()
    if len(words) > 1:
        pieces: List[str] = []
        cur: List[str] = []
        for word in words:
            if cur and count_tokens(" ".join(cur + [word])) > max_tokens:
                pieces.append(" ".join(cur))
                cur = []
            cur.append(word)
        if cur:
            pieces.append(" ".join(cur))
        result: List[str] = []
        for piece in pieces:
            if count_tokens(piece) > max_tokens:
                result.extend(_split_oversized(piece, max_tokens, count_tokens))
            else:
                result.append(piece)
        return result
    # Single word over the limit: bisect by characters. Length-1 pieces
    # are returned as-is so progress is guaranteed.
    if len(text) <= 1:
        return [text]
    mid = len(text) // 2
    return (
        _split_oversized(text[:mid], max_tokens, count_tokens)
        + _split_oversized(text[mid:], max_tokens, count_tokens)
    )


def chunk_text(
    text: str,
    max_tokens: Optional[int] = None,
    boundary: str = "paragraph_aware",
    count_tokens: Optional[Callable[[str], int]] = None,
) -> List[str]:
    """
    Split text into chunks of at most max_tokens tokens each.

    Greedy paragraph packing: paragraphs are kept intact and packed into
    chunks until the budget is reached. A paragraph exceeding max_tokens
    falls back to sentence units; a sentence exceeding max_tokens is
    hard-split as a last resort. Never returns empty chunks; empty or
    whitespace-only input returns [].

    Args:
        text: The text to chunk.
        max_tokens: Token budget per chunk. When None, derived from the
            active embedder's max_seq_length (minus special-token margin).
        boundary: Chunking strategy. Only "paragraph_aware" is implemented;
            the parameter exists so post-MVP section-retrieval can add
            strategies without an API change.
        count_tokens: Token counter. Defaults to whitespace word count;
            production passes the embedder's wordpiece counter.
    """
    if boundary not in _BOUNDARIES:
        raise ValueError(
            f"Unknown boundary strategy {boundary!r}; expected one of {_BOUNDARIES}"
        )
    if count_tokens is None:
        count_tokens = _default_count_tokens
    if max_tokens is None:
        max_tokens = _embedder_max_tokens()
    if max_tokens < 1:
        raise ValueError(f"max_tokens must be >= 1, got {max_tokens}")
    if not text or not text.strip():
        return []

    units: List[str] = []
    for para in (p.strip() for p in _PARAGRAPH_RE.split(text)):
        if not para:
            continue
        if count_tokens(para) <= max_tokens:
            units.append(para)
            continue
        for sent in (s.strip() for s in _SENTENCE_RE.split(para)):
            if not sent:
                continue
            if count_tokens(sent) <= max_tokens:
                units.append(sent)
            else:
                units.extend(_split_oversized(sent, max_tokens, count_tokens))

    chunks: List[str] = []
    cur: List[str] = []
    cur_tokens = 0
    for unit in units:
        unit_tokens = count_tokens(unit)
        if cur and cur_tokens + unit_tokens > max_tokens:
            chunks.append(_CHUNK_SEPARATOR.join(cur))
            cur = []
            cur_tokens = 0
        cur.append(unit)
        cur_tokens += unit_tokens
    if cur:
        chunks.append(_CHUNK_SEPARATOR.join(cur))
    return chunks
