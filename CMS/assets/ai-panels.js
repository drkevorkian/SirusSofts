/* Admin-only AI status sections load separately so the HTML shell never waits
   for congested Gentoo tunnels. All requests stay same-origin/session-gated. */
(() => {
  "use strict";
  const keys = {
    continuous: ["cr_source", "cr_offset", "cr_audit_offset"],
    evidence: [],
    procedures: ["procedure_candidate", "scope_proposal"],
    pdf: ["pdf_state", "pdf_search", "pdf_page", "pdf_preview", "pdf_part"]
  };
  const maxConcurrent = 2;
  let generation = 0;
  let controllers = [];
  let queue = [];
  let running = 0;

  function abortPrevious() {
    generation++;
    controllers.forEach((controller) => controller.abort());
    controllers = [];
    queue = [];
    running = 0;
  }

  function panelUrl(kind) {
    const url = new URL("/admin/ai/panel/" + kind, window.location.origin);
    const current = new URLSearchParams(window.location.search);
    for (const key of keys[kind]) {
      if (current.has(key)) url.searchParams.set(key, current.get(key));
    }
    return url;
  }

  function errorPanel(host, kind) {
    if (!host.isConnected) return;
    host.dataset.aiState = "failed";
    const status = host.querySelector('[role="status"]');
    if (status) status.textContent =
      "This private status section is temporarily unavailable. The AI page and other sections remain usable.";
    const retry = host.querySelector("[data-ai-panel-retry]");
    if (retry) retry.hidden = false;
  }

  async function fetchPanel(host, kind, ticket) {
    if (!host.isConnected || ticket !== generation) return;
    host.dataset.aiState = "loading";
    const status = host.querySelector('[role="status"]');
    if (status) status.textContent = "Loading private " + kind + " status…";
    const retry = host.querySelector("[data-ai-panel-retry]");
    if (retry) retry.hidden = true;
    const controller = new AbortController();
    controllers.push(controller);
    const timeout = window.setTimeout(() => controller.abort(), 12500);
    try {
      const response = await fetch(panelUrl(kind), {
        method: "GET", credentials: "same-origin", redirect: "error",
        cache: "no-store", signal: controller.signal,
        headers: { Accept: "text/html" }
      });
      if (!response.ok || response.headers.get("X-Sirus-AI-Panel") !== kind ||
          !(response.headers.get("Content-Type") || "").includes("text/html")) {
        throw new Error("Unverified private panel response");
      }
      const documentFragment = new DOMParser().parseFromString(await response.text(), "text/html");
      if (ticket !== generation || !host.isConnected) return;
      const nodes = Array.from(documentFragment.body.childNodes)
        .map((node) => document.importNode(node, true));
      if (!nodes.length) throw new Error("Empty panel response");
      host.replaceWith(...nodes);
      document.dispatchEvent(new CustomEvent("sirus:ai-panel-loaded", {detail: {panel: kind}}));
    } catch (_) {
      if (ticket === generation) errorPanel(host, kind);
    } finally {
      window.clearTimeout(timeout);
      controllers = controllers.filter((item) => item !== controller);
    }
  }

  function pump() {
    while (running < maxConcurrent && queue.length) {
      const [host, kind, ticket] = queue.shift();
      if (ticket !== generation || !host.isConnected) continue;
      running++;
      fetchPanel(host, kind, ticket).finally(() => {
        if (ticket !== generation) return;
        running--;
        pump();
      });
    }
  }

  function load() {
    abortPrevious();
    const ticket = generation;
    document.querySelectorAll("[data-ai-remote-panel]").forEach((host) => {
      const kind = host.getAttribute("data-ai-remote-panel");
      if (Object.prototype.hasOwnProperty.call(keys, kind)) {
        queue.push([host, kind, ticket]);
      }
    });
    pump();
  }

  document.addEventListener("click", (event) => {
    const retry = event.target.closest("[data-ai-panel-retry]");
    if (!retry) return;
    const host = retry.closest("[data-ai-remote-panel]");
    const kind = host?.getAttribute("data-ai-remote-panel");
    if (!host || !Object.prototype.hasOwnProperty.call(keys, kind)) return;
    event.preventDefault();
    if (host.dataset.aiState === "loading") return;
    queue.push([host, kind, generation]);
    pump();
  });
  document.addEventListener("sirus:navigated", load);
  window.addEventListener("pagehide", abortPrevious);
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", load, {once: true});
  } else {
    load();
  }
})();
