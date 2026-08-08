// js/background.js
// MV3 service worker compatible background script

/**
 * Cross-browser namespace (Firefox uses `browser`, Chrome uses `chrome`).
 */
function getBrowserNamespace() {
  if (typeof browser !== 'undefined') return browser;
  if (typeof chrome !== 'undefined') return chrome;
  throw new Error('No suitable namespace found for browser extensions.');
}
const extBrowser = getBrowserNamespace();

// ---- Runtime state ----
let isInitialized = false;

// ---- Utility: stable per-runtime session ID (persists for this SW run only) ----
const SESSION_ID = (() => {
  try {
    const arr = new Uint8Array(16);
    crypto.getRandomValues(arr);
    return [...arr].map(b => b.toString(16).padStart(2, '0')).join('');
  } catch {
    return String(Date.now()) + Math.random().toString(16).slice(2);
  }
})();

// ============ CAPTURE PAUSE ============
// User-facing kill switch, toggled from the toolbar button. Persisted because
// the MV3 service worker is torn down and restarted constantly.

const PAUSE_KEY = 'coyote_paused';
let isPaused = false;

/**
 * Reads the persisted pause flag into memory and syncs the toolbar button.
 * Awaited by the message handler so a message that wakes the service worker
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

/** Reflects the current pause state in the toolbar button. */
function updatePauseIndicator() {
  const title = isPaused
    ? 'Coyote — capture PAUSED (click to resume)'
    : 'Coyote — capturing (click to pause)';
  try {
    extBrowser.action.setTitle({ title });
    extBrowser.action.setBadgeText({ text: isPaused ? 'OFF' : '' });
    extBrowser.action.setBadgeBackgroundColor({ color: '#b3261e' });
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

/** Action (toolbar) click: toggle capture. */
extBrowser.action.onClicked.addListener(async () => {
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

/** Return a human-friendly browser name for diagnostics. */
function getBrowserName() {
  const ua = (self.navigator && self.navigator.userAgent) || '';
  if (ua.includes('Firefox')) return 'Firefox';
  if (ua.includes('Edg/')) return 'Edge';
  if (ua.includes('Chrome')) return 'Chrome';
  if (ua.includes('Safari')) return 'Safari';
  return 'Unknown';
}

/** Health probe to Coyote Core (5000). */
let _coreUp = false;
let _lastProbe = 0;
const CORE_HEALTH_URL = 'http://127.0.0.1:5000/health';
async function isCoreAvailable() {
  const now = Date.now();
  if (now - _lastProbe < 3000) return _coreUp; // reuse result for 3s
  _lastProbe = now;
  try {
    // no-cors: we only need to know if the port is listening
    await fetch(CORE_HEALTH_URL, { method: 'GET', mode: 'no-cors', signal: AbortSignal.timeout(800) });
    _coreUp = true;
  } catch {
    _coreUp = false;
  }
  return _coreUp;
}

/** Persist status to storage.local (MV3 SW cannot use localStorage). */
async function updateLocalStatus() {
  const statusData = {
    active: true,
    timestamp: Date.now(),
    version: extBrowser.runtime.getManifest().version,
    browser: getBrowserName(),
    initialized: isInitialized
  };
  try {
    await extBrowser.storage.local.set({ coyote_extension_status: statusData });
  } catch (e) {
    console.error('Could not write to storage.local:', e);
  }
}

/** Lightweight probe to the UI server (8080). */
async function checkServerAvailability() {
  try {
    await fetch('http://127.0.0.1:8080/', {
      method: 'GET',
      mode: 'no-cors',
      signal: AbortSignal.timeout(1000)
    });
    return true;
  } catch {
    return false;
  }
}

/** Send data to local server endpoint with user-visible failure notification if needed. */
async function sendDataToServer(data, endpoint) {
  console.log("Sending data to server", { data, endpoint });
  if (!endpoint) {
    console.error("Endpoint is undefined", { data, endpoint });
    return;
  }
  if (!(await isCoreAvailable())) {
    console.debug('Coyote Core is not running; skipping send:', endpoint);
    return;
  }
  const url = `http://127.0.0.1:5000/${endpoint}`;
  console.log(`URL being called: ${url}`, JSON.stringify(data));
  try {
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(data)
    });
    if (!res.ok) {
      const text = await res.text().catch(() => '');
      throw new Error(`Network response was not ok: ${res.status} ${res.statusText} ${text}`);
    }
    const responseData = await res.json().catch(() => ({}));
    console.log('Success:', responseData);
  } catch (error) {
    console.error('Error:', error);
    // Prefer chrome.notifications in MV3; fall back to Web Notifications if needed.
    try {
      if (extBrowser.notifications && extBrowser.notifications.create) {
        await extBrowser.notifications.create({
          type: 'basic',
          iconUrl: extBrowser.runtime.getURL('icons/coyote-128.png'),
          title: 'Coyote Extension Error',
          message: 'Failed to send data to the server. Please ensure the server is running.'
        });
      } else if (self.registration && self.registration.showNotification) {
        await self.registration.showNotification('Coyote Extension Error', {
          body: 'Failed to send data to the server. Please ensure the server is running.',
          icon: extBrowser.runtime.getURL('icons/coyote-128.png')
        });
      }
    } catch (e) {
      // Ignore notification errors
    }
  }
}

/** Message bridge */
extBrowser.runtime.onMessage.addListener((message, sender, sendResponse) => {
  // Keep response channel open for async work if needed
  const finish = (status) => { try { sendResponse({ status }); } catch {} };
  if (!isInitialized) {
    // In MV3 the SW may awaken due to this message; treat it as initialized now.
    isInitialized = true;
  }
  (async () => {
    await pauseReady;
    if (isPaused) {
      finish('Capture paused; data discarded');
      return;
    }
    switch (message.type) {
      case 'pageLoaded':
        await sendDataToServer(message.data, 'webpage_visit');
        break;
      case 'hyperlinkClicked':
        await sendDataToServer(message.data, 'hyperlink_click');
        break;
    }
    finish('OK');
  })();

  return true; // async response
});

/** Heartbeat: use alarms instead of setInterval (SW may sleep). */
const HEARTBEAT_ALARM = 'coyoteHeartbeat';
const HEARTBEAT_PERIOD_MIN = 0.5; // 30 seconds is the modern minimum (Chrome ≥ 120).

async function sendHeartbeat() {
  // Always write status to storage
  await updateLocalStatus();

  // Ping the UI server if it is reachable
  if (await checkServerAvailability()) {
    const payload = {
      extensionId: SESSION_ID,
      version: extBrowser.runtime.getManifest().version,
      browserName: getBrowserName(),
      timestamp: new Date().toISOString(),
      status: 'active',
      initialized: isInitialized
    };
    try {
      await fetch('http://127.0.0.1:8080/extension_heartbeat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
    } catch (error) {
      console.debug('Server heartbeat failed, but storage was updated');
    }
  }
}

/** Register the alarm and listeners (must be in global scope for SW). */
function scheduleHeartbeat() {
  try {
    extBrowser.alarms.create(HEARTBEAT_ALARM, { periodInMinutes: HEARTBEAT_PERIOD_MIN });
  } catch (e) {
    console.warn('Failed to create heartbeat alarm:', e);
  }
}
// Fire on every alarm tick
extBrowser.alarms.onAlarm.addListener((alarm) => {
  if (alarm && alarm.name === HEARTBEAT_ALARM) sendHeartbeat();
});

// Initialize promptly on SW start
function initializeExtension() {
  console.log("Initializing extension (MV3 SW)...");
  isInitialized = true; // avoid dropping early messages on cold start
  // Kick an immediate heartbeat and schedule periodic ones.
  sendHeartbeat();
  scheduleHeartbeat();
}
initializeExtension();

// Re-arm heartbeats across SW lifecycles.
extBrowser.runtime.onInstalled.addListener(() => {
  scheduleHeartbeat();
  sendHeartbeat();
});
extBrowser.runtime.onStartup.addListener(() => {
  scheduleHeartbeat();
  sendHeartbeat();
});

// Note: There is no reliable onSuspend in MV3 SW; do not rely on it. Cleanup is best-effort via alarms or on next start.
