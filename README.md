# Coyote: An AI-Powered Self-Directed Learning Record

## Introduction

Coyote is an AI-powered learning record designed for individuals engaged in self-directed learning and personal development. It leverages natural language processing (NLP) and graph databases to create a rich, semantic representation of your learning activities. By ensuring user ownership of their own learning data, Coyote serves as a foundational backend upon which tools for self-directed and community-based learning can be built.

## Features

* **API Data Aggregator:** Personal data aggregation via API calls to other platforms. Currently, Hypothes.is is the integrated platform, allowing you to incorporate your annotations into your learning record.
* **Natural Language Processing:** The Coyote Python app uses NLP, specifically Named Entity Recognition (NER) and Topic Modeling, to create a machine-readable and human-readable personal learning record. This captures the semantic, meaningful, narrative “aboutness” of your learning and information journeys. 
* **Integration with Coyote Browser Extension:** A JavaScript browser extension for Firefox and Chrome-based web browsers that sends data on your online search behavior to the Coyote Python app. 
* **Integration with Neo4j Graph Database:** Coyote integrates with Neo4j to store and visualize the relationships between different pieces of your learning data. This graph database allows you to explore and analyze your learning paths in a connected and meaningful way, uncovering insights and patterns in your self-directed learning.
* **WikiData Ontology Integration:** By connecting your learning data to the Wikidata ontology, Coyote enriches your personal learning record with structured knowledge from one of the largest open knowledge bases. This enhances the semantic depth of your learning record, enabling advanced queries and explorations.

## Installation & Setup Instructions

### Prerequisites
* **Docker and Docker Compose:** Ensure you have Docker and Docker Compose installed on your system. Instructions can be found on the Docker website.
* **Web Browser:** Firefox or Chrome to use the Coyote Browser Extension.

### Getting Started
1. **Obtain Coyote:**
The easiest way to obtain Coyote is to pull the Docker image from GitHub's Container Registry. 

Or, if you prefer to work with the source code, you can clone or download the Coyote app repository from GitHub:
`git clone https://github.com/captaininfo/coyote.git`

2. **Obtain the Coyote Browser Extension:**
Since the Coyote Browser Extension is not available in the official browser extension stores, you can clone or download the source code from the repository:
`git clone https://github.com/captaininfo/coyote-browser-extension.git`

3. **Set Up and Run Coyote:**
* **Using Docker Compose:**
Navigate to the Coyote project directory and start the application using Docker Compose.
`cd coyote
docker-compose up -d`

This command builds and starts both the Coyote app and a Neo4j instance in Docker containers.

* **Without Docker:**
If you cloned the repository and prefer to run Coyote without Docker, ensure you have Python 3.10 installed. Install the required packages and start the application.
`cd coyote
pip install -r requirements.txt
python3 -m coyote.coyote_server`

4. **Setting Neo4j Username and Password:**

* **In Docker Container:**
When using the containerized version of Neo4j, you can set the initial username and password through the NEO4J_AUTH environment variable in the docker-compose.yml file:
`environment:
  - NEO4J_AUTH=neo4j/your_password`

The credentials you set here will persist across container restarts because the Neo4j data is stored in a Docker volume.

* **Standalone Neo4j Installation:**
If you're running Neo4j outside of Docker, you'll be prompted to create a username and password when you first start the Neo4j server. These credentials will persist unless you reset the database.

5. **Access the Coyote Configuration Page:**
With the Coyote app running, open your web browser and navigate to:
`http://localhost:5000/configure`

On this page, you can input your Neo4j username and password. Enter the credentials you set in the docker-compose.yml file or during Neo4j setup. Click the "Save Configuration" button to store your credentials securely within Coyote.

6. **Connect Your Hypothes.is Account (Optional):**
On the same configuration page (configure.html), you can connect your Hypothes.is account:
* **Step 1:** Log in to your Hypothes.is account by visiting [https://hypothes.is/login](https://hypothes.is/login).
* **Step 2:** Obtain your API token from the Hypothes.is developer page at [https://hypothes.is/account/developer](https://hypothes.is/account/developer).
* **Step 3:** Enter your Hypothes.is username and API token into the corresponding fields on the configuration page.
* **Step 4:** Click "Save Configuration" to store your credentials securely within Coyote.
* **Step 5:** To fetch your annotations, click the "Fetch Data from Hypothes.is" button. The initial fetch may take some time, especially if you have many annotations, due to the API's limit of 200 records per call.

7. **Using the Coyote Browser Extension:**
* **First, start up the Coyote app if it's not already running:**
    * **Using Docker:**
    `docker-compose up -d`

    * **Without Docker:**
    `python3 -m coyote.coyote_server`

* **Second, load the Coyote Browser Extension as a temporary extension:**

    **For Firefox:**
    * Open Firefox and navigate to `about:debugging#/runtime/this-firefox`.
    * Click on "Load Temporary Add-on".
    * Select the `manifest.json` file from the cloned `coyote-browser-extension` directory.

    **For Chrome:**
    * Open Chrome and navigate to `chrome://extensions/`.
    * Enable "Developer mode" using the toggle switch in the upper-right corner.
    * Click on "Load unpacked".
    * Select the `coyote-browser-extension` directory.

* **Third, use the extension:**
    * Right-click on a browser tab to access the context menu.
    * Select "Coyote search" or "Coyote search in new tab".

* **Fourth, from the Coyote search page:**
    * Enter the purpose of your search and your search terms.
    * Click the "Search" button to proceed.

* **Fifth, browse the web as you normally would.** Coyote will record your browsing activity to enhance your learning record.


## Privacy
Your privacy is our priority. Here's how Coyote ensures your data remains secure:
* **Local Data Storage:** All your data is stored locally on your machine. There are no Coyote servers storing or processing your data externally.
* **User Control:** You have full control over your data. You can inspect, export, or delete your data at any time. 
* **Data Recording:** Coyote records your browsing activity to build your learning record. If you wish to exclude certain activities, consider using a dedicated browser profile or a different browser for activities you don't want to record.

## Contributing
Contributions are welcome! You can fix bugs, propose new features, improve documentation, or help spread the word.

### How to Contribute
* **Report Issues:** Use the GitHub issues tracker to report bugs or suggest enhancements.
* **Pull Requests:** Submit pull requests for code changes or documentation updates.
* **Feedback:** Share your experience using Coyote and suggest ways to improve it.

## License
Coyote is released under GPLv3 “copyleft” license. Please visit the GNU General Public License webpage to learn what this license allows and requires: [https://www.gnu.org/licenses/gpl-3.0.en.html](https://www.gnu.org/licenses/gpl-3.0.en.html)


## Road Map
The following are features planned for future development. Community contributions are very welcome! 
* **Write User Data to Database:** Transition from writing user data to `analysis_result.json` to storing it in the SQLite database. Update `json_to_neo4j.py` to pull data from the database, improving scalability and data management.
* **Periodic Archiving of `analysis_result.json`:** Implement a mechanism to archive or rotate the analysis_result.json file to prevent it from becoming too large. 
* **Enhanced NLP Features:** Integrate additional NLP capabilities such as sentiment analysis and advanced entity recognition. Improve existing NLP. 
* **Additional API Integrations:** Expand data aggregation to include other platforms like Obsidian, YouTube (e.g., NLP of transcripts), web-based word processors (e.g., Google Docs), or task/project management apps.
* **Integrate an Open, Local LLM as an Interface:** Using an LLM that can be installed locally as a front-end interface and that uses Coyote as a back-end for RAG (Retrieval Augmented Generation) could be interesting. 


## Acknowledgements

Coyote leverages several open-source technologies:
* **Python 3.10:** The primary programming language used for the Coyote application.
* **Flask:** A lightweight WSGI web application framework for serving the Coyote app and API.
* **Docker:** Used to containerize the application and its dependencies for easy deployment.
* **Neo4j:** A graph database platform for storing and querying the learning data.
* **Wikidata:** An open knowledge base that Coyote integrates with to enrich learning data.
* **Hypothes.is API:** Allows Coyote to fetch user annotations and integrate them into the learning record.
* **spaCy:** An open-source NLP library used for Named Entity Recognition.
* **BERTopic:** A topic modeling technique used to identify topics within the user's data.
* **Browser Extension APIs:** Used to develop the Coyote Browser Extension for Firefox and Chrome.

Each of these technologies is subject to its own licenses and terms of use. Please refer to their respective documentation for license details.

Additionally, I want to say thank you to the Open Recognition community for their support and feedback!


## Support
If you encounter any issues or have questions, please open an issue on GitHub or contact the maintainers directly.

## Anticipated Frequently Asked Questions (AFAQ)

**Q:** Is my data secure and private?
**A:** Yes. Coyote stores all data locally on your machine. There is no external data transmission beyond fetching data from APIs you've connected (e.g., Hypothes.is).

**Q:** Can I use Coyote without Docker?
**A:** Yes. You can clone the repository and run the application directly with Python 3.10, following the setup instructions provided.

**Q:** How do I update Coyote to the latest version?
**A:** If you're using Docker, pull the latest image from Docker Hub. If you're running from source, pull the latest changes from the GitHub repository and rebuild or restart the application.


