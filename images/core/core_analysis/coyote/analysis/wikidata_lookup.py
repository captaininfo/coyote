"""
wikidata_lookup.py

WikiData term→QID lookup: SPARQL query with circuit breaker, retry/backoff,
SPARQL-literal escaping, and the SQLite term cache (Unit 2).

Moved verbatim from text_bertopic_analysis.py in Unit 3 M2/M3 — this module
is core-only (NOT in shared/; the agent does not need WikiData lookup).
"""

import logging, json, os, re, sqlite3, threading
import time, random
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple
from urllib.error import HTTPError

from SPARQLWrapper import SPARQLWrapper, JSON, SPARQLExceptions

from coyote.utils.config_container import WIKIDATA_CACHE_DB_FILE

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


# --- Term→QID cache (Unit 2) ---------------------------------------------------
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
