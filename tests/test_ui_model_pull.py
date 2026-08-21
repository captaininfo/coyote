"""Host-side tests for the UI server's background Ollama model-ensure daemon.

A fresh install has no LLM model in Ollama (compose pulls the image, not the
model), so Chat failed with "model not found". The `_model_ensure_*` daemon keeps
the configured model present whenever Ollama is up — decoupled from `compose up`
returning 0 (an earlier trigger-on-`up` design silently failed when the cold
first build outran its timeout). These tests cover the properties that matter:
idempotence (skip when present), recovery (a prior error flips to done once the
model appears — e.g. via a manual `ollama pull`), a tolerant NDJSON parser, and a
retry backoff keyed to the specific model that failed. No Docker/Ollama required
— `requests` is faked.
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
    def __init__(self, reachable=True, tags=None, post_lines=None):
        self.reachable = reachable
        self.tags = tags or []
        self.post_lines = post_lines or []
        self.post_called = False

    def get(self, url, timeout=None):
        if not self.reachable:
            raise ConnectionError("connection refused")
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
    def _clean():
        with cus._model_pull_lock:
            cus._model_pull_state.clear()
            cus._model_pull_state.update(status="idle", detail="", model=None)
    _clean()
    yield
    _clean()


def _env(model="qwen2.5-coder:3b"):
    return lambda: {"LLM": model, "OLLAMA_PORT": "11434"}


# --- primitives --------------------------------------------------------------

def test_ollama_reachable_true(monkeypatch):
    monkeypatch.setattr(cus, "requests", FakeRequests(reachable=True))
    assert cus._ollama_reachable("http://x") is True


def test_ollama_reachable_false(monkeypatch):
    monkeypatch.setattr(cus, "requests", FakeRequests(reachable=False))
    assert cus._ollama_reachable("http://x") is False


def test_model_present_parses_tags(monkeypatch):
    monkeypatch.setattr(cus, "requests",
                        FakeRequests(tags=[{"name": "qwen2.5-coder:3b"}, {"name": "other:1b"}]))
    assert cus._model_present("http://x", "qwen2.5-coder:3b") is True
    assert cus._model_present("http://x", "absent:9b") is False


# --- the pull itself ---------------------------------------------------------

def test_do_model_pull_parses_mixed_ndjson_and_completes(monkeypatch):
    lines = [
        '{"status":"pulling manifest"}',                     # no byte counts
        "",                                                   # blank keep-alive
        '{"status":"downloading","total":100,"completed":50}',
        'not-json-keepalive',                                 # non-JSON line
        '{"status":"verifying sha256 digest"}',              # no byte counts
        '{"status":"success"}',
    ]
    fake = FakeRequests(post_lines=lines)
    monkeypatch.setattr(cus, "requests", fake)
    cus._do_model_pull("qwen2.5-coder:3b", "http://x")
    assert fake.post_called is True
    assert cus._model_pull_state["status"] == "done"


def test_do_model_pull_error_line_stamps_model(monkeypatch):
    fake = FakeRequests(post_lines=['{"error":"pull access denied"}'])
    monkeypatch.setattr(cus, "requests", fake)
    cus._do_model_pull("qwen2.5-coder:3b", "http://x")
    assert cus._model_pull_state["status"] == "error"
    assert "denied" in cus._model_pull_state["detail"]
    assert cus._model_pull_state.get("error_model") == "qwen2.5-coder:3b"


# --- the ensure tick ---------------------------------------------------------

def test_tick_model_present_flips_idle_to_done(monkeypatch):
    monkeypatch.setattr(cus, "_parse_env_file", _env())
    fake = FakeRequests(reachable=True, tags=[{"name": "qwen2.5-coder:3b"}])
    monkeypatch.setattr(cus, "requests", fake)
    cus._model_ensure_tick()
    assert cus._model_pull_state["status"] == "done"
    assert fake.post_called is False   # already present -> no re-download


def test_tick_model_present_flips_error_to_done(monkeypatch):
    # A prior failed attempt, then the model becomes present by another route
    # (e.g. the `docker exec ollama pull` unblock) -> status must recover to done,
    # not stay stuck showing "download failed" while Chat actually works.
    monkeypatch.setattr(cus, "_parse_env_file", _env())
    cus._set_pull_state("error", "boom", model="qwen2.5-coder:3b")
    fake = FakeRequests(reachable=True, tags=[{"name": "qwen2.5-coder:3b"}])
    monkeypatch.setattr(cus, "requests", fake)
    cus._model_ensure_tick()
    assert cus._model_pull_state["status"] == "done"
    assert fake.post_called is False


def test_tick_model_absent_pulls(monkeypatch):
    monkeypatch.setattr(cus, "_parse_env_file", _env())
    fake = FakeRequests(reachable=True, tags=[], post_lines=['{"status":"success"}'])
    monkeypatch.setattr(cus, "requests", fake)
    cus._model_ensure_tick()
    assert fake.post_called is True
    assert cus._model_pull_state["status"] == "done"


def test_tick_unreachable_does_not_pull(monkeypatch):
    monkeypatch.setattr(cus, "_parse_env_file", _env())
    fake = FakeRequests(reachable=False)
    monkeypatch.setattr(cus, "requests", fake)
    cus._model_ensure_tick()
    assert fake.post_called is False


# --- backoff, keyed to the failed model --------------------------------------

def test_tick_backoff_suppresses_recent_same_model_failure(monkeypatch):
    monkeypatch.setattr(cus, "_parse_env_file", _env("qwen2.5-coder:3b"))
    cus._set_pull_state("error", "boom", model="qwen2.5-coder:3b")   # error_at = now
    fake = FakeRequests(reachable=True, tags=[], post_lines=['{"status":"success"}'])
    monkeypatch.setattr(cus, "requests", fake)
    cus._model_ensure_tick()
    assert fake.post_called is False   # within cooldown -> no hammering


def test_tick_backoff_expired_retries(monkeypatch):
    monkeypatch.setattr(cus, "_parse_env_file", _env("qwen2.5-coder:3b"))
    cus._set_pull_state("error", "boom", model="qwen2.5-coder:3b")
    with cus._model_pull_lock:
        cus._model_pull_state["error_at"] = 0   # long ago -> cooldown elapsed
    fake = FakeRequests(reachable=True, tags=[], post_lines=['{"status":"success"}'])
    monkeypatch.setattr(cus, "requests", fake)
    cus._model_ensure_tick()
    assert fake.post_called is True


def test_tick_backoff_does_not_suppress_different_model(monkeypatch):
    # Settings changed the model right after a *different* model failed; the new
    # model's first attempt must not inherit the old model's cooldown.
    monkeypatch.setattr(cus, "_parse_env_file", _env("newmodel:7b"))
    cus._set_pull_state("error", "boom", model="oldmodel:3b")
    fake = FakeRequests(reachable=True, tags=[], post_lines=['{"status":"success"}'])
    monkeypatch.setattr(cus, "requests", fake)
    cus._model_ensure_tick()
    assert fake.post_called is True
