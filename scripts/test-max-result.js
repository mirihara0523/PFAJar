"use strict";
const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");
const handlers = {};
const elements = {};
let activated = 0;
let refreshed = 0;
const context = {
  document: { getElementById(id) {
    return elements[id] || (elements[id] = {
      classList: { add() {}, remove() {} }, style: {}, addEventListener() {},
    });
  } },
  require(name) {
    if (name === "electron") return { ipcRenderer: { on(n, fn) { handlers[n] = fn; }, once() {} } };
    if (name === "./project") return { isActive: () => true, refreshProjectIndex() { refreshed++; return Promise.resolve(); } };
    if (name === "./pipeline_runs") return { setActiveRunRel() { activated++; } };
    if (name === "./pipeline_run") return { ensureRunModeUi() {} };
    if (name === "./project_index_busy") return { populatePage() {} };
    if (name === "fs" || name === "path") return require(name);
    return {};
  },
};
vm.createContext(context);
vm.runInContext(fs.readFileSync(path.join(__dirname, "../js/max.js"), "utf8"), context);
context.lastRunRel = "max/test";
for (const payload of [{ ok: false, message: "partial failure" }, undefined]) {
  handlers.maxResult({}, payload);
  assert.equal(activated, 0, "failed runs must not become active");
  assert.equal(refreshed, 0);
  assert(elements.loadmessage.textContent);
  assert.equal(elements.run.innerHTML, "Run");
}
handlers.maxResult({}, { ok: true });
assert.equal(activated, 1);
assert.equal(refreshed, 1);
console.log("MAX result UI: failure, missing result, success passed");
