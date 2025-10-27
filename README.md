# Coyote  🐾  
*A local, privacy-first learning record & analytics engine for your local AI agents.  

**TL;DR** – Coyote quietly logs your web-search activity (purpose, searches, pages, hyperlinks, annotations), locally analyzes information you consume and create, and writes a queryable graph in Neo4j. You can explore your graph visually, ask questions with a local LLM (GraphRAG), and get simple, auditable “Learning Insights.” Everything runs on your machine; no cloud; you stay in control.
Coyote treats your mind like a classic **“black box”**: study the inputs (what you sought and read) and the outputs (what you highlighted, wrote, and linked), then use transparent NLP to map both to real‑world concepts. This creates a private “learning ledger” you can inspect, query, and build on. The goal isn’t a robot teacher; it’s a **personal learning analytics toolkit** that helps you see patterns, systematically research your online behavior, and/or feed your preferred local LLM/agent with high‑quality, on‑device context.

---

## 1  Why Coyote?
- **Private context for local agents.** Your graph (purpose → search → SERPs → pages → annotations → topics/entities) lives on device and can safely power local LLM workflows (Ollama).
- **Auditable pipeline.** Each step is inspectable: staging DB → event DB → NLP → Neo4j → optional ontology linking → insights. Failures are captured with status tables and logs.
- **Heutagogy in practice.** It models how you learn: intentions, trails, evidence (annotations) and the aboutness of pages via topics/entities linked to Wikidata.


## What's included (MVP)
- **One Compose project, multiple profiles.** core (Neo4j + Coyote Core), llm (Ollama), and agent (Streamlit chat bot). Health checks + `restart: unless-stopped`.
- **Double-click UI.** A lightweight Flask UI starts/stops services (via docker compose), generates a secure `.env` on first run, shows container health, and exposes “Insights”, “Explore Visually”, and “Chat Assistant”.
- **Browser extension capture.** Purpose + search terms, SERPs, visited pages, and hyperlink clicks are staged and moved through the pipeline automatically.
- **Hypothes.is importer (optional).** One-click fetch from the UI (token stored encrypted).
- **NLP pipeline (auditable).** Scraper → summarizer → topic extraction (BERTopic → TF-IDF fallback) → NER → Wikidata linking. Stored to SQLite with explicit contexts (purpose, search_terms, webpage, annotation_text, highlighted_text).
- **Graph writing + guards.** Data lands in Neo4j via a background manager; NL→Cypher is schema-gated read-only for safety.
- **Ontology linking (optional, automated).** Caches Wikidata lookups and attaches parent concepts (e.g., subclass of, instance of).
- **Learning Insights.** Built-in endpoints power “New Topics”, “Sensemaking Rate” (SERP→annotation conversion), and “Learning Rhythms” visualizations.


## Requirements
- **OS:** Windows 10/11 (WSL2), macOS 12+, or Linux x86_64/aarch64
- **Software:** 
    - **Windows/macOS:** Install [Docker Desktop](https://docs.docker.com/desktop/); Install [Firefox](https://www.firefox.com/en-US/) web browser. 
    - **Linux (Ubuntu/Debian/Fedora/etc.):** You can either install [Docker Desktop](https://docs.docker.com/desktop/), or install [Docker Engine](https://docs.docker.com/engine/install/) plus the [Docker Compose v2 plugin](https://docs.docker.com/compose/install/linux/); Install Mozilla's [Firefox](https://www.firefox.com/en-US/) web browser. 
- **Hardware:** 8 GB RAM for Core; 16 GB recommended if running LLM + Agent
- **Disk:** ~8–15 GB for images + Neo4j + optional model cache


## Quick Start (no terminal required)
1. **Ensure requirements are met:** Is [Docker Desktop](https://docs.docker.com/desktop/) (or for Linux users, [Docker Engine](https://docs.docker.com/engine/install/) + [Docker Compose v2 plugin](https://docs.docker.com/compose/install/linux/)) installed on your computer? Is the Mozilla [Firefox](https://www.firefox.com/en-US/) web browser installed?  
2. **Download `coyote-download-0.4.0.zip`:** The downloadable [Coyote package](https://github.com/captaininfo/coyote/releases/tag/v0.4.0-beta.1) can be found in the Resources section of [Coyote's GitHub repository](https://github.com/captaininfo/coyote).
2. **Unpack Coyote and make launch files executable:** Unzip **Coyote_0.4** in a folder on your computer where you have "write" permissions, e.g., Documents. Linux & macOS users must make the following files executable: `start_coyote_mac_linux.sh`, `Start Coyote on Linux.desktop`, and `Start Coyote on Mac.command`. You can make files executable by right-clicking them in your computer's UI, or you can run the following files in terminal or console: 
```
chmod +x 'launch/start_coyote_mac_linux.sh'
chmod +x 'launch/Start Coyote on Linux.desktop'
chmod +x 'launch/Start Coyote on Mac.command'

```
3. Double-click your OS launcher to open the UI at `http://localhost:8080`. First run auto-creates `compose/.env` (chmod 600) with sensible defaults and a strong Neo4j password.
4. **Start services** (UI → System Status)
    - **Start Core Services** (Neo4j + Coyote Core): Your browsing data will be recorded when both the browser extension and Core Services are running. 
    - (Optional) **Start LLM Service** (Ollama)
    - (Optional) **Start All Services** (adds the Agent/Bot): The UI invokes docker compose with appropriate profiles and `--pull=missing`.
5. **Load the Firefox extension (temporary add-on):** In your Firefox browser, navigate to `about:debugging#/runtime/this-firefox`, then click **Load Temporary Add-on… →**. Locate `manifest.json` from `coyote-download-0.4.0/extension/manifest.json`, then select it. When the browser extension is connected to the Coyote UI, the UI's "System Status" tab will show “Browser Extension: Online”.
6. (Optional) **Configure Neo4j creds** (UI → Configure): Coyote automatically generates a password for Neo4j and saves it in the `.env` file. However, if you want to create your own unique Neo4j credentials, use the Coyote UI's "Configure" tab. Enter your own credentials, click **Test Connection** (HTTP `/db/neo4j/tx/commit`), and then **Save** to persist credentials to `.env` and into Core’s encrypted store.
7. (Optional) **Connect Hypothes.is** (UI → Integrations): **Test** token **→ Save → Fetch Data**. The UI calls a Core worker that paginates with `search_after`. Tokens are stored encrypted in Core’s state DB.

### Default ports
UI **8080**, Core API **5000**, Neo4j **7474.7687**, Ollama **11434**, Agent **8501** (editable via `compose/.env`)

## Explore & Chat
- **Explore Visually.** Run canned or NL→Cypher queries; server enforces read-only queries, schema gating, and a required `nodes/rels` return shape for visualization.
- **Chat Assistant (GraphRAG).** Streamlit UI with hybrid strategy (topic/term GraphRAG → analytical NL→Cypher fallback → time-window fallback). Uses the same prompt/schema helpers as Explore to stay safe.

## Data Flow (what happens under the hood)
1. **Capture → Staging (SQLite).** Events from the extension and Hypothes.is land in `EventStaging`. A centralized `event_queue` in `coyote_state.db` drives processing.
2. **Ingest → Event DB**. Core copies staged rows into normalized tables (`Events`, `WebpageLoads`, `HyperlinkClicks`, `Annotations`, `Topics`, `Entities`, `EventTracking`).
3. **NLP (auditable).** Scrape, summarize, topics (BERTopic + TF-IDF), NER, Wikidata mapping; TF-IDF scores are persisted per context; failures mark status rows.
4. **Write → Neo4j.** A background manager reads “nlp_processed” events, writes nodes/relationships (Purpose→SearchTerms, Webpage, Annotation), and marks “neo4j_done”.
5. **Ontology linking (optional).** Another manager attaches Wikidata ontology parents with caching and depth limits; when finished marks “ontology_processed”.
6. **Janitor.** Periodic cleanup removes terminal events across DBs and prunes stale cache entries.

**Exemptions.** Coyote deliberately avoids NLP on non-content pages (Google SERPs, Hypothes.is account pages, local configure screen).

## Learning Insights (MVP, UI-backed)
- **New Topics (last N days).** First-seen topics across Webpages/Annotations.
- **Sensemaking Rate.** #SERPs vs #Annotations within a window after the SERP timestamp.
- **Learning Rhythms.** Hour-of-day activity (active seconds if present, else interaction counts).

## Privacy & Security
- **Local-first.** All DBs and logs live under `compose/volumes` (mounted to `/app/data` in containers).
- **Secrets at rest.** Sensitive values (Neo4j password, Hypothes.is token) are stored encrypted in Core’s state DB using a per-install key (Fernet).
- **Your control.** You can stop services from the UI at any time, inspect/delete local databases, or work in a separate browser profile if you want to keep activities out of Coyote.

## Install from source (contributors)
- **Core:** `python -m coyote.coyote_server` (see `requirements.txt` in your repo). Background managers start automatically.
- **UI:** `python ui/coyote_ui_server.py` (serves the dashboard at `:8080`).

## Manual CLI (optional fallback)
```
# macOS/Linux
cd Coyote_0.4/compose
COMPOSE_PROFILES=core docker compose -p coyote -f compose.yaml up -d --pull=missing
COMPOSE_PROFILES=agent,llm docker compose -p coyote -f compose.yaml up -d --pull=missing
docker compose -p coyote -f compose.yaml ps --format json
docker compose -p coyote -f compose.yaml down

```
These match the UI’s Start/Stop buttons and profiles.

## Troubleshooting
- **Neo4j “Unauthorized.”** Use **Configure → Test Connection → Save**, then **Restart Services** in UI.
- **Containers won’t stop.** Use **Force Cleanup** in UI (falls back to `docker stop/kill/rm` under the hood).
- **Where are logs?**
    - Core server: `/app/data/logs/coyote_server.log` (host: `compose/volumes/coyote/logs/…`).
    - UI: `Coyote_0.4/data/logs/coyote_ui_*.log`.
- **Duplicate Hypothes.is annotations.** The event writer detects duplicates and marks those events “duplicate” (no NLP).

## Roadmap (short list)
- UI “Update images” action and image pinning; richer Insight panels; more connectors (YouTube transcripts, Obsidian); improved topic/entity thresholds and weighting. *(PRs welcome.)*

## License
GPL-v3 (copyleft). See the license text linked in this repository.

