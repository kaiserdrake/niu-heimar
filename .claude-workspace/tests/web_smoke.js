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
  _ls: {}, addEventListener(t, f) { (document._ls[t] = document._ls[t] || []).push(f); }, body: el("body"), documentElement: el("html"), hidden: false, title: "",
};
const ctx = {
  document, window: null, console, setTimeout: () => 0, clearTimeout() {}, setInterval: () => 0, clearInterval() {},
  requestAnimationFrame: () => 0, cancelAnimationFrame() {}, location: { hash: "", pathname: "/", search: "" },
  history: { replaceState() {} }, localStorage: { getItem: () => null, setItem(k, v) { (globalThis.LS_SET ||= []).push([k, v]); } }, innerWidth: 1400, innerHeight: 900,
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
const RS_IDS = ["rshead", "rsgate", "rsquota", "rscomp", "rsexplore", "rssched", "rslanes", "rsgatelog", "rslast", "rserrors"];
// Gate log: one row per gate check, newest first, linked to the gate dots in the lanes graph by a shared data-gi index.
async function gateLogChecks(fx) {
  const fail = (m) => { throw new Error("research gate log: " + m); };
  const run = (c) => vm.runInContext(c, ctx);
  const html = fs.readFileSync(require("path").join(require("path").dirname(process.argv[2]), "index.html"), "utf8");
  // the logs live in the Activities sub-view: gate log, then runs, then errors, and nothing else in #rsact
  if (!/<div id="rsact"[^>]*>\s*<div id="rssched"[^>]*><\/div>\s*<div id="rslanes"[^>]*><\/div>\s*<div id="rsgatelog"[^>]*><\/div>\s*<div id="rslast"[^>]*><\/div>\s*<div id="rserrors"[^>]*><\/div>\s*<\/div>/.test(html)) fail("#rsact must hold #rssched, #rslanes, #rsgatelog, #rslast, #rserrors in that order");
  const statusHtml = html.slice(html.indexOf('id="rsbody"'), html.indexOf('id="rsact"'));
  if (/rssched|rslanes|rsgatelog|rslast|rserrors/.test(statusHtml)) fail("a log or graph is still in the Status view");
  // Topics toolbar: row 1 = agent chips, search, count; row 2 = filters, list settings and the icon buttons
  const tb = html.slice(html.indexOf('<div class="tpbar">'), html.indexOf('id="tpmsg"'));
  const row1 = tb.slice(0, tb.indexOf('tpbar2')), row2 = tb.slice(tb.indexOf('tpbar2'));
  if (!/id="tpchips"[\s\S]*id="tpfilter"[\s\S]*id="tpcount"/.test(row1) || /tpstate|tpsort|tpadd/.test(row1)) fail("toolbar row 1 must hold only chips, search and count");
  for (const id of ["tpstate", "tprun", "tporigin", "tptrial", "tpsort", "tpdensity", "tprefresh", "tpadd"]) if (!row2.includes(`id="${id}"`)) fail(`#${id} must be in toolbar row 2`);
  for (const id of ["tpdensity", "tprefresh", "tpadd"]) if (!new RegExp(`id="${id}"[^>]*class="[^"]*tpicon[^"]*"[^>]*aria-label="[^"]+"`).test(row2)) fail(`#${id} must be an icon button with an aria-label`);
  if (!/id="rsview-status"[\s\S]*id="rsview-topics"[\s\S]*id="rsview-activity"[^>]*>Activities</.test(html)) fail("sub-view buttons must read Status | Topics | Activities");
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
// ---- Research Status: companion / explore cards, the next-24-hours strip, explore lane and badge (P3). Kept apart from the Topics checks. ----
async function researchStatusChecks(fx, appSrc) {
  const fail = (m) => { throw new Error("research status: " + m); };
  const run = (c) => vm.runInContext(c, ctx);
  const show = async (e) => { ctx.RS_CUR = e; await run("loadResearch()"); };
  const need = (id, re, why) => { if (!re.test(textOf(els.get(id)))) fail(`${why || id}: missing ${re} in "${textOf(els.get(id)).slice(0, 300)}"`); };
  const clone = (x) => JSON.parse(JSON.stringify(x));
  const hasCls = (n, c) => new RegExp(`\\b${c}\\b`).test(n.className || "");
  const statusSrc = appSrc.slice(appSrc.indexOf("// ---------- Research tab"), appSrc.indexOf("// ---------- Topics panel"));
  if (statusSrc.length < 5000) fail("could not find the Status section in app.js");
  for (const bad of ["innerHTML", "esc(", "insertAdjacentHTML", "outerHTML", "document.write", "eval(", "new Function"]) if (statusSrc.includes(bad)) fail(`the Status code uses ${bad}`);
  ctx.TP_TRAP = true;   // any innerHTML write while rendering the Status view throws
  const html = fs.readFileSync(require("path").join(require("path").dirname(process.argv[2]), "index.html"), "utf8");
  if (!/<div class="rsgrid">\s*<div id="rsgate"[^>]*><\/div>\s*<div id="rsquota"[^>]*><\/div>\s*<div id="rscomp"[^>]*><\/div>\s*<div id="rsexplore"[^>]*><\/div>\s*<\/div>/.test(html)
      || !/<div id="rssched"[^>]*><\/div>\s*<div id="rslanes"/.test(html)) fail("index.html: the four cards in .rsgrid and #rssched right before #rslanes");
  await show(fx.m3); run('setView("research")');   // the first load un-hides the tab the earlier checks disabled
  await show(fx.m3);
  const marks = allOf(els.get("rssched")).filter((n) => hasCls(n, "rsmark"));
  if (marks.some((n) => n.getAttribute && (n.getAttribute("tabindex") != null || n.getAttribute("data-tip") != null))) fail("strip markers must not be focusable or carry a hover tip");
  if (marks.length !== 5) fail(`expected 5 markers within 24 h, got ${marks.length}`);
  if (marks.some((n) => hasCls(n, "off"))) fail("no marker in the strip should be off");
  const letters = allOf(els.get("rssched")).filter((n) => hasCls(n, "rsmarktxt")).map((n) => n.textContent).sort().join("");
  if (letters !== "BCDER") fail("marker letters: " + letters);
  need("rssched", /Discovery \+ check-ins: Sun 10:00 \(in /, "discovery line");
  need("rssched", /Delta scout: 18:00 · off/, "disabled job listed below the strip");
  if (/newer valkyrie-dispatch/.test(textOf(els.get("rssched")))) fail("fallback note shown although jobs are present");
  need("rscomp", /Companion \(#hermes\)/); need("rscomp", /posted by the bot/); need("rscomp", /14 \(5 from chat\)/); need("rscomp", /3 proposals · 2 notes/);
  need("rscomp", /Last discovery/); need("rscomp", /Proposals, notes and picks are reviewed in #hermes/);
  need("rsexplore", /6 queries → 48 results → 9 picks → 4 posted/); need("rsexplore", /3 liked, 1 disliked/); need("rsexplore", /succeeded/);
  need("rshead", /companion up/); need("rshead", /explore 4 posted today/);
  for (const id of RS_IDS) if (els.get(id).innerHTML) fail(`${id} was filled with innerHTML`);
  // older server: no schedule.jobs gives the fallback strip and its note; no companion / explore gives fixed messages
  await show(fx.m2);
  if (!allOf(els.get("rssched")).some((n) => hasCls(n, "rsmark"))) fail("fallback strip has no markers");
  need("rssched", /Explore, reflection and discovery times need a newer valkyrie-dispatch/, "fallback note");
  need("rscomp", /Companion not available in this valkyrie-dispatch version/); need("rsexplore", /Explore is off/);
  if (/companion|explore \d/.test(textOf(els.get("rshead")))) fail("header chips shown without the blocks");
  {   // the explore run of m2 is a run-level bar in the explore lane; its run line replaces the agent table
    const lanes = els.get("rslanes"), eb = allOf(lanes).filter((n) => hasCls(n, "rs-explorebar"));
    if (eb.length !== 1 || !hasCls(eb[0], "rs-ok") || !/^explore · succeeded/.test(eb[0].getAttribute("data-tip"))) fail("explore lane bar: " + eb.length);
    if (!allOf(lanes).some((n) => n.tagName === "text" && n.textContent === "explore")) fail("explore lane label");
    const d = clone(fx.m2.data);
    d.last_run = { ...d.last_run, ...d.recent_runs.find((x) => x.kind === "explore"), agents: [], digest: null, error_code: null };
    await show({ ...fx.m2, data: d });
    need("rslast", /Explore run: see the Explore card/);
    if (allOf(els.get("rslast")).some((n) => n.tagName === "table")) fail("explore run drew an agent table");
  }
  {   // badge: computed on the newest research-kind run, so a later explore run cannot hide a failed overnight run
    const d = clone(fx.m2.data), ex = d.recent_runs.find((x) => x.kind === "explore"), comp = d.recent_runs.find((x) => x.kind === "comprehensive");
    d.recent_runs = d.recent_runs.filter((x) => x.status !== "skipped_gate");
    d.gate.pending = null;
    d.last_run = { ...d.last_run, ...ex, kind: "explore", status: "succeeded", mode: "normal", agents: [], digest: null, error_code: null };
    comp.status = "failed";
    await show({ ...fx.m2, data: d });
    if (!/rs-bad/.test(els.get("rsdot").className)) fail("badge must stay red behind a succeeded explore run: " + els.get("rsdot").className);
    comp.status = "succeeded"; comp.mode = "normal"; await show({ ...fx.m2, data: d });
    if (!els.get("rsdot").hidden) fail("badge should be clear: " + els.get("rsdot").className);
    ex.status = "failed"; d.last_run.status = "failed"; await show({ ...fx.m2, data: d });
    if (!/rs-warn/.test(els.get("rsdot").className) || /rs-bad/.test(els.get("rsdot").className)) fail("a failed explore run is amber, never red: " + els.get("rsdot").className);
    need("rserrors", /explore/);
    ex.status = "succeeded"; d.last_run.status = "succeeded"; d.companion = { ...clone(fx.m3.data.companion), bot: "stale" };
    await show({ ...fx.m2, data: d }); if (!/rs-warn/.test(els.get("rsdot").className)) fail("stale bot should be amber");
    d.companion.bot = "up"; d.companion.ollama = "down";
    await show({ ...fx.m2, data: d }); if (!/rs-warn/.test(els.get("rsdot").className)) fail("ollama down should be amber");
  }
  // hostile payloads: unknown values print "?", nothing is written as HTML
  await show(fx.hostile);
  for (const id of RS_IDS) {
    const t = textOf(els.get(id));
    if (/<img|<script|onerror|javascript:|rm -rf|funday/.test(t)) fail(`hostile text reached #${id}: ${t.slice(0, 160)}`);
    if (els.get(id).innerHTML) fail(`${id} used innerHTML`);
  }
  {
    const d = clone(fx.m3.data);
    Object.assign(d.companion, { bot: "__proto__", ollama: "<img>", brief_delivery: "constructor", feedback_7d: null, pending_proposals: "x" });
    Object.assign(d.explore, { last_status: "unknown", last_error: "unknown", posted_today: null });
    d.schedule.jobs = [{ job: "unknown", at: null, weekday: "funday", enabled: "yes", next_at: d.generated_at + 60 }, { job: "rm -rf", at: null, weekday: null, enabled: false, next_at: null }];
    await show({ ...fx.m3, data: d });
    need("rscomp", /\?/); need("rsexplore", /\? posted/); need("rshead", /explore \? posted today/);
    const t = textOf(els.get("rscomp")) + textOf(els.get("rssched")) + textOf(els.get("rsexplore"));
    if (/__proto__|constructor|<img|rm -rf|funday/.test(t)) fail("hostile enum leaked: " + t.slice(0, 300));
    if (!allOf(els.get("rssched")).some((n) => hasCls(n, "rsmarktxt") && n.textContent === "?")) fail("an unknown job should draw a ? marker");
    for (const id of RS_IDS) if (els.get(id).innerHTML) fail(`${id} used innerHTML`);
  }
  ctx.TP_TRAP = false;
  await show(fx.m2);
  return "research status ok";
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
  if (!get("rsact").hidden) fail("Activities shown with Topics");
  if (!get("rsview-topics").classList.contains("on")) fail("segmented control state");
  // Activities: the gate log, runs and errors, with Status and Topics hidden; the choice and the hash persist
  run('history.replaceState = (a, b, hash) => { location.hash = hash; }');
  run('setRsView("activity")');
  if (get("rsact").hidden || !get("rsbody").hidden || !get("tpbody").hidden) fail("Activities sub-view did not switch");
  if (!get("rsview-activity").classList.contains("on") || get("rsview-topics").classList.contains("on")) fail("Activities button state");
  const lastView = (globalThis.LS_SET || []).filter(([k]) => k === "niu.rsview").pop();
  if (!lastView || !/activity/.test(lastView[1])) fail("Activities choice not stored: " + JSON.stringify(lastView));
  if (run("location.hash") !== "#research/activity") fail("Activities hash: " + run("location.hash"));
  if (get("rsrefresh").hidden) fail("Refresh hidden on Activities");
  run('setRsView("bogus")'); if (get("rsbody").hidden || run("RSVIEW") !== "status") fail("unknown sub-view must fall back to Status");
  // with no data, the error text still shows on Status (the run list that used to carry it is in Activities now)
  run("renderResearch()"); if (!textOf(get("rsstate")).trim()) fail("state text missing on Status");
  run('setRsView("topics")');
  ctx.TP_TRAP = true;   // from here on, any innerHTML write while rendering fails the run
  run("renderTopics()");
  if (rowsOf().length !== 4) fail(`expected 3 active + 1 inactive rows (archived hidden), got ${rowsOf().length}`);
  const list = textOf(get("tplist"));
  if (!/General · 2 active/.test(list) || !/Hobby · 1 active · 1 inactive/.test(list) || /General · 2 active · /.test(list) || /in the run/.test(list)) fail("group counts (legacy: no run counts, 'n inactive' in the header): " + list.slice(0, 200));
  if (!/General 2\/200/.test(textOf(get("tpchips"))) || !/Fintrend 0\/200/.test(textOf(get("tpchips")))) fail("agent chips: " + textOf(get("tpchips")));
  if (!/Fintrend agent arrives in M4/.test(list)) fail("fintrend note missing");
  if (get("tpadd").disabled) fail("Add topic disabled although paired and ok");
  if (!allOf(get("tplist")).some((n) => n.tagName === "svg") || !allOf(get("tplist")).some((n) => /\btpw-fg\b/.test(n.className || ""))) fail("weight bar missing");
  run("TP.state = 'all'; renderTopics()");
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
  run("TP.state = 'current'; TP.filter = 'moog'; renderTopics()");
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
  if (names().join("|") !== "Philippine politics" || !/^Showing 1 of 1 · 5 total$/.test(get("tpcount").textContent)) fail("typo search: " + names() + " / " + get("tpcount").textContent);
  if (!allOf(get("tplist")).some((n) => /\btphit\b/.test(n.className || "")) || !/Matches keyword Rodrigo Duterte/.test(textOf(get("tplist")))) fail("keyword hit not shown");
  run("TP.filter = 'ff reso'; renderTopics()");
  if (names().join("|") !== "Final Fantasy Resonance") fail("initials+prefix: " + names());
  if (!allOf(get("tplist")).some((n) => n.className === "tpmatch")) fail("name highlight missing");
  run("TP.filter = 'ollama mdls'; renderTopics()");
  if (names().join("|") !== "Local LLM news") fail("keyword subsequence: " + names());
  run("TP.filter = 'duterte'; TP.state = 'all'; renderTopics()");
  if (names().join("|") !== "Philippine politics|Old Duterte archive" || !/^Showing 2 of 2 · 5 total$/.test(get("tpcount").textContent)) fail("archived scope: " + names());
  run("TP.state = 'current'; TP.agent = 'hobby'; TP.filter = 'duterte'; renderTopics()");
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
  if (get("tpfilter").value !== "" || run("TP.filter") !== "" || !ev.pd || get("tpcount").textContent !== "Showing 4 of 4 · 5 total") fail("Escape did not clear the search");
  run("TP.filter = ''; TP.state = 'current'; TP.agent = 'all'");
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
  run("TP.filter = ''; TP.state = 'current'; TP.agent = 'all'");
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
  const ctl = allOf(get("tplist")).filter((n) => ["input", "button"].includes(n.tagName) && !/tpex|tpgroup|tpmore/.test(n.className || ""));
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
    run("TP.state = 'current'; TP.filter = ''; TP.agent = 'all'; renderTopics()");
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
  if (!/Archived "AI chips"\. Set State to Archived to restore it\./.test(get("tpmsg").textContent) || rowOf(t0id)) fail("archive confirmation / row hidden: " + get("tpmsg").textContent);
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
  run("TP.state = 'all'; renderTopics()");
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
  run("TP.state = 'all'; renderTopics()");
  if (!/valkyrie-dispatch is older than this dashboard/.test(textOf(get("tphead"))) || allOf(get("tplist")).some((n) => /\btphold\b/.test(n.className || ""))) fail("legacy banner / hold button");
  calls.length = 0;
  Object.assign(reply, { ok: true, status: 200, data: { ok: true, topic: { ...fx.ok.topics[3], is_active: true, is_archived: false } } });
  await run("tpRestore(TP.data.topics.find((t) => t.is_archived))");
  if (!calls.some((x) => x.url === "/api/topics/update" && JSON.stringify(x.body.fields) === '{"is_active":true}')) fail("legacy restore sends is_active:true: " + JSON.stringify(calls));
  ctx.TP_TRAP = false;
  return "topics ok";
}
// Topics view at scale: 300 topics with the server's meta (and the same rows without it), through the real render path.
// Table tests of the pure tpFilter/tpSort/tpGroups come first.
function tpTableChecks(run, fail) {
  const mk = (id, agent, name, o = {}) => ({ id, agent_type: agent, name, keywords: o.kw || [], excluded_terms: [], weight: o.w ?? 1, is_active: o.st !== "inactive" && o.st !== "archived",
    is_archived: o.st === "archived", updated_at: o.up || "2026-11-01T00:00:00Z",
    meta: o.nometa ? null : { effective_weight: o.eff ?? o.w ?? 1, run_rank: o.rank ?? null, in_run: o.rank == null ? (o.st ? null : false) : o.rank <= 2, origin: o.origin ?? null, trial_until: o.trial ?? null,
      created_at: o.cr || "2026-10-01T00:00:00Z", weight_updated_at: null, likes_30d: 0, dislikes_30d: 0, notes_active: 0 } });
  ctx.TBL = [
    mk("a1", "general", "Bravo", { rank: 2, w: 2, origin: "chat", up: "2026-11-03T00:00:00Z", cr: "2026-10-05T00:00:00Z" }),
    mk("a2", "general", "alpha", { rank: 1, w: 3, origin: "explore", trial: "2026-12-01T00:00:00Z", up: "2026-11-02T00:00:00Z", cr: "2026-10-04T00:00:00Z" }),
    mk("a3", "general", "Charlie", { rank: 3, w: 1.5, up: "2026-11-05T00:00:00Z", cr: "2026-10-03T00:00:00Z" }),
    mk("a4", "general", "delta", { rank: 4, w: 1, origin: "discovery", trial: "2026-10-01T00:00:00Z", up: "2026-11-04T00:00:00Z", cr: "2026-10-02T00:00:00Z" }),
    mk("i1", "general", "Zulu idle", { st: "inactive", w: 4, up: "2026-11-06T00:00:00Z", cr: "2026-10-09T00:00:00Z" }),
    mk("i2", "general", "echo idle", { st: "inactive", w: 0.5 }),
    mk("x1", "general", "Archived one", { st: "archived", w: 5 }),
    mk("h1", "hobby", "Hobby topic", { rank: 1, w: 1, origin: "chat" }),
  ];
  const T = (o) => Object.assign({ agent: "all", filter: "", state: "current", run: "any", origin: "any", trial: false, sort: "run", sortUser: false }, o);
  const f = (o) => { ctx.TT = T(o); return run("tpFilter(TBL, TT).map((t) => t.id).join(',')"); };
  const s = (o) => { ctx.TT = T(Object.assign({ agent: "general" }, o)); return run("tpSort(tpFilter(TBL, TT), TT).map((t) => t.id).join(',')"); };
  const eq = (got, want, what) => { if (got !== want) fail(`${what}: got ${got}, want ${want}`); };
  eq(f({}), "a1,a2,a3,a4,i1,i2,h1", "state current hides archived");
  eq(f({ state: "active" }), "a1,a2,a3,a4,h1", "state active");
  eq(f({ state: "inactive" }), "i1,i2", "state inactive");
  eq(f({ state: "archived" }), "x1", "state archived");
  eq(f({ state: "all" }), "a1,a2,a3,a4,i1,i2,x1,h1", "state all");
  eq(f({ agent: "hobby" }), "h1", "agent filter");
  eq(f({ run: "in" }), "a1,a2,h1", "run=in is active rows with in_run");
  eq(f({ run: "below" }), "a3,a4", "run=below is active rows past the cut-off, never inactive ones");
  eq(f({ origin: "chat" }), "a1,h1", "origin chat");
  eq(f({ origin: "none" }), "a3,i1,i2", "origin none = added directly");
  eq(f({ trial: true }), "a2,a4", "trial filter");
  eq(f({ run: "below", origin: "discovery", trial: true }), "a4", "filters combine");
  eq(f({ filter: "idle" }), "i1,i2", "text filter");
  eq(f({ filter: "zzzz" }), "", "text filter, no match");
  // sorts: archived always last, inactive after active, ties by name
  eq(s({}), "a2,a1,a3,a4,i2,i1", "sort run: rank order, then inactive by name");
  eq(s({ sort: "weight" }), "a2,a1,a3,a4,i1,i2", "sort weight (base, desc)");
  eq(s({ sort: "name" }), "a2,a1,a3,a4,i2,i1", "sort name (case-insensitive)");
  eq(s({ sort: "updated" }), "a3,a4,a1,a2,i1,i2", "sort updated (newest edit first)");
  eq(s({ sort: "newest" }), "a1,a2,a3,a4,i1,i2", "sort newest (meta.created_at desc)");
  eq(s({ state: "all", sort: "name" }), "a2,a1,a3,a4,i2,i1,x1", "archived rows sort last");
  ctx.TBL2 = ctx.TBL.map((t) => ({ ...t, meta: null }));
  eq(run("tpSort(TBL2.filter((t) => t.agent_type === 'general' && !t.is_archived), " + JSON.stringify(T({})) + ").map((t) => t.id).join(',')"), "a2,a1,a3,a4,i1,i2", "run order falls back to weight without meta");
  eq(run("tpSort(TBL2.filter((t) => t.agent_type === 'general' && !t.is_archived), " + JSON.stringify(T({ sort: "newest" })) + ").map((t) => t.id).join(',')"), "a2,a1,a3,a4,i1,i2", "newest falls back to weight without meta");
  ctx.TT = T({ filter: "idle" }); ctx.MM = new Map();
  eq(run("(() => { const l = tpFilter(TBL, TT, MM); return tpSort(l, TT, MM).map((t) => t.id).join(','); })()"), "i1,i2", "search sorts by match");
  if (run("tpSortKey(TBL, TT)") !== "match") fail("search switches the sort to match");
  ctx.TT = T({ filter: "idle", sortUser: true, sort: "name" });
  if (run("tpSortKey(TBL, TT)") !== "name") fail("a sort picked after typing wins over best match");
  // groups: TP_AGENTS order, empty agents omitted, counts are over the agent's whole list
  ctx.TT = T({ run: "below" });
  const g = JSON.parse(run("JSON.stringify(tpGroups(TBL, TT).map((g) => ({ agent: g.agent, ids: g.rows.map((t) => t.id), counts: g.counts })))"));
  if (g.length !== 1 || g[0].agent !== "general" || g[0].ids.join() !== "a3,a4") fail("tpGroups omits agents with no rows: " + JSON.stringify(g));
  if (JSON.stringify(g[0].counts) !== JSON.stringify({ active: 4, inrun: 2, below: 2, inactive: 2, trial: 2 })) fail("tpGroups counts: " + JSON.stringify(g[0].counts));
  const g2 = JSON.parse(run("JSON.stringify(tpGroups(TBL, " + JSON.stringify(T({ state: "all" })) + ").map((g) => g.agent))"));
  if (g2.join() !== "general,hobby") fail("tpGroups order " + g2);
  // malformed rows never throw
  ctx.TT = T({ origin: "chat", trial: true, filter: "x" });
  run("tpGroups([{ id: 'm', agent_type: 'general', name: 'x', weight: 1, is_active: true, is_archived: false, meta: { origin: 5 } }, { id: 'n', agent_type: 'hobby', name: 'x', weight: 1, is_active: false, is_archived: false }], TT)");
}

async function topicsViewChecks(fx, appSrc) {
  const fail = (m) => { throw new Error("topics view: " + m); };
  const run = (c) => vm.runInContext(c, ctx);
  const get = (id) => els.get(id);
  const clone = (o) => JSON.parse(JSON.stringify(o));
  const fire = (id, type, ev) => Promise.all((get(id)._ls[type] || []).map((f) => f(ev)));
  const cls = (n) => n.className || "";
  const has = (n, c) => new RegExp("\\b" + c + "\\b").test(cls(n));
  const part = (n, c) => allOf(n).find((x) => has(x, c));
  const calls = [];
  const reply = { ok: true, status: 200, data: {} };
  ctx.fetch = (url, opts) => {
    calls.push({ url: String(url), body: opts && opts.body ? JSON.parse(opts.body) : null });
    const r = opts && opts.method === "POST" ? reply : { ok: true, status: 200, data: ctx.TP_CUR };
    return Promise.resolve({ ok: r.ok, status: r.status, json: () => Promise.resolve(clone(r.data)) });
  };
  ctx.TP_TRAP = true;   // the innerHTML trap stays on for everything below
  tpTableChecks(run, fail);
  const reset = () => run("Object.assign(TP, { agent: 'all', filter: '', state: 'current', run: 'any', origin: 'any', trial: false, sort: 'run', sortUser: false, density: 'compact' }); TP.shown.clear(); TP.collapsed.clear(); TP.focusId = null");
  const showAll = () => run("for (const a of TP_AGENTS) TP.shown.set(a, 9999); renderTopics()");
  // walk the list: groups -> { head, rows, cutAt (rows before the separator), cutText, more (button texts) }
  const walk = () => {
    const out = [];
    let g = null;
    for (const n of kidsOf(get("tplist"))) {
      if (has(n, "tpgroup")) { g = { agent: n.getAttribute("data-agent"), head: textOf(n), open: n.getAttribute("aria-expanded"), rows: [], cutAt: -1, cutText: "", more: [] }; out.push(g); }
      else if (!g) continue;
      else if (has(n, "tprow")) g.rows.push(n);
      else if (has(n, "tpcut")) { g.cutAt = g.rows.length; g.cutText = n.textContent; if (n.getAttribute("role") !== "separator") fail("cut-off separator lacks its role"); }
      else if (has(n, "tpmorebar")) g.more = kidsOf(n).map((b) => b.textContent);
    }
    return out;
  };
  const rank = (row) => { const b = part(row, "tprank"); return b ? b.textContent : ""; };
  const idOf = (row) => row.getAttribute("data-id");
  const T = fx.many.topics, byId = new Map(T.map((t) => [t.id, t]));
  const agentRows = (a, pred) => T.filter((t) => t.agent_type === a && pred(t));
  const cur = (t) => !t.is_archived;
  const nRows = () => walk().reduce((n, g) => n + g.rows.length, 0);
  run("ME = { paired: true }");

  // --- 300 topics with meta
  reset();
  ctx.TP_CUR = clone(fx.many);
  await run("loadTopics()");
  if (get("tprun").hidden || get("tporigin").hidden || get("tptrial").hidden) fail("run/origin/trial controls must show when the server sends meta");
  if (/Update valkyrie-dispatch/.test(textOf(get("tphead")))) fail("legacy note shown although meta is supported");
  let groups = walk();
  if (groups.map((g) => g.agent).join() !== "general,hobby,fintrend") fail("groups: " + groups.map((g) => g.agent));
  let total = 0, vis = 0;
  for (const g of groups) {
    const all = agentRows(g.agent, cur), act = agentRows(g.agent, (t) => t.is_active);
    total += all.length; vis += Math.min(50, all.length);
    if (g.rows.length !== Math.min(50, all.length)) fail(`${g.agent}: ${g.rows.length} rows, want ${Math.min(50, all.length)}`);
    if (all.length > 50 && !(g.more.length === 2 && g.more[0] === `Show ${Math.min(50, all.length - 50)} more (${all.length - 50} left)` && g.more[1] === "Show all")) fail(`${g.agent} paging buttons: ${g.more}`);
    if (all.length <= 50 && g.more.length) fail(`${g.agent} must not page ${all.length} rows`);
    if (act.length <= 20) fail(`fixture should have more than 20 active ${g.agent} topics`);
    // separator exactly after rank 20; ranks 1..20 above it, "below cut-off" below it
    if (g.cutAt !== 20) fail(`${g.agent}: separator after ${g.cutAt} rows, want 20`);
    g.rows.slice(0, 20).forEach((r, i) => { if (rank(r) !== `#${i + 1}`) fail(`${g.agent} row ${i}: rank badge ${rank(r)}`); });
    if (!g.rows.slice(20).filter((r) => byId.get(idOf(r)).is_active).every((r) => rank(r) === "below cut-off")) fail(`${g.agent}: active rows after the line must say below cut-off`);
    if (!new RegExp(`the 20 above are researched each night; the ${act.length - 20} below are active but not in the run`).test(g.cutText)) fail("separator text: " + g.cutText);
    if (!new RegExp(`${act.length} active · 20 in the run · ${act.length - 20} below`).test(g.head)) fail("group head: " + g.head);
    // active rows first, in the server's rank order
    const ranks = g.rows.map((r) => byId.get(idOf(r))).filter((t) => t.is_active).map((t) => t.meta.run_rank);
    if (ranks.some((x, i) => i && x < ranks[i - 1])) fail(`${g.agent}: not in run order`);
  }
  if (get("tpcount").textContent !== `Showing ${vis} of ${total} · 300 total`) fail("count: " + get("tpcount").textContent);
  const all0 = () => allOf(get("tplist"));
  if (!all0().some((n) => has(n, "tporigin")) || !all0().some((n) => has(n, "tptrial") && /^(trial, \d+ d left|check-in due)$/.test(n.textContent))) fail("origin / trial badges missing");
  if (!all0().some((n) => has(n, "tpfb") && /^\+\d+ \/ −\d+$/.test(n.textContent)) || !all0().some((n) => has(n, "tpnotes"))) fail("likes / notes cells missing");
  if (!/top 20/.test(textOf(get("tpchips")))) fail("agent chips lack the top-k text: " + textOf(get("tpchips")));
  // paging: Show 50 more, then Show all
  const gen = () => walk().find((g) => g.agent === "general");
  const nGen = agentRows("general", cur).length;
  const moreBar = () => kidsOf(get("tplist")).filter((n) => has(n, "tpmorebar"));
  if (nGen > 100) fail("fixture changed: general should fit in two pages");
  await moreBar()[0].children[0].onclick();
  if (gen().rows.length !== nGen) fail("Show 50 more: " + gen().rows.length);
  if (gen().more.length) fail("buttons should disappear once the group is complete");
  reset();
  run("renderTopics()");
  await moreBar()[0].children[1].onclick();
  if (gen().rows.length !== nGen) fail("Show all renders the whole group: " + gen().rows.length);
  // run filter
  run("TP.run = 'below'"); showAll();
  groups = walk();
  for (const g of groups) {
    const want = agentRows(g.agent, (t) => t.is_active && t.meta.run_rank > 20);
    if (g.rows.length !== want.length || !g.rows.every((r) => rank(r) === "below cut-off" && byId.get(idOf(r)).meta.run_rank > 20)) fail(`run=below ${g.agent}: ${g.rows.length}/${want.length}`);
    if (g.cutAt !== -1) fail("no separator when only the rows below the cut-off show");
    if (!/outside the run/.test(g.head)) fail("run=below header: " + g.head);
  }
  run("TP.run = 'in'"); showAll();
  for (const g of walk()) if (g.rows.length !== 20 || !g.rows.every((r) => /^#\d+$/.test(rank(r)))) fail(`run=in ${g.agent}: ${g.rows.length}`);
  run("TP.run = 'any'");
  // origin and trial
  for (const o of ["chat", "discovery", "explore"]) {
    run(`TP.origin = '${o}'`); showAll();
    if (nRows() !== T.filter((t) => cur(t) && t.meta.origin === o).length || !walk().every((g) => g.rows.every((r) => byId.get(idOf(r)).meta.origin === o && part(r, "tporigin").textContent === o))) fail("origin " + o);
  }
  run("TP.origin = 'none'"); showAll();
  if (nRows() !== T.filter((t) => cur(t) && !t.meta.origin).length || all0().some((n) => has(n, "tporigin"))) fail("origin none (added directly)");
  run("TP.origin = 'any'; TP.trial = true"); showAll();
  const nTrial = T.filter((t) => cur(t) && t.meta.trial_until).length;
  if (!nTrial || nRows() !== nTrial || !walk().every((g) => g.rows.every((r) => part(r, "tptrial")))) fail("trial filter");
  if (get("tptrial").textContent !== `On trial (${nTrial})` || get("tptrial").getAttribute("aria-pressed") !== "true") fail("trial button: " + get("tptrial").textContent);
  run("TP.trial = false");
  // state filter
  for (const [st, pred] of [["active", (t) => t.is_active], ["inactive", (t) => !t.is_active && !t.is_archived], ["archived", (t) => t.is_archived], ["all", () => true]]) {
    run(`TP.state = '${st}'`); showAll();
    if (nRows() !== T.filter(pred).length) fail("state " + st + " row count " + nRows());
  }
  run("TP.state = 'current'");
  // sorts through the renderer: the first group's visible order
  const first = () => walk()[0].rows.map((r) => byId.get(idOf(r))).filter((t) => t.is_active);
  const sorted = (xs, cmp) => xs.every((x, i) => !i || cmp(xs[i - 1], x) <= 0);
  run("TP.sort = 'weight'"); showAll();
  if (!sorted(first(), (a, b) => b.weight - a.weight)) fail("sort weight order");
  run("TP.sort = 'name'"); showAll();
  if (!sorted(first(), (a, b) => a.name.localeCompare(b.name, undefined, { sensitivity: "base" }))) fail("sort name order");
  run("TP.sort = 'updated'"); showAll();
  if (!sorted(first(), (a, b) => (a.updated_at < b.updated_at ? 1 : a.updated_at > b.updated_at ? -1 : 0))) fail("sort updated order");
  run("TP.sort = 'newest'"); showAll();
  if (!sorted(first(), (a, b) => (a.meta.created_at < b.meta.created_at ? 1 : a.meta.created_at > b.meta.created_at ? -1 : 0))) fail("sort newest order");
  if (walk().some((g) => g.cutAt !== -1)) fail("the cut-off line only belongs to run order");
  run("TP.sort = 'run'");
  // search over 300 topics: typo cases, best-match sort, then a picked sort wins
  reset(); run("TP.state = 'all'");
  const found = (q) => { run(`TP.filter = ${JSON.stringify(q)}; TP.shown.clear(); renderTopics()`); showAll(); return new Set(walk().flatMap((g) => g.rows.map(idOf))); };
  const t0 = Date.now();
  let got = found("alpah");   // transposition of "alpha"
  const want1 = T.filter((t) => /\balpha\b/i.test(t.name));
  if (!want1.length || !want1.every((t) => got.has(t.id))) fail("typo 'alpah' misses " + want1.filter((t) => !got.has(t.id)).length + " of " + want1.length);
  got = found("strase");   // 1 edit from "strasse" / "straße"
  const want2 = T.filter((t) => /^stra(ss|ß)e/i.test(t.name));
  if (!want2.length || !want2.every((t) => got.has(t.id))) fail("typo 'strase' misses " + want2.filter((t) => !got.has(t.id)).map((t) => t.name));
  got = found("gama");
  const want3 = T.filter((t) => /\bgamma\b/i.test(t.name));
  if (!want3.length || !want3.every((t) => got.has(t.id))) fail("typo 'gama'");
  if (run("tpSortKey(TP.data.topics, tpEff(true))") !== "match" || get("tpsort").value !== "match" || get("tpsort-match").hidden) fail("a search shows Best match in the sort select");
  if (Date.now() - t0 > 4000) fail("search over 300 topics too slow: " + (Date.now() - t0) + "ms");
  get("tpsort").value = "name"; await fire("tpsort", "change", {});
  if (run("TP.sortUser") !== true || run("tpSortKey(TP.data.topics, tpEff(true))") !== "name") fail("picking a sort after typing wins");
  reset(); run("renderTopics()");

  // --- keyboard
  const rows = () => walk().flatMap((g) => g.rows);
  let focused = null;
  const track = () => { for (const r of rows()) r.focus = () => { focused = r; }; get("tpfilter").focus = () => { focused = get("tpfilter"); }; };
  track();
  if (rows()[0].getAttribute("tabindex") !== "0" || rows()[1].getAttribute("tabindex") !== "-1") fail("roving tabindex: first row 0, the others -1");
  const key = (k, target) => ({ key: k, target, preventDefault() { this.pd = true; } });
  get("tpdlg").open = false; ctx.document.activeElement = null;
  let ev = key("/");
  ctx.document._ls.keydown.forEach((f) => f(ev));
  if (focused !== get("tpfilter") || !ev.pd) fail("/ focuses the search");
  focused = null; ctx.document.activeElement = { tagName: "INPUT" };
  ctx.document._ls.keydown.forEach((f) => f(key("/")));
  if (focused) fail("/ must not steal the focus from a field");
  ctx.document.activeElement = null; get("tpdlg").open = true;
  ctx.document._ls.keydown.forEach((f) => f(key("/")));
  if (focused) fail("/ must not fire while the dialog is open");
  get("tpdlg").open = false;
  ev = key("ArrowDown", get("tpfilter"));
  await fire("tpfilter", "keydown", ev);
  if (focused !== rows()[0] || !ev.pd) fail("ArrowDown from the search enters the first row");
  const rk = (i, k, target) => { const e = key(k, target === undefined ? rows()[i] : target); rows()[i]._ls.keydown.forEach((f) => f(e)); return e; };
  rk(0, "ArrowDown");
  if (focused !== rows()[1] || rows()[1].getAttribute("tabindex") !== "0" || rows()[0].getAttribute("tabindex") !== "-1") fail("ArrowDown moves to the next row and the roving tabindex follows");
  rk(1, "ArrowUp");
  if (focused !== rows()[0]) fail("ArrowUp");
  focused = null; rk(0, "ArrowUp");
  if (focused !== rows()[0]) fail("ArrowUp at the top stays");
  rk(0, "End");
  if (focused !== rows()[rows().length - 1]) fail("End");
  rk(rows().length - 1, "Home");
  if (focused !== rows()[0]) fail("Home");
  rk(0, "Escape");
  if (focused !== get("tpfilter")) fail("Esc in the list goes back to the search");
  // arrows skip separators and group headers (they are not rows); Enter opens the dialog
  get("tpdlg").open = false;
  const e1 = rk(2, "Enter", part(rows()[2], "tpedit"));
  if (get("tpdlg").open || e1.pd) fail("Enter on a child control must not open the dialog");
  rk(2, "Enter");
  if (!get("tpdlg").open || run("TP.editing && TP.editing.id") !== idOf(rows()[2])) fail("Enter opens the edit dialog for the row");
  get("tpdlg").close();
  run("ME = { paired: false }"); run("renderTopics()"); track();
  rk(2, "Enter");
  if (get("tpdlg").open) fail("Enter must not open the dialog when writes are disabled");
  run("ME = { paired: true }"); run("renderTopics()"); track();

  // --- toolbar handlers persist the view (not the search) and reset paging
  run("TP.shown.set('general', 100)");
  globalThis.LS_SET = [];
  get("tpstate").value = "archived"; await fire("tpstate", "change", {});
  if (run("TP.state") !== "archived" || run("TP.shown.size") !== 0) fail("state select");
  get("tprun").value = "below"; await fire("tprun", "change", {});
  get("tporigin").value = "chat"; await fire("tporigin", "change", {});
  await fire("tptrial", "click", {});
  get("tpsort").value = "weight"; await fire("tpsort", "change", {});
  await fire("tpdensity", "click", {});
  const stored = () => globalThis.LS_SET.filter(([k]) => k === "niu.tp.view").pop()[1];
  if (JSON.stringify(JSON.parse(stored())) !== JSON.stringify({ state: "archived", run: "below", origin: "chat", trial: true, sort: "weight", density: "comfortable", collapsed: [] })) fail("persisted view: " + stored());
  run("TP.filter = 'secret search'; tpPersist()");
  if (stored().includes("secret")) fail("search text leaked into the stored view");
  reset(); run("renderTopics()");
  // group collapse
  await kidsOf(get("tplist")).find((n) => has(n, "tpgroup")).onclick();
  const g0 = walk()[0];
  if (g0.rows.length || g0.open !== "false" || !/General · \d+ active/.test(g0.head)) fail("collapse hides the rows but keeps the count text: " + g0.head);
  if (JSON.parse(stored()).collapsed.join() !== "general") fail("collapsed groups persist");
  await kidsOf(get("tplist")).find((n) => has(n, "tpgroup")).onclick();
  if (!walk()[0].rows.length || walk()[0].open !== "true") fail("expand");

  // --- writes that move ranks re-read the list; plain edits do not
  const moveTopic = clone(T.find((t) => t.is_active));
  calls.length = 0;
  Object.assign(reply, { ok: true, status: 200, data: { ok: true, topic: { ...moveTopic, is_active: false, updated_at: "2026-11-09T00:00:00.000000Z" } } });
  await run(`tpToggle(TP.data.topics.find((t) => t.id === '${moveTopic.id}'), false)`);
  if (calls.map((c) => c.url).join() !== "/api/topics/update,/api/topics") fail("a switch re-reads the list: " + calls.map((c) => c.url));
  ctx.TP_CUR = clone(fx.many); await run("loadTopics()");
  const open = () => run(`tpDialog(TP.data.topics.find((t) => t.id === '${moveTopic.id}'))`);
  const save = () => fire("tpform", "submit", { submitter: { value: "save" }, preventDefault() {} });
  calls.length = 0; open();
  get("tpname").value = "Renamed only";
  Object.assign(reply, { ok: true, status: 200, data: { ok: true, topic: { ...moveTopic, name: "Renamed only" } } });
  await save();
  if (calls.map((c) => c.url).join() !== "/api/topics/update") fail("a name-only edit does not re-read: " + calls.map((c) => c.url));
  ctx.TP_CUR = clone(fx.many); await run("loadTopics()");
  calls.length = 0; open();
  get("tpweight").value = "3.5";
  Object.assign(reply, { ok: true, status: 200, data: { ok: true, topic: { ...moveTopic, weight: 3.5 } } });
  await save();
  if (calls.map((c) => c.url).join() !== "/api/topics/update,/api/topics") fail("a weight edit re-reads the list: " + calls.map((c) => c.url));
  calls.length = 0;
  run("tpDialog(null)");
  get("tpagent").value = "hobby"; get("tpname").value = "Brand new"; get("tpkw").value = "x"; get("tpweight").value = "1";
  Object.assign(reply, { ok: true, status: 200, data: { ok: true, topic: { ...moveTopic, id: "0000ffff-0000-4000-8000-00000000ffff", name: "Brand new" } } });
  await save();
  if (calls.map((c) => c.url).join() !== "/api/topics/create,/api/topics") fail("a create re-reads the list: " + calls.map((c) => c.url));
  if (!/changed elsewhere \(CLI, Discord or feedback\)/.test(run("TP_MSG.changed")) || /duenext/i.test(appSrc)) fail("TP_MSG.changed text");

  // --- the same 300 rows from a valkyrie-dispatch without topic_meta
  reset();
  ctx.TP_CUR = clone(fx.many_legacy);
  await run("loadTopics()");
  if (!get("tprun").hidden || !get("tporigin").hidden || !get("tptrial").hidden) fail("legacy: run, origin and trial controls must be hidden");
  if (!/Update valkyrie-dispatch to see run order, the top-20 cut-off and where topics came from\./.test(textOf(get("tphead")))) fail("legacy note: " + textOf(get("tphead")));
  if (!get("tpsort-run").hidden || !get("tpsort-newest").hidden || get("tpsort").value !== "weight") fail("legacy: run order falls back to base weight in the select");
  run("TP.run = 'below'; TP.origin = 'chat'; TP.trial = true; renderTopics()");   // stale persisted choices must not hide rows
  groups = walk();
  if (!groups.length || groups.some((g) => g.cutAt !== -1 || /in the run|outside the run/.test(g.head) || g.rows.some((r) => rank(r) || part(r, "tporigin") || part(r, "tptrial")))) fail("legacy: no cut-off, rank, origin or trial");
  const byIdL = new Map(fx.many_legacy.topics.map((t) => [t.id, t]));
  const lw = groups[0].rows.map((r) => byIdL.get(idOf(r))).filter((t) => t.is_active).map((t) => t.weight);
  if (lw.some((w, i) => i && w > lw[i - 1])) fail("legacy: weight order " + lw.join());
  if (rows().length > 150 || groups[0].more.length !== 2) fail("legacy: paging still applies");
  reset(); run("renderTopics()");
  ctx.TP_TRAP = false;
  return "topics view ok";
}
// Weekly usage panel: draws the fixture history (gaps, finished weeks) with no innerHTML, themed tooltips and a text equivalent.
async function usageChecks(fx) {
  const fail = (m) => { throw new Error("usage: " + m); };
  const run = (c) => vm.runInContext(c, ctx);
  const get = (id) => els.get(id);
  const cls = (n) => n.className || "";
  const find = (re, tag) => allOf(get("usagegraph")).filter((n) => (!tag || n.tagName === tag) && re.test(cls(n)));
  ctx.TP_TRAP = true;   // any innerHTML write while rendering throws
  try {
    ctx.USAGE_FX = fx;
    run("USAGE.h5 = false; USAGE.weeks = 5; renderUsage(USAGE_FX)");
    if (find(/\bul-week\b/, "path").length < 2) fail("weekly line not drawn in segments");
    if (find(/\bul-opus\b/, "path").length < 1) fail("opus line missing though reported");
    if (find(/\bul-sonnet\b/).length) fail("sonnet line drawn though not reported");
    if (find(/\bul-5h\b/).length) fail("5-hour line drawn while the toggle is off");
    if (find(/\bgapband\b/, "rect").length < 1) fail("no gap band");
    if (find(/\breset\b/, "line").length !== 4) fail("reset markers: " + find(/\breset\b/, "line").length);
    if (find(/\bpace\b/, "line").length !== 1) fail("pace line missing");
    const text = textOf(get("usagegraph"));
    if (!/ended 83%/.test(text)) fail("'ended 83%' label missing");
    if (!/ended ≥67%/.test(text)) fail("'ended ≥67%' label for the partial week missing");
    if (!/pace ≈ \d+%/.test(text) || !new RegExp(`now ${Math.round(fx.current.used)}%`).test(text)) fail("pace / now label: " + text.slice(-120));
    const wk = find(/\bwkend\b/, "circle");
    if (wk.length !== 3 || !wk.some((c) => /\bwarn\b/.test(cls(c))) || !wk.some((c) => /\bok\b/.test(cls(c)))) fail("week-end markers carry a level class: " + wk.map(cls));
    if (!/[●▲◆] ended/.test(text)) fail("level is also a glyph, not only colour");
    const hits = find(/\bhit\b/, "rect");
    if (hits.length < 20 || hits.length > 240 || hits.some((r) => !(r.getAttribute("data-tip") || "").includes("\n"))) fail("tooltip rects: " + hits.length);
    if (!hits.some((r) => new RegExp(`Weekly ${Math.round(fx.current.used)}%`).test(r.getAttribute("data-tip")))) fail("current value missing from tooltips");
    if (!/No samples/.test(find(/\bgapband\b/, "rect")[0].getAttribute("data-tip"))) fail("gap tooltip");
    if (allOf(get("usagegraph")).some((n) => n.getAttribute && n.getAttribute("title") !== null)) fail("native title attribute (not themed)");
    const sum = textOf(get("usagesum")).trim();
    if (!sum.startsWith(`Weekly: ${Math.round(fx.current.used)}% now, resets `) || !/on pace for about \d+%/.test(sum) || !sum.endsWith("Last weeks ended at 83%, ≥67%, 72%.")) fail("summary line: " + sum);
    const svgEl = find(/\busagesvg\b/, "svg")[0];
    if (!svgEl || svgEl.getAttribute("role") !== "img" || svgEl.getAttribute("aria-label") !== sum) fail("svg role/aria-label must equal the summary line");
    if (get("usagesum").getAttribute("aria-live") !== null && get("usagesum").getAttribute("aria-live") !== "polite") fail("aria-live");
    if (/collecting since/.test(get("usageupd").textContent) || !/updated \d\d:\d\d · .*usage API/.test(get("usageupd").textContent)) fail("update line: " + get("usageupd").textContent);
    // 5-hour toggle, range
    run("USAGE.h5 = true; renderUsage(USAGE_FX)");
    if (find(/\bul-5h\b/, "path").length < 1) fail("5-hour line missing after the toggle");
    run("USAGE.h5 = false; USAGE.weeks = 2; renderUsage(USAGE_FX)");
    if (find(/\bul-week\b/, "path").length < 1) fail("2-week view draws nothing");
    run("USAGE.weeks = 5");
    // fit to data, label placement, drag-to-zoom
    const W = 900, ls = () => get("usagegraph")._ls;
    const fire = (type, ev) => { for (const f of ls()[type] || []) f(ev); };
    const render = (js) => { run(js); run("USAGE.view").root.getBoundingClientRect = () => ({ left: 0, top: 0, width: 900, height: 230 }); };
    const evt = (x, o = {}) => ({ pointerId: 1, isPrimary: true, button: 0, pointerType: "mouse", clientX: x, clientY: 100, target: find(/\busagesvg\b/, "svg")[0], preventDefault() {}, ...o });
    const inv = (x) => { const v = run("USAGE.view"); return v.t0 + (x - v.L) / (v.W - v.L - v.R) * (v.t1 - v.t0); };
    const drag = (x0, x1, o = {}) => { fire("pointerdown", evt(x0, o)); fire("pointermove", evt((x0 + x1) / 2, o)); fire("pointermove", evt(x1, o)); fire("pointerup", evt(x1, o)); };
    render("USAGE.zoom = null; USAGE.drag = null; USAGE.weeks = 5; renderUsage(USAGE_FX)");
    let v = run("USAGE.view");
    if (!(v.t0 > fx.current.resets_at - 35 * 86400) || !(v.t0 < fx.collecting_since)) fail("5w view does not fit the data: " + v.t0);
    render("USAGE.weeks = 2; renderUsage(USAGE_FX)");
    if (run("USAGE.view").t0 !== fx.current.resets_at - 14 * 86400) fail("2w view should be the full range");
    render("USAGE.weeks = 5; renderUsage(USAGE_FX)");
    if (!get("usagezoomreset").hidden) fail("Reset zoom shown while not zoomed");
    const sizeOf = (c) => (/resetlbl|pacelbl|guidelbl/.test(c) ? 10 : 11);
    const labelCheck = (name) => {
      const boxes = find(/resetlbl|pacelbl|guidelbl|nowlbl|wklbl/, "text").map((n) => ({ n, b: run("usageTextBox")(+n.getAttribute("x"), +n.getAttribute("y"), n.getAttribute("text-anchor") || "start", n.textContent, sizeOf(cls(n)), /nowlbl|wklbl/.test(cls(n))) }));
      for (let i = 0; i < boxes.length; i++) {
        const a = boxes[i].b;
        if (a.x0 < 0 || a.x1 > W) fail(`${name}: label outside the graph: ${boxes[i].n.textContent}`);
        for (let j = i + 1; j < boxes.length; j++) { const b = boxes[j].b; if (a.x0 < b.x1 && a.x1 > b.x0 && a.y0 < b.y1 && a.y1 > b.y0) fail(`${name}: labels overlap: "${boxes[i].n.textContent}" and "${boxes[j].n.textContent}"`); }
      }
      const fl = boxes.find((x) => /guidelbl/.test(cls(x.n)));
      if (fl && (fl.n.getAttribute("text-anchor") !== "start" || +fl.n.getAttribute("x") >= 60)) fail(name + ": floor label should sit at the left end");
      const rl = boxes.find((x) => /resetlbl/.test(cls(x.n)));
      if (!rl || rl.n.getAttribute("text-anchor") !== "end") fail(name + ": the current reset label should sit left of its line");
      const t = textOf(get("usagegraph"));
      if (!/ended 83%/.test(t) || !/ended ≥67%/.test(t)) fail(name + ": ended labels missing");
      return t;
    };
    labelCheck("default");
    ctx.USAGE_CROWD = { ...fx, current: { ...fx.current, used: 55 } };
    render("renderUsage(USAGE_CROWD)");
    if (!/pace ≈ 96%/.test(labelCheck("crowded"))) fail("crowded case: pace ≈ 96% missing (is the fixture pace still about 96?)");
    render("renderUsage(USAGE_FX)");
    // a click or a tiny drag never zooms
    fire("pointerdown", evt(300)); fire("pointermove", evt(303)); fire("pointerup", evt(303));
    if (run("USAGE.zoom") !== null || run("USAGE.drag") !== null) fail("a click must not zoom");
    // ignored input
    fire("pointerdown", evt(300, { button: 2 }));
    if (run("USAGE.drag") !== null) fail("right button started a drag");
    fire("pointerdown", evt(10));
    if (run("USAGE.drag") !== null) fail("pointerdown left of the plot started a drag");
    fire("pointerdown", evt(300, { isPrimary: false }));
    if (run("USAGE.drag") !== null) fail("a non-primary pointer started a drag");
    fire("pointerdown", evt(300)); fire("pointermove", evt(400)); fire("pointercancel", evt(400));
    if (run("USAGE.drag") !== null || run("USAGE.zoom") !== null || get("usagegraph").classList.contains("usagedrag")) fail("pointercancel must cancel the drag");
    fire("pointerdown", evt(300)); fire("pointermove", evt(400)); fire("lostpointercapture", evt(400)); fire("pointerup", evt(400));
    if (run("USAGE.zoom") !== null) fail("lostpointercapture must cancel the drag");
    // drag zooms
    const ta = inv(300), tb = inv(500);
    fire("pointerdown", evt(300)); run("TIP.hidden = false"); fire("pointermove", evt(400));
    if (run("TIP.hidden") !== true || !get("usagegraph").classList.contains("usagedrag") || !find(/\busagesel\b/, "rect").length) fail("drag state: tooltip hidden, cursor class, selection rect");
    fire("pointermove", evt(500)); fire("pointerup", evt(500));
    let z = run("USAGE.zoom");
    if (!z || Math.abs(z.t0 - ta) > 60 || Math.abs(z.t1 - tb) > 60) fail("zoom range: " + JSON.stringify(z) + " vs " + ta + ".." + tb);
    if (get("usagezoomreset").hidden !== false) fail("Reset zoom not shown while zoomed");
    if (run("USAGE.drag") !== null) fail("drag not cleared");
    let nh = find(/\bhit\b/, "rect").length;
    if (nh < 20 || nh > 240) fail("hit rects when zoomed: " + nh);
    if (find(/\busagesvg\b/, "svg")[0].getAttribute("aria-label") !== run("usageSummary(USAGE_FX)")) fail("aria-label changed when zoomed");
    // survives a refresh, the 5-hour toggle and redraw
    render("renderUsage(USAGE_FX)");
    run("USAGE.h5 = true; redrawUsage(); USAGE.h5 = false");
    if (JSON.stringify(run("USAGE.zoom")) !== JSON.stringify(z) || run("USAGE.view").t0 !== z.t0) fail("zoom lost on refresh/redraw");
    // double-click resets, then the button does
    fire("dblclick", evt(300));
    if (run("USAGE.zoom") !== null || get("usagezoomreset").hidden !== true) fail("double-click did not reset the zoom");
    render("renderUsage(USAGE_FX)");
    drag(300, 500, { pointerType: "touch" });
    if (!run("USAGE.zoom")) fail("a touch drag should zoom like the mouse");
    get("usagezoomreset").onclick();
    if (run("USAGE.zoom") !== null || get("usagezoomreset").hidden !== true) fail("Reset zoom button");
    // a selection under 8 px is ignored; a tiny time span is widened to the 2 hour minimum
    render("renderUsage(USAGE_FX)");
    drag(300, 306);
    if (run("USAGE.zoom") !== null) fail("a 6 px selection must not zoom");
    render(`USAGE.zoom = { t0: ${fx.now} - 10800, t1: ${fx.now} }; renderUsage(USAGE_FX)`);
    drag(300, 310);
    z = run("USAGE.zoom");
    if (!z || Math.abs(z.t1 - z.t0 - 7200) > 1) fail("minimum span: " + JSON.stringify(z));
    // zoom inside a zoom narrows further
    render("USAGE.zoom = { t0: USAGE.view.dmin, t1: USAGE.view.dmax }; renderUsage(USAGE_FX)");
    drag(100, 600);
    z = run("USAGE.zoom");
    if (!z || z.t1 - z.t0 >= run("USAGE.view").dmax - run("USAGE.view").dmin) fail("nested zoom: " + JSON.stringify(z));
    // hour ticks and the now label at a short span
    render(`USAGE.zoom = { t0: ${fx.now} - 86400, t1: ${fx.now} }; renderUsage(USAGE_FX)`);
    if (!find(/^lbl$/, "text").some((n) => /^\d{2}:\d{2}$/.test(n.textContent))) fail("no hour ticks when zoomed to a day");
    if (!new RegExp(`now ${Math.round(fx.current.used)}%`).test(textOf(get("usagegraph")))) fail("now label missing when zoomed to now");
    // a range change clears the zoom
    run("setUsageWeeks(2)");
    if (run("USAGE.zoom") !== null) fail("setUsageWeeks must clear the zoom");
    run("setUsageWeeks(5)");
    // a zoom outside the data is dropped
    render("USAGE.zoom = { t0: 1, t1: 2 }; renderUsage(USAGE_FX)");
    if (run("USAGE.zoom") !== null) fail("out-of-domain zoom kept");
    render("renderUsage(USAGE_FX)");
    // stale note
    run("renderUsage({ ...USAGE_FX, current: { ...USAGE_FX.current, stale: true } })");
    if (!/No new readings since .*usage API is off or unreachable/.test(textOf(get("usagegraph")))) fail("stale note");
    run("renderUsage({ ...USAGE_FX, current: { ...USAGE_FX.current, stale: true, api_error: 'expired' } })");
    if (!/No new readings since .*login token has expired.*claude -p hi/.test(textOf(get("usagegraph")))) fail("expired-token note");
    run("renderUsage({ ...USAGE_FX, current: { ...USAGE_FX.current, stale: true, api_error: 'renew_failed' } })");
    if (!/automatic renewal \(claude auth status\) didn't renew it/.test(textOf(get("usagegraph")))) fail("renew-failed note");
    run("renderUsage({ ...USAGE_FX, current: { ...USAGE_FX.current, stale: true, api_error: 'off' } })");
    if (!/usage API is off \(NIU_CLAUDE_USAGE_API=0\)/.test(textOf(get("usagegraph")))) fail("api-off note");
    // fixed state messages
    const msgs = { loading: /^Loading…$/, empty: /Collecting: the first sample appears within 5 minutes/, disabled: /Usage history is off \(NIU_USAGE_COLLECT=0\)/ };
    for (const [state, re] of Object.entries(msgs)) {
      run(`renderUsage({ state: "${state}" })`);
      if (!re.test(textOf(get("usagegraph")).trim()) || textOf(get("usagesum")).trim() !== "") fail(`${state} message: ` + textOf(get("usagegraph")));
    }
    for (const bad of ["null", '{ state: "error", message: "boom <b>x</b>" }', '{ state: "bogus" }', '"text"']) {
      run(`renderUsage(${bad})`);
      if (textOf(get("usagegraph")).trim() !== "Can't read usage history from the dashboard server.") fail("error message for " + bad + ": " + textOf(get("usagegraph")));
    }
    // hostile payload: wrong types never throw and no server text is shown
    ctx.USAGE_HOSTILE = { ...fx, tz: "<img onerror=x>", series: { seven_day: { label: "<b>x</b>", points: [[1, "a"], null, "x", [fx.now - 100, 5], [fx.now - 50, 6]] }, five_hour: "no", seven_day_opus: { points: 7 } },
      weeks: [{ resets_at: "x", ended_pct: "<b>", last_sample: fx.now - 10 }, null, { resets_at: fx.now - 5, ended_pct: 50, last_sample: fx.now - 9, partial: "yes" }],
      resets: ["a", null, fx.now - 3600], current: { used: "<i>", resets_at: "z", updated: "q", source: "<s>", stale: false }, recorder: { errors: 3, last_error: "<img>" } };
    run("renderUsage(USAGE_HOSTILE)");
    if (/<img|<b>|<i>|<s>/.test(textOf(get("usagegraph")) + textOf(get("usagesum")) + get("usageupd").textContent)) fail("hostile text shown");
    // recorder errors are shown from a fixed table
    run("renderUsage({ ...USAGE_FX, recorder: { state: 'running', errors: 2, last_error: 'database' } })");
    if (!/Recorder: the last attempt to save a sample failed \(2 errors/.test(textOf(get("usagegraph")))) fail("recorder error note");
  } finally { ctx.TP_TRAP = false; }
  // collapse state and fetching only while the Events tab is visible
  const calls = [];
  const realFetch = ctx.fetch;
  ctx.fetch = (url) => { calls.push(String(url)); return Promise.resolve({ ok: true, json: () => Promise.resolve(fx) }); };
  run('VIEW = "agents"; USAGE.data = null; USAGE.open = true'); await run("loadUsage()");
  if (calls.length) fail("fetched while the Events tab is not visible");
  run('VIEW = "events"'); await run("loadUsage()");
  if (calls.length !== 1 || calls[0] !== "/api/usage/history?days=35") fail("fetch url: " + calls);
  run("setUsageOpen(false)"); calls.length = 0; await run("loadUsage()");
  if (calls.length || get("usagepanel").getAttribute("aria-expanded") !== null && false) fail("fetched while collapsed");
  if (get("usagetoggle").getAttribute("aria-expanded") !== "false" || !get("usagepanel").classList.contains("collapsed")) fail("collapse state");
  run("setUsageOpen(true)"); await new Promise((r) => setImmediate(r));
  if (!calls.length) fail("expanding did not load");
  ctx.fetch = () => Promise.resolve({ ok: false, json: () => Promise.resolve({}) });
  run("USAGE.data = null"); await run("loadUsage()");
  if (!/Can't read usage history/.test(textOf(get("usagegraph")))) fail("fetch failure message");
  ctx.fetch = realFetch; run('VIEW = "agents"');
  return "usage ok";
}
// Token usage panel: ranked bars, KPI boxes, metric picker, the research-bot row kept out of the totals, states and hostile values.
async function tokenChecks(fx) {
  const fail = (m) => { throw new Error("tokens: " + m); };
  const run = (c) => vm.runInContext(c, ctx);
  const get = (id) => els.get(id);
  const cls = (n) => n.className || "";
  const find = (re, tag) => allOf(get("tokbody")).filter((n) => (!tag || n.tagName === tag) && re.test(cls(n)));
  const rowsOf = () => find(/\btokrow\b/, "div").filter((n) => !/\btokhead\b|\btokrs\b/.test(cls(n)));
  const names = () => rowsOf().map((n) => textOf(n.children[0]).trim().replace(/\s+\d+$/, ""));
  ctx.TP_TRAP = true;   // any innerHTML write while rendering throws
  try {
    ctx.TOK_FX = fx;
    run('TOK.range = "7d"; TOK.metric = "total"; TOK.stale = false; RESEARCH = null; renderTokens(TOK_FX)');
    const text = textOf(get("tokbody"));
    const kp = find(/\btokkpi\b/, "div");
    if (kp.length !== 3 || !/Today/.test(textOf(kp[0])) || !/7 days/.test(textOf(kp[1])) || !/30 days/.test(textOf(kp[2])) || !/output 120k/.test(textOf(kp[0]))) fail("KPI boxes: " + kp.map(textOf));
    if (!/4\.8M/.test(textOf(kp[0])) || !/150\.0M/.test(textOf(kp[2]))) fail("KPI numbers: " + kp.map(textOf));
    if (!/7 days · in .* · out .* · cache write .* · cache read .* · 3,560 calls · 32 sessions/.test(text)) fail("totals line: " + text.slice(0, 300));
    const nm = names();
    if (nm[0] !== "session" || nm[1] !== "implementer") fail("agent ranking: " + nm);
    if (!nm.includes("other (4)") || nm.indexOf("other (4)") !== 4) fail("folded row stays last in its list: " + nm);
    if (!nm.includes("<b>evil</b>") || !nm.includes("<img onerror=x>")) fail("hostile names must show as literal text: " + nm);
    if (!nm.includes("opus-5-5") || nm.indexOf("opus-5-5") < 5) fail("models listed after the agents: " + nm);
    for (const k of ["tk-in", "tk-out", "tk-cw", "tk-cr"]) if (!find(new RegExp("\\b" + k + "\\b"), "rect").length) fail("bar segment " + k);
    for (const f of ["fam-opus", "fam-sonnet", "fam-haiku"]) if (!find(new RegExp("\\b" + f + "\\b"), "rect").length) fail("daily column " + f);
    const hits = find(/\bhit\b/, "rect");
    if (hits.length !== fx.daily.length || hits.some((r) => !(r.getAttribute("data-tip") || "").includes("\n"))) fail("daily tooltips: " + hits.length);
    const tips = rowsOf().map((n) => n.getAttribute("data-tip") || "");
    if (tips.some((t) => !t.includes("\n")) || !/^session · \d+%\ninput .* · output .*\ncache write .* · cache read .*\n3,000 calls · 12 sessions$/.test(tips[0])) fail("row tooltip: " + JSON.stringify(tips[0]));
    if (allOf(get("tokbody")).some((n) => n.getAttribute && n.getAttribute("title") !== null)) fail("native title attribute (not themed)");
    if (!/input/.test(text) || !/output/.test(text) || !/cache write/.test(text) || !/cache read/.test(text)) fail("legend names the four kinds");
    if (!/What is counted/.test(text) || !/Cache reads dominate/.test(text) || !/did not observe/.test(text)) fail("'What is counted' note");
    if (!/Agent × model table/.test(text) || !/opus-5-5/.test(textOf(find(/\btokmatrix\b/, "details")[0]))) fail("matrix fallback table");
    if (find(/\btokdaily\b/, "svg")[0].getAttribute("role") !== "img" || !/stacked by model family/.test(find(/\btokdaily\b/, "svg")[0].getAttribute("aria-label"))) fail("daily chart aria-label");
    const sum = textOf(get("toksum")).trim();
    if (!sum.startsWith("Token usage, 7 days:") || !/Most by session/.test(sum) || !/cache reads \d+%/.test(sum)) fail("summary: " + sum);
    if (get("toksum").getAttribute("aria-live") !== null && get("toksum").getAttribute("aria-live") !== "polite") fail("aria-live");
    if (!/sessions · updated \d\d:\d\d · data from /.test(textOf(get("tokbody")))) fail("token status not in the totals line: " + textOf(get("tokbody")));
    if (els.has("tokupd")) fail("token status must not be in the section header");
    if (find(/\btokrs\b/, "div").length) fail("research row shown without research data");
    // research bot row: shown muted below the agents, never in totals or shares
    const before = JSON.stringify(run("tokRows(TOK_FX.by_agent, 'total', 'agent_type').map((r) => [r.name, r.total, r.share])"));
    run('RESEARCH = { state: "ok", data: { ledger: { today: { tokens_in: 1200000, tokens_out: 80000 }, last_7_days: [{ tokens_in: 100, tokens_out: 5 }, { tokens_in: 900, tokens_out: 15 }] } } }; renderTokens(TOK_FX)');
    const rs = find(/\btokrs\b/, "div");
    if (rs.length !== 1 || !/research bot/.test(textOf(rs[0])) || !/separate, not in totals/.test(textOf(rs[0])) || !/in 1\.0k · out 20/.test(textOf(rs[0]))) fail("research row (7 days): " + rs.map(textOf));
    if (allOf(rs[0]).filter((n) => n.tagName === "button").length !== 1 || typeof allOf(rs[0]).find((n) => n.tagName === "button").onclick !== "function") fail("research link");
    if (names().includes("research bot")) fail("research row is not a ranked row");
    if (JSON.stringify(run("tokRows(TOK_FX.by_agent, 'total', 'agent_type').map((r) => [r.name, r.total, r.share])")) !== before) fail("research row changed the shares");
    if (!/Token usage, 7 days: 4[0-9.]+M tokens/.test(textOf(get("toksum")))) fail("totals changed by the research row: " + textOf(get("toksum")));
    run('TOK.range = "today"; renderTokens(TOK_FX)');
    if (!/in 1\.2M · out 80k/.test(textOf(find(/\btokrs\b/, "div")[0]))) fail("research row (today)");
    run('TOK.range = "30d"; renderTokens(TOK_FX)');
    if (!/last 7 days only/.test(textOf(find(/\btokrs\b/, "div")[0]))) fail("research row (30 days) note");
    run('TOK.range = "7d"; RESEARCH = { state: "error", data: null }; renderTokens(TOK_FX)');
    if (find(/\btokrs\b/, "div").length) fail("research row hidden when Research is not ok");
    run("RESEARCH = null");
    // metric picker: ranking and shares follow the chosen metric
    for (const m of ["output", "inout", "cache_read", "total"]) {
      run(`TOK.metric = "${m}"; renderTokens(TOK_FX)`);
      const sh = run("tokRows(TOK_FX.by_agent, TOK.metric, 'agent_type').map((r) => r.share)");
      if (Math.abs(sh.reduce((a, b) => a + b, 0) - 100) > 0.001) fail("shares sum to 100 for " + m);
      if (!new RegExp("ranked by " + {output: "output", inout: "input \\+ cache write", cache_read: "cache read", total: "total"}[m]).test(textOf(get("tokbody")))) fail("heading names the metric " + m);
    }
    run('TOK.metric = "output"; renderTokens(TOK_FX)');
    if (rowsOf().some((r) => allOf(r).some((n) => /\btk-(cr|in|cw)\b/.test(cls(n))))) fail("output metric draws only output segments");
    run('TOK.metric = "total"');
    // local models: fam-local column and legend, KPI "local" line only under All, totals split, research row hidden under Local
    run(`TOK_LOC = { ...TOK_FX, kpis: { ...TOK_FX.kpis, today: { ...TOK_FX.kpis.today, local: 250000 } },
      by_source: { claude: { total: 40000000, output: 1, messages: 1 }, local: { total: 2494000, output: 1, messages: 1 } },
      by_model: [...TOK_FX.by_model, { model: "qwen3:14b", family: "local", input: 5, output: 7, cache_write: 0, cache_read: 0, total: 12, share: 1 }],
      daily: TOK_FX.daily.map((d, i) => (i === 0 ? { ...d, by_family: { ...d.by_family, local: 90000 } } : d)) }`);
    run('RESEARCH = { state: "ok", data: { ledger: { today: { tokens_in: 1, tokens_out: 1 }, last_7_days: [{ tokens_in: 1, tokens_out: 1 }] } } }; TOK.source = "all"; renderTokens(TOK_LOC)');
    if (!find(/\bfam-local\b/, "rect").length) fail("daily column fam-local");
    if (!find(/\blgitem\b/, "span").some((n) => /^local/.test(textOf(n).trim()) && allOf(n).some((m) => /\bfam-local\b/.test(cls(m))))) fail("legend item fam-local");
    let k0 = find(/\btokkpi\b/, "div")[0];
    if (!/local 250k/.test(textOf(k0)) || !/local 250,000 tokens/.test(k0.getAttribute("data-tip")) || /local/.test(textOf(find(/\btokkpi\b/, "div")[1]))) fail("KPI local line under All: " + textOf(k0));
    if (!/ · Claude 40\.0M · local 2\.5M · updated/.test(textOf(get("tokbody")))) fail("totals split under All: " + textOf(get("tokbody")).slice(0, 400));
    if (!names().includes("qwen3:14b") || !find(/\btokrs\b/, "div").length) fail("local model row and research row under All");
    run('TOK.source = "claude"; renderTokens(TOK_LOC)');
    if (/local 250k/.test(textOf(find(/\btokkpi\b/, "div")[0])) || / · Claude 40/.test(textOf(get("tokbody")))) fail("KPI local line and totals split are All-only");
    run('TOK.source = "local"; renderTokens(TOK_LOC)');
    if (find(/\btokrs\b/, "div").length) fail("research row hidden under Local");
    run('renderTokens({ state: "empty" })');
    if (!/No local-model usage recorded yet \(niu-agent \/ local_\* tools\)\./.test(textOf(get("tokbody")))) fail("empty message under Local: " + textOf(get("tokbody")));
    run('TOK.source = "claude"; renderTokens({ state: "empty" })');
    if (!/^No LLM usage found yet\.$/.test(textOf(get("tokbody")).trim())) fail("empty message under Claude");
    run('TOK.source = "all"; RESEARCH = null; renderTokens(TOK_LOC)');
    if (!/niu-agent agent-mode transcripts/.test(textOf(get("tokbody"))) || !/local-usage\.jsonl/.test(textOf(get("tokbody"))) || !/not part of the Weekly %/.test(textOf(get("tokbody")))) fail("'What is counted' covers local usage");
    // hostile values in the new keys
    run(`renderTokens({ ...TOK_FX, source: "<b>", kpis: { ...TOK_FX.kpis, today: { total: 5, output: 1, local: "x" }, "7d": { total: 5, output: 1, local: -4 } },
      by_source: { claude: "x", local: { total: "<i>" } }, by_model: [{ model: "m", family: 'local" onload', input: 1 }], coverage: { ...TOK_FX.coverage, local_root: "<b>" } })`);
    if (/local \d|· local/.test(textOf(get("tokbody"))) ) fail("hostile local values must not draw a local line: " + textOf(get("tokbody")).slice(0, 300));
    if (!find(/\bfam-other\b/, "rect").length && !allOf(get("tokbody")).some((n) => /\bfam-other\b/.test(cls(n)))) fail("hostile family falls back to fam-other");
    if (allOf(get("tokbody")).some((n) => /onload/.test(cls(n)))) fail("hostile family leaked into a class");
    run('RESEARCH = null; TOK.source = "all"');
    // states
    const msgs = { loading: /^Loading…$/, empty: /No LLM usage found yet\./, disabled: /Token statistics are off \(NIU_USAGE_TOKENS=0/ };
    for (const [state, re] of Object.entries(msgs)) {
      run(`renderTokens({ state: "${state}" })`);
      if (!re.test(textOf(get("tokbody")).trim()) || textOf(get("toksum")).trim() !== "") fail(`${state} message: ` + textOf(get("tokbody")));
    }
    run('renderTokens({ state: "empty", coverage: { scanning: true, files_done: 412, files_total: 1930 } })');
    if (!/Reading transcripts for the first time: 412 of 1[,.]?930 files/.test(textOf(get("tokbody")))) fail("scanning progress with no data yet: " + textOf(get("tokbody")));
    run('renderTokens({ ...TOK_FX, state: "scanning", coverage: { ...TOK_FX.coverage, scanning: true, files_done: 412, files_total: 1930 } })');
    if (!/Reading transcripts for the first time: 412 of 1[,.]?930 files\. The numbers below are partial/.test(textOf(get("tokbody"))) || !names().includes("session")) fail("scanning state keeps the partial data");
    run('renderTokens({ ...TOK_FX, coverage: { ...TOK_FX.coverage, corrupt_lines: 3 } })');
    if (!/3 transcript lines could not be read/.test(textOf(get("tokbody")))) fail("corrupt lines note");
    for (const bad of ["null", '{ state: "error", message: "boom <b>x</b>" }', '{ state: "bogus" }', '"text"', '{ state: "ok" }', '{ state: "ok", totals: {}, kpis: {}, by_agent: 5, by_model: [] }']) {
      run(`renderTokens(${bad})`);
      if (textOf(get("tokbody")).trim() !== "Can't read token usage from the dashboard server.") fail("error message for " + bad + ": " + textOf(get("tokbody")));
    }
    // hostile payload: wrong types never throw and no server text becomes markup
    ctx.TOK_HOSTILE = { ...fx, tz: "<img onerror=x>", kpis: { today: "x", "7d": { total: "<b>", output: -5 }, "30d": null },
      totals: { input: "a", output: NaN, cache_write: Infinity, cache_read: -1, total: "<i>", messages: "q", sessions: null },
      by_agent: [null, "x", { agent_type: "<script>alert(1)</script>", input: "9", output: 1e308, cache_write: -4, cache_read: null, share: "<b>", sessions: "z", family: "javascript:x" }, { agent_type: 5 }],
      by_model: [{ model: { a: 1 }, family: "<svg onload=x>", input: 1 }, { model: "m".repeat(500), family: "opus", output: 3 }],
      matrix: { agents: ["a", 7, null], models: ["m"], total: [[1, "x"], "no", null] }, daily: [null, { label: "<b>", by_family: "no" }, { label: 5, by_family: { opus: "x", sonnet: -3, haiku: 1e300 } }],
      coverage: { oldest: "x", last_scan: "y", scanning: "yes", files_done: -1, files_total: "<b>", corrupt_lines: "<i>" } };
    run("renderTokens(TOK_HOSTILE)");
    const ht = textOf(get("tokbody")) + textOf(get("toksum"));
    if (!ht.includes("<script>alert(1)</script>")) fail("hostile agent name must appear as literal text");
    if (find(/javascript|<svg/, "rect").length || allOf(get("tokbody")).some((n) => /javascript|<svg|onload/.test(cls(n)))) fail("hostile family leaked into a class");
    if (allOf(get("tokbody")).some((n) => n.tagName === "script" || n.tagName === "img" || n.tagName === "b" && n.textContent === "<b>x</b>")) fail("markup created from payload text");
    run("renderTokens({ ...TOK_FX, by_agent: [], by_model: [], daily: [], matrix: null })");
    if (!textOf(get("tokbody")).includes("nothing in this range")) fail("empty lists render");
  } finally { ctx.TP_TRAP = false; }
  // fetching only while the Events tab is visible, URL, collapse, failure keeps the last data
  const calls = [];
  const realFetch = ctx.fetch;
  ctx.fetch = (url) => { calls.push(String(url)); return Promise.resolve({ ok: true, json: () => Promise.resolve(fx) }); };
  get("domain").value = "";
  run('VIEW = "agents"; TOK.data = null; TOK.open = true; TOK.range = "7d"'); await run("loadTokens()");
  if (calls.length) fail("fetched while the Events tab is not visible");
  run('VIEW = "events"'); await run("loadTokens()");
  if (calls.length !== 1 || calls[0] !== "/api/usage/tokens?range=7d") fail("fetch url: " + calls);
  run('setTokSource("local")'); await new Promise((r) => setImmediate(r));
  if (!calls.includes("/api/usage/tokens?range=7d&source=local") || get("tok-s-local").getAttribute("aria-pressed") !== "true" || get("tok-s-all").getAttribute("aria-pressed") !== "false") fail("source button: " + calls);
  if (!(globalThis.LS_SET || []).some(([k, v]) => k === "niu.tokens.source" && v === '"local"')) fail("source choice is stored");
  run('setTokSource("<script>")'); if (run("TOK.source") !== "local") fail("junk source rejected");
  run('setTokSource("all")'); await new Promise((r) => setImmediate(r));
  if (calls[calls.length - 1] !== "/api/usage/tokens?range=7d" || (globalThis.LS_SET || []).filter(([k]) => k === "niu.tokens.source").pop()[1] !== '"all"') fail("All sends no source param: " + calls);
  calls.length = 0;
  get("domain").value = "PER SONAL&x";
  run("TOK.data = null"); calls.length = 0; await run("loadTokens()");
  if (calls[0] !== "/api/usage/tokens?range=7d&domain=PER%20SONAL%26x") fail("domain is encoded: " + calls);
  get("domain").value = "";
  run("setTokRange('30d')"); await new Promise((r) => setImmediate(r));
  if (!calls.some((u) => u === "/api/usage/tokens?range=30d") || get("tok-r-30d").getAttribute("aria-pressed") !== "true" || get("tok-r-7d").getAttribute("aria-pressed") !== "false") fail("range buttons: " + calls);
  run("setTokRange('7d')");
  run("setUsageOpen(false)"); calls.length = 0; await run("loadTokens()");
  if (calls.length) fail("tokens fetched while the usage panel is collapsed");
  if (get("usagetoggle").getAttribute("aria-expanded") !== "false" || !get("usagepanel").classList.contains("collapsed")) fail("collapse state");
  if (get("usagetoggle").getAttribute("aria-label") !== "Expand LLMs usage") fail("toggle label: " + get("usagetoggle").getAttribute("aria-label"));
  run("setUsageOpen(true)"); await new Promise((r) => setImmediate(r));
  if (!calls.some((u) => u.startsWith("/api/usage/tokens")) || !calls.some((u) => u.startsWith("/api/usage/history"))) fail("expanding did not load both sections: " + calls);
  if (typeof ctx.setTokOpen !== "undefined" || els.has("toktoggle")) fail("the Token usage section must not have its own toggle");
  ctx.fetch = () => Promise.resolve({ ok: false, json: () => Promise.resolve({}) });
  await run("loadTokens()");
  if (!/showing the data as of/.test(textOf(get("tokbody"))) || !names().includes("session")) fail("a failed refresh keeps the last data with 'as of'");
  run("TOK.data = null; TOK.stale = false"); await run("loadTokens()");
  if (textOf(get("tokbody")).trim() !== "Can't read token usage from the dashboard server.") fail("fetch failure message: " + textOf(get("tokbody")));
  ctx.fetch = realFetch; run('VIEW = "agents"; TOK.stale = false; TOK.data = null');
  return "tokens ok";
}
// Session agent map: the details panel shows every catalog agent (dashed = available) above a recent-activity list.
// The catalog is the real /api/agents response when the last argv entry is an agents.json file, else a small fixture.
async function amChecks(catFile) {
  const fail = (m) => { throw new Error("agent map: " + m); };
  const run = (c) => vm.runInContext(c, ctx);
  const tick = async () => { for (let i = 0; i < 5; i++) await new Promise((r) => setImmediate(r)); };
  const cls = (e) => (e && e.getAttribute("class")) || "";
  const fixture = { sid: "x", agents: [
    { name: "designer", source: "shared", model: "claude-opus-5-5", effort: null, description: "Designs." },
    { name: "implementer", source: "shared", model: "claude-sonnet-5-5", effort: null, description: "Builds." },
    { name: "security-reviewer", source: "shared", model: "claude-sonnet-5-5", effort: null, description: "Reviews security." },
    { name: "test-reviewer", source: "shared", model: "claude-sonnet-5-5", effort: "high", description: "Reviews tests." },
    { name: "general-purpose", source: "builtin", model: "inherit", effort: null, description: "General." },
    { name: "Explore", source: "builtin", model: "inherit", effort: null, description: "Search." },
    { name: "Plan", source: "builtin", model: "inherit", effort: null, description: "Plan." }],
    skills: [{ name: "develop", source: "shared", flow: true, description: "d" }, { name: "dual-review", source: "shared", flow: true, description: "r" }, { name: "other", source: "shared", flow: false, description: "o" }],
    local_tools: ["local_search", "local_diff_review", "local_ask", "local_logs", "local_ci", "local_git", "local_status"].map((n) => ({ name: n, description: n, available: true })), generated_at: 1 };
  const last = process.argv[process.argv.length - 1];
  const cat = /agents\.json$/.test(last) ? JSON.parse(fs.readFileSync(last, "utf8")) : fixture;
  const realFetch = ctx.fetch;
  const calls = [];
  ctx.fetch = (url) => { calls.push(String(url)); return Promise.resolve({ ok: true, json: () => Promise.resolve(cat) }); };
  ctx.TP_TRAP = true;   // the whole agent map is built with h() / svg(): any innerHTML write throws
  const packets = [], realPacket = ctx.packet;
  try {
    run(`(() => {
      const s0 = STATE.sessions[0], t = STATE.now;
      globalThis.AM_SAVED = { subagents: s0.subagents, tools: s0.tools, ended: s0.ended, state: s0.state, pipeline: s0.pipeline, handoffs: s0.chain && s0.chain.handoffs };
      s0.ended = false; s0.state = "working"; s0.pipeline = null; s0.waits = [t - 400];
      s0.subagents = [
        { id: "am-t", type: "test-reviewer", source: "shared", start: t - 300, stop: t - 100, tools: [[t - 200, "Bash"]], parent: s0.id, model: null, model_src: null, task: "" },
        { id: "am-s", type: "security-reviewer", source: "shared", start: t - 60, stop: null, tools: [], parent: s0.id, model: null, model_src: null, task: "" }];
      s0.tools = [[t - 30, "mcp__niu-local__local_search"], [t - 20, "Edit"]];
      renderGraph();
      globalThis.AM_G0 = JSON.stringify([[...G.nodes.keys()], [...G.edges.keys()]]);
    })()`);
    const sid = run("STATE.sessions[0].id");
    run(`showDetails({ sid: ${JSON.stringify(sid)} })`); await tick();
    if (!calls.some((u) => u === "/api/agents?sid=" + encodeURIComponent(sid))) fail("catalog not fetched: " + calls);
    if (!els.get("details").classList.contains("wide") || els.get("details").hidden) fail("panel not wide / visible");
    const nodes = run("AM.nodes");
    for (const a of cat.agents) if (!nodes.has("a:" + a.name)) fail("no node for catalog agent " + a.name);
    for (const t of cat.local_tools) if (!nodes.has("l:" + t.name)) fail("no node for local tool " + t.name);
    if (!/\bused\b/.test(cls(nodes.get("a:test-reviewer"))) || !/\brunning\b/.test(cls(nodes.get("a:security-reviewer")))) fail("reviewer states: " + cls(nodes.get("a:test-reviewer")) + " / " + cls(nodes.get("a:security-reviewer")));
    if (nodes.has("a:designer") && !/\bavail\b/.test(cls(nodes.get("a:designer")))) fail("designer should be available");
    if (!/\bused\b/.test(cls(run('AM.edges.get("d:dual-review>a:test-reviewer").base')))) fail("dual-review edge not used");
    if (!/\bavail\b/.test(cls(run('AM.edges.get("d:agent>a:Explore").base')))) fail("unused edge should be avail");
    if (!/\bavail\b/.test(cls(nodes.get("a:Explore"))) || !/available/.test(textOf(nodes.get("a:Explore")))) fail("Explore should be dimmed 'available'");
    // activity
    const rows = run("AM.rows");
    const rt = rows.find((r) => r.text === "test-reviewer · Bash"), rl = rows.find((r) => /^local_search/.test(r.text));
    if (!rt || !rl) fail("rows missing: " + rows.map((r) => r.text));
    if (rows.length > 40 || rows.some((r, i) => i && rows[i - 1].ts < r.ts)) fail("rows not newest first / capped");
    run(`amSelect(${JSON.stringify(rt.key)})`);
    if (!/\bhl\b/.test(cls(nodes.get("a:test-reviewer"))) || !/\bhl\b/.test(cls(run('AM.edges.get("d:dual-review>a:test-reviewer").base')))) fail("selected row did not highlight its agent and edge");
    if (nodes.has("a:designer") && !/\bfade\b/.test(cls(nodes.get("a:designer")))) fail("other agents not faded");
    const rowEls = kidsOf(els.get("amfeed")), rowEl = rowEls.find((e) => e.getAttribute("data-key") === rt.key);
    if (!rowEl || rowEl.getAttribute("aria-selected") !== "true" || els.get("amfeed").getAttribute("role") !== "listbox" || rowEl.getAttribute("role") !== "option") fail("row aria state");
    run(`showDetails(SELECTED, true)`);
    if (run("AM.sel") !== rt.key) fail("selection lost on refresh");
    run(`amSelect(${JSON.stringify(rl.key)})`);
    if (!/\bhl\b/.test(cls(nodes.get("l:local_search")))) fail("local_search row did not highlight l:local_search");
    // keyboard
    const ev = (key) => ({ key, preventDefault() {} });
    const keys = run("AM.rows.map((r) => r.key)");
    run(`AM.cur = ${JSON.stringify(keys[0])}; AM.sel = null`);
    els.get("amfeed")._ls.keydown.forEach((f) => f(ev("ArrowDown")));
    if (run("AM.sel") !== keys[1]) fail("ArrowDown did not select the next row");
    els.get("amfeed")._ls.keydown.forEach((f) => f(ev("End")));
    if (run("AM.sel") !== keys[keys.length - 1]) fail("End did not select the last row");
    els.get("amfeed")._ls.keydown.forEach((f) => f(ev("Backspace")));
    if (run("AM.sel") !== null || /\b(hl|fade)\b/.test(cls(nodes.get("a:test-reviewer")))) fail("Backspace did not clear the highlight");
    // clicking a node highlights its rows
    run('amFocus("a:test-reviewer")');
    if (!kidsOf(els.get("amfeed")).some((e) => /\bon\b/.test(cls(e))) || !/\bfocus\b/.test(cls(nodes.get("a:test-reviewer")))) fail("node click did not mark rows");
    run('amFocus("a:test-reviewer")');
    // isolation: the Agent interactions graph is untouched
    if (run("JSON.stringify([[...G.nodes.keys()], [...G.edges.keys()]])") !== run("AM_G0")) fail("opening the map changed the interactions graph");
    // live animation
    ctx.packet = (pid, c, o) => { packets.push([pid, c, o && o.layer]); };
    const addSub = (type, id) => run(`STATE.sessions[0].subagents.push({ id: ${JSON.stringify(id)}, type: ${JSON.stringify(type)}, source: "builtin", start: STATE.now, stop: null, tools: [], parent: STATE.sessions[0].id, model: null, model_src: null, task: "" }); showDetails(SELECTED, true)`);
    addSub("Explore", "am-x");
    const pid = (id) => run(`AM.edges.get(${JSON.stringify(id)}).pid`);
    if (packets.length < 2 || packets[0][0] !== pid("main>d:agent") || packets[0][1] !== "spawn" || packets[1][0] !== pid("d:agent>a:Explore")) fail("spawn packets: " + JSON.stringify(packets));
    if (packets[0][2] !== run("AM.pk")) fail("packets not drawn into the map's own layer");
    packets.length = 0;
    run('$("calm").checked = true'); addSub("Plan", "am-y");
    if (packets.length) fail("packets drawn with Reduce motion on");
    run('$("calm").checked = false; STATE.sessions[0].ended = true'); addSub("general-purpose", "am-z");
    if (packets.length) fail("packets drawn for an ended session");
    ctx.packet = realPacket;
    // catalog failure: used agents plus a note
    ctx.fetch = () => Promise.reject(new Error("down"));
    run('AM.cache.clear(); STATE.sessions[0].ended = false; $("details").hidden = true; showDetails({ sid: STATE.sessions[0].id })'); await tick();
    if (!/could not be loaded/.test(textOf(els.get("amap")))) fail("failure note missing: " + textOf(els.get("amap")));
    if (!run("AM.nodes").has("a:test-reviewer") || run("AM.nodes").has("a:Plan") && !/used|running/.test(cls(run("AM.nodes").get("a:Plan")))) fail("used agents must stay drawn when the catalog fails");
    if (run("AM.nodes").has("l:local_status")) fail("available-only nodes drawn without a catalog");
    // close button and Esc semantics
    kidsOf(els.get("amhead")).find((e) => e.getAttribute("id") === "dclose").onclick();
    if (!els.get("details").hidden || run("SELECTED") !== null) fail("close button");
    // source scan: no HTML sinks in the agent map code
    const app = fs.readFileSync(process.argv[2], "utf8");
    const a0 = app.indexOf("// ---------- session agent map"), a1 = app.indexOf("// ---------- actions ----------");
    if (a0 < 0 || a1 < a0) fail("section markers not found");
    const sect = app.slice(a0, a1);
    for (const bad of ["innerHTML", "insertAdjacentHTML", "outerHTML", "document.write", "eval(", "new Function"]) if (sect.includes(bad)) fail("agent map section uses " + bad);
    // The fake DOM here uses arrays for children; real browsers return an HTMLCollection (no filter/find/map/some...).
    const arr = sect.match(/\.(children|childNodes)\.(filter|find|findIndex|map|some|every|reduce|slice|indexOf|includes|forEach\b(?!\s*\())/g);
    if (arr) fail("agent map section calls an array method on a DOM collection: " + arr.join(", "));
    if (/\.(children|childNodes)\.(filter|find|findIndex|map|some|every|reduce|slice|indexOf|includes)\(/.test(sect)) fail("DOM collection used as an array in the agent map");
  } finally {
    ctx.TP_TRAP = false; ctx.packet = realPacket; ctx.fetch = realFetch;
    run(`(() => { const s0 = STATE.sessions[0], v = globalThis.AM_SAVED; if (v) { s0.subagents = v.subagents; s0.tools = v.tools; s0.ended = v.ended; s0.state = v.state; s0.pipeline = v.pipeline; } $("details").hidden = true; SELECTED = null; })()`);
  }
  return "agent map ok";
}
(async () => {
  try {
    console.log(vm.runInContext(src, ctx, { filename: "app.js" }));
    console.log(await vm.runInContext(extra, ctx, { filename: "extra.js" }));
    console.log(timelineChecks());
    console.log(flagChecks());
    if (process.argv[4]) console.log(await researchChecks(JSON.parse(fs.readFileSync(process.argv[4], "utf8"))));
    if (process.argv[4]) console.log(await researchStatusChecks(JSON.parse(fs.readFileSync(process.argv[4], "utf8")), fs.readFileSync(process.argv[2], "utf8")));
    if (process.argv[6]) console.log(await usageChecks(JSON.parse(fs.readFileSync(process.argv[6], "utf8"))));
    if (process.argv[7]) console.log(await tokenChecks(JSON.parse(fs.readFileSync(process.argv[7], "utf8"))));
    if (process.argv[5]) console.log(await topicsChecks(JSON.parse(fs.readFileSync(process.argv[5], "utf8")), fs.readFileSync(process.argv[2], "utf8")));
    console.log(await amChecks());
    if (process.argv[5]) console.log(await topicsViewChecks(JSON.parse(fs.readFileSync(process.argv[5], "utf8")), fs.readFileSync(process.argv[2], "utf8")));
  } catch (e) { console.log("ERROR:", e.message, (e.stack || "").split("\n").slice(1,5).join(" ; ")); process.exit(1); }
})();
