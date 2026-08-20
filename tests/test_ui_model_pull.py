"""Host-side tests for the UI server's background Ollama model-pull.

Fresh installs have no LLM model in Ollama (compose pulls the image, not the
model), so Chat failed with "model not found". `_trigger_model_pull_async` +
`_pull_model_worker` pull it in the background. These tests cover the properties
that matter: single-flight (double-click safe), idempotent (skip when present),
and a tolerant NDJSON parser (status lines without byte counts, blanks, errors).
No Docker/Ollama required — `requests` is faked.
"""

import sys
from pathlib import Path

import pytest

_UI_DIR = Path(__file__).resolve().parents[1] / "ui"
if str(_UI_DIR) not in sys.path:
    sys.path.insert(0, str(_UI_DIR))

pytest.importorskip("flask")
pytest.importorskip("requests")
cus = pytest.importorskip("coyote_ui_server")


class _Resp:
    def __init__(self, payload):
        self._p = payload

    def json(self):
        return self._p

    def raise_for_status(self):
        pass


class _Stream:
    def __init__(self, lines):
        self._lines = lines

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def raise_for_status(self):
        pass

    def iter_lines(self, decode_unicode=False):
        for ln in self._lines:
            yield ln


class FakeRequests:
    def __init__(self, tags=None, post_lines=None):
        self.tags = tags or []
        self.post_lines = post_lines or []
        self.post_called = False

    def get(self, url, timeout=None):
        if url.endswith("/api/version"):
            return _Resp({})
        if url.endswith("/api/tags"):
            return _Resp({"models": self.tags})
        return _Resp({})

    def post(self, url, json=None, stream=None, timeout=None):
        self.post_called = True
        return _Stream(self.post_lines)


@pytest.fixture(autouse=True)
def _reset_state():
    cus._set_pull_state("idle", "")
    yield
    cus._set_pull_state("idle", "")


def _fake_thread(counter):
    class FakeThread:
        def __init__(self, *a, **k):
            pass

        def start(self):
            counter.append(1)
    return FakeThread


def test_single_flight_guard_blocks_second_pull(monkeypatch):
    started = []
    monkeypatch.setattr(cus.threading, "Thread", _fake_thread(started))
    cus._set_pull_state("pulling", "downloading 10%")
    cus._trigger_model_pull_async()
    assert started == []   # already pulling -> no second worker


def test_trigger_spawns_worker_when_idle(monkeypatch):
    started = []
    monkeypatch.setattr(cus.threading, "Thread", _fake_thread(started))
    cus._set_pull_state("idle", "")
    cus._trigger_model_pull_async()
    assert started == [1]
    assert cus._model_pull_state["status"] == "pulling"


def test_model_present_parses_tags(monkeypatch):
    monkeypatch.setattr(cus, "requests",
                        FakeRequests(tags=[{"name": "qwen2.5-coder:3b"}, {"name": "other:1b"}]))
    assert cus._model_present("http://x", "qwen2.5-coder:3b") is True
    assert cus._model_present("http://x", "absent:9b") is False


def test_worker_skips_pull_when_model_present(monkeypatch):
    fake = FakeRequests(tags=[{"name": "qwen2.5-coder:3b"}])
    monkeypatch.setattr(cus, "requests", fake)
    monkeypatch.setattr(cus, "_wait_for_ollama", lambda base, timeout=60: True)
    cus._pull_model_worker("qwen2.5-coder:3b", "http://localhost:11434")
    assert cus._model_pull_state["status"] == "done"
    assert fake.post_called is False   # idempotent: no re-download


def test_worker_parses_mixed_ndjson_and_completes(monkeypatch):
    lines = [
        '{"status":"pulling manifest"}',                     # no byte counts
        "",                                                   # blank keep-alive
        '{"status":"downloading","total":100,"completed":50}',
        'not-json-keepalive',                                 # non-JSON line
        '{"status":"verifying sha256 digest"}',              # no byte counts
        '{"status":"success"}',
    ]
    fake = FakeRequests(tags=[], post_lines=lines)
    monkeypatch.setattr(cus, "requests", fake)
    monkeypatch.setattr(cus, "_wait_for_ollama", lambda base, timeout=60: True)
    cus._pull_model_worker("qwen2.5-coder:3b", "http://localhost:11434")
    assert fake.post_called is True
    assert cus._model_pull_state["status"] == "done"


def test_worker_reports_error_line(monkeypatch):
    fake = FakeRequests(tags=[], post_lines=['{"error":"pull access denied"}'])
    monkeypatch.setattr(cus, "requests", fake)
    monkeypatch.setattr(cus, "_wait_for_ollama", lambda base, timeout=60: True)
    cus._pull_model_worker("qwen2.5-coder:3b", "http://localhost:11434")
    assert cus._model_pull_state["status"] == "error"
    assert "denied" in cus._model_pull_state["detail"]


def test_worker_errors_when_ollama_unreachable(monkeypatch):
    fake = FakeRequests()
    monkeypatch.setattr(cus, "requests", fake)
    monkeypatch.setattr(cus, "_wait_for_ollama", lambda base, timeout=60: False)
    cus._pull_model_worker("qwen2.5-coder:3b", "http://localhost:11434")
    assert cus._model_pull_state["status"] == "error"
    assert fake.post_called is False
