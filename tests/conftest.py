"""
Pytest session setup.

`shared/` at the project root is a namespace package (no __init__.py).
`images/core/core_analysis/shared/` is a regular package with __init__.py
but contains only a subset of files (currently embedding_config.py). When
a test inserts `images/core/core_analysis/` onto sys.path — required to
import `coyote.*` modules under test — Python's import system finds the
regular package first and shadows the project-root namespace package,
breaking `from shared.nl2cypher import ...` and `from shared.time_utils ...`.

Resolution order rules: regular packages with __init__.py beat namespace
packages regardless of sys.path order. So sys.path manipulation alone
cannot fix this.

Workaround: pre-import `shared` submodules at session start. Once cached
in sys.modules, subsequent `from shared.X import Y` reuses the cached
binding without re-walking sys.path.

The same trick is applied to `requests` for a second, unrelated shadowing
problem — see the comment above that import below.
"""
import os
import sys
import tempfile
from pathlib import Path

# Set COYOTE_DATA_DIR before any coyote.* module loads. config_container.py
# runs mkdir on the resolved path at module load; the in-container default
# (/app/data) isn't writable on the host.
_TEST_DATA_DIR = Path(tempfile.gettempdir()) / "coyote_pytest_data"
_TEST_DATA_DIR.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("COYOTE_DATA_DIR", str(_TEST_DATA_DIR))

sys.path.insert(0, str(Path(__file__).parent.parent))

import shared.nl2cypher  # noqa: F401, E402
import shared.time_utils  # noqa: F401, E402
import shared.embedding_config  # noqa: F401, E402

# Pre-import the real `requests` so a test-module stub cannot become the
# process-wide one.
#
# Three WikiData test modules install a MagicMock as `requests` at MODULE
# level — test_wikidata_breaker.py, test_wikidata_term_cache.py and
# test_map_wikidata_unit8_wiring.py — so that importing `wikidata_lookup`
# (which imports requests at module level) works in an environment where
# requests is not installed.
#
# pytest imports EVERY test module during collection, before running any
# test, so without this those stubs become the process-wide `requests` for
# the whole session even though the three modules sort alphabetically after
# their victim. sentence-transformers then lazily imports
# huggingface_hub.file_download, whose `from requests import HTTPError`
# fails against a MagicMock, and test_coyote_embedder.py's Gate-3.2 test
# dies with a misleading "TypeError: metaclass conflict". The test passes in
# isolation and the code works fine in the containers, which makes this
# expensive to diagnose from the failure alone.
#
# All three use sys.modules.setdefault, so seeding the real module here
# makes their stub a no-op wherever requests exists while a bare
# environment still gets it. None of them depends on the stub for network
# isolation — each patches `requests.get` per test via patch.object — and
# their `_StubRequestException` is never raised or asserted.
try:
    import requests  # noqa: F401, E402
except ImportError:
    # Bare environment without requests: the per-module stubs still apply.
    pass
