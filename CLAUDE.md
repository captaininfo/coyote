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
| `tests/test_security.py` | Blocklist + sync guard unit tests (44 cases) |

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
| TFIDF_TOPIC_THRESHOLD | 0.15 | Drop HAS_TOPIC root URIs whose tfidf_score is below this (MVP Session 2) |
| WIKIDATA_BREAKER_THRESHOLD | 1 | Consecutive 403/429 from WDQS before tripping the WikiData circuit breakers in `text_bertopic_analysis.query_wikidata` and `connect_to_ontology.batch_query_wikidata` (independent instances, same env vars) |
| WIKIDATA_BREAKER_COOLDOWN | 1800 | Seconds either breaker stays OPEN before a single half-open probe is allowed |

### Things NOT To Do
- Never expose ports to public internet
- Never commit `.env` with real credentials
- Never use bare `except:` blocks
- Never interpolate user input into f-strings for code/queries (use `json.dumps()`)
- Never enable `USE_LC_NL2CYPHER=1` — bypasses read-only validation entirely

## Known Issues (Open)
- **Abandoned-search edge misattribution** (residual from orphan fix, 2026-04-21): if the user submits a search but no SERP ever loads (closes tab, network error), `last_search_terms_node_id` stays set on the singleton state manager. The next unrelated webpage — possibly hours later — will receive a `GENERATES_SERP`/`INITIATES` edge attributing it to the abandoned search. Affects edge semantics, not data integrity. A full fix requires session IDs in the event payload (post-MVP).
- **Single-linear-browsing-history assumption**: the `CoyoteNeo4jStateManager` writer assumes one sequential event stream per user. Multiple browser tabs, multiple devices, or opening search results in new tabs all produce interleaved events that break the state machine — the `last_*_node_id` attributes reflect only whichever event the poller processed most recently. Concurrent sessions produce undefined edge topology. Related design tension: `LINKS_TO` chains capture browsing *sequence* but make session-*membership* queries expensive (Phase C v2 would need arbitrary-length traversal to reconstruct which webpages belong to a given SearchTerms). Long-term fix: session ID in the event payload + dual relationships (`SearchTerms-[:INITIATES]->Webpage` for set membership + `Webpage-[:LINKS_TO]->Webpage` for sequence). Phase C v2 designs must not depend on `LINKS_TO` traversal for set membership.
- **Purpose and SearchTerms not embedded**: Phase B scope excluded these node types. They represent high-value intellectual output (user goals and queries) and should carry `content_role: "output"` embeddings. Target: **Phase B.5** (separate from Phase C v2). Until B.5 lands, the search-intent branch in `_build_context_hybrid` (chains.py) short-circuits to Tier-1 searches ahead of Tier 0.
- **Scrape effectiveness degradation**: `scrape_webpage.py` returns empty text for a growing share of URLs. Roughly two-thirds of post-Phase-B non-exempt Webpages land in the null-embedding bucket (combined with exempt URLs). Root cause unknown — possibly anti-scraping trends. Future enhancement: add `embedding_skip_reason` property to distinguish "exempt URL" vs. "empty scrape" in Neo4j. Sibling to `wikidata_skip_reason` below — both share the same plumbing path through NLP state manager → Neo4j writer and should be implemented together post-MVP.
- **WikiData-throttled events not tagged in Neo4j** (deferred from MVP breaker work 2026-05-12, extended 2026-05-14): when either WikiData circuit breaker is OPEN — `text_bertopic_analysis.query_wikidata` (term→Q-item lookup) or `connect_to_ontology.batch_query_wikidata` (ancestor traversal) — affected Webpages get empty topic/entity Wikidata mappings or truncated HAS_TOPIC ancestor chains, both indistinguishable from events with no matching labels and from URIs with no Wikidata parents respectively. Forensically recoverable by cross-referencing breaker state-transition log lines (`WikiData circuit breaker tripped/recovered`, `WikiData circuit breaker (ontology) tripped/recovered`) with `Webpage.timestamp`. Sibling to `embedding_skip_reason` above — both require parallel plumbing changes through `query_wikidata`/`batch_query_wikidata`/`scrape_webpage` → NLP state manager → Neo4j writer and should land together post-MVP.
- **Click-tracking redirects captured by browser extension**: the extension records every page navigation, including ephemeral redirect URLs (`google.com/url`, `t.co/`, `lnkd.in/`, etc.) that bounce to the real destination in <1s. The Python-side filter in `should_exempt_url` (`_REDIRECT_HOST_PATTERNS`) prevents these from creating Webpage nodes, but the events still reach SQLite staging and the NLP queue, doing avoidable work. Complete fix lives in the extension (filter before staging). Post-MVP.
- ~~**`"day"`/`"days"` leak through `_terms()` STOP set** (chains.py)~~ (resolved 2026-05-07, MVP Fix 1): added `day`, `days`, `hour`, `hours`, `minute`, `minutes`, `ago`, `lately`, `currently` to the STOP set in `_build_context_hybrid`.
- **LLM hallucinates empty-result response despite populated context**: observed during Phase C v1 gate verification (2026-04-21): a Tier 1 query assembled 557 chars of real context, but the LLM answered "I couldn't find anything matching your query in the selected time window." Pre-existing prompt-following issue, not specific to Tier 0. Investigate `PROMPT_RAG` wording and whether the empty-result instruction is over-weighted.
- **Wikidata entity disambiguation (Layer 2 — NER mapping)**: `map_ner_to_wikidata` / `map_topics_to_wikidata` resolve ambiguous short tokens to wrong Q-items (observed: `"ai"` → Anguilla, `"gpt"` → "GNU Portable Threads", `"First Monday"` → calendar date). SPARQL returns the first label match irrespective of prominence or context. Fix path: a semantic-similarity post-filter reusing the Phase B `all-MiniLM-L6-v2` embedder — score the page/topic context against each candidate's WikiData description and pick the best, falling back to "no mapping" below threshold. Investigate `map_topics_to_wikidata` first to see whether a cheaper upstream fix (e.g., minimum-token-length guard, label-equality preference) helps before adding the embedder dependency. Layer 1 (Wikimedia infrastructure cascade through ancestor traversal) is targeted by Session 3 (implemented 2026-05-07, gates pending); this Layer 2 problem remains separate and post-MVP.
- ~~**Entity TF-IDF scores are uniformly 0.0**~~ (resolved 2026-04-30, Session 1.5): `coyote_nlp_state_manager.py` Step 20's `WHERE event_id=? AND entity=?` was case-sensitive, but `term` came from sklearn `TfidfVectorizer.get_feature_names_out()` which lowercases by default. Fixed by adding `COLLATE NOCASE` to the WHERE clause. Residual minor edge case: entities containing non-word characters (`"AT&T"`, `"U.S."`) still won't match because sklearn's default `token_pattern=r'(?u)\b\w\w+\b'` strips them — minor share of entities, post-MVP.
- **HAS_TOPIC `tfidf_score` historical edges unreliable**: edges created before the Session 1 score-plumbing fix (2026-04-30) carry the old broadcast value (first entity's score replicated across all edges from that source). Single-distinct-score patterns in the graph are mostly historical, not new. New events post-fix produce correctly varied per-edge scores (verified: a single Frontiers article shows 41 distinct scores across 215 edges). Historical data will turn over naturally as Webpages age out of relevance windows; no backfill planned.
- **`tfidf_score` score-type mixing on rare topics**: `Topics.score` is set by Step 13's sklearn `TfidfVectorizer` (L2-normalized, ≤1) only when the topic survives the `threshold=0.07` filter; topics filtered out keep their original BERTopic c-TF-IDF score, which is not L2-normalized and can exceed 1.0. Observed once: a "10" topic on a "10 graphic novels" page carrying score 6.874 (5 edges of 7,128 affected, 0.07%). Cosmetic — does not break any consumer that thresholds at the low end. Post-MVP fix: rescore topics dropped by sklearn with a deterministic substitute (e.g., set to threshold-1) instead of leaving the BERTopic value untouched.
- `images/core/requirements.txt` is an orphan — outside the Dockerfile build context (`images/core/core_analysis/`). The actual file used in builds is `images/core/core_analysis/requirements.txt`. The orphan has diverged (missing `bert-extractive-summarizer`, has a stale `sentence-transformers` edit). Investigate and delete if confirmed unused.

## MVP Pre-Launch Work (in progress)
**Goal:** harden HAS_TOPIC edge quality before public MVP. Sequenced fixes:
- ~~**Session 1**~~ (shipped 2026-04-30): per-topic score plumbing in `connect_to_ontology.py`. `extract_uris_from_node_data` now returns `List[Tuple[str, float]]` reading per-item scores from each NLP-output JSON dict; `get_score_from_node_data` deleted (was the broadcast bug); `_process_single_event` iterates `(uri, score)` pairs. Pattern 1 (current production) carries real scores; legacy patterns 2/3 default to 0.0. Verified: distinct scores observed across edges from the same source.
- ~~**Session 1.5**~~ (shipped 2026-04-30): added `COLLATE NOCASE` to the `UPDATE Entities SET score=...` WHERE clause in Step 20 of `coyote_nlp_state_manager.py`. Entity TF-IDF scores now populate correctly. No backfill (data is expendible). Re-run percentile baseline before Session 2 threshold tuning.
- ~~**Session 2**~~ (shipped 2026-05-06): `TFIDF_TOPIC_THRESHOLD` env var (default `0.15`, ≈p50 of the post-1.5 distribution) applied at the entry of `_process_single_event`'s URI loop. Roots below threshold are skipped along with their entire WikiData ancestor tree. Per-event INFO log records the skip count; per-URI DEBUG log gives the dropped score. Legacy URI Patterns 2/3 default to score 0.0 and are filtered out at any positive threshold (intentional — Pattern 1 is the only shape current production NLP writes).
- ~~**Fix 1**~~ (shipped 2026-05-07): added `day`, `days`, `hour`, `hours`, `minute`, `minutes`, `ago`, `lately`, `currently` to the `_terms()` STOP set in `chains.py`.
- **Session 3** (implemented 2026-05-07, deploy + gates pending): ontology entry-point cleanup in `connect_to_ontology.py`. Three changes: (1) dropped P910 ("topic's main category") from the ancestor SPARQL — eliminates the `Category:X` gateway and the downstream Wikimedia-meta cascade. (2) post-query, pre-cache filter drops parents in `WIKIMEDIA_META_URIS` (Q4167836, Q15184295, Q4167410, Q14204246, Q11266439, Q13406463) so they never persist or get traversed. (3) `MAX_RECURSION_DEPTH` 5 → 3 to cap fan-out per surviving root.

**Deploy procedure (data is expendable):**
Default volume paths assume `NEO4J_DATA_DIR` and `COYOTE_USER_DATA` env vars are unset (compose.yaml defaults: `./volumes/neo4j`, `./volumes/coyote`, resolved relative to `compose/`). If overridden in `.env` or shell, substitute the override paths.
```bash
# from project root
cd compose
docker compose --profile core --profile llm --profile agent down
sudo rm -rf ./volumes/neo4j
rm -f ./volumes/coyote/wikidata_cache.db
docker compose --profile core --profile llm --profile agent up -d --build
cd ..
```
All three profiles are required: `bot` (agent profile) has a hard `depends_on: llm`, so omitting `--profile llm` triggers `service "bot" depends on undefined service "llm": invalid compose project`. Same combination as `ui/coyote_ui_server.py:327`.
Vector indexes recreate automatically on first node insert. SQLite source-of-truth is untouched; Webpage/Annotation/Purpose/SearchTerms nodes rebuild from event data on next NLP cycle. Tier 0 starts cold (Webpage embeddings repopulate as new browsing comes in) — acceptable per data-expendable invariant.

**Verification gates** (run 2hr post-deploy after some browsing):
- Gate A — material reduction in avg HAS_TOPIC edges/page from ~37 baseline (24hr pre-Session-3 window): `MATCH (w:Webpage)-[r:HAS_TOPIC]->() WHERE datetime(w.timestamp) > datetime() - duration({hours: 2}) WITH w, count(r) AS edges_per_page RETURN avg(edges_per_page) AS avg_edges, count(w) AS webpages;`
- Gate B — zero meta-class edges (real pass/fail): `MATCH (w:Webpage)-[:HAS_TOPIC]->(t:WikiDataOntology) WHERE datetime(w.timestamp) > datetime() - duration({hours: 2}) AND t.label IN ['Wikimedia category', 'Wikimedia administration category', 'Wikimedia disambiguation page', 'Category:Wikipedia categorization'] RETURN count(*) AS junk_edges;`
- Gate C — qualitative top-edge spot-check (records(15)-style query); top-scored edges should be semantically meaningful root entities, not infrastructure ancestors.

## Vector Embedding Rollout

**Status:** Phase C v1 verified 2026-04-20; `days_from_text_maybe()` sentinel shipped 2026-04-21 (Tier 0 drops the time filter when the query has no temporal signal). Orphan SearchTerms fix shipped 2026-04-21: reset `last_webpage_node_id` on new search events + ORDER BY `created_at`/`id` on the two poller fetches (two residual issues documented above). Next candidate: Phase B.5 (embed Purpose/SearchTerms).

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
| ~~Orphan fix~~ | ~~Restore `INITIATES` / `GENERATES_SERP` SearchTerms→Webpage edges: reset `last_webpage_node_id` on search events; add ORDER BY to poller fetches.~~ | ~~`MATCH (st:SearchTerms)-[r]->(w:Webpage) RETURN count(r)` > 0 for new sessions~~ (done 2026-04-21) |
| ~~MVP Session 1~~ | ~~Per-topic score plumbing in `connect_to_ontology.py`: `extract_uris_from_node_data` returns `List[Tuple[str, float]]`; delete `get_score_from_node_data` broadcast bug.~~ | ~~A single Webpage with multiple root entities shows distinct `tfidf_score` values across its HAS_TOPIC edges (verified: 41 distinct scores across 215 edges from one source).~~ (done 2026-04-30) |
| ~~MVP Session 1.5~~ | ~~`COLLATE NOCASE` on Step 20's `UPDATE Entities SET score=...` WHERE clause in `coyote_nlp_state_manager.py`.~~ | ~~`SELECT count(*) FROM Entities WHERE score > 0.0` returns >0 after processing one new event (was 0 across all historical data pre-fix).~~ (done 2026-04-30) |
| ~~MVP Session 2~~ | ~~`TFIDF_TOPIC_THRESHOLD` env var (default 0.15) applied at the URI loop entry in `connect_to_ontology._process_single_event`. Drops root URIs with score below threshold and skips their full WikiData ancestor tree.~~ | ~~Post-deploy edge counts: HAS_TOPIC creation rate per webpage drops materially vs pre-Session-2 baseline; per-event INFO log records skip count.~~ (done 2026-05-06) |
| MVP Session 3 | Ontology entry-point cleanup in `connect_to_ontology.py`: drop P910 from ancestor SPARQL, post-query filter for `WIKIMEDIA_META_URIS`, `MAX_RECURSION_DEPTH` 5 → 3. Volume wipe on deploy (Neo4j + wikidata_cache.db). | Gate B: zero edges to Wikimedia meta-class labels in 2hr post-deploy window. Gate A: avg edges/page drops materially from ~37 baseline. Gate C: qualitative top-edge review semantically meaningful. (implemented 2026-05-07, gates pending) |
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
- Time parsing: `shared.time_utils.days_from_text()` (default 90d) or `days_from_text_maybe()` (returns `Optional[int]`, `None` when no temporal signal — used by Tier 0 to drop the time filter)
- NL→Cypher pipeline: `graph_run()` → `_validate_and_execute()` (guards + Neo4j exec) with single-retry for NL queries; on failure, re-calls `_nl_to_cypher(prior_error=...)` with truncated error as `CORRECTION REQUIRED:` suffix
- `PROMPT_GRAPH` rules: no unprompted time filters (rule 2), `datetime()` wrapper required (rule 3), two labeled worked examples
- Python datetime: codebase is mixed — `coyote_server.py` and `text_bertopic_analysis.py` use `datetime.utcnow()` (naive); `coyote_embedder.py` uses `datetime.now(timezone.utc)` (aware). Core image is Python 3.11 (no deprecation warnings). Project-wide migration to the aware form is a Python 3.12+ readiness item, not MVP-blocking.

## Testing
```bash
python -m pytest tests/ -v        # 85 tests (security, sync check, time parsing, wikidata breakers x2)
make sync-shared                  # sync nl2cypher.py before docker build
make build-agent                  # sync + rebuild bot container
```

## Security Roadmap
**P1**: ~~LangChain 1.0 migration~~ (done — now on langchain-core 1.2.x, langchain-neo4j 0.7.0)
**P2**: Optional auth, CORS config, rate limiting, vector search activation (Phases B + C v1 + orphan fix done; B.5 / C v2 pending), extension config UI
