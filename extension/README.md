# Coyote Browser Extension

The Coyote Browser Extension is a key component of the Coyote app ecosystem, designed to unobtrusively gather data on the user's browsing activity and securely pass the user's data to the user's locally-installed Coyote App where it is analyzed and locally stored. The Coyote Browser Extension enables seamless interaction between a user's browsing behavior and the Coyote app's backend, gathering data that informs Coyote's personalized insights and adaptive features.

## Table of Contents
* Features
* Installation
* Usage
* Configuration
* Development
* Contributing
* License

## Features
* **User Data Collection:** Captures data on the user's browsing activity, including timestamped data on visited URLs, search terms used, purpose of browser activity, and hyperlinks clicked. 
* **Integration with Coyote app:** Connects with the main Coyote app, sending collected data to its backend for analysis.
* **Private Personal Data Collection:** Coyote is entirely private and under the user's control. User data is stored locally.

## Installation

### Prerequisites
* **Browser:** The extension is compatible with Chrome and Firefox.
* **Coyote App:** To use the extension fully, a running instance of the Coyote app is required.

### Installation Steps

1. **Clone the Repository:**
	```git clone https://github.com/YourGitHubUsername/coyote-browser-extension.git
	cd coyote-browser-extension```

2. **Load the Extension in Developer Mode** (for local testing):
	* **Chrome:**
		1. Go to `chrome://extensions/` in your browser.
		2. Enable **Developer Mode** (toggle in the top-right).
		3. Click **Load unpacked** and select the `coyote-browser-extension` folder. 
	* **Firefox:**
		1. Go to `about:debugging#/runtime/this-firefox` in your browser. 
		2. Click **Load Temporary Add-on**.
		3. Select the `manifest.json` file from the `coyote-browser-extension` folder.
3. **Connect to Coyote App:** Ensure the Coyote app is installed and running.

## Usage
1. After loading the extension, right-click a browser tab then select "Coyote search" or "Coyote search new tab" to initiate a new search. 
2. In the new Coyote browser tab, (1) input your purpose for browsing in the "Purpose" field, (2) input your search terms in the "Search terms" field, and then (3) click the "Search" button. 
3. Browse the web as you normally would. If you start a new search session, repeat the process. 

## Contributing
We welcome contributions to enhance the Coyote Browser Extension. To contribute:
1. **Fork the Repository:** Start by forking this repo to your GitHub account.
2. ** Create a Feature Branch:**
	`git checkout -b feature/YourFeatureName`
3. **Make and Commit Changes.**
4. **Push Your Changes:**
	`git push origin feature/YourFeatureName`
5. **Open a Pull Request:** Submit your changes for review.

## License
This project is licensed under the [GPLv3](https://www.gnu.org/licenses/gpl-3.0.en.html). 























