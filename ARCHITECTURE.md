# Coyote — Architecture Overview

A one-page map of how Coyote turns browsing into a semantic, queryable record. For contributor setup and conventions see [CONTRIBUTING.md](CONTRIBUTING.md); for where the project is headed see [VISION.md](VISION.md). `CLAUDE.md` holds the exhaustive reference and the running list of Known Issues.

## The governing principle

**LLMs verbalize; they do not compute the record.** The semantic record and every analysis over it — content extraction, embeddings, NLP enrichment, graph structure, similarity/divergence measures — stay **deterministic, explainable, and reproducible**. The local LLM is confined to the UI layer: it helps you query, summarize, and converse with your data, and never computes or mutates the stored record. This is what makes the data layer auditable and the analysis layer reproducible — the precondition for treating Coyote as a research instrument rather than a chatbot.

A second principle governs embeddings: **one coherent unit of content per vector.** Each embedding represents a single unit with one provenance and one `content_role` (a webpage's own text; an annotation's own prose). Content from a *different* node is never folded into another node's vector — cross-node relationships live in graph edges, recombined at query time by traversal, not by pre-embedding concatenation.

## Services

Four containers (Docker Compose) plus one standalone Flask app on the host:

| Service | Port | Role |
|---|---|---|
| **neo4j** | 7474 / 7687 | Graph DB (Neo4j 5.26, APOC restricted) |
| **ollama** | 11434 | Local LLM (`qwen2.5-coder:3b` by default) — UI layer only |
| **coyote_app** (Core) | 5000 | Flask API: event ingestion + the NLP pipeline + background managers |
| **bot** (Agent) | 8501 | Streamlit chat + GraphRAG retrieval |
| **ui_server** | 8080 | Flask dashboard that orchestrates the Docker services (runs on the host in a venv, *not* in Compose) |

## Data flow

```
Browser extension ─▶ SQLite staging ─▶ trafilatura content extraction
      (pages, links)                          │
                                              ▼
                    NLP enrichment (deterministic, no LLM):
                    pooled full-doc embedding · KeyBERT/RAKE topics · spaCy NER
                    · token-quality filter · WikiData concept linking
                                              │
                                              ▼
                              Neo4j graph  ──▶  GraphRAG
                        (Webpage/Annotation/           (Tier 0 vector +
                         Topic/WikiDataOntology)         Tier 1–3 Cypher)  ──▶ local LLM answer
```

- **Capture → staging.** The extension records the pages you visit and links you click; Hypothes.is annotations import via the UI. Events land in `EventStaging` (SQLite); a central `event_queue` drives downstream processing.
- **NLP enrichment.** For each content page: scrape → summarize → a pooled full-document embedding → KeyBERT topics → spaCy named entities → token-quality filter → WikiData concept linking. Every step is logged; failures write a status row rather than silently dropping data.
- **Write → Neo4j.** A background manager writes the graph and marks events `neo4j_done`; a separate manager attaches WikiData ancestor concepts (best-effort, subject to the WDQS rate limit).
- **Retrieve.** The Agent answers questions with a 3-tier GraphRAG strategy: parameterized Cypher (T1) → read-only LLM-generated Cypher (T2) → time-window fallback (T3), all schema-gated.

## Graph model

**Nodes:** `Webpage`, `Annotation`, `Purpose`, `SearchTerms`, `WikiDataOntology`
**Relationships:** `INITIATES_SEARCH`, `INITIATES`, `GENERATES_SERP`, `LINKS_TO`, `HAS_ANNOTATION`, `HAS_TOPIC`

Every embedded node carries `content_role` (`"input"` = consumed, e.g. a Webpage; `"output"` = produced, e.g. an Annotation), the `embedding_model`, the exact `embedding_text`, and a timestamp — the invariants that make embeddings comparable across time. Timestamps are ISO-8601 strings; wrap with Neo4j `datetime()` for comparisons.

> In the v0.5.0 MVP the in-browser search box is disabled, so `Purpose`/`SearchTerms` nodes are dormant (the schema still models them). Capture is pages + links + Hypothes.is annotations.

## Where to look

| Area | File |
|---|---|
| GraphRAG 3-tier logic | `images/agent/app/chains.py` |
| Core API + background managers | `images/core/core_analysis/coyote/coyote_server.py` |
| NLP pipeline | `images/core/core_analysis/coyote/analysis/nlp/` |
| WikiData linking / ontology | `images/core/core_analysis/coyote/analysis/wikidata_lookup.py`, `connect_to_ontology.py` |
| Cypher safety (blocklist, read-only) | `shared/nl2cypher.py` (canonical; duplicated into each image via `make sync-shared`) |
| SQLite schemas | `images/core/core_analysis/coyote/utils/initialize_databases.py` |
| Runnable demos | `images/core/core_analysis/coyote/demos/` (`source_inference.py`, `knowledge_shape.py`) |

## Security posture

Local-first (no cloud telemetry); read-only LLM Cypher enforced by a blocklist validator; APOC restricted to `apoc.meta.*` / `apoc.convert.*`; Neo4j and Hypothes.is credentials Fernet-encrypted at rest. The only outbound calls are the disclosed WikiData term lookups (individual concept terms, never your pages or history) and, if you use Hypothes.is, its own servers.
