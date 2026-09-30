# Contributing to Coyote

Thanks for looking. Coyote is an open, local-first learning tool built in the open, and it is deliberately designed to be **hackable** — you can swap the LLM, change the graph backend, add NLP steps, or write new connectors without touching the core pipeline. This guide gets you from clone to a running dev setup, states the conventions, and points you at good first work.

New here? Read [ARCHITECTURE.md](ARCHITECTURE.md) first (a one-page map), skim [VISION.md](VISION.md) for where this is headed, and keep `CLAUDE.md` open — it is the exhaustive reference and the running list of Known Issues.

## Ground rules

- **License is GPL-v3 (copyleft).** By contributing you agree your changes ship under the same license — improvements stay open.
- **Be excellent to each other.** Assume good faith, keep discussion technical and kind. Harassment isn't welcome.
- **The record stays deterministic.** Coyote's one non-negotiable architectural rule: *LLMs verbalize; they do not compute the record.* Do not introduce an LLM (or any non-reproducible step) into the data pipeline that writes the graph. LLMs belong in the UI/query layer only. If a feature seems to need an LLM in the pipeline, open an issue first — there's almost always a deterministic way.

## Development setup

Prerequisites: Docker (Desktop, or Engine + Compose v2), Firefox 140+, and Python 3.10+.

```bash
git clone https://github.com/captaininfo/coyote.git
cd coyote
# The dashboard runs on the host and orchestrates the containers:
./launch/start_coyote_mac_linux.sh        # macOS/Linux  (Start-Coyote.cmd on Windows)
# → opens http://localhost:8080 ; start services from the System Status tab
```

Running individual pieces from source:

```bash
python -m coyote.coyote_server    # Core API + background managers (see images/core/core_analysis/requirements.txt)
python ui/coyote_ui_server.py     # dashboard at :8080
```

Rebuild container images after changing code they contain:

```bash
make sync-shared     # sync shared/nl2cypher.py into both images — REQUIRED before a docker build
make build-core      # rebuild Core
make build-agent     # rebuild the Agent (implies sync-shared)
make build-all
```

## Tests

```bash
make build-core      # once — builds the image the tests run in
make test            # 390 tests
```

The suite runs **inside Coyote's Core container image**, which already carries every dependency the tests import — spaCy, sentence-transformers, torch, cryptography. That's deliberate: installing all of that on your host is a long, heavy setup for a project you need Docker to run anyway. If you'd rather test on the host, `pip install -r images/core/core_analysis/requirements.txt` into a virtualenv and use `make test-host` instead.

The suite covers the Cypher security blocklist, the `shared/` sync guard, time parsing, the WikiData circuit breakers, embeddings, and the demo pure-functions. **Please add tests with your change** — the demos are a good model: pure, stdlib-only functions kept separate from container and DB access, so they need no Neo4j to run (see `tests/test_knowledge_shape_demo.py`).

## Conventions (please match the surrounding code)

- **Cypher is parameterized and read-only.** Never interpolate user input into a query — use `$param`. LLM-generated Cypher must pass `is_read_only()` (`shared/nl2cypher.py`). User input in any string bound for code/queries goes through `json.dumps()`.
- **No bare `except:`** — catch specific exceptions.
- **3-state returns** where the codebase uses them: `(True, ctx)` = found, `(False, "")` = empty, `(None, "")` = error.
- **One coherent unit of content per embedding** — don't fold one node's content into another node's vector; use an edge.
- **Timestamps** are ISO-8601 strings; compare with Neo4j `datetime()`.
- **Secrets never in git.** `.env` is ignored; use `.env.example`. Never commit real credentials, and never enable `USE_LC_NL2CYPHER=1` (it bypasses the read-only guard).

## Git & pull requests

- `main` is the default branch. **Branch off `main`**, keep each change focused, and open a PR against `main`.
- **Stage by filename — never `git add -A` / `git add .`** The tree accumulates local scratch (`tmp_*.py`, diagnostic logs); a blanket add sweeps them in.
- Write a clear PR description: what changed, why, and how you tested it. Reference the issue you're closing.
- Keep debt/cleanup in its own commit, separate from behavior changes, so history stays bisectable.

## Where to start reading

- `images/agent/app/chains.py` — the 3-tier GraphRAG retrieval logic
- `images/core/core_analysis/coyote/coyote_server.py` — pipeline entry points + background managers
- `images/core/core_analysis/coyote/demos/` — small, self-contained, well-tested examples of reading the record deterministically

## Good first issues

The live list is in the tracker, so it can't drift out of step with the code:

**[→ Open good first issues](https://github.com/captaininfo/coyote/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22)**

Each one names the files involved, the traps worth knowing, and roughly how big it is. Area labels (`area: graph`, `area: nlp`, `area: extension`, `area: connectors`) let you filter to the part of Coyote you'd rather work in.

**The flagship** is [making the concept hierarchy traversable](https://github.com/captaininfo/coyote/issues/4) — storing `concept → parent` links as real edges so "the shape of what I know about X" becomes a graph traversal instead of something reconstructed in memory from a cache that expires weekly. It's load-bearing for the corpus-divergence work in [VISION.md](VISION.md), which is why it's the flagship rather than just graph hygiene.

More candidates live throughout `CLAUDE.md`'s "Known Issues" and "Post-MVP" sections — those are written for maintainers rather than newcomers, so they're rougher, but they're honest about what's broken.

Not sure where to jump in? Open an issue describing what you'd like to work on and we'll help scope it.

Questions, ideas, and PRs are all welcome.
