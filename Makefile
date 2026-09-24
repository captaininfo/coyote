.PHONY: sync-shared test test-host build-agent build-core build-all

# Image used to run the test suite. It carries Core's full dependency set
# (cryptography, spaCy, sentence-transformers, torch), which no host
# environment in this project provides. Built by `make build-core`.
TEST_IMAGE ?= coyote-core:local

# Copy canonical shared modules into agent build context
sync-shared:
	cp shared/nl2cypher.py images/agent/app/shared/nl2cypher.py
	cp shared/embedding_config.py images/agent/app/shared/embedding_config.py
	cp shared/embedding_config.py images/core/core_analysis/shared/embedding_config.py
	cp shared/time_utils.py images/agent/app/shared/time_utils.py
	@echo "Synced shared/ -> images/agent/app/shared/ and images/core/core_analysis/shared/"

# Run the suite inside the Core image. Plain `python -m pytest tests/` on the
# host does NOT work here: `python` is unresolvable under pyenv shims, and even
# `python3` lacks cryptography and the rest of Core's requirements.
#
# PYTHONPATH is the REPO ROOT ONLY. Adding images/core/core_analysis lets its
# `shared/` regular package shadow the project-root namespace package, which
# breaks `from shared.nl2cypher import ...` -- see the tests/conftest.py
# docstring for why sys.path order cannot fix that.
#
# HF_HUB_OFFLINE keeps sentence-transformers off the network. The embedding
# model is baked into the image; without this, a container with no working DNS
# spends minutes on HTTP retries before falling back to that local cache.
#
# Runs as the invoking host user (--user) so the few files the suite does create
# -- the UI modules make data/logs/ at import time -- stay owned by you rather
# than root. HOME is redirected to /tmp because that user has no home inside the
# image. Bytecode and pytest's cache are disabled to keep the tree clean.
test:
	@docker image inspect $(TEST_IMAGE) >/dev/null 2>&1 || { \
	  echo "make test: image '$(TEST_IMAGE)' not found -- run 'make build-core' first."; \
	  exit 1; }
	docker run --rm \
	  -v "$(CURDIR)":/repo -w /repo \
	  --user "$(shell id -u):$(shell id -g)" \
	  -e HOME=/tmp \
	  -e PYTHONPATH=/repo \
	  -e PYTHONDONTWRITEBYTECODE=1 \
	  -e HF_HUB_OFFLINE=1 \
	  $(TEST_IMAGE) python -m pytest tests/ -v -p no:cacheprovider

# Host-side escape hatch, for a venv that already has Core's requirements
# installed (pip install -r images/core/core_analysis/requirements.txt).
# python3, not python: the latter is not resolvable under pyenv shims.
test-host:
	PYTHONPATH="$(CURDIR)" HF_HUB_OFFLINE=1 python3 -m pytest tests/ -v

# Sync before building agent image
build-agent: sync-shared
	cd compose && docker compose --profile core --profile agent --profile llm build bot

# Rebuild core image
build-core:
	cd compose && docker compose --profile core build coyote_app

# Rebuild both
build-all: build-agent build-core
