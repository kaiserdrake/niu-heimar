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
try { console.log(vm.runInContext(src, ctx, { filename: "app.js" })); }
catch (e) { console.log("ERROR:", e.message, (e.stack || "").split("\n")[1]); process.exit(1); }
