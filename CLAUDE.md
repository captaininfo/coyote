# Coyote Development Guide
**Target: ~2700 tokens max. Keep concise.**

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
| neo4j | 7474, 7687 | Graph DB (Neo4j 5.26, APOC restricted) |
| ollama | 11434 | Local LLM (qwen2.5-coder:3b) |
| coyote_app | 5000 | Flask Core API + NLP pipeline |
| bot | 8501 | Streamlit chat + GraphRAG |
| ui_server | 8080 | Flask Docker orchestration UI (not in compose) |

**Data Flow**: Browser Extension → SQLite staging → NLP enrichment (spaCy NER, BERTopic/RAKE, embedding) → Neo4j graph → GraphRAG (Tier 0 vector + Tier 1-3) → LLM response

### Neo4j Graph Model
**Nodes:** `Webpage`, `Annotation`, `Purpose`, `SearchTerms`, `WikiDataOntology`
**Relationships:** `INITIATES_SEARCH`, `INITIATES`, `GENERATES_SERP`, `LINKS_TO`, `HAS_ANNOTATION`, `HAS_TOPIC`
**Timestamps:** Stored as ISO 8601 strings, not native datetime. Use `datetime(node.timestamp)` wrapper for all comparisons.

### GraphRAG 3-Tier Fallback (chains.py)
- **TIER 1**: Parameterized Cypher (CY_TOPICS_SAFE, CY_TEXT_SAFE, CY_SEARCHES_SAFE) via `$params` + `apoc.convert.fromJsonList`
- **TIER 2**: LLM-generated Cypher → `is_read_only()` guard → execute (analytical queries only)
- **TIER 3**: Time-filtered fallback (all webpages in N days, limit 20)

Returns convention: `(True, context)` = found | `(False, "")` = empty | `(None, "")` = error

### UI Architecture (wireframe_v2.html)
**Status:** Phase 5 complete. Post-MVP: remove `/legacy` route and delete `coyote_wireframe.html` once stability is confirmed.
**File:** `ui/templates/wireframe_v2.html` (served at `/`). Legacy fallback: `coyote_wireframe.html` at `/legacy` — do not delete yet.

**Layout:** Two-zone flex column
- Sky zone (flex: 1): content stage. Active feature owns this space.
- Ground zone (padding-bottom: calc(476/3000 * 100%)): desert horizon strip.
- Sky background: CSS `linear-gradient` (replaced SVG to fix subpixel gaps)
- Ground/avatar: inlined SVGs (`#desert-layer`, `#avatar-layer`)

**Sky panels** (one `.active` at a time — all complete, no stubs):
`sky-overview` (Cytoscape graph + 5 chip queries + NL input),
`sky-insights` (post-MVP stub), `sky-chat` (Streamlit iframe, lazy-loaded),
`sky-neo4j` (link to :7474), `sky-setup` (static guide),
`sky-configure` (Neo4j form), `sky-integrations` (Hypothesis form),
`sky-status` (live polling + Docker buttons)

**Navigation:** Slide-out drawer. `ctx-overview` chip row for graph queries.
`window.switchSection(name)` shim maps old names (`browse`→`sky-overview`, etc.).

**Deferred post-MVP:** CSS extraction to separate file.

## Key Files
| File | Purpose |
|------|---------|
| `ui/coyote_ui_server.py` | Docker orchestration UI + insights API |
| `images/core/core_analysis/coyote/coyote_server.py` | Core Flask API (event ingestion, background managers) |
| `images/agent/app/bot.py` | Streamlit chat UI + canned queries |
| `images/agent/app/chains.py` | 3-tier GraphRAG chain logic |
| `images/agent/app/coyote_schema.py` | `get_schema()` via apoc.meta.schema() |
| `shared/nl2cypher.py` | Cypher validation (ALSO duplicated at `images/agent/app/shared/`) |
| `images/core/core_analysis/coyote/utils/initialize_databases.py` | SQLite schema definitions |
| `images/core/core_analysis/coyote/analysis/nlp/` | NER, BERTopic, RAKE, summarization |
| `shared/embedding_config.py` | `EMBEDDING_MODEL_NAME`, `EMBEDDING_DIMENSION` constants (Phase A) |
| `images/core/core_analysis/coyote/coyote_embedder.py` | `embed_text()`, model singleton, lazy import (Phase B) |
| `tests/test_security.py` | Blocklist unit tests (21 cases) |

## Security

### By Design
- **Local-first**: No cloud telemetry
- **Read-only Cypher**: LLM queries validated via blocklist (`is_read_only()`)
- **APOC restricted**: Only `apoc.meta.*`, `apoc.convert.*` allowed
- **Credentials git-ignored**: `.env` excluded, use `.env.example` as template
- **Encrypted at rest**: Neo4j/Hypothesis credentials Fernet-encrypted in state DB

### Environment Variables
| Variable | Default | Purpose |
|----------|---------|---------|
| COYOTE_LOG_LEVEL | INFO | Logging verbosity |
| USE_LC_NL2CYPHER | 0 | Dangerous LangChain mode (keep off) |
| LLM | qwen2.5-coder:3b | Ollama model name |
| FLASK_DEBUG | 0 | Flask debug mode |
| SENTENCE_TRANSFORMERS_HOME | /opt/embedding_model | Embedding model cache path (both containers) |
| VECTOR_SIMILARITY_THRESHOLD | 0.65 | Tier 0 cosine similarity cutoff (Phase C) |

### Things NOT To Do
- Never expose ports to public internet
- Never commit `.env` with real credentials
- Never use bare `except:` blocks
- Never interpolate user input into f-strings for code/queries (use `json.dumps()`)
- Never enable `USE_LC_NL2CYPHER=1` — bypasses read-only validation entirely

## Known Issues (Open)
- **Orphan SearchTerms (blocks Phase C v2)**: every SearchTerms node in the graph has only `HAS_TOPIC → WikiDataOntology` outgoing edges; zero `INITIATES` / `GENERATES_SERP → Webpage` edges exist. The creation logic in `coyote_browser_extension_to_neo4j.py:426-432` is present but relies on `state_manager.last_search_terms_node_id`, which appears not to be preserved between search and subsequent webpage events. Fix requires understanding the full `CoyoteNeo4jStateManager` lifecycle — not a one-line change. Phase C v1 (pure vector) is unaffected.
- **Purpose and SearchTerms not embedded**: Phase B scope excluded these node types. They represent high-value intellectual output (user goals and queries) and should carry `content_role: "output"` embeddings. Target: **Phase B.5** (separate from Phase C v2). Embedding SearchTerms would also partially mitigate the Orphan SearchTerms bug *at query time*, since vector similarity does not depend on the missing `INITIATES`/`GENERATES_SERP` edges. Until B.5 lands, the search-intent branch in `_build_context_hybrid` (chains.py) short-circuits to Tier-1 searches ahead of Tier 0.
- **Tier 0 default time window**: Tier 0 applies the `days_from_text()` default (90d) when the query has no time signal. Long-range semantic recall (e.g., "what have I read about X ever?") requires a `days_from_text_maybe()` sentinel that distinguishes "user specified" from "default used"; Tier 0 would pass `None` to drop the time filter in that case. Target: post-C-v1 follow-up.
- **Scrape effectiveness degradation**: `scrape_webpage.py` returns empty text for a growing share of URLs. Roughly two-thirds of post-Phase-B non-exempt Webpages land in the null-embedding bucket (combined with exempt URLs). Root cause unknown — possibly anti-scraping trends. Future enhancement: add `embedding_skip_reason` property to distinguish "exempt URL" vs. "empty scrape" in Neo4j.
- `images/core/requirements.txt` is an orphan — outside the Dockerfile build context (`images/core/core_analysis/`). The actual file used in builds is `images/core/core_analysis/requirements.txt`. The orphan has diverged (missing `bert-extractive-summarizer`, has a stale `sentence-transformers` edit). Investigate and delete if confirmed unused.

## Vector Embedding Rollout

**Status:** Phase C v1 verified 2026-04-20 (both gates passed). Next candidates: `days_from_text_maybe()` sentinel (low risk, small) or Phase B.5 (embed Purpose/SearchTerms, partially mitigates orphan bug at query time). Orphan fix deferred — required only for Phase C v2, not MVP.

### Architectural Invariants (all phases must preserve)
- `content_role: "input"|"output"` on every embedded node (replaces `isInput` in new CREATEs)
- `embedding_model: "all-MiniLM-L6-v2"` on every embedded node
- `embedding_text: <exact string embedded>` on every embedded node
- Vector indexes: `webpage_embedding`, `annotation_embedding` (384 dims, cosine, per-label)
- Shared constants: `shared/embedding_config.py` (`EMBEDDING_MODEL_NAME`, `EMBEDDING_DIMENSION`)

### Design Vision: Input/Output Separation
The `content_role` distinction is foundational, not cosmetic. Coyote models the user's mind as a black box by embedding two separate corpora:
- **Input embeddings** (`content_role: "input"`): resources the user consumed — Webpage nodes today, anything the user read/watched/visited
- **Output embeddings** (`content_role: "output"`): the user's intellectual products — Annotation nodes today, future Document nodes

Three future capabilities depend on keeping these corpora distinct:
1. **Source inference** — given an output, find inputs that likely informed it via cross-corpus similarity within a relevant time window
2. **Perspective divergence** — quantify where a user's writing diverges from the sources they consumed on the same topic
3. **Longitudinal conceptual modeling** — compare output embeddings across time to track concept evolution, correlated with shifts in inputs

**Design rule for Phase C v2 and beyond:** preserve role labels when assembling LLM context. Do not merge top-K hits from `webpage_embedding` and `annotation_embedding` into an unlabeled blob — downstream reasoning must distinguish consumed content from produced content.

### Model / Infrastructure
- Model: `all-MiniLM-L6-v2` (384-dim, CPU-only, `sentence-transformers==3.3.1`)
- Pre-downloaded at build time to `/opt/embedding_model`; `ENV SENTENCE_TRANSFORMERS_HOME=/opt/embedding_model`
- Core uses `SentenceTransformer` directly; Agent uses `HuggingFaceEmbeddings` (LangChain wrapper)
- No shared volume for model files
- Node provenance: `embedding_generated_at` (ISO UTC string) on every embedded node — not an invariant but required for observability and future model migration

### Phases
| Phase | Scope | Gate |
|-------|-------|------|
| ~~A~~ | ~~`embedding_config.py`, SQLite migrations, `create_vector_index()` fix, Dockerfile/compose~~ | ~~Vector indexes ONLINE, new columns visible, `sentence-transformers` in Core~~ (done) |
| ~~B~~ | ~~`coyote_embedder.py`, NLP Steps 20.5/10.5, Neo4j writers, Core `shared/` sync~~ | ~~Embedded nodes in Neo4j with all invariant properties~~ (done 2026-04-17, Gates 1-4 all passed) |
| ~~C v1~~ | ~~Tier 0 (`_try_tier0_vector`) in `chains.py` — **pure vector** retrieval, no relationship traversal. Runs after search-intent branch, before Tier 1. Role labels `[input]`/`[output]` embedded in result text.~~ | ~~**Gate**: (1) `TIER 0 context: N chars` logged on topic-match queries, no subsequent `TIER 1 context`. (2) `VECTOR_SIMILARITY_THRESHOLD=0.99` forces fallthrough to Tier 1.~~ (done 2026-04-20, both gates passed) |
| Orphan fix | Restore `INITIATES` / `GENERATES_SERP` SearchTerms→Webpage edges (see Known Issues). Risky — touches same file as Phase B; read full `CoyoteNeo4jStateManager` before editing. | `MATCH (st:SearchTerms)-[r]->(w:Webpage) RETURN count(r)` > 0 for new sessions |
| C v2 | Context expansion from vector hits via 1-hop traversal; **must preserve input/output role labels** in LLM context | LLM context blocks assemble input and output nodes with distinct labels |
| D | CLAUDE.md final update | Docs match implementation |

### Fixed in Phase A
- `create_vector_index()` was silently failing (missing `OPTIONS` clause) — indexes now confirmed ONLINE
- CLAUDE.md previously stated indexes existed — corrected

### Phase B Implementation Details
- `coyote_embedder.py`: singleton model with `_model_load_failed` sentinel (no retry spam on permanent failure)
- `shared/embedding_config.py` synced to Core build context via `make sync-shared` (new: `images/core/core_analysis/shared/`)
- Core Dockerfile updated: `COPY shared/ /app/shared/`
- NLP manager: Step 20.5 (webpage) after TF-IDF, Step 10.5 (annotation) after WikiData mapping, both before commit
- Neo4j writers: `isInput` replaced with `content_role` on all new CREATE statements; embedding properties on Webpage and Annotation nodes
- Exempt URLs (`should_exempt_url`: `google.com/search`, `hypothes.is/account|users|oauth`, `localhost:5000/configure`): embedding columns NULL in SQLite, Neo4j node gets `embedding: null, content_role: "input"` — correct behavior
- Empty-scrape URLs (non-exempt but `scrape_webpage` returned no text): same null-embedding outcome. Currently indistinguishable from exempt URLs in Neo4j — see Known Issues.

## Development Patterns
- 3-state returns: `(True=found, False=empty, None=error)`
- Read-only Cypher via regex blocklist
- Input validation via `_validate_string_input()` (50KB cap)
- Schema gating via `_schema_gate()` in ui_server
- All Cypher params via `$param` pattern, never interpolated
- f-strings OK in logs; user input must go through `json.dumps()`
- `shared/` is canonical; run `make sync-shared` after editing to update both agent (`images/agent/app/shared/`) and core (`images/core/core_analysis/shared/`) copies
- Time parsing via `shared.time_utils.days_from_text()` (default 90d)
- NL→Cypher pipeline: `graph_run()` → `_validate_and_execute()` (guards + Neo4j exec) with single-retry for NL queries; on failure, re-calls `_nl_to_cypher(prior_error=...)` with truncated error as `CORRECTION REQUIRED:` suffix
- `PROMPT_GRAPH` rules: no unprompted time filters (rule 2), `datetime()` wrapper required (rule 3), two labeled worked examples

## Testing
```bash
python -m pytest tests/ -v        # 43 tests (security, sync check)
make sync-shared                  # sync nl2cypher.py before docker build
make build-agent                  # sync + rebuild bot container
```

## Security Roadmap
**P1**: ~~LangChain 1.0 migration~~ (done — now on langchain-core 1.2.x, langchain-neo4j 0.7.0)
**P2**: Optional auth, CORS config, rate limiting, vector search activation (Phases B + C v1 done; B.5 / orphan fix / C v2 pending), extension config UI
