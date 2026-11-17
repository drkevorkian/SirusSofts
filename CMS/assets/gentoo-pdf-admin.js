/* Signed Gentoo PDF status is proxied through the authenticated CMS.
   The browser NEVER receives the node key or contacts Gentoo directly. */
(() => {
  "use strict";
  const panel = document.querySelector("[data-gentoo-pdf]");
  if (!panel) return;
  const status = panel.querySelector("[data-gentoo-live]");
  const qs = new URLSearchParams(window.location.search);
  const url = new URL("/admin/ai/gentoo-status", window.location.origin);
  for (const key of ["pdf_state", "pdf_search", "pdf_page"]) {
    if (qs.has(key)) url.searchParams.set(key, qs.get(key));
  }
  let busy = false;
  async function refresh() {
    if (busy || document.hidden) return;
    busy = true;
    try {
      const response = await fetch(url, {
        method: "GET", credentials: "same-origin", cache: "no-store",
        headers: { "Accept": "application/json" }
      });
      if (!response.ok) throw new Error("status " + response.status);
      const data = await response.json();
      if (!data || data.ok !== true || !Array.isArray(data.documents))
        throw new Error("invalid response");
      const counts = Object.assign({}, data.counts, {total: data.total});
      panel.querySelectorAll("[data-pdf-count]").forEach((node) => {
        const key = node.getAttribute("data-pdf-count");
        node.textContent = String(counts[key] || 0);
      });
      const eligible = panel.querySelector("[data-pdf-eligible]");
      if (eligible) eligible.textContent = Number(data.eligible_ready || 0).toLocaleString();
      const approveAll = panel.querySelector("[data-pdf-bulk-submit]");
      if (approveAll) approveAll.disabled = Number(data.eligible_ready || 0) <= 0;
      const workers = panel.querySelector("[data-pdf-workers]");
      if (workers) workers.textContent = String(data.workers);
      const paused = panel.querySelector("[data-pdf-state]");
      if (paused) paused.textContent = data.paused ? "Paused" : "Running";
      const free = panel.querySelector("[data-pdf-free]");
      if (free && data.storage) {
        free.textContent = (Number(data.storage.free_bytes) / (1024 ** 3)).toFixed(2) + " GiB";
      }
      const byHash = new Map(data.documents.map((doc) => [doc.sha256, doc]));
      panel.querySelectorAll("[data-pdf-sha]").forEach((row) => {
        const doc = byHash.get(row.getAttribute("data-pdf-sha"));
        const progress = row.querySelector("[data-pdf-job]");
        if (doc && progress) {
          // Never render PDF content or filename as HTML.
          progress.textContent = doc.status.charAt(0).toUpperCase() + doc.status.slice(1) +
            (doc.error ? ": " + doc.error : "");
        }
      });
      status.textContent = "Gentoo connected · counts updated at " +
        new Date().toLocaleTimeString() + " · refresh page for changed action buttons";
    } catch (error) {
      status.textContent = "Gentoo PDF status unavailable. Existing data is unaffected.";
    } finally {
      busy = false;
    }
  }
  const timer = window.setInterval(refresh, 12000);
  window.addEventListener("pagehide", () => window.clearInterval(timer), {once: true});
})();
