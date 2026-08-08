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

// ============ CAPTURE PAUSE ============
// User-facing kill switch, toggled from the toolbar button. Persisted so the
// choice survives an event-page suspend/wake cycle.

const PAUSE_KEY = 'coyote_paused';
let isPaused = false;

/**
 * Reads the persisted pause flag into memory and syncs the toolbar button.
 * Awaited by the message handler so a message arriving during a cold wake-up
 * can never be captured while the user has capture paused.
 * @returns {Promise<void>}
 */
async function loadPauseState() {
    try {
        const stored = await extBrowser.storage.local.get(PAUSE_KEY);
        isPaused = Boolean(stored && stored[PAUSE_KEY]);
    } catch (error) {
        console.error('Could not read pause state; defaulting to paused:', error);
        isPaused = true; // fail closed: never capture on an unknown state
    }
    updatePauseIndicator();
}

/**
 * Reflects the current pause state in the toolbar button.
 */
function updatePauseIndicator() {
    const title = isPaused
        ? 'Coyote — capture PAUSED (click to resume)'
        : 'Coyote — capturing (click to pause)';
    try {
        extBrowser.browserAction.setTitle({ title });
        extBrowser.browserAction.setBadgeText({ text: isPaused ? 'OFF' : '' });
        extBrowser.browserAction.setBadgeBackgroundColor({ color: '#b3261e' });
    } catch (error) {
        console.debug('Could not update the toolbar indicator:', error);
    }
}

const pauseReady = loadPauseState();

// Keep memory in sync if the flag is changed elsewhere (e.g. a second window).
extBrowser.storage.onChanged.addListener((changes, area) => {
    if (area === 'local' && Object.prototype.hasOwnProperty.call(changes, PAUSE_KEY)) {
        isPaused = Boolean(changes[PAUSE_KEY].newValue);
        updatePauseIndicator();
    }
});

/**
 * Handles clicks on the extension's browser action icon: toggles capture.
 */
extBrowser.browserAction.onClicked.addListener(async () => {
    await pauseReady;
    isPaused = !isPaused;
    try {
        await extBrowser.storage.local.set({ [PAUSE_KEY]: isPaused });
    } catch (error) {
        console.error('Could not persist pause state:', error);
    }
    updatePauseIndicator();
    console.log(isPaused ? 'Coyote capture paused.' : 'Coyote capture resumed.');
});

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
            iconUrl: extBrowser.runtime.getURL('icons/coyote-128.png'),
            title: 'Coyote Extension Error',
            message: 'Failed to send data to the server. Please ensure the server is running.',
        });
    });
}

/**
 * Listens for messages from other extension scripts and handles them accordingly.
 */
extBrowser.runtime.onMessage.addListener((message, sender, sendResponse) => {
    (async () => {
        if (!isInitialized) {
            sendResponse({ status: 'Extension initializing, data ignored' });
            return;
        }

        await pauseReady;
        if (isPaused) {
            sendResponse({ status: 'Capture paused; data discarded' });
            return;
        }

        switch (message.type) {
            case 'pageLoaded':
                sendDataToServer(message.data, 'webpage_visit');
                break;
            case 'hyperlinkClicked':
                sendDataToServer(message.data, 'hyperlink_click');
                break;
        }
        sendResponse({ status: 'Data sent to server' });
    })();

    return true; // Indicates asynchronous response
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
 * Whether the user has granted the optional `technicalAndInteraction` data
 * collection permission. Firefox shows this as a toggle at install time and in
 * about:addons; declining it must not break anything, so only the diagnostic
 * FIELDS of the heartbeat are gated on it — never the request itself.
 * @returns {Promise<boolean>}
 */
async function hasTechnicalDataConsent() {
    try {
        const granted = await extBrowser.permissions.getAll();
        return Array.isArray(granted && granted.data_collection)
            && granted.data_collection.includes('technicalAndInteraction');
    } catch (error) {
        console.debug('Could not read data-collection permissions; assuming declined:', error);
        return false; // fail closed
    }
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
        await fetch('http://127.0.0.1:8080/', {
            method: 'GET',
            mode: 'no-cors', // Avoid CORS issues for simple check
            signal: AbortSignal.timeout(1000) // 1 second timeout
        });
        return true;
    } catch {
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
            // The status light keys off this POST arriving at all, so the bare
            // body below is sufficient on its own.
            const payload = {
                timestamp: new Date().toISOString(),
                status: 'active'
            };
            if (await hasTechnicalDataConsent()) {
                payload.extensionId = SESSION_ID;
                payload.version = extBrowser.runtime.getManifest().version;
                payload.browserName = getBrowserName();
                payload.initialized = isInitialized;
            }
            // Hit the UI so the dashboard can see us
            await fetch('http://127.0.0.1:8080/extension_heartbeat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload)
            });
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
