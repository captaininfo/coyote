# Coyote  🐾  
*A personal learning record & analytics engine: powerful, private, and unobtrusively automated* 

**TL;DR** – Coyote is a new kind of technology designed for our AI-assisted future: a personal data back-end of a kind that will be essential for useful, private, user-controlled AI systems. It quietly logs your information behavior and returns actionable feedback. Coyote analyzes how you search, read and annotate on the open web, and saves a richly-linked knowledge graph of your real-world learning and intellectual outputs. The result is a personal knowledge base that can be queried, visualized, analyzed, or paired with AI agents. Coyote runs locally, is 100% private, and is user-controlled – no cloud, no vendor lock-in. Coyote is not a learning management system. It is a heutagogical tool designed to turn practically any real-world experience into an intentional learning opportunity. 

Here are a few rough analogues to help new users understand the “what” and “why” of Coyote. 
You can picture Coyote as…  
*… a “Fitbit” for the mind.
*… xAPI for self-directed, open-world learning – no preplanned curricula required.
*… a private data back-end to work with personal AI agents (think “A Young Lady’s Illustrated Primer”).

---

## 1  Why Coyote?
If you’re pursuing serious, self‑directed, life‑wide learning, exploring personal learning analytics, building privacy‑respecting PKM/Tools‑for‑Thought, or prototyping local agent workflows, Coyote gives you a practical foundation: local‑first data collection, an auditable analysis pipeline, a queryable graph, and a simple UI. It’s free/open‑source (GPLv3), designed for individuals and opt‑in communities, and it never phones home.

**Private & Secure AI Agents**: AI agents are most useful when they have rich, personal context—but giving that context to a third‑party cloud invites surveillance, lock‑in, and misuse. Coyote takes the opposite approach: it runs locally and turns your everyday activity—search intent, web pages you read, and the annotations you make—into a machine‑readable knowledge graph you own.

**Mind Modelling**: Coyote treats your mind like a classic “black box”: study the inputs (what you sought and read) and the outputs (what you highlighted, wrote, and linked), then use transparent NLP to map both to real‑world concepts. This creates a private “learning ledger” you can inspect, query, and build on. The goal isn’t a robot teacher; it’s a personal learning analytics toolkit that helps you see patterns, test hunches, and feed your preferred local LLM/agent with high‑quality, on‑device context.

**Enhanced Self-Understanding and Cognitive Autonomy**: Understanding your own information behavior is critical for maintaining independence in a landscape filled with persuasive technologies and subtle manipulations. Coyote helps you see clearly how you engage with information, offering insights that protect your cognitive autonomy and decision-making freedom.

**Personal Development Through Lifelong Learning**: Learning happens everywhere—not just in classrooms. Coyote captures and analyzes your informal learning experiences, providing insights that help you recognize and strengthen your skills, talents, and interests. Think of it as a "Fitbit" for your intellectual growth, continuously supporting your personal and professional development.

**Democratizing Education and Opportunity**: Traditional education often excludes many due to cost, geography, or other systemic barriers. Coyote empowers individuals worldwide to access meaningful self-directed learning experiences, creating pathways to personal growth and career opportunities regardless of their circumstances. It's a practical step toward educational equity.

**Empowered Decision-Making**: Coyote visualizes your digital habits, helping you make intentional, informed decisions toward your personal and professional goals.
 

---

## 2  Feature highlights

- **Local‑first, privacy by default**
    Everything runs on your machine. Your browsing traces, annotations, NLP outputs, and graph data are stored locally and remain under your control. No telemetry; no external servers. Built for individuals and opt‑in communities—not for institutional monitoring.

- **Purposeful capture from the browser**
    The Coyote extension records learning context—not just URLs:
    Purpose statements, search terms, SERPs, visited pages, and follow‑through links become first‑class nodes and relationships in your graph.

- **Hypothes.is integration (optional)**
    Import highlights and notes with your API token. Annotations are linked to their source pages and to concepts in your graph, so you can follow evidence trails across sources.

- **Real semantics, not just metadata**
    Transparent NLP (TF‑IDF, RAKE, NER, topic models) extracts topics/entities from what you read and write, then links them to Wikidata concepts. Coyote computes “aboutness” so the most meaningful ideas and their ontology parents rise to the top.

- **A graph you can see and a graph you can ask**
    Explore visually with a fast Cytoscape view and quick queries (recent activity, top topics, annotations↔pages).
    Ask in natural language—Coyote translates NL → read‑only Cypher and runs it against Neo4j, with shape/schema guardrails to keep queries safe and auditable.

- **Early, useful Learning Insights (auditable)**
    Out‑of‑the‑box views highlight New Topics, Sensemaking Rate (searches that convert into annotations), and Learning Rhythms (when deep work happens). Calculations are simple and inspectable.

- **Simple launch & predictable ops**
    Double‑click launchers start a lightweight UI. From the System Status panel:
    Start Core, Start LLM, Start All, Stop, Restart, with health checks. A minimal .env is generated on first run (including a unique Neo4j password).

- **Bring your own local LLM**
    Point Coyote at an Ollama model to power NL→Cypher and agent workflows entirely offline. Keep sensitive context on device while enabling GraphRAG‑style reasoning.

- **Extensible by design**
    Clean boundaries—ingest → analyze → write to graph → explore—make it straightforward to add connectors (RSS, Zotero, note tools), new insights, or UI panels. Use Coyote as a personal data backend for your own agents and PKM workflows.

**Bottom line:** Coyote helps you see, query, and reason over your real learning trails—privately—and gives your local agents the context they need without surrendering your data.

– **Unobtrusive data capture**  
    – Browser extension records Google (or Brave, DuckDuckGo, etc.) searches, click-streams, and webpage contents.   
    – Hypothes.is API importer pulls your public/private annotations and highlights.

– **Local SQLite event store**  
    – Each interaction is written to a lightweight database.
    – Background threads batch-process events without blocking your browsing.

– **NLP (natural language processing) pipeline**  
    – spaCy → BERTopic extract entities & topics.
    – Results are stored *as-JSON* so you can rerun, enrich, or analyze data later.

– **Graph backend (Neo4j 5)**  
    – Events are continuously mirrored into Neo4j, producing a personal knowledge graph.

– **Wikidata ontology linking**  
    – Topics/entities are resolved to Wikidata URIs.
    – Recursive lookup builds an *ad-hoc slice* of the world ontology around your interests.

– **All local, all yours**  
    – Works offline.
    – Data folder is mounted as a Docker *volume* so you can back-up or delete with one command.

---


## Installation & Setup Instructions

### Prerequisites

#### Operating systems
- **Windows 10/11** (with **Docker Desktop**; WSL2 backend enabled)
- **macOS 12+** (Intel or Apple Silicon; Docker Desktop)
- **Linux (x86_64 / aarch64)** (Docker Desktop or Docker Engine + Compose plugin)

#### Software
- **Docker + Docker Compose v2** (Compose is included with Docker Desktop, which can be found at `https://docs.docker.com/`. Linux users have the option to install `docker` and the `docker compose` plugin)
- **Python 3.10+** (UI creates a virtualenv automatically; Windows launcher looks for `py` or `python`)
- **Firefox web browser** (for the Coyote browser extension)

#### Hardware
- 8 GB RAM minimum recommended to capture user data (requires running Coyote Core + Neo4j containers)
- 16 GB RAM minimum recommended to run Coyote Agent + Ollama/LLM containers
- ~8-15 GB free disk space (first-run Docker pulls, Neo4j data, optional model cache)

#### Network
- First run may pull container images and (optionally) an LLM.

### Obtain Coyote
**Goal:** a single downloadable **Coyote_0.4** package containing the UI, the extension, and the Compose project in `./compose` with a ready `.env`.

#### Where to get it
- **For Beta testers:** Download from Google Drive: `https://drive.google.com/drive/folders/13XN3tBaN_Mvzq_Qxts6FaP_52RHUDqSD?usp=sharing`.
- **Source:** “Coyote Core” code on GitHub (for contributors).
- **Container images:** GitHub Container Registry (GHCR).


## Setup & Run Coyote (Step-by-step)

### 1. Unpack & launch the UI
Unzip **Coyote_0.4** somewhere you have write access (e.g., `~/Applications/Coyote_0.4` on macOS/Linux or `C:\Apps\Coyote_0.4` on Windows). The important relative paths are already wired.
- **Windows:** double‑click `launch/Start-Coyote.cmd`
What happens: The script creates `ui/.venv-04` if needed, installs `ui/requirements.txt`, sets `COYOTE_COMPOSE_DIR=../compose`, and opens http://localhost:8080
- **macOS:** double‑click `launch/Start Coyote on Mac.command`
(Calls `start_coyote_mac_linux.sh`, sets `COYOTE_COMPOSE_DIR`, creates venv, opens the browser)
- **Linux:** double‑click `launch/Start Coyote on Linux.desktop` (or run `bash launch/start_coyote_mac_linux.sh`)

**First run:** the UI generates `compose/.env` (chmod 600 on Unix) with a random Neo4j password and sensible defaults. Keep this file private.

### 2. Start containers from the UI (no terminal)
Open the **System Status** tab and use the buttons:
- The button **Start Core Services** starts Neo4j and Coyote Core (API on port 5000)
- The button **Start LLM Service** (optional) runs the Ollama service and checks the configured model (port **11434**) 
- The button **Start All Services** starts `core + llm + agent` (the Streamlit bot on port **8501**).
Health checks, found on the **System Status** tab, will turn indicators Online/green as services pass checks. 

#### Default ports (from `.env`):
- UI: **8080** (Flask serving the dashboard)
- Coyote Core API: **5000**
- Neo4j HTTP/Bolt: **7474 / 7687**
- Ollama: **11434**
- Agent/Bot: **8501**

If any ports are already in use on your system, edit `compose/.env` and then click the `Restart Services` button on the **System Status** tab.

### 3. Load the Firefox extension (temporary add-on for 0.4)
1. In Firefox, open `about:debugging#/runtime/this-firefox` (you might want to bookmark this page in your browser)
2. Click **"Load Temporary Add-on..."**
3. Select the `extension/manifest.json` in your `Coyote_0.4/extension` folder. 
4. Keep the tab open while testing; temporary add‑ons unload when Firefox restarts

The Coyote UI's **System Status** tab shows Browser Extension: Online/green within a few seconds (heartbeats drive the status). 

### 4. Configure your Neo4j Authentication Credentials
Coyote needs to know how to authenticate to your Neo4j database. On first run, Coyote created credentials in `compose/.env`.

### A. Configure via the Coyote UI (recommended)
1. Go to **Configure → Neo4j Database**.
2. **Neo4j URI:** leave as `bolt://localhost:7687` unless you’ve changed ports.
3. Enter **Username** and **Password** you want Coyote to use. 
4. Click the **Test Connection** button. 
    - If you see **Unauthorized**, double-check against `compose/.env`.
5. Click **Save**. 
    - Writes the values to `compose/.env` (`NEO4J_USERNAME`, `NEO4J_PASSWORD`, `NEO4J_AUTH`) and saves your host URI for display.
6. Click **Restart Services** (or **Stop All → Start Core**) to ensure all services consume the updated settings.

### 5. (Optional) Connect Hypothes.is
Hypothes.is is a social web-annotation tool. Connecting to a Hypothes.is account is optional, but the data it generates will create a clearer picture of your learning and information behavior.
1. From the Coyote UI, open the **Integrations** tab (found in the side nav bar). Click the **Hypothes.is** toggle to expand the Hypothes.is section. 
2. Paste in your Hypothes.is username and your API token. 
    - Hypothes.is username can be found at `https://hypothes.is/users/`
    - API token can be found at `https://hypothes.is/account/developer`
3. Click the **Test** button, then the **Save** button. 
4. Click the **Fetch Data** button to import your annotations. In Neo4j, your imported **Annotation** nodes will link to the **Webpage** nodes they annotate, as well as ontology nodes. 


## Privacy
Your privacy is our priority. Here's how Coyote ensures your data remains secure:
– **Local Data Storage:** All your data is stored locally on your machine. There are no Coyote servers storing or processing your data externally.
– **User Control:** You have full control over your data. You can inspect, export, or delete your data at any time. 
– **Data Recording:** Coyote records your browsing activity to build your learning record. If you wish to exclude certain activities, consider using a dedicated browser profile or a different browser for activities you don't want to record.


## Contributing
We welcome your contributions: 
– **Report Issues:** Use the GitHub issues tracker to report bugs or suggest enhancements.
– **Pull Requests:** Submit pull requests for code changes or documentation updates.
– **Feedback:** Share your experience using Coyote and suggest ways to improve it.


## License
Coyote is released under GPLv3 “copyleft” license. Please visit the GNU General Public License webpage to learn what this license allows and requires: [https://www.gnu.org/licenses/gpl-3.0.en.html](https://www.gnu.org/licenses/gpl-3.0.en.html)


## Road Map
The following are features planned for future development. Community contributions are welcome!  
– **Refine/Improve NLP:**
    – Improve TF-IDF scoring of extracted topics and entities to more accurately reflect which topics/entities are truly important in a given web resource.
    – Limit the number of topics and entities recorded for a given online resource to only those that meet a given TF-IDF threshold for importance to the resource. 
    – Integrate additional NLP capabilities such as sentiment analysis.  
– **Additional API Integrations:** Expand data aggregation to include other platforms like Obsidian, YouTube (e.g., NLP of transcripts), web-based word processors (e.g., Google Docs), or task/project management apps.
– **Exclude certain URLs from NLP Analysis:** Some webpages don't need NLP analysis and shouldn't be part of the user's personal data record. For example, when users visit the "Configure Coyote" webpage, or log into Hypothes.is, those events needn't be analyzed or recorded. 


## Acknowledgments

Coyote leverages several open-source technologies:
– **Python 3.11:** The primary programming language used for the Coyote application.
– **Flask:** A lightweight WSGI web application framework for serving the Coyote app and API.
– **Docker:** Used to containerize the application and its dependencies for easy deployment.
– **Neo4j:** A graph database platform for storing and querying the learning data.
– **Wikidata:** An open knowledge base that Coyote integrates with to enrich learning data.
– **Hypothes.is API:** Allows Coyote to fetch user annotations and integrate them into the learning record.
– **spaCy:** An open-source NLP library used for Named Entity Recognition.
– **BERTopic:** A topic modeling technique used to identify topics within the user's data.
– **Browser Extension APIs:** Used to develop the Coyote Browser Extension for Firefox and Chrome.

Each of these technologies is subject to its own licenses and terms of use. Please refer to their respective documentation for license details.

Additionally, I want to say thank you to the Open Recognition community for their support and feedback!


## Found a Bug?
1. Run:  docker compose logs > logs.txt
2. Click:  https://github.com/CoyoteOrg/coyote/issues/new?template=bug.yml
3. Fill the boxes, attach logs.txt.  Done!


## Anticipated Frequently Asked Questions (AFAQ)

**Q:** Is my data secure and private?
**A:** Yes. Coyote stores all data locally on your machine. There is no external data transmission beyond fetching data from APIs you've connected (e.g., Hypothes.is).

**Q:** Can I use Coyote without Docker?
**A:** Yes. You can clone the repository and run the application directly with Python 3.10, following the setup instructions provided.

**Q:** How do I update Coyote to the latest version?
**A:** If you're using Docker, pull the latest image from Docker Hub. If you're running from source, pull the latest changes from the GitHub repository and rebuild or restart the application.


