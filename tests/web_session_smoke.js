"use strict";
const fs = require("node:fs");
const path = require("node:path");
const assert = require("node:assert/strict");
const source = fs.readFileSync(path.join(__dirname, "../skill/web/app.js"), "utf8");
const start = source.indexOf("  function renderSession()");
const end = source.indexOf("  function modelChoices()", start);
assert.ok(start >= 0 && end > start);
// Execute the actual renderer with minimal DOM nodes; this is not a browser test.
const render = new Function("state", "$", source.slice(start, end) + ";renderSession();");
function view(session) {
  const nodes = Object.fromEntries(["health-context", "health-compactions", "health-note"].map(id => [id, {textContent: ""}]));
  render({session}, id => nodes[id]);
  return nodes;
}
assert.equal(view(null)["health-context"].textContent, "알 수 없음");
assert.equal(view({context_percent: 69})["health-context"].textContent, "69.0% (최근 관측)");
const warning = view({context_percent: 70, auto_compactions: 10, count_scope: "observed_since_attach",
  alerts: [{message: "인계를 준비하세요", resolved_at: null}], recommended_at: "observed"});
assert.ok(warning["health-note"].textContent.includes("인계를 준비하세요"));
assert.ok(warning["health-note"].textContent.includes("새 채팅"));
assert.equal(warning["health-compactions"].textContent, "10회 이상");
assert.ok(!view({alerts: [{message: "resolved-warning", resolved_at: "saved"}]})["health-note"].textContent.includes("resolved-warning"));
assert.equal(view({context_percent: 101})["health-context"].textContent, "알 수 없음");
console.log("Web session renderer: 8 assertions passed (DOM stub; not a real browser).");
