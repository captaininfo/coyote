# summarize_text.py
#
# Extractive lead-sentence summarizer (no ML dependencies).
# TODO: Replace with Ollama LLM call for higher-quality summaries post-MVP.

import logging
import re

_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")


def summarize_text(text, min_length=60, max_length=500):
    """Return the first few sentences of *text* that fit within max_length."""
    try:
        if not text:
            return ""
        text = " ".join(text.split())  # collapse whitespace
        if len(text) <= max_length:
            return text

        sentences = _SENTENCE_RE.split(text)
        summary = ""
        for s in sentences:
            candidate = (summary + " " + s).strip() if summary else s
            if len(candidate) > max_length:
                break
            summary = candidate

        # If the first sentence alone exceeds max_length, truncate at word boundary
        if not summary:
            summary = text[:max_length].rsplit(" ", 1)[0] + " ..."

        # Ensure we meet min_length when possible
        if len(summary) < min_length and len(text) > min_length:
            summary = text[:max_length].rsplit(" ", 1)[0] + " ..."

        return summary
    except Exception as e:
        logging.error("Error during summarization: %s", e)
        return ""
