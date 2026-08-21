"""Guard: bot must depend on llm as `service_started`, never `service_healthy`.

Regression tripwire for a fresh-install deadlock (verified 2026-08-20):

    bot.depends_on.llm.condition: service_healthy
      + llm healthcheck `ollama show "$LLM"` (unhealthy until the model is pulled)
      + the UI server's background model-pull firing only AFTER `compose up`
        returns returncode 0

...mutually deadlock the one-click "Start All" path. `up` blocks on the
never-healthy llm, exits non-zero, so the UI's `if returncode == 0` guard never
triggers the pull, so the model never lands, so llm is never healthy. The bot
connects to Ollama lazily per-query, so it does not need the model present at
boot; `service_started` lets `up` return 0, the pull start, and Chat self-heal
once the download finishes.

Parsing the YAML would need PyYAML (absent from the host test venv), so this is a
whitespace-tolerant text tripwire. `llm:` appears under `depends_on` in exactly
one place (only bot depends on llm), so the check cannot be fooled by the
top-level `llm:` service key (whose next line is `image:`, not `condition:`).
"""

import re
from pathlib import Path

_COMPOSE = Path(__file__).resolve().parents[1] / "compose" / "compose.yaml"

# `llm:` immediately followed by a `condition:` line = the llm entry inside a
# depends_on block (not the top-level service, whose next line is `image:`).
_DEP_STARTED = re.compile(r"llm:\s*\n\s*condition:\s*service_started")
_DEP_HEALTHY = re.compile(r"llm:\s*\n\s*condition:\s*service_healthy")


def test_bot_depends_on_llm_as_service_started():
    text = _COMPOSE.read_text(encoding="utf-8")
    assert _DEP_STARTED.search(text), (
        "bot's depends_on for llm must be `condition: service_started` — see this "
        "file's docstring for the Start-All deadlock it prevents."
    )


def test_no_service_healthy_gate_on_llm():
    text = _COMPOSE.read_text(encoding="utf-8")
    assert not _DEP_HEALTHY.search(text), (
        "A depends_on gate of `llm: service_healthy` reintroduces the fresh-install "
        "Start-All deadlock (llm stays unhealthy until the model is pulled, but the "
        "pull only fires after `compose up` returns 0). Use service_started."
    )
