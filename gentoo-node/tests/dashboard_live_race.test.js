// Regression: stale HTTPS fallback or old socket close must never mask a live WS.
"use strict";
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");

async function main() {
  const source = fs.readFileSync(path.join(__dirname, "..", "dashboard_live.js"), "utf8");
  const ids = ["live-status", "live-cpu", "live-memory", "live-disk",
               "live-uptime", "live-history-status", "live-updated"];
  const nodes = Object.fromEntries(ids.map(id => [id, {textContent:""}]));
  const timeouts = new Map();
  let timerId = 0, intervalId = null, pagehide = null;
  let fallbackResolve = null;
  const sockets = [];
  class Socket {
    static OPEN = 1;
    constructor(url, protocols) {
      this.url=url; this.protocols=protocols; this.protocol="sirus-live";
      this.readyState=Socket.OPEN; this.handlers={};
      sockets.push(this);
    }
    addEventListener(name, fn) {this.handlers[name]=fn;}
    emit(name, detail={}) {
      assert.equal(typeof this.handlers[name], "function");
      if (name==="close") this.readyState=3;
      this.handlers[name](detail);
    }
    close() {this.readyState=3;}
  }
  const snapshot = (cpu=22) => ({
    type: "snapshot", metrics: {
      cpu_pct:cpu, memory_used_bytes:1073741824, disk_used_bytes:1,
      disk_total_bytes:10, uptime_seconds:36000, gpus:[], services:[], applications:[]
    }, hardware:{ram_bytes:8589934592}, jobs:[], graph:[], sync:"fixture history"
  });
  const sandbox = {
    URL, Date, WebSocket:Socket,
    document:{readyState:"complete", getElementById:id=>nodes[id]||null},
    window:{
      location:{href:"https://sirussofts.com/Gentoo/",protocol:"https:"},
      setTimeout(fn,ms) {const id=++timerId;timeouts.set(id,{fn,ms});return id;},
      clearTimeout(id) {timeouts.delete(id);},
      setInterval(fn,ms) {assert.equal(ms,5000);intervalId=++timerId;return intervalId;},
      clearInterval(id) {if(id===intervalId)intervalId=null;},
      addEventListener(name,fn) {if(name==="pagehide")pagehide=fn;}
    },
    fetch: async url => {
      if (String(url).includes("snapshot=1")) {
        return await new Promise(resolve => {fallbackResolve=resolve;});
      }
      return {ok:true,json:async()=>({token:"signed-fixture-token"})};
    }
  };
  const tick=async()=>{for(let n=0;n<8;n++)await new Promise(setImmediate);};
  vm.runInNewContext(source,sandbox);
  await tick();
  assert.equal(sockets.length,1);
  const old=sockets[0];
  old.emit("close");
  await tick();
  assert.ok(fallbackResolve,"Fallback must be awaiting an HTTPS response");
  assert.ok(intervalId!==null,"Fallback interval must exist");
  const retry=[...timeouts.values()].find(entry=>entry.ms===1000);
  assert.ok(retry,"Reconnection timer missing");
  retry.fn();
  await tick();
  assert.equal(sockets.length,2);
  const active=sockets[1];
  active.emit("open");
  active.emit("message",{data:JSON.stringify({type:"ready"})});
  assert.equal(nodes["live-status"].textContent,"Live: connected (WebSocket)");
  assert.equal(intervalId,null,"Fallback timer must stop on a healthy socket");
  fallbackResolve({ok:true,json:async()=>snapshot(17)});
  await tick();
  assert.equal(nodes["live-status"].textContent,"Live: connected (WebSocket)",
    "An in-flight HTTPS fallback must not mask a connected socket");
  active.emit("message",{data:JSON.stringify(snapshot(58))});
  assert.equal(nodes["live-cpu"].textContent,"58.0%");
  assert.equal(nodes["live-status"].textContent,"Live: connected (WebSocket)");
  old.emit("close");
  await tick();
  assert.equal(nodes["live-status"].textContent,"Live: connected (WebSocket)",
    "Obsolete socket closes must not force another fallback");
  assert.equal(intervalId,null);
  pagehide();
  console.log("PASS reconnect wins against stale fallback HTTP response and obsolete close");
}
main().catch(e=>{console.error(e);process.exitCode=1;});
