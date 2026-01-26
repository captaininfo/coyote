# Coyote Development Guide
**Target: ~2500 tokens max. Keep concise.**

## Project Philosophy
Coyote is a local-first, privacy-first heutagogical learning tool. It transforms browsing behavior into a semantic graph for AI-enhanced self-determined learning.

## Quick Start
```bash
./launch/start_coyote_mac_linux.sh
# UI: http://localhost:8080 | Bot: http://localhost:8501
```

## Architecture
| Service | Port | Purpose |
|---------|------|---------|
| neo4j | 7474, 7687 | Graph database |
| ollama | 11434 | Local LLM (mistral:7b-instruct) |
| coyote_app | 5000 | Flask API + NLP pipeline |
| bot | 8501 | Streamlit chat + GraphRAG |

**Data Flow**: Browser Extension → SQLite staging → NLP enrichment → Neo4j graph → GraphRAG → LLM response

## Key Files
- `ui/coyote_ui_server.py` - Flask API
- `images/agent/app/bot.py` - Chat interface
- `images/agent/app/chains.py` - GraphRAG logic
- `shared/nl2cypher.py` - Cypher validation
- `tests/test_security.py` - Security tests

## Security

### By Design
- **Local-first**: No cloud telemetry
- **Read-only Cypher**: LLM queries validated via blocklist (`is_read_only()`)
- **APOC restricted**: Only `apoc.meta.*`, `apoc.convert.*` allowed
- **Credentials git-ignored**: `.env` excluded, use `.env.example` as template

### Environment Variables
| Variable | Default | Purpose |
|----------|---------|---------|
| COYOTE_LOG_LEVEL | INFO | Logging verbosity |
| USE_LC_NL2CYPHER | 0 | Dangerous LangChain mode (keep off) |

### Things NOT To Do
- Never expose ports to public internet
- Never commit `.env` with real credentials
- Never use bare `except:` blocks
- Never interpolate user input into f-strings for code/queries (use `json.dumps()`)

## Known Issues (Open)
- LangChain 0.2 APIs need v1.0+ migration
- Silent tier fallbacks in GraphRAG
- Hardcoded stop words and canned queries
- Docker image version pinning needed

## Development Patterns
- 3-state returns: `(True=found, False=empty, None=error)`
- Read-only Cypher via regex blocklist
- Input validation via `_validate_string_input()`

## Testing
```bash
python -m pytest tests/ -v
```

## Security Roadmap
**P1**: LangChain migration, parameterized Cypher, blocklist fuzzing
**P2**: Optional auth, CORS config, rate limiting, extension config UI
