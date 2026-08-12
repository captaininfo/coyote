"""Host-side tests for the UI server's Docker-availability handling.

These verify that a missing `docker` binary or an unreachable engine is
translated into a friendly, user-facing message (DockerUnavailable) instead of
leaking a raw `[WinError 2]` / `Cannot connect to the Docker daemon` traceback
to the dashboard. No real Docker is required — `subprocess.run` is patched.
"""

import sys
from pathlib import Path
from unittest import mock

import pytest

# The UI server lives under ui/, not on the default test path.
_UI_DIR = Path(__file__).resolve().parents[1] / "ui"
if str(_UI_DIR) not in sys.path:
    sys.path.insert(0, str(_UI_DIR))

# Skip cleanly if the UI server's deps (Flask) aren't installed in this env.
pytest.importorskip("flask")
m = pytest.importorskip("coyote_ui_server")


# --- _looks_like_engine_down -------------------------------------------------

@pytest.mark.parametrize("text", [
    "Cannot connect to the Docker daemon at unix:///var/run/docker.sock",
    "error during connect: this error may indicate that the docker daemon is not running",
    "The Docker daemon is not running",
    "Is the docker daemon running?",
])
def test_engine_down_patterns_match(text):
    assert m._looks_like_engine_down(text) is True


@pytest.mark.parametrize("text", [
    "", None, 12345,
    "No such container: foo",
    "unknown flag: --nope",
])
def test_engine_down_patterns_reject_non_daemon_text(text):
    assert m._looks_like_engine_down(text) is False


# --- _docker_run classification ---------------------------------------------

def test_docker_run_missing_binary_raises_missing():
    with mock.patch.object(m.subprocess, "run", side_effect=FileNotFoundError()):
        with pytest.raises(m.DockerUnavailable) as exc:
            m._docker_run([m.DOCKER_BIN, "--version"])
    assert exc.value.user_message == m.DOCKER_MISSING_MSG


def test_docker_run_engine_down_raises_engine_down():
    result = mock.Mock(returncode=1, stderr="Cannot connect to the Docker daemon at ...")
    with mock.patch.object(m.subprocess, "run", return_value=result):
        with pytest.raises(m.DockerUnavailable) as exc:
            m._docker_run([m.DOCKER_BIN, "ps"], capture_output=True, text=True)
    assert exc.value.user_message == m.DOCKER_ENGINE_DOWN_MSG


def test_docker_run_ordinary_nonzero_passes_through():
    # A non-zero exit that is NOT a daemon-connection error must not be
    # misclassified as engine-down (e.g. `docker stop` of a missing container).
    result = mock.Mock(returncode=1, stderr="No such container: foo")
    with mock.patch.object(m.subprocess, "run", return_value=result):
        got = m._docker_run([m.DOCKER_BIN, "stop", "foo"], capture_output=True, text=True)
    assert got.returncode == 1


def test_docker_run_success_passes_through():
    result = mock.Mock(returncode=0, stderr="")
    with mock.patch.object(m.subprocess, "run", return_value=result):
        got = m._docker_run([m.DOCKER_BIN, "--version"])
    assert got.returncode == 0


# --- endpoint integration: /api/status --------------------------------------

def test_status_endpoint_reports_missing_docker_friendly():
    """A missing docker binary yields status=error + friendly message + 503,
    which is what the dashboard's passive status poll now surfaces."""
    with mock.patch.object(m.subprocess, "run", side_effect=FileNotFoundError()):
        client = m.app.test_client()
        resp = client.get("/api/status")
    assert resp.status_code == 503
    payload = resp.get_json()
    assert payload["status"] == "error"
    assert payload["message"] == m.DOCKER_MISSING_MSG
    assert payload["services"] == []


def test_status_endpoint_reports_engine_down_friendly():
    """Binary present but daemon unreachable: `docker --version` succeeds, then
    `compose ps` returns a daemon-connection error -> friendly engine-down msg."""
    def fake_run(cmd, **kwargs):
        if "--version" in cmd:
            return mock.Mock(returncode=0, stdout="Docker version 27", stderr="")
        return mock.Mock(
            returncode=1, stdout="",
            stderr="Cannot connect to the Docker daemon at unix:///var/run/docker.sock. Is the docker daemon running?",
        )
    with mock.patch.object(m.subprocess, "run", side_effect=fake_run):
        client = m.app.test_client()
        resp = client.get("/api/status")
    assert resp.status_code == 503
    payload = resp.get_json()
    assert payload["status"] == "error"
    assert payload["message"] == m.DOCKER_ENGINE_DOWN_MSG
    assert payload["services"] == []


# --- compose-failure stderr surfacing (gate fix #2) -------------------------

# A realistic multi-line compose/BuildKit failure: the decisive error is at
# the END, mirroring the Mac install-gate transcript.
_MULTILINE_STDERR = (
    "#10 ERROR: No matching distribution found for torch==2.5.1+cpu\n"
    "------\n"
    'failed to solve: process "/bin/sh -c pip install ..." did not '
    "complete successfully: exit code: 1\n"
)


def test_compose_error_tail_returns_end_of_text():
    r = mock.Mock(stderr=("x" * 1000) + "TAIL_MARKER", stdout="")
    out = m._compose_error_tail(r, max_len=50)
    assert out.endswith("TAIL_MARKER")
    assert out.startswith("...")
    assert len(out) <= 53  # "..." + max_len


def test_compose_error_tail_falls_back_to_stdout():
    r = mock.Mock(stderr="", stdout="some stdout detail")
    assert m._compose_error_tail(r) == "some stdout detail"


def _post_start_core(result_mock):
    """POST /api/start-core with subprocess/compose env/COMPOSE_FILE stubbed."""
    fake_compose_file = mock.MagicMock()
    fake_compose_file.exists.return_value = True
    with mock.patch.object(m.subprocess, "run", return_value=result_mock), \
         mock.patch.object(m, "get_compose_env", return_value={}), \
         mock.patch.object(m, "COMPOSE_FILE", new=fake_compose_file):
        client = m.app.test_client()
        return client.post("/api/start-core")


def test_start_core_failure_puts_stderr_tail_in_message():
    result = mock.Mock(returncode=1, stdout="", stderr=_MULTILINE_STDERR)
    payload = _post_start_core(result).get_json()
    assert payload["status"] == "error"
    # The decisive tail line is surfaced to the dashboard...
    assert "failed to solve" in payload["message"]
    # ...and newlines survive (rendered by the #status-summary pre-wrap rule).
    assert "\n" in payload["message"]


def test_start_core_success_message_unchanged():
    result = mock.Mock(returncode=0, stdout="ok", stderr="")
    payload = _post_start_core(result).get_json()
    assert payload["status"] == "success"
    assert payload["message"] == "Core services starting"
