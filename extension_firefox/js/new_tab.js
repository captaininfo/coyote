// new_tab.js  –  v2  (restore search functionality)

/* global chrome, browser */
document.addEventListener("DOMContentLoaded", () => {
  /* ---------- 1.  Cross-browser namespace ---------- */
  const extBrowser =
    typeof browser !== "undefined"
      ? browser
      : typeof chrome !== "undefined"
      ? chrome
      : (() => {
          throw new Error("Browser API not found");
        })();

  /* ---------- 2.  DOM refs ---------- */
  const purposeInput = document.getElementById("purposeInput");
  const searchInput = document.getElementById("searchTermsInput");
  const searchButton = document.getElementById("searchButton");
  const recentPurposesContainer = document.getElementById("recentPurposesList");
  const searchForm = document.getElementById("searchForm");

  /* ---------- 3.  Enable / disable button ---------- */
  function updateButtonState() {
    const blocked = !purposeInput.value.trim() || !searchInput.value.trim();
    searchButton.disabled = blocked;
    searchButton.setAttribute("aria-disabled", blocked);
  }

  purposeInput.addEventListener("input", updateButtonState);
  searchInput.addEventListener("input", updateButtonState);

  /* ---------- 4.  Allow Enter key in inputs ---------- */
  [purposeInput, searchInput].forEach((el) =>
    el.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !searchButton.disabled) {
        e.preventDefault(); // avoid accidental form submit before validation
        handleSearch(e);
      }
    })
  );

  /* ---------- 5.  Recent purposes helpers ---------- */
  function handleRecentPurposeClick(e) {
    if (e.target.nodeName === "LI") {
      purposeInput.value = e.target.textContent;
      updateButtonState();
    }
  }

  function updateRecentPurposes(newPurpose) {
    const maxPurposes = 10;
    let recent = JSON.parse(localStorage.getItem("recentPurposes")) || [];

    newPurpose = newPurpose.trim();
    recent = recent.filter(
      (p) => p.toLowerCase() !== newPurpose.toLowerCase()
    );

    recent.unshift(newPurpose);
    recent = recent.slice(0, maxPurposes);

    localStorage.setItem("recentPurposes", JSON.stringify(recent));
  }

  function populateRecentPurposes() {
    const purposes = JSON.parse(localStorage.getItem("recentPurposes")) || [];
    recentPurposesContainer.innerHTML = "";

    purposes.forEach((purpose) => {
      const li = document.createElement("li");
      li.textContent = purpose;
      li.addEventListener("click", handleRecentPurposeClick);
      recentPurposesContainer.appendChild(li);
    });

    // Show / hide empty-state helper text
    const emptyMsg = document.getElementById("emptyPurposesMsg");
    if (emptyMsg) emptyMsg.style.display = purposes.length ? "none" : "";
  }

  populateRecentPurposes();
  updateButtonState();

  /* ---------- 6.  Core search handler ---------- */
  function handleSearch(e) {
    if (e) e.preventDefault(); // stops default form nav

    if (searchButton.disabled) return; // guard against stray calls

    const purposeValue = purposeInput.value.trim();
    const searchValue = searchInput.value.trim();

    /* 6a. persist recent purposes */
    updateRecentPurposes(purposeValue);
    populateRecentPurposes();

    /* 6b. message background script */
    const payload = {
      timestamp: new Date().toISOString(),
      event: "User starts or modifies a search",
      purpose: purposeValue,
      searchTerms: searchValue,
    };

    extBrowser.runtime.sendMessage(
      { type: "searchInitiated", data: payload },
      (response) => {
        if (extBrowser.runtime.lastError) {
          console.error("Error sending message:", extBrowser.runtime.lastError);
        } else {
          console.log("Response from background script:", response);
        }
      }
    );

    /* 6c. redirect to search engine */
    window.location.href = `https://www.google.com/search?q=${encodeURIComponent(
      searchValue
    )}`;
  }

  /* ---------- 7.  Wire up events ---------- */
  searchButton.addEventListener("click", handleSearch);
  searchForm.addEventListener("submit", handleSearch);
});
