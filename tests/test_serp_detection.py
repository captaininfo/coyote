"""Tests for deterministic search-results page detection."""

import sys
from pathlib import Path

sys.path.insert(
    0, str(Path(__file__).parent.parent / "images" / "core" / "core_analysis")
)

from coyote.analysis.serp_detection import is_serp_url  # noqa: E402


def test_recognizes_major_search_engine_results_pages():
    urls = (
        "https://www.google.com/search?q=python",
        "https://www.bing.com/search?q=python",
        "https://duckduckgo.com/?q=python",
        "https://duckduckgo.com/html/?q=python",
        "https://search.brave.com/search?q=python",
        "https://kagi.com/search?q=python",
        "https://search.yahoo.com/search?p=python",
        "https://www.ecosia.org/search?q=python",
    )
    assert all(is_serp_url(url) for url in urls)


def test_recognizes_regional_engine_subdomains():
    assert is_serp_url("https://uk.bing.com/search?q=python")
    assert is_serp_url("https://de.search.yahoo.com/search?p=python")
    assert is_serp_url("https://fr.ecosia.org/search?q=python")


def test_does_not_match_ordinary_pages_on_search_engine_domains():
    urls = (
        "https://www.google.com/about/",
        "https://www.bing.com/news",
        "https://duckduckgo.com/",
        "https://search.brave.com/",
        "https://kagi.com/",
        "https://search.yahoo.com/news",
        "https://www.ecosia.org/",
    )
    assert not any(is_serp_url(url) for url in urls)


def test_rejects_deceptive_hosts_and_unrelated_paths():
    assert not is_serp_url("https://google.com.example.org/search?q=python")
    assert not is_serp_url("https://notgoogle.com/search?q=python")
    assert not is_serp_url("https://www.bing.com/news/search?q=python")
    assert not is_serp_url("https://example.org/search?q=python")


def test_preserves_google_search_title_detection():
    assert is_serp_url("https://example.org/redirect", title="Python - Google Search")


def test_handles_empty_and_malformed_urls():
    assert not is_serp_url("")
    assert not is_serp_url(None)
    assert not is_serp_url("https://[invalid")
