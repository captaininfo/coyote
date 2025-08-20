// content.js

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
    throw new Error('Browser API not found');
}

const extBrowser = getBrowserNamespace();

/**
 * Sends information about the page when it is loaded.
 */
function sendPageLoadInfo() {
    const pageData = {
        url: window.location.href,
        title: document.title,
        timestamp: new Date().toISOString()
    };

    // Send page load information to the background script
    extBrowser.runtime.sendMessage({ type: 'pageLoaded', data: pageData }, function(response) {
        if (extBrowser.runtime.lastError) {
            console.error('Error sending page load info:', extBrowser.runtime.lastError);
        }
    });
}

/**
 * Listens for clicks on any hyperlink and sends relevant data back.
 */
function captureHyperlinkClicks() {
    document.body.addEventListener('click', function(event) {
        let target = event.target;

        // Traverse up the DOM tree to find the nearest <a> ancestor
        while (target && target !== document.body) {
            if (target.tagName && target.tagName.toLowerCase() === 'a' && target.href) {
                const linkData = {
                    sourceURL: window.location.href,
                    destinationURL: target.href,
                    linkText: target.textContent.trim(),
                    timestamp: new Date().toISOString()
                };

                // Send hyperlink click information to the background script
                extBrowser.runtime.sendMessage({ type: 'hyperlinkClicked', data: linkData }, function(response) {
                    if (extBrowser.runtime.lastError) {
                        console.error('Error sending hyperlink click info:', extBrowser.runtime.lastError);
                    }
                });

                break; // Stop traversing once the link is found
            }
            target = target.parentElement;
        }
    }, false);
}

// Attach to Coyote UI's New Tab when served from Flask
(function attachToCoyoteNewTab() {
  const isLocalUI = (location.hostname === 'localhost' || location.hostname === '127.0.0.1');
  const isNewTabHtml = location.pathname.endsWith('/static/new_tab.html') ||
                       document.querySelector('meta[name="coyote-new-tab"]');
  if (!isLocalUI || !isNewTabHtml) return;

  window.addEventListener('message', (e) => {
    if (e.source !== window) return;
    const msg = e.data || {};
    if (msg.type === 'COYOTE_PAGE_READY') {
      // Confirm we’re listening
      try { window.postMessage({ type: 'COYOTE_CONTENT_SCRIPT_READY' }, '*'); } catch {}
    }
    if (msg.type === 'COYOTE_TRACK') {
      // Forward tracking to background.js (adapt to your schema)
      try { browser.runtime.sendMessage({ type: 'coyote-track', payload: msg.payload }); } catch {}
    }
  }, false);
})();

/**
 * Initializes the content script.
 */
function initContentScript() {
    sendPageLoadInfo();
    captureHyperlinkClicks();
}

// Initialize the content script once the DOM is fully loaded
if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", initContentScript);
} else {
    initContentScript();
}
