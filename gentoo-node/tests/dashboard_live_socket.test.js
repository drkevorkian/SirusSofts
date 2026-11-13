// Confirm the browser uses signed WebSocket subprotocol authentication, and
// keeps using the socket rather than the HTTPS fallback after it connects.
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

async function main() {
  const source = fs.readFileSync(path.join(__dirname, "..", "dashboard_live.js"), "utf8");
  const ids = ["live-status", "live-cpu", "live-memory", "live-disk",
               "live-uptime", "live-history-status", "live-updated",
               "live-socket-diagnostic"];
  const nodes = Object.fromEntries(ids.map(id => [id, {textContent: ""}]));
  let ws = null, fallbackPolls = 0, interval = null, pagehide = null;
  let lastRequested = "";
  class MockSocket {
    static OPEN = 1;
    constructor(url, protocols) {
      this.url = url;
      this.protocols = protocols;
      this.protocol = "sirus-live";
      this.readyState = MockSocket.OPEN;
      this.handlers = {};
      this.messagesSent = [];
      ws = this;
    }
    addEventListener(name, handler) { this.handlers[name] = handler; }
    send(message) { this.messagesSent.push(message); }
    close() { this.closed = true; }
    emit(name, detail = {}) {
      assert.equal(typeof this.handlers[name], "function");
      this.handlers[name](detail);
    }
  }
  const sandbox = {
    URL, Date, WebSocket: MockSocket,
    document: {
      readyState: "complete",
      getElementById(id) { return nodes[id] ?? null; }
    },
    window: {
      location: {href: "https://sirussofts.com/Gentoo/?a=1", protocol: "https:"},
      setTimeout() { return 1; },
      clearTimeout() {},
      setInterval(fn) { interval = fn; fallbackPolls++; return 2; },
      clearInterval() { interval = null; },
      addEventListener(name, cb) { if (name === "pagehide") pagehide = cb; }
    },
    fetch: async (url, options) => {
      assert.equal(options.credentials, "same-origin");
      lastRequested = String(url);
      return {ok: true, json: async () => ({token: "signed-token-test"})};
    }
  };
  vm.runInNewContext(source, sandbox);
  for (let n=0; n<6; n++) await new Promise(resolve => setImmediate(resolve));
  assert.ok(ws, "Browser did not open a WebSocket");
  assert.equal(lastRequested, "https://sirussofts.com/Gentoo/live-token");
  assert.equal(ws.url, "wss://sirussofts.com/Gentoo/live");
  assert.equal(Array.from(ws.protocols).join(","), "sirus-live,sirus-auth.signed-token-test");

  ws.emit("open");
  assert.deepEqual(ws.messagesSent, [], "No insecure post-upgrade auth frame");
  ws.emit("message", {data: JSON.stringify({type:"ready"})});
  assert.equal(nodes["live-status"].textContent, "Live: connected (WebSocket)");
  assert.equal(nodes["live-socket-diagnostic"].textContent,
               "WSS diagnostic: connected; signed ready frame received");
  assert.equal(fallbackPolls, 0);
  ws.emit("message", {data: JSON.stringify({
    type:"snapshot",
    metrics:{cpu_pct:24, memory_used_bytes:1073741824, disk_used_bytes:1,
             disk_total_bytes:10, uptime_seconds:36000, gpus:[]},
    hardware:{ram_bytes:8589934592}, jobs:[], graph:[], sync:"signed history"
  })});
  assert.equal(nodes["live-cpu"].textContent, "24.0%");
  ws.emit("message", {data: JSON.stringify({
    type:"snapshot",
    metrics:{cpu_pct:51, memory_used_bytes:1073741824, disk_used_bytes:1,
             disk_total_bytes:10, uptime_seconds:36001, gpus:[]},
    hardware:{ram_bytes:8589934592}, jobs:[], graph:[], sync:"signed history"
  })});
  assert.equal(nodes["live-cpu"].textContent, "51.0%");
  assert.equal(nodes["live-status"].textContent, "Live: connected (WebSocket)");
  assert.equal(fallbackPolls, 0);
  pagehide();
  assert.equal(ws.closed, true);
  console.log("PASS browser authenticated WebSocket subprotocol and continuing snapshot updates");
}
main().catch(e => {console.error(e); process.exitCode = 1;});
