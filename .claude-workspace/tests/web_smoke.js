// Smoke test for web/app.js: run it against a permissive fake DOM, render graph, timeline and sessions with a
// real /api/state payload, and fail on the first runtime error. Usage: node web_smoke.js app.js state.json
const fs = require("fs"), vm = require("vm");
const els = new Map();
function el(tag = "div") {
  const kids = [], attrs = {}, cls = new Set();
  const e = {
    tagName: tag, children: kids, childNodes: kids, style: {}, dataset: {}, hidden: false, checked: false, value: "",
    textContent: "", innerHTML: "", clientWidth: 900, clientHeight: 500, isConnected: true,
    classList: { add: (...c) => c.forEach((x) => cls.add(x)), remove: (...c) => c.forEach((x) => cls.delete(x)),
      toggle: (c, on) => (on ?? !cls.has(c)) ? cls.add(c) : cls.delete(c), contains: (c) => cls.has(c) },
    setAttribute: (k, v) => { attrs[k] = String(v); if (k === "class") { cls.clear(); String(v).split(/\s+/).filter(Boolean).forEach((x) => cls.add(x)); } }, getAttribute: (k) => attrs[k] ?? null, removeAttribute: (k) => delete attrs[k],
    append: (...n) => kids.push(...n), appendChild: (n) => (kids.push(n), n), prepend: (...n) => kids.unshift(...n),
    insertBefore: (n, ref) => { if (ref === undefined) throw new Error("insertBefore(ref undefined)"); kids.push(n); return n; },
    remove() {}, replaceChildren: (...n) => { kids.length = 0; kids.push(...n); },
    querySelector: (s) => { for (const k of kids) if (k._sel === s || (s.startsWith(".") && k.classList.contains(s.slice(1)))) return k; return null; },
    querySelectorAll: () => [], addEventListener() {}, removeEventListener() {}, closest: () => null, contains: () => false,
    getBoundingClientRect: () => ({ width: 100, height: 40, left: 0, top: 0 }), scrollIntoView() {}, focus() {},
    getTotalLength: () => 100, getPointAtLength: () => ({ x: 0, y: 0 }), animate: () => ({ onfinish: null, cancel() {} }),
  };
  Object.defineProperty(e, "className", { get: () => [...cls].join(" "), set: (v) => { cls.clear(); String(v).split(/\s+/).filter(Boolean).forEach((x) => cls.add(x)); } });
  return e;
}
const document = {
  getElementById: (id) => { if (!els.has(id)) els.set(id, el()); return els.get(id); },
  createElement: (t) => el(t), createElementNS: (_, t) => el(t), querySelector: () => null, querySelectorAll: () => [],
  addEventListener() {}, body: el("body"), documentElement: el("html"), hidden: false, title: "",
};
const ctx = {
  document, window: null, console, setTimeout: () => 0, clearTimeout() {}, setInterval: () => 0, clearInterval() {},
  requestAnimationFrame: () => 0, cancelAnimationFrame() {}, location: { hash: "", pathname: "/", search: "" },
  history: { replaceState() {} }, localStorage: { getItem: () => null, setItem() {} }, innerWidth: 1400, innerHeight: 900,
  matchMedia: () => ({ matches: false, addEventListener() {} }), addEventListener() {}, CSS: { escape: (s) => s },
  fetch: () => new Promise(() => {}), EventSource: function () { return { addEventListener() {}, close() {} }; },
  Notification: { permission: "denied" }, navigator: {}, URLSearchParams, Date, Math, JSON, Map, Set, Promise, Object, Array, String, Number,
  performance: { now: () => 0 }, ResizeObserver: function () { return { observe() {} }; },
};
ctx.window = ctx; ctx.self = ctx; ctx.globalThis = ctx;
vm.createContext(ctx);
const src = fs.readFileSync(process.argv[2], "utf8") + `
;STATE = ${fs.readFileSync(process.argv[3], "utf8")}; STATE.prs = undefined;
renderGraph(); renderTimeline(); renderSessions();
ME = { paired: true }; ARMED.sid = "smoke-wait"; ARMED.until = Date.now() + 4000; renderSessions();
if (!$("sessions").innerHTML.includes("Confirm dismiss") || !$("sessions").innerHTML.includes('data-dismiss="smoke-wait"')) throw new Error("dismiss buttons not rendered");
"rendered";`;
// Message edges expire, and the LLM card shows "offline" with the detail in the themed tooltip (data-tip), not inline.
const extra = `
(() => {
  const s0 = STATE.sessions[0] || (() => { throw new Error("smoke state has no session"); })();
  const sid = s0.id, fail = (m) => { throw new Error(m); };
  const had = s0.messages;
  s0.messages = [{ ts: STATE.now - 5, from: sid, to: "fresh-bot" }, { ts: STATE.now - 600, from: sid, to: "old-bot" }];
  renderGraph();
  if (!G.nodes.has("msg:" + sid + ":fresh-bot")) fail("fresh message recipient not drawn");
  if (G.nodes.has("msg:" + sid + ":old-bot")) fail("expired message recipient still drawn");
  STATE.now += 120; renderGraph();
  if (G.nodes.has("msg:" + sid + ":fresh-bot")) fail("message recipient did not expire");
  s0.messages = had;
  return fetchLLM();
})()`;
ctx.fetch = (url) => Promise.resolve({ json: () => Promise.resolve(String(url).startsWith("/api/research") ? ctx.RS_CUR : String(url).startsWith("/api/llm") ? { claude: { available: false }, endpoints: [
  { name: "remote", label: "GPU", url: "http://gpu.example:11434", up: false, error: "Connection refused", mode: "auto", models: {} },
  { name: "local", label: "CPU", url: "http://127.0.0.1:11434", up: true, version: "0.5", mode: "auto", models: { a: { present: true, name: "m1" } } }] } : {}) });
ctx.fetchLLM = async () => {
  await vm.runInContext("loadLLM()", ctx);
  const html = els.get("llmcard").innerHTML;
  const down = html.split('<div class="llmrow').find((x) => x.includes("GPU")) || "";
  if (!/<span class="nm">offline<\/span>/.test(down)) throw new Error("down endpoint not shown as offline: " + html);
  if (!/data-tip="[^"]*Error: Connection refused/.test(down)) throw new Error("error detail missing from themed tooltip");
  if (/Connection refused[^"]*<\/span>|class="hint"/.test(html.replace(/data-tip="[^"]*"/g, ""))) throw new Error("error text shown inline");
  if (/\btitle="/.test(html)) throw new Error("native title= used on the LLM card (not themed)");
  if (!/<span class="nm">online<\/span>/.test(html)) throw new Error("up endpoint not online");
  return "llm ok";
};
// Research tab: optional 4th argument = JSON file with /api/research envelopes {m1, m2, hostile} (data already shaped by the server).
const kidsOf = (e) => (e.children || []).filter((c) => c && typeof c === "object");
const allOf = (e) => [e, ...kidsOf(e).flatMap(allOf)];
const textOf = (e) => allOf(e).map((n) => n.textContent || "").join(" ");
const RS_IDS = ["rshead", "rsgate", "rsquota", "rslanes", "rslast", "rserrors"];
async function researchChecks(fx) {
  const fail = (m) => { throw new Error("research: " + m); };
  const run = (c) => vm.runInContext(c, ctx);
  const show = async (env) => { ctx.RS_CUR = env; await run("loadResearch()"); };
  const need = (id, re, why) => { if (!re.test(textOf(els.get(id)))) fail(`${why || id}: missing ${re} in "${textOf(els.get(id)).slice(0, 200)}"`); };
  ctx.RS_CUR = fx.m2; run('setView("research")');
  if (run("VIEW") !== "research" || run("$('mcgrid').dataset.view") !== "research") fail("view did not switch");
  await show(fx.m2);
  if (els.get("tab-research").hidden) fail("tab hidden although enabled");
  need("rslast", /general/, "last-run table lists the agent"); need("rslast", /hobby/); need("rslast", /agent timed out/);
  need("rslast", /held for quiet hours/, "digest status");
  need("rshead", /Next run/); need("rshead", /valkyrie-dispatch ok/); need("rsgate", /Waiting for the gate: attempt 2 of 3/);
  need("rsgate", /20% weekly floor/, "weekly floor tick label"); need("rsgate", /gate floor/);
  need("rsquota", /Quota today/); need("rserrors", /agent timed out/); need("rserrors", /skipped/);
  const lanes = els.get("rslanes");
  if (!kidsOf(lanes).length) fail("lanes empty");
  const bars = allOf(lanes).filter((n) => /\bbar-sub\b/.test(n.className || ""));
  if (bars.length < 2) fail(`expected agent bars in the lanes, got ${bars.length}`);
  if (!bars.some((n) => /\brs-bad\b/.test(n.className)) || !bars.some((n) => /\brs-ok\b/.test(n.className))) fail("bar status classes missing");
  if (!allOf(lanes).some((n) => /\brs-dot\b/.test(n.className || ""))) fail("gate dots missing");
  if (!allOf(lanes).some((n) => /\bnowline\b/.test(n.className || ""))) fail("now line missing");
  if (!allOf(els.get("rsgate")).some((n) => n.tagName === "progress" && /\blim\b/.test(n.className))) fail("gate bars do not reuse progress.lim");
  if (!allOf(els.get("rsgate")).some((n) => /\bfloor-tick\b/.test(n.className || ""))) fail("floor ticks missing");
  const dot = els.get("rsdot");
  if (dot.hidden || !/rs-warn/.test(dot.className)) fail("badge should be amber for a partial conservative run: " + dot.className);
  for (const id of RS_IDS) if (els.get(id).innerHTML) fail(`${id} was filled with innerHTML`);
  // a run in progress: elapsed time, and the "possibly stuck" label only after 45 minutes
  const live = (secs) => {
    const d = JSON.parse(JSON.stringify(fx.m2.data)), now = fx.m2.server_now;
    d.current_run = { ...d.last_run, id: "00000000-0000-4000-8000-000000000077", status: "running", started_at: now - secs, finished_at: null, duration_s: null,
      digest: null, agents: [{ ...d.last_run.agents[0], status: "running", started_at: now - secs, finished_at: null, error_code: null }] };
    return { ...fx.m2, data: d };
  };
  await show(live(20 * 60));
  need("rslast", /running for/); if (/possibly stuck/.test(textOf(els.get("rslast")))) fail("20 min run flagged as stuck");
  if (dot.hidden || !/rs-ok/.test(dot.className) || !/pulse/.test(dot.className)) fail("running badge should be green and pulsing: " + dot.className);
  await show(live(50 * 60));
  need("rslast", /possibly stuck/); need("rslast", /docker compose restart app/);
  // M1: partial data and the M2 note
  await show(fx.m1);
  need("rslanes", /arrives with valkyrie-dispatch M2/); need("rslast", /No run history yet/); need("rsgate", /Run/); need("rsquota", /weekly limit/);
  if (!dot.hidden) fail("badge should be hidden for a healthy M1 payload: " + dot.className);
  // failure states
  await show({ state: "unreachable", data: null });
  need("rslast", /not reachable/); if (dot.hidden || !/rs-bad/.test(dot.className)) fail("unreachable badge should be red");
  if (kidsOf(els.get("rsgate")).length) fail("gate card should be empty without data");
  await show({ state: "unauthorized", data: null }); need("rslast", /Token rejected/);
  await show({ state: "incompatible", data: null }); need("rslast", /monitor\.v1/);
  await show({ state: "not_configured", data: null }); need("rslast", /niu-dash research-token/); need("rslast", /gen-token/);
  await show({ ...fx.m2, state: "unreachable", stale: true });
  need("rshead", /Showing data as of/); if (!els.get("rsbody").classList.contains("stale")) fail("stale data not greyed");
  // hostile payload (already shaped by the server): nothing leaks into the page, nothing is written as HTML
  await show(fx.hostile);
  for (const id of RS_IDS) {
    const t = textOf(els.get(id));
    if (/sk-ant|<img|<script|onerror|Secret title|evil\.example|webhook|rd_pat_/.test(t)) fail(`hostile text reached #${id}: ${t.slice(0, 120)}`);
    if (els.get(id).innerHTML) fail(`${id} used innerHTML`);
  }
  // disabled: the tab goes away and the view falls back to Agents
  await show({ state: "disabled", data: null });
  if (!els.get("tab-research").hidden || run("VIEW") !== "agents") fail("disabled research should hide the tab and leave the view");
  return "research ok";
}
(async () => {
  try {
    console.log(vm.runInContext(src, ctx, { filename: "app.js" }));
    console.log(await vm.runInContext(extra, ctx, { filename: "extra.js" }));
    if (process.argv[4]) console.log(await researchChecks(JSON.parse(fs.readFileSync(process.argv[4], "utf8"))));
  } catch (e) { console.log("ERROR:", e.message, (e.stack || "").split("\n")[1]); process.exit(1); }
})();
