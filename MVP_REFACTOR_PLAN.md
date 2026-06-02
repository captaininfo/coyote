# Coyote MVP NLP-Quality Refactor — Implementation Plan

**Draft v1, 2026-05-27**
**Working branch:** `coyote-0.5-nlp-refactor` (to be created from `coyote-0.4`)
**Preserves:** `coyote-0.4` branch untouched; rollback = abandon refactor branch.

---

## 1. Scope

### 1.1 In-scope

Improving the quality of HAS_TOPIC edges and entity-level signals in Coyote's Neo4j graph, by replacing the topic-extraction stack, fixing silent embedding truncation, adding cache + fuzzy-match coverage to WikiData lookups, harvesting structured metadata from scraped pages, and replacing TF-IDF scoring (which has been quietly broken).

### 1.2 Out-of-scope (explicit)

The following are recognized work items but will NOT land in this refactor:
- Section-level chunk persistence for per-section retrieval (post-MVP feature: "I read an article 1-2 years ago about...").
- Wikidata embedding centroid filter (Justin's 4-part plan Part 4) — blocked on 25-100 GB local-deployment problem.
- OpenTapioca investigation (separate evaluation track).
- Phase B.5 — embedding Purpose and SearchTerms nodes.
- Hyperlink / Webpage / Annotation queue-update bug (separate small fix, outside WikiData scope).
- Two-stage NER-context-aware SPARQL disambiguation (4000 queries-per-page pattern — deferred indefinitely).
- LLM context input/output role-label preservation (Phase C v2).

### 1.3 Preconditions (shipped — assumed in place)

These are NOT part of this plan; they are foundation work already on `coyote-0.4`:
- Sessions 1 / 1.5 / 2 / 3 (per-topic score plumbing; entity COLLATE NOCASE fix; TFIDF_TOPIC_THRESHOLD; ontology entry-point cleanup).
- Fix 1 (chains.py `_terms()` STOP-set extension).
- WikiData circuit breakers (both `query_wikidata` and `batch_query_wikidata`).
- Phase A / B / C v1 vector embedding work.
- Orphan-search fix.

Session 3 verification gates A/B/C are not preconditions for this refactor. Gates A and C are informational (superseded by KeyBERT-era measurements). Gate B is deferred and reframed as a post-refactor verification under Unit 9d (see § 1.3 and Unit 9d's verification gate).

---

## 2. Pre-flight checks

Spike tasks to run BEFORE creating the refactor branch. Estimated 2-4 hours total. Each produces a go/no-go decision.

1. **Verify Pekar implementation against current keybert API.** Article principle is already verified (WebFetched 2026-05-27 — see session notes). Build a 20-line proof-of-concept against the current pinned `keybert` and `sentence-transformers` versions to confirm API compatibility (specifically: the `vectorizer=` parameter still accepts a sklearn `CountVectorizer` instance, and the `tokenizer=` callable signature matches). Run on one sample Coyote page. Confirm output is grammatically coherent and matches Pekar's "after" pattern (no cross-sentence n-grams like "supervised learning machine").
   - **Result (2026-05-28):** PASS, with one API correction. Versions used: `keybert==0.9.0` (no prior pin), `sentence-transformers==3.3.1`, `spacy==3.7.4`, `scikit-learn==1.5.2`. `KeyBERT(model="all-MiniLM-L6-v2")` resolves via `SENTENCE_TRANSFORMERS_HOME=/opt/embedding_model`; `extract_keywords(text, vectorizer=cv, use_mmr=True, diversity=0.6, top_n=10)` works. Sample page: Wikipedia "International Council for Open and Distance Education" (id=411 in `WebpageLoads`, 4593 chars). Top signal `distance education icde` @ cosine 0.6653. Spot-check of five suspicious phrases against source: all within single sentences — no cross-sentence n-grams. **API correction for Unit 3c:** use the `CountVectorizer(analyzer=callable)` pathway, NOT `tokenizer=callable`. Reason: with `tokenizer=`, sklearn forms n-grams via `_word_ngrams` over the flat token stream and `ngram_range=(1,3)` will cross sentence boundaries, defeating Pekar's whole point. With `analyzer=`, the callable returns the final list of (already-bounded) n-grams directly; sklearn's `ngram_range` is bypassed. The plan's pre-flight text said "tokenizer=" — kept here for historical fidelity, but the verified implementation contract is `analyzer=`.
2. **KeyBERT MMR diversity sanity check.** On 5 representative Coyote pages, run KeyBERT + MMR at λ = 0.5, 0.6, 0.7. Confirm top-5 phrases are diverse (no near-duplicates) and on-topic. Pick a starting λ. Record the observed cosine-similarity score distribution — feeds Gate 3.4's empirical baseline.
   - **Result (2026-06-01):** A/B re-run on 5 representative pages with three candidate-generation strategies feeding KeyBERT via the `vectorizer=` pathway, λ=0.6, top_n=20. The three strategies: **Pekar** (POS-filtered NOUN/PROPN/ADJ 1-3 grams within sentence boundaries), **spaCy `noun_chunks`** (linguistic noun phrases from the dependency parse), **RAKE** (stopword-bounded co-occurrence phrases via `rake_nltk`).
     - **Sub-finding routed to R1:** KeyBERT 0.9.0's `candidates=` parameter is broken for multi-word vocabularies — silently re-tokenizes via default `token_pattern=r'(?u)\b\w\w+\b'`, dropping multi-word strings from the vocabulary. An initial A/B attempt on 2026-05-28 used this pathway and produced unigram-only output (invalid as a comparison). Production code MUST use `vectorizer=` with a `CountVectorizer(analyzer=callable)`. The corrected pathway used in the 2026-06-01 re-run closes the analyzer over the pre-computed candidate list and returns it verbatim per document.
     - **Decision criterion (Sonnet, 2026-05-28):** phrases recognizable as Wikidata-searchable for top-3 of ≥3/5 pages.
     - **Result by condition (raw top-5 per page captured at `/tmp/preflight_2_ab_v2.out`):** Pekar produces POS-soup phrases that fail Wikidata-searchability — e.g., "think tanks governmental" @ 0.7922, "taxonomy constructs psychology" @ 0.6865 — 0/5 pages pass. RAKE produces verb-led and fragmentary phrases — e.g., "vibe coding might come" @ 0.5665, "abstract taxonomic incommensurability denotes" @ 0.3983 — 0/5 pages pass. noun_chunks produces actual noun phrases — "international personality item pool" @ 0.4679 (real IPIP instrument), "topic model" @ 0.3715 (Wikidata Q-item), "policy think tanks" @ 0.7749, "freelance education writer" @ 0.5033, "education speaker" @ 0.5463 — 2/5 pages pass strict criterion (Convivial 3/3, Nature 2/3), 4/5 pass charitable criterion (at least one searchable top-3 hit).
     - **Score distribution (n=100 per condition, top-20 × 5 pages):** Pekar median 0.1923 IQR 0.1900 max 0.7922; noun_chunks median 0.1808 IQR 0.1622 max 0.7749; RAKE median 0.1764 IQR 0.1617 max 0.7924. Distributions nearly identical across conditions — the choice is candidate-quality, not score-tuning. Small negatives observed (min ~-0.05). Feeds Gate 3.4 baseline.
     - **Decision:** **noun_chunks (Option E) selected for Unit 3c.** Pekar and RAKE candidate sets corrupt KeyBERT's output regardless of MMR tuning — KeyBERT cannot fix bad candidates.
     - **Architectural consequence:** Unit 3c originally specified two spaCy instances (one parser-disabled for Pekar speed, one full-pipeline for NER). noun_chunks requires the parser, which is the same configuration NER needs. Both call sites can now share one full-pipeline spaCy instance. Implementation note: pre-compute the noun-chunk set ONCE from the shared parse and close over it in the analyzer callable; do NOT call `nlp()` inside the analyzer. Unit 3c text amended.
     - **Independent finding routed to Unit 6:** "doc pre" appears in Pekar's top-5 on the BERTopic page (0.2350) — confirms that Unit 6's `<3 chars` length filter (length 1-2 only) leaves bound-morpheme tokens untouched. Unit 6 amended to drop the bound-morpheme set `pre, anti, non, sub, pro, neo, post, semi, pseudo, quasi` regardless of candidate generator. "cite web" Wikipedia-template noise did NOT appear in any condition's top-5 on these 5 pages but is added to Unit 6's stopword list at lower priority as cheap insurance.
     - **MMR λ:** held constant at 0.6 throughout the A/B. λ-comparison at 0.5/0.7 was deferred (the pathway debugging consumed pre-flight 2's original budget) but `KEYBERT_MMR_LAMBDA` env var allows post-deploy tuning without code change. R2 marked resolved.
     - **Code-block scraping leakage observed across all three conditions** on the BERTopic page ("bertopic import bertopic" @ 0.5745–0.6792, "sentencetransformer pre calculate embeddings" @ 0.2063). Trafilatura post-processing concern, not candidate-generation; recorded as a possible post-MVP unit ("Option N" in the 2026-06-01 advisor exchange) and explicitly **out of scope** for this refactor.
     - **Alternative considered and deferred:** gazetteer-based entity linking with embedding-disambiguation ("Option G" in the 2026-06-01 advisor exchange — DBpedia Spotlight / OpenTapioca family) was reviewed and deferred to post-MVP as a v0.6+ quality leap; same family as the OpenTapioca investigation already flagged in CLAUDE.md.
     - **Scratch artifacts:** `/tmp/preflight_2_ab_v2.py` (corrected A/B script), `/tmp/preflight_2_ab_v2.out` (raw output) — diagnostic-only, do NOT commit.
3. **Session 3 gates — deferred and superseded (resolved 2026-05-28).** Gate B cannot be measured on current data: WDQS throttling contaminates Wikidata-enriched events, leaving topic/entity mappings sparse or missing, so the HAS_TOPIC → meta-class query has nothing meaningful to count. Additionally, Units 3/9b/9d change which terms reach Wikidata and filter edges differently, so any pre-refactor Gate B number is invalid for the shipped system. Gate B's intent (zero meta-class edges) is preserved as a post-refactor verification requirement under Unit 9d (P31 instance-of blocklist) — run after first database wipe on `coyote-0.5-nlp-refactor`. Gates A and C are informational only; superseded by KeyBERT-era measurements. **Pre-flight 3 is therefore a no-op at branch open.**
4. **CorpusDocuments TED Talk count.** Run `SELECT count(*) FROM CorpusDocuments WHERE source='TEDTalk';` against the live `coyote_event_data.db`. If zero, the current production TF-IDF path has been running against an empty corpus (degenerate IDF) and any prior Session-1/2/3 gate measurements were done against a degenerate baseline. Note the count for the record; the refactor dissolves the dependency, so the value matters for *interpreting historical measurements*, not for proceeding.
   - **Result (2026-05-28):** `tedtalk_count = 0`. The entire `CorpusDocuments` table is empty (`GROUP BY source` returns no rows). Most likely cause: the table was populated at some earlier point and the rows were accidentally deleted — not a never-populated state. Schema confirmed (`source TEXT, -- e.g., "TEDTalk"`), so this is not a case/identifier mismatch. **Consequence:** all Session-1 / 1.5 / 2 / 3 gate threshold tuning (`TFIDF_TOPIC_THRESHOLD=0.15`, the per-topic score-plumbing fix) was performed against a degenerate sklearn IDF computed over zero documents. Historical gate numbers should be treated as untrustworthy when interpreting refactor diffs. Does NOT block proceeding — Unit 3's KeyBERT swap dissolves the dependency entirely. **Sharper implication for Unit 3e:** the `0.15` value carried by `TFIDF_TOPIC_THRESHOLD` has no empirical grounding. The Unit 3e rename to `TOPIC_SCORE_THRESHOLD` is a cosmetic change — the *value* must be retuned from scratch against real KeyBERT cosine-similarity distributions observed on production pages. Do NOT carry `0.15` forward as if it were validated; treat it as an unset parameter that pre-flight 2 + post-deploy Gate 3.4 must establish.
5. **Chunk-and-pool similarity distribution measurement.** Build a quick POC that runs the chunk-and-pool fix (Unit 3b) on 4-5 real Coyote pages of varying length (one short, one medium, one long-tutorial-style, one news-inverted-pyramid-style). Record the observed cosine similarity between pooled embedding and first-chunk-only embedding for each. Use the lowest value among substantive-tail-content pages to set Gate 3.2's empirical threshold (the gate threshold should sit just below that observed value, so substantive-tail content reliably passes). This replaces the currently-guessed 0.92. **The POC is throwaway code for measurement only — discard after pre-flight. Unit 3b is implemented from scratch against the chunking module API (3a), not by promoting the POC.** Contingency: if ALL test pages cluster near 0.99 cosine (no measurable pooling effect), either the test sample lacks substantive tail content or the chunking strategy is broken — re-test with deliberately tail-heavy material before concluding the gate is uninformative.
6. **Empty-scrape baseline.** Re-measure the current empty-scrape rate on `coyote-0.4` HEAD. The 67% figure in CLAUDE.md is pre-trafilatura and not the live baseline. **Operational note (2026-05-27):** "fresh browsing replay" was loose phrasing — Justin cannot generate fresh browsing data due to WDQS throttling (Coyote Core would create nodes with empty topics/entities), but pre-flight 6 doesn't actually need new browsing. Query existing post-trafilatura Webpage nodes in Neo4j for `scraped_text` length distribution; WDQS throttling does NOT affect `scraped_text` population (it's pure scraper output, written before NLP/WikiData runs). Approximate query: `MATCH (w:Webpage) WHERE w.timestamp > "<trafilatura ship date>" RETURN size(coalesce(w.scraped_text, "")) AS text_length`. Factor out redirect-URL events from the denominator. Recorded as a scraper-health observation; **not gating any unit** (Gate 5.1 was retired after this measurement — see Unit 5).
   - **Result (2026-05-28):** the live-DB approach yielded only 23 post-trafilatura `WebpageLoads` rows; 11 of 12 non-exempt/non-redirect rows were empty, with all 11 failures clustered in a single 9-minute window on 2026-05-08 (06:01–06:10). Diagnostic: `git log e617d38..HEAD -- scrape_webpage.py` returns zero commits (today's code is byte-identical to what ran on 2026-05-08), and re-scraping three of the failing URLs through current code produces clean text (Wikipedia/Open_education 19.7k chars, Wikipedia/Critical_pedagogy 29.8k chars, Clemson blog 3.8k chars). **The 2026-05-08 cluster was environmental** (transient network / rate-limit / DNS), not a code regression. The 23-row corpus is too small and contaminated by a single bad window to set a baseline.
   - **Option B synthetic baseline (2026-05-28, 50 user-curated URLs):** ran `scrape_webpage()` against a diverse mix (PDFs, anti-bot interstitials, paywalled academic, SPA, JS-heavy, blog, news, Wikipedia, etc.). Raw: 30 OK, 20 EMPTY, 0 exempt. Empty breakdown by failure mode:
     - **9× HTTP 4xx blocks** (server-refused: dl.acm.org, wiley, mdpi, sciencedirect, reuters, science.org, writingcenter.unc, open.edu). Code can't help without browser-emulating scraper.
     - **3× PDF (200 + empty extract)** — trafilatura doesn't handle PDFs. Known limitation; post-MVP `pypdf` fallback would close (see CLAUDE.md Post-MVP).
     - **4× PMC interstitial caught by `_BOT_INTERSTITIAL_PATTERNS`** — **working as designed** per the e617d38 commit, which explicitly mentioned PMC. Empty is the correct outcome.
     - **2× Reddit SPA shell**, **2× other JS/SPA** (hms.harvard.edu, nypl.org) — trafilatura on static HTML hits its expected ceiling here.
   - **Sharper metric:** "unexpected empty rate after factoring out server-blocks, PDFs, and interstitials" = **4 / 34 = 11.8%**. This is the meaningful signal for scraper-quality regressions; treat as the working synthetic baseline. **Caveat:** n=34 in the effective denominator gives a 95% Wilson CI of roughly [4.6%, 26.6%] — treat 11.8% as directional, not precise. Tightening requires more URL coverage or post-deploy production data via the Unit 9c filter below.
   - **Production measurement methodology (post-Unit-9c).** Once `embedding_skip_reason` lands, the canonical query for tracking unexpected empties is `WHERE embedding_skip_reason IS NULL AND size(coalesce(w.scraped_text, '')) = 0` (Cypher) or the SQLite analog `WHERE embedding_skip_reason IS NULL AND (scraped_text IS NULL OR scraped_text = '')`. **Note:** the empty-string clause is load-bearing — `scrape_webpage()` returns `("", "")` on failure (not NULL), so `IS NULL` alone misses the cases this query exists to surface. Documented here so the methodology doesn't get orphaned by Gate 5.1's deletion.
   - **Scratch artifacts:** `preflight_6_urls.txt` (50 URLs, repo root) and the throwaway scraper script (`/tmp/preflight_6_scrape.py`, host + container) are diagnostic-only. Do NOT commit `preflight_6_urls.txt` — same hygiene class as `coyote_server_logs_*.txt` and `tmp_*.csv` per CLAUDE.md's git note.
7. **Unit 10 embedder candidate pre-verification.** (Run only when Unit 10 is imminent — not at branch open. Listed here for completeness so R7 can reference it.) For the chosen candidate from the MTEB CSV review: verify license terms allow Coyote's distribution model, confirm no instruction-prefix requirement (rules out e5/nomic family without API changes), and run a held-out retrieval sanity check on 20 representative Coyote pages to confirm the headline MTEB jump is not a leaderboard artifact (contamination / task-specific tuning). For embeddinggemma-300m specifically: this is the highest-priority verification given the +19.76 MTEB jump.
8. **Branch and CI baseline.** Create `coyote-0.5-nlp-refactor` from `coyote-0.4`. Run existing 85-test suite. All green before adding any new code.

---

## 3. Branch and rollback strategy

- **Branch from:** `coyote-0.4` (HEAD as of refactor kickoff).
- **Branch name:** `coyote-0.5-nlp-refactor`.
- **Merge target:** none yet. Do not merge to `main` or back to `coyote-0.4` until top-level verification (section 5) passes.
- **Rollback:** if any unit's gate fails irrecoverably, the unit's commits stay on the branch but the branch is paused. Worst case = abandon branch entirely; `coyote-0.4` is the live state. No data migrations to undo because Coyote data is expendable (per CLAUDE.md).
- **Volume strategy per unit:** prefer to wipe `volumes/neo4j` and `volumes/coyote/wikidata_cache.db` after each major unit lands, then run a known browsing replay sample so each unit's gates measure against clean data. Procedure documented in CLAUDE.md MVP deploy section.

---

## 4. Implementation sequence

Ten work units. Each unit has: deliverable, dependencies, work breakdown, verification gate. Units 1-3 are sequential (each blocks the next). Units 4-8 are mostly sequential with some parallelization possible. Unit 9 is parallelizable polish. Unit 10 is last.

---

### Unit 1 — Fix Finding G (search-event zero-Topics bug)

**Plan item:** 1. **Estimated effort:** small, but depends on root-cause investigation.

**Why first:** every subsequent unit's verification gate measures topic/edge counts. If search-event pages contribute zero topics, the denominator is biased and gates lie.

**Work breakdown:**
- **Design decision (resolved 2026-06-01):** SERPs exempt by design. Justin is also considering removing SERPs entirely as post-MVP work — OUT of scope here.
- **SERP tagging: use existing `isSERP` property — do NOT introduce `topic_skip_reason`.** `isSERP` (boolean) is set on every Webpage at `coyote_browser_extension_to_neo4j.py:446, :485` via the SERP detector at `:298`, and is already referenced in `nl2cypher.py:84` (worked Cypher example) and `:12` (LLM-facing schema string). Gate queries for Unit 1+ should use `WHERE w.isSERP = false` to exclude SERPs from topic-count denominators. The other zero-topic categories (non-SERP exempt URLs, empty-scrape) are covered by Unit 9c's `embedding_skip_reason` — different problem, different scope. The convention split (`isSERP` boolean vs `embedding_skip_reason` string) is accepted as historical; renaming `isSERP` would touch the schema doc and worked-example Cypher and is not worth the cosmetic gain.
- **Root-cause investigation still required.** The design decision doesn't tell us whether the current zero-topics-on-SERP-Webpage state results from correct exemption or from an incidental bug. Confirm via Neo4j query that the partition matches expectation (categories listed in the gate below) before concluding "no code change needed beyond documentation." Likely bug candidates if expectation is violated: search events skipping the NLP pipeline incorrectly, `analyze_topics` failing on short SERP text, scraping returning empty text on the SERP URL.
- **Known Issue dependency:** the `isSERP` detector is Google-only — non-Google SERPs (Bing, DuckDuckGo, etc.) get `isSERP = false` and contaminate the "true-no-topic" partition. Pre-existing tech debt in coyote-0.4; documented in CLAUDE.md Known Issues; out of scope here. Unit 1's gate accuracy is bounded by this gap.

**Verification gate:** documented "expected distribution" of Webpage nodes with zero HAS_TOPIC edges, partitioned as: (c) SERP-exempt-by-design (`isSERP = true`); (a+b+d) everything-else aggregated until Unit 9c's `embedding_skip_reason` lands and enables sub-partitioning into (a) exempt URLs, (b) empty-scrape, (d) true-no-topic. On a fresh browsing replay, observed distribution matches the documented expectation within tolerance. Unit 1's MVP gate passes with the coarse (c) vs everything-else partition; the fine four-way partition becomes verifiable in Unit 9c.

---

### Unit 2 — Term→QID cache (`query_wikidata`)

**Plan item:** 0b. **Estimated effort:** small (~half day).

**Why second:** cheap WDQS-load win. Should land before Unit 7 (mwapi fuzzy, which increases query volume) so cache absorbs that increase. Independent of the KeyBERT swap.

**Work breakdown:**
- Repurpose the vestigial `WikidataCache(entity, data, timestamp)` table in `initialize_databases.py:242-256`. It already has the correct shape.
- Add SELECT-by-entity at the top of `query_wikidata()` in `text_bertopic_analysis.py:157-219`. On hit, return cached data. On miss, run SPARQL, INSERT result (including empty results — cache misses too, so repeated zero-match terms don't keep hitting WDQS).
- Add expiry on `timestamp`. Default TTL: 30 days, configurable via env var `WIKIDATA_TERM_CACHE_TTL_DAYS`. Document in CLAUDE.md.
- Add a config_manager DB connection accessor if not present (`get_wikidata_cache_db_connection()` exists at `config_manager.py:233`; reuse).
- Log cache hits and misses at DEBUG; emit a per-batch INFO summary (e.g., `WikiData cache: 47 hits / 12 misses`).

**Verification gate:**
- On a fixed event-replay sample of N pages, count of `query_wikidata` SPARQL outbound calls drops materially vs Unit 1 baseline (target: >50% reduction on a representative re-run sample).
- Cache hit ratio observable in logs.
- 85-test suite still green; new cache hit/miss unit test added (see section 8).

---

### Unit 3 — KeyBERT swap + per-doc chunk-and-pool fix (BUNDLED)

**Plan items:** 2 + 3 + 0a (dissolved). **Estimated effort:** large — the highest-impact and most code-touching unit.

**Why bundled:** KeyBERT scores phrases against the document embedding. The current document embedding is silently truncated to ~200 words (256-token MiniLM cap). Bundling ensures the doc embedding being scored against is representative of the full document, not its first paragraph.

**Sub-units:**

**3a. Chunking module.**
- New module: `images/core/core_analysis/coyote/analysis/nlp/chunking.py`.
- API target: `chunk_text(text: str, max_tokens: Optional[int] = None, boundary: str = "paragraph_aware") -> List[str]`.
- `max_tokens` default: when None, derived from the active embedder's `max_seq_length` property at call time (sentence-transformers models expose this — e.g., `embedder._sentence_transformer.max_seq_length` or equivalent through the `coyote_embedder` module wrapper). **This couples chunk size to the active embedder automatically**, so Unit 10's model swap propagates to chunking without a separate config change. Explicit `max_tokens` overrides the default for callers that want a different size (e.g., post-MVP section-retrieval may want smaller chunks).
- `boundary="paragraph_aware"`: greedy paragraph split; if a paragraph exceeds `max_tokens`, fall back to sentence split via existing spaCy sentencizer.
- Designed so post-MVP section-retrieval feature can call the same module with different params (smaller `max_tokens`, different boundary strategy).
- Unit-tested in isolation (no embedding calls — pass `max_tokens` explicitly in tests).

**3b. Per-doc embedding chunk-and-pool fix.**
- Modify `coyote_embedder.embed_text()` OR add `embed_document(text: str) -> np.ndarray`. Decision: add a separate function so the simple `embed_text` API remains usable for short text (search terms, etc.).
- `embed_document`: chunk text → embed each chunk → mean-pool to one vector → return.
- Replace callers of `embed_text` in `coyote_nlp_state_manager.py` Step 20.5 with `embed_document` for Webpage embeddings.
- Leave Annotation embedding (Step 10.5) using `embed_text` (annotations are short).

**3c. KeyBERT integration.**
- Replace `bertopic_analysis.py` and the BERTopic path in `coyote_nlp_state_manager.py` Step 13 with KeyBERT.
- New module: `images/core/core_analysis/coyote/analysis/nlp/keybert_analysis.py`.
- API target: `extract_keywords(text: str, doc_embedding: np.ndarray, nlp: spacy.Language, top_n: int = 20, mmr_lambda: float = 0.6) -> List[Tuple[str, float]]`. The caller passes the pre-loaded spaCy instance — see single-instance paragraph below.
- KeyBERT receives the pre-computed `doc_embedding` from 3b — does not re-embed. (KeyBERT supports passing precomputed doc embeddings.)
- Custom vectorizer using spaCy `noun_chunks` (Option E, per pre-flight 2 A/B re-run, 2026-06-01): pre-compute the noun-chunk set once per document from the shared spaCy parse (det/pronoun-stripped, alpha-token-only, length 1-4 tokens), then build a `CountVectorizer(analyzer=callable)` whose callable **closes over the pre-computed set** and returns it verbatim per document. **Do NOT call `nlp()` inside the analyzer** — that would re-parse the document on every `CountVectorizer.fit` invocation, defeating the single-spaCy-instance simplification below. **Use `analyzer=`, NOT `tokenizer=`, and NOT KeyBERT's `candidates=` parameter.** Rationale (verified by pre-flight 1 + pre-flight 2): `tokenizer=` would let sklearn's `_word_ngrams` re-form n-grams across the analyzer's output and break the noun-chunk boundary guarantee; KeyBERT 0.9.0's `candidates=` parameter silently re-tokenizes multi-word strings via default `token_pattern=r'(?u)\b\w\w+\b'`, dropping them from the vocabulary entirely (sub-risk recorded under R1).
- **Single spaCy instance shared with NER** (`text_ner_analysis.py`). noun_chunks requires the parser to be enabled — the same configuration the NER step needs. The KeyBERT candidate generator and the NER step share one fully-loaded `spacy.load("en_core_web_sm")` instance. **Mechanism: constructor injection, not module-level singleton.** The NLP state manager owns the instance (loaded once at manager startup), holds it as an attribute, and passes it explicitly as the `nlp` parameter to both `text_ner_analysis.extract_entities(...)` and `keybert_analysis.extract_keywords(...)`. No hidden module-level state; instance lifetime tracks the manager's lifetime. Memory cost ~50 MB for the full pipeline (vs ~30 MB for parser-disabled); net saving versus the two-instance approach the plan originally specified for Pekar. **Residual double-parse:** each document is still parsed twice per event (once inside NER, once inside KeyBERT's noun-chunk pre-computation). Eliminating this via a pre-parsed `Doc` parameter is a candidate post-MVP follow-up if profiling shows the second parse is a hotspot — out of scope for Unit 3c. Document the shared-instance lifecycle in CLAUDE.md.
- MMR enabled (λ starting per pre-flight check 2 result; configurable via `KEYBERT_MMR_LAMBDA` env var, default 0.6).
- Score is cosine similarity to doc embedding — replaces `tfidf_score`.

**3d. Wiring and module migration.**

`Topics.score` semantics change: now cosine-similarity (range ~0.0-1.0). Update CLAUDE.md and any consumer that filters on the old TF-IDF range.

The migration is order-sensitive. Execute as an ordered checklist; do not skip ahead.

- [ ] **M1. Create new module** `images/core/core_analysis/coyote/analysis/wikidata_lookup.py` (core-only — NOT in `shared/`, since the agent does not need WikiData lookup and adding to `shared/` would force agent-side sync).
- [ ] **M2. Move `query_wikidata`** (`text_bertopic_analysis.py:157-219`) into `wikidata_lookup.py`. The cache logic from Unit 2 lives in this module.
- [ ] **M3. Move `map_topics_to_wikidata`** (`text_bertopic_analysis.py:234-257`) into `wikidata_lookup.py`.
- [ ] **M4. Update imports in `coyote_nlp_state_manager.py`:** change `from text_bertopic_analysis import ...` for the two moved functions to `from coyote.analysis.wikidata_lookup import ...`.
- [ ] **M5. Update imports in `text_ner_analysis.py`:** `map_ner_to_wikidata` calls `query_wikidata` internally. Update its import to point at `wikidata_lookup`.
- [ ] **M6. Clean up `text_ner_analysis.py` internally:** delete the duplicate `calculate_tfidf_on_phrases` at line 119. It is dead code (only the bertopic copy is imported). This is a tech-debt cleanup independent of the migration; do it now so the file is clean.
- [ ] **M7. Confirm `text_ner_analysis.py` still passes its module-level smoke test** after M5 + M6. The file survives the refactor; it should be functional with cleaner internals.
- [ ] **M8. Remove `get_topic_from_text`** from `text_bertopic_analysis.py`. Verified 2026-05-27: zero callers anywhere in the codebase (`grep -rn "get_topic_from_text" --include="*.py"` returns only the definition). Pure dead code; delete with no migration.
- [ ] **M9. Mark `calculate_tfidf_on_phrases` as transitional dead-after-Unit-4.** Do NOT delete it yet. After M8, the topics call site at `coyote_nlp_state_manager.py:610` is replaced by Unit 3's KeyBERT path, but the entities call site at `:665` still calls this function until Unit 4 lands. Leave the function in place; add a `# DEPRECATED: remove after Unit 4 lands` comment. Final deletion happens in Unit 4's cleanup (see Unit 4 work breakdown). This preserves a buildable state between Unit 3 and Unit 4 completing.
- [ ] **M10. Delete `bertopic_analysis.py`** (the BERTopic wrapper module).
- [ ] **M11. Move surviving constants out, then delete `text_bertopic_analysis.py`.** After M2–M9 the file is down to spaCy/stopwords boilerplate. Move the custom stopword list at `text_bertopic_analysis.py:143-146` to a new shared module `images/core/core_analysis/coyote/analysis/nlp/stopwords.py` (Unit 6 also imports from here). Then delete `text_bertopic_analysis.py` entirely. No conditional or "either/or" — both halves of this step ship together.
- [ ] **M12. Update `requirements.txt`:** remove `bertopic`, `umap-learn`, `hdbscan` if KeyBERT does not transitively require them (verify on install — KeyBERT depends on sentence-transformers but should not transitively pull UMAP/HDBSCAN). Add `keybert`.
- [ ] **M13. `make sync-shared`** runs (if applicable — `wikidata_lookup.py` is core-only and NOT under `shared/`, so sync test should not require new entries; verify the sync test still passes).

**3e. Connect to ontology (`connect_to_ontology.py`) — env var rename, atomic across all touch points.**
- The threshold-filter at the URI-loop entry uses `tfidf_score`. After this unit, score semantics differ. Re-baseline the threshold around p50 of the new score distribution, measured empirically.
- **`TFIDF_TOPIC_THRESHOLD` → `TOPIC_SCORE_THRESHOLD` rename — atomic across:**
  - `connect_to_ontology.py` (code reference)
  - `CLAUDE.md` (Environment Variables table + Session 2 reference)
  - `compose.yaml` (if referenced)
  - `.env.example` (if referenced)
  - Any test that references the old name
  Treat the rename as a single commit; do NOT ship the code change in one commit and docs in another, since the running pipeline will read whatever env var is set in the deploy environment.

**Verification gates:**
- **Gate 3.1 (functional):** on a sample of 20 representative pages, KeyBERT produces 5-20 topics each. **Phrase-quality criteria** (replaces the cross-sentence-ngram criterion from the Pekar-era plan — structurally impossible to violate with noun_chunks, since dependency parses are per-sentence): top-5 phrases per page are recognizable noun phrases — no verb-led fragments (e.g., "vibe coding might come"-style), no POS-soup (e.g., "think tanks governmental"-style), no bound-morpheme unigrams (e.g., bare "pre" / "anti"). **Wikidata-searchability criterion (from pre-flight 2 A/B):** ≥12/20 pages (60%, proportionally equivalent to pre-flight 2's ≥3/5 threshold) have at least one top-5 phrase recognizable as a Wikidata-searchable concept. Spot-check by human reviewer at gate-run time.
- **Gate 3.2 (truncation fix):** committed test fixture at `tests/fixtures/long_tutorial_article.txt` (~3000 words, content that develops substantively across many paragraphs — choose a tutorial / technical explainer / long-form argument, NOT a news article with inverted-pyramid structure where the head already covers the content). Gate threshold is **set empirically from pre-flight check 5** (chunk-and-pool similarity distribution measurement), NOT hardcoded. Threshold sits just below the lowest cosine-similarity observed on substantive-tail-content pages during pre-flight, so substantive-tail content reliably passes the gate. Fixture committed so Unit 10 can re-run the regression with the same threshold.
- **Gate 3.3 (no regressions):** 85-test suite green; new KeyBERT and chunking unit tests pass; Neo4j vector indexes still ONLINE.
- **Gate 3.4 (score sanity):** baseline `Topics.score` distribution set empirically from pre-flight 2 results: median ~0.18, IQR ~0.16, max ~0.77 across the 5 sample pages at λ=0.6 with noun_chunks candidates (n=100). **`Topics.score` is raw cosine similarity between candidate-phrase embedding and pooled document embedding — NOT the MMR objective.** Verified by reading `keybert._mmr.mmr` source (2026-05-28): MMR's diversity-penalized objective is used only for `np.argmax` selection within the loop; the score stored alongside each selected keyword is the raw `word_doc_similarity[idx]`. Gate (post-filter, what reaches Neo4j): persisted `Topics.score` values are in (0, 1]; distribution is non-degenerate (sample IQR meaningfully non-zero). Pre-filter raw KeyBERT output may include small negatives (~-0.05 observed in pre-flight 2) — these are mild anti-correlation in MiniLM cosine space, not MMR penalty artifacts; `TOPIC_SCORE_THRESHOLD` (Unit 3e, retuned per Unit 3 deploy) filters them before persistence.

---

### Unit 4 — NER mention-frequency scoring

**Plan item:** 0c. **Estimated effort:** small.

**Why this position:** the existing TF-IDF path runs against a TED Talk corpus (`coyote_nlp_state_manager.py:607`), not the placeholder corpus — but TED Talks are a wrong-domain reference for arbitrary web content, and the corpus is brittle to seed-data presence (empty `CorpusDocuments.source='TEDTalk'` rows → empty corpus → broken IDF). Once Unit 3 dissolves TF-IDF for topics, leaving `Entities.score` on the same broken plumbing makes the two tables incoherent.

**Code locations to verify and modify:**
- `coyote_nlp_state_manager.py:665` — Step 19 calculates `entities_scored = calculate_tfidf_on_phrases(processed_text, corpus=corpus, threshold=0.07)`. This is the TF-IDF call to replace.
- `coyote_nlp_state_manager.py:668-676` — Step 20 writes the result with `UPDATE Entities SET score=? WHERE event_id=? AND entity=? COLLATE NOCASE`. The UPDATE shape stays; only the value source changes.
- The topics TF-IDF call at `coyote_nlp_state_manager.py:610` (Step 13) is dissolved by Unit 3's KeyBERT swap, not by this unit. Confirm Unit 3 lands first.

**Work breakdown:**
- Replace the entities TF-IDF call path with a mention-frequency calculation. Source data: `extracted_entities` already populated at Step 14; count mentions of each entity in the `scraped_text` (or use the `entity_context` column already in the Entities table for context-window-based counting).
- Recommended formula: `score = log(1 + count_of_mentions_in_doc)`. Rationale: scale-invariant; ranks robustly; doesn't dilute on long docs.
- Configurable via `NER_SCORE_FORMULA` env var (values: `log`, `freq_normalized`, `saturated`) so the choice can be revisited without code change. Default `log`.
- Update CLAUDE.md to document the new score semantics.
- **Cleanup (final deletion deferred from Unit 3 M9):** once the entities call site at `coyote_nlp_state_manager.py:665` no longer invokes `calculate_tfidf_on_phrases`, delete the function from `text_bertopic_analysis.py` (or wherever it now lives after Unit 3's M11 final pass). Also remove the import of `calculate_tfidf_on_phrases` from `coyote_nlp_state_manager.py:34`. This completes the dead-code removal that Unit 3 M9 deliberately deferred to preserve buildability between units.

**Verification gate:**
- `SELECT count(*) FROM Entities WHERE score > 0.0` returns >0 after one new event processed.
- On a sample page, entities mentioned 5+ times outrank entities mentioned once.
- A synthetic-test page (committed fixture) with controlled mention counts produces the expected ranking under each of the three formulas.

---

### Unit 5 — Metadata harvesting (trafilatura swap already shipped)

**Plan item:** 6. **Estimated effort:** small to medium.

**Status check:** Trafilatura is ALREADY in production on `coyote-0.4` — `scrape_webpage.py:7` imports it; `:140` calls `extract()` for body text; `:147` calls `extract_metadata()` but currently only persists `.title`. This unit harvests the additional structured metadata that the original trafilatura swap did not.

**Why this position:** feeds Unit 7 (mwapi fuzzy benefits from disambiguation hints), polish items (title-boost, domain-aware routing), and provides candidate context for Unit 8.

**Work breakdown:**
- Extend the metadata extraction in `scrape_webpage.py` beyond `metadata.title`. Use trafilatura's `extract_metadata()` properties: `description`, `tags`, `categories`, `date`, plus parse the raw HTML separately (or via trafilatura JSON-LD support) for `og_type`, `og_description`, and `schema.org/about` `sameAs` Wikidata QIDs.
- Persist as new properties on the SQLite WebpageLoads row and on the Neo4j Webpage node.
- New Neo4j writer parameters: extend Webpage CREATE/MERGE to set the new properties when present.
- Schema documentation update in CLAUDE.md.

**Pre-Unit measurement required:**
- Re-measure the current empty-scrape rate on `coyote-0.4` HEAD. The 67% figure in CLAUDE.md Known Issues predates trafilatura and is not the live baseline. Note: the rate is bounded above by the unfiltered redirect-URL events the browser extension still captures (see CLAUDE.md Known Issue on click-tracking redirects) — factor those OUT of the denominator, or use them as a known ceiling.
- **Satisfied by pre-flight 6 (2026-05-28).** Results: 11.8% unexpected empty rate (4/34 effective denominator, Wilson 95% CI [4.6%, 26.6%]); full breakdown in pre-flight 6 results above.

**Verification gates:** (Gate 5.1 retired 2026-05-28 — was conceptually misaligned with Unit 5, which harvests additional metadata from already-successful scrapes and does not move the empty-scrape rate. Scraper-health observations now live under pre-flight 6 results instead. Numbering preserved as 5.2 / 5.3 to keep the gate-number history readable.)
- **Gate 5.2:** at least 5/20 pages with `og_type` or `og_description` non-null (sanity check on OG harvesting); at least 2/20 pages with non-null `schema_about_uris` containing valid Wikidata QIDs (JSON-LD adoption is sparser than OG tags).
- **Gate 5.3:** no regression in `embedding_text` content quality — the text being embedded is still the body content, not metadata fields contaminating the body.

---

### Unit 6 — Layer 2 post-extraction token quality filter

**Plan item:** 7. **Estimated effort:** small.

**Work breakdown:**
- After KeyBERT (Unit 3) returns phrases, before WikiData mapping:
  - Drop phrases matching the existing custom-stopword list (`text_bertopic_analysis.py:143-146` — preserve and import to a shared location).
  - Drop phrases of length <3 characters. Preserves meaningful 3-char tokens (`NLP`, `OER`, `API`, `MVP`, `AI`) common in academic/tech corpora.
  - Drop bound-morpheme tokens that survive the length-3 check but are word-formation prefixes, not standalone concepts: `pre`, `anti`, `non`, `sub`, `pro`, `neo`, `post`, `semi`, `pseudo`, `quasi`. Pre-flight 2 surfaced "doc pre" in candidate output (score 0.2350 on the BERTopic page), confirming the gap: spaCy tags these as ADJ in compound-word contexts and they leak through as unigrams. Independent of the noun_chunks switch — applies to whichever candidate generator is in use.
  - Drop Wikipedia citation-template fragments: `cite web`, `cite news`, `cite book`, `cite journal`. These are wiki-markup leakage that trafilatura should strip but doesn't always. Pre-flight 2 did NOT surface these in any condition's top-5 on the 5 sample pages, so this is cheap insurance, not a gating issue.
  - Drop pure-numeric phrases.
  - Drop single-character tokens.
  - Drop temporal-noise phrases (extension of MVP Fix 1 STOP set: `day`, `days`, `hour`, etc.).
- Centralize the filter list in a shared module so chains.py `_terms()` and the NLP filter use the same source.

**Verification gate:** on a sample of 20 pages, no phrases in the filter list appear in `Topics` rows.

---

### Unit 7 — WikiData mwapi fuzzy label matching

**Plan item:** 4. **Estimated effort:** medium.

**Why this position:** depends on Unit 2 (cache absorbs the increased query volume from broader matching). Improves coverage of terms that currently return zero matches under exact-label SPARQL.

**Work breakdown:**
- Replace the current strict label-match SPARQL in `query_wikidata` with a `wikibase:mwapi` fuzzy-search query. Single-stage (no NER-context disambiguation — too expensive, deferred).
- The cache layer from Unit 2 wraps this; cache lookup first, fuzzy SPARQL on miss, cache result (including empty results).
- Update the User-Agent and any rate-limit handling for the new query shape.
- Keep the existing exact-match path as a fallback if mwapi returns nothing.

**Verification gates:**
- **Gate 7.1:** on a sample of pages, label-mapping coverage (proportion of KeyBERT phrases that map to a Wikidata QID) increases materially vs Unit 6 baseline (target: >20% relative increase).
- **Gate 7.2:** no spurious mappings introduced. Top-mapped phrases pass human spot-check (avoid the known "ai → Anguilla" class of errors — these are Unit 8's domain, not made worse here).
- **Gate 7.3:** WDQS call volume does not spike disproportionately (cache from Unit 2 is doing its job).
- **Gate 7.4:** WikiData circuit breaker does not trip during the replay.

---

### Unit 8 — NER semantic-similarity post-filter

**Plan item:** 5. **Estimated effort:** medium.

**Work breakdown:**
- For ambiguous mappings (currently `"ai"` → Anguilla, `"gpt"` → GNU Portable Threads, `"First Monday"` → calendar date), add a post-mapping filter.
- For each candidate (label, uri) pair, fetch the Wikidata `description` for the QID. **Batch description fetches in a single SPARQL `VALUES` clause per page** rather than one call per QID, to bound WDQS load.
- Embed the description using the active embedder (whichever Unit 10 selects, or current MiniLM).
- Compare against the page/entity context embedding.
- Pick the highest-scoring candidate. Below threshold → "no mapping".

**Cost accounting (per page, M phrases × K candidates from mwapi):**
- SPARQL: 1 batched description query per page (after Unit 2 cache absorbs repeats across pages). Cache extension: persist description fetches in the term→QID cache keyed by QID alongside the term mapping, so repeat description fetches don't re-hit WDQS.
- Embedding ops: K × M descriptions embedded per page (local CPU; no rate limit but additive latency). With K=5 mwapi candidates and M=20 phrases that's 100 embedding ops per page, ~1-3 seconds CPU on current MiniLM. Tolerable but not free.
- Net: small SPARQL increase (1 batched call), material CPU increase (~1-3s per page). Document the latency in CLAUDE.md.

**Verification gates:**
- **Gate 8.1 (precision on known-bad cases):** ≥80% of the curated known-bad set resolve to the semantically correct QID. Curated set must include: `"ai"` → Q11660 (artificial intelligence) not Anguilla; `"gpt"` → Q105434500 (GPT) not GNU Portable Threads; `"First Monday"` → the journal Q3739241 (or the magazine, depending on context) not the calendar date; plus 5-10 additional cases assembled during pre-flight check 1. Curated set committed to `tests/fixtures/ambiguous_terms_known_bad.json`.
- **Gate 8.2 (no regression on known-good):** terms that mapped correctly before Unit 8 still map correctly after. Curated set committed similarly.
- **Gate 8.3 (informational, NOT pass/fail):** track mapping coverage delta vs Unit 7 baseline. A correctly-working semantic filter SHOULD prune some bad mappings, so coverage may legitimately drop by 10-20%. Use this number as a sanity check (a 60%+ drop would indicate the embedder threshold is too aggressive), not as a binary gate.

---

### Unit 9 — Polish (parallelizable)

**Plan items:** 8a, 8b, 9, 10, 11. **Estimated effort:** medium total; individual items small.

Each of these can ship independently. Order them by impact / risk:

- **9a. Title-anchored scoring boost.** For KeyBERT phrases that appear in `page_title` (from Unit 5), multiply the cosine-similarity score by a boost multiplier. Default: 1.5×. Configurable via `TITLE_BOOST_MULTIPLIER` env var (suggested range 1.5–2.0). **Runtime order:** KeyBERT scoring (Unit 3c) → Unit 6 post-extraction filter → title boost → Unit 3e threshold cut. Boosting happens AFTER the stopword/length/numeric filter (no point boosting phrases that will be discarded) and BEFORE the threshold cut (so a boosted phrase can survive the threshold that an unboosted version would fail).
- **9b. Per-Webpage HAS_TOPIC edge cap.** Hard top-N by score (suggest N=20) at edge-creation time. Deterministic ceiling.
- **9c. `embedding_skip_reason` and `wikidata_skip_reason` properties.** Tag exempt URLs vs empty-scrape vs breaker-throttled events so post-deploy gates can interpret the null-embedding bucket.
- **9d. Wikidata P31 instance-of blocklist.** Replace hardcoded `WIKIMEDIA_META_URIS` list in `connect_to_ontology.py` with a P31 query + blocklist at mapping time. Survives Wikidata schema drift.
- **9e. Domain/type-aware routing.** `og_type=video` vs `og_type=article` vs SERP routed to different pipelines or scoring profiles.

**Verification gates:** each sub-item has its own small gate (e.g., for 9b, no Webpage has more than N HAS_TOPIC edges; for 9c, a sample query distinguishes the three skip reasons; for 9d, two checks — (i) zero meta-class edges in a 2-hour window post-deploy [the deferred Gate B query: `MATCH (w:Webpage)-[:HAS_TOPIC]->(t:WikiDataOntology) WHERE datetime(w.timestamp) > datetime() - duration({hours: 2}) AND t.label IN ['Wikimedia category', 'Wikimedia administration category', 'Wikimedia disambiguation page', 'Category:Wikipedia categorization'] RETURN count(*) AS junk_edges;`], and (ii) after a Wikidata schema change to category labels, mapping still works).

---

### Unit 10 — MTEB embedder swap (decision pending)

**Plan item:** 13. **Estimated effort:** medium — this is NOT a "parameter swap," see below.

**Why last:** the swap sits on top of a stable pipeline so new-embedder behavior is not confounded with new-pipeline behavior. But the swap itself has non-trivial scope.

**Decision dependencies:** Justin to rank candidates on MTEB Clustering + STS subscores (deferred from 2026-05-27 session).

**Work breakdown (full scope — NOT a parameter swap when the embedding dimension changes):**

- **Update model identity and dimension:** change `EMBEDDING_MODEL_NAME` and `EMBEDDING_DIMENSION` in `shared/embedding_config.py`. Run `make sync-shared` to propagate to both `images/agent/app/shared/` and `images/core/core_analysis/shared/`.
- **Docker rebuild with pre-download:** the Core Dockerfile pre-downloads the embedder to `/opt/embedding_model` at build time. Update the Dockerfile pre-download step to fetch the new model. Rebuild both `coyote_app` and `bot` images (agent uses the same model via `HuggingFaceEmbeddings` wrapper).
- **Verify model loads through BOTH wrappers:** Core uses `SentenceTransformer` directly (`coyote_embedder.py`); Agent uses LangChain's `HuggingFaceEmbeddings`. Different wrappers exercise different parts of the model loading code path. Test both load paths before proceeding.
- **Drop and recreate Neo4j vector indexes:** indexes `webpage_embedding` and `annotation_embedding` were created with 384 dimensions. If the new model has a different dimension, the indexes must be dropped, the volume wiped (since all existing embeddings now have a wrong dimension and can't be queried), and indexes recreated on first node insert with the new dimension. The volume wipe is acceptable per data-expendable policy but must be explicitly performed; the indexes do not auto-recreate themselves to a new dimension.
- **Re-baseline `VECTOR_SIMILARITY_THRESHOLD`:** the current 0.65 was tuned against all-MiniLM-L6-v2's cosine distribution. Different embedders produce different similarity score distributions (e.g., bge-base-en-v1.5 at 768 dim tends to produce lower raw cosine values than MiniLM at 384). Without re-baselining, Tier 0 may silently over- or under-retrieve. Procedure: run a sample of representative NL queries against the new index, observe top-K cosine values, set threshold at the elbow of the score distribution.
- **Update `embedding_model` property on all embedded nodes:** the architectural invariant `embedding_model: "all-MiniLM-L6-v2"` (CLAUDE.md Vector Embedding Rollout) must reflect the new model name. Existing nodes are wiped with the volume, so this happens automatically on re-ingest.
- **API compatibility check:** confirm the new model does NOT require an instruction prefix (e.g., `intfloat/e5-*` and `nomic-embed-*` require `query:` / `passage:` prefixes — these are NOT plug-and-play and would require splitting `embed_text()` into `embed_document()` / `embed_query()`).

**Top candidates from MTEB CSV review (2026-05-26):**
- A. GIST-all-MiniLM-L6-v2 (384/512, +1.31 over baseline) — literal drop-in. No index dim change. Threshold re-baseline still needed (different model distribution even at same dim).
- B. bge-base-en-v1.5 (768/512, +3.13) — conservative upgrade, MIT. Reindex required.
- G. embeddinggemma-300m (768/2048, +19.76) — investigate license + verify non-contamination first.
- H. gte-base-en-v1.5 (768/8192) — long-context; if chosen, per-doc chunking still runs but most pages = 1 chunk.

**Verification gates:**
- 85-test suite green.
- Vector indexes recreate ONLINE at the correct dimension for the chosen model.
- On a sample of 20 pages, Tier 0 vector retrieval returns sensible top-K matches with the re-baselined threshold.
- Gate 3.2 fixture regression test still passes (long-article pooled embedding ≠ first-chunk-only).
- New `embedding_model` property on a sampled Webpage node matches the chosen model name.

---

## 5. Top-level verification — "are we done?"

After all units land, run these gates on a fresh deploy with a known browsing replay sample (~50 pages spanning articles, SERPs, videos, exempt URLs).

- **Top-level Gate 1 — HAS_TOPIC edge quality.** Compared to the Session 3 post-deploy baseline (when measured): avg edges/page is in a reasonable range (target: 5-20 per article, lower for SERPs); top-5 edges per page are semantically meaningful to a human reviewer; no meta-class edges (Gate B regression check).
- **Top-level Gate 2 — Entity scoring sanity.** `Entities.score` is non-zero, log-distributed, with high-mention entities outranking low-mention ones. Verified by sampling.
- **Top-level Gate 3 — WikiData coverage and load.** Mapping coverage improved from baseline; WDQS call volume per page reduced (cache effect); circuit breakers do not trip during normal replay.
- **Top-level Gate 4 — Vector retrieval (Tier 0).** Long articles' pooled embeddings retrieve correctly; truncation regression test fails for the pre-refactor pipeline (sanity that the bug existed) and passes for the new pipeline.
- **Top-level Gate 5 — No security regressions.** `is_read_only()` blocklist unchanged; sync test still passes; ~85+ test suite green.

If all five pass → refactor branch is ready to merge to `main` (or to be tagged as `coyote-0.5`, depending on release model).

---

## 6. Risks and mitigations

- **R1 — Custom vectorizer integration is more subtle than the summary suggests.** *Originally resolved 2026-05-28 by pre-flight 1 for the Pekar pathway (analyzer= vs tokenizer=). Superseded 2026-06-01 by pre-flight 2 A/B re-run:* Pekar's POS-filtered n-gram output is grammatically incoherent ("think tanks governmental", "taxonomy constructs psychology") even when correctly implemented with `CountVectorizer(analyzer=callable)`. Dropped in favor of spaCy `noun_chunks` (Option E), which produces actual noun phrases on the same A/B sample. **Sub-risk discovered and recorded:** KeyBERT 0.9.0's `candidates=` parameter silently re-tokenizes multi-word strings via default `token_pattern`, dropping them from the vocabulary. Production code MUST use `vectorizer=` with a `CountVectorizer(analyzer=callable)` — never `candidates=`. See Unit 3c implementation notes.
- **R2 — KeyBERT diversity (MMR) doesn't give acceptable top-N on Coyote's content.** *Resolved 2026-06-01 by pre-flight 2 A/B re-run:* λ=0.6 produces acceptably diverse top-20 across all 5 sample pages with no near-duplicate clustering. Score distribution non-degenerate (median 0.18, max 0.77, IQR 0.16). λ-comparison at 0.5/0.7 was deferred — the pathway debugging consumed pre-flight 2's original budget — but λ remains configurable via `KEYBERT_MMR_LAMBDA`; downstream tuning can re-test without code change.
- **R3 — Chunk-and-pool changes per-doc embedding semantics, breaking Phase C v1 Tier 0 retrieval.** Tier 0 cosine similarity threshold (VECTOR_SIMILARITY_THRESHOLD=0.65) was tuned against first-chunk embeddings. *Mitigation:* re-baseline the threshold after Unit 3 deploy, document in CLAUDE.md.
- **R4 — Trafilatura returns empty on some site types and there is no fallback extractor.** Verified 2026-05-27 against `scrape_webpage.py`: the only error path is `except Exception → return ""`. There is NO BeautifulSoup or alternative-extractor fallback; the current code logs empty-extraction as a normal outcome. *Mitigation:* (a) extend pre-flight 6 to bucket the empty-scrape sample by site type so the failure-mode breadth is known before the refactor lands; (b) if a specific site type shows systematic empty extraction, add a targeted BS4 fallback path as a follow-up unit (out of MVP scope unless the breadth is large); (c) Unit 9c (`embedding_skip_reason`) makes the failure observable post-deploy so site-type patterns can be detected from production data.
- **R5 — Cache size grows unboundedly.** *Mitigation:* TTL expiry + the existing `database_cleanup_manager.py` already handles `wikidata_cache` URI cache; extend the same cleanup to the entity cache.
- **R6 — mwapi fuzzy match returns more false-positive mappings than current exact match.** *Mitigation:* Unit 8 (NER semantic post-filter) is sequenced *after* Unit 7 for this reason.
- **R7 — embeddinggemma-300m's +19.76 MTEB jump is a leaderboard artifact (contamination / task-specific tuning).** *Mitigation:* pre-flight check 7 (Unit 10 embedder candidate pre-verification) runs a held-out retrieval sanity check before committing to the swap.
- **R8 — Refactor takes longer than expected and blocks MVP launch indefinitely.** *Mitigation:* the MVP delay decision is recorded as still open. Each unit is independently shippable; if pressure to launch arises, ship Unit 1+2+9c (Finding G + cache + skip_reason tagging) as minimum-viable, defer Units 3+ to a 0.5.1.

---

## 7. Open decisions deferred to implementation time

- NER score formula (Unit 4): `log(1+count)` recommended, alternatives `freq_normalized` / `saturated`.
- Per-doc chunk boundary strategy (Unit 3a): `paragraph_aware` recommended, alternative `token_budget`.
- MMR λ for KeyBERT (Unit 3c): **default 0.6 confirmed** by pre-flight 2 A/B re-run; configurable via `KEYBERT_MMR_LAMBDA` if post-deploy tuning becomes necessary.
- MTEB embedder candidate (Unit 10): pending separate evaluation evening.
- KeyBERT verification gate pass thresholds (Unit 3, Gate 3.1): "5-20 topics per page" and "top-5 pass human spot-check" — sharpen at implementation time.
- `TOPIC_SCORE_THRESHOLD` value (Unit 3e): empirical, set after observing the post-KeyBERT score distribution. **Do NOT carry the old `TFIDF_TOPIC_THRESHOLD=0.15` forward** — that value was tuned against a degenerate TF-IDF (pre-flight 4 confirmed `CorpusDocuments` is empty) and has no empirical grounding. Retune from scratch.
- `TITLE_BOOST_MULTIPLIER` value (Unit 9a): default 1.5; pre-flight measurement may suggest 1.5–2.0.
- `VECTOR_SIMILARITY_THRESHOLD` re-baseline (Unit 10): empirical, set against the chosen embedder's cosine distribution.
- Whether SERP pages get topics at all (Unit 1): design decision, not just a bug.

---

## 8. Test plan additions

Current suite: 85 tests. Additions, by unit:

- **Unit 2:** WikiData term cache hit/miss, TTL expiry, empty-result caching.
- **Unit 3a (chunking):** chunk sizes respect `max_tokens` parameter; paragraph-aware boundary doesn't split mid-paragraph unless paragraph exceeds limit; sentence-fallback works.
- **Unit 3b (embedding pool):** `embed_document` on a long string returns a vector of correct dimension; pooled vector differs from first-chunk-only vector for long documents.
- **Unit 3c (KeyBERT):** custom vectorizer's analyzer returns a subset of the pre-computed noun_chunks set (test: every returned phrase appears in `{c.text.lower().strip() for c in nlp(text).noun_chunks}` after det/pronoun stripping); analyzer-closure pattern doesn't re-parse the document (test: mock-counter on the spaCy `nlp()` callable shows exactly one invocation per `extract_keywords` call); MMR with λ=0 returns ranked-by-similarity only; MMR with λ=1 returns max-diversity only.
- **Unit 4:** NER score formula produces expected ordering on a synthetic page with controlled mention counts.
- **Unit 5:** metadata harvesting on `tests/fixtures/metadata_harvest_fixture.html` picks up `og:type`, `og:title`, `og:description`, JSON-LD `schema:about` URIs (extraction itself is already shipped on 0.4 and out of scope for this test).
- **Unit 6:** post-extraction filter removes test phrases (stopwords, short, numeric, single-char).
- **Unit 7:** mwapi query shape; fallback to exact match when fuzzy returns empty.
- **Unit 8:** NER semantic post-filter resolves the known-bad cases (`"ai"`, `"gpt"`, `"First Monday"`).

Total target: ~120 tests post-refactor.

---

## 9. Documentation deltas

Files to update at the end of each unit (or batched per release):

- **CLAUDE.md:** BERTopic → KeyBERT swap; new `Topics.score` semantics (raw cosine, not MMR objective); new `Entities.score` semantics; new Webpage node properties from trafilatura; updated `embedding_model` invariant if Unit 10 selects a new model; updated `VECTOR_SIMILARITY_THRESHOLD` baseline if Unit 10 re-baselines; removed dependencies (`bertopic`, `umap-learn`, `hdbscan` if no longer pulled); Known Issues entries closed or updated; document the **shared single full-pipeline spaCy instance** used by both KeyBERT (Unit 3c, for noun_chunks) and NER (`text_ner_analysis.py`) — initialized once at NLP-manager startup, passed into both call sites.
- **MEMORY.md (auto-memory) and `project_mvp_revised_plan.md`:** mark shipped items as such; update the resume-here pointer when 0.5 is tagged.
- **architecture.md:** SQLite schema for the now-active `WikidataCache(entity, ...)` table; NLP pipeline step renumbering if BERTopic step removal renumbers the flow.
- **requirements.txt (core):** remove BERTopic/UMAP/HDBSCAN, add KeyBERT, add trafilatura.
- **README / launch docs:** unchanged unless an env var becomes user-facing.

### Env-var audit (consolidated)

Every new or renamed env var must land in ALL of: code that reads it, `CLAUDE.md` Environment Variables table, `compose.yaml` (if surfaced via compose), and `.env.example` (if surfaced to user-facing config). Track as a single checklist; treat each row as a single atomic commit.

| Env var | Unit | Default | Code reads it | CLAUDE.md row | compose.yaml | .env.example |
|---|---|---|---|---|---|---|
| `WIKIDATA_TERM_CACHE_TTL_DAYS` | 2 | 30 | `wikidata_lookup.py` | new row | unlikely | unlikely |
| `KEYBERT_MMR_LAMBDA` | 3c | 0.6 | `keybert_analysis.py` | new row | possibly | possibly |
| `TOPIC_SCORE_THRESHOLD` (rename of `TFIDF_TOPIC_THRESHOLD`) | 3e | empirical p50 | `connect_to_ontology.py` | update row + Session 2 reference | rename if present | rename if present |
| `NER_SCORE_FORMULA` | 4 | `log` | `coyote_nlp_state_manager.py` Step 19 replacement | new row | possibly | possibly |
| `TITLE_BOOST_MULTIPLIER` | 9a | 1.5 | `connect_to_ontology.py` (apply before threshold cut) | new row | possibly | possibly |
| `VECTOR_SIMILARITY_THRESHOLD` (re-baseline) | 10 | empirical for chosen model | `chains.py` | update row value | possibly | possibly |

### Committed test fixtures (consolidated)

Fixtures referenced across multiple gates. Commit under `tests/fixtures/`:

| Fixture | Used by | Purpose |
|---|---|---|
| `long_tutorial_article.txt` (~3000 words, tail-substantive) | Gate 3.2, Unit 10 regression | Verifies chunk-and-pool actually captures tail content |
| `ambiguous_terms_known_bad.json` | Gate 8.1 | Curated set: `ai → AI not Anguilla`, `gpt → GPT not GNU Portable Threads`, `First Monday → journal not date`, plus 5-10 pre-flight additions |
| `ambiguous_terms_known_good.json` | Gate 8.2 | Curated set of terms that should still map correctly after Unit 8 |
| `ner_synthetic_page.txt` (controlled mention counts) | Unit 4 verification gate | Verifies score formula produces expected ranking |
| `metadata_harvest_fixture.html` | Unit 5 test | Static HTML with og: tags, JSON-LD schema:about, dc:* metadata for harvesting test |

---

## 10. Out of scope (recap)

Already listed in 1.2. Restated here so a reader of this section alone sees them:
- Section-level chunk persistence (post-MVP retrieval feature).
- Wikidata embedding centroid filter.
- OpenTapioca.
- Phase B.5 (Purpose / SearchTerms embeddings).
- Hyperlink / Webpage / Annotation queue-update bug.
- Two-stage NER-context SPARQL disambiguation.
- LLM context input/output role-label preservation (Phase C v2).

---

**End of plan v1.**
