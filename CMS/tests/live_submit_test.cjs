/* Regression: HTMLFormElement named controls can mask .action. */
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const handlers = {};
const requests = [];
const feedback = {textContent: ""};
const button = {disabled: false};
const form = {
  action: {name: "action", value: "enqueue_node_job"},
  getAttribute: name => name === "action" ? "/admin/infrastructure" : null,
  querySelector: sel => sel.includes("button") ? button : feedback,
};
const doc = {
  addEventListener: (name, fn) => { handlers[name] = fn; },
  querySelector: () => null,
  dispatchEvent: () => {},
};
const ctx = {
  document: doc,
  window: {addEventListener: () => {}},
  location: new URL("https://sirussofts.com/admin/infrastructure"),
  URL,
  FormData: class {constructor(form) {this.form = form;}},
  CustomEvent: class {},
  fetch: async (url, options) => {
    requests.push({url, options});
    return {ok: true, status: 202, redirected: false,
      headers: {get: () => "application/json"},
      json: async () => ({ok: true, request_id: "abcdef0123456789"})};
  },
};
vm.runInNewContext(fs.readFileSync(require("node:path").join(__dirname, "../assets/live.js"), "utf8"), ctx);
(async () => {
  await handlers.submit({target: {closest: () => form}, preventDefault() {}});
  assert.equal(requests.length, 1);
  assert.equal(requests[0].url, "https://sirussofts.com/admin/infrastructure");
  assert.equal(requests[0].options.method, "POST");
  assert.match(feedback.textContent, /queued abcdef01/);
  assert.equal(button.disabled, false);
  console.log("PASS: delegated form submits to its actual action attribute");
})().catch(error => {console.error(error); process.exitCode = 1;});
