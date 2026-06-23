"""
stopwords.py

Single source for Coyote's stopword lists (Unit 3 M11). Consolidates the
custom list previously duplicated across text_bertopic_analysis.py,
text_ner_analysis.py, and coyote_nlp_state_manager.py.

Import hardened to be network-free (Unit 6): the nltk English corpus is
pre-baked into the core image (images/core/Dockerfile:46) and nltk is
pinned, so the full union list always loads in the container. If nltk or
the corpus is unavailable (a bare host / CI), the import degrades to
CUSTOM_STOPWORDS only with a logged warning rather than calling
nltk.download() — keeping this module import-safe AND network-free
everywhere (the Unit 4 testability lesson). nltk corpus loading is lazy, so
the missing-corpus LookupError is raised by .words('english'), not the
import; both it and a missing-nltk ImportError are caught here.
"""

import logging

logger = logging.getLogger(__name__)

# Domain-specific stopwords: web-chrome vocabulary that pollutes topic
# and TF-IDF extraction from scraped pages.
CUSTOM_STOPWORDS = [
    'page', 'click', 'link', 'comment', 'username', 'password', 'login',
    'subscribe', 'share', 'like', 'read', 'more', 'article', 'posted', 'said'
]

try:
    from nltk.corpus import stopwords as _nltk_stopwords
    STOP_WORDS = list(set(_nltk_stopwords.words('english')).union(set(CUSTOM_STOPWORDS)))
except (ImportError, LookupError) as exc:
    logger.warning(
        "nltk English stopwords unavailable (%s: %s); degrading to "
        "CUSTOM_STOPWORDS only. The core image pre-bakes the corpus, so "
        "this fallback should only ever fire on a bare host/CI.",
        type(exc).__name__, exc,
    )
    STOP_WORDS = list(CUSTOM_STOPWORDS)
