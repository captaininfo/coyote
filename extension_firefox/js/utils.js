// utils.js

/**
 * Gets the appropriate browser namespace for cross-browser compatibility.
 * @returns {object} The browser namespace object.
 * @throws {Error} If no suitable browser API is found.
 */
function getBrowserNamespace() {
    if (typeof browser !== 'undefined') {
        return browser; // Firefox
    } else if (typeof chrome !== 'undefined') {
        return chrome; // Chrome
    }
    throw new Error('Browser API not found');
}

const extBrowser = getBrowserNamespace();

/**
 * Helper function to send data to the server via a POST request.
 * @param {string} url - The server URL to send the data to.
 * @param {object} data - The data to send in the POST request.
 * @returns {Promise<object>} A promise that resolves with the server's response data.
 */
function postData(url, data) {
    return fetch(url, {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
        },
        body: JSON.stringify(data)
    })
    .then(response => {
        if (!response.ok) {
            return response.text().then(text => {
                throw new Error(`Server responded with status ${response.status}: ${text}`);
            });
        }
        return response.json();
    });
}

/**
 * Helper function to format a Date object as an ISO string.
 * @param {Date} date - The date to format.
 * @returns {string} The ISO-formatted date string.
 */
function formatDateTime(date) {
    return date.toISOString();
}

/**
 * Helper function to retrieve data from the browser's local storage.
 * @param {string} key - The key of the data to retrieve.
 * @returns {Promise<any>} A promise that resolves with the retrieved data.
 */
function getStoredData(key) {
    if (extBrowser.storage.local.get.length === 1) {
        // API returns a Promise (Firefox)
        return extBrowser.storage.local.get(key).then(result => result[key]);
    } else {
        // API uses callbacks (Chrome)
        return new Promise((resolve, reject) => {
            extBrowser.storage.local.get([key], function(result) {
                if (extBrowser.runtime.lastError) {
                    reject(extBrowser.runtime.lastError);
                } else {
                    resolve(result[key]);
                }
            });
        });
    }
}

/**
 * Helper function to save data to the browser's local storage.
 * @param {string} key - The key under which to save the data.
 * @param {any} data - The data to save.
 * @returns {Promise<void>} A promise that resolves when the data is saved.
 */
function saveData(key, data) {
    if (extBrowser.storage.local.set.length === 1) {
        // API returns a Promise (Firefox)
        return extBrowser.storage.local.set({ [key]: data });
    } else {
        // API uses callbacks (Chrome)
        return new Promise((resolve, reject) => {
            extBrowser.storage.local.set({ [key]: data }, function() {
                if (extBrowser.runtime.lastError) {
                    reject(extBrowser.runtime.lastError);
                } else {
                    resolve();
                }
            });
        });
    }
}
