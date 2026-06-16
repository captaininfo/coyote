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
   - **Status (2026-06-11): OPEN — never run.** The "unclear" status in session memory was confusion with the retirement of Gate 5.1 (an unrelated Unit 5 gate). Runs as Phase 0.1 of the Unit 3 execution sequence below; Gate 3.2's threshold cannot be set without it.
   - **Result (2026-06-11): PASS — pooling effect measurable on every multi-chunk page; contingency condition (all pages ~0.99) decisively not met.** Run in `coyote-core:local` (`--network host` required — the default Docker bridge has no outbound DNS on this host; `HF_HUB_OFFLINE=1` to skip HF freshness checks against the baked model). Method: live scrape via `scrape_webpage()`, `title + "\n\n" + text`, paragraph-aware chunking at `max_seq_length - 2 = 254` wordpiece tokens, normalize→mean→re-normalize, cosine(pooled, first-chunk-only):
     | page | profile | tokens | chunks | cosine |
     |---|---|---|---|---|
     | Wikipedia Heutagogy | encyclopedic | 4,851 | 22 | 0.7847 |
     | Wikipedia ICDE (pre-flight-1 sample) | short | 952 | 5 | 0.9023 |
     | Wikipedia Open education | encyclopedic-medium | 4,560 | 20 | 0.8784 |
     | jalammar.github.io Illustrated Transformer | **long-tutorial (fixture profile)** | 5,356 | 24 | **0.6626** |
     | Wikipedia Critical pedagogy | long-encyclopedic | 6,912 | 33 | 0.8365 |
     - **Gate 3.2 threshold: `cosine(pooled, first_chunk) < 0.90` on the committed fixture.** Resolution of the original phrasing ("just below the lowest observed value"), which was inverted as written: the gate asserts the pooled embedding *differs* from the first chunk, so the fixture must come in UNDER the threshold. 0.90 sits above the highest substantive-tail observation (0.8784, encyclopedic-medium) and far above the tutorial profile the fixture will match (0.6626), while staying clearly below the ~0.99 no-effect signature — margin in both directions, robust to Unit 10 re-runs under a different embedder.
     - Note: the news-inverted-pyramid sample was not obtainable (paywalls/bot-blocks per pre-flight 6); not needed — the threshold derives from substantive-tail pages, and the trivial single-chunk case (cosine = 1.0 by construction) covers the head-only control.
     - Chunk-count observation: 5–33 chunks across this sample; the `MAX_CHUNKS=100` cap (Unit 3b) implies ~25k tokens (~100 KB text) before truncation — comfortable.
     - Scratch artifact: `/tmp/preflight5_poc.py` (throwaway per plan; discarded, not committed).
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

### Unit 1 — Fix Finding G (RAKE-event zero-Topics bug)

**Plan item:** 1. **Estimated effort:** small (documentation only; underlying code fix likely already shipped via commit `21a610d`).

**Why first:** every subsequent unit's verification gate measures topic/edge counts. RAKE runs on search, hyperlink, and annotation event paths at `coyote_nlp_state_manager.py:355-356, :766, :933-934`; if RAKE was silently failing on those paths, zero SQLite Topics rows are produced for those events and zero HAS_TOPIC edges land on the resulting SearchTerms, Purpose, and Annotation nodes in Neo4j. Downstream gates that count topic/edge density across non-Webpage node types would be biased. The Webpage HAS_TOPIC path is BERTopic-driven (KeyBERT-driven after Unit 3) and is not affected by this gap; Unit 1's scope is the RAKE-driven non-Webpage paths and a separate by-design exemption decision for SERP Webpages.

**Work breakdown — two distinct threads:**

**Thread A — SearchTerms / Purpose / Annotation zero-HAS_TOPIC (original Finding G).** Justin's historical recollection (2026-06-02): the discovery was that SearchTerms nodes in Neo4j had no HAS_TOPIC edges; investigation question was RAKE failure vs NLP-pipeline failure. Likely root cause: `punkt` (and `stopwords`) nltk corpora not baked into the Core image, causing `rake_nltk` to raise `LookupError` on first invocation across all three RAKE event paths. The Dockerfile fix in commit `21a610d` (this branch, 2026-06-01) bakes both corpora into the image and likely closes the thread in-place. Spot-check on `coyote_event_data.db` (2026-06-02) confirmed zero Topics rows for all search and hyperlink event IDs. The 150 annotation `topic_context='annotation_text'` Topics rows present (2026-04-17, different image vintage) cannot serve as a RAKE control — annotations with `word_count >= 50` route to BERTopic instead of RAKE (`coyote_nlp_state_manager.py:915-934`), and the absence of any `highlighted_text` Topics rows confirms those 150 came through the BERTopic branch (the BERTopic branch writes only the `annotation_text` context; the RAKE branch writes both `annotation_text` and `highlighted_text`). Container is also contaminated by an ephemeral `nltk.download('punkt')` from pre-flight 2 (writable layer at `/root/nltk_data/`, mtimes 2026-05-28/29), preventing a clean test of current-image RAKE behavior. Verification requires post-rebuild fresh replay with the `21a610d` image and HEALTHY WDQS breakers. **No further code change planned under Unit 1 for Thread A.**

**Thread B — SERP Webpage zero-HAS_TOPIC (exempt by design).**
- **Design decision (resolved 2026-06-01):** SERPs exempt by design. Justin is also considering removing SERPs entirely as post-MVP work — OUT of scope here.
- **SERP tagging: use existing `isSERP` property — do NOT introduce `topic_skip_reason`.** `isSERP` (boolean) is set on every Webpage at `coyote_browser_extension_to_neo4j.py:446, :485` via the SERP detector at `:298`, and is already referenced in `nl2cypher.py:84` (worked Cypher example) and `:12` (LLM-facing schema string). Gate queries for Unit 1+ should use `WHERE w.isSERP = false` to exclude SERPs from topic-count denominators. Other zero-topic categories (non-SERP exempt URLs, empty-scrape, breaker-throttled) are covered by Unit 9c's `embedding_skip_reason` / `wikidata_skip_reason` — different problem, different scope. The convention split (`isSERP` boolean vs `embedding_skip_reason` string) is accepted as historical; renaming `isSERP` would touch the schema doc and worked-example Cypher and is not worth the cosmetic gain.
- **Known Issue dependency:** the `isSERP` detector is Google-only — non-Google SERPs (Bing, DuckDuckGo, etc.) get `isSERP = false` and contaminate the "true-no-topic" partition. Pre-existing tech debt in coyote-0.4; documented in CLAUDE.md Known Issues; out of scope here. Thread B's gate accuracy is bounded by this gap.
- **No code change planned under Unit 1 for Thread B.**

**Verification gate (documented expected distribution, measured on post-Unit-3 fresh replay):**

- **Thread A:** on a fresh browsing replay that exercises all three RAKE event types (search, hyperlink, annotation) under HEALTHY WDQS breakers, the SearchTerms / Purpose / Annotation node populations carry non-zero HAS_TOPIC edge density. **Prerequisite: `docker compose --profile core --profile llm --profile agent down` followed by `... up -d --build` to ensure the running image contains `21a610d` and the `/root/nltk_data/` writable layer from prior pre-flight downloads (2026-05-28/29) is wiped.** Pass threshold (provisional, to be revised against the first clean measurement): ≥80% of nodes of each type carry at least one HAS_TOPIC edge. Zero density on any of the three would indicate the `21a610d` Dockerfile fix did NOT resolve the underlying bug and further root-cause investigation is required.
- **Thread B:** documented expected distribution of Webpage nodes with zero HAS_TOPIC edges, partitioned as: (c) SERP-exempt-by-design (`isSERP = true`); (a+b+d) everything-else aggregated until Unit 9c's `embedding_skip_reason` lands and enables sub-partitioning into (a) exempt URLs, (b) empty-scrape, (d) true-no-topic. On the same fresh replay, observed distribution matches the documented expectation within tolerance. Thread B's MVP gate passes with the coarse (c) vs everything-else partition; the fine four-way partition becomes verifiable in Unit 9c.

Both thread gates run on the same post-Unit-3 fresh-replay substrate. Unit 1 closes with documentation only; no code changes land under Unit 1 itself.

---

### Unit 2 — Term→QID cache (`query_wikidata`)

**Status: CLOSED 2026-06-09** — shipped in three commits through `7cf0a43` (`wikidata_term_cache` table, TTL env var, janitor extension, tests). Text below is the historical spec; "85-test suite" was the count at gate time (suite is 90 post-Unit-2).

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

**Why bundled:** KeyBERT scores phrases against a document embedding. **Correction (2026-06-11, code-verified):** the persisted Webpage embedding (Step 20.5, `coyote_nlp_state_manager.py:682-688`) is NOT the raw document — it is the Phase B structured digest from `build_webpage_embedding_text()` (`Title / Summary / Entities / Topics`). Truncation is real but hits the digest's tail (Entities list overflow; Topics section dropped entirely on entity-rich pages), not "everything after the first paragraph." The raw document has never been embedded anywhere in the pipeline, so the embedding KeyBERT must score against does not yet exist. The bundle stands: 3b creates that embedding, 3c consumes it.

**Architecture decision (2026-06-11, Justin):** the pooled full-document embedding REPLACES the digest as the persisted Webpage node embedding. Rationale (roadmap-driven): digest embeddings entangle the vector space with the NLP pipeline version (Topics/Entities are pipeline outputs — the KeyBERT swap itself would move every Webpage embedding for unchanged content), poisoning longitudinal conceptual modeling and perspective divergence; content embeddings are a function of (content, embedder) only. Pooled doc vectors are the mean of chunk vectors, so post-MVP section-level chunk persistence becomes additive (same space) rather than a parallel system. Cross-corpus source inference compares user prose against source *content*, not extraction artifacts. Cost: Tier 0 was verified against digest embeddings — re-baselining `VECTOR_SIMILARITY_THRESHOLD` is therefore a closure-blocking gate (Gate 3.5 below), and mean-pool smearing on multi-topic long docs is accepted as transitional (chunk-level retrieval is its proper fix). `build_webpage_embedding_text()` is deleted; the Annotation digest builder stays (output corpus — Phase B.5 territory).

**Sub-units:**

**3a. Chunking module.**
- New module: `images/core/core_analysis/coyote/analysis/nlp/chunking.py`.
- API target: `chunk_text(text: str, max_tokens: Optional[int] = None, boundary: str = "paragraph_aware") -> List[str]`.
- `max_tokens` default: when None, derived from the active embedder's `max_seq_length` property at call time (sentence-transformers models expose this — e.g., `embedder._sentence_transformer.max_seq_length` or equivalent through the `coyote_embedder` module wrapper). **This couples chunk size to the active embedder automatically**, so Unit 10's model swap propagates to chunking without a separate config change. Explicit `max_tokens` overrides the default for callers that want a different size (e.g., post-MVP section-retrieval may want smaller chunks).
- `boundary="paragraph_aware"`: greedy paragraph split; if a paragraph exceeds `max_tokens`, fall back to sentence split. **Amended 2026-06-11:** sentence split via regex (same `_SENTENCE_RE` pattern as `summarize_text.py`), NOT the spaCy sentencizer originally specified — the chunking module must stay dependency-free so `coyote_embedder` (which holds no spaCy instance) can call it. A single sentence exceeding `max_tokens` hard-splits by token windows as last resort; never return empty chunks. Token counting via injected `count_tokens: Optional[Callable[[str], int]]` parameter (default `len(text.split())`; production passes the embedder's wordpiece counter — see 3b). Post-MVP section-retrieval can inject a smarter splitter.
- Designed so post-MVP section-retrieval feature can call the same module with different params (smaller `max_tokens`, different boundary strategy).
- Unit-tested in isolation (no embedding calls — pass `max_tokens` explicitly in tests).

**3b. Per-doc embedding chunk-and-pool fix.** (Amended 2026-06-11 per the architecture decision above.)
- Add `embed_document(text: str) -> Optional[list]` to `coyote_embedder.py`; the simple `embed_text` API remains for short text (search terms, etc.). Returns `None` on empty/failure, matching `embed_text` semantics.
- `embed_document`: chunk text (`max_tokens = model.max_seq_length - 2` for special-token margin; `count_tokens` = wordpiece count via `model.tokenizer(..., add_special_tokens=False)`) → batch-encode chunks → **L2-normalize each chunk → mean-pool → re-normalize**. Normalize-before-mean so high-norm chunks don't dominate the pool and Gate 3.4's distribution stays comparable to pre-flight 2's baseline. Guard every normalization with `norm = max(norm, 1e-12)` — a zero vector would otherwise produce NaN and poison the Neo4j vector index.
- `MAX_CHUNKS` safety cap (~100; log a warning when hit) to bound pathological pages. **Behavior when hit (2026-06-11 second pass):** pool the FIRST `MAX_CHUNKS` chunks (document-head bias — standard truncation convention); drop the tail. `embedding_text` must then be the truncated prefix actually pooled, NOT the full input — keeps the architectural invariant (`embedding_text` = exact string embedded) literally true and bounds the stored property on pathological pages.
- Add public `get_model()` accessor — 3c's KeyBERT instance must wrap the same SentenceTransformer singleton, never load a second copy.
- New Step 7.5 in the webpage path: `emb_text = title + "\n\n" + scraped_text` (title prepended for retrieval signal; bare `scraped_text` when no title); **`doc_embedding, embedded_text = embed_document_with_text(emb_text)`** (Phase 2 added this tuple variant; plain `embed_document` is for callers that don't persist, e.g., the annotation KeyBERT call). The embedding is passed to KeyBERT (Step 8) AND persisted at Step 20.5 — **`embedding_text` MUST be the tuple's `embedded_text`, never `emb_text` reconstructed independently**: the two differ exactly when `MAX_CHUNKS` truncates, which is the case the invariant exists for. `embedding_text` duplicating `scraped_text` on the node is accepted at MVP (local-first, data-expendable); it dissolves when chunk nodes carry their own text post-MVP.
- Annotation embedding (Step 10.5) unchanged — still `embed_text` of the annotation digest. **Breadcrumb for Phase B.5 (2026-06-11 second pass):** the post-Unit-3 annotation embedding persists the *digest*, not the *content* — the same architectural question just resolved for Webpages, same resolution path (Phase B.5). Side effect of 3c's annotation swap: long annotations make two model calls (pooled full-doc for KeyBERT, discarded; digest for Step 10.5, persisted). B.5 should consider persisting the content embedding instead of re-deriving it.

**3c. KeyBERT integration.**
- Replace `bertopic_analysis.py` and the BERTopic path in `coyote_nlp_state_manager.py` Step 13 with KeyBERT.
- New module: `images/core/core_analysis/coyote/analysis/nlp/keybert_analysis.py`.
- API target: `extract_keywords(text: str, doc_embedding: Optional[list], nlp: spacy.Language, top_n: int = 20, mmr_lambda: float = 0.6) -> List[Tuple[str, float]]`. The caller passes the pre-loaded spaCy instance — see single-instance paragraph below. **Type boundary (2026-06-11 second pass):** `embed_document` returns `Optional[list]` (persistence-friendly); `keybert_analysis` owns the ndarray conversion — `np.asarray(doc_embedding, dtype=np.float32).reshape(1, -1)` internally, since KeyBERT expects 2-D `(n_docs, dim)`.
- KeyBERT receives the pre-computed `doc_embedding` from 3b — does not re-embed. (KeyBERT supports passing precomputed doc embeddings.)
- Custom vectorizer using spaCy `noun_chunks` (Option E, per pre-flight 2 A/B re-run, 2026-06-01): pre-compute the noun-chunk set once per document from the shared spaCy parse (det/pronoun-stripped, alpha-token-only, length 1-4 tokens), then build a `CountVectorizer(analyzer=callable)` whose callable **closes over the pre-computed set** and returns it verbatim per document. **Do NOT call `nlp()` inside the analyzer** — that would re-parse the document on every `CountVectorizer.fit` invocation, defeating the single-spaCy-instance simplification below. **Use `analyzer=`, NOT `tokenizer=`, and NOT KeyBERT's `candidates=` parameter.** Rationale (verified by pre-flight 1 + pre-flight 2): `tokenizer=` would let sklearn's `_word_ngrams` re-form n-grams across the analyzer's output and break the noun-chunk boundary guarantee; KeyBERT 0.9.0's `candidates=` parameter silently re-tokenizes multi-word strings via default `token_pattern=r'(?u)\b\w\w+\b'`, dropping them from the vocabulary entirely (sub-risk recorded under R1).
- **Single spaCy instance shared with NER** (`text_ner_analysis.py`). noun_chunks requires the parser to be enabled — the same configuration the NER step needs. The KeyBERT candidate generator and the NER step share one fully-loaded `spacy.load("en_core_web_sm")` instance. **Mechanism: constructor injection, not module-level singleton.** The NLP state manager owns the instance (loaded once at manager startup), holds it as an attribute, and passes it explicitly as the `nlp` parameter to both `text_ner_analysis.extract_entities(...)` and `keybert_analysis.extract_keywords(...)`. No hidden module-level state; instance lifetime tracks the manager's lifetime. Memory cost ~50 MB for the full pipeline (vs ~30 MB for parser-disabled); net saving versus the two-instance approach the plan originally specified for Pekar. **Residual double-parse:** each document is still parsed twice per event (once inside NER, once inside KeyBERT's noun-chunk pre-computation). Eliminating this via a pre-parsed `Doc` parameter is a candidate post-MVP follow-up if profiling shows the second parse is a hotspot — out of scope for Unit 3c. Document the shared-instance lifecycle in CLAUDE.md.
- MMR enabled (λ starting per pre-flight check 2 result; configurable via `KEYBERT_MMR_LAMBDA` env var, default 0.6).
- Score is cosine similarity to doc embedding — replaces `tfidf_score`.
- **Raw-text constraint (added 2026-06-11):** KeyBERT/noun_chunks receives RAW `scraped_text` — the current Step 8 stopword-strip is deleted from the topic path. Stopword-stripped text destroys sentence grammar; the dependency parse (and therefore `noun_chunks`) would be garbage. Interim wrinkle: Steps 18–19 (entity TF-IDF, alive until Unit 4 per M9) still consume a stopword-stripped `processed_text` — that strip survives as a local computation on the entity path only, and the CorpusDocuments corpus fetch moves from Step 13 into that block.
- **KeyBERT model identity (added 2026-06-11):** `KeyBERT(model=coyote_embedder.get_model())`, built lazily once at module level. KeyBERT still embeds candidate phrases even with `doc_embeddings=` supplied, so it needs the model — but it must be the shared singleton. Model-load failure / empty candidate set (e.g., non-English pages) / `None` doc embedding → return `[]`, log at INFO. Provide `_reset_for_tests()` (same pattern as `_breaker_reset_for_tests`) — Phase 2/3 tests monkeypatch the model singleton, and a lazily-built module-level KeyBERT instance initialized under one test's stub would leak into later tests. `KEYBERT_MMR_LAMBDA` is read once with a try/except float fallback to 0.6 — a malformed env value must not take down the topic path.
- **Second production call site (added 2026-06-11; decision: Justin):** `analyze_topics` is also called for annotations with ≥50 words at `coyote_nlp_state_manager.py:920` — missed by the original Unit 3 scope; M10 would have broken annotation processing. Decision: 1:1 KeyBERT swap at that call site — `extract_keywords(full_text, embed_document(full_text), self.nlp, ...)` on RAW text (same constraint as above; `embed_document` handles >256-token annotations transparently). The ≥50-word routing split stays; the RAKE path for short annotations is untouched. `Topics.score` becomes uniformly cosine for everything KeyBERT touches.
- Respect `nlp.max_length` (truncate with a warning — same exposure `extract_entities` already has today; no regression).

**3d. Wiring and module migration.**

`Topics.score` semantics change: now cosine-similarity (range ~0.0-1.0). Update CLAUDE.md and any consumer that filters on the old TF-IDF range.

The migration is order-sensitive. Execute as an ordered checklist; do not skip ahead.

- [ ] **M1. Create new module** `images/core/core_analysis/coyote/analysis/wikidata_lookup.py` (core-only — NOT in `shared/`, since the agent does not need WikiData lookup and adding to `shared/` would force agent-side sync).
- [ ] **M2. Move `query_wikidata`** into `wikidata_lookup.py`. **Scope expanded 2026-06-11 (the original `:157-219` line range was stale and far too narrow):** the function cannot move alone. Moving with it: the **entire circuit-breaker block** (state globals, lock, all `_breaker_*` helpers, `_parse_retry_after`, `_breaker_reset_for_tests`), `_escape_sparql_literal`, `_INVISIBLE_CHARS`, and the Unit 2 cache layer (`_cache_lookup`, `_cache_store`, `WIKIDATA_TERM_CACHE_TTL_DAYS`, `_CACHE_STATS_LOCK`, hit/miss counters).
- [ ] **M3. Move `map_topics_to_wikidata`** (`text_bertopic_analysis.py:234-257`) into `wikidata_lookup.py`.
- [ ] **M4. Update imports in `coyote_nlp_state_manager.py`:** change `from text_bertopic_analysis import ...` for the two moved functions to `from coyote.analysis.wikidata_lookup import ...`.
- [ ] **M5. Update imports in `text_ner_analysis.py`:** `map_ner_to_wikidata` calls `query_wikidata` internally. Update its import to point at `wikidata_lookup`. **Two names, not one (2026-06-11):** the same import statement (`text_ner_analysis.py:16-19`) also pulls `_INVISIBLE_CHARS`.
- [ ] **M6. Clean up `text_ner_analysis.py` internally.** **Scope expanded 2026-06-11 (code-verified):** it is not just the duplicate `calculate_tfidf_on_phrases` at line 119 — the entire chain `calculate_tfidf_on_phrases` → `map_tfidf_to_wikidata` → `combine_nlp_results` → `get_ner_from_text` (lines ~119-278, more than half the file) has zero external callers. Also dead: the `from coyote.analysis.nlp.ner import extract_entities` at line 15 (immediately **shadowed** by the local redefinition at line 45 — but the import still triggers `ner.py`'s module-level `spacy.load()`, a third ~50 MB spaCy instance doing nothing), and the module-level stopwords block (only consumer was the dead TF-IDF copy). Delete all of it.
- [ ] **M7. Confirm `text_ner_analysis.py` still passes its module-level smoke test** after M5 + M6. The file survives the refactor; it should be functional with cleaner internals.
- [ ] **M8. Remove `get_topic_from_text`** from `text_bertopic_analysis.py`. Verified 2026-05-27: zero callers anywhere in the codebase (`grep -rn "get_topic_from_text" --include="*.py"` returns only the definition). Pure dead code; delete with no migration.
- [ ] **M9. Mark `calculate_tfidf_on_phrases` as transitional dead-after-Unit-4.** Do NOT delete it yet. After M8, the topics call site at `coyote_nlp_state_manager.py:610` is replaced by Unit 3's KeyBERT path, but the entities call site at `:665` still calls this function until Unit 4 lands. Leave the function in place; add a `# DEPRECATED: remove after Unit 4 lands` comment. Final deletion happens in Unit 4's cleanup (see Unit 4 work breakdown). This preserves a buildable state between Unit 3 and Unit 4 completing.
- [ ] **M10. Delete `bertopic_analysis.py`** (the BERTopic wrapper module).
- [ ] **M11. Move surviving constants out; `text_bertopic_analysis.py` becomes a rump.** **Amended 2026-06-11 — the original "delete entirely" contradicted M9** (which keeps `calculate_tfidf_on_phrases` alive until Unit 4; the function lives in this file). Resolution: create `images/core/core_analysis/coyote/analysis/nlp/stopwords.py` exporting `CUSTOM_STOPWORDS` and `STOP_WORDS` (nltk ∪ custom) — note the custom list is currently duplicated in THREE files (`text_bertopic_analysis.py:144`, `text_ner_analysis.py:31`, `coyote_nlp_state_manager.py:51`); all three consumers switch to importing it. `text_bertopic_analysis.py` then survives as a rump containing ONLY `calculate_tfidf_on_phrases` (+ stopwords import) with a `# DEPRECATED: delete file after Unit 4` docstring; also delete its now-unused module-level `spacy.load()`. Unit 4's cleanup deletes the file.
- [ ] **M12. Update `requirements.txt`** (`images/core/core_analysis/requirements.txt` — the authoritative file; see note below): add `keybert==0.9.0` (lands with the 3c commit so the container builds); remove `bertopic` (UMAP/HDBSCAN are its transitive deps, not pinned lines — verify keybert pulls neither). **Ride-alongs (2026-06-11):** (a) remove `bert-extractive-summarizer==0.10.1` — dead weight since the 0.4 session replaced it with the stdlib lead-sentence extractor; zero imports anywhere (verified); (b) delete the orphan `images/core/requirements.txt` and close its CLAUDE.md Known Issue — **confirmed orphan 2026-06-11:** `compose.yaml:65-66` builds with `context: ../images/core/core_analysis` + `dockerfile: ../Dockerfile`, and the Dockerfile's `COPY requirements.txt` resolves against the *context*, i.e. `core_analysis/requirements.txt`. (`images/core/Dockerfile` itself IS load-bearing — only the requirements file there is dead.) Do not add torch to requirements (Dockerfile installs CPU-only torch separately).
- [ ] **M13. `make sync-shared`** runs (if applicable — `wikidata_lookup.py` is core-only and NOT under `shared/`, so sync test should not require new entries; verify the sync test still passes).
- [ ] **M14. Update BOTH breaker/cache test files in the same commit as M2** (added 2026-06-11): `tests/test_wikidata_term_cache.py` AND `tests/test_wikidata_breaker.py` both do `from coyote.analysis.nlp import text_bertopic_analysis as target` and call `target._breaker_reset_for_tests()` / other `target.*` internals — both retarget to `wikidata_lookup`. **Prune the stub preambles while there:** both files stub `spacy` / `nltk` / `sklearn` / `bertopic_analysis` in `sys.modules` before import because `text_bertopic_analysis` loads them at module level; `wikidata_lookup.py` imports none of these. Keep only the `SPARQLWrapper` stub. Stale `sys.modules.setdefault` stubs would mask real import regressions. Update the module docstrings that name the old module.
- [ ] **M15. Delete orphan modules `ner.py` and `tfidf_analysis.py`** (added 2026-06-11, code-verified): `ner.py`'s sole importer is the shadowed `text_ner_analysis.py:15` import removed in M6 (`nlp/__init__.py` is empty — no re-exports; no test imports); `tfidf_analysis.py` has zero importers. Delete both in the deletion commit, after M6 lands.
- [ ] **M16. State-manager import hygiene rides with the wiring commit, not the deletion commit** (added 2026-06-11): when the wiring commit removes the Step 12 call (`coyote_nlp_state_manager.py:602`) and both `analyze_topics` call sites (`:561`, `:920`), the `extract_and_replace_topics` import (`:35`) and the `analyze_topics` import (`:37`) must be removed in that same commit. (Leaving them wouldn't break the build — the functions still exist until M8/M10 — but the deletion commit would then break them, and unused imports between commits are avoidable noise.) The `calculate_tfidf_on_phrases` import (`:34`) stays until Unit 4 per M9.

**3e. Connect to ontology (`connect_to_ontology.py`) — env var rename, atomic across all touch points.**
- The threshold-filter at the URI-loop entry uses `tfidf_score`. After this unit, score semantics differ. Re-baseline the threshold around p50 of the new score distribution, measured empirically.
- **`TFIDF_TOPIC_THRESHOLD` → `TOPIC_SCORE_THRESHOLD` rename — atomic across:**
  - `connect_to_ontology.py` (code reference)
  - `CLAUDE.md` (Environment Variables table + Session 2 reference)
  - `compose.yaml` (if referenced)
  - `.env.example` (if referenced)
  - Any test that references the old name
  Treat the rename as a single commit; do NOT ship the code change in one commit and docs in another, since the running pipeline will read whatever env var is set in the deploy environment.
- **Edge property rename rides in the same atomic commit (added 2026-06-11):** the HAS_TOPIC relationship property is literally named `tfidf_score` (`connect_to_ontology.py:534`). After this unit the value is cosine similarity — rename to `topic_score`. Grep-verified: no consumers outside core (`chains.py`, `nl2cypher.py`, UI all clean); data-expendable policy makes the graph-side rename free. Update CLAUDE.md Known Issues entries that reference HAS_TOPIC `tfidf_score`.
- **MERGE restructure rides in the same atomic commit (decision: Justin, 2026-06-11 second pass):** the current pattern puts `timestamp` and the score INSIDE the relationship merge key (`MERGE (n)-[rel:HAS_TOPIC {timestamp: $timestamp, tfidf_score: $score}]->(wdo)` at `connect_to_ontology.py:532-535`), making each edge unique per (node, topic, timestamp, score) tuple. Webpage nodes are MERGEd by URL, so revisiting a URL re-runs NLP with a new timestamp and stacks a duplicate HAS_TOPIC edge onto the same topic — inflating every edge-density gate and guaranteeing Unit 9b's per-node edge-cap gate fails on any twice-visited page. Restructure to `MERGE (n)-[rel:HAS_TOPIC]->(wdo) SET rel.topic_score = $score, rel.timestamp = $timestamp` — one edge per (page, topic), last-write-wins, idempotent on reprocessing. Not a security issue (all values are bound `$params`; `relationship_type` is allowlist-validated at `:512`). **Accepted tech debt (Justin, 2026-06-11 — document in CLAUDE.md Known Issues at Phase 8):** last-write-wins discards revisit history. In a longitudinal personal learning record, previous visits to the same URL may be meaningful in their own right, and a dynamic page's content can differ per visit — same URL does not imply same consumed content. The proper post-MVP fix is per-visit modeling (session IDs / visit nodes — kin to the session-ID Known Issue), not multi-edge MERGE keys.
- **Ride-along (from Unit 2 session):** fix the hardcoded `Path('data/wikidata_cache.db')` at `connect_to_ontology.py:560` → import `WIKIDATA_CACHE_DB_FILE` from `config_container` (canonical, not the `config_manager` re-export).
- **Provisional ship value (2026-06-11):** `TOPIC_SCORE_THRESHOLD=0.10` — a floor that drops pre-flight 2's observed small-negative/noise tail (distribution median ~0.18) without cutting into the meaty middle. Gate 3.4 retunes empirically post-deploy. Do NOT carry 0.15 (pre-flight 4: no empirical grounding).

**Verification gates:**
- **Gate 3.1 (functional):** on a sample of 20 representative pages, KeyBERT produces 5-20 topics each. **Phrase-quality criteria** (replaces the cross-sentence-ngram criterion from the Pekar-era plan — structurally impossible to violate with noun_chunks, since dependency parses are per-sentence): top-5 phrases per page are recognizable noun phrases — no verb-led fragments (e.g., "vibe coding might come"-style), no POS-soup (e.g., "think tanks governmental"-style), no bound-morpheme unigrams (e.g., bare "pre" / "anti"). **Wikidata-searchability criterion (from pre-flight 2 A/B):** ≥12/20 pages (60%, proportionally equivalent to pre-flight 2's ≥3/5 threshold) have at least one top-5 phrase recognizable as a Wikidata-searchable concept. Spot-check by human reviewer at gate-run time.
- **Gate 3.2 (truncation fix):** committed test fixture at `tests/fixtures/long_tutorial_article.txt` (~3000 words, content that develops substantively across many paragraphs — choose a tutorial / technical explainer / long-form argument, NOT a news article with inverted-pyramid structure where the head already covers the content). Gate threshold is **set empirically from pre-flight check 5** (chunk-and-pool similarity distribution measurement), NOT hardcoded. Threshold sits just below the lowest cosine-similarity observed on substantive-tail-content pages during pre-flight, so substantive-tail content reliably passes the gate. Fixture committed so Unit 10 can re-run the regression with the same threshold.
- **Gate 3.3 (no regressions):** full test suite green (90 at Unit 3 start); new KeyBERT and chunking unit tests pass; Neo4j vector indexes still ONLINE.
- **Gate 3.4 (score sanity):** baseline `Topics.score` distribution set empirically from pre-flight 2 results: median ~0.18, IQR ~0.16, max ~0.77 across the 5 sample pages at λ=0.6 with noun_chunks candidates (n=100). **`Topics.score` is raw cosine similarity between candidate-phrase embedding and pooled document embedding — NOT the MMR objective.** Verified by reading `keybert._mmr.mmr` source (2026-05-28): MMR's diversity-penalized objective is used only for `np.argmax` selection within the loop; the score stored alongside each selected keyword is the raw `word_doc_similarity[idx]`. Gate (post-filter, what reaches Neo4j): persisted `Topics.score` values are in (0, 1]; distribution is non-degenerate (sample IQR meaningfully non-zero). Pre-filter raw KeyBERT output may include small negatives (~-0.05 observed in pre-flight 2) — these are mild anti-correlation in MiniLM cosine space, not MMR penalty artifacts; `TOPIC_SCORE_THRESHOLD` (Unit 3e, retuned per Unit 3 deploy) filters them before persistence.
- **Gate 3.5 (Tier 0 re-baseline — added 2026-06-11, closure-blocking):** because the persisted Webpage embedding semantics change (digest → pooled full-doc), `VECTOR_SIMILARITY_THRESHOLD` (current 0.65, tuned against digest embeddings) MUST be re-baselined before Unit 3 is declared closed — this is a prerequisite, not an optional post-deploy task. Procedure: on the post-wipe fresh replay (same substrate as Gates 3.1/Unit 1), run ~10 representative NL queries through Tier 0, observe top-K cosine values, set the threshold at the distribution elbow; spot-check that retrieval results are sensible. Lands as: env default update (compose/.env.example if present) + CLAUDE.md Environment Variables row. This subsumes R3's mitigation. **Sampling must include annotation-targeting queries (2026-06-11 second pass):** `chains.py:358` applies ONE threshold to both the `webpage_embedding` and `annotation_embedding` indexes, and Unit 3 shifts only the webpage distribution (annotation embeddings stay digest-based). If the two distributions' elbows diverge badly, that is evidence for accelerating Phase B.5 — not for reverting the webpage change. **Decision criterion (2026-06-11, third pass):** the single threshold is set from the WEBPAGE elbow regardless — webpages are the primary Tier 0 corpus and the one whose semantics changed. "Diverge badly" = elbows differ by more than 0.10: record the annotation elbow alongside the chosen value and accept the annotation-side consequence (over-retrieval if the annotation elbow is higher — bounded by top-K ranking; under-retrieval if lower) as the lesser harm versus mis-tuning webpage retrieval. Divergence >0.10 is the trigger for prioritizing Phase B.5; a per-index threshold split is the B.5-era fix, not a Unit 3 deliverable.

**Unit 3 execution sequence (v2, 2026-06-11) — commit-sized phases, tree green after each:**

- **Phase 0 — pre-work, no production code.** 0.1: run pre-flight 5 (in-container throwaway POC; sets Gate 3.2's threshold; record results in §2). 0.2: this plan amendment (done).
- **Phase 1 — `chunking.py` + pure unit tests** (3a). No model, no spaCy.
- **Phase 2 — `embed_document()` + `get_model()` + tests** (3b). Unit tests monkeypatch the model singleton with a deterministic stub; the Gate 3.2 fixture test (`tests/fixtures/long_tutorial_article.txt`, real model) ships skip-marked on model availability, runnable in-container.
- **Phase 3 — `keybert_analysis.py` + tests + `keybert==0.9.0` requirements add** (3c). Model-dependent tests use `importorskip`/skip markers so the host suite stays green.
- **Phase 4 — migration M1–M7 + M14, pure refactor, zero behavior change.** Includes the expanded M2 scope, M5's two-name import fix, M6's expanded dead-code cut, and `stopwords.py` creation (M11's first half, pulled forward so all three duplicate stopword lists consolidate here). Full suite green before proceeding.
- **Phase 5 — wiring, the behavior change.** Constructor injection (`self.nlp` loaded once in `CoyoteNLPStateManager.__init__`; `extract_entities(text, nlp)` gains the parameter and loses its module-level load; **six call sites across the four event paths** pass `self.nlp` — grep-verified 2026-06-11: search `:361` + `:362` (purpose, search_terms), webpage `:624`, hyperlink `:770`, annotation `:943` + `:944`. The seventh call inside `get_ner_from_text` (`text_ner_analysis.py:229`) is dead code already deleted by M6 in Phase 4 — do not count it). Webpage path: new Step 7.5 (pooled embedding), Step 8 → KeyBERT on raw text, Step 9 writes cosine scores directly (the Step 13 second-pass score UPDATE disappears), Steps 12–13 deleted, Steps 18–19 survive with a local `processed_text` strip + relocated corpus fetch, Step 20.5 persists the Step 7.5 embedding AND its `embedded_text` (tuple from `embed_document_with_text` — never reconstruct `embedding_text` independently; see 3b). Annotation path: `:920` → KeyBERT per 3c, **and the `:919` stopword-strip line is deleted** (KeyBERT receives raw `full_text`). M16 import hygiene rides here.
- **Phase 6 — deletions.** M8, M10, M11 rump conversion, M15 (`ner.py`, `tfidf_analysis.py`), `build_webpage_embedding_text()`, M12 requirements ride-alongs. Container rebuild + smoke test.
- **Phase 7 — 3e atomic rename commit** (env var + edge property + MERGE restructure to last-write-wins + `:560` ride-along + provisional 0.10 default).
- **Phase 8 — docs, deploy, gates.** CLAUDE.md deltas (incl. every stale `text_bertopic_analysis.query_wikidata` module-path reference in the env-var table; the residual double-parse documented as a known inefficiency so a future session doesn't "fix" it by re-parsing inside the analyzer; Webpage `embedding_text` invariant change; new Known Issues entry for the HAS_TOPIC last-write-wins revisit-history debt per 3e). **CLAUDE.md deltas committed 2026-06-12** (BERTopic→KeyBERT, module-path fixes, two Known Issues closed, three new entries, Phase-B-digest-superseded note); the breaker code comment at `connect_to_ontology.py:38` retargeted to `wikidata_lookup.py`. Then volume wipe per §3, fresh replay, then Gates 3.1–3.5 AND the Unit 1 thread gates on the same replay substrate.
  - **Gate partitioning under WDQS throttling (2026-06-12):** Gates 3.1 (topic quality), 3.2 (truncation fixture), 3.3 (no-regression), 3.4 (`Topics.score` distribution), and 3.5 (Tier-0 re-baseline, closure-blocking) all measure the KeyBERT/embedding layer, which the Phase-5 pipeline writes at Steps 7.5–9 **before** any WikiData call (Step 10). They are therefore WDQS-independent and can run even while the breaker is OPEN. Only the **Unit 1 thread gates** (HAS_TOPIC edge density on SearchTerms/Purpose/Annotation) need the WikiData→ontology mapping and must be deferred when WDQS is throttling.
  - **Operational trigger for the deferred Unit 1 thread gates (Sonnet, 2026-06-12):** run them when ALL of: (a) the WikiData breaker has been **closed for ≥30 min**, (b) a **fresh ≥20-page browsing replay** has completed, and (c) the Gate A query (`MATCH (w:Webpage)-[r:HAS_TOPIC]->() WHERE datetime(w.timestamp) > datetime() - duration({hours: 2}) WITH w, count(r) AS edges_per_page RETURN avg(edges_per_page)`) returns a **nonzero** `avg_edges_per_page`. Until all three hold, the Unit 1 gates are not measurable and Unit 3 closes on Gates 3.1–3.5 alone.

Estimated effort (calibrated up per Unit 2 lesson): ~8 commits, 3–5 working sessions; Phases 4 and 5 are each session-sized alone. Test count target ~105–110.

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
- **Scope note (2026-06-11 second pass):** annotation-path Entities rows are unscored on coyote-0.4 — inserted with `score=NULL` (`coyote_nlp_state_manager.py:976-978`) and the annotation path contains no scoring step at all. Unit 3 does not change this; do not misread NULL annotation entity scores as a refactor regression. Whether mention-frequency scoring extends to the annotation path is a post-MVP decision.
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
- Full test suite green.
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
- **Top-level Gate 5 — No security regressions.** `is_read_only()` blocklist unchanged; sync test still passes; full test suite green.

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
- `TOPIC_SCORE_THRESHOLD` value (Unit 3e): empirical, set after observing the post-KeyBERT score distribution. **Do NOT carry the old `TFIDF_TOPIC_THRESHOLD=0.15` forward** — that value was tuned against a degenerate TF-IDF (pre-flight 4 confirmed `CorpusDocuments` is empty) and has no empirical grounding. Retune from scratch. **Provisional ship value 0.10 (2026-06-11)** — noise-tail floor per pre-flight 2 distribution; Gate 3.4 retunes.
- `TITLE_BOOST_MULTIPLIER` value (Unit 9a): default 1.5; pre-flight measurement may suggest 1.5–2.0.
- `VECTOR_SIMILARITY_THRESHOLD` re-baseline (Unit 3 Gate 3.5, re-run at Unit 10): empirical — Gate 3.5 sets it against pooled-full-doc embeddings (webpage elbow; see Gate 3.5 decision criterion); Unit 10 re-baselines against the chosen embedder's distribution.
- Whether SERP pages get topics at all (Unit 1): design decision, not just a bug.

---

## 8. Test plan additions

Current suite: 90 tests (post-Unit-2; "85" elsewhere in this doc is the pre-Unit-2 count, retained only in closed-unit text). Additions, by unit:

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
| `VECTOR_SIMILARITY_THRESHOLD` (re-baseline) | 3 (Gate 3.5), re-run at 10 | empirical per Gate 3.5; again for chosen model | `chains.py` | update row value | possibly | possibly |

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
