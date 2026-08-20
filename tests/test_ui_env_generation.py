"""Host-side tests for the UI server's auto-generated compose/.env.

Locks in two fresh-install bugs found on the Windows install gate (2026-08-19):
  1. `secrets.token_urlsafe` could mint a Neo4j password starting with '-',
     which `neo4j-admin dbms set-initial-password` parses as a CLI flag,
     crashing the Neo4j container in a restart loop (exit 64).
  2. The template hardcoded a stale `LLM=mistral:7b-instruct` instead of the
     project's `qwen2.5-coder:3b`.
No Docker required — ensure_env_file() writes a plain file.
"""

import sys
from pathlib import Path

import pytest

# The UI server lives under ui/, not on the default test path.
_UI_DIR = Path(__file__).resolve().parents[1] / "ui"
if str(_UI_DIR) not in sys.path:
    sys.path.insert(0, str(_UI_DIR))

pytest.importorskip("flask")
cus = pytest.importorskip("coyote_ui_server")


def _parse(envfile: Path) -> dict:
    return dict(
        ln.split("=", 1)
        for ln in envfile.read_text().splitlines()
        if ln and not ln.startswith("#") and "=" in ln
    )


def test_password_is_flag_safe_and_model_correct(tmp_path, monkeypatch):
    envfile = tmp_path / ".env"
    monkeypatch.setattr(cus, "ENV_FILE", envfile)

    cus.ensure_env_file()
    env = _parse(envfile)

    pw = env["NEO4J_PASSWORD"]
    assert pw, "password must be non-empty"
    assert pw[0] not in "-_", f"password must not start with -/_: {pw!r}"
    assert all(c in "0123456789abcdef" for c in pw), "token_hex output expected"
    assert env["LLM"] == "qwen2.5-coder:3b"


def test_password_never_starts_with_dash_across_many_draws(tmp_path, monkeypatch):
    # token_hex guarantees a hex first char, but prove the invariant holds
    # across many generations so a future switch back to token_urlsafe fails here.
    for i in range(500):
        envfile = tmp_path / f".env{i}"
        monkeypatch.setattr(cus, "ENV_FILE", envfile)
        cus.ensure_env_file()
        pw = _parse(envfile)["NEO4J_PASSWORD"]
        assert pw[0] not in "-_", f"draw {i} unsafe: {pw!r}"
