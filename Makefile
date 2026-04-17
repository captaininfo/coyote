.PHONY: sync-shared test build-agent build-core build-all

# Copy canonical shared modules into agent build context
sync-shared:
	cp shared/nl2cypher.py images/agent/app/shared/nl2cypher.py
	cp shared/embedding_config.py images/agent/app/shared/embedding_config.py
	cp shared/embedding_config.py images/core/core_analysis/shared/embedding_config.py
	@echo "Synced shared/ -> images/agent/app/shared/ and images/core/core_analysis/shared/"

test:
	python -m pytest tests/ -v

# Sync before building agent image
build-agent: sync-shared
	cd compose && docker compose --profile core --profile agent --profile llm build bot

# Rebuild core image
build-core:
	cd compose && docker compose --profile core build coyote_app

# Rebuild both
build-all: build-agent build-core
