"""
wikidata_lookup.py

WikiData term→QID lookup via the Wikibase **Action API**
(`www.wikidata.org/w/api.php?action=wbsearchentities`), with a circuit
breaker, retry/backoff, serial inter-call pacing, and the SQLite term cache
(Unit 2). Returns prominence-ranked candidate triples
`(label, concepturi, description)`.

Unit 7 (0.5 refactor) moved this path off the WDQS SPARQL endpoint
(`query.wikidata.org/sparql`) — a per-IP throttle that zeroed WikiData
coverage on the Units 1-4 replay (one 429 + breaker threshold=1). The Action
API has far more generous read limits and native prefix/alias matching, and
returns each candidate's `description` inline (consumed by Unit 8). The WDQS
SPARQL endpoint is still used for P279/P31 ancestor traversal in
`connect_to_ontology.batch_query_wikidata` (a graph query the Action API
cannot serve) — that path keeps its own independent breaker.

Core-only (NOT in shared/; the agent does not need WikiData lookup).
"""

import logging, json, os, sqlite3, threading
import time, random
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Tuple

import requests

from coyote.utils.config_container import WIKIDATA_CACHE_DB_FILE

logger = logging.getLogger(__name__)

MAX_RETRIES = 3
BACKOFF     = (1.0, 3.0)   # seconds

# --- WikiData circuit breaker -------------------------------------------------
# Trips on 403/429 from the Wikidata Action API. Once tripped, query_wikidata()
# short-circuits to [] without making API calls until the cooldown expires.
# 5xx (incl. maxlag-503) is transient and does NOT count toward the breaker.
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
    """Test-only: reset breaker AND pacing module state. Do not call from
    production code. (Name kept for the existing call sites; also clears the
    Unit 7 pacing clock so timing tests start from a known state.)"""
    global _BREAKER_STATE, _BREAKER_CONSECUTIVE_FAILURES, _BREAKER_OPEN_UNTIL
    global _last_call_monotonic
    with _BREAKER_LOCK:
        _BREAKER_STATE = "closed"
        _BREAKER_CONSECUTIVE_FAILURES = 0
        _BREAKER_OPEN_UNTIL = None
    with _PACE_LOCK:
        _last_call_monotonic = 0.0


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


# --- Wikidata Action API transport (Unit 7) -----------------------------------
WIKIDATA_ACTION_API_URL = "https://www.wikidata.org/w/api.php"

# K candidates per term. Top-1 is all Unit 7's consumers use; the deeper list
# is cached so Unit 8's semantic re-rank works off a cache hit with no extra
# network. K-wide × 3-fields makes a cached row ~1-2 KB (sentence descriptions)
# vs the old LIMIT-1 2-tuple — a deliberate, SQLite-trivial tradeoff.
_CANDIDATE_LIMIT = 7

# Steady-state inter-call pacing (seconds) between cache-MISS Action-API calls.
# This is the one parameter PF-9b tunes; default 0.6s = the probe spacing that
# returned 8/8 HTTP 200. <= 0 disables pacing. Safe under per-event pacing only
# because the NLP manager is a single-threaded serial drain (see CLAUDE.md
# rate-limit safety invariant).
WIKIDATA_ACTION_MIN_INTERVAL = float(
    os.environ.get("WIKIDATA_ACTION_MIN_INTERVAL", "0.6")
)

# Wikidata blocks generic clients — identify ourselves per the UA policy.
_USER_AGENT = (
    "Coyote/0.4 (https://github.com/captaininfo/coyote; "
    "mailto:lifewidelearningllc@gmail.com)"
)

_PACE_LOCK = threading.Lock()
_last_call_monotonic = 0.0


def _pace() -> None:
    """Sleep so consecutive Action-API calls are >= WIKIDATA_ACTION_MIN_INTERVAL
    apart. No-op when the interval is disabled. Computes the wait under the lock
    but sleeps outside it; under the single-threaded serial drain there is no
    contention, and even with future threads this only ever under-paces."""
    global _last_call_monotonic
    if WIKIDATA_ACTION_MIN_INTERVAL <= 0:
        return
    with _PACE_LOCK:
        wait = _last_call_monotonic + WIKIDATA_ACTION_MIN_INTERVAL - time.monotonic()
    if wait > 0:
        time.sleep(wait)
    with _PACE_LOCK:
        _last_call_monotonic = time.monotonic()


def _parse_wbsearchentities(payload) -> List[Tuple[str, str, str]]:
    """Extract (label, concepturi, description) triples from a wbsearchentities
    formatversion=2 response, prominence order preserved. `concepturi` is the
    canonical `http://www.wikidata.org/entity/Q####` form (matches stored
    `Entities/Topics.wikidata_uri`); items without one are skipped. Missing
    label/description default to ''."""
    out: List[Tuple[str, str, str]] = []
    for item in (payload.get("search") or []):
        uri = item.get("concepturi") or ""
        if not uri:
            continue
        out.append((item.get("label") or "", uri, item.get("description") or ""))
    return out


# --- Term→QID cache (Unit 2) ---------------------------------------------------
WIKIDATA_TERM_CACHE_TTL_DAYS = int(os.environ.get("WIKIDATA_TERM_CACHE_TTL_DAYS", "30"))
_CACHE_STATS_LOCK = threading.Lock()
_cache_hits = 0
_cache_misses = 0


def _cache_lookup(entity: str) -> Optional[List[Tuple[str, str, str]]]:
    """Return cached candidate triples for *entity*, or None if
    missing/expired/error. `[tuple(item) for item in raw]` reconstructs each
    3-tuple from the JSON-serialized list unchanged."""
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


def _cache_store(entity: str, data: List[Tuple[str, str, str]]) -> None:
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


def query_wikidata(term: str) -> List[Tuple[str, str, str]]:
    """
    Look *term* up via the Wikidata Action API (`wbsearchentities`) and return
    prominence-ranked candidate triples `[(label, concepturi, description), …]`.

    `description` rides inline in the same response (Unit 8 consumes it); Unit
    7's own consumers use only `result[0]`. The term needs no SPARQL escaping —
    it is a URL query parameter that `requests` encodes.

    Args:
        term (str): The term to query.

    Returns:
        List[Tuple[str, str, str]]: (label, canonical entity URI, description)
        per candidate, up to `_CANDIDATE_LIMIT`; [] on no match / error / open
        breaker.
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

        params = {
            "action": "wbsearchentities",
            "search": term,
            "language": "en",
            "uselang": "en",
            "type": "item",
            "format": "json",
            "formatversion": "2",
            "limit": _CANDIDATE_LIMIT,
            "maxlag": "5",
        }

        payload = None
        for attempt in range(1, MAX_RETRIES + 1):
            if _breaker_check_state() == "open":
                return []
            _pace()
            retry_after_hint = None  # set by maxlag-503 to honor its Retry-After
            try:
                resp = requests.get(
                    WIKIDATA_ACTION_API_URL,
                    params=params,
                    headers={"User-Agent": _USER_AGENT},
                    timeout=30,
                )
                status = resp.status_code
                if status in (403, 429):
                    retry_after = _parse_retry_after(resp.headers)
                    logger.warning(
                        "WikiData Action API HTTP %d on attempt %d/%d for '%s'%s",
                        status, attempt, MAX_RETRIES, term,
                        f" (Retry-After: {retry_after}s)" if retry_after else "",
                    )
                    _breaker_record_failure(retry_after_seconds=retry_after)
                elif status >= 500:
                    # 5xx incl. maxlag-503: transient, do NOT count toward breaker.
                    # We sent maxlag=5, so honor the server's Retry-After hint for
                    # the inter-retry sleep below (a maxlag-503 carries it); else
                    # fall through to the default backoff.
                    retry_after_hint = _parse_retry_after(resp.headers)
                    logger.warning(
                        "WikiData Action API %d (transient) on attempt %d/%d for '%s'%s",
                        status, attempt, MAX_RETRIES, term,
                        f" (Retry-After: {retry_after_hint}s)" if retry_after_hint else "",
                    )
                else:
                    body = resp.json()
                    if isinstance(body, dict) and "error" in body:
                        # in-band error (e.g. maxlag reported at HTTP 200): transient
                        logger.warning(
                            "WikiData Action API in-band error for '%s': %s",
                            term, body.get("error", {}).get("code", "unknown"),
                        )
                    else:
                        _breaker_record_success()
                        payload = body
                        break
            except (requests.RequestException, ValueError) as e:
                # network error or undecodable JSON: transient, do not count
                logger.warning(
                    "WikiData Action API error on attempt %d/%d for '%s': %s",
                    attempt, MAX_RETRIES, term, e,
                )
            # Skip backoff sleep if the breaker just opened — next call to
            # query_wikidata will short-circuit anyway, no point waiting here.
            if _breaker_check_state() == "open":
                return []
            if retry_after_hint:
                time.sleep(min(retry_after_hint, _BREAKER_RETRY_AFTER_CAP))
            else:
                time.sleep(random.uniform(*BACKOFF) * attempt)
        else:
            return []  # all retries exhausted without success

        result = _parse_wbsearchentities(payload)
        _cache_store(term, result)
        return result
    except Exception as e:
        logger.error(f"Error querying WikiData for term '{term}': {e}")
        return []


def map_topics_to_wikidata(
    topics: List[str], context_embedding: Optional[List[float]] = None
) -> Dict[str, Dict[str, str]]:
    """
    Map a list of topic strings to WikiData URIs.

    Args:
        topics (List[str]): A list of topic strings.
        context_embedding: when provided (webpage path only — the page's
            pooled full-doc embedding), Unit 8 re-ranks each term's candidate
            list by description<->context cosine and may DECLINE a mapping;
            when None (every other event path, or a webpage whose own
            embedding failed) the prominence top-1 is used unchanged.

    Returns:
        Dict[str, Dict[str, str]]: Mapped topics with URIs and labels.
    """
    # Function-local import: keeps wikidata_lookup's module-import surface (and
    # its requests-stubbed test imports) free of the embedder chain, and
    # sidesteps any import cycle. Only loads when a map is actually requested.
    from coyote.analysis.nlp.wikidata_disambiguation import select_best_candidate
    try:
        mapped_topics = {}
        with _CACHE_STATS_LOCK:
            start_hits, start_misses = _cache_hits, _cache_misses
        for topic in topics:
            if not topic or not topic.strip(_INVISIBLE_CHARS):
                continue
            wikidata_result = query_wikidata(topic)
            if not wikidata_result:
                continue
            if context_embedding is not None:
                selected = select_best_candidate(
                    context_embedding, wikidata_result, term=topic
                )
                if selected is None:
                    continue  # Unit 8 declined below threshold -> no mapping
                label, uri = selected
            else:
                label, uri, _ = wikidata_result[0]  # prominence top-1
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
