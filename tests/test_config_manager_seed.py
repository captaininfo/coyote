"""
Unit tests for coyote.utils.config_manager.seed_neo4j_credentials_from_env
(first-run Neo4j credential bootstrap).

Pure host tests: get_setting / store_setting are monkeypatched, so no real
state DB is touched and no encryption/decryption occurs. The conftest points
COYOTE_DATA_DIR at a temp dir, so config_manager's import-time key generation
writes harmlessly there.

Covers the four behaviors that matter for the fix:
  1. Happy path        — empty store + env present  -> all three seeded, pw encrypted.
  2. Idempotent skip    — password already stored     -> nothing written (no overwrite).
  3. Missing env no-op  — empty store, env absent      -> nothing written.
  4. Failed-write guard  — store_setting silently fails  -> writes attempted, success NOT
                          logged (the re-read-before-success check catches it).
"""
import logging
import sys
from pathlib import Path

import pytest

sys.path.insert(
    0, str(Path(__file__).parent.parent / "images" / "core" / "core_analysis")
)

from coyote.utils import config_manager as cm  # noqa: E402


def _set_neo4j_env(monkeypatch, uri="bolt://database:7687", user="neo4j", pwd="s3cret"):
    monkeypatch.setenv("NEO4J_URI", uri)
    monkeypatch.setenv("NEO4J_USERNAME", user)
    monkeypatch.setenv("NEO4J_PASSWORD", pwd)


def _clear_neo4j_env(monkeypatch):
    for var in ("NEO4J_URI", "NEO4J_USERNAME", "NEO4J_PASSWORD"):
        monkeypatch.delenv(var, raising=False)


def test_seed_happy_path(monkeypatch):
    """Empty store + env present -> all three settings written, password encrypted."""
    store = {}
    calls = []

    def fake_get(name, decrypt=False):
        return store.get(name)

    def fake_store(name, value, encrypt=False):
        store[name] = value
        calls.append((name, value, encrypt))

    monkeypatch.setattr(cm, "get_setting", fake_get)
    monkeypatch.setattr(cm, "store_setting", fake_store)
    _set_neo4j_env(monkeypatch)

    cm.seed_neo4j_credentials_from_env()

    assert store["neo4j_uri"] == "bolt://database:7687"
    assert store["neo4j_username"] == "neo4j"
    assert store["neo4j_password"] == "s3cret"
    # Password must be stored encrypted; uri/username in the clear (matches Configure path).
    by_name = {name: encrypt for (name, _value, encrypt) in calls}
    assert by_name == {
        "neo4j_uri": False,
        "neo4j_username": False,
        "neo4j_password": True,
    }


def test_seed_idempotent_when_already_configured(monkeypatch):
    """A password already in the store must never be overwritten."""
    store = {"neo4j_password": "user-set-password"}
    calls = []

    monkeypatch.setattr(cm, "get_setting", lambda name, decrypt=False: store.get(name))
    monkeypatch.setattr(
        cm, "store_setting", lambda name, value, encrypt=False: calls.append(name)
    )
    _set_neo4j_env(monkeypatch, pwd="different-env-password")

    cm.seed_neo4j_credentials_from_env()

    assert calls == []  # no writes attempted
    assert store["neo4j_password"] == "user-set-password"  # untouched


def test_seed_noop_when_env_missing(monkeypatch):
    """Empty store but no env credentials -> nothing written, no exception."""
    calls = []
    monkeypatch.setattr(cm, "get_setting", lambda name, decrypt=False: None)
    monkeypatch.setattr(
        cm, "store_setting", lambda name, value, encrypt=False: calls.append(name)
    )
    _clear_neo4j_env(monkeypatch)

    cm.seed_neo4j_credentials_from_env()

    assert calls == []


def test_seed_detects_failed_write(monkeypatch, caplog):
    """
    store_setting swallows its own errors (logs, does not raise). If the write
    silently fails, the re-read guard must catch it: writes are still attempted,
    but success is NOT logged and no exception escapes.
    """
    calls = []
    # get_setting always None => presence check passes AND the post-write re-read
    # still reports "not present" (simulating a silently failed write).
    monkeypatch.setattr(cm, "get_setting", lambda name, decrypt=False: None)
    monkeypatch.setattr(
        cm, "store_setting", lambda name, value, encrypt=False: calls.append(name)
    )
    _set_neo4j_env(monkeypatch)

    with caplog.at_level(logging.DEBUG, logger=cm.logger.name):
        cm.seed_neo4j_credentials_from_env()  # must not raise

    assert calls == ["neo4j_uri", "neo4j_username", "neo4j_password"]
    messages = " ".join(rec.getMessage() for rec in caplog.records)
    assert "FAILED" in messages
    assert "Seeded Neo4j credentials" not in messages
