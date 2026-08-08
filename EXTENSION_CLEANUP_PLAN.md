# Coyote Browser-Extension Cleanup & Mozilla-Signing Readiness — Consolidated Plan

**Status:** GREENLIT and **IMPLEMENTED 2026-07-23** (Tiers 1-4 + Pause bundle + manifest block
+ description + READMEs). Remaining: pick the gecko `id`, update `web-ext`/`addons-linter`,
and run §9 verification (needs Justin — browser-driven). See §10 for the as-built delta.
Prepared 2026-07-22 (Opus). Line numbers below are pre-implementation snapshots.

Scope: `extension_firefox/` (MV2) and `extension_chrome/` (MV3). The Mozilla-signing
work (Track E) targets the **Firefox unlisted** extension; Chrome Web Store submission is
a separate, out-of-scope effort. Hygiene changes apply to BOTH extensions in parallel.

---

## 0. Decisions LOCKED this cycle (Justin + Sonnet + Opus)

- **`data_collection_permissions` (Firefox manifest only):**
  `required: ["browsingActivity", "websiteContent"]`, `optional: ["technicalAndInteraction"]`.
  - `browsingActivity` — URLs of pages visited + links followed (both captured unconditionally).
  - `websiteContent` — the extension captures anchor `linkText` (`content.js:50`,
    `target.textContent.trim()`), and Mozilla's category text literally names "text… and links."
    Justin's call (honesty-as-strategy). ACCURATE, not over-declaration.
  - `technicalAndInteraction` — heartbeat diagnostics (browser/version/session id). FORCED
    optional by Mozilla. **Carries a gating obligation** (see §5, heartbeat field-gate).
  - **`searchTerms` EXCLUDED** — Tier-4 deletes the search code path; declaring it would be false.
  - **Why unconditionally-captured ⇒ `required`:** optional data perms (except
    `technicalAndInteraction`) are NOT shown at install and NOT granted by default; they need
    `permissions.request()`. So `optional` = OFF by default → unconditional capture without
    `required` = collecting non-consented data. Verified against Extension Workshop docs.
- **`strict_min_version: "140.0"`** (Firefox) — decouples the Pause button from signing (FF 140+
  ships the built-in data-consent prompt, satisfying the consent affordance requirement).
- **Corrected `technicalAndInteraction` design (VERIFIED against code, 2026-07-22):** the
  heartbeat POST always fires; only the diagnostic FIELDS are gated on `getAll()`. Declining does
  NOT break Coyote — `/extension_status.active` depends only on a recent POST, never on those
  fields (server `coyote_ui_server.py:248,270,278`; client `coyote_ui.js:112,380`,
  `wireframe_v2.html:1890`). **No "declining breaks Coyote" README warning needed** (and omitting
  it avoids coercing consent to an optional toggle — better for Mozilla compliance).
- **Manifest-vs-prose boundary:** the manifest enum declares only what the EXTENSION CODE does
  (URL + link text). Core's independent trafilatura scraping is NOT an extension capability →
  it goes in PROSE (README + reviewer notes), never the manifest.
- **Transmission definition:** fetch to `127.0.0.1` (Core :5000 / UI :8080) IS transmission under
  Mozilla's "outside the add-on or the local browser." Loopback shields the OFF-DEVICE
  exfiltration/encryption clauses, NOT the disclosure key. "Nothing leaves your device" stays as
  privacy reassurance only, never as disclosure-avoidance.

---

## 1. Tier 1 — dead code (zero-risk, both extensions)

Firefox (`extension_firefox/`):
- **Delete `js/utils.js`** — entirely dead (`postData`/`getStoredData`/`saveData`, 0 call sites).
- **Remove the `:5000` heartbeat** in `background.js` (~301-305) — the 404 spammer. **Keep the
  `:8080` heartbeat** (~295-299; UI status light uses it).
- **Remove the dead `coyote-track` onMessage listener** (~341-347, TODO stub, commented-out fetch).
- **Remove the dead `fetchHypothesisData` onMessage case** (~127-135) — GET to Core's legacy
  `/fetch_hypothesis_data`. VERIFIED dead: `fetchHypothesisData` exists ONLY as a `case` label
  repo-wide; nothing dispatches the message (Hypothesis moved to the UI Integrations tab). Core's
  legacy GET route stays (Core concern, out of scope for extension cleanup).
- **Remove the full `attachToCoyoteNewTab` IIFE** in `content.js` (~69-87) — proven dead
  (activates on `ui/static/new_tab.html:6` meta, but nothing in `ui/` posts
  `COYOTE_PAGE_READY`/`COYOTE_TRACK`).
- **Remove dangling `connect_hypothesis` references** (files DON'T EXIST): `background.js`
  context-menu create (~83-87) + onClicked case (~99-102); manifest `web_accessible_resources`
  lines 39 (`html/connect_hypothesis.html`) and 42 (`js/connect_hypothesis.js`).

Chrome (`extension_chrome/`) — parallel:
- **Remove the `:5000` heartbeat** (~261-266). **Keep the `:8080` heartbeat** (~256-260).
- **Remove the dead `coyote-track` case** (~221-224).
- **Remove the dead `fetchHypothesisData` case** (~213-219) — same verified-dead pattern.
- **Remove dangling `connect_hypothesis`** context-menu create (~159-163) + onClicked case
  (~188-190). NOTE: Chrome manifest has NO `connect_hypothesis` WAR entry (no-op there — unlike
  FF's two dangling entries).
- **Remove the `attachToCoyoteNewTab` IIFE** in `extension_chrome/js/content.js` — CONFIRMED
  present (identical to FF except one `browser.`/`extBrowser.` line; still read before editing).
- NOTE: Chrome `background.js` USES `storage.local` (`:71,:78`) — do NOT touch storage.

---

## 2. Tier 2 — permissions (both extensions)

- **Remove `webNavigation`** (0 uses) and **`activeTab`** (0 uses) from BOTH manifests.
- **KEEP `storage`** in BOTH (changed per Sonnet): Chrome uses it now (`:71,:78`), and Pause will
  persist its flag via `storage.local` in both. Stripping now = churn + would break Pause.
- `contextMenus` / `notifications` stay (still used). Chrome keeps `alarms` (drives heartbeat).

---

## 3. Tier 3 — host-permission narrowing (Firefox, TEST after)

- **Firefox manifest:** narrow the `permissions` host entry `*://*/*` →
  `http://127.0.0.1/*` + `http://localhost/*`. (Chrome `host_permissions` is already scoped.)
- **Content-script `matches: ["<all_urls>"]` STAYS** — that's the disclosed primary function
  (capture on every page). Only the fetch-target host permission narrows.
- **Rollback this item alone** if capture breaks in testing.

---

## 4. Tier 4 — search-box deprecation (APPROVED, product sign-off given, both extensions)

- **Delete** `html/new_tab.html`, `css/new_tab.css`, `js/new_tab.js` and their
  `web_accessible_resources` entries.
- **Remove** the two `coyote-search*` context menus + their onClicked cases.
- **Remove** the `searchInitiated` onMessage case (drops `init_search` transmission + the Google
  redirect FROM THE SUBMITTED EXTENSION — dissolves the clause-6 search-terms exposure and lets
  us drop `searchTerms` from the manifest).
- **Toolbar action NOT deleted — REPURPOSED for Pause** (see §5). Deleting it while leaving the
  handler would create a dangling ref.
- Search remains available in the Flask UI (`ui/static/new_tab.html`, independent of the
  extension) — no capability lost.

---

## 5. Pause + heartbeat-gate + private-browsing guard (ONE bundle, both extensions)

Shared implementation machinery (`browser.permissions` + a stored flag + capture gates), exposed
as TWO independent user controls.

**A. Pause (our toolbar button):**
- Repurpose the toolbar `action`/`browserAction` `onClicked` from "open new_tab" → "toggle pause";
  reflect state in the toolbar icon.
- Persist the pause flag in `storage.local`.
- Gate capture in `content.js`: skip `sendPageLoadInfo()` and the `hyperlinkClicked` send when paused.

**B. Private-browsing guard (fold into the same capture gate):**
- In `content.js`, skip capture when `browser.extension.inIncognitoContext` (FF default is already
  protective; this makes it explicit).

**C. Heartbeat field-gate (Firefox `technicalAndInteraction` obligation — VERIFIED design):**
- The heartbeat POST **always fires on schedule** (keeps the status light alive).
- Before building the payload, read `browser.permissions.getAll()`; if its `data_collection`
  array does NOT include `technicalAndInteraction`, send a **bare body** (`{status, timestamp}`).
  If it DOES, add `browserName` / `version` / `extensionId`.
- Verified safe: `/extension_status.active` depends only on POST arrival, never on those fields
  (§0). Declining loses nothing functional.
- (Optional nicety: Pause may also suppress the heartbeat while paused — additive, not a
  replacement for the `getAll()` gate.)

Chrome has no `data_collection_permissions` framework, so the heartbeat field-gate is a
Firefox-specific behavior; Chrome keeps its current heartbeat body. Pause + private-browsing
guard apply to both.

---

## 6. Firefox manifest — new `browser_specific_settings` block

Add (Firefox only — Chrome MV3 doesn't use this):

```json
"browser_specific_settings": {
  "gecko": {
    "id": "coyote@<choose-a-domain>",
    "strict_min_version": "140.0",
    "data_collection_permissions": {
      "required": ["browsingActivity", "websiteContent"],
      "optional": ["technicalAndInteraction"]
    }
  }
}
```
- `id` is independently required for unlisted signing (pick a stable value, e.g. `coyote@…`).
- Verify the exact JSON placement is accepted by a CURRENT `addons-linter` (see §8).

---

## 7. Submission documents (draft → Justin + Sonnet review → create in repo)

- **Manifest `description` rewrite (both):** current FF `description` (line 5) still says
  "tracking search purposes and interactions" — FALSE post-Tier-4. Final (131 chars, under
  Chrome's 132 cap, accurate, privacy-reassurance framing):
  `Records the pages you visit and links you click, sending them only to your local Coyote app (127.0.0.1)—nothing leaves your device.`
- **Extension `README` rewrite:** drop the `init_search` row from the data table; update the
  permissions list; state the source-code checklist answers ("no build step; plain JS; load
  as-is"); add Sonnet's holistic sentence, e.g. *"The Coyote application uses the URLs this
  extension reports to independently fetch and analyze the full content of the pages you visit,
  building your personal knowledge graph."*; note `technicalAndInteraction` is optional
  diagnostics whose decline costs no functionality.
- **Reviewer notes:** loopback-only (no third party, no off-device); encryption-in-transit N/A to
  127.0.0.1; own-search-box (not interception); "do NOT build de-Google Level 2."
- **Reviewer note — the one accepted lint warning.** `KEY_FIREFOX_ANDROID_UNSUPPORTED_BY_MIN_VERSION`
  fires because `gecko.strict_min_version` is 140 while Android gained
  `data_collection_permissions` in 142. It is **spurious for this add-on**: with no
  `gecko_android` object the extension is desktop-only, per Mozilla's own guidance ("To stop
  marking your extension as Android compatible on AMO, ensure that your manifest.json file does
  not include a `browser_specific_settings.gecko_android` object"). The warning rests on the
  linter inferring an Android target that we do not declare. Accepted deliberately — see §10.

---

## 8. Implementation order + pre-submission

1. Tier 1 (dead code) — both extensions.
2. Tier 2 (permissions) — both.
3. Tier 3 (FF host narrow) — then TEST capture.
4. Tier 4 (search-box deprecation) — both.
5. Pause + private-browsing guard + heartbeat field-gate bundle — both (field-gate FF-only).
6. FF manifest `browser_specific_settings` block (§6).
7. Description rewrite (both manifests) (§7).
8. Create extension `README` (§7).
9. **Pre-submission:** update `web-ext` / `addons-linter` to CURRENT (avoids the spurious
   `DATA_COLLECTION_PERMISSIONS_PROP_RESERVED`, addons-linter #5845); confirm a clean lint;
   (optional, later) verify Matrix room access if posting there.

## 9. Verification (after implementation, temp-load the FF extension)

- Browse a few pages → confirm `webpage_visit` + `hyperlink_click` still land in Core.
- Status light turns green (heartbeat to :8080).
- Click Pause → browse → confirm NO capture events; unpause → capture resumes.
- Simulate `technicalAndInteraction` declined → heartbeat body is bare → status STILL active.
- `addons-linter` (current) reports no errors on the FF package.
- Private/incognito window → confirm no capture.

---

## 10. AS-BUILT delta (2026-07-23) — what changed vs. the plan text above

Three defects the plan had NOT caught, found while re-verifying line numbers, plus two
permissions that the plan's own Tier-2 rule ("0 uses ⇒ remove") only turned dead *after*
Tiers 1 and 4 removed their call sites:

1. **FF `options_ui` pointed at a nonexistent file** (`html/options.html` was never created).
   An `addons-linter` error waiting to happen. **Removed the whole `options_ui` key.**
2. **FF `browser_action.default_icon` pointed at nonexistent files** (`icons/icon16.png`,
   `icons/icon48.png`; the icons dir only has `coyote-50/75/100.png`). **Repointed** to the
   real assets — load-bearing, because Pause now lives on that button.
3. **Chrome manifest had NO `action` key at all**, while `background.js:233` did
   `(extBrowser.action || extBrowser.browserAction).onClicked` — in MV3 `chrome.action` is
   undefined unless the key is declared, so that line threw on every service-worker start.
   Pre-existing live bug; **added the `action` key** (also required for Pause).
4. **`contextMenus` and `tabs` removed from BOTH manifests.** Every `contextMenus.create` and
   every `tabs.create/update` call site sat inside code Tier 1 or Tier 4 deletes; verified zero
   remaining uses. Net permissions: FF `storage` + `notifications` + the two loopback hosts;
   Chrome the same plus `alarms`.
5. **`web_accessible_resources` dropped entirely from both.** After Tier 4 the only surviving
   entry was `images/*`, referenced solely by the deleted `new_tab.html`/`new_tab.css`. The
   `images/` directories were deleted with them. (`icons/coyote_mosiac.png` is now unreferenced
   too — harmless, left in place.)

**Lint status: `errors 0 / notices 0 / warnings 1`** (`npx web-ext@latest lint
--source-dir=extension_firefox`). The one warning is accepted on purpose — see below.

**REVERSED DECISION — `gecko_android` (2026-07-23, Sonnet caught this, Opus verified):** an
earlier pass added `"gecko_android": {"strict_min_version": "142.0"}` to silence
`KEY_FIREFOX_ANDROID_UNSUPPORTED_BY_MIN_VERSION`. **That was wrong and has been reverted.**
Per Mozilla's Add-ons blog, presence of the `gecko_android` object is precisely what *marks an
extension Android-compatible on AMO* — so the "fix" made the manifest assert a capability Coyote
cannot have (it needs a Docker daemon on the same machine; Firefox for Android can't reach a
desktop's loopback). Measured, all three variants:

| variant | warnings | manifest accurate? |
|---|---|---|
| A — no `gecko_android`, `gecko` min **140** | 1 | yes (desktop-only) |
| B — `gecko_android` 142, `gecko` min 140 | 0 | **NO — false Android claim** |
| C — no `gecko_android`, `gecko` min **142** | 0 | yes |

**Shipped: A.** B is disqualified on accuracy. C is also accurate and lints clean, but was
rejected because bumping the floor to 142 would block **every current ESR user** — ESR 140 is
the ESR in service today (the next is ~152), and institutional/library Firefox is exactly
Coyote's audience. Paying real installability to silence a cosmetic warning is the wrong trade;
warnings do not block signing. The warning is documented for reviewers in §7.

Other as-built notes:
- **Dead symbols swept while editing:** FF `generateSessionId()` (no callers), FF
  `serverAvailable` (write-only), Chrome `generateSessionId()` and `clearLocalStatus()`
  (no callers in MV3 — there is no reliable `onSuspend`).
- **Pause gate lives in `background.js`, not `content.js`** (plan §5A said content). The
  background is the authoritative gate because the pause flag load is async: the handler
  `await`s a module-level `pauseReady` promise before dispatching, so a message arriving during
  a cold event-page wake-up can never slip through. Reads fail **closed** (`isPaused = true` on
  a storage error). `storage.onChanged` keeps it in sync across windows.
- **Private-browsing guard lives in `content.js`** (`extension.inIncognitoContext`, synchronous,
  needs no permission), and aborts `initContentScript` outright — nothing is even observed.
- **`isInitialized`'s 5-second startup delay was left alone** (FF only) — out of scope, but note
  it still silently drops events for 5s after every event-page wake.
- **Verified: no capability lost by Tier 4.** `ui/coyote_ui_server.py:633-660` forwards the UI's
  own search form to Core's `/init_search` independently of the extension.
- **Left in place, flagged as debt:** the `coyote_extension_status` local status blob (FF
  `localStorage`, Chrome `storage.local`) is written every heartbeat and **read by nothing**
  anywhere in the repo. Local-only, so no compliance implication; a candidate deletion later.

## 11. DEFERRED follow-ups — each to land ALONE, in its own commit (Justin's call, 2026-07-23)

Both were surfaced during the Tier 1-4 work and are believed safe to delete, but they are
**deliberately NOT bundled** with it. Rationale (Justin): if a capture regression shows up after
this batch, the cause must be unambiguous — a bundled "harmless cleanup" is exactly what turns a
five-minute diagnosis into an afternoon of bisecting. Ship each on its own, test, then move on.

**11a. Delete the `coyote_extension_status` blob.**
- Scope: FF `background.js` — `updateLocalStatus()`, `clearLocalStatus()`, `LOCALSTORAGE_KEY` and
  their call sites (`localStorage`); Chrome `background.js` — `updateLocalStatus()` only
  (`storage.local`; its `clearLocalStatus()` was already swept as a dead symbol, no caller in MV3).
- Verified: written on every heartbeat, **read by nothing** anywhere in the repo (grepped across
  `extension_*`, `ui/`, `images/`). Local-only, so no disclosure implication — but it does retain
  browser name + version on disk for no consumer, which is a thing a Mozilla reviewer can read.
- Test after: status light still goes green; heartbeat still POSTs; no console errors on load.

**11b. Remove the FF 5-second `isInitialized` delay** (`background.js`, `initializeExtension`).
- Current effect: for 5s after every event-page wake, messages are answered
  `'Extension initializing, data ignored'` and dropped. A non-persistent MV2 background suspends
  whenever idle, so this window reopens repeatedly during normal browsing.
- Proposed change: set `isInitialized = true` at load, which is what the Chrome MV3 build already
  does (`initializeExtension`: "avoid dropping early messages on cold start").
- **Justin recalls the delay was added to fix a real problem; git cannot confirm or refute it** —
  it is present in `9e0d7b4`, the commit that first added the file, so there is no separate fix
  commit to read. Treat the recollection as live: change it alone and watch for whatever it was.
- Test after: cold-start the browser and immediately load a page → the visit reaches Core; reload
  the extension and immediately load a page → same; watch for duplicate or malformed events.

## 12. §9 VERIFICATION RESULTS (2026-07-23, Justin temp-loaded FF)

PASS (extension-owned capture): webpage_visit lands as Webpage nodes; hyperlink_click emitted
(confirmed in the extension's background console — Core has no per-event UI readout); status light
green; **Pause stops webpage capture, unpause resumes**; **incognito suppresses webpage capture**;
no console errors; heartbeat 404 spam gone. Core of the work verified.

Findings + dispositions:
1. **`name` was "Coyote V2"** → renamed to **"Coyote Browser Extension"** in both manifests
   (display-only, zero-risk; matches README). DONE.
2. **"Can always read and change data on this site"** — Firefox-generated from the `<all_urls>`
   content script; describes content-script *capability* (DOM write is possible), not our behavior.
   MV2 has no read-only content-script declaration. NOT fixable in-manifest; honesty lives in
   README/reviewer prose (already there). "Run extension" / "Can't read or change data on
   about:debugging" are likewise Firefox's own labels. No action.
3. **Pause button buried in the Extensions overflow menu** — modern Firefox does not auto-pin, and
   an extension CANNOT self-pin. Pinned to the toolbar, the OFF badge + updating tooltip work as
   designed. Disposition: **README setup step** ("Pin the Coyote button to your toolbar"). TODO in
   README (not yet written).
4. **Pause + incognito do NOT gate the UI search box** (Purpose/SearchTerms still minted). Root
   cause verified in code: UI-server pipeline independent of the extension. **DOCUMENTED as a
   CLAUDE.md Known Issue; fix is post-MVP UI-server work, isolated commit** (Justin's call). See
   §11-style discipline — do NOT bundle the UI-server fix into extension work.
5. **incognito→normal session bleed** (search session attributed across the private-window
   boundary) — already covered by the "single-linear-browsing-history assumption" +
   "abandoned-search edge misattribution" Known Issues. Out of scope, post-MVP (session IDs).

Still open: write the extension README's "pin to toolbar" step (finding 3); the
`technicalAndInteraction`-declined sub-test (see §9 note — needs the about:addons data-collection
toggle, which may be absent for a temp add-on; code path is simple and read-verifiable).

## Open / deferred (not blocking implementation)
- Pick the concrete `id` domain string.
- Chrome Web Store data-disclosure (separate track, out of scope now).
- Matrix handle for Add-ons rooms (Justin's call, later; room-access rules vary).
- Email exposure was a Matrix-chat concern, NOT signing — resolved, no action.
