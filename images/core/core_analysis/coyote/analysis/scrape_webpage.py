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


def scrape_webpage(url: str) -> tuple[str, str]:
    """
    Scrape the title and main text content from a webpage.

    Uses requests for fetch (with a real-browser UA) and trafilatura for
    main-content extraction. Trafilatura handles encoding detection,
    boilerplate removal, and falls back through multiple extractors
    (readability, justext) when its primary algorithm fails.

    Args:
        url (str): The URL of the webpage to scrape.

    Returns:
        tuple[str, str]: (title, main_content) - The page title and extracted text,
            or empty strings if an error occurs or URL is invalid.
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
            favor_recall=True,
        ) or ""

        title = ""
        metadata = trafilatura.extract_metadata(html_bytes)
        if metadata and metadata.title:
            title = metadata.title.strip()

        logger.debug(
            f"Extracted {len(text)} chars from {url}; title='{title[:80]}'"
        )
        return title, text
    except Exception as e:
        logger.error(f"Unexpected error during extraction at {url}: {e}")
        return "", ""


def should_exempt_url(url: str) -> bool:
    """
    Check if a URL should be exempt from NLP processing.

    This includes:
    - Google SERPs
    - Hypothes.is account, users, and oauth pages
    - The local configure page
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

    return False
