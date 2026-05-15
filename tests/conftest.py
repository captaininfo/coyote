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
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import shared.nl2cypher  # noqa: F401, E402
import shared.time_utils  # noqa: F401, E402
import shared.embedding_config  # noqa: F401, E402
