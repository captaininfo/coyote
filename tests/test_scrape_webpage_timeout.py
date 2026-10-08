"""Fetch timeout configuration, without network or NLP dependencies."""
import importlib.util
from pathlib import Path
from unittest.mock import Mock

import pytest


SOURCE = (
    Path(__file__).parent.parent / "images" / "core" / "core_analysis"
    / "coyote" / "analysis" / "scrape_webpage.py"
)


def load_scraper(monkeypatch, connect=None, read=None):
    for name, value in (
        ("COYOTE_FETCH_CONNECT_TIMEOUT", connect),
        ("COYOTE_FETCH_READ_TIMEOUT", read),
    ):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)
    spec = importlib.util.spec_from_file_location("timeout_scraper", SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("connect,read,expected", [
    (None, None, (3.05, 30)),
    ("5.5", None, (5.5, 30)),
    (None, "60", (3.05, 60)),
    (" 1.25 ", " 45.5 ", (1.25, 45.5)),
])
def test_timeout_passed_to_fetch(monkeypatch, connect, read, expected):
    scraper = load_scraper(monkeypatch, connect, read)
    response = Mock(headers={}, content=b"")
    get = Mock(return_value=response)
    monkeypatch.setattr(scraper.requests, "get", get)

    assert scraper.scrape_webpage("https://example.com/article") == ("", "")
    assert get.call_args.kwargs["timeout"] == expected
    response.raise_for_status.assert_called_once_with()


@pytest.mark.parametrize("invalid", ["", "   ", "invalid", "0", "-1", "nan", "inf", "-inf"])
@pytest.mark.parametrize("component", ["connect", "read"])
def test_invalid_component_falls_back_independently(monkeypatch, invalid, component):
    if component == "connect":
        scraper = load_scraper(monkeypatch, invalid, "60")
        expected = (3.05, 60)
    else:
        scraper = load_scraper(monkeypatch, "5", invalid)
        expected = (5, 30)
    assert scraper._FETCH_TIMEOUT == expected
