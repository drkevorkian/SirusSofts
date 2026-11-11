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

  let lastPlansSignature = null;

  function refreshReleaseComponentOptions() {
    const form = document.querySelector('form[data-release-planner]');
    if (!form) return;
    const host = form.querySelector('[name="host"]')?.value;
    const component = form.querySelector('[name="component"]');
    if (!component) return;
    for (const option of component.querySelectorAll("option[data-release-host]")) {
      option.disabled = option.dataset.releaseHost !== host;
      option.hidden = option.disabled;
    }
    if (component.selectedOptions[0]?.disabled) {
      const available = Array.from(component.options).find(option => !option.disabled);
      if (available) component.value = available.value;
    }
  }

  function releaseForm(plan, action, csrf) {
    const form = element("form");
    form.method = "post";
    form.action = "/admin/updates";
    form.dataset.liveSubmit = "";
    for (const [name, value] of Object.entries({csrf, action, id: plan.id})) {
      const field = element("input");
      field.type = "hidden";
      field.name = name;
      field.value = value;
      form.appendChild(field);
    }
    if (["approve_source_release","request_source_deploy","request_source_rollback"].includes(action)) {
      const pass = element("input");
      pass.type = "password";
      pass.name = "password";
      pass.placeholder = "Admin password";
      pass.autocomplete = "current-password";
      pass.required = true;
      form.appendChild(pass);
      const confirm = element("input");
      confirm.type = "text";
      confirm.name = "confirm";
      const verb = action === "approve_source_release" ? "APPROVE " :
        action === "request_source_deploy" ? "DEPLOY " : "ROLLBACK ";
      confirm.placeholder = verb + String(plan.commit_sha).slice(0, 12);
      confirm.required = true;
      form.appendChild(confirm);
    }
    const button = element("button",
      action === "approve_source_release" ? "Approve revision" :
      action === "request_source_deploy" ? "Install approved source" :
      action === "request_source_rollback" ? "Roll back source" : "Cancel plan");
    button.type = "submit";
    button.className = "btn subtle";
    form.appendChild(button);
    return form;
  }

  function renderReleasePlans(snapshot) {
    const mount = document.querySelector("[data-live-release-plans]");
    if (!mount) { lastPlansSignature = null; return; }
    const plans = Array.isArray(snapshot.release_plans) ? snapshot.release_plans : [];
    const requests = Array.isArray(snapshot.deployment_requests) ? snapshot.deployment_requests : [];
    const signature = JSON.stringify([plans,requests]);
    if (signature === lastPlansSignature && mount.querySelector("table")) return;
    lastPlansSignature = signature;
    const csrf = document.querySelector("[data-live-csrf]")?.value || "";
    const table = element("table");
    const head = element("thead");
    const header = element("tr");
    for (const value of ["Host", "Component", "Revision", "Status", "Approval"])
      header.appendChild(element("th", value));
    head.appendChild(header);
    table.appendChild(head);
    const tbody = element("tbody");
    for (const plan of plans) {
      const row = element("tr");
      for (const value of [plan.host, plan.source_path,
        String(plan.commit_sha || "").slice(0, 12), plan.state])
        row.appendChild(element("td", value || ""));
      const actions = element("td");
      if (plan.state === "planned")
        actions.appendChild(releaseForm(plan, "approve_source_release", csrf));
      if (plan.state === "planned" || plan.state === "approved")
        actions.appendChild(releaseForm(plan, "cancel_source_release", csrf));
      if (plan.state === "approved") {
        const history = requests.filter(req => req.plan_id === plan.id);
        const active = history.find(req => req.state === "queued" || req.state === "running");
        const applied = history.some(req => req.action === "apply" && req.state === "done");
        const rolledBack = history.some(req => req.action === "rollback" && req.state === "done");
        const recent = history[0];
        if (active) {
          actions.prepend(element("p", String(active.action) + ": " + String(active.state)));
        } else if (recent?.state === "interrupted") {
          actions.prepend(element("p", "Interrupted: manual recovery required"));
        } else if (recent?.state === "failed") {
          actions.prepend(element("p", "Failed: " + String(recent.error || "inspect release log")));
        } else if (applied && !rolledBack) {
          actions.prepend(element("p", "Installed and verified"));
        } else if (rolledBack) {
          actions.prepend(element("p", "Rolled back to previous release"));
        } else {
          actions.prepend(element("p", plan.staged_at ? "Approved and privately staged" : "Approved; awaiting staged source"));
        }
        const supported = ["ubuntu-worker/worker.py", "realtime/server.py"].includes(plan.source_path);
        if (plan.host === "ubuntu-primary" && supported && !active) {
          if (applied && !rolledBack && recent?.state !== "interrupted") {
            actions.appendChild(releaseForm(plan, "request_source_rollback", csrf));
          } else if (!applied && plan.staged_at && recent?.state !== "interrupted") {
            actions.appendChild(releaseForm(plan, "request_source_deploy", csrf));
          }
        }
      }
      row.appendChild(actions);
      tbody.appendChild(row);
    }
    table.appendChild(tbody);
    mount.replaceChildren(table);
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
    const sourceStatus = document.querySelector("[data-live-source-status]");
    const sourceList = document.querySelector("[data-live-source-list]");
    if (sourceStatus && sourceList) {
      const source = snapshot.source || {};
      if (!source.commit || !Array.isArray(source.files)) {
        sourceStatus.textContent = "GitHub source index unavailable; check the private indexer service.";
        sourceList.replaceChildren();
      } else {
        const checked = source.checked_at ?
          new Date(source.checked_at * 1000).toISOString() : "unknown";
        sourceStatus.textContent = "Approved branch: " + String(source.branch) +
          " · commit " + String(source.commit).slice(0, 12) + " · checked " + checked;
        const deployed = new Map();
        for (const item of source.installed || [])
          deployed.set(item.path, item.sha256);
        for (const node of snapshot.nodes || []) {
          if (node.node_id !== "gentoo-home") continue;
          for (const item of node.metrics?.software_files || [])
            deployed.set(item.path, node.online ? item.sha256 : null);
        }
        const table = element("table");
        const head = element("thead");
        const header = element("tr");
        for (const name of ["System", "Component", "Source comparison"])
          header.appendChild(element("th", name));
        head.appendChild(header);
        table.appendChild(head);
        const body = element("tbody");
        for (const item of source.files) {
          const tr = element("tr");
          tr.appendChild(element("td", item.platform || ""));
          tr.appendChild(element("td", item.path || ""));
          const installed = deployed.get(item.path);
          const state = installed === undefined ? "Not installed / not reported" :
            installed === null ? "Node offline (stale)" :
            installed === item.sha256 ? "Matches approved GitHub" :
            "Differs from approved GitHub — review";
          tr.appendChild(element("td", state));
          body.appendChild(tr);
        }
        table.appendChild(body);
        sourceList.replaceChildren(table);
      }
    }
    const commitSelect = document.querySelector("[data-live-release-commits]");
    if (commitSelect && Array.isArray(snapshot.source?.candidates)) {
      const candidates = snapshot.source.candidates;
      const fingerprint = JSON.stringify(candidates);
      if (commitSelect.dataset.candidates !== fingerprint) {
        const previouslySelected = commitSelect.value;
        const fragment = document.createDocumentFragment();
        for (const candidate of candidates) {
          if (!/^[a-f0-9]{40}$/.test(String(candidate.commit || ""))) continue;
          const option = element("option",
            String(candidate.commit).slice(0, 12) + " — " + String(candidate.message || ""));
          option.value = candidate.commit;
          fragment.appendChild(option);
        }
        commitSelect.replaceChildren(fragment);
        if (Array.from(commitSelect.options).some(el => el.value === previouslySelected))
          commitSelect.value = previouslySelected;
        commitSelect.dataset.candidates = fingerprint;
        const button = commitSelect.closest("form")?.querySelector('button[type="submit"]');
        if (button) button.disabled = !commitSelect.options.length;
      }
      refreshReleaseComponentOptions();
    }
    renderReleasePlans(snapshot);
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
      status(data.state === "planned" ? "release plan created" :
             data.state === "approved" ? "revision approved (not installed)" :
             data.state === "canceled" ? "release plan canceled" :
             data.state === "queued" ? "deployment queued" :
             "queued " + String(data.request_id).slice(0, 8));
      document.dispatchEvent(new CustomEvent("sirus:job-queued", {detail: data}));
    } catch (error) {
      status("job error: " + String(error.message || "request failed").slice(0, 120));
    } finally {
      if (button) button.disabled = false;
    }
  });

  document.addEventListener("change", event => {
    if (event.target.matches('form[data-release-planner] [name="host"]'))
      refreshReleaseComponentOptions();
  });
  window.addEventListener("DOMContentLoaded", refreshReleaseComponentOptions);

  document.addEventListener("sirus:navigated", () => {
    if (!document.querySelector("[data-live-csrf]")) {
      if (socket) socket.close();
      socket = null;
      if (retryTimer) window.clearTimeout(retryTimer);
      retryTimer = null;
      latest = null;
      return;
    }
    lastPlansSignature = null;
    refreshReleaseComponentOptions();
    if (latest) render(latest);
    connect();
  });
  window.addEventListener("DOMContentLoaded", connect);
  window.addEventListener("pageshow", connect);
  window.addEventListener("pagehide", () => { if (socket) socket.close(); });
})();
