// Smoke test for web/app.js: run it against a permissive fake DOM, render graph, timeline and sessions with a
// real /api/state payload, and fail on the first runtime error. Usage: node web_smoke.js app.js state.json
const fs = require("fs"), vm = require("vm");
const els = new Map();
function el(tag = "div") {
  const kids = [], attrs = {}, cls = new Set();
  const e = {
    tagName: tag, children: kids, childNodes: kids, style: {}, dataset: {}, hidden: false, checked: false, value: "",
    textContent: "", clientWidth: 900, clientHeight: 500, isConnected: true,
    classList: { add: (...c) => c.forEach((x) => cls.add(x)), remove: (...c) => c.forEach((x) => cls.delete(x)),
      toggle: (c, on) => (on ?? !cls.has(c)) ? cls.add(c) : cls.delete(c), contains: (c) => cls.has(c) },
    setAttribute: (k, v) => { attrs[k] = String(v); if (k === "class") { cls.clear(); String(v).split(/\s+/).filter(Boolean).forEach((x) => cls.add(x)); } }, getAttribute: (k) => attrs[k] ?? null, removeAttribute: (k) => delete attrs[k],
    append: (...n) => kids.push(...n), appendChild: (n) => (kids.push(n), n), prepend: (...n) => kids.unshift(...n),
    insertBefore: (n, ref) => { if (ref === undefined) throw new Error("insertBefore(ref undefined)"); kids.push(n); return n; },
    remove() {}, replaceChildren: (...n) => { kids.length = 0; kids.push(...n); },
    querySelector: (s) => { for (const k of kids) if (k._sel === s || (s.startsWith(".") && k.classList.contains(s.slice(1)))) return k; return null; },
    querySelectorAll: () => [], addEventListener(t, f) { (e._ls[t] = e._ls[t] || []).push(f); }, removeEventListener() {}, closest: () => null, contains: () => false,
    getBoundingClientRect: () => ({ width: 100, height: 40, left: 0, top: 0 }), scrollIntoView() {}, focus() {},
    _ls: {}, showModal() { e.open = true; }, close() { e.open = false; e.closed = (e.closed || 0) + 1; },
    getTotalLength: () => 100, getPointAtLength: () => ({ x: 0, y: 0 }), animate: () => ({ onfinish: null, cancel() {} }),
  };
  let inner = "";   // innerHTML is trapped while the Topics view renders (ctx.TP_TRAP): topic text must never become HTML
  Object.defineProperty(e, "innerHTML", { get: () => inner, set: (v) => { if (ctx.TP_TRAP) throw new Error("innerHTML written while rendering Topics"); inner = String(v); } });
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
ctx.fetch = (url) => Promise.resolve({ json: () => Promise.resolve(String(url).startsWith("/api/topics") ? ctx.TP_CUR : String(url).startsWith("/api/research") ? ctx.RS_CUR : String(url).startsWith("/api/llm") ? { claude: { available: false }, endpoints: [
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
// Timeline: agent lanes carry the model ("designer [opus-5-5]"), muted and italic when it is a guess, plus a themed tooltip.
function timelineChecks() {
  const fail = (m) => { throw new Error("timeline: " + m); };
  const run = (c) => vm.runInContext(c, ctx);
  run(`(() => {
    const s0 = STATE.sessions[0], t = STATE.now;
    globalThis.TL_SAVED = s0.subagents;
    s0.subagents = [
      { id: "tl-a", type: "designer", start: t - 300, stop: t - 60, tools: [], parent: s0.id, model: "claude-opus-5-5", model_src: "actual" },
      { id: "tl-b", type: "implementer", start: t - 200, stop: null, tools: [], parent: s0.id, model: "claude-sonnet-5-5-20261001", model_src: "definition" },
      { id: "tl-c", type: "test-reviewer", start: t - 100, stop: null, tools: [], parent: s0.id, model: null, model_src: null },
      { id: "tl-d", type: "an-extremely-long-agent-type-name", start: t - 50, stop: null, tools: [], parent: s0.id, model: "claude-haiku-5-5", model_src: "inherited" },
    ];
    renderTimeline();
  })()`);
  const nodes = allOf(els.get("timeline"));
  const label = (type) => nodes.find((n) => n.tagName === "text" && (n.getAttribute("data-tip") || "").split("\n")[0] === type);
  const tag = (n) => kidsOf(n).find((k) => k.tagName === "tspan" && /\btlmodel\b/.test(k.className || k.getAttribute("class") || ""));
  const a = label("designer");
  if (!a || !tag(a) || tag(a).textContent !== " [opus-5-5]") fail("actual model label: " + (a && textOf(a)));
  if (/\bguess\b/.test(tag(a).getAttribute("class"))) fail("actual model marked as a guess");
  if (a.getAttribute("data-tip") !== "designer\nmodel: opus-5-5") fail("actual model tooltip: " + a.getAttribute("data-tip"));
  const b = label("implementer");
  if (!b || !tag(b) || tag(b).textContent !== " [sonnet-5-5]") fail("date suffix not stripped: " + (b && textOf(b)));
  if (!/\bguess\b/.test(tag(b).getAttribute("class"))) fail("definition model not marked as a guess");
  if (!/model: sonnet-5-5 \(from agent definition\)/.test(b.getAttribute("data-tip"))) fail("definition tooltip: " + b.getAttribute("data-tip"));
  const c = label("test-reviewer");
  if (!c || tag(c) || kidsOf(c).length !== 1 || c.getAttribute("data-tip") !== "test-reviewer") fail("lane without a model should have no model tag");
  const d = label("an-extremely-long-agent-type-name");
  if (!d || !tag(d) || !/model: haiku-5-5 \(inherited\)/.test(d.getAttribute("data-tip"))) fail("inherited model lane");
  if (kidsOf(d)[0].textContent.length >= "an-extremely-long-agent-type-name".length) fail("long agent type not truncated to make room for the model");
  const bar = nodes.find((n) => n.tagName === "rect" && /^implementer\nmodel: sonnet-5-5/.test(n.getAttribute("data-tip") || ""));
  if (!bar) fail("bar tooltip lacks the model");
  run("STATE.sessions[0].subagents = globalThis.TL_SAVED; renderTimeline()");
  return "timeline ok";
}
// Research tab: optional 4th argument = JSON file with /api/research envelopes {m1, m2, hostile} (data already shaped by the server).
const kidsOf = (e) => (e.children || []).filter((c) => c && typeof c === "object");
const allOf = (e) => [e, ...kidsOf(e).flatMap(allOf)];
const textOf = (e) => allOf(e).map((n) => n.textContent || "").join(" ");
const RS_IDS = ["rshead", "rsgate", "rsquota", "rslanes", "rsgatelog", "rslast", "rserrors"];
// Gate log: one row per gate check, newest first, linked to the gate dots in the lanes graph by a shared data-gi index.
async function gateLogChecks(fx) {
  const fail = (m) => { throw new Error("research gate log: " + m); };
  const run = (c) => vm.runInContext(c, ctx);
  const html = fs.readFileSync(require("path").join(require("path").dirname(process.argv[2]), "index.html"), "utf8");
  if (!/id="rslanes"[^>]*><\/div>\s*<div id="rsgatelog"/.test(html)) fail("#rsgatelog must follow #rslanes directly in index.html");
  const fire = (id, type, ev) => (els.get(id)._ls[type] || []).forEach((f) => f(ev));
  const lanes = els.get("rslanes"), log = els.get("rsgatelog");
  const dots = allOf(lanes).filter((n) => /\brs-dot\b/.test(n.className || ""));
  const rows = allOf(log).filter((n) => /\brsgrow\b/.test(n.className || ""));
  if (!/Gate log/.test(textOf(log))) fail("title missing");
  if (rows.length !== 2 || dots.length !== 2) fail(`expected 2 rows and 2 dots, got ${rows.length} / ${dots.length}`);
  if (rows.length > 12) fail("more than 12 rows");
  if (rows[0].getAttribute("data-gi") !== "0" || rows[1].getAttribute("data-gi") !== "1") fail("rows not newest first / indexes: " + rows.map((r) => r.getAttribute("data-gi")));
  if (!/5h 40% · wk 60%/.test(textOf(rows[0])) || !/usage API/.test(textOf(rows[0])) || !/Run/.test(textOf(rows[0]))) fail("row text: " + textOf(rows[0]));
  if (!/5h 88% · wk 58%/.test(textOf(rows[1])) || !/daily Claude session cap reached/.test(textOf(rows[1]))) fail("second row text: " + textOf(rows[1]));
  for (const d of dots) {
    if (!rows.some((r) => r.getAttribute("data-gi") === d.getAttribute("data-gi"))) fail("dot without a matching row");
    if (d.getAttribute("tabindex") !== "0" || !/^Gate check/.test(d.getAttribute("aria-label") || "") || d.getAttribute("title") != null || !d.getAttribute("data-tip")) fail("dot attributes (tabindex / aria-label / no title / data-tip)");
  }
  const dotOf = (gi) => dots.find((d) => d.getAttribute("data-gi") === gi), rowOf = (gi) => rows.find((r) => r.getAttribute("data-gi") === gi);
  const on = (el) => ({ target: { closest: () => el } });
  const list = allOf(log).find((n) => /\brsgl\b/.test(n.className || ""));
  Object.assign(rows[1], { offsetTop: 500, offsetHeight: 20 }); Object.assign(list, { scrollTop: 0, clientHeight: 100 });
  fire("rslanes", "pointerover", on(dotOf("1")));
  if (!rowOf("1").classList.contains("hl") || rowOf("0").classList.contains("hl")) fail("hovering a dot did not highlight its row");
  if (!dotOf("1").classList.contains("hl") || dotOf("1").getAttribute("r") !== "7") fail("highlighted dot not enlarged");
  if (list.scrollTop !== 420) fail("the row was not scrolled into view inside its list: " + list.scrollTop);
  fire("rslanes", "pointerout", on(dotOf("1")));
  if (rowOf("1").classList.contains("hl") || dotOf("1").classList.contains("hl") || dotOf("1").getAttribute("r") !== "4") fail("mouseout did not clear");
  fire("rsgatelog", "pointerover", on(rowOf("0")));
  if (!dotOf("0").classList.contains("hl") || !rowOf("0").classList.contains("hl")) fail("hovering a row did not highlight its dot");
  fire("rsgatelog", "focusout", on(rowOf("0")));
  if (dotOf("0").classList.contains("hl")) fail("blur did not clear the dot");
  fire("rslanes", "focusin", on(dotOf("0")));
  if (!rowOf("0").classList.contains("hl")) fail("keyboard focus on a dot did not highlight the row");
  fire("rslanes", "pointerleave", {});
  if (rowOf("0").classList.contains("hl")) fail("pointerleave did not clear");
  fire("rslanes", "pointerover", { target: { closest: () => null } });   // elsewhere in the graph: nothing happens
  // order comes from the time, not the position in the data
  const d = JSON.parse(JSON.stringify(fx.m2.data)); d.gate.recent.reverse();
  ctx.RS_CUR = { ...fx.m2, data: d };
  await run("loadResearch()");
  const r2 = allOf(els.get("rsgatelog")).filter((n) => /\brsgrow\b/.test(n.className || ""));
  if (r2[0].getAttribute("data-gi") !== "1" || r2[1].getAttribute("data-gi") !== "0") fail("newest first with reversed data: " + r2.map((r) => r.getAttribute("data-gi")));
  ctx.RS_CUR = fx.m2;
  await run("loadResearch()");
}
// Unreviewed marker: only release sessions are flagged; release sessions carry a small tag; the Today line counts only those.
function flagChecks() {
  const fail = (m) => { throw new Error("flag: " + m); };
  const run = (c) => vm.runInContext(c, ctx);
  const cards = (patch) => { run(`(() => { Object.assign(STATE.sessions[0], ${JSON.stringify(patch)}); renderSessions(); })()`); return els.get("sessions").innerHTML; };
  const saved = JSON.stringify(run("(({ edited, release, flagged, ran }) => ({ edited, release, flagged, ran }))(STATE.sessions[0])"));
  let h = cards({ edited: true, release: false, flagged: false, ran: [] });
  if (/class="flagmsg"|class="reltag"|without both reviewers/.test(h)) fail("an edit-only session must show no marker");
  h = cards({ edited: true, release: true, flagged: true, ran: ["security-reviewer"] });
  if (!/<div class="flagmsg">⚠ release without both reviewers<\/div>/.test(h) || /edited files without/.test(h)) fail("flag message for a release session");
  if (!/class="reltag"[^>]*>release<\/span>/.test(h)) fail("release tag missing");
  h = cards({ edited: true, release: true, flagged: false, ran: ["security-reviewer", "test-reviewer"] });
  if (/flagmsg/.test(h) || !/release ✓ reviewed/.test(h)) fail("a reviewed release shows the check mark and no flag");
  run(`(() => { const s = STATE.sessions[0], o = ${saved}; Object.assign(s, o); renderSessions(); })()`);
  const today = (patch) => { run(`(() => { STATE.today = Object.assign({ sessions: 3, subagent_runs: 4, runs: [], edited: 2, reviewed: 0, flagged: [], flagged_projects: [] }, ${JSON.stringify(patch)}); renderToday(); })()`); return els.get("today").innerHTML; };
  h = today({});
  if (/unreviewed/.test(h) || !/<b>0\/2<\/b> reviewed/.test(h) || /class="bad"[^>]*><b>0\/2/.test(h)) fail("edited sessions without a release are counted but not flagged: " + h);
  h = today({ flagged: ["x"], flagged_projects: ["PERSONAL/app"] });
  if (!/<b>1<\/b> unreviewed/.test(h) || !/Release sessions without both reviewers: PERSONAL\/app/.test(h)) fail("Today unreviewed span: " + h);
  return "flag ok";
}
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
  need("rsgate", /gate floors: 5-hour ≥ \d+% · weekly ≥ \d+% remaining/, "floors as plain text");
  need("rsgate", /\(overnight run\)/, "pending gate carries the run kind");
  need("rsquota", /Quota today/); need("rserrors", /agent timed out/); need("rserrors", /skipped/);
  const lanes = els.get("rslanes");
  if (!kidsOf(lanes).length) fail("lanes empty");
  const bars = allOf(lanes).filter((n) => /\bbar-sub\b/.test(n.className || ""));
  if (bars.length < 2) fail(`expected agent bars in the lanes, got ${bars.length}`);
  if (!bars.some((n) => /\brs-bad\b/.test(n.className)) || !bars.some((n) => /\brs-ok\b/.test(n.className))) fail("bar status classes missing");
  if (!allOf(lanes).some((n) => /\brs-dot\b/.test(n.className || ""))) fail("gate dots missing");
  if (!allOf(lanes).some((n) => /\bnowline\b/.test(n.className || ""))) fail("now line missing");
  const gateEl = els.get("rsgate");
  if (allOf(gateEl).some((n) => n.tagName === "progress" || n.tagName === "svg" || /\bfloor-tick\b|\brsmeter\b/.test(n.className || ""))) fail("the gate card still draws Claude limit meters");
  if (/\b5h \d|\bwk \d|Gate log/.test(textOf(gateEl))) fail("recent gate checks are still in the gate card");
  if (!/Gate/.test(textOf(gateEl)) || !/Run/.test(textOf(gateEl))) fail("gate card lost its decision badge");
  await gateLogChecks(fx);
  // run kinds: head line (next slot + brief), run header tag, tag after the lane bar, fixed messages only
  need("rshead", /Next run \d\d:\d\d \(delta\)/, "next slot kind"); need("rshead", /brief stale: overnight run failed/, "stale brief message");
  need("rslast", /overnight/, "run kind tag");
  if (!allOf(lanes).some((n) => /\brs-kindtag\b/.test(n.className || "") && n.textContent === "C")) fail("run kind tag missing next to the lane bar");
  if (!allOf(els.get("rslast")).some((n) => /\bkindtag\b/.test(n.className || ""))) fail("run kind tag missing in the run header");
  need("rsgatelog", /daily Claude session cap reached/, "session_cap reason");
  {
    const d = JSON.parse(JSON.stringify(fx.m2.data)); d.brief = { ...d.brief, stale: false, stale_reason: null, state: "ready" };
    await show({ ...fx.m2, data: d }); need("rshead", /brief ready 08:00/, "ready brief"); need("rshead", /7 items, 2 updates/);
    d.brief = { ...d.brief, state: "scheduled", items: null }; await show({ ...fx.m2, data: d }); need("rshead", /brief due 08:00/);
    await show(fx.m2);
  }
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
  // M1: partial data and the M2 note (no brief, no kinds: the additive fields are optional)
  await show(fx.m1);
  if (/brief|\(overnight\)|\(delta\)/.test(textOf(els.get("rshead")))) fail("M1 head line shows a brief or run kind: " + textOf(els.get("rshead")));
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
  if (/Ignore previous|LEAK|agent said|javascript:|<b>/.test(textOf(els.get("rshead")) + textOf(els.get("rslast")) + textOf(els.get("rsgatelog")))) fail("hostile brief / kind text reached the page");
  // disabled: the tab goes away and the view falls back to Agents
  await show({ state: "disabled", data: null });
  if (!els.get("tab-research").hidden || run("VIEW") !== "agents") fail("disabled research should hide the tab and leave the view");
  return "research ok";
}
// Pure scoring tables for fuzzyScore()/tpMatch() in the Topics section of app.js.
function fuzzyChecks(run, fail) {
  const sc = (q, t) => run(`fuzzyScore(${JSON.stringify(q)}, ${JSON.stringify(t)})`);
  const yes = [
    ["duterte", "Duterte"], ["DUTERTE", "duterte"], ["dute", "Duterte"], ["uter", "Duterte"],       // exact, case, prefix, substring
    ["dutrte", "Duterte"], ["dutert", "Duterte"], ["duertte", "Duterte"], ["duetrte", "Duterte"],     // typo, transposition
    ["resonanse", "Resonance"], ["resonnnce", "Resonance"],                                          // 7+ chars allow 2 edits
    ["cafe", "Café du monde"], ["CAFÉ", "cafe"], ["naive", "naïve"],                                  // accent folding
    ["ff", "Final Fantasy Resonance"], ["ff reso", "Final Fantasy Resonance"], ["fantasy final", "Final Fantasy"],  // initials, AND in any order
    ["ollama mdls", "ollama models"], ["mdls", "models"],                                             // subsequence
    ["a", "apple"], ["ap", "apple"], ["", "anything"], ["   ", "anything"],                            // short prefix, empty query
    ["img", "<img src=x onerror=alert(1)>"], ["onerror", "<img src=x onerror=alert(1)>"], ["ａｂｃ", "abc"],
  ];
  for (const [q, t] of yes) if (!(sc(q, t) > 0)) fail(`fuzzyScore(${JSON.stringify(q)}, ${JSON.stringify(t)}) should match`);
  const no = [
    ["xyz", "Duterte"], ["dutxxxe", "Duterte"], ["ollama zzz", "ollama models"],                        // AND: one bad token fails all
    ["ab", "xaxb"], ["fq", "Final Fantasy"], ["cat", "cut"], ["duk", "Duterte"],                         // short tokens: no typos, no subsequence
    ["!!!", "Duterte"], ["resonance", "Final"], ["fantasyyyy", "Fantasy"],
  ];
  for (const [q, t] of no) if (sc(q, t) !== 0) fail(`fuzzyScore(${JSON.stringify(q)}, ${JSON.stringify(t)}) should not match, got ${sc(q, t)}`);
  const better = [   // [query, better text, worse text]
    ["duterte", "Duterte", "Dutrte"], ["duterte", "Duterte news", "the dutertes"], ["reso", "Resonance", "Xresonance"],
    ["fantasy", "Fantasy", "Fantasyland"], ["models", "models", "modles"],
  ];
  for (const [q, a, b] of better) if (!(sc(q, a) > sc(q, b))) fail(`ranking: ${JSON.stringify(q)} should prefer ${JSON.stringify(a)} (${sc(q, a)}) over ${JSON.stringify(b)} (${sc(q, b)})`);
  // hostile / huge input must neither throw nor hang
  const big = "a".repeat(100000), t0 = Date.now();
  if (sc("a".repeat(500), big) < 0 || sc(big, "a") < 0 || sc("zz", big) !== 0) fail("huge input");
  if (!(sc("é".repeat(30), "é".repeat(5000)) >= 0) || Date.now() - t0 > 2000) fail("huge input too slow: " + (Date.now() - t0) + "ms");
  for (const q of ["\u0000‮", "\ud800", "((((", "[a-", "\\", "😀😀😀", "ǅ"]) { sc(q, "anything <b>x</b> 😀"); sc("x", q); }
  // edit budgets: 4-6 chars allow 1 edit, 7+ allow 2; short tokens never match mid-word or by typo
  for (const [q, t] of [["plxxet", "planet"], ["pxxnt", "plant"], ["x", "axe"], ["e", "Alpha Beta"], ["an", "Final Fantasy"], ["na", "Fantasy"], ["ar", "Final Fantasy"]]) {
    if (sc(q, t) !== 0) fail(`budget: ${JSON.stringify(q)} must not match ${JSON.stringify(t)}, got ${sc(q, t)}`);
  }
  for (const [q, t] of [["plnet", "planet"], ["plxxnetary", "planetary"], ["resonnnce", "Resonance"]]) if (!(sc(q, t) > 0)) fail(`budget: ${JSON.stringify(q)} should match ${JSON.stringify(t)}`);
  // caps: only the first 8 tokens and the first 80 characters of the query count
  const w8 = "alpha beta gamma delta omega sigma theta kappa";
  if (!(sc(w8 + " zzzzzz", w8) > 0)) fail("9th token should be dropped by the token cap");
  if (sc("zzzzzz " + w8, w8) !== 0) fail("a wrong 1st token must still fail");
  if (!(sc("alpha" + " ".repeat(80) + "zzzzzz", "alpha") > 0)) fail("query tail past 80 chars should be ignored");
  if (sc("alpha" + " ".repeat(70) + "zzzzzz", "alpha") !== 0) fail("query inside 80 chars must count");
  const tm = (q, name) => run(`tpMatch({ name: ${JSON.stringify(name)}, keywords: [], excluded_terms: [] }, ${JSON.stringify(q)}).score`);
  if (!(tm("final fantasy", "Final Fantasy") > tm("fantasy final", "Final Fantasy"))) fail("phrase in the name should outrank the same words reversed");
  // emoji-only queries have no tokens: they match nothing (not everything)
  if (sc("😀😀", "anything 😀") !== 0 || run(`tpMatch({ name: "x", keywords: [], excluded_terms: [] }, "😀")`) !== null) fail("emoji-only query should match nothing");
  if (!(sc("😀 anything", "anything") > 0)) fail("emoji tokens are dropped, the rest of the query still counts");
  // malformed rows never throw
  if (run(`tpMatch({ name: "abc", keywords: "abc", excluded_terms: 5 }, "abc")`) === null || run(`tpMatch({ name: "abc", keywords: { a: 1 }, excluded_terms: null }, "abc")`) === null) fail("tpMatch on malformed row");
  // tpMatch: stable fields and name indexes
  const m = run(`(() => { const r = tpMatch({ name: "Final Fantasy Resonance", keywords: ["ff14"], excluded_terms: ["leaks"] }, "reso leaks"); return [r.score > 0, [...r.name], [...r.ex], [...r.kw]]; })()`);
  if (JSON.stringify(m) !== JSON.stringify([true, [14, 15, 16, 17], [0], []])) fail("tpMatch details " + JSON.stringify(m));
  if (run(`tpMatch({ name: "A", keywords: [], excluded_terms: [] }, "")`).score !== 1 || run(`tpMatch({ name: "A", keywords: [], excluded_terms: [] }, "b")`) !== null) fail("tpMatch empty/none");
}
// Topics sub-view: optional 5th argument = JSON file {ok, hostile, overlimit, not_paired} of /api/topics envelopes (rows already shaped by the server).
async function topicsChecks(fx, appSrc) {
  const fail = (m) => { throw new Error("topics: " + m); };
  const run = (c) => vm.runInContext(c, ctx);
  const get = (id) => els.get(id);
  const rowsOf = () => allOf(get("tplist")).filter((n) => /\btprow\b/.test(n.className || ""));
  const fire = (id, type, ev) => Promise.all((get(id)._ls[type] || []).map((f) => f(ev)));
  const calls = [];
  const reply = { ok: true, status: 200, data: {} };
  ctx.fetch = (url, opts) => {
    calls.push({ url: String(url), body: opts && opts.body ? JSON.parse(opts.body) : null });
    const r = opts && opts.method === "POST" ? reply : { ok: true, status: 200, data: String(url).startsWith("/api/topics") ? ctx.TP_CUR : { state: "error", data: null } };
    return Promise.resolve({ ok: r.ok, status: r.status, json: () => Promise.resolve(r.data) });
  };
  const topicsSrc = appSrc.slice(appSrc.indexOf("// ---------- Topics panel"), appSrc.indexOf("// ---------- end Topics panel"));
  if (topicsSrc.length < 2000) fail("could not find the Topics section in app.js");
  for (const bad of ["innerHTML", "esc(", "insertAdjacentHTML", "outerHTML", "document.write", "eval(", "new Function"]) if (topicsSrc.includes(bad)) fail(`the Topics code uses ${bad}`);
  run("ME = { paired: true }");
  ctx.TP_CUR = fx.ok;
  run('RESEARCH = { state: "error", data: null }');   // the Research tab is shown (the earlier checks left it disabled)
  run('setView("research")');
  run('setRsView("topics")');
  await run("loadTopics()");
  if (get("tpbody").hidden || !get("rsbody").hidden) fail("sub-view did not switch");
  if (!get("rsview-topics").classList.contains("on")) fail("segmented control state");
  ctx.TP_TRAP = true;   // from here on, any innerHTML write while rendering fails the run
  run("renderTopics()");
  if (rowsOf().length !== 4) fail(`expected 3 active + 1 inactive rows (archived hidden), got ${rowsOf().length}`);
  const list = textOf(get("tplist"));
  if (!/General · 2\/200/.test(list) || !/Hobby · 1\/200 · 1 inactive/.test(list) || /General · 2\/200 · /.test(list)) fail("group counts (active only, 'n inactive' in the header): " + list.slice(0, 200));
  if (!/General 2\/200/.test(textOf(get("tpchips"))) || !/Fintrend 0\/200/.test(textOf(get("tpchips")))) fail("agent chips: " + textOf(get("tpchips")));
  if (!/Fintrend agent arrives in M4/.test(list)) fail("fintrend note missing");
  if (get("tpadd").disabled) fail("Add topic disabled although paired and ok");
  if (!allOf(get("tplist")).some((n) => n.tagName === "svg") || !allOf(get("tplist")).some((n) => /\btpw-fg\b/.test(n.className || ""))) fail("weight bar missing");
  run("TP.archived = true; renderTopics()");
  if (rowsOf().length !== 5 || !rowsOf().some((n) => /\btp-archived\b/.test(n.className)) || !/Restore/.test(textOf(get("tplist")))) fail("archived toggle");
  // three states: Active = switch on; Inactive = switch off + badge + hold button; Archived = Restore + badge, no Edit / hold
  const rowOf = (id) => rowsOf().find((r) => r.getAttribute("data-id") === id);
  const part = (row, cls) => allOf(row).find((n) => new RegExp("\\b" + cls + "\\b").test(n.className || ""));
  const rA = rowOf("00000001-0000-4000-8000-000000000001"), rI = rowOf("00000005-0000-4000-8000-000000000005"), rX = rowOf("00000004-0000-4000-8000-000000000004");
  if (!part(rA, "tpswitch").checked || /inactive|archived/.test(textOf(rA)) || !part(rA, "tphold") || !part(rA, "tpedit")) fail("active row");
  if (part(rI, "tpswitch").checked || !/inactive/.test(textOf(rI)) || !/\btp-inactive\b/.test(rI.className) || !part(rI, "tphold") || !part(rI, "tpedit")) fail("inactive row");
  if (part(rX, "tpswitch") || !part(rX, "tprestore") || !/archived/.test(textOf(rX)) || part(rX, "tphold") || part(rX, "tpedit")) fail("archived row");
  const ico = part(rA, "tphold");
  if (!allOf(ico).some((n) => n.tagName === "svg" && /\btpico\b/.test(n.getAttribute("class") || "")) || ico.textContent !== "" || !/Archive topic/.test(ico.getAttribute("aria-label"))) fail("hold button is an svg.tpico icon button with only an aria-label");
  const order = rowsOf().map((r) => r.getAttribute("data-id").slice(0, 8));
  if (order.indexOf("00000005") < order.indexOf("00000003") || order.indexOf("00000004") < order.indexOf("00000002")) fail("ordering: " + order);
  run("TP.archived = false; TP.filter = 'moog'; renderTopics()");
  if (rowsOf().length !== 1) fail("text filter");
  run("TP.filter = 'zzzz'; renderTopics()");
  if (!/No topic matches "zzzz"/.test(textOf(get("tplist")))) fail("empty filter state");
  run("TP.filter = ''; renderTopics()");
  fuzzyChecks(run, fail);
  // fuzzy search through the real render path: ranking, AND narrowing, counts, highlight, hostile text
  const mk = (id, agent, name, kw, ex, act = true) => ({ id, agent_type: agent, name, keywords: kw, excluded_terms: ex || [], weight: 1, is_active: act, is_archived: !act, updated_at: "2026-11-07T00:00:00Z" });
  ctx.TP_CUR = { state: "ok", loaded_at: 1, topics: [
    mk("a", "general", "Philippine politics", ["Rodrigo Duterte", "senate"]),
    mk("b", "hobby", "Final Fantasy Resonance", ["ff14", "dawntrail"], ["leaks"]),
    mk("c", "general", "Local LLM news", ["ollama models", "llama.cpp"]),
    mk("d", "general", "<img src=x onerror=alert(1)>", ["<script>alert(1)</script>"]),
    mk("e", "general", "Old Duterte archive", ["history"], [], false),
  ] };
  await run("loadTopics()");
  const names = () => rowsOf().map((r) => allOf(r).find((n) => /\btpname\b/.test(n.className || "")).children.map((c) => c.textContent).join("") || allOf(r).find((n) => /\btpname\b/.test(n.className || "")).textContent);
  run("TP.filter = 'dutrte'; renderTopics()");
  if (names().join("|") !== "Philippine politics" || !/1 of 4 topics/.test(get("tpcount").textContent)) fail("typo search: " + names() + " / " + get("tpcount").textContent);
  if (!allOf(get("tplist")).some((n) => /\btphit\b/.test(n.className || "")) || !/Matches keyword Rodrigo Duterte/.test(textOf(get("tplist")))) fail("keyword hit not shown");
  run("TP.filter = 'ff reso'; renderTopics()");
  if (names().join("|") !== "Final Fantasy Resonance") fail("initials+prefix: " + names());
  if (!allOf(get("tplist")).some((n) => n.className === "tpmatch")) fail("name highlight missing");
  run("TP.filter = 'ollama mdls'; renderTopics()");
  if (names().join("|") !== "Local LLM news") fail("keyword subsequence: " + names());
  run("TP.filter = 'duterte'; TP.archived = true; renderTopics()");
  if (names().join("|") !== "Philippine politics|Old Duterte archive" || !/2 of 5 topics/.test(get("tpcount").textContent)) fail("archived scope: " + names());
  run("TP.archived = false; TP.agent = 'hobby'; TP.filter = 'duterte'; renderTopics()");
  if (rowsOf().length || !/No topic matches "duterte"/.test(textOf(get("tplist")))) fail("agent tab not respected");
  run("TP.agent = 'all'; TP.filter = 'xyz' + 'q'.repeat(100); renderTopics()");
  if (!/No topic matches "xyzq+…"/.test(textOf(get("tplist"))) || textOf(get("tplist")).length > 200) fail("empty state not capped");
  run("TP.filter = 'onerror'; renderTopics()");
  if (names().join("|") !== "<img src=x onerror=alert(1)>") fail("hostile name must render literally: " + names());
  if (allOf(get("tplist")).some((n) => ["img", "script", "iframe", "a"].includes(n.tagName))) fail("hostile search created an element");
  run("TP.filter = '<script>'; renderTopics()");
  if (rowsOf().length !== 1) fail("hostile keyword search");
  // input is debounced; Escape clears (setTimeout is stubbed to a no-op in this harness, so only the cleared state is checked)
  get("tpfilter").value = "x"; run("TP.filter = 'x'");
  const ev = { key: "Escape", preventDefault() { ev.pd = true; } };
  await fire("tpfilter", "keydown", ev);
  if (get("tpfilter").value !== "" || run("TP.filter") !== "" || !ev.pd || get("tpcount").textContent !== "") fail("Escape did not clear the search");
  run("TP.filter = ''; TP.archived = false; TP.agent = 'all'");
  // ordering is by score, not by list order: the better match comes second in the data and must render first
  ctx.TP_CUR = { state: "ok", loaded_at: 1, topics: [
    mk("x1", "general", "Zebra news", ["duterte"]), mk("x2", "general", "Duterte", ["other"]),
    mk("x3", "general", "Bad keywords", "not-an-array", 7), mk("x4", "general", "Null lists", null, null),
  ] };
  await run("loadTopics()");
  run("TP.filter = 'duterte'; renderTopics()");
  if (names().join("|") !== "Duterte|Zebra news") fail("render order should follow the score: " + names());
  run("TP.filter = ''; renderTopics()");
  if (rowsOf().length !== 4) fail("malformed rows (non-array keywords/excluded_terms) must still render: " + rowsOf().length);
  run("TP.filter = 'bad'; renderTopics()");
  if (names().join("|") !== "Bad keywords") fail("malformed row search: " + names());
  // empty-state echo is cut by code points (no lone surrogate), emoji-only queries show the empty state, hostile text stays text
  run("TP.filter = 'a'.repeat(39) + '😀😀😀'; renderTopics()");
  const echo = textOf(get("tplist"));
  if (/[\ud800-\udbff](?![\udc00-\udfff])/.test(echo) || /(^|[^\ud800-\udbff])[\udc00-\udfff]/.test(echo) || !echo.includes("a".repeat(39) + "😀…")) fail("empty-state echo must cut by code points: " + JSON.stringify(echo));
  run("TP.filter = '😀😀'; renderTopics()");
  if (rowsOf().length || !/No topic matches "😀😀"/.test(textOf(get("tplist")))) fail("emoji-only query behaviour changed");
  run("TP.filter = '\\u0000\\u202e<img src=x onerror=alert(1)>'; renderTopics()");
  if (allOf(get("tplist")).some((n) => ["img", "script", "iframe", "a"].includes(n.tagName))) fail("hostile query created an element");
  run("TP.filter = ''; TP.archived = false; TP.agent = 'all'");
  // hostile topic text: literal, never markup
  ctx.TP_CUR = fx.hostile;
  await run("loadTopics()");
  const all = allOf(get("tplist"));
  if (all.some((n) => ["img", "script", "iframe", "a"].includes(n.tagName))) fail("hostile text created an element");
  if (!all.some((n) => n.textContent === "<img src=x onerror=alert(1)>")) fail("hostile name not shown literally");
  if (!all.some((n) => n.textContent === "<script>alert(1)</script>")) fail("hostile keyword not shown literally");
  if (/Ignore previous|rd_pat_/.test(textOf(get("tplist")))) fail("dropped fields reached the page");
  if (!rowsOf().some((r) => /CLI-only limits/.test(textOf(r)))) fail("over-limit badge missing");
  // CLI topic over the API limits: notice + highlighted fields in the edit dialog
  ctx.TP_CUR = fx.overlimit;
  await run("loadTopics()");
  run("tpDialog(TP.data.topics[0])");
  if (get("tpnote").hidden || !/exceeds what the API accepts/.test(get("tpnote").textContent)) fail("over-limit notice");
  if (!get("tperr-keywords").textContent) fail("over-limit fields not highlighted: " + get("tperr-keywords").textContent);
  // not paired: message, no rows, write controls disabled
  ctx.TP_CUR = fx.not_paired;
  run("ME = { paired: false }");
  await run("loadTopics()");
  if (!/Pair this browser to view and edit topics/.test(textOf(get("tphead")))) fail("not_paired message");
  if (rowsOf().length || !get("tpadd").disabled) fail("not_paired must show no rows and disable Add");
  ctx.TP_CUR = fx.ok;
  await run("loadTopics()");
  const ctl = allOf(get("tplist")).filter((n) => ["input", "button"].includes(n.tagName) && !/tpex/.test(n.className || ""));
  if (!ctl.length || !ctl.every((n) => n.disabled)) fail("write controls must be disabled when the browser is not paired");
  // state banners keep the (stale) list and disable writes
  run("ME = { paired: true }");
  ctx.TP_CUR = { ...fx.ok, state: "unreachable", stale: true, message: "valkyrie-dispatch is not reachable on 127.0.0.1:8080. Is the container running?" };
  await run("loadTopics()");
  if (!/not reachable/.test(textOf(get("tphead"))) || !get("tpadd").disabled || !get("tpbody").classList.contains("stale")) fail("stale unreachable state");
  ctx.TP_CUR = { state: "too_powerful", topics: [], message: "This token has scope admin. The dashboard only accepts a topics-scope token." };
  await run("loadTopics()");
  if (!/scope admin/.test(textOf(get("tphead"))) || rowsOf().length) fail("too_powerful state");
  // toggle: confirmed (the POST happens, then the row is replaced by the server's record)
  ctx.TP_CUR = fx.ok;
  await run("loadTopics()");
  const t0 = JSON.parse(JSON.stringify(fx.ok.topics[0]));
  Object.assign(reply, { ok: true, status: 200, data: { ok: true, topic: { ...t0, is_active: false, updated_at: "2026-11-07T11:00:00.000000Z" } } });
  calls.length = 0;
  await run("tpToggle(TP.data.topics[0], false)");
  const c = calls.find((x) => x.url === "/api/topics/update");
  if (!c || c.body.id !== t0.id || c.body.if_match !== t0.updated_at || JSON.stringify(c.body.fields) !== '{"is_active":false}') fail("toggle request: " + JSON.stringify(calls));
  if (run("TP.data.topics[0].is_active") !== false) fail("row not replaced by the server record");
  if (!/inactive/.test(textOf(rowsOf().find((r) => r.getAttribute("data-id") === t0.id)))) fail("switched-off row should show the inactive badge");
  // toggle error: the row shows a fixed message and keeps the server state
  Object.assign(reply, { ok: false, status: 412, data: { ok: false, code: "changed" } });
  await run("tpToggle(TP.data.topics[1], false)");
  if (!/changed elsewhere/.test(textOf(get("tplist"))) || run("TP.data.topics[1].is_active") !== true) fail("toggle failure handling");
  // add dialog: client-side validation first, then the request
  const form = { submitter: { value: "save" }, preventDefault() {} };
  run("tpDialog(null)");
  await fire("tpform", "submit", form);
  if (get("tperr-name").textContent !== "Required" || !/At least 1 keyword/.test(get("tperr-keywords").textContent)) fail("client validation messages");
  calls.length = 0;
  get("tpagent").value = "hobby"; get("tpname").value = "  New   thing "; get("tpkw").value = "a\n\nA\nb\r\n"; get("tpex").value = ""; get("tpweight").value = "2.5";
  await fire("tpkw", "input", {});
  if (get("tpkwn").textContent !== "2/20") fail("live counter: " + get("tpkwn").textContent);
  const newId = "00000009-0000-4000-8000-000000000009";
  Object.assign(reply, { ok: true, status: 200, data: { ok: true, topic: { ...t0, id: newId, name: "New thing", agent_type: "hobby" } } });
  const closed = get("tpdlg").closed || 0;
  await fire("tpform", "submit", form);
  const cr = calls.find((x) => x.url === "/api/topics/create");
  if (!cr || JSON.stringify(cr.body) !== JSON.stringify({ agent_type: "hobby", name: "New thing", keywords: ["a", "b"], excluded_terms: [], weight: 2.5 })) fail("create request: " + JSON.stringify(cr));
  if ((get("tpdlg").closed || 0) !== closed + 1) fail("dialog did not close after a confirmed save");
  if (!run(`TP.data.topics.some((t) => t.id === '${newId}')`)) fail("created topic not added");
  // duplicate with an archived twin offers Restore; cap names the agent; rate limit and unreachable keep the form
  run("tpDialog(null)");
  get("tpagent").value = "hobby"; get("tpname").value = "Dup"; get("tpkw").value = "x"; get("tpweight").value = "1";
  Object.assign(reply, { ok: false, status: 409, data: { ok: false, code: "duplicate", existing_id: "00000004-0000-4000-8000-000000000004", existing_active: false, existing_archived: true } });
  await fire("tpform", "submit", form);
  if (!/already exists/.test(get("tpformmsg").textContent) || get("tprestoredup").hidden) fail("duplicate handling");
  if (get("tprestoredup").textContent !== "Restore the archived one") fail("archived twin label: " + get("tprestoredup").textContent);
  Object.assign(reply, { ok: false, status: 409, data: { ok: false, code: "duplicate", existing_id: "00000005-0000-4000-8000-000000000005", existing_active: false, existing_archived: false } });
  await fire("tpform", "submit", form);
  if (get("tprestoredup").textContent !== "Switch the existing one on") fail("inactive twin label: " + get("tprestoredup").textContent);
  Object.assign(reply, { ok: false, status: 409, data: { ok: false, code: "cap" } });
  await fire("tpform", "submit", form);
  if (!/Hobby already has 200 active topics/.test(get("tpformmsg").textContent)) fail("cap message: " + get("tpformmsg").textContent);
  Object.assign(reply, { ok: false, status: 429, data: { ok: false, code: "rate_limited" } });
  await fire("tpform", "submit", form);
  if (!/Too many changes/.test(get("tpformmsg").textContent)) fail("rate limit message");
  Object.assign(reply, { ok: false, status: 502, data: { ok: false, code: "unreachable" } });
  await fire("tpform", "submit", form);
  if (!/nothing was saved/.test(get("tpformmsg").textContent) || get("tpname").value !== "Dup") fail("unreachable keeps the form filled");
  Object.assign(reply, { ok: false, status: 400, data: { ok: false, code: "invalid", fields: { name: "forbidden_char", evil: "x" } } });
  await fire("tpform", "submit", form);
  if (!/not allowed/.test(get("tperr-name").textContent)) fail("per-field server error");
  Object.assign(reply, { ok: true, status: 200, data: { ok: true, topic: { ...t0, id: "00000005-0000-4000-8000-000000000005", is_active: true } } });
  calls.length = 0;
  await get("tprestoredup")._ls.click[0]({});
  const dupc = calls.find((x) => x.url === "/api/topics/update");
  if (!dupc || JSON.stringify(dupc.body) !== '{"id":"00000005-0000-4000-8000-000000000005","fields":{"is_active":true}}') fail("duplicate restore still sends is_active:true: " + JSON.stringify(calls));
  ctx.TP_CUR = fx.ok;
  await run("loadTopics()");   // the handler's own refresh is a POST that the mock answers with `reply`
  // edit sends only changed fields; 412 offers Reload
  run("tpDialog(TP.data.topics.find((t) => t.is_active))");
  const cur = run("TP.editing");
  get("tpweight").value = "4";
  Object.assign(reply, { ok: false, status: 412, data: { ok: false, code: "changed" } });
  await fire("tpform", "submit", form);
  if (get("tpreload").hidden || !/changed elsewhere/.test(get("tpformmsg").textContent)) fail("412 handling");
  Object.assign(reply, { ok: true, status: 200, data: { ok: true, topic: { ...cur, weight: 4 } } });
  calls.length = 0;
  await fire("tpform", "submit", form);
  const upd = calls.find((x) => x.url === "/api/topics/update");
  if (!upd || JSON.stringify(upd.body.fields) !== '{"weight":4}' || upd.body.if_match !== cur.updated_at) fail("edit sends only changed fields with if_match: " + JSON.stringify(upd));
  // the dialog has no archive control any more: the hold button is the only way
  run("tpDialog(TP.data.topics.find((t) => t.is_active))");
  if (els.has("tparchive") || els.has("tparchhelp")) fail("dialog must not reference the removed archive button");
  // hold to archive (the sandbox setTimeout is captured so the 1000 ms hold can be fired by hand)
  const timers = [], cleared = [];
  const realSet = ctx.setTimeout, realClear = ctx.clearTimeout;
  ctx.setTimeout = (f, ms) => { timers.push({ f, ms, id: timers.length + 1 }); return timers.length; };
  ctx.clearTimeout = (id) => cleared.push(id);
  const settle = () => new Promise((r) => setImmediate(r));
  const holdTimers = () => timers.filter((x) => x.ms === 1000);
  const fresh = async (id = fx.ok.topics[0].id) => {
    ctx.TP_CUR = fx.ok; await run("loadTopics()");
    run("TP.archived = false; TP.filter = ''; TP.agent = 'all'; renderTopics()");
    timers.length = 0; cleared.length = 0; calls.length = 0;
    const row = rowOf(id), b = part(row, "tphold");
    row.querySelector = (q) => part(row, q.slice(1));
    b.closest = () => row;
    return { row, b };
  };
  let { row, b } = await fresh();
  b._ls.pointerdown[0]({ button: 0 });
  if (!b.classList.contains("holding") || calls.length || holdTimers().length !== 1) fail("pointerdown starts the hold, sends nothing");
  b._ls.pointerup[0]({});
  if (b.classList.contains("holding") || !cleared.includes(holdTimers()[0].id) || calls.length) fail("early release cancels the hold");
  b._ls.click[0]({});
  if (calls.length || !/Hold to archive/.test(textOf(row))) fail("a plain click only shows the hint");
  b._ls.pointerdown[0]({ button: 2 });
  if (holdTimers().length !== 1) fail("only the primary button starts a hold");
  b._ls.pointerdown[0]({ button: 0 });
  const t0id = fx.ok.topics[0].id, t0im = fx.ok.topics[0].updated_at;
  Object.assign(reply, { ok: true, status: 200, data: { ok: true, topic: { ...fx.ok.topics[0], is_active: false, is_archived: true, updated_at: "2026-11-07T12:00:00.000000Z" } } });
  holdTimers()[1].f();
  await settle();
  const arch = calls.filter((x) => x.url === "/api/topics/update");
  if (arch.length !== 1 || arch[0].body.id !== t0id || arch[0].body.if_match !== t0im || JSON.stringify(arch[0].body.fields) !== '{"is_archived":true}') fail("finished hold sends exactly one {is_archived:true}: " + JSON.stringify(calls));
  if (!/Archived "AI chips"\. Turn on Show archived to restore it\./.test(get("tpmsg").textContent) || rowOf(t0id)) fail("archive confirmation / row hidden: " + get("tpmsg").textContent);
  // keyboard: Space and Enter hold, repeat does not start a second timer, keyup cancels, click alone never sends
  ({ row, b } = await fresh("00000002-0000-4000-8000-000000000002"));
  const key = (k, rep) => ({ key: k, repeat: !!rep, preventDefault() { this.prevented = true; } });
  const kd = key(" ");
  b._ls.keydown[0](kd);
  b._ls.keydown[0](key(" ", true));
  if (!kd.prevented || holdTimers().length !== 1 || !b.classList.contains("holding")) fail("Space starts one hold; repeat does not restart it");
  b._ls.keyup[0]({ key: "Enter" });
  if (!b.classList.contains("holding")) fail("keyup of another key must not cancel");
  b._ls.keyup[0]({ key: " " });
  if (b.classList.contains("holding") || calls.length) fail("keyup cancels");
  b._ls.keydown[0](key("Enter"));
  if (holdTimers().length !== 2) fail("Enter starts a hold");
  b._ls.blur[0]({});
  if (b.classList.contains("holding")) fail("blur cancels");
  b._ls.click[0]({});
  if (calls.length) fail("click alone never archives");
  // pointerleave, and a re-render during the hold clears the timer (a stale button must not fire)
  b._ls.pointerdown[0]({ button: 0 });
  b._ls.pointerleave[0]({});
  if (b.classList.contains("holding")) fail("pointerleave cancels");
  b._ls.pointerdown[0]({ button: 0 });
  const live = holdTimers()[holdTimers().length - 1];
  run("renderTopics()");
  if (!cleared.includes(live.id) || run("TP.hold") !== null) fail("renderTopics cancels a running hold");
  live.f();
  await settle();
  if (calls.some((x) => x.url === "/api/topics/update")) fail("a cancelled hold's timer must not archive");
  ctx.setTimeout = realSet; ctx.clearTimeout = realClear;
  // Restore sends is_archived:false and lands as Inactive
  ctx.TP_CUR = fx.ok; await run("loadTopics()");
  run("TP.archived = true; renderTopics()");
  const xr = rowOf("00000004-0000-4000-8000-000000000004");
  const x3im = fx.ok.topics[3].updated_at;
  Object.assign(reply, { ok: true, status: 200, data: { ok: true, topic: { ...fx.ok.topics[3], is_active: false, is_archived: false, updated_at: "2026-11-07T12:00:00.000000Z" } } });
  calls.length = 0;
  await part(xr, "tprestore").onclick();
  const rc = calls.find((x) => x.url === "/api/topics/update");
  if (!rc || JSON.stringify(rc.body.fields) !== '{"is_archived":false}' || rc.body.if_match !== x3im) fail("restore request: " + JSON.stringify(calls));
  const after = rowOf("00000004-0000-4000-8000-000000000004");
  if (!/inactive/.test(textOf(after)) || !/Restored as inactive/.test(textOf(after)) || part(after, "tpswitch").checked) fail("restored row is Inactive with a note");
  // old backend: banner, no hold button, Restore falls back to the switch
  ctx.TP_CUR = { ...fx.ok, states_supported: false, topics: fx.ok.topics.map((t) => ({ ...t, is_archived: !t.is_active })) };
  await run("loadTopics()");
  run("TP.archived = true; renderTopics()");
  if (!/valkyrie-dispatch is older than this dashboard/.test(textOf(get("tphead"))) || allOf(get("tplist")).some((n) => /\btphold\b/.test(n.className || ""))) fail("legacy banner / hold button");
  calls.length = 0;
  Object.assign(reply, { ok: true, status: 200, data: { ok: true, topic: { ...fx.ok.topics[3], is_active: true, is_archived: false } } });
  await run("tpRestore(TP.data.topics.find((t) => t.is_archived))");
  if (!calls.some((x) => x.url === "/api/topics/update" && JSON.stringify(x.body.fields) === '{"is_active":true}')) fail("legacy restore sends is_active:true: " + JSON.stringify(calls));
  ctx.TP_TRAP = false;
  return "topics ok";
}
(async () => {
  try {
    console.log(vm.runInContext(src, ctx, { filename: "app.js" }));
    console.log(await vm.runInContext(extra, ctx, { filename: "extra.js" }));
    console.log(timelineChecks());
    console.log(flagChecks());
    if (process.argv[4]) console.log(await researchChecks(JSON.parse(fs.readFileSync(process.argv[4], "utf8"))));
    if (process.argv[5]) console.log(await topicsChecks(JSON.parse(fs.readFileSync(process.argv[5], "utf8")), fs.readFileSync(process.argv[2], "utf8")));
  } catch (e) { console.log("ERROR:", e.message, (e.stack || "").split("\n").slice(1,5).join(" ; ")); process.exit(1); }
})();
