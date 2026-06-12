"""
stopwords.py

Single source for Coyote's stopword lists (Unit 3 M11). Consolidates the
custom list previously duplicated across text_bertopic_analysis.py,
text_ner_analysis.py, and coyote_nlp_state_manager.py.
"""

# Domain-specific stopwords: web-chrome vocabulary that pollutes topic
# and TF-IDF extraction from scraped pages.
CUSTOM_STOPWORDS = [
    'page', 'click', 'link', 'comment', 'username', 'password', 'login',
    'subscribe', 'share', 'like', 'read', 'more', 'article', 'posted', 'said'
]

try:
    from nltk.corpus import stopwords as _nltk_stopwords
    STOP_WORDS = list(set(_nltk_stopwords.words('english')).union(set(CUSTOM_STOPWORDS)))
except LookupError:
    import nltk
    nltk.download('stopwords')
    from nltk.corpus import stopwords as _nltk_stopwords
    STOP_WORDS = list(set(_nltk_stopwords.words('english')).union(set(CUSTOM_STOPWORDS)))
