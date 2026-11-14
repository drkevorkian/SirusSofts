/* Gentoo dashboard: socket-driven updates, no document refresh or HTTP polling. */
(() => {
  "use strict";
  let ws = null, retry = null, attempts = 0;
  let disposed = false, connecting = false, socketReady = false;
  let pollTimer = null, pollBusy = false, handshakeTimer = null;
  const byId = id => document.getElementById(id);
  const base = new URL(".", window.location.href);
  // Only the dashboard on Gentoo's own loopback port can use plaintext WS.
  // All public sites must continue to use authenticated HTTPS and WSS.
  const localLoopback = window.location.protocol === "http:" &&
    window.location.hostname === "127.0.0.1" && window.location.port === "18080";
  let socketStage = "initializing";

  // Browser-visible, credential-free connection diagnostics. The browser
  // extension can inspect this element without privileged DevTools access.
  function socketDiagnostic(status) {
    const target = byId("live-socket-diagnostic");
    if (target) target.textContent = (localLoopback ? "Local WS diagnostic: " : "WSS diagnostic: ") + status;
  }

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

  function renderServices(metrics) {
    const services = byId("live-services");
    if (services) {
      const names = Array.isArray(metrics.services) ? metrics.services.slice(0, 48) : [];
      services.textContent = names.length ? names.join(", ") : "No running services reported";
    }
    const applications = byId("live-apps");
    if (applications) {
      const apps = Array.isArray(metrics.applications) ? metrics.applications.slice(0, 8) : [];
      applications.replaceChildren();
      for (const app of apps) {
        if (!app || typeof app !== "object") continue;
        const item = node("p", String(app.name || "app") + ": " +
          (app.healthy ? "healthy" : "unavailable"));
        applications.appendChild(item);
      }
      if (!applications.childNodes.length)
        applications.textContent = "No local applications reported";
    }
  }

  let lastGpu = "", lastJobs = "", lastGraph = "", lastServices = "";
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
    const serviceState = JSON.stringify([m.services || [], m.applications || []]);
    if (serviceState !== lastServices) {
      renderServices(m);
      lastServices = serviceState;
    }
    const gpus = JSON.stringify(m.gpus || []);
    const jobs = JSON.stringify(data.jobs || []);
    const graph = JSON.stringify(data.graph || []);
    if (gpus !== lastGpu) { renderGPUs(m.gpus || []); lastGpu = gpus; }
    if (jobs !== lastJobs) { renderJobs(data.jobs || []); lastJobs = jobs; }
    if (graph !== lastGraph) { drawGraph(data.graph || []); lastGraph = graph; }
    metric("live-updated", "Latest data: " + new Date().toLocaleTimeString());
  }

  // The snapshot uses the already installed /Gentoo/live-token proxy
  // (with a query parameter). No page reload, additional public endpoint,
  // credentials in a URL, or per-viewer polling of the Ubuntu job broker.
  async function pollSnapshot() {
    if (disposed || pollBusy || socketReady) return;
    pollBusy = true;
    try {
      const response = await fetch(new URL("live-token?snapshot=1", base), {
        credentials: "same-origin", cache: "no-store", redirect: "error"
      });
      // A WebSocket may finish authenticating while an HTTPS fallback
      // request is still in flight. Never overwrite its connected status.
      if (disposed || socketReady) return;
      if (!response.ok) {
        label("updates unavailable (HTTP " + response.status + ")");
        return;
      }
      const snapshot = await response.json();
      if (disposed || socketReady) return;
      if (!snapshot || snapshot.type !== "snapshot")
        throw new Error("Invalid live snapshot");
      render(snapshot);
      label("updating in place (HTTPS fallback)");
    } catch {
      if (!disposed && !socketReady) label("updates unavailable (retrying)");
    } finally {
      pollBusy = false;
    }
  }

  function stopFallback() {
    if (pollTimer !== null) window.clearInterval(pollTimer);
    pollTimer = null;
  }

  function startFallback() {
    if (disposed || pollTimer !== null || socketReady) return;
    void pollSnapshot();
    pollTimer = window.setInterval(() => { void pollSnapshot(); }, 5000);
  }

  function reconnect() {
    if (disposed || retry !== null ||
        (socketReady && ws && ws.readyState === WebSocket.OPEN)) return;
    socketReady = false;
    startFallback();
    const delay = Math.min(30000, 1000 * (2 ** Math.min(attempts++, 5)));
    retry = window.setTimeout(() => {
      retry = null;
      void connect();
    }, delay);
  }

  async function connect() {
    if (disposed || connecting || (ws && ws.readyState <= WebSocket.OPEN)) return;
    if (window.location.protocol !== "https:" && !localLoopback) {
      label("HTTPS required");
      return;
    }
    connecting = true;
    socketStage = "requesting signed token";
    socketDiagnostic(socketStage);
    try {
      const response = await fetch(new URL("live-token", base), {
        credentials: "same-origin", cache: "no-store", redirect: "error"
      });
      if (!response.ok) {
        socketDiagnostic("token endpoint HTTP " + response.status);
        socketStage = "token HTTP failure";
        throw new Error("Authentication unavailable");
      }
      socketStage = "reading token response";
      const data = await response.json();
      if (typeof data.token !== "string") throw new Error("Missing live token");
      if (disposed) return;

      const endpoint = new URL("live", base);
      endpoint.protocol = localLoopback ? "ws:" : "wss:";
      // The existing signed token is offered at handshake time so a
      // rejected client cannot receive 101 and then close silently.
      socketStage = "creating WebSocket";
      socketDiagnostic("token received; attempting WSS handshake");
      const socket = new WebSocket(endpoint.href, ["sirus-live", "sirus-auth." + data.token]);
      ws = socket;
      socketStage = "awaiting WSS handshake";
      socketReady = false;
      // Some reverse proxies silently stall a bad handshake. In that case,
      // fall back to authenticated HTTPS snapshots rather than a frozen page.
      handshakeTimer = window.setTimeout(() => {
        if (!socketReady && ws === socket) {
          socketDiagnostic("WSS did not deliver ready frame within 8 seconds");
          socket.close();
        }
      }, 8000);

      socket.addEventListener("open", () => {
        // The server authenticates the signed subprotocol before HTTP 101.
        // Its first frame will be a ready acknowledgment, not a login prompt.
        if (socket.protocol !== "sirus-live") {
          socketDiagnostic("WSS connected but selected unexpected subprotocol");
          socket.close(1002, "Subprotocol mismatch");
        } else {
          socketDiagnostic("HTTP 101 accepted; awaiting ready frame");
        }
      });
      socket.addEventListener("message", event => {
        if (disposed || ws !== socket) return;
        let payload;
        try { payload = JSON.parse(event.data); } catch { return; }
        if (payload.type === "ready") {
          socketReady = true;
          attempts = 0;
          if (handshakeTimer !== null) window.clearTimeout(handshakeTimer);
          handshakeTimer = null;
          stopFallback();
          socketDiagnostic("connected; signed ready frame received");
          label("connected (WebSocket)");
        } else if (payload.type === "snapshot") {
          render(payload);
          // Recover from any older delayed HTTPS response that might have
          // overwritten the visible transport label during a reconnect.
          if (socketReady) label("connected (WebSocket)");
        }
      });
      socket.addEventListener("close", event => {
        if (ws !== socket) return; // Ignore late closes from an obsolete socket.
        const code = event && Number.isInteger(event.code) ? event.code : 1006;
        socketDiagnostic("WSS closed (code " + code +
                         (socketReady ? ", after ready" : ", before ready") + ")");
        if (handshakeTimer !== null) window.clearTimeout(handshakeTimer);
        handshakeTimer = null;
        ws = null;
        reconnect();
      });
      socket.addEventListener("error", () => {
        if (ws === socket && !socketReady) {
          socketDiagnostic("browser reported a WebSocket connection error");
          startFallback();
        }
      });
    } catch {
      if (socketStage === "creating WebSocket") {
        socketDiagnostic("browser rejected WebSocket constructor");
      } else if (socketStage === "reading token response") {
        socketDiagnostic("invalid token response (no signed token available)");
      } else if (socketStage === "requesting signed token") {
        socketDiagnostic("token request failed (network, browser policy or login)");
      }
      reconnect();
    } finally {
      connecting = false;
    }
  }

  window.addEventListener("pagehide", () => {
    disposed = true;
    stopFallback();
    if (retry !== null) window.clearTimeout(retry);
    if (handshakeTimer !== null) window.clearTimeout(handshakeTimer);
    if (ws) ws.close();
  });

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => { void connect(); }, {once: true});
  } else {
    void connect();
  }
})();
