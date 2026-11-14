// Real browser page on Gentoo may use authenticated ws:// ONLY on loopback.
// Public HTTP and alternate LAN hosts must never use plaintext sockets.
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const source = fs.readFileSync(path.join(__dirname, "..", "dashboard_live.js"), "utf8");

async function check(href, expectedSocket, expectedDiagnostic) {
  const location = new URL(href);
  const nodes = Object.fromEntries(["live-status", "live-socket-diagnostic"].map(id =>
    [id, {textContent: ""}]));
  const sockets = [];
  let requests = 0;
  class MockSocket {
    static OPEN = 1;
    constructor(url, protocols) {
      this.url = url;
      this.protocols = protocols;
      this.protocol = "sirus-live";
      this.readyState = 1;
      this.handlers = {};
      sockets.push(this);
    }
    addEventListener(type, fn) { this.handlers[type] = fn; }
    close() { this.readyState = 3; }
    emit(type, detail = {}) { this.handlers[type](detail); }
  }
  vm.runInNewContext(source, {
    WebSocket: MockSocket, URL, Date,
    document: {readyState: "complete", getElementById: id => nodes[id] ?? null},
    window: {
      location,
      setTimeout() { return 1; }, clearTimeout() {},
      setInterval() { return 2; }, clearInterval() {},
      addEventListener() {}
    },
    fetch: async () => {
      requests++;
      return {ok: true, json: async () => ({token: "fixture-signed-token"})};
    }
  });
  for (let i = 0; i < 6; i++) await new Promise(setImmediate);
  if (expectedSocket) {
    assert.equal(requests, 1);
    assert.equal(sockets.length, 1);
    assert.equal(sockets[0].url, expectedSocket);
    assert.equal(Array.from(sockets[0].protocols).join(","),
                 "sirus-live,sirus-auth.fixture-signed-token");
    sockets[0].emit("open");
    sockets[0].emit("message", {data: JSON.stringify({type: "ready"})});
    assert.equal(nodes["live-status"].textContent, "Live: connected (WebSocket)");
    assert.equal(nodes["live-socket-diagnostic"].textContent, expectedDiagnostic);
  } else {
    assert.equal(requests, 0);
    assert.equal(sockets.length, 0);
    assert.equal(nodes["live-status"].textContent, "Live: HTTPS required");
  }
}

async function main() {
  await check("http://127.0.0.1:18080/", "ws://127.0.0.1:18080/live",
              "Local WS diagnostic: connected; signed ready frame received");
  await check("https://sirussofts.com/Gentoo/", "wss://sirussofts.com/Gentoo/live",
              "WSS diagnostic: connected; signed ready frame received");
  await check("http://sirussofts.com/Gentoo/", null);
  await check("http://127.0.0.1:18081/", null);
  await check("http://192.168.1.10:18080/", null);
  await check("http://localhost:18080/", null);
  console.log("PASS direct loopback WS and public WSS; plaintext denied on all other hosts");
}
main().catch(error => { console.error(error); process.exitCode = 1; });
