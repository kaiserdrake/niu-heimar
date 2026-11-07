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
  if (rowsOf().length !== 3) fail(`expected 3 active rows, got ${rowsOf().length}`);
  const list = textOf(get("tplist"));
  if (!/General · 2\/200/.test(list) || !/Hobby · 1\/200/.test(list)) fail("group counts: " + list.slice(0, 200));
  if (!/General 2\/200/.test(textOf(get("tpchips"))) || !/Fintrend 0\/200/.test(textOf(get("tpchips")))) fail("agent chips: " + textOf(get("tpchips")));
  if (!/Fintrend agent arrives in M4/.test(list)) fail("fintrend note missing");
  if (get("tpadd").disabled) fail("Add topic disabled although paired and ok");
  if (!allOf(get("tplist")).some((n) => n.tagName === "svg") || !allOf(get("tplist")).some((n) => /\btpw-fg\b/.test(n.className || ""))) fail("weight bar missing");
  run("TP.archived = true; renderTopics()");
  if (rowsOf().length !== 4 || !rowsOf().some((n) => /\btp-archived\b/.test(n.className)) || !/Restore/.test(textOf(get("tplist")))) fail("archived toggle");
  run("TP.archived = false; TP.filter = 'moog'; renderTopics()");
  if (rowsOf().length !== 1) fail("text filter");
  run("TP.filter = 'zzzz'; renderTopics()");
  if (!/No topic matches "zzzz"/.test(textOf(get("tplist")))) fail("empty filter state");
  run("TP.filter = ''; renderTopics()");
  fuzzyChecks(run, fail);
  // fuzzy search through the real render path: ranking, AND narrowing, counts, highlight, hostile text
  const mk = (id, agent, name, kw, ex, act = true) => ({ id, agent_type: agent, name, keywords: kw, excluded_terms: ex || [], weight: 1, is_active: act, updated_at: "2026-11-07T00:00:00Z" });
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
  Object.assign(reply, { ok: false, status: 409, data: { ok: false, code: "duplicate", existing_id: "00000004-0000-4000-8000-000000000004", existing_active: false } });
  await fire("tpform", "submit", form);
  if (!/already exists/.test(get("tpformmsg").textContent) || get("tprestoredup").hidden) fail("duplicate handling");
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
  // edit sends only changed fields; 412 offers Reload; archive needs two clicks
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
  run("tpDialog(TP.data.topics.find((t) => t.is_active))");
  calls.length = 0;
  await get("tparchive")._ls.click[0]({});
  if (calls.length || get("tparchive").textContent !== "Confirm archive") fail("first archive click must only arm");
  Object.assign(reply, { ok: true, status: 200, data: { ok: true, topic: { ...run("TP.editing"), is_active: false } } });
  await get("tparchive")._ls.click[0]({});
  if (!calls.some((x) => x.url === "/api/topics/update" && x.body.fields.is_active === false)) fail("second click archives");
  ctx.TP_TRAP = false;
  return "topics ok";
}
(async () => {
  try {
    console.log(vm.runInContext(src, ctx, { filename: "app.js" }));
    console.log(await vm.runInContext(extra, ctx, { filename: "extra.js" }));
    if (process.argv[4]) console.log(await researchChecks(JSON.parse(fs.readFileSync(process.argv[4], "utf8"))));
    if (process.argv[5]) console.log(await topicsChecks(JSON.parse(fs.readFileSync(process.argv[5], "utf8")), fs.readFileSync(process.argv[2], "utf8")));
  } catch (e) { console.log("ERROR:", e.message, (e.stack || "").split("\n")[1]); process.exit(1); }
})();
