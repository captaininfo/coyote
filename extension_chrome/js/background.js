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

/** Shorthand so existing callers keep working. */
function generateSessionId() {
  return SESSION_ID;
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
async function clearLocalStatus() {
  try {
    await extBrowser.storage.local.remove('coyote_extension_status');
  } catch (e) {
    console.error('Could not clear storage.local:', e);
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
          iconUrl: extBrowser.runtime.getURL('icons/coyote-75.png'),
          title: 'Coyote Extension Error',
          message: 'Failed to send data to the server. Please ensure the server is running.'
        });
      } else if (self.registration && self.registration.showNotification) {
        await self.registration.showNotification('Coyote Extension Error', {
          body: 'Failed to send data to the server. Please ensure the server is running.',
          icon: extBrowser.runtime.getURL('icons/coyote-75.png')
        });
      }
    } catch (e) {
      // Ignore notification errors
    }
  }
}

/** Build context menus (call after removeAll to avoid duplicates). */
function createContextMenus() {
  // In MV3, use "action" instead of "browser_action" as a context.
  extBrowser.contextMenus.create({
    id: "coyote-search",
    title: "Coyote search",
    contexts: ["page", "action"]
  });
  extBrowser.contextMenus.create({
    id: "coyote-search-new-tab",
    title: "Coyote search in new tab",
    contexts: ["page", "action"]
  });
  extBrowser.contextMenus.create({
    id: "connect-hypothesis",
    title: "Connect to Hypothes.is",
    contexts: ["action"]
  });
}

/** Rebuild menus on install/startup (SW can restart many times). */
async function rebuildContextMenus() {
  try {
    // Chrome 123+ returns a Promise for removeAll()
    await extBrowser.contextMenus.removeAll();
  } catch { /* ignore */ }
  createContextMenus();
}

// Handle context menu clicks
extBrowser.contextMenus.onClicked.addListener((info, tab) => {
  switch (info.menuItemId) {
    case "coyote-search":
      if (tab && tab.id != null) {
        extBrowser.tabs.update(tab.id, { url: extBrowser.runtime.getURL("html/new_tab.html") });
      } else {
        extBrowser.tabs.create({ url: extBrowser.runtime.getURL("html/new_tab.html") });
      }
      break;
    case "coyote-search-new-tab":
      extBrowser.tabs.create({ url: extBrowser.runtime.getURL("html/new_tab.html") });
      break;
    case "connect-hypothesis":
      extBrowser.tabs.create({ url: extBrowser.runtime.getURL("html/connect_hypothesis.html") });
      break;
  }
});

/** Message bridge */
extBrowser.runtime.onMessage.addListener((message, sender, sendResponse) => {
  // Keep response channel open for async work if needed
  const finish = (status) => { try { sendResponse({ status }); } catch {} };
  if (!isInitialized) {
    // In MV3 the SW may awaken due to this message; treat it as initialized now.
    isInitialized = true;
  }
  (async () => {
    switch (message.type) {
      case 'pageLoaded':
        await sendDataToServer(message.data, 'webpage_visit');
        break;
      case 'hyperlinkClicked':
        await sendDataToServer(message.data, 'hyperlink_click');
        break;
      case 'searchInitiated':
        await sendDataToServer(message.data, 'init_search');
        break;
      case 'fetchHypothesisData':
        try {
          const r = await fetch('http://127.0.0.1:5000/fetch_hypothesis_data', { method: 'GET' });
          console.log('Fetched Hypothesis data:', await r.json().catch(() => ({})));
        } catch (e) {
          console.error('Error fetching data:', e);
        }
        break;
      case 'coyote-track':
        // TODO: forward to Coyote Core API or buffer locally
        // e.g., fetch('http://127.0.0.1:5000/track', { method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(message.payload) })
        break;
    }
    finish('OK');
  })();

  return true; // async response
});

/** Action (toolbar) click: open new_tab page.  MV3 uses `action` API; keep fallback for Firefox. */
(extBrowser.action || extBrowser.browserAction).onClicked.addListener(() => {
  extBrowser.tabs.create({ url: extBrowser.runtime.getURL("html/new_tab.html") });
});

/** Heartbeat: use alarms instead of setInterval (SW may sleep). */
const HEARTBEAT_ALARM = 'coyoteHeartbeat';
const HEARTBEAT_PERIOD_MIN = 0.5; // 30 seconds is the modern minimum (Chrome ≥ 120).

async function sendHeartbeat() {
  // Always write status to storage
  await updateLocalStatus();

  // Ping UI and Core if UI server is reachable
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
      // Best-effort to Core as well
      fetch('http://127.0.0.1:5000/extension_heartbeat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      }).catch(() => {});
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

// Maintain menus & schedule heartbeats across SW lifecycles.
extBrowser.runtime.onInstalled.addListener(() => {
  rebuildContextMenus();
  scheduleHeartbeat();
  sendHeartbeat();
});
extBrowser.runtime.onStartup.addListener(() => {
  rebuildContextMenus();
  scheduleHeartbeat();
  sendHeartbeat();
});

// Note: There is no reliable onSuspend in MV3 SW; do not rely on it. Cleanup is best-effort via alarms or on next start.
