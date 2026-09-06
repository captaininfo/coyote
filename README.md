# Coyote 🐾
### *A free, open-source, local-first tool that turns what you read and write online into a private knowledge graph on your own computer — a searchable record of your self-directed, informal learning.*

---

Coyote runs quietly in the background while you browse. It captures what you read, the links you follow, and the passages you annotate, and turns them into a semantic knowledge graph on your own machine — meaning, not just URLs and timestamps. No cloud, no account, no algorithm deciding what matters. Then you can explore that graph visually, or ask questions of it in plain language using a local AI, so the answers draw on your learning history and nothing else.

It's built for people who already know they learn seriously outside a classroom — autodidacts, researchers, lifelong and self-directed learners — and want an honest record of it, not the tidy story memory tells afterward. If you've ever wished for a private, local-first alternative to the learning analytics a school or platform runs on you, Coyote is that, pointed the other way: the data is yours, and the analysis serves you.

A deliberate choice about what the AI is for. The record, and every analysis over it, stays deterministic and inspectable — the same page always produces the same result, computed by transparent NLP you can audit. The language model helps you explore and question that record; it never computes or rewrites what actually happened. Coyote furnishes the evidence. You infer the meaning. And it works with whatever local model you run — a small one ships out of the box — so nothing here depends on a single vendor.

What's real today, and what's vision. This is a version 0.5 MVP, built by one person in the open. What's described below as working is running and checkable against the code right now: capture, the deterministic pipeline, the knowledge graph, visual exploration, and GraphRAG chat. The more ambitious ideas — tracing how your understanding shifts over time, richer graph-based context — are labeled as vision and sized as doable community work, not finished features or a solo moonshot. It asks real friction of you (Docker, a Firefox extension, a multi-gigabyte install), so the rest of this page is written to help you decide whether that trade is worth it — and to let you verify the claims yourself.

---

## Requirements

| | |
|---|---|
| **OS** | Windows 10/11 (WSL2), macOS 12+, Linux x86_64/aarch64 |
| **Software** | **Docker** — [Docker Desktop](https://docs.docker.com/desktop/) (Windows/macOS; on a Mac, choose the installer matching your chip — **Apple Silicon** or **Intel**) or [Docker Engine](https://docs.docker.com/engine/install/) + [Compose v2 plugin](https://docs.docker.com/compose/install/linux/) (Linux). On older macOS the latest Docker Desktop may refuse to open — install a version matching your macOS from [Docker's release notes](https://docs.docker.com/desktop/release-notes/). **·** [**Firefox**](https://www.firefox.com/en-US/) 140+. **·** [**Python**](https://www.python.org/downloads/) 3.10+ (runs the launcher + dashboard on your machine). |
| **RAM** | 8 GB runs Core capture; **16 GB recommended** for the Chat Assistant (LLM + Agent) — 8 GB is not enough to run Chat well |
| **Disk** | ~8–15 GB for images + Neo4j data + optional model cache |

> **First-run downloads take time — plan for it.** The first launch builds Coyote's container images (several GB), and **Start All** additionally pulls the optional LLM stack — the Ollama runtime image plus the ~2 GB `qwen2.5-coder:3b` model, several gigabytes in total. On a fast connection the first install is roughly **10–30 minutes**; on a slow link it can take *hours*. To install light, click **Start Core Services** first — capture works without the LLM download entirely — and add the LLM/Chat later when bandwidth allows.

---

## Quick Start

**1. Confirm requirements** — Docker (Desktop, or Engine + Compose on Linux), Python 3.10+, and Firefox 140+ are installed. On a Mac, check your chip first (Apple menu → **About This Mac**) so you download the matching Docker installer, and verify Python with `python3 --version`.

**2. Download and unpack Coyote**
From the [latest release](https://github.com/captaininfo/coyote/releases/latest), download the **Source code** archive — `.zip` (Windows) or `.tar.gz` (macOS/Linux) — and unpack it somewhere you have write permissions (e.g., your Documents folder).
> *Prefer git?* `git clone https://github.com/captaininfo/coyote.git && cd coyote && git checkout v0.5.0`

**3. Make launch files executable** *(Linux and macOS only — skip on Windows)*
Open a terminal **in the unpacked `coyote` folder** (the one containing the `launch/` folder), then run:
```bash
chmod +x launch/start_coyote_mac_linux.sh
chmod +x launch/Start-Coyote-on-Linux.desktop
chmod +x launch/Start-Coyote-on-Mac.command
```

> *Prefer not to touch a terminal?* On Linux, do this in your file manager: right-click each file → **Properties → Permissions → Allow executing file as program**. On macOS the terminal `chmod` above is the reliable path (double-clicking these files just opens them in a text editor). This is the only place a command might be needed — everything after is buttons in the dashboard.

> **Linux only — add yourself to the `docker` group first.** If you installed Docker Engine via apt or the convenience script, your user may not be able to reach the Docker daemon, and the very first **Start** click fails instantly with a raw `permission denied while trying to connect to the Docker daemon socket` error. Fix it once, up front: run `sudo usermod -aG docker $USER`, then **log out and back in** (or run `newgrp docker` and relaunch the dashboard from that shell). Verify with `docker ps`. Docker Desktop users on Windows/macOS are unaffected.

**4. Launch the UI**
Double-click the launcher **for your OS** (inside the `launch/` folder):
- **macOS** → **`Start-Coyote-on-Mac.command`** *(not `start_coyote_mac_linux.sh` — that's the underlying script; double-clicking it just opens a text editor)*
- **Linux** → **`Start-Coyote-on-Linux.desktop`**
- **Windows** → **`Start-Coyote.cmd`**

**macOS first launch:** Gatekeeper blocks it with an *"unidentified developer"* message that only offers **OK** — that's a dead end. Instead of double-clicking, **right-click the file → Open**, then click **Open** in the dialog that follows (this one *does* have an Open button). macOS remembers the choice, so later double-clicks work.

The dashboard opens at `http://localhost:8080` in your **default** browser. If that's Safari or Chrome and you see *"can't connect to the server,"* the dashboard may still be starting — wait a few seconds and refresh, or open `http://localhost:8080` in **Firefox** (the browser the extension runs in). On first run the launcher auto-generates `compose/.env` (chmod 600) with a strong Neo4j password and sensible defaults.

**5. Start services** (UI → System Status)
- **Start Core Services** — Neo4j + Coyote Core. This is all you need to begin capturing browsing data, and it's the **low-bandwidth path**: it skips the several-GB LLM download, so start here first and add the LLM/Chat later.
- *(Optional)* **Start LLM Service** — runs Ollama and, on first run, downloads the `qwen2.5-coder:3b` model (~2 GB, several minutes). The System Status line shows *"Downloading language model…"* with progress; Chat isn't ready until it finishes.
- *(Optional)* **Start All Services** — adds Coyote's GraphRAG **Chat Assistant** and pulls the same model. *To use Chat you need this, not just Start Core.* On first run the model downloads in the background (~2 GB, several minutes); the System Status line shows *"Downloading language model…"* with progress. The dashboard and services come up right away, but if you open Chat before the download finishes it will report that the model isn't ready — just wait for the progress line to complete, then try again.

> **The first build is slow and quiet — this is normal.** The very first **Start Core Services** downloads and builds the container images, commonly **10–30 minutes** depending on your machine and connection. During that time the status may sit at **"0 of N services running" with little visible progress. Don't quit or assume it has hung** — subsequent starts are fast.
>
> *(macOS)* You may get a pop-up asking to install the **Command Line Tools** for `git`. Click **Cancel** — it's Docker capturing build metadata and isn't required.

**6. Install the Firefox extension**
Download **`coyote_browser_extension-2.0.0.xpi`** from the [latest release](https://github.com/captaininfo/coyote/releases/latest). In Firefox, open `about:addons` → the gear icon ⚙ → **Install Add-on From File…** → choose the `.xpi` (or simply drag the `.xpi` onto the `about:addons` page). It's signed by Mozilla, so it installs permanently — no developer mode or temporary loading needed.

Then **pin it to your toolbar** so the Pause button is reachable: click the Extensions puzzle-piece icon → the gear next to **Coyote Browser Extension** → **Pin to Toolbar**. When connected, the UI's System Status tab shows **Browser Extension: Online**.

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

## What's Included (v0.5 MVP)

- **Browser extension (Firefox).** Captures the pages you visit and the links you click — staged automatically into the local pipeline. (Annotations arrive via the optional Hypothes.is importer; the in-browser search box that also captured a stated *purpose* + search terms is disabled this release — see "Not in this release" below.)
- **Double-click UI.** A lightweight dashboard starts/stops all services, shows container health, and surfaces the Visual Explorer and Chat Assistant. No terminal required for normal use. (A Learning Insights panel is present as a "coming post-MVP" placeholder.)
- **Local NLP pipeline (auditable).** Scraper → summarizer → topic extraction (KeyBERT) → named entity recognition → Wikidata concept linking. Every step is logged; failures are captured with status codes, not silent drops.
- **Neo4j knowledge graph.** Your browsing data lands in a structured graph of `Webpage` and `Annotation` nodes with `Topic` and `WikiDataOntology` concepts attached. Fully inspectable via the Neo4j browser. (The schema also models `Purpose` and `SearchTerms` from the search box, dormant this release.)
- **GraphRAG chat assistant.** Three-tier hybrid retrieval: parameterized Cypher for topic/term queries → LLM-generated analytical Cypher → time-window fallback. Schema-gated and read-only by design.
- **Learning Insights (coming post-MVP).** A placeholder panel today — the planned analytics are described in the [Learning Insights](#learning-insights-coming-post-mvp) section below.
- **Hypothes.is importer (optional).** One-click fetch from the UI; token stored encrypted locally.
- **Wikidata ontology linking (optional).** Automatically attaches parent concepts (subclass of, instance of) with caching and depth limits.

**Not in this release.** The in-browser **search box** — which captured a stated *purpose* alongside your search terms — is disabled while it's reworked behind a proper pause/consent gate (it previously recorded even when capture was paused or in a private window). So this release captures the pages you visit, the links you click, and your Hypothes.is annotations; `Purpose`/`SearchTerms` nodes and the Sensemaking-Rate insight return once the search box does.

---

## What It Looks Like in Practice

Say you spent a few weeks reading across a topic — climate policy, a programming language, a medical question, a historical period — following links, reading papers, and highlighting passages with Hypothes.is. Coyote was quietly building a semantic graph of that work the whole time. Here's what you can do with it today:

- **See the shape of your reading.** The Visual Explorer draws your pages linked to the real-world concepts they contain and the passages you annotated. The `knowledge_shape` demo goes further, rendering a coverage map where the concepts you returned to loom large and one-off reading recedes — an honest picture of where your attention actually went. *(A sparse spot means you haven't read about it here, not that you don't understand it.)*
- **Ask in plain language.** In the Chat Assistant: *"What have I read about carbon markets?"* or *"Show me everything I annotated in the last 30 days about machine learning."* Your question is matched against your own reading and notes, and a local model answers from what it retrieves — no cloud, nothing but your own data in the context.
- **Trace a note back to its sources.** Hand Coyote one of your annotations and it ranks your reading by similarity in *meaning* to surface what likely informed it. This is measured, not a hunch: on one real user's data (83 substantive notes), the true source landed at a **median rank of 2**, was the single best match **37%** of the time, and fell in the **top five 67%** of the time — with a real annotation link to grade each answer against. It's deliberately the *smallest* claim Coyote makes: a checkable control proving the record measures something real.

Everything that answers those questions came from *your* data, processed *on your machine*, by tools you can inspect. The coverage-map and source-inference views run as scripts in [`demos/`](images/core/core_analysis/coyote/demos/) today; wiring them into the dashboard is a good first contribution.

---

## Explore & Chat

**Explore Visually** — Run built-in queries over your graph; Coyote enforces read-only access and schema gating and renders the results visually. A good starting point for orienting yourself to the shape of your own learning. (For free-form natural-language questions, use the Chat Assistant below.)

**Chat Assistant (GraphRAG)** — Ask questions about what you've read and learned. The agent uses a three-tier hybrid retrieval strategy: parameterized Cypher for topic and term matching, LLM-generated Cypher for analytical questions, and a time-window fallback so you always get something. The same schema guards used in Explore apply here.

---

## How the Pipeline Works

Understanding the pipeline helps you trust it — and debug it when something looks off.

1. **Capture → Staging (SQLite).** Events from the extension and Hypothes.is land in `EventStaging`. A centralized `event_queue` in `coyote_state.db` drives all downstream processing.
2. **Ingest → Event DB.** Core normalizes staged rows into typed tables: `Events`, `WebpageLoads`, `HyperlinkClicks`, `Annotations`, `Topics`, `Entities`, `EventTracking`.
3. **NLP (auditable).** For each content page: scrape → summarize → pooled full-document text embedding → topic extraction (KeyBERT) → named entity recognition → Wikidata concept linking. Topic and entity scores, and the page's embedding, are persisted per context; failures write a status row rather than silently dropping data.
4. **Write → Neo4j.** A background manager reads `nlp_processed` events and writes the graph: `Webpage`, `Annotation`, `Topic`, and ontology links (plus `Purpose`/`SearchTerms` when a search is recorded — dormant this release). Marks `neo4j_done` when complete.
5. **Ontology linking (optional).** A separate manager attaches Wikidata parent concepts with caching and configurable depth limits.
6. **Janitor.** Periodic cleanup removes terminal-state events and prunes stale cache entries.

**Note on exemptions:** Coyote deliberately skips NLP on non-content pages (Google SERPs, Hypothes.is account pages, the local configuration screen). Only substantive content pages enter the pipeline.

---

## Is Coyote for You?

Coyote is for people who **learn seriously** and want something to show for it:

- **Researchers and self-directed learners** who want to audit and reflect on their own intellectual work over time
- **Privacy-conscious users** who are done feeding their learning data to cloud platforms in exchange for features they don't control
- **PKM enthusiasts** (Obsidian, Logseq, Roam, etc.) who want *behavioral* evidence of learning alongside their curated notes
- **Learning analytics researchers** looking for a local-first, open-source platform to study self-directed learning in the wild
- **Developers and tinkerers** who want a hackable, extensible knowledge graph they actually own — and a codebase designed to be approachable

---

## Learning Insights (coming post-MVP)

The dashboard reserves a **Learning Insights** panel; in this release it shows a "coming post-MVP" placeholder. The planned analytics — deliberately simple and transparent, meant to prompt reflection rather than score you — are:

- **Topic frequency** — the concepts your reading returns to most.
- **Sensemaking rate** — how often exploration produces something you annotate (returns with the search box).
- **Browsing rhythms** — when in the day you do your deep vs. skim learning.

The record already captures what these need; wiring up the panels is a good first contribution.

---

## Privacy & Security

Coyote was designed from the start around a specific premise: **your learning data is yours, and it should never leave your machine without your explicit action.**

- **Local-first.** All databases and logs live under `compose/volumes/` on your own disk (mounted to `/app/data` in containers). Your browsing history, embeddings, graph, and the local model that answers your questions never leave your machine. The only outbound calls are the disclosed WikiData concept lookups (individual extracted terms — never your pages, URLs, or history) and, if you connect Hypothes.is, its own servers.
- **Pause and private browsing.** The extension's **Pause** toggle suspends capture, and browsing in a Firefox private window is not captured — nothing you read while paused or in a private window enters Coyote.
- **Encrypted secrets.** Credentials you enter in the UI — a custom Neo4j login and your Hypothes.is token — are Fernet-encrypted (per-install key) in Core's state database. One honest exception: the Neo4j password is *also* written to `compose/.env` in plaintext, because Docker Compose reads it to start the database. That file is created `chmod 600` (owner-only) and is git-ignored, so it stays on your machine and out of version control — but, unlike the state-DB secrets, it is not encrypted at rest.
- **Read-only LLM queries.** Natural language → Cypher translation is enforced as read-only via a blocklist validator. The LLM cannot modify your graph.
- **Schema-gated queries.** Cypher execution is gated against the live graph schema — the LLM cannot hallucinate node types or relationships that don't exist.
- **Your control.** You can stop all services from the UI at any time, inspect or delete your local databases directly, or browse in a separate Firefox profile to keep specific activity out of Coyote.

---

## Troubleshooting

**Neo4j "Unauthorized" error**
→ UI → Configure → Test Connection → Save → Restart Services

**Chat says the model isn't ready / "model not found"**
→ The LLM downloads on first run (~2 GB). Watch the System Status line for *"Downloading language model…"* and wait for it to finish, then reopen Chat. Chat needs **Start All Services** (or **Start LLM Service**), not just Start Core.

**Reinstalling, or ran an earlier Coyote before?**
→ Delete the old unpacked folder before unpacking the new one. A leftover `compose/.env` from a previous install is silently reused, which can carry stale credentials or ports into the new copy.

**Docker won't open on older macOS** — *"You can't use this version of Docker.app with this version of macOS."*
→ The latest Docker Desktop supports only recent macOS releases. Install an older version matching your macOS from [Docker's release notes](https://docs.docker.com/desktop/release-notes/) (each release lists Intel and Apple-Silicon downloads).

**Containers won't stop**
→ UI → Force Cleanup (falls back to `docker stop / kill / rm`)

**(Linux, rare) Container downloads/builds fail with `Temporary failure resolving …`**
→ If your home network happens to use the `172.17.0.0/16` range, it collides with Docker's default bridge and containers can't reach the internet. Relocate Docker's bridge in `/etc/docker/daemon.json` (e.g. `{"bip": "10.200.0.1/24", "default-address-pool": [{"base": "10.201.0.0/16", "size": 24}]}`) and restart Docker. Most networks (192.168.x / 10.x) are unaffected.

**Where are the logs?**
- Core: `compose/volumes/coyote/logs/coyote_server.log` (inside container: `/app/data/logs/`)
- UI server: `data/logs/coyote_ui_*.log` (under the unpacked Coyote folder)

**Duplicate Hypothes.is annotations**
→ Expected behavior. The event writer detects duplicates and marks them `"duplicate"` — they are skipped by NLP and do not enter the graph.

---

## Manual CLI (Optional Fallback)

The UI's Start/Stop buttons are wrappers around standard `docker compose` commands. If you prefer the terminal or need to script deployments:

```bash
cd compose        # run from inside the unpacked Coyote folder

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

## Contributing

Coyote is GPL-v3 licensed and built to be hackable — swap the LLM, change the graph backend, add NLP steps, or write new connectors without touching the core pipeline. Contributions are welcome; four short docs get you oriented:

- **[CONTRIBUTING.md](CONTRIBUTING.md)** — dev setup, conventions, tests, and good first issues (the flagship: materialize the ontology as a traversable DAG).
- **[ARCHITECTURE.md](ARCHITECTURE.md)** — a one-page map of the services, data flow, and graph model.
- **[VISION.md](VISION.md)** — where the project is headed, and which rungs are already real versus community work.
- **[`CLAUDE.md`](CLAUDE.md)** — the exhaustive architecture reference and the running list of Known Issues.

Questions, ideas, and PRs are all welcome.

---

## Roadmap

Near-term priorities:
- UI "Update images" action and image pinning
- Ship the Learning Insights panels (topic frequency, sensemaking rate, browsing rhythms) with configurable time windows
- More connectors: YouTube transcripts, Obsidian, Zotero
- Richer semantic-search surfacing in Explore and Chat (the embeddings pipeline and Tier-0 vector retrieval already ship; deeper context expansion is next)
- Simplified onboarding for non-Docker users

Longer horizon:
- Local Wikidata embedding index (ChromaDB/Qdrant) as a privacy-preserving alternative to external API lookups for concept mapping
- Configurable privacy profiles (per-domain exclusion, capture rules) — a Pause toggle already ships in the extension
- Export formats for interoperability with other PKM tools

---

## License

GPL-v3 (copyleft). If you build on Coyote, your improvements stay open. See [`LICENSE`](LICENSE) for the full text.
