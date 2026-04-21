# Coyote 🐾
### *Your curiosity leaves trails. Coyote maps them.*

---

Every search you run, every page you read, every passage you highlight — these are evidence of your mind at work. Most of that evidence evaporates: your browser history is noise, your bookmarks are a graveyard, and your notes capture conclusions but not the messy, productive trail that led there.

**Coyote captures that trail. Locally. Privately. And makes it queryable.**

It runs quietly in the background while you browse, logs the structure of your learning (your intentions, searches, the pages you read, the passages you annotated), and builds a semantic knowledge graph on your own machine. No cloud. No algorithm deciding what matters. Then it lets you explore, visualize, and converse with that graph using a local LLM — so the AI answering your questions is drawing on *your* learning history, not someone else's data.

Coyote treats your mind like a classic **black box flight recorder**: study the inputs (what you sought, read, and clicked) and the outputs (what you highlighted and wrote), use transparent NLP to map both to real-world concepts, and surface the patterns you couldn't see while you were in the middle of learning. The result is a private, auditable *learning ledger* — something you can inspect, query, and build on over time.

> *In Neal Stephenson's* The Diamond Age*, a girl is raised partly by an AI book called the Young Lady's Illustrated Primer — a personal tutor that knows her, challenges her at the edge of her understanding, and grows with her. Coyote won't write you stories. But the idea of a personal AI that understands your learning trajectory, built from your data, owned entirely by you? That's the north star.*

---

## Who Coyote Is For

Coyote is for people who **learn seriously** and want something to show for it:

- **Researchers and self-directed learners** who want to audit and reflect on their own intellectual work over time
- **Privacy-conscious users** who are done feeding their learning data to cloud platforms in exchange for features they don't control
- **PKM enthusiasts** (Obsidian, Logseq, Roam, etc.) who want *behavioral* evidence of learning alongside their curated notes
- **Learning analytics researchers** looking for a local-first, open-source platform to study self-directed learning in the wild
- **Developers and tinkerers** who want a hackable, extensible knowledge graph they actually own — and a codebase designed to be approachable

---

## What It Looks Like in Practice

Imagine you spent three weeks going deep on a topic — climate policy, a new programming language, a medical diagnosis, a historical event. You ran searches, followed links, read papers, highlighted passages. Coyote was building a graph of that work the whole time: which topics clustered together, what you returned to repeatedly, how your searches evolved, which pages produced annotations versus dead ends.

Now, a month later, you can:

- **Ask your local LLM:** *"What did I read about carbon markets? What am I probably missing based on what I've covered?"*
- **Explore visually:** See a graph of how your understanding formed — purposes connecting to searches, searches to pages, pages to the topics and real-world concepts they contained
- **Query directly:** *"Show me everything I annotated in the last 30 days related to machine learning"*
- **Check your rhythms:** When do you do your best exploratory learning? When do you go deep vs. skim?

Everything that answers those questions came from *your* data, processed *on your machine*, by tools you can inspect.

---

## What's Included (v0.4 MVP)

- **Browser extension (Firefox).** Captures purpose + search terms, SERPs, visited pages, hyperlink clicks, and annotations — staged automatically into the local pipeline.
- **Double-click UI.** A lightweight dashboard starts/stops all services, shows container health, and surfaces Insights, Visual Explorer, and Chat Assistant. No terminal required for normal use.
- **Local NLP pipeline (auditable).** Scraper → summarizer → topic extraction (BERTopic with TF-IDF fallback) → named entity recognition → Wikidata concept linking. Every step is logged; failures are captured with status codes, not silent drops.
- **Neo4j knowledge graph.** Your browsing data lands in a structured graph: `Purpose → SearchTerms → Webpage → Annotation`, with `Topic` and `WikiDataOntology` nodes attached. Fully inspectable via the Neo4j browser.
- **GraphRAG chat assistant.** Three-tier hybrid retrieval: parameterized Cypher for topic/term queries → LLM-generated analytical Cypher → time-window fallback. Schema-gated and read-only by design.
- **Learning Insights.** Built-in panels for New Topics (first-seen concepts over time), Sensemaking Rate (how often searches lead to annotations), and Learning Rhythms (hour-of-day activity patterns).
- **Hypothes.is importer (optional).** One-click fetch from the UI; token stored encrypted locally.
- **Wikidata ontology linking (optional).** Automatically attaches parent concepts (subclass of, instance of) with caching and depth limits.

---

## Requirements

| | |
|---|---|
| **OS** | Windows 10/11 (WSL2), macOS 12+, Linux x86_64/aarch64 |
| **Software** | [Docker Desktop](https://docs.docker.com/desktop/) (Windows/macOS) or [Docker Engine](https://docs.docker.com/engine/install/) + [Compose v2 plugin](https://docs.docker.com/compose/install/linux/) (Linux); [Firefox](https://www.firefox.com/en-US/) |
| **RAM** | 8 GB for Core services; 16 GB recommended if running LLM + Agent |
| **Disk** | ~8–15 GB for images + Neo4j data + optional model cache |

---

## Quick Start (No Terminal Required)

**1. Confirm requirements** — Docker Desktop (or Engine + Compose) and Firefox are installed.

**2. Download and unpack Coyote**
Get [`coyote-download-0.4.0.zip`](https://github.com/captaininfo/coyote/releases/tag/v0.4.0-beta.1) from the Releases section of this repository. Unzip it somewhere you have write permissions (e.g., your Documents folder).

**3. Make launch files executable** *(Linux and macOS only)*
```bash
chmod +x 'launch/start_coyote_mac_linux.sh'
chmod +x 'launch/Start Coyote on Linux.desktop'
chmod +x 'launch/Start Coyote on Mac.command'
```
On Windows, no extra step is needed.

**4. Launch the UI**
Double-click the launcher for your OS. The Coyote dashboard opens at `http://localhost:8080`. On first run, it auto-generates `compose/.env` (chmod 600) with a strong Neo4j password and sensible defaults.

**5. Start services** (UI → System Status)
- **Start Core Services** — Neo4j + Coyote Core. This is all you need to begin capturing browsing data.
- *(Optional)* **Start LLM Service** — downloads and runs Ollama with `mistral:7b-instruct` locally.
- *(Optional)* **Start All Services** — adds the GraphRAG Chat Assistant.

**6. Load the Firefox extension**
In Firefox, go to `about:debugging#/runtime/this-firefox` → **Load Temporary Add-on…** → select `manifest.json` from `coyote-download-0.4.0/extension/`. When connected, the UI's System Status tab will show **Browser Extension: Online**.

**7. Browse normally.** Coyote captures your activity in the background. Your graph begins building automatically.

### Default ports
| Service | Port |
|---|---|
| Coyote UI | 8080 |
| Core API | 5000 |
| Neo4j (browser / bolt) | 7474 / 7687 |
| Ollama | 11434 |
| Chat Agent | 8501 |

All ports are configurable via `compose/.env`.

### Optional setup
- **Custom Neo4j credentials:** UI → Configure → enter credentials → **Test Connection → Save**
- **Connect Hypothes.is:** UI → Integrations → **Test → Save → Fetch Data** (token stored encrypted)

---

## Explore & Chat

**Explore Visually** — Run canned queries or type natural language; Coyote translates to Cypher, enforces read-only access and schema gating, and renders your graph. A good starting point for orienting yourself to the shape of your own learning.

**Chat Assistant (GraphRAG)** — Ask questions about what you've read and learned. The agent uses a three-tier hybrid retrieval strategy: parameterized Cypher for topic and term matching, LLM-generated Cypher for analytical questions, and a time-window fallback so you always get something. The same schema guards used in Explore apply here.

---

## How the Pipeline Works

Understanding the pipeline helps you trust it — and debug it when something looks off.

1. **Capture → Staging (SQLite).** Events from the extension and Hypothes.is land in `EventStaging`. A centralized `event_queue` in `coyote_state.db` drives all downstream processing.
2. **Ingest → Event DB.** Core normalizes staged rows into typed tables: `Events`, `WebpageLoads`, `HyperlinkClicks`, `Annotations`, `Topics`, `Entities`, `EventTracking`.
3. **NLP (auditable).** For each content page: scrape → summarize → topic extraction (BERTopic + TF-IDF) → named entity recognition → Wikidata mapping. TF-IDF scores are persisted per context; failures write a status row rather than silently dropping data.
4. **Write → Neo4j.** A background manager reads `nlp_processed` events and writes the graph: `Purpose → SearchTerms`, `Webpage`, `Annotation`, `Topic`, ontology links. Marks `neo4j_done` when complete.
5. **Ontology linking (optional).** A separate manager attaches Wikidata parent concepts with caching and configurable depth limits.
6. **Janitor.** Periodic cleanup removes terminal-state events and prunes stale cache entries.

**Note on exemptions:** Coyote deliberately skips NLP on non-content pages (Google SERPs, Hypothes.is account pages, the local configuration screen). Only substantive content pages enter the pipeline.

---

## Learning Insights

Three lightweight analytics panels are built into the UI:

- **New Topics** — First-seen topics across Webpages and Annotations over a configurable time window. A map of your conceptual frontier.
- **Sensemaking Rate** — Ratio of SERPs to Annotations within a time window following each search. A rough proxy for how often your exploration produces something worth capturing.
- **Learning Rhythms** — Hour-of-day activity patterns (active seconds where available, interaction counts otherwise). When do you actually learn?

These are MVP panels — deliberately simple, deliberately transparent. The goal is to prompt reflection, not to score you.

---

## Privacy & Security

Coyote was designed from the start around a specific premise: **your learning data is yours, and it should never leave your machine without your explicit action.**

- **Local-first.** All databases and logs live under `compose/volumes/` on your own disk (mounted to `/app/data` in containers). Nothing is transmitted externally.
- **Encrypted secrets.** Your Neo4j password and Hypothes.is token are stored encrypted in Core's state database using a per-install Fernet key. They are never written to plaintext config files.
- **Read-only LLM queries.** Natural language → Cypher translation is enforced as read-only via a blocklist validator. The LLM cannot modify your graph.
- **Schema-gated queries.** Cypher execution is gated against the live graph schema — the LLM cannot hallucinate node types or relationships that don't exist.
- **Your control.** You can stop all services from the UI at any time, inspect or delete your local databases directly, or browse in a separate Firefox profile to keep specific activity out of Coyote.

---

## Troubleshooting

**Neo4j "Unauthorized" error**
→ UI → Configure → Test Connection → Save → Restart Services

**Containers won't stop**
→ UI → Force Cleanup (falls back to `docker stop / kill / rm`)

**Where are the logs?**
- Core: `compose/volumes/coyote/logs/coyote_server.log` (inside container: `/app/data/logs/`)
- UI server: `Coyote_0.4/data/logs/coyote_ui_*.log`

**Duplicate Hypothes.is annotations**
→ Expected behavior. The event writer detects duplicates and marks them `"duplicate"` — they are skipped by NLP and do not enter the graph.

---

## Manual CLI (Optional Fallback)

The UI's Start/Stop buttons are wrappers around standard `docker compose` commands. If you prefer the terminal or need to script deployments:

```bash
cd Coyote_0.4/compose

# Start Core (Neo4j + Coyote Core)
COMPOSE_PROFILES=core docker compose -p coyote -f compose.yaml up -d --pull=missing

# Start LLM + Agent
COMPOSE_PROFILES=agent,llm docker compose -p coyote -f compose.yaml up -d --pull=missing

# Check status
docker compose -p coyote -f compose.yaml ps --format json

# Stop everything
docker compose -p coyote -f compose.yaml down
```

---

## Contributing & Installing from Source

Coyote is GPL-v3 licensed and designed to be hackable. The codebase is intentionally modular — you can replace the LLM, swap the graph backend, add new NLP steps, or build new connectors without touching the core pipeline.

**Run from source:**
```bash
# Core API + background managers
python -m coyote.coyote_server    # see images/core/requirements.txt

# UI server
python ui/coyote_ui_server.py     # serves dashboard at :8080
```

**Run tests:**
```bash
python -m pytest tests/ -v        # 43 tests (security, sync checks)
make sync-shared                  # sync nl2cypher.py before docker builds
```

**Good first contributions:**
- Additional connectors (YouTube transcripts, Obsidian vault importer, Zotero)
- Richer Insights panels
- Improved topic and entity thresholds and weighting
- Simplified onboarding documentation and setup experience

**Where to start:** Read [`CLAUDE.md`](CLAUDE.md) for a compact architecture reference, then look at `chains.py` (GraphRAG logic) and `coyote_server.py` (pipeline entry points).

Questions, ideas, and PRs are welcome.

---

## Roadmap

Near-term priorities:
- UI "Update images" action and image pinning
- Richer Insight panels with configurable time windows
- More connectors: YouTube transcripts, Obsidian, Zotero
- Vector search activation (indexes exist; embeddings pipeline not yet wired)
- Simplified onboarding for non-Docker users

Longer horizon:
- Local Wikidata embedding index (ChromaDB/Qdrant) as a privacy-preserving alternative to external API lookups for concept mapping
- Configurable privacy profiles (exclude domains, pause capture, etc.)
- Export formats for interoperability with other PKM tools

---

## License

GPL-v3 (copyleft). If you build on Coyote, your improvements stay open. See [`LICENSE`](LICENSE) for the full text.
