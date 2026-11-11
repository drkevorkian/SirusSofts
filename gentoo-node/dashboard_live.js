/* Gentoo dashboard: socket-driven updates, no document refresh or HTTP polling. */
(() => {
  "use strict";
  let ws = null, retry = null, attempts = 0;
  let disposed = false, connecting = false;
  const byId = id => document.getElementById(id);
  const base = new URL(".", window.location.href);

  function label(message) {
    const node = byId("live-status");
    if (node) node.textContent = "Live: " + message;
  }

  function node(tag, content) {
    const el = document.createElement(tag);
    if (content !== undefined) el.textContent = String(content);
    return el;
  }

  function fmtDate(value) {
    const d = new Date(Number(value) * 1000);
    return Number.isFinite(d.getTime()) ? d.toUTCString().slice(5, 22) + " UTC" : "N/A";
  }

  function gb(value) {
    return typeof value === "number" && Number.isFinite(value)
      ? (value / 1073741824).toFixed(2) + " GiB" : "N/A";
  }

  function metric(id, value) {
    const target = byId(id);
    if (target && target.textContent !== value) target.textContent = value;
  }

  function drawGraph(samples) {
    const container = byId("live-cpu-chart");
    if (!container || !Array.isArray(samples)) return;
    if (samples.length < 2) {
      if (!container.querySelector(".muted"))
        container.replaceChildren(node("p", "Collecting CPU history; check back after two samples."));
      return;
    }
    const svgNS = "http://www.w3.org/2000/svg";
    let svg = container.querySelector("svg");
    if (!svg) {
      svg = document.createElementNS(svgNS, "svg");
      svg.setAttribute("viewBox", "0 0 600 120");
      svg.setAttribute("preserveAspectRatio", "none");
      svg.setAttribute("role", "img");
      svg.setAttribute("aria-label", "CPU use over 24 hours");
      const polyline = document.createElementNS(svgNS, "polyline");
      polyline.setAttribute("stroke", "#6db9ff");
      polyline.setAttribute("stroke-width", "2");
      polyline.setAttribute("fill", "none");
      svg.appendChild(polyline);
      container.replaceChildren(svg);
    }
    const points = samples.map((v, index) => {
      const number = Math.max(0, Math.min(100, Number(v) || 0));
      return (index * 600 / Math.max(1, samples.length - 1)).toFixed(1) +
        "," + (110 - number).toFixed(1);
    }).join(" ");
    svg.firstElementChild?.setAttribute("points", points);
  }

  function renderGPUs(gpus) {
    const table = byId("live-gpu-rows");
    if (!table || !Array.isArray(gpus)) return;
    const fragment = document.createDocumentFragment();
    for (const gpu of gpus.slice(0, 24)) {
      if (!gpu || typeof gpu !== "object") continue;
      const tr = node("tr");
      const name = node("td", gpu.name || "GPU");
      if (gpu.note) name.appendChild(node("small", gpu.note));
      tr.appendChild(name);
      tr.appendChild(node("td", typeof gpu.usage_pct === "number" ? gpu.usage_pct + "%" : "N/A"));
      tr.appendChild(node("td", typeof gpu.temp_c === "number" ? gpu.temp_c + " °C" : "N/A"));
      tr.appendChild(node("td", gb(gpu.vram_used_bytes) + " / " + gb(gpu.vram_total_bytes)));
      fragment.appendChild(tr);
    }
    if (!fragment.childNodes.length) {
      const row = node("tr");
      const cell = node("td", "No GPUs found");
      cell.colSpan = 4;
      row.appendChild(cell);
      fragment.appendChild(row);
    }
    table.replaceChildren(fragment);
  }

  function renderJobs(jobs) {
    const table = byId("live-job-rows");
    if (!table || !Array.isArray(jobs)) return;
    const opened = new Set(Array.from(table.querySelectorAll("tr[data-job-id]"))
      .filter(row => row.querySelector("details")?.open)
      .map(row => row.dataset.jobId));
    const fragment = document.createDocumentFragment();
    for (const job of jobs.slice(0, 40)) {
      if (!job || typeof job !== "object") continue;
      const row = node("tr");
      row.dataset.jobId = String(job.request_id || "");
      const id = node("td");
      id.appendChild(node("code", job.request_id || ""));
      row.appendChild(id);
      for (const val of [job.kind || "", job.state || "", job.attempts ?? 0,
                         fmtDate(job.updated_at)]) row.appendChild(node("td", val));
      const result = node("td");
      const details = node("details");
      details.open = opened.has(row.dataset.jobId);
      details.appendChild(node("summary", "Details"));
      details.appendChild(node("pre", JSON.stringify(job.result ?? {}, null, 2).slice(0, 8192)));
      if (job.error) details.appendChild(node("small", String(job.error).slice(0, 1024)));
      result.appendChild(details);
      row.appendChild(result);
      fragment.appendChild(row);
    }
    if (!fragment.childNodes.length) {
      const row = node("tr");
      const cell = node("td", "No previous jobs");
      cell.colSpan = 6;
      row.appendChild(cell);
      fragment.appendChild(row);
    }
    table.replaceChildren(fragment);
  }

  let lastGpu = "", lastJobs = "", lastGraph = "";
  function render(data) {
    if (!data || data.type !== "snapshot") return;
    const m = data.metrics || {};
    const h = data.hardware || {};
    metric("live-cpu", typeof m.cpu_pct === "number" ? m.cpu_pct.toFixed(1) + "%" : "N/A");
    metric("live-memory", ((100 * Number(m.memory_used_bytes || 0)) /
      Math.max(1, Number(h.ram_bytes || 0))).toFixed(1) + "%");
    metric("live-disk", ((100 * Number(m.disk_used_bytes || 0)) /
      Math.max(1, Number(m.disk_total_bytes || 0))).toFixed(1) + "%");
    metric("live-uptime", Math.floor(Number(m.uptime_seconds || 0) / 3600) + "h");
    metric("live-history-status", data.sync || "Waiting for coordinator history");
    const gpus = JSON.stringify(m.gpus || []);
    const jobs = JSON.stringify(data.jobs || []);
    const graph = JSON.stringify(data.graph || []);
    if (gpus !== lastGpu) { renderGPUs(m.gpus || []); lastGpu = gpus; }
    if (jobs !== lastJobs) { renderJobs(data.jobs || []); lastJobs = jobs; }
    if (graph !== lastGraph) { drawGraph(data.graph || []); lastGraph = graph; }
    metric("live-updated", "Latest data: " + new Date().toLocaleTimeString());
  }

  function reconnect() {
    if (disposed || retry) return;
    label("reconnecting");
    const delay = Math.min(30000, 1000 * (2 ** Math.min(attempts++, 5)));
    retry = window.setTimeout(() => { retry = null; connect(); }, delay);
  }

  async function connect() {
    if (disposed || connecting || (ws && ws.readyState <= WebSocket.OPEN)) return;
    if (window.location.protocol !== "https:") {
      label("HTTPS required");
      return;
    }
    connecting = true;
    try {
      const response = await fetch(new URL("live-token", base), {
        credentials: "same-origin", cache: "no-store", redirect: "error"
      });
      if (!response.ok) throw new Error("Authentication unavailable");
      const data = await response.json();
      if (typeof data.token !== "string") throw new Error("Missing live token");
      if (disposed) return;
      const endpoint = new URL("live", base);
      endpoint.protocol = "wss:";
      const socket = new WebSocket(endpoint.href);
      ws = socket;
      socket.addEventListener("open", () => socket.send(JSON.stringify({token: data.token})));
      socket.addEventListener("message", event => {
        let payload;
        try { payload = JSON.parse(event.data); } catch { return; }
        if (payload.type === "ready") {
          attempts = 0;
          label("connected");
        } else if (payload.type === "snapshot") {
          render(payload);
        }
      });
      socket.addEventListener("close", () => {
        if (ws === socket) ws = null;
        reconnect();
      });
      socket.addEventListener("error", () => label("connection interrupted"));
    } catch {
      label("unable to connect");
      reconnect();
    } finally {
      connecting = false;
    }
  }

  window.addEventListener("pagehide", () => {
    disposed = true;
    if (retry) window.clearTimeout(retry);
    if (ws) ws.close();
  });
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", connect, {once: true});
  } else {
    connect();
  }
})();
