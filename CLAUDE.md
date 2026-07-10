# Coyote Development Guide
**Target: ~2700 tokens max. Keep concise.**

## Project Philosophy
Coyote is a local-first, privacy-first heutagogical learning tool. It transforms browsing behavior into a semantic graph for AI-enhanced self-determined learning.

**Architectural principle — LLMs verbalize; they do not compute the record.** The semantic record and every analysis over it (embeddings, NLP enrichment, graph structure, similarity/divergence measures) stay deterministic, explainable, and reproducible. LLMs are confined to the UI layer — helping the user interact with, query, summarize, and visualize their data — and never compute or mutate the stored record or its analyses. This keeps the data layer auditable and the analysis layer reproducible.

**Architectural principle — one coherent unit of content per embedding; cross-node relationships are edges, not concatenation.** Each vector represents a single unit of content with one provenance and one `content_role` (a webpage's own text; an annotation's own prose). Content belonging to a *different* node is never folded into another node's vector — the referential link lives in a graph edge (`HAS_ANNOTATION`) plus the stored source property (`highlighted_text`) and is recombined at query-time by traversal, not by pre-embedding concatenation. Same-node enrichment (a page's `title + body`, a doc's own chunks pooled) is fine; only cross-node content-mixing is forbidden. *Why:* fusing two units into one vector is lossy and irreversible; keeping them separate is lossless and recombinable, preserving optionality for future analyses (source-inference, divergence, RSA, regression) that may want the units apart or together. *Known deviation:* the current Annotation embedding (`build_annotation_embedding_text`) still bakes in the source page's `highlighted_text` (cross-node input bleed, against this principle) **and** an `Entities:` digest (an extraction artifact, against Unit 3's "embeddings are a function of `(content, embedder)` only") — two independent defects; Phase B.5 resolves both by pooling the annotation's own prose.

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

**Data Flow**: Browser Extension → SQLite staging → trafilatura content extraction → NLP enrichment (spaCy NER, KeyBERT/RAKE, token-quality filter, pooled full-doc embedding) → Neo4j graph → GraphRAG (Tier 0 vector + Tier 1-3) → LLM response

**Token-quality filter (Unit 6, `analysis/nlp/token_filter.py`):** drops junk tokens (`pp.`, `978`, bare years, single chars, citation fragments, stopword-only/non-alpha-dominant phrases, numeric/date NER labels) from topics and entities on all four event paths before storage. On the webpage path only, an entity mention-frequency floor (`ENTITY_MAP_MENTION_FLOOR`, +optional `ENTITY_MAP_CAP`) caps how many entities are sent to WikiData, keeping the Unit 7 Action-API request volume rate-safe.

**WikiData term→QID lookup (Unit 6/7, `analysis/wikidata_lookup.py`):** `query_wikidata(term)` hits the Wikibase **Action API** (`www.wikidata.org/w/api.php?action=wbsearchentities`, `requests`), returning prominence-ranked candidate triples `(label, concepturi, description)` (K=7, `description` rides inline for Unit 8). Unit 7 moved this off the **WDQS SPARQL** endpoint (`query.wikidata.org/sparql`), whose per-IP throttle zeroed coverage on the Units 1-4 replay (one 429 + breaker threshold=1). **Two independent WikiData clients, only one migrated:** the high-volume term→QID NLP path now uses the Action API; `connect_to_ontology.batch_query_wikidata` (P279/P31 **ancestor traversal**, a graph query the Action API cannot serve) stays on WDQS SPARQL with its own breaker. *Determinism:* the local NLP (spaCy/KeyBERT/embeddings) stays deterministic; the WikiData *linking* step has always been a live external lookup, and the old exact-label SPARQL was `LIMIT 1` with no `ORDER BY` (arbitrary) — Action API prominence ranking is strictly more principled. Within-TTL reproducibility comes from the Unit 2 term cache. **Rate-limit safety invariant:** per-event pacing (`WIKIDATA_ACTION_MIN_INTERVAL` for the Action API; serial WDQS calls for ancestor traversal) is safe *only because* the NLP and ontology managers are single-threaded serial drains — one `while True` per thread, NLP additionally guarded by `is_event_processing` (`coyote_nlp_state_manager.py:142`). If event processing is ever parallelized (post-MVP session-ID work), pacing must move to a **shared cross-event limiter, one per endpoint** (two endpoints). Do NOT add concurrency caps now — they would defend a parallelism the single-linear-browsing-history design forbids.

**Unit 8 — context-aware disambiguation (`analysis/nlp/wikidata_disambiguation.py`):** prominence ranking from `wbsearchentities` fixes the common ambiguities for free but not context-dependent ones (on a French-Revolution page, `robespierre` ranks the surname over the person, `revolution` ranks the Nintendo Wii over the concept). `select_best_candidate` re-ranks each term's K candidates by the **cosine of the candidate's WikiData `description` against the page's pooled full-doc embedding** (Unit 3 Step 7.5, reused — no recompute), with a `WIKIDATA_DISAMBIG_MARGIN` guard (only override prominence #1 if a candidate beats it by ≥ margin) and a `WIKIDATA_DISAMBIG_THRESHOLD` floor (below it → no mapping). Split for testability: `score_candidates(...)` is a pure numeric function (true cosine — the context embedding is L2-normalized but candidate vectors are raw, so it divides by both norms), wrapped by `select_best_candidate` which owns a **URI(QID)-keyed in-process description-embedding cache** and a single **batched** `embed_texts` over cache-misses (a cold deep page is ≤ ENTITY_MAP_CAP × K ≈ 700 short-string embeds — local CPU, no network, but a real per-page latency on deep pages; the cache amortizes across terms and pages within a process). **Webpage path ONLY:** the map functions take an optional `context_embedding` and pass it at the two webpage call sites (`coyote_nlp_state_manager.py` Step 10/16); the `None` default keeps search/hyperlink/annotation paths — and any webpage whose own embedding failed — on the prior prominence top-1 behavior verbatim (same webpage-only asymmetry as Unit 6's mapping floor). Annotation disambiguation is deferred post-MVP (its correct context is the *parent webpage's* embedding, a DB lookup that may be absent — see Known Issues).

**Track A — A1 junk-candidate filter + A3 direct-edge cap (2026-07-08, A5-verified):** **A1** (`analysis/nlp/wikidata_candidate_filter.py`, pure/zero-network): in both map functions on all four event paths, after `query_wikidata` returns the K=7 candidates and before selection, description-pattern + META-QID classification removes junk candidates. Class `meta` (scholarly-article/Wikimedia boilerplate) → drop candidate, fall back to next survivor; classes `name_marker`/`disambig` → filter-with-context (Unit 8 re-ranks surviving persons: Dewey→John Dewey) but **DROP THE TERM on no-context paths** (blind #2 fallback would mint unflaggable wrong-person junk). Word-boundary regex only, never substring ("linguist" must not fire in "linguistics"); term-drop keyed on prominence-#1 ONLY (k-tail junk is normal for legit terms: AI/Africa/Berlin). `WIKIMEDIA_META_QIDS` is canonically here (`connect_to_ontology` re-imports; the QID blocklist closed the Gate-B direct-path hole — Q4167410 had bypassed the ancestor-only Session-3 filter). The term cache stays RAW: filtering happens at consumption, so pattern edits apply to cached terms immediately. **A3** = `ONTOLOGY_DIRECT_EDGE_CAP` (env table). A5 replay (same 205-URL cohort, cache-preserved controlled comparison): A1-primary edges 14.3%→0.1% (single documented survivor: Stanford Encyclopedia of Philosophy, saved by the Gate-A1.0 `peer-reviewed`→journal-phrasings narrowing), name-marker edges 85→0, Gate B 0, person-desc 2.8% (F2 branch did not fire), direct edges −37%.

**Direct webpage→concept HAS_TOPIC edge (option-(a) restoration, `connect_to_ontology.link_concept_and_ancestors`):** for each above-threshold disambiguated `(uri, label, score)` triple (`extract_uris_from_node_data` now returns the label inline), the ontology manager **(1)** MERGEs the concept node + a direct `(node)-[:HAS_TOPIC]->(concept)` edge using only the Action-API-resolved `(uri, label)` — **zero WDQS calls** — then **(2)** runs the WDQS P279/P31 ancestor walk as **best-effort enrichment**. This decouples the disambiguated concept from WDQS availability: an OPEN WDQS breaker (the per-IP SPARQL throttle) no longer zeroes the page's topic graph, and the specific concept (e.g. `Q131805` John Dewey) finally lands as a node instead of only its abstract ancestors. The two WikiData breakers now guard genuinely different endpoints (Action API term→QID vs WDQS SPARQL ancestor traversal) and no longer trip together. The **flat** ancestor model is retained for now (webpage→every ancestor); concept→parent DAG edges are post-MVP (see Known Issues).

**WDQS per-IP throttle (empirically confirmed 2026-06-26):** a controlled probe from inside `coyote_app` (Coyote's own egress IP + UA, via SPARQLWrapper) returned **HTTP 429 + `Retry-After: 1000`** (a Wikimedia *edge* HTML error, served before the SPARQL engine) for **BOTH a trivial query AND Coyote's exact ancestor query** — so the throttle is a wholesale **per-IP edge rate-limit, not query cost**, and it was active on the deploy host's egress IP. (Any past "200 proof" came from a *different* egress IP than the container; same query, different IP, opposite result — that is the whole `429-when-Coyote-runs / 200-when-tested` puzzle.) 429 not 403, with a compliant UA → rate throttle, not a UA ban. **Root cause of the throttling:** `batch_query_wikidata` is called **one URI at a time** (`connect_to_ontology.py:489` recursion, `:717` root) despite `BATCH_SIZE=50` being available, so a single page fires dozens–hundreds of serial single-URI requests, exhausting WDQS's documented **60s-processing-per-60s** and **30-errors/min** budgets. Calls are **serial, never concurrent** (single-threaded ontology drain) so the 5-parallel limit is irrelevant. The breaker *does* honor `Retry-After` (`_parse_retry_after` → cooldown, capped 3600s) but is **per-process**, so it resets to `closed` on every container restart and each deploy re-pokes the throttled endpoint. The direct-concept-edge above makes all of this **non-blocking** (concept edges land WDQS-free); ancestor traversal is best-effort. **Post-MVP remedies (only if the ancestor tree is wanted):** actually batch (one ≤50-URI query per page via the existing-but-unused `BATCH_SIZE`), drop the expensive `wikibase:label` SERVICE (labels are local now), and persist/respect `Retry-After` across restarts.

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
| `images/core/core_analysis/coyote/analysis/nlp/` | NER, KeyBERT, RAKE, summarization |
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
| VECTOR_SIMILARITY_THRESHOLD | 0.40 | Tier 0 cosine similarity cutoff. Re-baselined 0.65→0.40 by Unit 3 Gate 3.5 (2026-06-17): the pooled full-doc Webpage embedding has lower query↔doc cosines than the old keyword-dense digest, so 0.65 zeroed out Tier 0 for common queries (e.g. "self-directed learning" best match 0.644, "cybersecurity" 0.578 — both < 0.65). 0.40 sits in the relevant/noise gap (true-positive floor ~0.47, noise ceiling ~0.28; off-topic control maxed 0.156). Thin sample (15 pages / 5 queries) — revisit as data grows. Annotation-corpus elbow not yet measurable (no annotations in cohort) → webpage elbow rules; B.5 revisits the webpage-vs-annotation divergence. |
| TOPIC_SCORE_THRESHOLD | 0.10 | Drop HAS_TOPIC root URIs whose topic_score (KeyBERT cosine) is below this. Renamed from TFIDF_TOPIC_THRESHOLD + default 0.15→0.10 by Unit 3 (KeyBERT swap); Gate 3.4 retunes. |
| NER_SCORE_FORMULA | log | Named-entity (`Entities.score`) mention-frequency formula. `log` = `ln(1+count)` (default), `freq_normalized` = `count/total_mentions` (page-relative share), `saturated` = `count/(count+1)` (bounded, k hard-coded 1.0). Replaced the TED-Talk-corpus TF-IDF entity scoring (Unit 4, 2026-06-18). Only the webpage path is scored; hyperlink/annotation/purpose/search entities stay `score=NULL`. Score affects only entity ordering within the `Webpage.entities` JSON blob (no numeric downstream consumer). |
| ENTITY_MAP_MENTION_FLOOR | 2 | Minimum per-page mention count for a (quality-filtered) **webpage** entity to be mapped to WikiData (`token_filter.select_mapping_entities`, Unit 6). Confirmed = 2 by PF-9a (29-page representative browse 2026-06-22): the salience cliff is the ≥1→≥2 step (per-page median distinct entities 27→5). Per-event application (that page's own case-folded mention counts); the floor governs *mapping*, not storage (all filtered entities are still stored and scored). Webpage path ONLY — search/purpose/hyperlink/annotation entities are low-volume/high-value and map every filtered survivor. |
| ENTITY_MAP_CAP | 100 (deploy); `None` (code) | Per-page top-K cap on webpage entities mapped to WikiData (`token_filter.select_mapping_entities`, Unit 6), applied AFTER the floor, keeping the highest-mention entities (deterministic tie-break). Code default is `None`/disabled (unset/blank/unparseable/≤0 ⇒ `None`); **the deploy enables it at 100** (`compose.yaml`). **Enabled per PF-9b (2026-06-23):** the Action API showed **no rate ceiling** (260 live calls / 0 throttle / 0 in-band over two sets), so the cap is a **throughput** knob, not rate-safety — a cold deep page is ~578 cache-miss calls × ~0.91s ≈ 8.8 min on the single-threaded NLP drain; K=100 bounds that to ~1.8 min while keeping the top-100 most-mentioned entities (the 101+ tail is the 2-mention incidental set). Typical pages (~5 entities) are unaffected (5 < K). Tail-driven, so the cap was preferred over a floor raise (which would over-prune typical pages). |
| WIKIDATA_ACTION_MIN_INTERVAL | 0.6 | Seconds of steady-state pacing between cache-MISS Action-API (`wbsearchentities`) calls in `wikidata_lookup.query_wikidata` (Unit 7). `<= 0` disables pacing. Tracks requests **sent** (the pacing clock advances on every attempt, including transient failures — rate-safety is about not hammering the endpoint, not success count). Default 0.6s = the 2026-06-19 probe spacing that returned 8/8 HTTP 200; **this is the one knob PF-9b tunes** (config change, no code). Backoff on a transient retry normally subsumes this interval, so retries rarely add pacing. |
| WIKIDATA_BREAKER_THRESHOLD | 1 | Consecutive 403/429 before tripping the WikiData circuit breakers in `wikidata_lookup.query_wikidata` (now the **Action API** `wbsearchentities`) and `connect_to_ontology.batch_query_wikidata` (**WDQS SPARQL** ancestor traversal) — independent instances, **same env var**. Stays **1** post-Unit-7: the two breakers now guard different endpoints with different rate profiles, so raising the *shared* default would loosen the WDQS breaker we want hair-trigger. If PF-9b shows the Action API genuinely 429s under sustained load, split per-endpoint (`WIKIDATA_ACTION_BREAKER_THRESHOLD`) rather than bumping the shared default. |
| WIKIDATA_BREAKER_COOLDOWN | 1800 | Seconds either breaker stays OPEN before a single half-open probe is allowed |
| WIKIDATA_TERM_CACHE_TTL_DAYS | 30 | Days a row in `wikidata_term_cache` (in `wikidata_cache.db`) stays fresh before `query_wikidata` (in `wikidata_lookup.py`) re-queries the **Action API** (`wbsearchentities`). Rows store the K=7 `(label, concepturi, description)` candidate list (~1-2 KB) so Unit 8 re-ranks a cache hit with zero extra network. Empty results cached as `"[]"` so repeat zero-match terms don't re-hit the endpoint. Cleanup janitor purges expired rows on its 6-minute interval. |
| WIKIDATA_DISAMBIG_THRESHOLD | 0.0 | Cosine floor for Unit 8 to accept a context-aware disambiguation winner (`wikidata_disambiguation.select_best_candidate`, **webpage path only**). The winner is the candidate whose WikiData `description` embedding is most similar to the page's pooled full-doc embedding (Unit 3 Step 7.5); below this floor → **no mapping** (the term is dropped, DEBUG-logged). A **different similarity space** than `VECTOR_SIMILARITY_THRESHOLD` (description↔page-context, not query↔doc) — own tuning, do NOT borrow 0.40. Default **0.0** = near-lossless first deploy: still drops *anti-correlated* (negative-cosine) winners but otherwise re-ranks without dropping, so Gate 8.3 can measure the cosine distribution before the floor is set; a value `< 0` (e.g. `-1.0`) makes the first pass truly lossless. **Read at import** (bound as the `select_best_candidate` default arg) — a deploy-time knob, **restart to change**, NOT runtime-mutable; tests must pass `threshold=` explicitly (monkeypatching the module constant won't affect already-bound defaults). |
| ONTOLOGY_DIRECT_EDGE_CAP | 20 | Per-stream cap on direct webpage→concept HAS_TOPIC links per event (`connect_to_ontology.select_direct_link_targets`, Track A A3). Applied after `TOPIC_SCORE_THRESHOLD`; topic-sourced and entity-sourced URIs are capped **independently** at N each — never combined, because the score families are incommensurable (webpage entity `ln(1+count)` ≥ ln(3) ≈ 1.099 outranks every KeyBERT topic cosine ≤ 1.0, so a combined cap would evict all topics exactly where it binds). Per-family distinct-URI dedupe first (entity JSON is one-object-per-mention), then deterministic `(-score, uri)` sort. Capped-out concepts also skip their WDQS ancestor walk. **Resting-state INVERSION vs `ENTITY_MAP_CAP`: unset ⇒ ENABLED at 20; set-but-blank/≤0/unparseable ⇒ DISABLED.** Read at import — restart to change. A5-verified (2026-07-08): entity stream binds (16/70 pages, 23–77→20 with INFO log), topic stream self-limits (max 15, dormant insurance). |
| WIKIDATA_DISAMBIG_MARGIN | 0.15 | Unit 8 margin guard (`wikidata_disambiguation`): a non-prominence candidate must beat prominence #1's cosine by **≥ this** to override it; otherwise prominence #1 wins. `0.0` disables the guard (pure argmax re-rank); higher = overrides harder. Protects already-correct prominence-#1 mappings (Gate 8.2) from marginal cosine flips. **Selection order:** margin picks the winner, then `WIKIDATA_DISAMBIG_THRESHOLD` gates it. **Default 0.15 CALIBRATED from Gate 8.1/8.2 (2026-06-25):** the one bad override (`gpt`→specific GPT-1 over the general concept) beat prominence by +0.131; the smallest *good* override (`transformer`→ML sense) was +0.179 — 0.15 sits in that gap, blocking same-family granularity flips while keeping all six known-bad lifts. Pre-gate provisional was 0.05. Same import-time / restart-to-change semantics as the threshold. |

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
- **Purpose and SearchTerms not linked to the WikiData ontology** (identified 2026-06-30; **not intentional** — post-MVP fix): the writer DOES create Purpose/SearchTerms nodes carrying `event_id` + `topics`/`entities` JSON (`coyote_browser_extension_to_neo4j.py:365-384`) and the linker (`connect_to_ontology._process_single_event:726`, `MATCH (n) WHERE n.event_id=$event_id`) selects and reads them — so it is **not** a property-name or node-matching gap. The gap is a **missing per-item `score`**: the purpose/search topic/entity dicts are built `{topic/entity, wikidata_uri, label}` without the `score` field the webpage path includes (`:267-272`), so `extract_uris_from_node_data`'s `_coerce_score(None)`→0.0 and `_process_single_event`'s `if score < TOPIC_SCORE_THRESHOLD (0.10)` drops every URI. **Fix is asymmetric:** *topics* — `Topics.score` IS populated for purpose/search (a RAKE weight, state-manager Step 5), so adding `score` to the two topic `SELECT`s surfaces it, but RAKE weights aren't on the KeyBERT-cosine threshold's 0–1 scale (most pass ≥1.0, so the threshold stops filtering them meaningfully); *entities* — purpose/search `Entities.score` is inserted NULL and never scored (NER scoring is webpage-only by design, Step 6/18), so adding `score` to the entity `SELECT` still yields 0.0 → still dropped, and fixing it needs scoring those paths or a threshold bypass. Distinct from the embedding (B.5) gap on the same nodes. Post-MVP.
- **Scrape effectiveness degradation**: `scrape_webpage.py` returns empty text for a growing share of URLs. The "two-thirds" figure was measured pre-trafilatura; the current rate on `coyote-0.4` HEAD is unknown — `MVP_REFACTOR_PLAN.md` pre-flight check 6 re-measures it. Future enhancement: add `embedding_skip_reason` property to distinguish "exempt URL" vs. "empty scrape" in Neo4j (targeted by Unit 9c of the 0.5 refactor). Sibling to `wikidata_skip_reason` below — both share the same plumbing path through NLP state manager → Neo4j writer.
- **WikiData-throttled events not tagged in Neo4j** (deferred from MVP breaker work 2026-05-12, extended 2026-05-14): when either WikiData circuit breaker is OPEN — `wikidata_lookup.query_wikidata` (term→Q-item lookup) or `connect_to_ontology.batch_query_wikidata` (ancestor traversal) — affected Webpages get empty topic/entity Wikidata mappings or truncated HAS_TOPIC ancestor chains, both indistinguishable from events with no matching labels and from URIs with no Wikidata parents respectively. Forensically recoverable by cross-referencing breaker state-transition log lines (`WikiData circuit breaker tripped/recovered`, `WikiData circuit breaker (ontology) tripped/recovered`) with `Webpage.timestamp`. Sibling to `embedding_skip_reason` above — both require parallel plumbing changes through `query_wikidata`/`batch_query_wikidata`/`scrape_webpage` → NLP state manager → Neo4j writer and should land together post-MVP.
- **Click-tracking redirects captured by browser extension**: the extension records every page navigation, including ephemeral redirect URLs (`google.com/url`, `t.co/`, `lnkd.in/`, etc.) that bounce to the real destination in <1s. The Python-side filter in `should_exempt_url` (`_REDIRECT_HOST_PATTERNS`) prevents these from creating Webpage nodes, but the events still reach SQLite staging and the NLP queue, doing avoidable work. Complete fix lives in the extension (filter before staging). Post-MVP.
- **`isSERP` detector is Google-only** (identified 2026-06-01): `coyote_browser_extension_to_neo4j.py:298` sets `is_serp = "- Google Search" in title or url.startswith("https://www.google.com/search?")`. Non-Google SERPs (Bing, DuckDuckGo, Brave, Kagi, Yahoo, Ecosia, etc.) get `isSERP = false`, are treated as content pages, and pollute topic-extraction denominators, gate measurements that filter `WHERE w.isSERP = false`, and any future content-vs-search-activity analysis. Detection asymmetry across layers: the Python-side `should_exempt_url()` (NLP-pipeline skip) and the Neo4j-side `isSERP` flag (Webpage tagging) use different criteria — Google-search URLs are covered by both, non-Google search URLs by neither. **Load-bearing for Unit 1 of the 0.5 refactor**, which relies on the existing `isSERP` flag for the SERP partition rather than introducing a redundant `topic_skip_reason` tag, so this gap directly bounds Unit 1's gate accuracy. Post-MVP fix: broaden the detector to match search-engine URL patterns and titles consistently across both sides via a shared `is_serp_url()` predicate; backfill historical Webpages or accept the cohort skew.
- ~~**`"day"`/`"days"` leak through `_terms()` STOP set** (chains.py)~~ (resolved 2026-05-07, MVP Fix 1): added `day`, `days`, `hour`, `hours`, `minute`, `minutes`, `ago`, `lately`, `currently` to the STOP set in `_build_context_hybrid`.
- **LLM hallucinates empty-result response despite populated context** — deferred; see Post-MVP § Deferred items with concrete scope.
- ~~**Wikidata entity disambiguation (Layer 2 — NER mapping)**~~ (resolved by **Units 7 + 8, 2026-06-25 — WEBPAGE PATH ONLY**): the old SPARQL path returned the first label match irrespective of prominence or context (observed: `"ai"` → Anguilla, `"gpt"` → "GNU Portable Threads", `"First Monday"` → calendar date). **Two-part fix:** **Unit 7** moved term→QID to the Action API (`wbsearchentities`), whose **prominence ranking alone** fixed the common cases (Gate-verified: `ai`→Q11660, `gpt`→Q116777014; `First Monday` was a SPARQL-era example, moot under the new transport). **Unit 8** added a **context-aware re-rank** (`analysis/nlp/wikidata_disambiguation.select_best_candidate`) over the top-K — each candidate's WikiData `description` is scored against the page's pooled full-doc embedding, with a margin guard + threshold and "no mapping" below the floor — fixing context-dependent cases prominence #1 still gets wrong (Gate 8.1: `robespierre`→person, `jacobin`→club, `revolution`→concept-not-Wii, `congress`→US, `transformer`→ML, `dewey`→John Dewey, 6/6). **Scope qualifier:** wired into the **webpage path only**; the annotation / search / hyperlink paths still use prominence top-1, and **annotation disambiguation is explicitly deferred** (see the "Annotation WikiData disambiguation deferred" Known Issue above — its correct context is the parent webpage's embedding, a DB join that may be absent). Layer 1 (Wikimedia infrastructure cascade) was resolved by Session 3.
- **Wrong-sense short-term WikiData mappings (accepted debt, post-MVP)** — *identified Probe #2 / A5 (2026-07-08)*: short/acronym terms whose prominence-or-rerank winner has a clean, legit-looking description are invisible to both the A1 filter (description not junk) and Unit 8's margin (context cosine didn't lift the right sense): `STS`→`Q3965305` "alpine subsection" (SOIUSA Alps taxonomy, 5 NLP pages), and post-A1 `Chen`→`Q281519` "genus of birds of the family Anatidae" (4 pages — the surname sense is now correctly filtered and prominence #2 is the snow-goose genus). Same family as the ~1–2% wrong-person residual (Richard→Stephen King). Candidates if it grows: class-N-adjacent guards, Unit-8 margin/threshold tuning on real-data cosines.
- **Annotation WikiData disambiguation deferred (post-MVP)** — *new, Unit 8 (2026-06-25)*: Unit 8's context-aware re-rank (`wikidata_disambiguation.select_best_candidate`) is wired into the **webpage path only**. `map_ner_to_wikidata` / `map_topics_to_wikidata` are still called from the **annotation** path with `context_embedding=None`, i.e. the prior prominence top-1 behavior (no disambiguation). The correct context for an annotation is the **parent webpage's stored embedding**, NOT the annotation's own text (the annotation embeds the user's note, not the consumed source). That embedding is **not a local variable**: the annotation path's Step 2 (`coyote_nlp_state_manager.py:915`) fetches only `annotation_id`/`annotation_text`/`highlighted_text`, so reaching the webpage embedding needs a **DB join via URL to `WebpageLoads`** — *and it may be absent* (Hypothesis-only Webpages have no embedding; an annotation can be processed before its webpage is embedded). This is a **resourcing deferral, not a value judgment**: annotation terms carry disproportionately high epistemic value per term (the output-space signal — how the user's mind transforms consumed information), so per-term disambiguation quality matters *more* there. Hyperlink/search paths are likewise unmapped-by-context but are low-value/low-volume.
- ~~**Entity TF-IDF scores are uniformly 0.0**~~ (resolved 2026-04-30, Session 1.5): `coyote_nlp_state_manager.py` Step 20's `WHERE event_id=? AND entity=?` was case-sensitive, but `term` came from sklearn `TfidfVectorizer.get_feature_names_out()` which lowercases by default. Fixed by adding `COLLATE NOCASE` to the WHERE clause. Residual minor edge case: entities containing non-word characters (`"AT&T"`, `"U.S."`) still won't match because sklearn's default `token_pattern=r'(?u)\b\w\w+\b'` strips them — minor share of entities, post-MVP.
- ~~**HAS_TOPIC `tfidf_score` historical edges unreliable**~~ (resolved by Unit 3, Phase 7, 2026-06-12): the HAS_TOPIC edge property is now `topic_score` (KeyBERT cosine), written via a last-write-wins MERGE. The old `tfidf_score` broadcast-value concern is moot for new events; historical edges turn over as Webpages age out. No backfill.
- ~~**`tfidf_score` score-type mixing on rare topics**~~ (resolved by Unit 3, Phase 5, 2026-06-12): the BERTopic/TF-IDF topic path is gone. `Topics.score` for webpage and ≥50-word-annotation topics is now KeyBERT cosine similarity (raw `word_doc_similarity`, range ~0–1, can be mildly negative pre-threshold); the unbounded-c-TF-IDF mixing that produced the 6.874 outlier no longer exists.
- ~~**TF-IDF corpus is not what its name implies**~~ (fully resolved 2026-06-18, Unit 4): the topic path was resolved by Unit 3 Phase 5 (KeyBERT cosine); the surviving **entity** scoring path (TF-IDF against the empty `CorpusDocuments WHERE source='TEDTalk'` corpus → degenerate IDF) is now gone. Unit 4 replaced `Entities.score` with mention-frequency scoring (`coyote_nlp_state_manager.py` Step 18, `entity_scoring.mention_frequency_score`, `NER_SCORE_FORMULA`) and deleted the dead apparatus entirely: `text_bertopic_analysis.py` (the `calculate_tfidf_on_phrases` rump), `text_ner_analysis.replace_named_entities_in_text`, the `utils/import_tfidf_corpus.py` + `utils/preprocess_tfidf_doc_corpus.py` loader scripts, and the `CorpusDocuments` CREATE in `initialize_databases.py`. Existing DBs may still carry a dangling empty `CorpusDocuments` table (harmless; cleared on the next data-expendable wipe). Note: the per-mention duplicate rows in `Entities`/`Webpage.entities` (one JSON object per mention, all sharing the score) predate Unit 4 and are a deliberate separate follow-up.
- **Hypothesis-only Webpages have no `timestamp` property** — *new, identified 2026-05-27*: `hypothesis_to_neo4j.py:127-128` creates Webpage nodes via `MERGE (w:Webpage {url: $url}) ON CREATE SET w.title = $webpage_title` — only `title` is set on create. Webpage nodes whose URLs first reach Neo4j via a Hypothesis annotation (not via the browser extension) have a NULL `timestamp` property. Effects: (a) ad-hoc Cypher like `ORDER BY datetime(w.timestamp) DESC LIMIT 50` sorts these to the TOP in DESC order (Neo4j sorts NULLs first in DESC), producing surprising "most recent" results — add `WHERE w.timestamp IS NOT NULL` to filter them out for ad-hoc queries; (b) chains.py Tier 1 time-window queries silently exclude these nodes (NULL `datetime()` fails the `>=` comparison), which is the correct behavior for "recent activity" queries but means Hypothesis-only Webpages are invisible to those tiers. Important: this does NOT indicate that Neo4j's `datetime()` function is broken on the documented browser-extension format (`datetime.now().isoformat()` produces parseable naive ISO 8601 strings — the mandate at the top of this file remains correct). Long-term fix: set `w.timestamp` from the annotation's `created` field on first MERGE, or mark it imputed with a sentinel value. Post-MVP unless it bites a Tier 0/1 query.
- **SQLite table-name casing is bifurcated.** Existing tables split between PascalCase for most domain tables (`Events`, `Entities`, `Topics`, `CorpusDocuments`, `WebpageLoads`, `SearchEvents`, `Annotations`, `EventTracking`, `AnnotationTags`) and snake_case for infrastructure tables (`user_settings`, `event_queue`, `node_processing_queue`, `wikidata_cache`, `wikidata_term_cache`). The split is accidental — different authors picked different cases over time, not a chosen convention. Not renaming existing tables before MVP (avoidable churn). Post-MVP: pick one and migrate atomically.
- **`Events` table is a dedup filter, NOT a recency log** (identified 2026-06-17, Unit 3 Phase 8 deploy): despite its name and its `timestamp`/`event_type`/`data_source` columns, `Events` is used in `event_data_handler.py:32-39` purely as a set of seen `event_id`s — fetched (`SELECT event_id FROM Events`) to skip already-handled rows when pulling the next item from `EventStaging`. It is populated by `insert_common_event_data` (`:108-133`). It is **not** a complete or reliable record of processed content: a fully-processed Webpage (embedding + KeyBERT topics in `WebpageLoads`/`Topics`) was observed absent from `Events` while `Events` `MAX(timestamp)` lagged weeks behind live activity. **Do not query `Events.timestamp` for "latest activity" / recency** — it will silently mislead (this caused a false "fresh browsing didn't land" diagnosis during the Unit 3 deploy). For recency use `WebpageLoads.embedding_generated_at` (webpages), `Annotations.timestamp` (annotations), or Neo4j `Webpage.timestamp`.
- **Orphaned `get_wikidata_cache_db_connection()` accessor** (identified 2026-06-09, Unit 2): `config_manager.py:233-251` exposes a thread-safe accessor for `wikidata_cache.db` along with `wikidata_cache_db_lock` at `:54`, but neither has any operational call site. The URI-cache helpers in `connect_to_ontology.py:181-238` and the cleanup janitor in `database_cleanup_manager.py` use direct `sqlite3.connect()` instead. Unit 2's new term cache also bypasses the accessor (matches the surrounding pattern). Routing one writer through it while others bypass would create a false impression of protection. Post-MVP: either adopt the accessor consistently across all writers OR delete the orphan.
- **`wikidata_cache.db` not in WAL mode** (identified 2026-06-09, Unit 2): `initialize_wikidata_cache_db()` in `initialize_databases.py` creates the schema but does not call `enable_wal_mode(WIKIDATA_CACHE_DB_FILE)`, unlike `EVENT_DATA_DB_FILE` which does. Under default rollback-journal mode, writers and readers block each other on the file lock; WAL would let them run concurrently. Practical impact at current cache-write frequency is small (writes are brief and infrequent), but the asymmetry with `event_data` is unintentional. One-line ride-along fix candidate for any future commit that touches `initialize_databases.py`.
- **HAS_TOPIC edges are last-write-wins; revisit history is discarded** (accepted tech debt, Unit 3 Phase 7, 2026-06-12): `create_or_link_node` (`connect_to_ontology.py`) now does `MERGE (n)-[rel:HAS_TOPIC]->(wdo) SET rel.topic_score=$score, rel.timestamp=$timestamp` — timestamp + score are out of the merge key, so re-processing a `(Webpage, topic)` pair updates one edge in place instead of stacking a duplicate per revisit. This was a deliberate fix for duplicate-edge inflation (which broke Unit 9b's per-node edge-cap gate). Cost: a Webpage's HAS_TOPIC edges reflect only the most recent processing — previous visits to the same URL, and topic changes when a dynamic page's content differs across visits, are lost. In a longitudinal personal-learning record those revisits may themselves be meaningful. Proper post-MVP fix: per-visit modeling (session IDs / visit nodes — kin to the session-ID Known Issue), not multi-edge MERGE keys.
- **Ontology graph is flat, not a DAG** (post-MVP target, identified 2026-06-26 during the option-(a) restoration): `create_or_link_node` links the user node **directly to every ancestor** (webpage→parent, webpage→grandparent, … at each recursion level), and `WikiDataOntology` nodes are **not connected to each other**. So "the ancestors of concept X" is not answerable by graph traversal — the hierarchy exists only as a fan of flat per-webpage edges, which also inflates edge count per page. The option-(a) restoration added the missing direct webpage→**concept** edge but deliberately kept this flat ancestor model. **Post-MVP target:** model `concept→parent→grandparent` as **ontology-to-ontology** edges (a DAG — as close as Wikidata's hierarchical, crowd-sourced P279/P31 graph allows, i.e. accepting multiple parents and the occasional cycle the visited-set already guards), with webpage→concept as the single user-node edge. Then hierarchy is a traversal, edge count per page drops to one-per-concept, and revisit/session modeling composes cleanly. Kin to the per-visit/session-ID modeling work.
- **Webpage NLP parses each document twice per event** (known inefficiency, Unit 3c, 2026-06-12): the shared spaCy instance (`CoyoteNLPStateManager.self.nlp`) is invoked once inside `extract_entities` (NER) and again inside `keybert_analysis.extract_keywords` (noun_chunks need the parse). This is intentional and documented so a future session does NOT "optimize" it by re-parsing inside the `CountVectorizer` analyzer closure — that would re-parse on every `fit` call and defeat the single-instance design. The correct elimination is passing a pre-parsed `Doc` into both call sites; deferred post-MVP unless profiling shows the second parse is a hotspot.
- **Shared full-pipeline spaCy instance** (Unit 3c, 2026-06-12): one `spacy.load("en_core_web_sm")` is loaded once in `CoyoteNLPStateManager.__init__` (`self.nlp`) and passed explicitly to every `extract_entities(text, nlp)` call (six sites across the four event paths) and to `extract_keywords(...)`. No module-level spaCy loads remain in `text_ner_analysis.py` or the topic path. Instance lifetime tracks the manager's lifetime. KeyBERT separately wraps the shared SentenceTransformer singleton via `coyote_embedder.get_model()` (never a second model copy).

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
- ~~**Session 2**~~ (shipped 2026-05-06): env var (default `0.15`) applied at the entry of `_process_single_event`'s URI loop. Drops low-scored root URIs and their full WikiData ancestor tree. **Renamed `TFIDF_TOPIC_THRESHOLD`→`TOPIC_SCORE_THRESHOLD`, default `0.15`→`0.10`, by Unit 3 Phase 7 (2026-06-12) when KeyBERT replaced TF-IDF.**
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
# no-sudo alternative (docker group):
#   docker run --rm -v "$PWD/volumes/neo4j:/wipe" --entrypoint sh neo4j:5.26 -c 'rm -rf /wipe/*'
# WITHOUT the next line the graph stays empty forever: the SQLite dedup
# tables (Events / Annotations / EventStaging) block all reprocessing —
# see the corrected note below the recipe.
rm -f ./volumes/coyote/coyote_event_data.db* ./volumes/coyote/coyote_event_staging.db*
# wikidata_cache.db holds both the URI cache (wikidata_cache table)
# and the term cache (wikidata_term_cache table) — one rm wipes both.
# OPTIONAL: keeping it is a pure speed win (rate-safe; Track A closed).
rm -f ./volumes/coyote/wikidata_cache.db
# NEVER rm coyote_state.db or coyote_*_key.key (Fernet-encrypted creds).
docker compose --profile core --profile llm --profile agent up -d --build
cd ..
```
All three profiles are required: `bot` (agent profile) has a hard `depends_on: llm`, so omitting `--profile llm` triggers `service "bot" depends on undefined service "llm": invalid compose project`. Same combination as `ui/coyote_ui_server.py:327`.
Vector indexes are (re)created by the **bot** at startup (`create_vector_index`, `images/agent/app/utils.py:33` called from `bot.py:78`, `IF NOT EXISTS`, dim 384 cosine) — i.e. on the first chat-UI (Streamlit) session, NOT by Core node inserts. They are therefore absent until the bot/chat UI is first opened after a wipe; that is expected, not breakage (do not diagnose "missing `webpage_embedding`/`annotation_embedding` indexes" as a fault before the bot has run). **A Neo4j-only wipe does NOT rebuild the graph** (corrected 2026-07-09 — the previous claim here that nodes "rebuild from event data on next NLP cycle" was wrong and has misled three diagnoses): the SQLite dedup tables mark processed items and BLOCK reprocessing — `Events` (browser events, checked by `fetch_next_event`) and `Annotations` + in-flight `EventStaging` (Hypothesis, checked by `_annotation_already_seen`, `coyote_event_writer.py:29`). After wiping only the graph, browsing events will not re-drain and Integrations→Fetch Data fetches all annotations but skips every one as "already seen." **For a full graph rebuild, also delete `coyote_event_staging.db` and `coyote_event_data.db`** (schemas recreate on startup) **and clear `event_queue`/`node_processing_queue` in `coyote_state.db` — but NEVER delete `coyote_state.db` itself or the `coyote_*_key.key` files** (`user_settings` holds the Fernet-encrypted Neo4j/Hypothes.is creds). Then: annotations re-import in full via Fetch Data (source of truth is Hypothes.is); browsing history must be re-browsed or re-staged; Hypothesis-only Webpage nodes carry no embeddings, so annotation source pages need a real (re-)browse to be embedded. Tier 0 starts cold — acceptable per data-expendable invariant.

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
**Superseded for Webpages by Unit 3 (2026-06-12):** the Webpage node embedding is no longer the structured digest (`Title / Summary / Entities / Topics`). It is now a **pooled full-document embedding** of `title + "\n\n" + scraped_text` via `coyote_embedder.embed_document_with_text` (chunk → L2-normalize → mean-pool → re-normalize); `embedding_text` stores the exact text pooled (the truncated prefix when `MAX_CHUNKS` hits). `build_webpage_embedding_text()` was deleted. Rationale: content embeddings are a function of `(content, embedder)` only, not the NLP-pipeline version — see MVP_REFACTOR_PLAN.md Unit 3 architecture decision. **Annotation** embedding still uses the digest (`build_annotation_embedding_text`, output corpus — Phase B.5 territory). The detail below describes the original Phase B digest design, retained for the annotation path and as archaeology.
- `coyote_embedder.py`: singleton model with `_model_load_failed` sentinel (no retry spam on permanent failure)
- `shared/embedding_config.py` synced to Core build context via `make sync-shared` (new: `images/core/core_analysis/shared/`)
- Core Dockerfile updated: `COPY shared/ /app/shared/`
- NLP manager: Step 20.5 (webpage) persists the Step 7.5 pooled embedding; Step 10.5 (annotation) after WikiData mapping, both before commit
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
- Python datetime: codebase is mixed — `coyote_server.py` and `wikidata_lookup.py` (the WikiData circuit breaker) use `datetime.utcnow()` (naive); `coyote_embedder.py` uses `datetime.now(timezone.utc)` (aware). Core image is Python 3.11 (no deprecation warnings). Project-wide migration to the aware form is a Python 3.12+ readiness item, not MVP-blocking.

## Testing
```bash
python -m pytest tests/ -v        # 85 tests (security, sync check, time parsing, wikidata breakers x2)
make sync-shared                  # sync nl2cypher.py before docker build
make build-agent                  # sync + rebuild bot container
```

## Security Roadmap
**P1**: ~~LangChain 1.0 migration~~ (done — now on langchain-core 1.2.x, langchain-neo4j 0.7.0)
**P2**: Optional auth, CORS config, rate limiting, vector search activation (Phases B + C v1 + orphan fix done; B.5 / C v2 pending), extension config UI
