"""
text_bertopic_analysis.py

Module for extracting topics from text using BERTopic and TF-IDF analysis,
and mapping them to WikiData entities.
"""

import logging, json, os, re, sqlite3, threading
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple
from urllib.error import HTTPError

import spacy
from nltk.corpus import stopwords
from sklearn.feature_extraction.text import TfidfVectorizer
from SPARQLWrapper import SPARQLWrapper, JSON, SPARQLExceptions
import time, random

from coyote.utils.config_container import WIKIDATA_CACHE_DB_FILE
from coyote.analysis.nlp.bertopic_analysis import analyze_topics

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
BACKOFF     = (1.0, 3.0)   # seconds

# --- WikiData circuit breaker -------------------------------------------------
# Trips on 403/429 from query.wikidata.org. Once tripped, query_wikidata()
# short-circuits to [] without making SPARQL calls until the cooldown expires.
# A failed probe in the half_open state immediately re-trips.
_BREAKER_FAILURE_THRESHOLD = int(os.environ.get("WIKIDATA_BREAKER_THRESHOLD", "1"))
_BREAKER_COOLDOWN_SECONDS  = int(os.environ.get("WIKIDATA_BREAKER_COOLDOWN", "1800"))
_BREAKER_RETRY_AFTER_CAP   = 3600  # bound server-supplied cooldowns

_BREAKER_STATE: str = "closed"  # "closed" | "open" | "half_open"
_BREAKER_CONSECUTIVE_FAILURES: int = 0
_BREAKER_OPEN_UNTIL: Optional[datetime] = None
_BREAKER_LOCK = threading.Lock()


def _breaker_check_state() -> str:
    """Return effective state; transitions open→half_open if cooldown expired."""
    global _BREAKER_STATE
    with _BREAKER_LOCK:
        if _BREAKER_STATE == "open" and _BREAKER_OPEN_UNTIL is not None:
            if datetime.utcnow() >= _BREAKER_OPEN_UNTIL:
                _BREAKER_STATE = "half_open"
                logger.info("WikiData circuit breaker: open → half_open")
        return _BREAKER_STATE


def _breaker_record_success() -> None:
    """Reset to closed; logs recovery if breaker had been open/half_open."""
    global _BREAKER_STATE, _BREAKER_CONSECUTIVE_FAILURES, _BREAKER_OPEN_UNTIL
    with _BREAKER_LOCK:
        prev = _BREAKER_STATE
        _BREAKER_STATE = "closed"
        _BREAKER_CONSECUTIVE_FAILURES = 0
        _BREAKER_OPEN_UNTIL = None
        if prev != "closed":
            logger.info("WikiData circuit breaker: %s → closed (recovered)", prev)


def _breaker_record_failure(retry_after_seconds: Optional[int] = None) -> None:
    """Increment failure counter; trip if threshold reached. Half-open probe
    failure re-trips immediately regardless of threshold."""
    global _BREAKER_STATE, _BREAKER_CONSECUTIVE_FAILURES, _BREAKER_OPEN_UNTIL
    with _BREAKER_LOCK:
        cooldown = retry_after_seconds if retry_after_seconds else _BREAKER_COOLDOWN_SECONDS
        cooldown = min(cooldown, _BREAKER_RETRY_AFTER_CAP)
        if _BREAKER_STATE == "half_open":
            _BREAKER_STATE = "open"
            _BREAKER_OPEN_UNTIL = datetime.utcnow() + timedelta(seconds=cooldown)
            logger.warning(
                "WikiData circuit breaker re-tripped from half_open, cooldown=%ds", cooldown,
            )
            return
        _BREAKER_CONSECUTIVE_FAILURES += 1
        if _BREAKER_CONSECUTIVE_FAILURES >= _BREAKER_FAILURE_THRESHOLD:
            _BREAKER_STATE = "open"
            _BREAKER_OPEN_UNTIL = datetime.utcnow() + timedelta(seconds=cooldown)
            logger.warning(
                "WikiData circuit breaker tripped: %d consecutive failure(s), cooldown=%ds",
                _BREAKER_CONSECUTIVE_FAILURES, cooldown,
            )


def _breaker_reset_for_tests() -> None:
    """Test-only: reset module state. Do not call from production code."""
    global _BREAKER_STATE, _BREAKER_CONSECUTIVE_FAILURES, _BREAKER_OPEN_UNTIL
    with _BREAKER_LOCK:
        _BREAKER_STATE = "closed"
        _BREAKER_CONSECUTIVE_FAILURES = 0
        _BREAKER_OPEN_UNTIL = None


def _parse_retry_after(headers) -> Optional[int]:
    """Parse Retry-After HTTP header. Integer-seconds form only; HTTP-date
    form returns None (caller falls back to default cooldown)."""
    if headers is None:
        return None
    try:
        value = headers.get("Retry-After")
    except (AttributeError, TypeError):
        return None
    if not value:
        return None
    try:
        seconds = int(value)
    except (ValueError, TypeError):
        return None
    return seconds if seconds >= 0 else None
# -----------------------------------------------------------------------------

# Whitespace + invisible Unicode that scrapers can leak into topic strings.
# str.strip() alone does not handle these (soft hyphen / ZW chars are not
# Python whitespace). An all-invisible token resolves to wrong Q-items
# (e.g., U+00AD -> Q257834 "soft hyphen"), creating ghost HAS_TOPIC edges.
_INVISIBLE_CHARS = " \t\n\r\u00ad\u200b\u200c\u200d\ufeff"


def _escape_sparql_literal(raw: str) -> str:
    """
    Make *raw* safe for insertion between double quotes in a SPARQL query.

    • use json.dumps() to get proper back-slash escaping of quotes, control chars …
    • strip the surrounding pair of quotes added by json.dumps()
    • drop line-breaks and excessive whitespace (SPARQL literals cannot span lines)
    • truncate to some sane length to avoid DoS-size queries
    """
    safe = json.dumps(raw)[1:-1]          #  → \" and other escapes
    safe = re.sub(r"\s+", " ", safe)      # collapse \n, \t … into spaces
    return safe[:250]                     # hard cap – adjust as you like


# Load spaCy model
try:
    nlp = spacy.load("en_core_web_sm")
except Exception as e:
    logger.error(f"Failed to load spaCy model: {e}")
    nlp = None  # Handle initialization failure

# Custom list of domain-specific stopwords
custom_stopwords = [
    'page', 'click', 'link', 'comment', 'username', 'password', 'login',
    'subscribe', 'share', 'like', 'read', 'more', 'article', 'posted', 'said'
]

# Combine with standard stopwords
try:
    stop_words_list = list(set(stopwords.words('english')).union(set(custom_stopwords)))
except LookupError:
    import nltk
    nltk.download('stopwords')
    stop_words_list = list(set(stopwords.words('english')).union(set(custom_stopwords)))


# NOTE: Unit 3 M2 will relocate this cache (helpers + query_wikidata + map_topics_to_wikidata)
# to wikidata_lookup.py.
WIKIDATA_TERM_CACHE_TTL_DAYS = int(os.environ.get("WIKIDATA_TERM_CACHE_TTL_DAYS", "30"))
_CACHE_STATS_LOCK = threading.Lock()
_cache_hits = 0
_cache_misses = 0


def _cache_lookup(entity: str) -> Optional[List[Tuple[str, str]]]:
    """Return cached SPARQL result for *entity*, or None if missing/expired/error."""
    try:
        with sqlite3.connect(WIKIDATA_CACHE_DB_FILE) as conn:
            cur = conn.cursor()
            cur.execute(
                "SELECT data, timestamp FROM wikidata_term_cache WHERE entity = ?",
                (entity,),
            )
            row = cur.fetchone()
        if not row:
            return None
        data_str, ts_str = row
        cached_at = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
        if (datetime.now() - cached_at).days >= WIKIDATA_TERM_CACHE_TTL_DAYS:
            return None
        raw = json.loads(data_str)
        return [tuple(item) for item in raw]
    except (sqlite3.Error, json.JSONDecodeError, ValueError) as e:
        logger.debug("WikiData term-cache lookup error for '%s': %s", entity, e)
        return None


def _cache_store(entity: str, data: List[Tuple[str, str]]) -> None:
    """INSERT OR REPLACE the cache row for *entity*. Empty list cached too."""
    try:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with sqlite3.connect(WIKIDATA_CACHE_DB_FILE) as conn:
            cur = conn.cursor()
            cur.execute(
                "INSERT OR REPLACE INTO wikidata_term_cache (entity, data, timestamp) "
                "VALUES (?, ?, ?)",
                (entity, json.dumps(data), ts),
            )
            conn.commit()
    except sqlite3.Error as e:
        logger.warning("WikiData term-cache store failed for '%s': %s", entity, e)


def query_wikidata(term: str) -> List[Tuple[str, str]]:
    """
    Query WikiData for *term* and return [(label, uri), …].
    The term is escaped so that quotes, back-slashes or line-breaks
    cannot break the SPARQL syntax.

    Args:
        term (str): The term to query.

    Returns:
        List[Tuple[str, str]]: A list of tuples containing the item label and item URI.
    """
    global _cache_hits, _cache_misses
    try:
        cached = _cache_lookup(term)
        if cached is not None:
            logger.debug("WikiData term-cache hit: '%s' (%d entries)", term, len(cached))
            with _CACHE_STATS_LOCK:
                _cache_hits += 1
            return cached
        with _CACHE_STATS_LOCK:
            _cache_misses += 1
        logger.debug("WikiData term-cache miss: '%s'", term)

        if _breaker_check_state() == "open":
            return []

        sparql = SPARQLWrapper("https://query.wikidata.org/sparql")
        # Wikidata blocks generic clients, so identify yourself
        sparql.agent = (
            "Coyote/0.4 (https://github.com/captaininfo/coyote; "
            "mailto:lifewidelearningllc@gmail.com)"
        )

        safe_term = _escape_sparql_literal(term)

        sparql.setQuery(f"""
            SELECT ?item ?itemLabel WHERE {{
                ?item ?label "{safe_term}"@en .
                FILTER (STRSTARTS(STR(?item), "http://www.wikidata.org/entity/Q"))
                SERVICE wikibase:label {{ bd:serviceParam wikibase:language "[AUTO_LANGUAGE],en". }}
            }}
            LIMIT 1
        """)
        sparql.setReturnFormat(JSON)

        results = None
        for attempt in range(1, MAX_RETRIES + 1):
            if _breaker_check_state() == "open":
                return []
            try:
                results = sparql.query().convert()
                _breaker_record_success()
                break
            except HTTPError as e:
                retry_after = _parse_retry_after(e.headers)
                logger.warning(
                    "WikiData HTTP %d on attempt %d/%d for '%s'%s",
                    e.code, attempt, MAX_RETRIES, term,
                    f" (Retry-After: {retry_after}s)" if retry_after else "",
                )
                if e.code in (403, 429):
                    _breaker_record_failure(retry_after_seconds=retry_after)
                else:
                    raise  # unexpected HTTP error — real bug
            except SPARQLExceptions.EndPointInternalError as e:
                # 5xx is transient server-side; log but do not count toward breaker
                logger.warning(
                    "WikiData 5xx on attempt %d/%d for '%s': %s",
                    attempt, MAX_RETRIES, term, e,
                )
            # Skip backoff sleep if the breaker just opened — next call to
            # query_wikidata will short-circuit anyway, no point waiting here.
            if _breaker_check_state() == "open":
                return []
            time.sleep(random.uniform(*BACKOFF) * attempt)
        else:
            return []  # all retries exhausted without success

        result = [
            (b['itemLabel']['value'], b['item']['value'])
            for b in results['results']['bindings']
        ]
        _cache_store(term, result)
        return result
    except Exception as e:
        logger.error(f"Error querying WikiData for term '{term}': {e}")
        return []


def map_topics_to_wikidata(topics: List[str]) -> Dict[str, Dict[str, str]]:
    """
    Map a list of topic strings to WikiData URIs.

    Args:
        topics (List[str]): A list of topic strings.

    Returns:
        Dict[str, Dict[str, str]]: Mapped topics with URIs and labels.
    """
    try:
        mapped_topics = {}
        with _CACHE_STATS_LOCK:
            start_hits, start_misses = _cache_hits, _cache_misses
        for topic in topics:
            if not topic or not topic.strip(_INVISIBLE_CHARS):
                continue
            wikidata_result = query_wikidata(topic)
            if wikidata_result:
                label, uri = wikidata_result[0]
                mapped_topics[topic] = {'uri': uri, 'label': label}
        with _CACHE_STATS_LOCK:
            batch_hits = _cache_hits - start_hits
            batch_misses = _cache_misses - start_misses
        if batch_hits or batch_misses:
            logger.info("WikiData term cache (topics): %d hits / %d misses", batch_hits, batch_misses)
        logger.debug(f"Mapped Topics to WikiData: {mapped_topics}")
        return mapped_topics
    except Exception as e:
        logger.error(f"Error in map_topics_to_wikidata: {e}")
        return {}


def extract_and_replace_topics(text: str, topics_mapped: Dict[str, Dict[str, str]]) -> str:
    """
    Replace topics in text with underscores for multi-word topics.

    Args:
        text (str): The original text.
        topics_mapped (Dict[str, Dict[str, str]]): Mapped topics with URIs.

    Returns:
        str: The processed text with topics replaced.
    """
    for topic in topics_mapped.keys():
        replacement = topic.replace(" ", "_")
        text = text.replace(topic, replacement)
    return text


def calculate_tfidf_on_phrases(
    text: str,
    corpus: List[str],
    threshold: float = 0.07
) -> Dict[str, float]:
    """
    Calculate TF-IDF scores for phrases in the text.

    Args:
        text (str): The text to analyze.
        corpus (List[str]): The corpus of documents for TF-IDF calculation.
        threshold (float): The threshold for including terms based on TF-IDF score.

    Returns:
        Dict[str, float]: Dictionary of terms and their TF-IDF scores.
    """
    try:
        vectorizer = TfidfVectorizer(stop_words=stop_words_list)
        tfidf_matrix = vectorizer.fit_transform(corpus + [text])
        feature_names = vectorizer.get_feature_names_out()
        scores = {
            feature_names[i]: tfidf_matrix[-1, i]
            for i in tfidf_matrix[-1].nonzero()[1]
            if tfidf_matrix[-1, i] > threshold
        }
        logger.debug(f"TF-IDF Scores on Phrases: {scores}")
        return scores
    except Exception as e:
        logger.error(f"Error in calculate_tfidf_on_phrases: {e}")
        return {}


def combine_nlp_results(
    tfidf_scores: Dict[str, float],
    topics_mapped: Dict[str, Dict[str, str]]
) -> Dict[str, Dict[str, Any]]:
    """
    Combine TF-IDF scores and mapped topics into a single result.

    Args:
        tfidf_scores (Dict[str, float]): TF-IDF scores for terms.
        topics_mapped (Dict[str, Dict[str, str]]): Mapped topics with URIs and labels.

    Returns:
        Dict[str, Dict[str, Any]]: Combined results with scores, URIs, and labels.
    """
    combined_results = {}
    for term, score in tfidf_scores.items():
        mapped_data = topics_mapped.get(term, {})
        combined_results[term] = {
            'score': score,
            'uri': [mapped_data.get('uri', '')],
            'labels': mapped_data.get('label', 'UNKNOWN')
        }

    for topic, data in topics_mapped.items():
        topic_key = topic.replace(" ", "_")
        if topic_key not in combined_results:
            combined_results[topic_key] = {
                'score': 0,
                'uri': [data['uri']],
                'labels': data.get('label', 'UNKNOWN')
            }
    logger.debug(f"Combined NLP Results: {combined_results}")
    return combined_results


def get_topic_from_text(
    text: str,
    corpus: Optional[List[str]] = None,
    threshold: float = 0.07
) -> Dict[str, Any]:
    """
    Extract topics from text using BERTopic and TF-IDF analysis.

    Args:
        text (str): The text to analyze.
        corpus (Optional[List[str]]): The corpus of documents for TF-IDF calculation.
            If None, a default corpus is used.
        threshold (float): The threshold for including terms based on TF-IDF score.

    Returns:
        Dict[str, Any]: A dictionary containing topics with weights and mapped topics.
    """
    try:
        if not text or 'error' in text.lower():
            raise ValueError("Text contains an error message or is empty")

        # Step 1: Remove stopwords
        processed_text = ' '.join(
            [word for word in text.split() if word.lower() not in stop_words_list]
        )

        # Step 2: Model topics with BERTopic
        topic_info, detailed_topics = analyze_topics(processed_text)
        if not detailed_topics:
            raise ValueError("No topics extracted from the input text.")

        logger.debug(f"Detailed Topics: {detailed_topics}")

        # Step 3: Map topics to WikiData
        topics_mapped = map_topics_to_wikidata(detailed_topics)

        # Step 4: Replace topics in text
        processed_text = extract_and_replace_topics(processed_text, topics_mapped)
        logger.debug(f"Processed Text after replacing topics: {processed_text}")

        # Step 5: Calculate TF-IDF scores
        if corpus is None:
            corpus = [
                "Sample text corpus for reference.",
                "Another document in the corpus.",
                "More documents..."
            ]
            # Note: Replace with a real corpus in production

        tfidf_scores = calculate_tfidf_on_phrases(processed_text, corpus, threshold)

        # Step 6: Combine NLP results
        combined_results = combine_nlp_results(tfidf_scores, topics_mapped)

        mapped_topics = [
            [k, v['uri'][0]] for k, v in combined_results.items() if v['uri'][0]
        ]

        return {
            "topics_with_weights": combined_results,
            "mapped_topics": mapped_topics
        }
    except ValueError as ve:
        logger.error(f"ValueError during topic modeling: {ve}")
        return {
            "topics_with_weights": {},
            "mapped_topics": [],
            "error": str(ve)
        }
    except Exception as e:
        logger.error(f"Error during topic modeling: {e}", exc_info=True)
        return {
            "topics_with_weights": {},
            "mapped_topics": [],
            "error": f"Error during topic modeling: {str(e)}"
        }
