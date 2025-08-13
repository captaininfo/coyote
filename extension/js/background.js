// background.js
// This script runs in the background and listens for events from the browser tabs

/**
 * Gets the appropriate browser namespace for cross-browser compatibility.
 * @returns {object} The browser namespace object.
 * @throws Will throw an error if no suitable namespace is found.
 */
function getBrowserNamespace() {
    if (typeof browser !== 'undefined') {
        return browser; // Firefox, LibreWolf, etc.
    } else if (typeof chrome !== 'undefined') {
        return chrome; // Chrome, Edge, etc.
    }
    throw new Error('No suitable namespace found for browser extensions.');
}

const extBrowser = getBrowserNamespace();

let isInitialized = false;

/**
 * Sends data to the local server endpoint.
 * Provides user feedback if the data transmission fails.
 * @param {object} data - The data to send to the server.
 * @param {string} endpoint - The server endpoint to send the data to.
 */
async function sendDataToServer(data, endpoint) {
    console.log("Sending data to server", { data, endpoint });
    if (!endpoint) {
        console.error("Endpoint is undefined", { data, endpoint });
        return;
    }
    // If Core isn't running yet, silently skip rather than alerting the user.
    if (!(await isCoreAvailable())) {
        console.debug('Coyote Core is not running; skipping send:', endpoint);
        return;
    }
    console.log(`URL being called: http://127.0.0.1:5000/${endpoint}`, JSON.stringify(data));
    fetch(`http://127.0.0.1:5000/${endpoint}`, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify(data)
    })
    .then(response => {
        if (!response.ok) {
            throw new Error('Network response was not ok: ' + response.statusText);
        }
        return response.json();
    })
    .then(responseData => {
        console.log('Success:', responseData);
    })
    .catch((error) => {
        console.error('Error:', error);
        // Provide user feedback if data transmission fails
        extBrowser.notifications.create({
            type: 'basic',
            iconUrl: extBrowser.runtime.getURL('icons/coyote-75.png'),
            title: 'Coyote Extension Error',
            message: 'Failed to send data to the server. Please ensure the server is running.',
        });
    });
}

/**
 * Creates context menu items for the extension.
 */
function createContextMenus() {
    extBrowser.contextMenus.create({
        id: "coyote-search",
        title: "Coyote search",
        contexts: ["tab", "browser_action"],
    });
    extBrowser.contextMenus.create({
        id: "coyote-search-new-tab",
        title: "Coyote search in new tab",
        contexts: ["tab", "browser_action"],
    });
    // Add Connect to Hypothes.is menu item
    extBrowser.contextMenus.create({
        id: "connect-hypothesis",
        title: "Connect to Hypothes.is",
        contexts: ["browser_action"],
    });
}

// Handle clicks on context menu items
extBrowser.contextMenus.onClicked.addListener((info, tab) => {
    switch (info.menuItemId) {
        case "coyote-search":
            extBrowser.tabs.update(tab.id, { url: extBrowser.runtime.getURL("html/new_tab.html") });
            break;
        case "coyote-search-new-tab":
            extBrowser.tabs.create({ url: extBrowser.runtime.getURL("html/new_tab.html") });
            break;
        case "connect-hypothesis":
            // Open a new tab for Hypothes.is connection setup
            extBrowser.tabs.create({ url: extBrowser.runtime.getURL("html/connect_hypothesis.html") });
            break;
    }
});

createContextMenus();

/**
 * Listens for messages from other extension scripts and handles them accordingly.
 */
extBrowser.runtime.onMessage.addListener((message, sender, sendResponse) => {
    if (!isInitialized) {
        sendResponse({ status: 'Extension initializing, data ignored' });
        return true; // Indicates asynchronous response
    }

    switch (message.type) {
        case 'pageLoaded':
            sendDataToServer(message.data, 'webpage_visit');
            break;
        case 'hyperlinkClicked':
            sendDataToServer(message.data, 'hyperlink_click');
            break;
        case 'searchInitiated':
            sendDataToServer(message.data, 'init_search');
            break;
        case 'fetchHypothesisData':
            // Make a GET request to fetch data from Hypothes.is
            fetch('http://127.0.0.1:5000/fetch_hypothesis_data', {
                method: 'GET'
            })
            .then(response => response.json())
            .then(data => console.log('Fetched Hypothesis data:', data))
            .catch(error => console.error('Error fetching data:', error));
            break;
    }
    sendResponse({ status: 'Data sent to server' });
    return true; // Indicates asynchronous response
});

/**
 * Handles clicks on the extension's browser action icon.
 * Opens the 'new_tab.html' page in a new browser tab.
 */
extBrowser.browserAction.onClicked.addListener(() => {
    extBrowser.tabs.create({ url: extBrowser.runtime.getURL("html/new_tab.html") });
});

/**
 * Initializes the extension after a delay.
 * This can be adjusted or changed to event-based initialization if needed.
 */
function initializeExtension() {
    console.log("Initializing extension...");
    setTimeout(() => {
        isInitialized = true;
        console.log("Extension initialized, now tracking events.");
    }, 5000); // Adjust this delay as needed
}

initializeExtension();

// Uses localStorage for status when server isn't available
// ============ DUAL-MODE STATUS REPORTING ============

const HEARTBEAT_INTERVAL = 5000;
const LOCALSTORAGE_KEY = 'coyote_extension_status';
let heartbeatTimer = null;
let serverAvailable = false;


// --- Stable per-runtime session ID ---
// This value resets when the background script is reloaded (e.g., extension reload or
// event-page wakes up again). It is NOT persisted to disk by design.
const SESSION_ID = (() => {
  try {
    const arr = new Uint8Array(16);
    crypto.getRandomValues(arr);
    return [...arr].map(b => b.toString(16).padStart(2, '0')).join('');
  } catch {
    return String(Date.now()) + Math.random().toString(16).slice(2);
  }
})();

/**
 * A wrapper so existing calls still work.
 */
function generateSessionId() {
  return SESSION_ID;
}

/**
 * Return a human-friendly browser name for diagnostics.
 */
function getBrowserName() {
    const ua = navigator.userAgent || '';
    if (ua.includes('Firefox')) return 'Firefox';
    if (ua.includes('Edg/')) return 'Edge';
    if (ua.includes('Chrome')) return 'Chrome';
    if (ua.includes('Safari')) return 'Safari';
    return 'Unknown';
}

/**
 * Write status to localStorage (always available)
 */
function updateLocalStatus() {
    const statusData = {
        active: true,
        timestamp: Date.now(),
        version: extBrowser.runtime.getManifest().version,
        browser: getBrowserName(),
        initialized: isInitialized
    };

    // Write to localStorage (this persists even if extension reloads)
    try {
        localStorage.setItem(LOCALSTORAGE_KEY, JSON.stringify(statusData));
    } catch (e) {
        console.error('Could not write to localStorage:', e);
    }
}

/**
 * Clear status when extension stops
 */
function clearLocalStatus() {
    try {
        localStorage.removeItem(LOCALSTORAGE_KEY);
    } catch (e) {
        console.error('Could not clear localStorage:', e);
    }
}

/**
 * Check if Coyote server is available
 */
async function checkServerAvailability() {
    try {
        const response = await fetch('http://127.0.0.1:8080/', {
            method: 'GET',
            mode: 'no-cors', // Avoid CORS issues for simple check
            signal: AbortSignal.timeout(1000) // 1 second timeout
        });
        serverAvailable = true;
        return true;
    } catch {
        serverAvailable = false;
        return false;
    }
}

/**
 * Check if Coyote Core server is available
 */
let _coreUp = false;
let _lastProbe = 0;
const CORE_HEALTH_URL = 'http://127.0.0.1:5000/health';

async function isCoreAvailable() {
  const now = Date.now();
  if (now - _lastProbe < 3000) return _coreUp; // reuse result for 3s

  _lastProbe = now;
  try {
    // no-cors avoids CORS preflight; a network success implies server is listening
    await fetch(CORE_HEALTH_URL, { method: 'GET', mode: 'no-cors', signal: AbortSignal.timeout(800) });
    _coreUp = true;
  } catch {
    _coreUp = false;
  }
  return _coreUp;
}


/**
 * Combined heartbeat function
 */
async function sendHeartbeat() {
    // Always update localStorage
    updateLocalStatus();

    // Send to UI server (for the System Status light)
    if (await checkServerAvailability()) {
        try {
            const payload = {
                extensionId: SESSION_ID,
                version: extBrowser.runtime.getManifest().version,
                browserName: getBrowserName(),
                timestamp: new Date().toISOString(),
                status: 'active',
                initialized: isInitialized
            };
            // Hit the UI so the dashboard can see us
            await fetch('http://127.0.0.1:8080/extension_heartbeat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });

            fetch('http://127.0.0.1:5000/extension_heartbeat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            }).catch(() => {});

        } catch (error) {
            console.debug('Server heartbeat failed, but localStorage updated');
        }
    }
}

/**
 * Start the dual-mode heartbeat
 */
function startHeartbeat() {
    // Initial heartbeat
    sendHeartbeat();

    // Clear any existing timer
    if (heartbeatTimer) {
        clearInterval(heartbeatTimer);
    }

    // Set up regular heartbeat
    heartbeatTimer = setInterval(sendHeartbeat, HEARTBEAT_INTERVAL);
    console.log('Dual-mode heartbeat started');
}

/**
 * Stop heartbeat and clear status
 */
function stopHeartbeat() {
    if (heartbeatTimer) {
        clearInterval(heartbeatTimer);
        heartbeatTimer = null;
    }
    clearLocalStatus();
}

// Start on load
startHeartbeat();

// Clean up on unload
window.addEventListener('beforeunload', stopHeartbeat);
extBrowser.runtime.onSuspend?.addListener(stopHeartbeat);
