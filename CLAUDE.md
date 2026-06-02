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

**Data Flow**: Browser Extension → SQLite staging → trafilatura content extraction → NLP enrichment (spaCy NER, BERTopic/RAKE, embedding) → Neo4j graph → GraphRAG (Tier 0 vector + Tier 1-3) → LLM response

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
- Never `git add -A` or `git add .` — the repo accumulates untracked diagnostic logs (`coyote_server_logs_*.txt`) and scratch artifacts (`tmp_*.csv`) that should never enter the index. Stage by filename.

## Known Issues (Open)
- **Abandoned-search edge misattribution** (residual from orphan fix, 2026-04-21): if the user submits a search but no SERP ever loads (closes tab, network error), `last_search_terms_node_id` stays set on the singleton state manager. The next unrelated webpage — possibly hours later — will receive a `GENERATES_SERP`/`INITIATES` edge attributing it to the abandoned search. Affects edge semantics, not data integrity. A full fix requires session IDs in the event payload (post-MVP).
- **Single-linear-browsing-history assumption**: the `CoyoteNeo4jStateManager` writer assumes one sequential event stream per user. Multiple browser tabs, multiple devices, or opening search results in new tabs all produce interleaved events that break the state machine — the `last_*_node_id` attributes reflect only whichever event the poller processed most recently. Concurrent sessions produce undefined edge topology. Related design tension: `LINKS_TO` chains capture browsing *sequence* but make session-*membership* queries expensive (Phase C v2 would need arbitrary-length traversal to reconstruct which webpages belong to a given SearchTerms). Long-term fix: session ID in the event payload + dual relationships (`SearchTerms-[:INITIATES]->Webpage` for set membership + `Webpage-[:LINKS_TO]->Webpage` for sequence). Phase C v2 designs must not depend on `LINKS_TO` traversal for set membership.
- **Purpose and SearchTerms not embedded**: Phase B scope excluded these node types. They represent high-value intellectual output (user goals and queries) and should carry `content_role: "output"` embeddings. Target: **Phase B.5** (separate from Phase C v2). Until B.5 lands, the search-intent branch in `_build_context_hybrid` (chains.py) short-circuits to Tier-1 searches ahead of Tier 0.
- **Scrape effectiveness degradation**: `scrape_webpage.py` returns empty text for a growing share of URLs. The "two-thirds" figure was measured pre-trafilatura; the current rate on `coyote-0.4` HEAD is unknown — `MVP_REFACTOR_PLAN.md` pre-flight check 6 re-measures it. Future enhancement: add `embedding_skip_reason` property to distinguish "exempt URL" vs. "empty scrape" in Neo4j (targeted by Unit 9c of the 0.5 refactor). Sibling to `wikidata_skip_reason` below — both share the same plumbing path through NLP state manager → Neo4j writer.
- **WikiData-throttled events not tagged in Neo4j** (deferred from MVP breaker work 2026-05-12, extended 2026-05-14): when either WikiData circuit breaker is OPEN — `text_bertopic_analysis.query_wikidata` (term→Q-item lookup) or `connect_to_ontology.batch_query_wikidata` (ancestor traversal) — affected Webpages get empty topic/entity Wikidata mappings or truncated HAS_TOPIC ancestor chains, both indistinguishable from events with no matching labels and from URIs with no Wikidata parents respectively. Forensically recoverable by cross-referencing breaker state-transition log lines (`WikiData circuit breaker tripped/recovered`, `WikiData circuit breaker (ontology) tripped/recovered`) with `Webpage.timestamp`. Sibling to `embedding_skip_reason` above — both require parallel plumbing changes through `query_wikidata`/`batch_query_wikidata`/`scrape_webpage` → NLP state manager → Neo4j writer and should land together post-MVP.
- **Click-tracking redirects captured by browser extension**: the extension records every page navigation, including ephemeral redirect URLs (`google.com/url`, `t.co/`, `lnkd.in/`, etc.) that bounce to the real destination in <1s. The Python-side filter in `should_exempt_url` (`_REDIRECT_HOST_PATTERNS`) prevents these from creating Webpage nodes, but the events still reach SQLite staging and the NLP queue, doing avoidable work. Complete fix lives in the extension (filter before staging). Post-MVP.
- **`isSERP` detector is Google-only** (identified 2026-06-01): `coyote_browser_extension_to_neo4j.py:298` sets `is_serp = "- Google Search" in title or url.startswith("https://www.google.com/search?")`. Non-Google SERPs (Bing, DuckDuckGo, Brave, Kagi, Yahoo, Ecosia, etc.) get `isSERP = false`, are treated as content pages, and pollute topic-extraction denominators, gate measurements that filter `WHERE w.isSERP = false`, and any future content-vs-search-activity analysis. Detection asymmetry across layers: the Python-side `should_exempt_url()` (NLP-pipeline skip) and the Neo4j-side `isSERP` flag (Webpage tagging) use different criteria — Google-search URLs are covered by both, non-Google search URLs by neither. **Load-bearing for Unit 1 of the 0.5 refactor**, which relies on the existing `isSERP` flag for the SERP partition rather than introducing a redundant `topic_skip_reason` tag, so this gap directly bounds Unit 1's gate accuracy. Post-MVP fix: broaden the detector to match search-engine URL patterns and titles consistently across both sides via a shared `is_serp_url()` predicate; backfill historical Webpages or accept the cohort skew.
- ~~**`"day"`/`"days"` leak through `_terms()` STOP set** (chains.py)~~ (resolved 2026-05-07, MVP Fix 1): added `day`, `days`, `hour`, `hours`, `minute`, `minutes`, `ago`, `lately`, `currently` to the STOP set in `_build_context_hybrid`.
- **LLM hallucinates empty-result response despite populated context** — deferred; see Post-MVP § Deferred items with concrete scope.
- **Wikidata entity disambiguation (Layer 2 — NER mapping)** — *targeted by Unit 8 of `MVP_REFACTOR_PLAN.md`*: `map_ner_to_wikidata` / `map_topics_to_wikidata` resolve ambiguous short tokens to wrong Q-items (observed: `"ai"` → Anguilla, `"gpt"` → "GNU Portable Threads", `"First Monday"` → calendar date). SPARQL returns the first label match irrespective of prominence or context. Refactor approach: a semantic-similarity post-filter reusing the Phase B embedder — score the page/topic context against each candidate's WikiData description and pick the best, falling back to "no mapping" below threshold. Layer 1 (Wikimedia infrastructure cascade) was targeted by Session 3; this Layer 2 problem is targeted by the refactor.
- ~~**Entity TF-IDF scores are uniformly 0.0**~~ (resolved 2026-04-30, Session 1.5): `coyote_nlp_state_manager.py` Step 20's `WHERE event_id=? AND entity=?` was case-sensitive, but `term` came from sklearn `TfidfVectorizer.get_feature_names_out()` which lowercases by default. Fixed by adding `COLLATE NOCASE` to the WHERE clause. Residual minor edge case: entities containing non-word characters (`"AT&T"`, `"U.S."`) still won't match because sklearn's default `token_pattern=r'(?u)\b\w\w+\b'` strips them — minor share of entities, post-MVP.
- **HAS_TOPIC `tfidf_score` historical edges unreliable** — *targeted by Unit 3 of `MVP_REFACTOR_PLAN.md`*: edges created before the Session 1 score-plumbing fix (2026-04-30) carry the old broadcast value (first entity's score replicated across all edges from that source). Single-distinct-score patterns in the graph are mostly historical, not new. New events post-fix produce correctly varied per-edge scores. The 0.5 refactor replaces the TF-IDF score with KeyBERT cosine similarity entirely, so historical edges become a moot legacy concern after that lands. Historical data will turn over naturally as Webpages age out of relevance windows; no backfill planned.
- **`tfidf_score` score-type mixing on rare topics** — *dissolved by Unit 3 of `MVP_REFACTOR_PLAN.md`*: `Topics.score` is set by Step 13's sklearn `TfidfVectorizer` (L2-normalized, ≤1) only when the topic survives the `threshold=0.07` filter; topics filtered out keep their original BERTopic c-TF-IDF score, which is not L2-normalized and can exceed 1.0. Observed once: a "10" topic on a "10 graphic novels" page carrying score 6.874 (5 edges of 7,128 affected, 0.07%). Cosmetic — does not break any consumer that thresholds at the low end. Refactor replaces the whole TF-IDF path with KeyBERT cosine similarity.
- **TF-IDF corpus is not what its name implies** — *targeted by Unit 3 of `MVP_REFACTOR_PLAN.md`*: the production NLP path at `coyote_nlp_state_manager.py:607,610,665` calls `calculate_tfidf_on_phrases` with a corpus loaded from `SELECT content FROM CorpusDocuments WHERE source='TEDTalk' LIMIT 500`. TED Talks are a wrong-domain reference for arbitrary web content; the IDF component is computed against a 500-doc subset of TED transcripts, which doesn't represent the user's actual browsing. Brittle to empty `CorpusDocuments` — if no TEDTalk rows exist, `corpus = []` and IDF degenerates silently. Sessions 1/2 gate measurements depend on this corpus being populated. The placeholder corpus in `text_bertopic_analysis.py:385-391` (`"Sample text corpus for reference."` etc.) is only used when `get_topic_from_text(corpus=None)` is called directly — that function has zero callers in production (verified via grep 2026-05-27); it is dead code that the refactor removes.
- **Hypothesis-only Webpages have no `timestamp` property** — *new, identified 2026-05-27*: `hypothesis_to_neo4j.py:127-128` creates Webpage nodes via `MERGE (w:Webpage {url: $url}) ON CREATE SET w.title = $webpage_title` — only `title` is set on create. Webpage nodes whose URLs first reach Neo4j via a Hypothesis annotation (not via the browser extension) have a NULL `timestamp` property. Effects: (a) ad-hoc Cypher like `ORDER BY datetime(w.timestamp) DESC LIMIT 50` sorts these to the TOP in DESC order (Neo4j sorts NULLs first in DESC), producing surprising "most recent" results — add `WHERE w.timestamp IS NOT NULL` to filter them out for ad-hoc queries; (b) chains.py Tier 1 time-window queries silently exclude these nodes (NULL `datetime()` fails the `>=` comparison), which is the correct behavior for "recent activity" queries but means Hypothesis-only Webpages are invisible to those tiers. Important: this does NOT indicate that Neo4j's `datetime()` function is broken on the documented browser-extension format (`datetime.now().isoformat()` produces parseable naive ISO 8601 strings — the mandate at the top of this file remains correct). Long-term fix: set `w.timestamp` from the annotation's `created` field on first MERGE, or mark it imputed with a sentinel value. Post-MVP unless it bites a Tier 0/1 query.
- `images/core/requirements.txt` is an orphan — outside the Dockerfile build context (`images/core/core_analysis/`). The actual file used in builds is `images/core/core_analysis/requirements.txt`. The orphan has diverged (missing `bert-extractive-summarizer`, has a stale `sentence-transformers` edit). Investigate and delete if confirmed unused.

## MVP Roadmap

Three tiers; details for each tier live in their own sections or referenced documents.

**Shipped on `coyote-0.4`:**
- Phases A / B / Cv1 (vector embedding rollout — see below), Orphan SearchTerms fix.
- Sessions 1 / 1.5 / 2 / 3 (HAS_TOPIC edge-quality stabilization — see below).
- Fix 1 (chains.py `_terms()` STOP-set extension).
- WikiData circuit breakers (both `query_wikidata` and `batch_query_wikidata`).
- Trafilatura content extraction.

**Active refactor on `coyote-0.5-nlp-refactor`** (10 units; pre-flight checks in progress at time of writing):
- See `MVP_REFACTOR_PLAN.md` (repo root) for full scope, sequence, gates, and risk register.
- High-impact bundle: KeyBERT swap + per-doc chunk-and-pool (Unit 2+3). Replaces BERTopic and fixes the silent embedding truncation at Phase B.
- Other units: WikiData term cache + mwapi fuzzy matching, NER mention-frequency scoring, trafilatura metadata harvesting, polish (title-boost, edge cap, skip-reason tagging, P31 instance-of blocklist), and MTEB embedder swap as the closing parameter pass.

**Post-refactor MVP work** (after `coyote-0.5-nlp-refactor` lands, still pre-launch):
- **Phase B.5** — embed Purpose and SearchTerms nodes (`content_role: "output"`). Documented under Vector Embedding Rollout.
- **Phase C v2** — context expansion from vector hits via 1-hop traversal, preserving input/output role labels. Documented under Vector Embedding Rollout.
- **Comprehensive test protocol** — fix any MVP-killing bugs surfaced during.
- **Pre-launch artifacts** — revise Setup-panel instructions, README, launch communication plan; collect early tester feedback; LAUNCH.

## MVP Pre-Launch Work (shipped, retained for archaeology)
**Goal at the time:** harden HAS_TOPIC edge quality before public MVP. Sequenced fixes (all shipped on `coyote-0.4`):
- ~~**Session 1**~~ (shipped 2026-04-30): per-topic score plumbing in `connect_to_ontology.py`. `extract_uris_from_node_data` now returns `List[Tuple[str, float]]`; `get_score_from_node_data` deleted (was the broadcast bug).
- ~~**Session 1.5**~~ (shipped 2026-04-30): added `COLLATE NOCASE` to the `UPDATE Entities SET score=...` WHERE clause in Step 20 of `coyote_nlp_state_manager.py`.
- ~~**Session 2**~~ (shipped 2026-05-06): `TFIDF_TOPIC_THRESHOLD` env var (default `0.15`) applied at the entry of `_process_single_event`'s URI loop. Drops low-scored root URIs and their full WikiData ancestor tree. **Renamed `TOPIC_SCORE_THRESHOLD` by 0.5 refactor when KeyBERT replaces TF-IDF.**
- ~~**Fix 1**~~ (shipped 2026-05-07): added `day`, `days`, `hour`, `hours`, `minute`, `minutes`, `ago`, `lately`, `currently` to the `_terms()` STOP set in `chains.py`.
- ~~**Session 3**~~ (shipped 2026-05-07, gate status below): ontology entry-point cleanup in `connect_to_ontology.py`. Three changes: (1) dropped P910 from the ancestor SPARQL; (2) post-query filter for `WIKIMEDIA_META_URIS` (Q4167836, Q15184295, Q4167410, Q14204246, Q11266439, Q13406463); (3) `MAX_RECURSION_DEPTH` 5 → 3.

**Session 3 gate status** (re-classified 2026-05-27 in light of 0.5 refactor):
- **Gate B** — deferred and superseded (resolved 2026-05-28). Cannot be measured on current data: WDQS throttling contaminates Wikidata-enriched events, leaving topic/entity mappings sparse or missing — Gate B's HAS_TOPIC → meta-class query has nothing meaningful to count. Additionally, Units 3/9b/9d of the 0.5 refactor change which terms reach Wikidata and filter edges differently, so any pre-refactor Gate B number is invalid for the shipped system. Gate B's intent (zero meta-class edges) is preserved as a post-refactor verification requirement under Unit 9d (P31 instance-of blocklist). Run after first database wipe on `coyote-0.5-nlp-refactor`.
- **Gate A** — informational. The 0.5 refactor's KeyBERT-based topic extraction, Unit 9b edge cap, and Unit 9d P31 instance-of blocklist all change edge counts in ways that supersede this gate's reference point.
- **Gate C** — informational. Replaced by Unit 3's KeyBERT verification gate (Gate 3.1 in the refactor plan).

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
| Phase | Scope | Status |
|-------|-------|------|
| ~~A / B / C v1 / Orphan fix~~ | Vector infrastructure, embedding pipeline, Tier 0 retrieval, SearchTerms→Webpage edge restoration | Shipped 2026-04-17 through 2026-04-21; all gates passed. Details retained in archived sections below. |
| ~~Sessions 1 / 1.5 / 2 / 3, Fix 1~~ | HAS_TOPIC edge-quality stabilization | Shipped on `coyote-0.4` between 2026-04-30 and 2026-05-07. See "MVP Pre-Launch Work" section for per-session detail; Session 3 gate status re-classified there. |
| **0.5 refactor** | KeyBERT swap + chunk-and-pool + WikiData cache/fuzzy + NER mention-frequency + metadata harvesting + MTEB swap | **Active** on `coyote-0.5-nlp-refactor`. See `MVP_REFACTOR_PLAN.md` for 10-unit breakdown, 8 pre-flight checks, env-var audit, and verification gates. |
| B.5 | Embed Purpose and SearchTerms nodes (`content_role: "output"`). Updates `_build_context_hybrid` to use Tier 0 for search-intent queries. | After 0.5 refactor lands, still pre-launch MVP work. |
| C v2 | Context expansion from vector hits via 1-hop traversal; **must preserve input/output role labels** in LLM context. | After 0.5 refactor lands, still pre-launch MVP work. |
| D | Comprehensive pre-MVP test protocol; CLAUDE.md final update; pre-launch artifacts (Setup panel, README, launch comms). | After C v2 lands. |

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

## Post-MVP

Catch-all for work that's named but explicitly NOT in the MVP scope. Two flavors: concrete deferred items, and big-vision items still short on detail.

### Deferred items with concrete scope
- **Session ID in event payload** — fixes the abandoned-search edge misattribution AND the single-linear-browsing-history assumption simultaneously. Both already documented in Known Issues. Larger work than the refactor; explicitly out of scope.
- **Browser extension click-tracking redirect filter** — already filtered in `should_exempt_url` Python-side, but the events still reach SQLite staging and the NLP queue. Complete fix lives in the extension, which has not been touched in this development line.
- **Browser extension configuration UI** — currently configured via UI server forms (Neo4j credentials, Hypothesis tokens). Extension-side config would be a separate effort.
- **Hypothesis-only Webpage `timestamp` fix** — set `w.timestamp` from the annotation's `created` field on first MERGE (see Known Issues for full context).
- **Hyperlink / Webpage / Annotation queue-update bug** — small fix outside the WikiData scope of the 0.5 refactor.
- **OpenTapioca investigation** — alternative entity-linking pipeline; deferred evaluation track.
- **Python datetime aware-form migration** — for 3.12+ readiness. Codebase currently mixes `datetime.now()`, `datetime.utcnow()`, and `datetime.now(timezone.utc)`. Not MVP-blocking on Python 3.11; becomes a blocker when the base image moves to 3.12+.
- **Optional auth, CORS, rate limiting** — Security Roadmap P2 items.
- **AT&T / U.S. entity NER edge cases** — sklearn's default `token_pattern=r'(?u)\b\w\w+\b'` strips non-word characters from entity names; affects a minor share of entities.
- **Wikidata-throttled events not tagged in Neo4j** — `embedding_skip_reason` and `wikidata_skip_reason` properties, plumbing changes through `query_wikidata` / `batch_query_wikidata` / `scrape_webpage` → NLP state manager → Neo4j writer. Unit 9c of the 0.5 refactor targets `embedding_skip_reason` specifically; full sibling implementation including `wikidata_skip_reason` is post-MVP.
- **LLM hallucinates empty-result response despite populated context** — observed during Phase C v1 gate verification (2026-04-21): a Tier 1 query assembled 557 chars of real context, but the LLM answered "I couldn't find anything matching your query in the selected time window." Pre-existing prompt-following issue, not specific to Tier 0. Investigate `PROMPT_RAG` wording and whether the empty-result instruction is over-weighted.
- **Remove `/legacy` route + delete `coyote_wireframe.html`** — once `wireframe_v2.html` stability is confirmed in production.
- **PDF extraction fallback (`pypdf`)** — trafilatura returns empty on PDF URLs. Pre-flight 6 measured 3/50 URLs as PDFs across a typical academic mix. Add `pypdf` as a content-type-routed fallback in `scrape_webpage.py`. Targets the PDF subset of the scrape-effectiveness Known Issue above.

### Big-vision items, short on detail
These extend the "Design Vision: Input/Output Separation" section above.
- **Section-level chunk persistence and retrieval** — the post-MVP feature that motivates the generic chunking module in Unit 3a. Use case: "I read an article 1-2 years ago about [paraphrased argument]." Requires persisting chunk text + per-chunk embedding as separate nodes/properties; not just per-doc pooling.
- **Source inference** — given an output (Annotation), find inputs (Webpages) that likely informed it via cross-corpus vector similarity within a relevant time window. Depends on Phase B.5 (Purpose/SearchTerms embedded) and a stable input/output role-label distinction in retrieval.
- **Perspective divergence** — quantify where a user's writing diverges from the sources they consumed on the same topic.
- **Longitudinal conceptual modeling** — compare output embeddings across time to track concept evolution, correlated with shifts in inputs.
- **Wikidata embedding centroid filter for disambiguation** — Justin's Part-4 vision from the 4-part disambiguation plan. Blocked on 25-100 GB local-deployment problem for the Wikidata Embedding Project's full corpus; not viable until smaller distillations or on-demand fetch infrastructure exist.
- **Two-stage NER-context-aware SPARQL disambiguation** — uses NER context as a SPARQL filter when resolving ambiguous labels. Deferred indefinitely due to per-page query-count cost (~4000 queries-per-page worst case).
- **Bidirectional `Annotation → Webpage` links** — currently Hypothesis annotations create a one-way `HAS_ANNOTATION` from Webpage, but the user's intellectual output rarely points back to the input that produced it explicitly.

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
