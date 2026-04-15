# shared/embedding_config.py
#
# Shared embedding model configuration.
# CRITICAL: Both coyote_app (Core) and bot (Agent) containers must use
# the same model. Changing these constants requires:
#   1. Rebuilding both Docker images
#   2. Dropping and recreating vector indexes (dimension is baked in)
#   3. Re-embedding all existing nodes (post-MVP backfill script)
#   4. Updating the model name in both Dockerfiles

EMBEDDING_MODEL_NAME = "all-MiniLM-L6-v2"
EMBEDDING_DIMENSION = 384
