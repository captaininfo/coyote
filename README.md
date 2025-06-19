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
Coyote is designed to empower individuals and communities in an AI-driven world. Here's why you should try it:

**Privacy and Personal Agency**: AI is increasingly integral to everyday life, capturing unprecedented volumes of deeply personal data. Corporations and governments have repeatedly mishandled sensitive personal information (e.g., Cambridge Analytica, NSA surveillance). Should such entities be trusted with insights into your thoughts, interests, and behaviors? Coyote helps your data remains yours—private, secure, and entirely under your control.

**Enhanced Self-Understanding and Cognitive Autonomy**: Understanding your own information behavior is critical for maintaining independence in a landscape filled with persuasive technologies and subtle manipulations. Coyote helps you see clearly how you engage with information, offering insights that protect your cognitive autonomy and decision-making freedom.

**Personal Development Through Lifelong Learning**: Learning happens everywhere—not just in classrooms. Coyote captures and analyzes your informal learning experiences, providing insights that help you recognize and strengthen your skills, talents, and interests. Think of it as a "Fitbit" for your intellectual growth, continuously supporting your personal and professional development.

**Democratizing Education and Opportunity**: Traditional education often excludes many due to cost, geography, or other systemic barriers. Coyote empowers individuals worldwide to access meaningful self-directed learning experiences, creating pathways to personal growth and career opportunities regardless of their circumstances. It's a practical step toward educational equity.

**Empowered Decision-Making**: Coyote visualizes your digital habits, helping you make intentional, informed decisions toward your personal and professional goals.
 

---

## 2  Feature highlights

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
– **Docker and Docker Compose:** Ensure you have Docker Desktop installed on your system. Instructions can be found on the Docker website. (Note: Linux users have the option to install Docker Engine and Compose seperately.)
– **Web Browser:** Firefox or Chrome to use the Coyote Browser Extension.

### Getting Started: Instructions for Early Testers
The following instructions should help beta testers get started quickly and easily. If you run into technical issues, please reach out to me at justinmason.mlis@gmail.com or text me at (406) 207-3108. 

**Important note:** For this early testing phase, the Neo4j credentials are turned off in the `docker-compose.yml` file for convenience. This zero-auth configuration is for local beta testing only. Do not run it on a server or expose ports externally. Production releases will restore password authentication. 

#### 1. **Obtain Coyote:**
Beta testers can download the Coyote package (app, browser extension, etc.) from my Google Drive: https://drive.google.com/drive/folders/13XN3tBaN_Mvzq_Qxts6FaP_52RHUDqSD?usp=sharing  

Once public, users can obtain Coyote by pulling the Docker image from GitHub's Container Registry.

If users prefer to work with the source code, they can clone or download the Coyote app repository from GitHub:
`git clone https://github.com/captaininfo/coyote.git` 

#### 2. **Obtain the Coyote Browser Extension:**
Once again, beta testers can download the entire Coyote package from my Google Drive: 
https://drive.google.com/drive/folders/13XN3tBaN_Mvzq_Qxts6FaP_52RHUDqSD?usp=sharing 

Since the Coyote Browser Extension is not available in the official browser extension stores, once public, users can clone or download the source code from the repository:
`git clone https://github.com/captaininfo/coyote-browser-extension.git`

#### 3. **Set Up and Run Coyote:**
##### **Recommended: Use Docker & Docker Compose:**

**Step 0:** Install Docker and Docker-Compose if you don’t already have them on your computer. 
The following link connects to the official Docker website: https://docs.docker.com/ 

**Step 1:** Create a coyote project directory, then navigate to that directory. 

    ‘mkdir coyote
    cd coyote’

**Step 2:** Load Coyote’s Docker image (enter one of the following OS-specific commands in your CLI): 

    Mac:‘docker load -i insert/filepath/to/coyote/directory/coyote_2025-05-15.tar’ 
    
    Linux: ‘sudo docker load -i insert/filepath/to/coyote/directory/coyote_2025-05-15.tar’

    Windows: 'docker load -i C:\insert\filepath\Downloadsto\coyote\directory\coyote_2025-05-15.tar'

**Step 3:** Start the application using Docker Compose. This command builds and starts both the Coyote app and a Neo4j instance in Docker containers:

    ‘docker compose up -d’
    
    Linux may require: ‘sudo docker compose up -d’

##### **Optional: Run Without Docker:**
If you cloned the repository and prefer to run Coyote without Docker, ensure you have the correct version of Python installed (currently Python 3.11). Install the required packages and start the application:

    `cd coyote
    pip install -r requirements.txt
    python3 -m coyote.coyote_server`

#### 4. **Setting Neo4j Username and Password:**

##### **If You’re Using Docker**
For this beta testing phase, the Neo4j authorization requirement has been turned off in the `docker-compose.yml` file. This zero-auth configuration is for local beta testing only. Do not run it on a server or expose ports externally. Production releases will restore password authentication.

##### **If You’re Running Neo4j Without Docker**
If you're running Neo4j outside of Docker, you'll be prompted to create a username and password when you first start the Neo4j server. These credentials will persist unless you reset the database.

#### 5. **Access the Coyote Configuration Page:**
With the Coyote app running, open your web browser and navigate to:
`http://localhost:5000/configure`

**Beta testers:** On this page, you will tell Coyote where to find Neo4j. 
    1. In the field “Neo4j URI”, enter: ‘bolt://localhost:7687’ 
    2. Leave “Neo4j Username” blank
    3. Leave “Neo4j Password” blank
    4. Click the "Save Configuration" button (unless you’re connecting a Hypothes.is account, in which case continue to Step 6).

    Once Coyote is public and login credentials are required, this page will allow users to input their Neo4j URI, Neo4j Username, and Neo4j Password. 

#### 6. **Connect Your Hypothes.is Account (Optional):**
On the same configuration page (configure.html), you can connect your Hypothes.is account:
– **Step 1:** Log in to your Hypothes.is account by visiting [https://hypothes.is/login](https://hypothes.is/login).
– **Step 2:** Obtain your API token from the Hypothes.is developer page at [https://hypothes.is/account/developer](https://hypothes.is/account/developer).
– **Step 3:** Enter your Hypothes.is username and API token into the corresponding fields on the configuration page.
– **Step 4:** Click "Save Configuration" to store your credentials securely within Coyote.
– **Step 5:** To fetch your annotations, click the "Fetch Data from Hypothes.is" button. The initial fetch may take some time, especially if you are already a Hypothes.is user with many annotations. Note: Hypothes.is sets an API limit of 200 records per call.

#### 7. **Using the Coyote Browser Extension:**
    **Step 1:** Start up the Coyote app if it's not already running:
        **Using Docker:**
        docker-compose up -d` 
        
        Or Linux may require: 
        ‘sudo docker-compose up -d’

    **Step 2:** Load the Coyote Browser Extension as a temporary extension:

        **For Firefox:**
            – Open Firefox and navigate to `about:debugging#/runtime/this-firefox`.
            – Click on "Load Temporary Add-on".
            – Select the `manifest.json` file from the cloned `coyote-browser-extension` directory.

        **For Chrome:**
            – Open Chrome and navigate to `chrome://extensions/`.
            – Enable "Developer mode" using the toggle switch in the upper-right corner.
            – Click on "Load unpacked".
            – Select the `coyote-browser-extension` directory.

    **Step 3:** Use the extension
        – Right-click on a browser tab to access the context menu.
        – Select "Coyote search" or "Coyote search in new tab".

    **Step 4:** From the Coyote search page
        – Enter the purpose of your search and your search terms.
        – Click the "Search" button to proceed.

    **Step 5:** Browse the web as you normally would. Coyote will record your browsing activity to enhance your learning record.

    **Step 6:** Open the Neo4j Browser and connect it to Neo4j to view your data.
        – Open the Neo4j Browser by opening a new tab in your browser and navigating to: ‘http://localhost:7474/browser’
        – On the “Connect to Neo4j” webpage:
            * Connect URL: select ‘bolt://localhost:7687’
            * Database: Leave blank to choose default
            * Authentication type: select “No Authoriztion”
            * Username: Leave blank
            * Password: Leave blank
            * Click the “Connect” button

    **Step 7:** Enter Cypher queries into the Neo4j Browser to view your data. 
        – Display your data:

            MATCH (n)
            WHERE n:Purpose OR n:SearchTerms OR n:Webpage OR n:Annotation
            OPTIONAL MATCH (n)-[r1]->(m1)
            OPTIONAL MATCH (m1)-[r2]->(m2)   // second hop
            RETURN n,r1,m1,r2,m2

        – Delete your data from Neo4j:
            
            MATCH (n)
            DETACH DELETE n;


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
– **Local LLM Integration:** Use local LLMs for a front-end interface and Retrieval Augmented Generation (RAG). 
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


