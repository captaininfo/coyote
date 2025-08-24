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

  // local recent purposes (COY-21 will move to SQLite)
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
    postInit(purpose, terms);

    // navigate
    window.location.href = `https://www.google.com/search?q=${encodeURIComponent(terms)}`;
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
