// Browser-level regression: a WebSocket failure must not freeze Gentoo metrics.
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");

async function main() {
  const client = fs.readFileSync(path.join(__dirname, "..", "dashboard_live.js"), "utf8");
  const ids = ["live-status", "live-cpu", "live-memory", "live-disk",
               "live-uptime", "live-history-status", "live-updated",
               "live-socket-diagnostic"];
  const nodes = Object.fromEntries(ids.map(id => [id, {textContent: ""}]));
  let interval = null;
  let pagehide = null;
  let currentCpu = 22;
  const fetched = [];
  const setIntervals = [];
  const snapshot = () => ({
    type: "snapshot",
    metrics: { cpu_pct: currentCpu, memory_used_bytes: 1073741824,
               disk_used_bytes: 100, disk_total_bytes: 1000,
               uptime_seconds: 36000, gpus: [], services: [], applications: [] },
    hardware: {ram_bytes: 8589934592},
    jobs: [], graph: [], sync: "Signed history available"
  });
  const sandbox = {
    URL,
    Date,
    WebSocket: class {
      static OPEN = 1;
      constructor() { throw new Error("WebSocket Upgrade blocked"); }
    },
    document: {
      readyState: "complete",
      getElementById(id) { return nodes[id] ?? null; }
    },
    window: {
      location: {
        href: "https://sirussofts.com/Gentoo/?utm_source=chatgpt.com",
        protocol: "https:"
      },
      setTimeout() { return 1; },
      clearTimeout() {},
      setInterval(fn, delay) {
        interval = fn;
        setIntervals.push(delay);
        return 2;
      },
      clearInterval() { interval = null; },
      addEventListener(name, handler) {
        if (name === "pagehide") pagehide = handler;
      }
    },
    fetch: async (url, options) => {
      fetched.push(String(url));
      assert.equal(options.credentials, "same-origin");
      if (String(url).includes("snapshot=1")) {
        return {ok: true, json: async () => snapshot()};
      }
      return {ok: true, json: async () => ({token: "safe-test-token"})};
    }
  };
  vm.runInNewContext(client, sandbox);
  // Flush the rejected socket promise and the background HTTPS fetch.
  for (let n=0; n<6; n++) await new Promise(resolve => setImmediate(resolve));
  assert.equal(nodes["live-cpu"].textContent, "22.0%");
  assert.equal(nodes["live-memory"].textContent, "12.5%");
  assert.match(nodes["live-status"].textContent, /HTTPS fallback/);
  assert.equal(nodes["live-socket-diagnostic"].textContent,
               "WSS diagnostic: browser rejected WebSocket constructor");
  assert.deepEqual(setIntervals, [5000]);
  assert.ok(fetched.some(url => url === "https://sirussofts.com/Gentoo/live-token?snapshot=1"));

  currentCpu = 46;
  assert.equal(typeof interval, "function");
  interval();
  for (let n=0; n<3; n++) await new Promise(resolve => setImmediate(resolve));
  assert.equal(nodes["live-cpu"].textContent, "46.0%");
  assert.equal(nodes["live-status"].textContent, "Live: updating in place (HTTPS fallback)");
  assert.equal(typeof pagehide, "function");
  pagehide();
  assert.equal(interval, null);

  console.log("PASS disconnected WebSocket uses authenticated 5-second HTTPS snapshots without navigating");
}

main().catch(err => { console.error(err); process.exitCode = 1; });
