# scrape_webpage.py

import logging
from urllib.parse import urlparse

import requests
import trafilatura

logger = logging.getLogger(__name__)

# SSRF Protection: Only allow http/https schemes
ALLOWED_SCHEMES = frozenset({'http', 'https'})

# A real-browser UA materially reduces 403/429 from sites that block the
# default python-requests UA. No cookie or auth state is sent.
_FETCH_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
        '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
    ),
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.9',
}
# (connect, read) — separates handshake stall from slow streaming.
# .05 on connect avoids landing on a TCP SYN retry boundary.
_FETCH_TIMEOUT = (3.05, 10)

# Bot/anti-DDoS interstitial detection.
#
# Anti-bot platforms (Cloudflare, Akamai, DataDome, etc.) sometimes serve
# challenge pages with HTTP 200 — `response.raise_for_status()` does not
# catch these — and trafilatura extracts the placeholder copy as if it
# were real content, producing a confident-looking but meaningless node
# that pollutes both topic edges and the vector index.
#
# This list is the body-pattern half of a two-axis filter (the other axis
# is the `cf-mitigated` response header check below). Match is
# case-insensitive substring against the extracted text.
#
# Maintenance note: when a new interstitial slips through, capture a
# distinct phrase from the body and add it here. Keep entries narrow
# enough to avoid matching legitimate content discussing the same topic
# (e.g., a real article *about* Cloudflare).
_BOT_INTERSTITIAL_PATTERNS = (
    "checking your browser before accessing",
    "just a moment...",
    "verify you are human",
    "cloudflare ray id",
    "enable javascript and cookies to continue",
    "ddos protection by cloudflare",
)


def _validate_url(url: str) -> bool:
    """
    Validate URL to prevent SSRF attacks.

    Only allows http/https schemes. Blocks file://, gopher://, etc.
    Note: Does not block internal IPs since users may legitimately
    browse local network resources.

    Args:
        url: The URL to validate

    Returns:
        True if URL is safe to fetch, False otherwise
    """
    try:
        parsed = urlparse(url)
        if parsed.scheme.lower() not in ALLOWED_SCHEMES:
            logger.warning(f"Blocked URL with disallowed scheme: {parsed.scheme}")
            return False
        if not parsed.netloc:
            logger.warning(f"Blocked URL with no host: {url}")
            return False
        return True
    except Exception as e:
        logger.warning(f"URL validation error for {url}: {e}")
        return False


def _looks_like_bot_interstitial(text: str) -> bool:
    """Return True if the extracted body matches a known anti-bot challenge page."""
    if not text:
        return False
    lowered = text.lower()
    return any(pat in lowered for pat in _BOT_INTERSTITIAL_PATTERNS)


def scrape_webpage(url: str) -> tuple[str, str]:
    """
    Scrape the title and main text content from a webpage.

    Uses requests for fetch (with a real-browser UA) and trafilatura for
    main-content extraction. Trafilatura handles encoding detection,
    boilerplate removal, and falls back through multiple extractors
    (readability, justext) when its primary algorithm fails.

    Anti-bot interstitial filter: HTTP 4xx/5xx responses already route
    to the error path via raise_for_status(); this function additionally
    rejects HTTP 200 responses that are anti-bot challenge pages, via
    Cloudflare's `cf-mitigated` header and a body-pattern allow-list.

    Args:
        url (str): The URL of the webpage to scrape.

    Returns:
        tuple[str, str]: (title, main_content) - The page title and extracted text,
            or empty strings if an error occurs, the URL is invalid, or the
            response is an anti-bot interstitial.
    """
    if not _validate_url(url):
        logger.error(f"Refused to fetch invalid/unsafe URL: {url}")
        return "", ""

    try:
        response = requests.get(url, headers=_FETCH_HEADERS, timeout=_FETCH_TIMEOUT)
        response.raise_for_status()
    except requests.RequestException as e:
        logger.error(f"Error fetching webpage at {url}: {e}")
        return "", ""

    # Header-side interstitial check: Cloudflare sets `cf-mitigated` only when
    # its WAF intervened. Legitimate Cloudflare-fronted pages do not carry it.
    if response.headers.get('cf-mitigated'):
        logger.info(
            f"Skipping Cloudflare-mitigated response at {url} "
            f"(cf-mitigated={response.headers.get('cf-mitigated')})"
        )
        return "", ""

    # Pass raw bytes; trafilatura's encoding detection is more robust than
    # relying on response.text's charset inference.
    html_bytes = response.content
    if not html_bytes:
        logger.debug(f"Empty response body from {url}")
        return "", ""

    try:
        text = trafilatura.extract(
            html_bytes,
            include_comments=False,
            include_tables=False,
        ) or ""

        title = ""
        metadata = trafilatura.extract_metadata(html_bytes)
        if metadata and metadata.title:
            title = metadata.title.strip()

        # Body-side interstitial check: catches HTTP 200 challenge pages that
        # passed raise_for_status() and the cf-mitigated header check.
        if _looks_like_bot_interstitial(text):
            logger.info(f"Skipping bot-interstitial body at {url}")
            return "", ""

        logger.debug(
            f"Extracted {len(text)} chars from {url}; title='{title[:80]}'"
        )
        return title, text
    except Exception as e:
        logger.error(f"Unexpected error during extraction at {url}: {e}")
        return "", ""


# Click-tracking and link-shortener redirect URLs that the browser extension
# captures as page navigations. They have no learning content — the user
# spends a fraction of a second on them before the redirect fires — but
# they would otherwise create empty Webpage nodes that pollute time-window
# queries.
#
# A complete fix lives in the browser extension (filter before staging).
# See CLAUDE.md "Known Issues" for the deferred extension-side cleanup.
_REDIRECT_HOST_PATTERNS = (
    "google.com/url",          # Google SERP click-tracking
    "google.com/aclk",         # Google sponsored-result tracking
    "googleadservices.com/aclk",
    "l.facebook.com/l.php",    # Facebook outbound redirect
    "t.co/",                   # Twitter shortener
    "lnkd.in/",                # LinkedIn shortener
    "link.medium.com",         # Medium shortener
)


def should_exempt_url(url: str) -> bool:
    """
    Check if a URL should be exempt from NLP processing.

    This includes:
    - Google SERPs
    - Hypothes.is account, users, and oauth pages
    - The local configure page
    - Click-tracking redirects and link shorteners (see _REDIRECT_HOST_PATTERNS)
    """
    # Original Google SERP check
    if "google.com/search" in url:
        return True

    # Hypothes.is pages to exempt
    if url.startswith("https://hypothes.is/account"):
        return True
    if url.startswith("https://hypothes.is/users"):
        return True
    if url.startswith("https://hypothes.is/oauth"):
        return True

    # Local configure page
    if url.startswith("http://localhost:5000/configure"):
        return True

    # Click-tracking redirects and link shorteners
    if any(pat in url for pat in _REDIRECT_HOST_PATTERNS):
        return True

    return False
