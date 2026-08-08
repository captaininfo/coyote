# Coyote Browser Extension (Firefox)

The Coyote Browser Extension is one component of the Coyote app ecosystem — a local-first, privacy-first learning tool. The extension records which pages you visit and which links you click, and hands that record to the Coyote application running on your own machine at `http://127.0.0.1`. Coyote analyses and stores everything locally; the extension never contacts any third-party server.

## What the extension collects

| Data | Sent to | Purpose |
|------|---------|---------|
| Page URL, page title, timestamp | Core, `POST 127.0.0.1:5000/webpage_visit` | Builds the record of what you read |
| Clicked link: source URL, destination URL, link text, timestamp | Core, `POST 127.0.0.1:5000/hyperlink_click` | Records how you moved between resources |
| Liveness ping (`status`, `timestamp`) | UI, `POST 127.0.0.1:8080/extension_heartbeat` | Drives the "extension active" light in the Coyote UI |
| *Optional* diagnostics (browser name, extension version, per-session id) | UI, same heartbeat request | Troubleshooting only — see below |

Nothing else is captured, and nothing is sent anywhere except those two loopback addresses on your own device.

**Note on scope:** the Coyote *application* independently fetches and analyses the full content of the pages whose URLs this extension reports, in order to build your personal knowledge graph. That fetching is done by Coyote, not by this extension — but you should know it happens.

## Data collection permissions (Firefox)

The manifest declares, per Mozilla's data-collection framework:

- **Required — Browsing activity:** the URLs of pages you visit and links you follow. This is the extension's entire purpose; it cannot be switched off separately.
- **Required — Website content:** the visible text of a link you click (its anchor text) is included with the click record.
- **Optional — Technical and interaction data:** browser name, extension version and a per-session identifier, attached to the liveness ping.

**Declining the optional toggle costs you nothing.** The liveness ping is still sent, so the Coyote UI still shows the extension as active — only the diagnostic fields are omitted.

## Privacy behaviour

- **Pause:** click the Coyote toolbar button to pause browsing capture. The button shows an `OFF`
  badge while paused, and the setting persists until you click it again.
  **Pin the button first:** Firefox hides new extensions in the puzzle-piece **Extensions** menu.
  Open that menu, click the gear next to Coyote (or right-click it) and choose **Pin to Toolbar** —
  then the `OFF` badge and "paused/capturing" tooltip are visible at a glance and Pause is one click.
  - *Scope:* Pause (and private browsing, below) stop the pages-and-links capture this extension
    performs. They do **not** affect the separate search box inside the Coyote app's own window
    (`127.0.0.1:8080`) — that is a different part of Coyote, not this extension.
- **Private browsing:** the extension never records pages in a private window, regardless of the pause setting.
- **Loopback only:** every request goes to `127.0.0.1`. There is no remote endpoint, no analytics, and no telemetry.

## Permissions requested

| Permission | Why |
|------------|-----|
| `storage` | Remembers whether capture is paused |
| `notifications` | Warns you if Coyote is running but rejects data |
| `http://127.0.0.1/*`, `http://localhost/*` | The only hosts the extension may contact |
| Content script on `<all_urls>` | Required to observe page loads and link clicks — this is the extension's primary function |

## Installation

### Prerequisites
- Firefox 140 or later.
- A running instance of the Coyote app (the extension is inert without it).

### Load for local testing
1. Go to `about:debugging#/runtime/this-firefox`.
2. Click **Load Temporary Add-on**.
3. Select `manifest.json` from this directory.

Chrome/Edge users should load `extension_chrome/` instead (the Manifest V3 equivalent).

## Usage

Install the extension, start the Coyote app, and browse normally. There is no search box and no setup step — capture begins automatically. Use the toolbar button to pause it at any time.

*(Coyote's purpose-and-search-terms feature lives in the Coyote application's own UI at `http://127.0.0.1:8080`, not in the extension.)*

## For reviewers

- **No build step.** The submitted package is the source: plain JavaScript and JSON, no bundler, minifier, transpiler or package manager. Load the directory as-is.
- **No remote code.** No `eval`, no injected `<script>` tags, no code fetched at runtime.
- **No third parties.** Requests go only to `http://127.0.0.1:5000` (Coyote Core) and `http://127.0.0.1:8080` (Coyote UI), both processes on the user's own machine.
- **Encryption in transit** is not applicable: traffic never leaves the loopback interface.
- The extension does **not** intercept, redirect or read searches performed on any search engine.

## Contributing
1. Fork the repository.
2. `git checkout -b feature/YourFeatureName`
3. Make and commit changes.
4. `git push origin feature/YourFeatureName`
5. Open a pull request.

## License
[GPLv3](https://www.gnu.org/licenses/gpl-3.0.en.html)
