"""Host-side tests for the UI server's background compose-up runner.

`docker compose up` on a cold install can run far longer than any HTTP request;
the earlier design ran it inside the request under subprocess.run(timeout=N),
which KILLED compose mid-build on slow hardware so nothing came up (MacBook gate,
2026-08-24). The start endpoints now dispatch the `up` to a background worker and
return immediately, with `_compose_op_state` / `/api/compose-status` carrying
progress + failure. These cover the worker's state transitions and the
dispatcher's single-flight guard + synchronous Docker-down surfacing. No Docker
required — `run_compose_command` is faked.
"""

import subprocess
import sys
from pathlib import Path

import pytest

_UI_DIR = Path(__file__).resolve().parents[1] / "ui"
if str(_UI_DIR) not in sys.path:
    sys.path.insert(0, str(_UI_DIR))

pytest.importorskip("flask")
cus = pytest.importorskip("coyote_ui_server")


class _FakeResult:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


@pytest.fixture(autouse=True)
def _reset_state():
    cus._set_compose_op_state(status="idle", detail="", profiles=[],
                              returncode=None, error_tail="")
    yield


def test_bg_up_success_sets_done(monkeypatch):
    calls = {}

    def fake_run(args, timeout=None):
        calls["args"], calls["timeout"] = args, timeout
        return _FakeResult(returncode=0)

    monkeypatch.setattr(cus, "run_compose_command", fake_run)

    cus._run_compose_up_bg(["core", "llm", "agent"], "All services starting")

    assert cus._compose_op_state["status"] == "done"
    assert cus._compose_op_state["returncode"] == 0
    assert calls["args"] == ["--profile", "core", "--profile", "llm",
                             "--profile", "agent", "up", "-d", "--pull=missing"]
    assert calls["timeout"] == cus.COMPOSE_UP_TIMEOUT   # backstop, not request-sized


def test_bg_up_nonzero_sets_error_with_tail(monkeypatch):
    monkeypatch.setattr(cus, "run_compose_command",
                        lambda args, timeout=None: _FakeResult(
                            returncode=1, stderr="failed to solve: pip could not reach PyPI"))
    cus._run_compose_up_bg(["core"], "Core services starting")
    assert cus._compose_op_state["status"] == "error"
    assert cus._compose_op_state["returncode"] == 1
    assert "PyPI" in cus._compose_op_state["error_tail"]


def test_bg_up_docker_unavailable_sets_error(monkeypatch):
    def boom(args, timeout=None):
        raise cus.DockerUnavailable("Docker isn't running")

    monkeypatch.setattr(cus, "run_compose_command", boom)
    cus._run_compose_up_bg(["llm"], "LLM service starting")
    assert cus._compose_op_state["status"] == "error"
    assert "Docker" in cus._compose_op_state["error_tail"]


def test_bg_up_timeout_backstop_sets_error(monkeypatch):
    def slow(args, timeout=None):
        raise subprocess.TimeoutExpired(cmd="docker compose up", timeout=timeout)

    monkeypatch.setattr(cus, "run_compose_command", slow)
    cus._run_compose_up_bg(["core"], "Core services starting")
    assert cus._compose_op_state["status"] == "error"
    assert "backstop" in cus._compose_op_state["error_tail"]


def test_dispatch_single_flight_no_second_worker(monkeypatch):
    monkeypatch.setattr(cus, "_preflight_docker", lambda: None)
    started = []

    class _FakeThread:
        def __init__(self, *a, **k):
            started.append((a, k))

        def start(self):
            pass

    monkeypatch.setattr(cus.threading, "Thread", _FakeThread)

    body, code = cus._dispatch_compose_up(["core"], "Core services starting")
    assert code == 202 and body["status"] == "success"
    assert len(started) == 1
    assert cus._compose_op_state["status"] == "running"   # slot claimed synchronously

    body2, code2 = cus._dispatch_compose_up(["core"], "Core services starting")
    assert code2 == 202 and body2["status"] == "success"
    assert len(started) == 1   # guard prevented a second worker


def test_dispatch_docker_down_surfaces_503(monkeypatch):
    def down():
        raise cus.DockerUnavailable("The Docker engine isn't running.")

    monkeypatch.setattr(cus, "_preflight_docker", down)

    def _no_thread(*a, **k):
        raise AssertionError("must not start a worker when Docker is down")

    monkeypatch.setattr(cus.threading, "Thread", _no_thread)

    body, code = cus._dispatch_compose_up(["core"], "Core services starting")
    assert code == 503 and body["status"] == "error"
    assert "Docker" in body["message"]
