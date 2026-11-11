/* SirusSofts persistent WebSocket client. No reloads, no page polling. */
(() => {
  "use strict";
  let socket = null, retries = 0, retryTimer = null, connecting = false;
  let latest = null, lastEventId = null;

  function status(message) {
    const element = document.querySelector("[data-live-status]");
    if (element) element.textContent = "Live updates: " + message;
  }

  function element(tag, value) {
    const el = document.createElement(tag);
    if (value !== undefined) el.textContent = String(value);
    return el;
  }

  function jobRow(job) {
    const row = element("tr");
    row.dataset.liveJob = String(job.request_id || "");
    for (const value of [job.request_id, job.node_id, job.kind, job.state,
                         job.attempts, job.updated_at
                           ? new Date(job.updated_at * 1000).toISOString() : "N/A"]) {
      const cell = element("td", value ?? "");
      row.appendChild(cell);
    }
    const resultCell = element("td");
    if (job.state === "done" || job.state === "failed") {
      const details = element("details");
      details.appendChild(element("summary", "View " + job.state));
      details.appendChild(element("pre", JSON.stringify(job.result ?? {}, null, 2)));
      if (job.error) details.appendChild(element("p", job.error));
      resultCell.appendChild(details);
    } else {
      resultCell.textContent = job.state === "queued" ? "In queue" : "Working";
    }
    row.appendChild(resultCell);
    return row;
  }

  function render(snapshot) {
    latest = snapshot;
    const events = Array.isArray(snapshot.events) ? snapshot.events : [];
    if (lastEventId === null) {
      lastEventId = events.reduce((max, item) => Math.max(max, Number(item.id) || 0), 0);
    } else {
      for (const item of events) {
        const id = Number(item.id) || 0;
        if (id <= lastEventId) continue;
        document.dispatchEvent(new CustomEvent("sirus:notification", {detail: item}));
        const banner = document.querySelector("[data-live-notifications]");
        if (banner) {
          const titles = {
            "message.received": "New site message",
            "mail.received": "New mail notification",
            "deployment.changed": "Deployment status changed",
            "site.notice": "New site notice"
          };
          if (titles[item.topic]) {
            banner.prepend(element("p", titles[item.topic]));
            while (banner.children.length > 5) banner.lastElementChild.remove();
          }
        }
      }
      lastEventId = events.reduce((max, item) => Math.max(max, Number(item.id) || 0), lastEventId);
    }
    const updates = document.querySelector("[data-live-update-jobs]");
    if (updates) {
      const fragment = document.createDocumentFragment();
      for (const job of (snapshot.jobs || [])) {
        if (job.kind !== "package_update_check") continue;
        const row = element("tr");
        for (const text of [job.node_id, job.state,
                job.updated_at ? new Date(job.updated_at * 1000).toISOString() : "N/A"]) {
          row.appendChild(element("td", text || ""));
        }
        const cell = element("td");
        const details = element("details");
        details.appendChild(element("summary", "View result"));
        details.appendChild(element("pre", JSON.stringify(job.result ?? {}, null, 2)));
        if (job.error) details.appendChild(element("p", job.error));
        cell.appendChild(details);
        row.appendChild(cell);
        fragment.appendChild(row);
      }
      updates.replaceChildren(fragment);
    }
    const jobs = document.querySelector("[data-live-infrastructure-jobs]");
    if (jobs) {
      const fragment = document.createDocumentFragment();
      for (const job of (snapshot.jobs || [])) fragment.appendChild(jobRow(job));
      jobs.replaceChildren(fragment);
    }
    for (const node of (snapshot.nodes || [])) {
      const badge = Array.from(document.querySelectorAll("[data-live-node-id]"))
        .find(el => el.dataset.liveNodeId === node.node_id);
      if (badge) badge.textContent = node.online ? "Online" : "Offline — stale metrics";
      const fmtGiB = bytes => (Number(bytes || 0) / (1024 ** 3)).toFixed(1) + " GiB";
      const metrics = node.metrics && typeof node.metrics === "object" ? node.metrics : {};
      document.querySelectorAll("[data-live-metric]").forEach(target => {
        if (target.dataset.liveMetricNode !== node.node_id) return;
        const key = target.dataset.liveMetric;
        let value = "Unavailable";
        if (node.online) {
          if (key === "cpu_pct" && typeof metrics.cpu_pct === "number")
            value = metrics.cpu_pct.toFixed(1) + "%";
          if (key === "cpu_temp_c" && typeof metrics.cpu_temp_c === "number")
            value = metrics.cpu_temp_c.toFixed(1) + " °C";
          if (key === "memory" && typeof metrics.memory_used_bytes === "number")
            value = fmtGiB(metrics.memory_used_bytes);
          if (key === "disk" && typeof metrics.disk_total_bytes === "number")
            value = fmtGiB(metrics.disk_used_bytes) + " / " + fmtGiB(metrics.disk_total_bytes);
        }
        target.textContent = value;
      });
      const gpuPanel = Array.from(document.querySelectorAll("[data-live-gpus]"))
        .find(el => el.dataset.liveGpus === node.node_id);
      if (gpuPanel && Array.isArray(metrics.gpus)) {
        const fragment = document.createDocumentFragment();
        for (const gpu of metrics.gpus) {
          const card = element("div");
          card.className = "node-metric";
          card.appendChild(element("span", String(gpu.name || "GPU")));
          card.appendChild(element("strong", node.online && typeof gpu.usage_pct === "number"
            ? gpu.usage_pct.toFixed(1) + "% GPU" : "GPU load unavailable"));
          card.appendChild(element("small", "Temperature: " +
            (node.online && typeof gpu.temp_c === "number"
              ? gpu.temp_c.toFixed(1) + " °C" : "Unavailable")));
          fragment.appendChild(card);
        }
        gpuPanel.replaceChildren(fragment);
      }
      const services = Array.from(document.querySelectorAll("[data-live-node-services]"))
        .find(el => el.dataset.liveNodeServices === node.node_id);
      if (services) {
        const metrics = node.metrics && typeof node.metrics === "object" ? node.metrics : {};
        const running = Array.isArray(metrics.services) ? metrics.services : [];
        const apps = Array.isArray(metrics.applications) ? metrics.applications : [];
        const summary = element("p", node.online
          ? "OpenRC running: " + (running.join(", ") || "None reported")
          : "Latest known OpenRC services (host offline): " + (running.join(", ") || "None reported"));
        const appSummary = element("p", "Local applications: " +
          (apps.map(app => String(app.name || "app") + ": " +
            (node.online && app.healthy ? "Healthy" : "Unavailable")).join(" · ") || "None reported"));
        services.replaceChildren(summary, appSummary);
      }
    }
    document.dispatchEvent(new CustomEvent("sirus:live", {detail: snapshot}));
  }

  function scheduleRetry() {
    if (retryTimer || !document.querySelector("[data-live-csrf]")) return;
    const delay = Math.min(30000, 1000 * (2 ** Math.min(retries++, 5)));
    status("reconnecting");
    retryTimer = window.setTimeout(() => {
      retryTimer = null;
      connect();
    }, delay);
  }

  async function connect() {
    const field = document.querySelector("[data-live-csrf]");
    if (!field || connecting || (socket && socket.readyState <= WebSocket.OPEN)) return;
    if (location.protocol !== "https:") {
      status("HTTPS required");
      return;
    }
    connecting = true;
    try {
      const body = new URLSearchParams({action: "live_token", csrf: field.value});
      const result = await fetch("/admin/live-token", {
        method: "POST", credentials: "same-origin",
        headers: {"Content-Type": "application/x-www-form-urlencoded"},
        body, cache: "no-store", redirect: "error"
      });
      if (!result.ok) throw new Error("Authentication unavailable");
      const proof = await result.json();
      if (!proof.ok || typeof proof.token !== "string") throw new Error("Token unavailable");
      if (!document.querySelector("[data-live-csrf]")) return;
      const ws = new WebSocket("wss://" + location.host + "/admin/live-socket");
      socket = ws;
      ws.addEventListener("open", () => ws.send(JSON.stringify({token: proof.token})));
      ws.addEventListener("message", event => {
        let value;
        try { value = JSON.parse(event.data); } catch { return; }
        if (value.type === "ready") {
          retries = 0;
          status("connected");
        } else if (value.type === "snapshot") {
          render(value);
        } else if (value.type === "degraded") {
          status("data temporarily unavailable");
        }
      });
      ws.addEventListener("close", () => {
        if (socket === ws) socket = null;
        scheduleRetry();
      });
      ws.addEventListener("error", () => status("connection interrupted"));
    } catch (error) {
      status("connection unavailable");
      scheduleRetry();
    } finally {
      connecting = false;
    }
  }

  // Enqueue jobs without document navigation; WebSocket supplies status transitions.
  document.addEventListener("submit", async event => {
    const form = event.target.closest("form[data-live-submit]");
    if (!form) return;
    event.preventDefault();
    const button = form.querySelector('button[type="submit"]');
    if (button && button.disabled) return;
    if (button) button.disabled = true;
    status("submitting job");
    try {
      const reply = await fetch(form.action, {
        method: "POST", credentials: "same-origin",
        body: new FormData(form), redirect: "error",
        headers: {"Accept": "application/json"}, cache: "no-store"
      });
      const data = await reply.json();
      if (!reply.ok || !data.ok) throw new Error(data.error || "Job was rejected");
      status("queued " + String(data.request_id).slice(0, 8));
      document.dispatchEvent(new CustomEvent("sirus:job-queued", {detail: data}));
    } catch (error) {
      status("job error: " + String(error.message || "request failed").slice(0, 120));
    } finally {
      if (button) button.disabled = false;
    }
  });

  document.addEventListener("sirus:navigated", () => {
    if (!document.querySelector("[data-live-csrf]")) {
      if (socket) socket.close();
      socket = null;
      if (retryTimer) window.clearTimeout(retryTimer);
      retryTimer = null;
      latest = null;
      return;
    }
    if (latest) render(latest);
    connect();
  });
  window.addEventListener("DOMContentLoaded", connect);
  window.addEventListener("pageshow", connect);
  window.addEventListener("pagehide", () => { if (socket) socket.close(); });
})();
