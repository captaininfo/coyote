// new_tab_ui.js — Flask-served "new tab" (no extension dependencies)

document.addEventListener("DOMContentLoaded", () => {
  const purposeInput = document.getElementById("purposeInput");
  const searchInput  = document.getElementById("searchTermsInput");
  const searchButton = document.getElementById("searchButton");
  const recentList   = document.getElementById("recentPurposesList");
  const searchForm   = document.getElementById("searchForm");

  function updateButtonState() {
    const ok = !!purposeInput.value.trim() && !!searchInput.value.trim();
    searchButton.disabled = !ok;
    searchButton.setAttribute("aria-disabled", String(!ok));
  }

  // local recent purposes
  function loadRecent() {
    const recents = JSON.parse(localStorage.getItem("recentPurposes") || "[]");
    recentList.innerHTML = "";
    recents.forEach((p) => {
      const li = document.createElement("li");
      li.textContent = p;
      li.tabIndex = 0;
      li.addEventListener("click", () => { purposeInput.value = p; updateButtonState(); });
      li.addEventListener("keydown", (e) => { if (e.key === "Enter") { purposeInput.value = p; updateButtonState(); } });
      recentList.appendChild(li);
    });
  }

  function saveRecent(purpose) {
    const max = 10;
    const p = purpose.trim();
    let list = JSON.parse(localStorage.getItem("recentPurposes") || "[]");
    list = [p, ...list.filter(x => x.toLowerCase() !== p.toLowerCase())].slice(0, max);
    localStorage.setItem("recentPurposes", JSON.stringify(list));
  }

  async function postInit(purpose, searchTerms) {
    try {
      const res = await fetch("/api/search/init", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
          timestamp: new Date().toISOString(),
          purpose,
          searchTerms
        })
      });
      // We don't block on this; COY-20 acceptance only requires "submits with no console errors"
      await res.json().catch(() => ({}));
    } catch (e) {
      // Intentionally swallow: core may be down; we still allow the redirect
      console.debug("init send failed (non-fatal):", e);
    }
  }

  async function handleSearch(e) {
    e?.preventDefault();
    if (searchButton.disabled) return;

    const purpose = purposeInput.value.trim();
    const terms   = searchInput.value.trim();

    saveRecent(purpose);
    loadRecent();

    // fire-and-forget server submission
    await postInit(purpose, terms);

    // NAVIGATION DISABLED FOR MVP (privacy + de-Google).
    //
    // This previously redirected the typed query to Google Search. It was removed
    // because (a) the whole Coyote Search box is disabled for the MVP — it recorded
    // Purpose/SearchTerms even when capture was paused or in a private window (see
    // the CLAUDE.md search-box Known Issue), and its server endpoint now returns 410
    // (ui/coyote_ui_server.py :: api_search_init); and (b) hardcoding one search
    // engine is a poor fit for a privacy-first tool.
    //
    // RESTORE (post-MVP, behind the Option-4' pause/consent gate): re-enable the
    // endpoint (see its RESTORE note), unhide the button in wireframe_v2.html, then
    // reinstate navigation here as a USER-CONFIGURABLE engine — a registry of
    // name->URL-template plus a custom-template field for self-hosted engines
    // (e.g. SearXNG) — never a hardcoded redirect. The capture logic above
    // (saveRecent + postInit) is preserved intact for that restoration.
  }

  // wire up
  purposeInput.addEventListener("input", updateButtonState);
  searchInput.addEventListener("input", updateButtonState);
  [purposeInput, searchInput].forEach((el) => {
    el.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !searchButton.disabled) handleSearch(e);
    });
  });

  searchButton.addEventListener("click", handleSearch);
  if (searchForm) searchForm.addEventListener("submit", handleSearch);

  // boot
  loadRecent();
  updateButtonState();
});
