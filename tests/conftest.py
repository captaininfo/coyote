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
