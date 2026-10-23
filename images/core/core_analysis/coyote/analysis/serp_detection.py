"""Deterministic detection of search-engine results pages."""

from urllib.parse import urlparse


# Match complete host names and engine-specific result paths so articles hosted
# on these domains are not mistaken for search results.
_SERP_HOST_PATHS = (
    ({"google.com"}, "/search"),
    ({"bing.com"}, "/search"),
    ({"duckduckgo.com"}, "/"),
    ({"duckduckgo.com"}, "/html"),
    ({"duckduckgo.com"}, "/lite"),
    ({"search.brave.com"}, "/search"),
    ({"kagi.com"}, "/search"),
    ({"search.yahoo.com"}, "/search"),
    ({"ecosia.org"}, "/search"),
)


def _host_matches(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


def is_serp_url(url: str, title: str = "") -> bool:
    """Return whether url points to a supported search-results page.

    The optional title preserves detection of Google SERPs whose browser title
    includes the Google Search suffix even if their URL has been redirected or
    normalized. URL matching uses parsed hosts and paths rather than substring
    checks, avoiding false positives for unrelated pages and deceptive hosts.
    """
    if not isinstance(url, str) or not url:
        return False

    if "- google search" in (title or "").lower():
        return True

    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower().rstrip(".")
        path = parsed.path.lower().rstrip("/") or "/"
    except ValueError:
        return False

    for domains, result_path in _SERP_HOST_PATHS:
        if path == result_path and any(_host_matches(host, domain) for domain in domains):
            # DuckDuckGo's homepage is only a SERP when it carries a query.
            if host.endswith("duckduckgo.com") and path == "/" and not parsed.query:
                return False
            return True
    return False
