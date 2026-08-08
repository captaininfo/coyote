# Coyote Browser Extension (Chrome / Edge)

Manifest V3 build of the Coyote Browser Extension. Behaviour, data collected and privacy
guarantees are identical to the Firefox build — see [`../extension_firefox/README.md`](../extension_firefox/README.md)
for the full description.

Chrome-specific differences:

- Manifest V3 service worker instead of an MV2 event page; the heartbeat is driven by
  `chrome.alarms` rather than `setInterval`.
- Pause state is stored in `chrome.storage.local` (an MV3 service worker has no `localStorage`).
- Chrome has no equivalent of Firefox's `data_collection_permissions` framework, so the
  heartbeat always carries its diagnostic fields. Everything it sends is documented in the
  Firefox README's data table.

## Load for local testing
1. Go to `chrome://extensions/`.
2. Enable **Developer mode**.
3. Click **Load unpacked** and select this directory.

## License
[GPLv3](https://www.gnu.org/licenses/gpl-3.0.en.html)
