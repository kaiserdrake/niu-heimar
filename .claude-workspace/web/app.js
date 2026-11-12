"use strict";
// niu-heimar web dashboard. All server strings are escaped (or set via textContent) before rendering.

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const SVGNS = "http://www.w3.org/2000/svg";
const POST = (url, body) => fetch(url, {
  method: "POST", credentials: "same-origin",
  headers: { "Content-Type": "application/json", "X-Niu-CSRF": "1" },
  body: JSON.stringify(body || {}),
}).then(async (r) => ({ ok: r.ok, status: r.status, data: await r.json().catch(() => ({})) }));
const store = {
  get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch { /* storage unavailable */ } },
};

let STATE = null, PRS = null, ME = { paired: false }, ES = null, SELECTED = null, LAST_FEED_TS = 0, CLOCK_OFFSET = 0;
let RESEARCH = null, RS_OFFSET = 0, RS_TIMER = 0;
let ACTIONS = [], SEEN_ACTIONS = null, ACT_BY = { session: new Map(), pr: new Map() };
const STATE_LABEL = { working: "working", waiting: "waiting for you", idle: "idle", ended: "ended", stale: "stale" };
const TOOL_CLASS = (t) => String(t || "").startsWith("mcp__niu-local") ? "local" : t === "Bash" ? "bash" : ["Read", "Grep", "Glob"].includes(t) ? "read"
  : ["Edit", "MultiEdit", "Write", "NotebookEdit"].includes(t) ? "edit" : ["Agent", "Task", "SendMessage"].includes(t) ? "agent"
  : ["WebFetch", "WebSearch"].includes(t) ? "web" : "other";
const reducedMotion = () => $("calm").checked || window.matchMedia("(prefers-reduced-motion: reduce)").matches;
const HOST = () => STATE?.host || "workspace";
const dl = (d) => d === "(workspace)" ? HOST() : (d || "?");

function dur(secs) {
  secs = Math.max(0, Math.round(secs));
  if (secs < 60) return `${secs}s`;
  if (secs < 3600) return `${Math.floor(secs / 60)}m${String(secs % 60).padStart(2, "0")}s`;
  if (secs < 86400) return `${Math.floor(secs / 3600)}h${String(Math.floor((secs % 3600) / 60)).padStart(2, "0")}m`;
  return `${Math.floor(secs / 86400)}d${Math.floor((secs % 86400) / 3600)}h`;
}
const hhmmss = (ts) => new Date(ts * 1000).toLocaleTimeString([], { hour12: false });
// Gone = ended, or stale (no SessionEnd, e.g. a closed or archived desktop session, and nothing logged for 30 min).
const gone = (s) => s.ended || s.state === "stale";
const visible = () => (STATE?.sessions || []).filter((s) => $("ended").checked || !gone(s));
const trunc = (s, n) => (s = String(s || ""), s.length > n ? s.slice(0, n - 1) + "…" : s);
const projLabel = (s) => s.project || (s.domain === "(workspace)" ? "niu-heimar config" : "(domain root)");
const TASK_NAME = { search: "search agent", diff: "diff reviewer", ask: "Q&A agent", local_search: "search agent",
  local_diff_review: "diff reviewer", local_ask: "Q&A agent" };
const shortModel = (m) => String(m || "").replace(/^claude-/, "").replace(/-\d{8}$/, "");
// Name: the session's own title, or what it is (local agent task / Claude session + short id).
function sessName(s) {
  if (s.local) return `Local ${TASK_NAME[s.task] || TASK_NAME[s.parent_tool] || "agent"}`;
  return s.title || `Claude session ${s.id.slice(0, 4)}`;
}
// Details line: where it runs and on which model.
function sessSub(s) {
  if (s.local) return `${s.local} · ${STATE_LABEL[s.state] || s.state}`;
  const bits = [s.title ? projLabel(s) : `${projLabel(s)}`, dl(s.domain)];
  if (s.domain === "(workspace)") bits.splice(1, 1);
  if (s.model) bits.push(shortModel(s.model));
  bits.push(`${STATE_LABEL[s.state] || s.state}${s.current ? ` · ${s.current[0]}` : ""}`);
  return bits.join(" · ");
}
function svg(tag, attrs, text) {
  const el = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs || {})) el.setAttribute(k, v);
  if (text !== undefined) el.textContent = text;
  return el;
}

// ---------- connection ----------
function connect() {
  if (ES) ES.close();
  const q = new URLSearchParams({ since: $("since").value });
  if ($("domain").value) q.set("domain", $("domain").value);
  if (VIEW === "events") q.set("feed", "250");
  ES = new EventSource(`/api/stream?${q}`);
  ES.onopen = () => { $("conn").className = "dot on"; };
  ES.onerror = () => { $("conn").className = "dot off"; };
  ES.onmessage = (ev) => {
    const s = JSON.parse(ev.data);
    PRS = s.prs; delete s.prs; STATE = s;
    // The server only pushes on change; keep durations moving with a local clock aligned to the host.
    if (s.server_now) CLOCK_OFFSET = s.server_now - Date.now() / 1000;
    STATE.now = Date.now() / 1000 + CLOCK_OFFSET;
    render();
  };
}

async function loadMe() {
  ME = await fetch("/api/me", { credentials: "same-origin" }).then((r) => r.json()).catch(() => ({ paired: false }));
  $("pairform").hidden = ME.paired;
  $("unpair").hidden = !ME.paired;
  $("menudot").hidden = ME.paired;
  $("devstatus").textContent = ME.paired ? `Paired as "${ME.device.name}". Merge buttons are enabled.` : "Not paired: you can view everything, but merging is disabled.";
  if (PRS) renderPRs();
  renderTopics();
}

// ---------- menus (hamburger + "needs you") ----------
const DROPS = { menu: "menubtn" };
function toggleDrop(name, open) {
  for (const [n, btn] of Object.entries(DROPS)) {
    const show = n === name ? (open ?? $(n).hidden) : false;
    $(n).hidden = !show;
    $(btn).setAttribute("aria-expanded", String(show));
  }
  if (name === "menu" && !$("menu").hidden && !ME.paired) setTimeout(() => $("paircode").focus(), 50);
}
const toggleMenu = (open) => toggleDrop("menu", open);
$("menubtn").addEventListener("click", (e) => { e.stopPropagation(); toggleDrop("menu"); });
$("bellbtn").addEventListener("click", (e) => {
  e.stopPropagation();
  const panel = $("needspanel");
  panel.scrollIntoView({ block: "nearest", behavior: reducedMotion() ? "auto" : "smooth" });
  panel.classList.remove("flash"); void panel.offsetWidth; panel.classList.add("flash");
});

// ---------- views ----------
function refit() { if (STATE && VIEW === "agents") { renderGraph(); renderTimeline(); } if (VIEW === "events") redrawUsage(); }
let VIEW = "agents";
function setView(v) {
  VIEW = v === "events" ? "events" : v === "research" ? "research" : "agents";
  $("mcgrid").dataset.view = VIEW;
  for (const [id, name] of [["tab-agents", "agents"], ["tab-events", "events"], ["tab-research", "research"]]) {
    $(id).classList.toggle("on", VIEW === name); $(id).setAttribute("aria-pressed", String(VIEW === name));
  }
  store.set("niu.view", VIEW);
  const hash = VIEW === "agents" ? "" : VIEW === "research" && RSVIEW !== "status" ? `#research/${RSVIEW}` : `#${VIEW}`;
  if (location.hash !== hash) history.replaceState(null, "", hash || location.pathname);
  connect();
  setTimeout(refit, 50);
  if (VIEW === "events") { loadUsage(); loadTokens(); }
  if (VIEW === "research") { renderResearch(); loadResearch(); if (RSVIEW === "topics") loadTopics(); }
}
$("tab-agents").onclick = () => setView("agents");
$("tab-events").onclick = () => setView("events");
$("tab-research").onclick = () => setView("research");
let LLM_EPS = [];
document.addEventListener("click", (e) => {
  for (const n of Object.keys(DROPS)) if (!$(n).hidden && !$(n).contains(e.target)) toggleDrop(n, false);
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") { toggleDrop("menu", false); $("details").hidden = true; SELECTED = null; }
});

// Hold Ctrl to reveal which workspace each session belongs to (hidden otherwise, e.g. when screen sharing).
const setCtrl = (on) => document.body.classList.toggle("ctrl", on);
document.addEventListener("keydown", (e) => { if (e.key === "Control") setCtrl(true); });
document.addEventListener("keyup", (e) => { if (e.key === "Control") setCtrl(false); });
window.addEventListener("blur", () => setCtrl(false));

// ---------- render ----------
function render() {
  $("hostname").textContent = HOST();
  for (const el of document.querySelectorAll(".hostref")) el.textContent = HOST();
  document.title = `${HOST()} · niu-heimar agents`;
  document.body.classList.toggle("calm", reducedMotion());
  indexActions();
  renderDomains(); renderCounters(); renderActions(); renderSessions(); renderPRs(); renderToday(); renderFeed();
  if (VIEW === "agents") { renderGraph(); renderTimeline(); }
  if (SELECTED) showDetails(SELECTED, true);
}

function renderDomains() {
  const sel = $("domain"), cur = sel.value;
  const have = new Set([...sel.options].map((o) => o.value));
  for (const d of STATE.domains) if (!have.has(d)) sel.add(new Option(dl(d), d));
  for (const o of sel.options) if (o.value) o.text = dl(o.value);
  sel.value = cur;
}

function renderCounters() {
  const live = STATE.sessions.filter((s) => !gone(s));
  const subs = live.reduce((n, s) => n + s.subagents.filter((a) => !a.stop).length, 0);
  const waiting = live.filter((s) => s.state === "waiting").length;
  const green = (PRS?.repos || []).reduce((n, r) => n + r.prs.filter((p) => p.green && !p.release).length, 0);
  $("counters").innerHTML = `<span title="Live sessions"><b>${live.length}</b> sessions</span>` +
    `<span class="${subs ? "hot" : ""}" title="Agents running now"><b>${subs}</b> running</span>` +
    `<span class="${waiting ? "wait" : ""}" title="Sessions waiting for you"><b>${waiting}</b> waiting</span>` +
    `<span title="Pull requests ready to merge"><b>${green}</b> PR${green === 1 ? "" : "s"} ready</span>`;
}

function renderSessions() {
  const list = visible();
  if (!list.length) { $("sessions").innerHTML = `<div class="empty">No active sessions in the workspace.</div>`; return; }
  const byDom = {};
  for (const s of list) (byDom[s.domain] ||= []).push(s);
  let html = "";
  for (const [dom, ss] of Object.entries(byDom).sort()) {
    for (const s of ss) {
      const cur = s.state === "waiting" && s.note ? `waiting: ${s.note}` : s.current ? `▸ ${s.current[0]}: ${s.current[1]}` : "";
      const chips = s.subagents.filter((a) => !a.stop || STATE.now - a.stop < 600).map((a) =>
        `<span class="chip ${a.stop ? "" : "run"}" title="${esc(a.task)}">${a.stop ? "✓" : "●"} ${esc(a.type)} <span class="src src-${esc(a.source)}">${esc(a.source)}</span>` +
        `${!a.stop && a.current ? ` <span class="muted">${esc(a.current[0])}</span>` : ""}</span>`).join("") +
        s.pending.map((p) => `<span class="chip pend">○ ${esc(p.type)} <span class="src src-${esc(p.source)}">${esc(p.source)}</span></span>`).join("");
      const need = ACT_BY.session.get(s.id);
      const needTag = need ? `<span class="needtag ${need.priority >= 3 ? "" : "mid"}" title="${esc(need.title)}">needs you</span>` : "";
      html += `<div class="sess st-${esc(s.state)} ${s.flagged ? "flag" : ""} ${need ? `needs-you ${need.priority >= 3 ? "high" : ""}` : ""}" data-sid="${esc(s.id)}">
        <div class="top"><span class="proj" title="${esc(sessName(s))}"><span class="wspill">${esc(dl(s.domain))}</span>${esc(sessName(s))} ${s.local ? `<span class="localtag" title="Agent running on a local Ollama model">local · ${esc(s.local)}</span>` : ""} ${needTag}</span>
        <span class="pill st-${esc(s.state)}">${esc(STATE_LABEL[s.state] || s.state)} · ${dur(STATE.now - s.since)}</span></div>
        <div class="muted small">${esc(projLabel(s))}${s.model ? ` · ${esc(shortModel(s.model))}` : ""}${s.local && s.query ? ` · “${esc(trunc(s.query, 50))}”` : ""} · ${esc(s.id.slice(0, 8))}${s.chain?.review_rounds ? ` · <b title="Rounds in which a mandatory reviewer ran">${s.chain.review_rounds} review round${s.chain.review_rounds === 1 ? "" : "s"}</b>` : ""}${s.release ? ` · <span class="reltag" title="This session performed a release (gh release, git tag, release-please or /dual-review)">release${["security-reviewer", "test-reviewer"].every((m) => s.ran.includes(m)) ? " ✓ reviewed" : ""}</span>` : ""}</div>
        ${s.pipeline ? `<div class="pipe" aria-label="/develop progress">${s.pipeline.map((p) =>
          `<span class="stg ${esc(p.status)}" title="${esc(`${p.stage}: ${p.status}${p.at ? ` · ${hhmmss(p.at)}` : ""}`)}">${p.status === "done" ? "✓" : p.status === "active" ? "●" : p.status === "skipped" ? "–" : "○"} ${esc(p.stage)}</span>`).join('<span class="stg-sep">›</span>')}</div>` : ""}
        <div class="cur" title="${esc(cur)}">${esc(cur) || "&nbsp;"}</div>
        <div class="chips">${chips}</div>
        ${s.flagged ? `<div class="flagmsg">⚠ release without both reviewers</div>` : ""}
        ${canDismiss(s) ? `<div class="sessacts"><span class="small muted">quiet ${dur(STATE.now - s.last)} · closed or archived?</span>
          <button class="ghost tiny" data-dismiss="${esc(s.id)}" ${ME.paired ? "" : `disabled title="Pair this browser (menu) to dismiss"`}>${ARMED.sid === s.id && Date.now() < ARMED.until ? "Confirm dismiss" : "Dismiss"}</button></div>` : ""}</div>`;
    }
  }
  $("sessions").innerHTML = html;
  for (const el of $("sessions").querySelectorAll(".sess")) el.onclick = () => showDetails({ sid: el.dataset.sid });
  for (const b of $("sessions").querySelectorAll("[data-dismiss]")) b.onclick = (e) => { e.stopPropagation(); dismissSession(b); };
}

// Dismiss: a session the hooks never saw end (closed or archived in the desktop app). Two clicks, paired browsers only.
const ARMED = { sid: null, until: 0 };
const canDismiss = (s) => !s.ended && (s.state === "stale" || (s.state === "waiting" && STATE.now - s.last > 1800));
async function dismissSession(btn) {
  const sid = btn.dataset.dismiss;
  if (ARMED.sid !== sid || Date.now() > ARMED.until) {
    Object.assign(ARMED, { sid, until: Date.now() + 4000 });
    btn.textContent = "Confirm dismiss";
    setTimeout(() => { if (btn.isConnected && ARMED.sid === sid && Date.now() >= ARMED.until) btn.textContent = "Dismiss"; }, 4100);
    return;
  }
  ARMED.sid = null;
  btn.disabled = true;
  const r = await POST("/api/sessions/dismiss", { session: sid });
  btn.textContent = r.ok ? "Dismissed" : (r.data.error || "Failed");
}

// ---------- themed hover tooltip (graph, timeline, LLM card; native SVG <title> tooltips can't be styled) ----------
const TIP = Object.assign(document.createElement("div"), { className: "gtip", hidden: true });
TIP.setAttribute("role", "tooltip");
document.body.append(TIP);
function placeTip(e) {
  const r = TIP.getBoundingClientRect(), gap = 14;
  let x = e.clientX + gap, y = e.clientY + gap;
  if (x + r.width > innerWidth - 8) x = Math.max(8, e.clientX - r.width - gap);
  if (y + r.height > innerHeight - 8) y = Math.max(8, e.clientY - r.height - gap);
  TIP.style.transform = `translate(${Math.round(x)}px, ${Math.round(y)}px)`;
}
for (const id of ["graph", "timeline", "llmcard", "usagegraph", "tokbody", "rshead", "rsgate", "rsquota", "rslanes", "rslast", "tplist", "amap", "amfeed"]) {
  const box = $(id);
  box.addEventListener("pointerover", (e) => {
    const t = e.target.closest?.("[data-tip]");
    if (!t || e.pointerType === "touch") return;
    const [head, ...rest] = t.getAttribute("data-tip").split("\n");
    TIP.replaceChildren(Object.assign(document.createElement("b"), { textContent: head }),
      ...rest.filter(Boolean).map((line) => Object.assign(document.createElement("div"), { textContent: line })));
    TIP.dataset.owner = id;
    TIP.hidden = false;
    placeTip(e);
  });
  box.addEventListener("pointermove", (e) => {
    if (TIP.hidden) return;
    // a re-render can remove the hovered node without a pointerout: hide once the pointer is off any tip target
    if (!e.target.closest?.("[data-tip]") || !e.target.isConnected) { TIP.hidden = true; return; }
    placeTip(e);
  });
  box.addEventListener("pointerout", (e) => {
    const t = e.target.closest?.("[data-tip]");
    if (t && !t.contains(e.relatedTarget)) TIP.hidden = true;
  });
  box.addEventListener("pointerleave", () => { TIP.hidden = true; });
}
addEventListener("scroll", () => { TIP.hidden = true; }, true);

// ---------- animated interaction graph ----------
// The SVG is built once and updated in place, so transitions and flows keep running between updates.
const G = { root: null, layers: {}, nodes: new Map(), edges: new Map(), seen: new Map(), first: true, ids: 0 };
const MSG_TTL = 45; // seconds a SendMessage edge stays on the graph
const NW = 210, NH = 52, XG = 90, YG = 16, PAD = 16;

function graphInit() {
  if (G.root) return;
  const root = svg("svg", { role: "img", "aria-label": "Agent interaction graph" });
  const defs = svg("defs");
  for (const [id, cls] of [["ar", "arrow"], ["ar-active", "arrow active"], ["ar-msg", "arrow msg"], ["ar-local", "arrow local"], ["ar-handoff", "arrow handoff"]]) {
    const m = svg("marker", { id, viewBox: "0 0 10 10", refX: 9, refY: 5, markerWidth: 7, markerHeight: 7, orient: "auto-start-reverse" });
    m.append(svg("path", { d: "M0,0 L10,5 L0,10 z", class: cls }));
    defs.append(m);
  }
  root.append(defs);
  G.zoom = svg("g", { class: "zoom" });
  root.append(G.zoom);
  for (const k of ["edges", "packets", "nodes", "fx"]) { G.layers[k] = svg("g", { class: `layer-${k}` }); G.zoom.append(G.layers[k]); }
  $("graph").textContent = "";
  $("graph").append(root);
  G.root = root;
}

// Local Ollama agent runs started by a session's niu-local tool call, shown under that session.
function localKids() {
  const out = {};
  for (const s of STATE.sessions) {
    if (!s.local || !s.parent_session) continue;
    if (((s.ended && STATE.now - s.last > 600) || s.state === "stale") && !$("ended").checked) continue;
    (out[s.parent_session] ||= []).push(s);
  }
  return out;
}

function graphLayout(list, cw, ch) {
  let y = PAD; const nodes = [], edges = [], trees = [];
  const lk = localKids();
  for (const s of list) {
    const n0 = nodes.length, e0 = edges.length, y0 = y;
    const kids = {};
    for (const a of s.subagents) (kids[a.parent] ||= []).push(a);
    for (const l of lk[s.id] || []) (kids[s.id] ||= []).push({ localSession: l });
    // The session's own implement stretches, so the hand-off chain reads design → implement → review.
    const rounds = new Map((s.chain?.steps || []).filter((x) => x.round).map((x) => [x.id, x.round]));
    const hasAgents = (kids[s.id] || []).length > 0;
    if (hasAgents) for (const st of (s.chain?.steps || []).filter((x) => x.kind === "implement")) (kids[s.id] ||= []).push({ implStep: st });
    const startOf = (c) => c.localSession ? c.localSession.first : c.implStep ? c.implStep.start : c.start;
    if (kids[s.id]) kids[s.id].sort((a, b) => startOf(a) - startOf(b));
    // Steps of the session stair-step to the right in pipeline order: design, implement, local review, review.
    const IND = 24, STAGE = { designer: 0, implement: 1, implementer: 1, local: 2, "security-reviewer": 3, "test-reviewer": 3 };
    const place = (id, depth, info) => {
      const node = { id, col: depth, x: PAD + depth * (NW + XG) + (info.stage || 0) * IND, y, ...info };
      nodes.push(node);
      const children = kids[id] || [];
      if (!children.length) y += NH + YG;
      for (const c of children) {
        if (c.implStep) {
          const st = c.implStep, live = st.end == null;
          place(st.id, depth + 1, {
            kind: "step", sid: s.id, label: "Implement", stage: STAGE.implement, source: "session", running: live && s.state === "working", done: !live,
            sub: `${st.edits} edit${st.edits === 1 ? "" : "s"} by the session${live ? "" : ` · ${dur(Math.max(0, st.end - st.start))}`}`,
          });
          continue;
        }
        if (c.localSession) {
          const l = c.localSession, live = !l.ended;
          const child = place(l.id, depth + 1, {
            kind: "local", sid: l.id, label: sessName(l), source: "local", running: live && l.state === "working", done: !live,
            sub: `${l.local} · ${live ? (l.current ? `${l.current[0]}: ${l.current[1]}` : STATE_LABEL[l.state] || l.state) : `done in ${dur(l.last - l.first)}`}`,
            localModel: l.local, stage: id === s.id ? STAGE.local : 0,
          });
          edges.push({ id: `${id}>${l.id}`, a: node, b: child, label: l.query || l.task, active: live, kind: "local" });
          continue;
        }
        const child = place(c.id, depth + 1, {
          kind: "sub", sid: s.id, aid: c.id, label: c.type, source: c.source, running: !c.stop, done: !!c.stop,
          stage: id === s.id ? (STAGE[c.type] ?? 0) : 0, model: c.model, modelSrc: c.model_src,
          sub: (rounds.get(c.id) ? `round ${rounds.get(c.id)} · ` : "") + (c.stop ? `done in ${dur(c.stop - c.start)}` : c.current ? `${c.current[0]}: ${c.current[1]}` : `running ${dur(STATE.now - c.start)}`),
        });
        edges.push({ id: `${id}>${c.id}`, a: node, b: child, label: c.task, active: !c.stop, kind: "spawn" });
      }
      return node;
    };
    const root = place(s.id, 0, {
      kind: "session", sid: s.id, label: sessName(s), source: s.local ? "local" : "session", running: s.state === "working", waiting: s.state === "waiting",
      sub: sessSub(s), localModel: s.local || null,
    });
    // Hand-offs: the session passes each finished step's result to the next step(s).
    for (const h of s.chain?.handoffs || []) {
      const a = nodes.find((n) => n.id === h.from), b = nodes.find((n) => n.id === h.to);
      if (a && b) edges.push({ id: `${h.from}=>${h.to}`, a, b, label: h.label, kind: "handoff" });
    }
    // A message is a moment, not a state: show its edge and recipient only for MSG_TTL s after it was sent
    // (the state keeps the whole list so the flow-packet bookkeeping below can index into it).
    s.messages.filter((m) => STATE.now - m.ts < MSG_TTL).forEach((m, i) => {
      const from = nodes.find((n) => n.id === m.from) || root;
      const ext = { id: `msg:${s.id}:${m.to}`, x: from.x + NW + XG, y: from.y + i * 4, kind: "ext", label: `→ ${m.to}`, sub: "message recipient", source: "msg" };
      if (!nodes.some((n) => n.id === ext.id)) nodes.push(ext);
      edges.push({ id: `${from.id}>${ext.id}`, a: from, b: nodes.find((n) => n.id === ext.id), kind: "msg" });
    });
    y += YG;
    const tn = nodes.slice(n0), te = edges.slice(e0);
    trees.push({ nodes: tn, y0, h: y - y0, w: Math.max(...tn.map((n) => n.x + NW)) + (te.some((e) => e.kind === "handoff") ? HB + 120 : 0) });
  }
  // Pack session trees into columns to use the panel's width: pick the column count with the largest zoom.
  const GAP = 40;
  const pack = (c) => {
    const cols = Array.from({ length: c }, () => ({ h: PAD, w: 0, trees: [] }));
    for (const t of trees) {
      const col = cols.reduce((a, b) => (b.h < a.h ? b : a));
      col.trees.push(t); col.h += t.h; col.w = Math.max(col.w, t.w);
    }
    const used = cols.filter((c2) => c2.trees.length);
    const W = used.reduce((a, c2) => a + c2.w, 0) + GAP * (used.length - 1) + PAD;
    const H = Math.max(...used.map((c2) => c2.h)) + PAD;
    return { cols: used, W, H, scale: Math.min(1.15, (cw || W) / W, (ch || H) / H) };
  };
  let best = pack(1);
  for (let c = 2; c <= Math.min(4, trees.length); c++) { const p = pack(c); if (p.scale > best.scale * 1.05) best = p; }
  let x0 = 0;
  for (const col of best.cols) {
    let cy = PAD;
    for (const t of col.trees) {
      const dy = cy - t.y0;
      for (const n of t.nodes) { n.x += x0; n.y += dy; }
      cy += t.h;
    }
    x0 += col.w + GAP;
  }
  return { nodes, edges, W: Math.max(best.W, 400), H: best.H };
}

const edgePath = (a, b) => {
  const x1 = a.x + NW, y1 = a.y + NH / 2, x2 = b.x - 4, y2 = b.y + NH / 2, mx = (x1 + x2) / 2;
  return `M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`;
};
// Hand-off between steps stacked in the same column: a bracket on the right-hand side, from one step to the next.
const HB = 46;
const handoffPath = (a, b) => {
  if (a.col !== b.col) return edgePath(a, b);
  const xa = a.x + NW, xb = b.x + NW, xr = Math.max(xa, xb) + HB, y1 = a.y + NH * 0.66, y2 = b.y + NH * 0.34;
  return `M${xa},${y1} C${xr},${y1} ${xr},${y2} ${xb + 4},${y2}`;
};

// Slide a node to its new position (attribute transform, so it scales with the zoom group).
function moveNode(g, x, y) {
  const from = g._pos || { x, y };
  g._pos = { x, y };
  if ((from.x === x && from.y === y) || reducedMotion()) { g.setAttribute("transform", `translate(${x},${y})`); return; }
  const t0 = performance.now(), D = 600;
  const step = (t) => {
    const k = Math.min(1, (t - t0) / D), e = 1 - Math.pow(1 - k, 3);
    g.setAttribute("transform", `translate(${from.x + (x - from.x) * e},${from.y + (y - from.y) * e})`);
    if (k < 1 && g._pos.x === x && g._pos.y === y) requestAnimationFrame(step);
  };
  requestAnimationFrame(step);
}

function graphNode(n) {
  let g = G.nodes.get(n.id);
  if (!g) {
    g = svg("g", { class: "gnode enter" });
    g.setAttribute("transform", `translate(${n.x},${n.y})`); g._pos = { x: n.x, y: n.y };
    g.append(svg("rect", { class: "halo", width: NW, height: NH, rx: 10 }));
    g.append(svg("rect", { class: "box", width: NW, height: NH, rx: 10 }));
    g.append(svg("text", { class: "badge", x: 12, y: 16 }));
    g.append(svg("text", { class: "name", x: 12, y: 32 }));
    g.append(svg("text", { class: "lbl sub", x: 12, y: 46 }));
    g.append(svg("rect", { class: "bar", x: 10, y: NH - 4, width: NW - 20, height: 2, rx: 1 }));
    g.addEventListener("click", () => g.dataset.sid && showDetails({ sid: g.dataset.sid, aid: g.dataset.aid || undefined }));
    G.layers.nodes.append(g);
    G.nodes.set(n.id, g);
    setTimeout(() => g.classList.remove("enter"), 600);
  }
  moveNode(g, n.x, n.y);
  g.classList.toggle("session", n.kind === "session");
  g.classList.toggle("localnode", !!n.localModel);
  g.classList.toggle("running", !!n.running);
  g.classList.toggle("waiting", !!n.waiting);
  g.classList.toggle("done", !!n.done);
  g.classList.remove("leave");
  g.dataset.sid = n.sid || ""; g.dataset.aid = n.aid || "";
  const [badge, name, sub, bar] = [g.querySelector(".badge"), g.querySelector(".name"), g.querySelector(".sub"), g.querySelector(".bar")];
  badge.textContent = n.kind === "session" ? "session" : n.kind === "ext" ? "message" : n.kind === "step" ? "session step" : n.source;
  badge.setAttribute("class", `badge src-${n.kind === "sub" ? n.source : "session"}`);
  name.textContent = trunc(n.label, 26);
  sub.textContent = trunc(n.sub, 32);
  bar.classList.toggle("anim", !!n.running && n.kind === "sub");
  let mdl = g.querySelector(".mdl");
  if (!mdl) { mdl = svg("text", { class: "lbl mdl", x: NW - 10, y: 16, "text-anchor": "end" }); g.append(mdl); }
  const mshort = n.model ? shortModel(n.model) : "";
  mdl.textContent = trunc(mshort, 18);
  mdl.classList.toggle("guess", !!n.model && n.modelSrc !== "actual");
  g.setAttribute("data-tip", `${n.label}\n${n.sub}`);
  n.el = g;
}

function graphEdge(e) {
  let rec = G.edges.get(e.id);
  if (!rec) {
    const pid = `ge${++G.ids}`;
    const base = svg("path", { id: pid, class: "gedge" });
    const flow = svg("path", { class: "gflow" });
    const label = svg("text", { class: "elabel" });
    G.layers.edges.append(base, flow, label);
    rec = { pid, base, flow, label };
    G.edges.set(e.id, rec);
  }
  const hand = e.kind === "handoff";
  const d = hand ? handoffPath(e.a, e.b) : edgePath(e.a, e.b);
  rec.base.setAttribute("d", d); rec.flow.setAttribute("d", d);
  rec.base.setAttribute("class", `gedge ${e.kind === "msg" ? "msg" : ""} ${e.kind === "local" ? "local" : ""} ${hand ? "handoff" : ""} ${e.active ? "active" : ""}`);
  rec.flow.setAttribute("class", `gflow ${e.active && e.kind !== "msg" ? "active" : ""} ${e.kind === "local" ? "local" : ""}`);
  rec.base.setAttribute("marker-end", `url(#${e.kind === "msg" ? "ar-msg" : e.kind === "local" ? "ar-local" : hand ? "ar-handoff" : e.active ? "ar-active" : "ar"})`);
  rec.label.textContent = trunc(e.label, 22);
  rec.label.setAttribute("class", `elabel ${hand ? "hlabel" : ""}`);
  if (hand) {
    rec.label.setAttribute("x", Math.max(e.a.x, e.b.x) + NW + HB + 6);
    rec.label.setAttribute("y", (e.a.y + e.b.y) / 2 + NH / 2 + 4);
  } else {
    rec.label.setAttribute("x", e.a.x + NW + 8);
    rec.label.setAttribute("y", e.b.y + NH / 2 - 7);
  }
  rec.alive = true;
  return rec;
}

function packet(pathId, cls, { reverse = false, delay = 0, dur = 0.95, layer = G.layers.packets } = {}) {
  if (reducedMotion()) return;
  for (const [r, extra] of [[7, " pk-trail"], [4, ""]]) {
    const c = svg("circle", { r, class: `pk pk-${cls}${extra}`, cx: 0, cy: 0, opacity: 0 });
    const m = svg("animateMotion", { dur: `${dur}s`, begin: "indefinite", fill: "freeze", calcMode: "spline",
      keyPoints: reverse ? "1;0" : "0;1", keyTimes: "0;1", keySplines: ".4 0 .2 1" });
    m.append(svg("mpath", { href: `#${pathId}` }));
    c.append(m);
    layer.append(c);
    setTimeout(() => { c.setAttribute("opacity", 1); try { m.beginElement(); } catch { /* SMIL unsupported */ } }, delay);
    setTimeout(() => c.remove(), delay + dur * 1000 + 80);
  }
}

function burst(n, cls, text, { layer = G.layers.fx, w = NW, h = NH } = {}) {
  if (reducedMotion() || !n) return;
  const ring = svg("rect", { class: `ripple rp-${cls}`, x: n.x, y: n.y, width: w, height: h, rx: 10 });
  layer.append(ring);
  setTimeout(() => ring.remove(), 950);
  if (text) {
    const t = svg("text", { class: `toolpop pk-${cls}`, x: n.x + w - 8, y: n.y - 4, "text-anchor": "end" }, text);
    layer.append(t);
    setTimeout(() => t.remove(), 1450);
  }
}

// Mouse-wheel zoom on top of the auto-fit scale (G.fit); G.uz is the viewer's factor, kept per browser.
// Shift+wheel scrolls sideways, dragging empty space pans; double-click goes back to fit.
G.uz = (() => { const v = Number(store.get("niu.graph.zoom", 1)); return Number.isFinite(v) && v > 0 ? v : 1; })();
function graphZoomApply() {
  if (!G.root || !G.fit) return;
  G.uz = Math.max(0.3 / G.fit, Math.min(3 / G.fit, G.uz));
  const s = G.fit * G.uz;
  G.root.setAttribute("width", Math.round(G.W * s)); G.root.setAttribute("height", Math.round(G.H * s));
  G.zoom.setAttribute("transform", `scale(${s.toFixed(4)})`);
  const box = $("graph");
  box.classList.toggle("pannable", box.scrollWidth > box.clientWidth + 1 || box.scrollHeight > box.clientHeight + 1);
}
// Drag empty space with the left button to pan (nodes keep their click).
const GPAN = { id: null, x: 0, y: 0, sl: 0, st: 0, moved: false };
$("graph").addEventListener("pointerdown", (e) => {
  const box = $("graph");
  if (!G.root || e.button !== 0 || e.pointerType === "touch" || e.target.closest?.(".gnode") || !box.classList.contains("pannable")) return;
  Object.assign(GPAN, { id: e.pointerId, x: e.clientX, y: e.clientY, sl: box.scrollLeft, st: box.scrollTop, moved: false });
  box.setPointerCapture?.(e.pointerId);
  box.classList.add("panning");
  e.preventDefault();
});
$("graph").addEventListener("pointermove", (e) => {
  if (GPAN.id !== e.pointerId) return;
  const box = $("graph"), dx = e.clientX - GPAN.x, dy = e.clientY - GPAN.y;
  if (Math.abs(dx) + Math.abs(dy) > 3) GPAN.moved = true;
  box.scrollLeft = GPAN.sl - dx; box.scrollTop = GPAN.st - dy;
});
const gpanEnd = (e) => {
  if (GPAN.id !== e.pointerId) return;
  GPAN.id = null; $("graph").classList.remove("panning");
};
$("graph").addEventListener("pointerup", gpanEnd);
$("graph").addEventListener("pointercancel", gpanEnd);
function graphZoomTo(uz, cx, cy) {
  if (!G.root || !G.fit) return;
  const box = $("graph"), r = G.root.getBoundingClientRect(), s0 = G.fit * G.uz;
  const px = (cx - r.left) / s0, py = (cy - r.top) / s0;
  G.uz = uz; graphZoomApply(); store.set("niu.graph.zoom", G.uz);
  const r2 = G.root.getBoundingClientRect(), s = G.fit * G.uz;
  box.scrollLeft += r2.left + px * s - cx; box.scrollTop += r2.top + py * s - cy;
}
$("graph").addEventListener("wheel", (e) => {
  if (!G.root || e.shiftKey) return;
  e.preventDefault();
  const dy = Math.max(-120, Math.min(120, e.deltaY * (e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? 400 : 1)));
  graphZoomTo(G.uz * Math.exp(-dy * 0.0015), e.clientX, e.clientY);
}, { passive: false });
const graphZoomReset = () => { G.uz = 1; store.set("niu.graph.zoom", 1); graphZoomApply(); };
$("graph").addEventListener("dblclick", (e) => { if (G.root && !GPAN.moved && !e.target.closest(".gnode")) graphZoomReset(); });

function renderGraph() {
  // Local agent runs with a visible caller are drawn under that caller, not as separate roots.
  const shown = new Set(visible().map((s) => s.id));
  const list = visible().filter((s) => !(s.local && s.parent_session && shown.has(s.parent_session))).slice(0, 10);
  if (!list.length) {
    G.root = null; G.nodes.clear(); G.edges.clear(); G.seen.clear(); G.first = true;
    $("graph").innerHTML = `<div class="empty">&nbsp;&nbsp;No agent activity yet. Start a Claude session in the workspace.</div>`;
    return;
  }
  graphInit();
  const gbox = $("graph");
  const { nodes, edges, W, H } = graphLayout(list, (gbox.clientWidth || 0) - 12, (gbox.clientHeight || 0) - 12);
  const box = $("graph"), cw = (box.clientWidth || W) - 12, ch = (box.clientHeight || H) - 12;
  G.fit = Math.max(0.55, Math.min(1.15, cw / W, ch / H)); G.W = W; G.H = H;
  graphZoomApply();
  $("graph").classList.toggle("calm", reducedMotion());

  const live = new Set();
  for (const n of nodes) { graphNode(n); live.add(n.id); }
  for (const [id, g] of G.nodes) if (!live.has(id)) {
    g.classList.add("leave"); G.nodes.delete(id); setTimeout(() => g.remove(), 450);
  }
  for (const rec of G.edges.values()) rec.alive = false;
  const recs = new Map(edges.map((e) => [e.id, graphEdge(e)]));
  for (const [id, rec] of G.edges) if (!rec.alive) { rec.base.remove(); rec.flow.remove(); rec.label.remove(); G.edges.delete(id); }

  // Data flows: animate what changed since the last update (nothing on first paint).
  const byId = new Map(nodes.map((n) => [n.id, n]));
  for (const s of list) {
    const key = `s:${s.id}`, prev = G.seen.get(key), toolsNow = s.tools.length, msgsNow = s.messages.length;
    if (prev && !G.first) {
      const fresh = s.tools.slice(prev.tools, toolsNow).slice(-3);
      fresh.forEach(([, tool], i) => setTimeout(() => burst(byId.get(s.id), TOOL_CLASS(tool), tool), i * 220));
      s.messages.slice(prev.msgs).forEach((m, i) => {
        const rec = recs.get(`${(byId.get(m.from) ? m.from : s.id)}>msg:${s.id}:${m.to}`);
        if (rec) packet(rec.pid, "msg", { delay: i * 200, dur: 1.2 });
      });
    }
    G.seen.set(key, { tools: toolsNow, msgs: msgsNow });
    for (const a of s.subagents) {
      const k = `a:${a.id}`, p = G.seen.get(k), rec = recs.get(`${a.parent}>${a.id}`), now = a.tools.length;
      if (!G.first && rec) {
        if (!p) packet(rec.pid, "spawn", { dur: 1.1 });
        else {
          a.tools.slice(p.tools, now).slice(-3).forEach(([, tool], i) => {
            packet(rec.pid, TOOL_CLASS(tool), { delay: i * 180 });
            setTimeout(() => burst(byId.get(a.id), TOOL_CLASS(tool), tool), i * 180 + 900);
          });
          if (!p.stopped && a.stop) packet(rec.pid, "result", { reverse: true, dur: 1.1 });
        }
      }
      G.seen.set(k, { tools: now, stopped: !!a.stop });
    }
  }
  // Local Ollama agents: request out, tool work, answer back
  for (const kids of Object.values(localKids())) for (const l of kids) {
    const k = `l:${l.id}`, p = G.seen.get(k), rec = recs.get(`${l.parent_session}>${l.id}`), now = l.tools.length;
    if (!G.first && rec) {
      if (!p) packet(rec.pid, "local", { dur: 1.1 });
      else {
        l.tools.slice(p.tools, now).slice(-3).forEach(([, tool], i) => {
          packet(rec.pid, "local", { delay: i * 180 });
          setTimeout(() => burst(byId.get(l.id), "local", tool), i * 180 + 900);
        });
        if (!p.ended && l.ended) packet(rec.pid, "result", { reverse: true, dur: 1.1 });
      }
    }
    G.seen.set(k, { tools: now, ended: !!l.ended });
  }
  // A new hand-off: the finished step's result travels to the next step.
  edges.filter((e) => e.kind === "handoff").forEach((e, i) => {
    const k = `h:${e.id}`, rec = recs.get(e.id);
    if (!G.seen.has(k) && !G.first && rec) packet(rec.pid, "handoff", { delay: i * 150, dur: 1.2 });
    G.seen.set(k, true);
  });
  G.first = false;
}

// ---------- "needs you" actions ----------
function indexActions() {
  ACTIONS = STATE.actions || [];
  ACT_BY = { session: new Map(), pr: new Map() };
  for (const a of ACTIONS) {
    const map = a.target.session ? ACT_BY.session : ACT_BY.pr, key = a.target.session || a.target.pr;
    const cur = map.get(key);
    if (!cur || a.priority > cur.priority) map.set(key, a);
  }
}

function setFavicon(n, high) {
  const color = n ? (high ? "#c2281e" : "#d08a00") : "#2563c9";
  const label = n ? `<text x='16' y='21.5' font-size='16' font-family='sans-serif' font-weight='700' text-anchor='middle' fill='white'>${n > 9 ? "9+" : n}</text>` : "";
  const svgIcon = `<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><circle cx='16' cy='16' r='14' fill='${color}'/>${label}</svg>`;
  $("favicon").setAttribute("href", "data:image/svg+xml," + encodeURIComponent(svgIcon));
}

function renderActions() {
  const n = ACTIONS.length, high = ACTIONS.some((a) => a.priority >= 3);
  const badge = $("bellcount"), prev = badge.textContent;
  badge.hidden = !n; badge.textContent = n > 99 ? "99+" : String(n);
  badge.className = `bellcount ${high ? "high" : ""} ${prev !== badge.textContent && n ? "bump" : ""}`;
  $("bellbtn").className = `icon-btn bell ${n ? "has" : ""} ${high ? "high" : ""}`;
  $("bellbtn").title = n ? `${n} item(s) need you` : "Nothing needs you";
  document.title = `${n ? `(${n}) ` : ""}${HOST()} · niu-heimar agents`;
  setFavicon(n, high);

  const sessN = ACT_BY.session.size, prN = ACT_BY.pr.size;
  const sessHigh = [...ACT_BY.session.values()].some((a) => a.priority >= 3), prHigh = [...ACT_BY.pr.values()].some((a) => a.priority >= 3);
  $("sessneeds").hidden = !sessN; $("sessneeds").textContent = `${sessN} need${sessN === 1 ? "s" : ""} you`; $("sessneeds").className = `needs ${sessHigh ? "high" : ""}`;
  $("prneeds").hidden = !prN; $("prneeds").textContent = `${prN} need${prN === 1 ? "s" : ""} you`; $("prneeds").className = `needs ${prHigh ? "high" : ""}`;

  const fresh = SEEN_ACTIONS ? ACTIONS.filter((a) => !SEEN_ACTIONS.has(a.id)) : [];
  $("needslist").innerHTML = n ? ACTIONS.map((a) => {
    const isPR = !!a.target.pr, pr = isPR && PRS ? PRS.repos.flatMap((r) => r.prs).find((p) => p.ref === a.target.pr) : null;
    const merge = a.kind === "pr-ready" && pr && pr.green ? `<button class="danger" data-amerge="${esc(a.target.pr)}" ${ME.paired ? "" : `disabled title="Pair this browser (menu) to merge"`}>Merge</button>` : "";
    const open = isPR ? `<button class="ghost" data-aopen="${esc(a.target.url || "")}">${a.kind === "pr-blocked" ? "Merge on GitHub" : "Open"}</button>` : "";
    return `<div class="act p${a.priority} ${fresh.some((f) => f.id === a.id) ? "new" : ""}" data-aid="${esc(a.id)}">
      <span class="ico"></span><span class="t">${esc(a.title)}</span>
      <span class="b"><button class="ghost" data-ago="${esc(a.id)}">Show</button>${open}${merge}</span>
      <span class="d" title="${esc(a.detail)}">${esc(a.detail)}</span></div>`;
  }).join("") : `<div class="none">Nothing needs you right now.</div>`;
  $("needscount").hidden = !n; $("needscount").textContent = String(n); $("needscount").className = `needs ${high ? "high" : ""}`;
  for (const box of [$("needslist")]) {
    for (const b of box.querySelectorAll("[data-ago]")) b.onclick = () => { if (VIEW !== "agents") setView("agents"); setTimeout(() => jumpTo(ACTIONS.find((a) => a.id === b.dataset.ago)), 60); };
    for (const b of box.querySelectorAll("[data-aopen]")) b.onclick = () => b.dataset.aopen && window.open(b.dataset.aopen, "_blank", "noopener");
    for (const b of box.querySelectorAll("[data-amerge]")) b.onclick = () => openMerge(b.dataset.amerge);
  }

  if (SEEN_ACTIONS && fresh.length) notify(fresh);
  SEEN_ACTIONS = new Set(ACTIONS.map((a) => a.id));
}

function jumpTo(a) {
  if (!a) return;
  if (a.target.pr && VIEW !== "events") setView("events");  // the PR panel lives on the Events tab
  const sel = a.target.session ? `.sess[data-sid="${CSS.escape(a.target.session)}"]` : `.pr[data-ref="${CSS.escape(a.target.pr)}"]`;
  const el = document.querySelector(sel);
  if (el) {
    el.scrollIntoView({ behavior: reducedMotion() ? "auto" : "smooth", block: "center" });
    el.classList.remove("highlight"); void el.offsetWidth; el.classList.add("highlight");
  }
  if (a.target.session) showDetails({ sid: a.target.session });
}

function notify(fresh) {
  if (!$("notify").checked || typeof Notification === "undefined" || Notification.permission !== "granted") return;
  for (const a of fresh.slice(0, 3)) {
    try {
      const n = new Notification(`${HOST()}: ${a.title}`, { body: a.detail, tag: a.id, silent: a.priority < 3 });
      n.onclick = () => { window.focus(); jumpTo(a); n.close(); };
    } catch { /* notifications unavailable */ }
  }
}

async function setNotify(on) {
  const msg = $("notifymsg");
  if (!on) { store.set("niu.notify", false); msg.textContent = ""; return; }
  if (typeof Notification === "undefined" || !window.isSecureContext) {
    $("notify").checked = false; msg.textContent = "Notifications need https (Tailscale URL) or localhost."; return;
  }
  const perm = Notification.permission === "default" ? await Notification.requestPermission() : Notification.permission;
  if (perm !== "granted") { $("notify").checked = false; msg.textContent = "Notifications are blocked for this site in your browser settings."; return; }
  store.set("niu.notify", true); msg.textContent = "You'll be notified when something new needs you.";
}

// ---------- timeline ----------
function renderTimeline() {
  if (TIP.dataset.owner === "timeline") TIP.hidden = true;  // the timeline is rebuilt from scratch
  const box = $("timeline"); box.textContent = "";
  const list = visible().slice(0, 8);
  if (!list.length) { box.innerHTML = `<div class="empty">Nothing on the timeline yet.</div>`; return; }
  const lanes = [];
  for (const s of list) {
    lanes.push({ label: projLabel(s), start: s.first, stop: s.ended ? s.last : null, tools: s.tools, waits: s.waits, session: true });
    for (const a of s.subagents) lanes.push({ label: a.type, start: a.start, stop: a.stop, tools: a.tools, model: a.model ? shortModel(a.model) : "", modelSrc: a.model_src });
  }
  const t0 = Math.max(Math.min(...lanes.map((l) => l.start)), STATE.now - 6 * 3600), t1 = STATE.now;
  const avail = (box.clientHeight || 300) - 22;
  const LH = Math.max(14, Math.min(24, Math.floor((avail - 26) / Math.max(1, lanes.length))));
  const LW = 170, W = Math.max(640, (box.clientWidth || 900) - 20), H = lanes.length * LH + 26;
  const x = (t) => LW + ((Math.max(t, t0) - t0) / Math.max(1, t1 - t0)) * (W - LW - 10);
  const root = svg("svg", { width: W, height: H, viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "Agent timeline" });
  for (let i = 0; i <= 4; i++) {
    const t = t0 + ((t1 - t0) * i) / 4, xx = x(t);
    root.append(svg("line", { x1: xx, x2: xx, y1: 0, y2: H - 20, class: i === 4 ? "nowline" : "axis" }));
    root.append(svg("text", { x: xx, y: H - 6, class: "lbl", "text-anchor": i === 4 ? "end" : "middle" }, i === 4 ? "now" : hhmmss(t)));
  }
  lanes.forEach((l, i) => {
    const y = i * LH + 2;
    root.append(svg("rect", { x: LW, y, width: W - LW - 10, height: Math.max(6, LH - 4), class: "lane", rx: 3 }));
    const fs = LH < 18 ? 10.5 : 12;
    const label = svg("text", { x: l.session ? 0 : 12, y: y + Math.min(14, LH - 4), class: l.session ? "" : "muted", "font-size": fs });
    // Agent lanes: "designer [opus-5-5]". The model is in italics when it's a guess (not read from the agent's transcript).
    const modelTag = l.model ? `${l.model}${l.modelSrc && l.modelSrc !== "actual" ? ` (${l.modelSrc === "definition" ? "from agent definition" : l.modelSrc})` : ""}` : "";
    if (l.session) label.textContent = trunc(l.label, 22);
    else {
      const room = l.model ? Math.max(6, 25 - l.model.length - 3) : 20;
      label.append(svg("tspan", {}, trunc(l.label, room)));
      if (l.model) label.append(svg("tspan", { class: `tlmodel${l.modelSrc && l.modelSrc !== "actual" ? " guess" : ""}`, "font-size": fs - 1.5 }, ` [${trunc(l.model, 14)}]`));
      label.setAttribute("data-tip", [l.label, modelTag && `model: ${modelTag}`].filter(Boolean).join("\n"));
    }
    root.append(label);
    if (!l.session) {
      const bar = svg("rect", { x: x(l.start), y: y + 1, width: Math.max(3, x(l.stop || t1) - x(l.start)), height: Math.max(3, LH - 7), rx: 2, class: `bar-sub ${l.stop ? "" : "running"}` });
      bar.setAttribute("data-tip", [l.label, modelTag && `model: ${modelTag}`, `${hhmmss(l.start)} → ${l.stop ? hhmmss(l.stop) : "running"}`].filter(Boolean).join("\n"));
      root.append(bar);
    }
    for (const [ts, tool] of l.tools || []) {
      if (ts < t0) continue;
      const tick = svg("rect", { x: x(ts) - 1, y: y + 1, width: 2.5, height: Math.max(4, LH - 7), class: `tk-${TOOL_CLASS(tool)}` });
      tick.setAttribute("data-tip", `${tool}\n${hhmmss(ts)}`);
      root.append(tick);
    }
    for (const ts of l.waits || []) if (ts >= t0) root.append(svg("circle", { cx: x(ts), cy: y + (LH - 4) / 2, r: Math.min(4, LH / 3), class: "wait" }));
  });
  box.append(root);
}

// ---------- PRs ----------
// Yellow dot on the Events tab while an open PR's checks are still running (none failing yet).
function renderEventsDot(repos) {
  const n = repos.reduce((k, r) => k + r.prs.filter((p) => p.checks && p.checks.pending > 0 && !p.checks.failed).length, 0);
  const dot = $("evdot");
  dot.hidden = !n;
  dot.setAttribute("aria-label", `${n} pull request${n === 1 ? "" : "s"} with checks running`);
}

function renderPRs() {
  if (!PRS) { $("prs").innerHTML = `<div class="empty">Loading…</div>`; $("evdot").hidden = true; return; }
  const dom = $("domain").value;
  const repos = PRS.repos.filter((r) => !dom || r.label === dom || r.label.startsWith(dom + "/"));
  renderEventsDot(repos);
  let html = PRS.error ? `<div class="msg err">${esc(PRS.error)}</div>` : "";
  if (!repos.some((r) => r.prs.length || r.error)) html += `<div class="empty">No open pull requests.</div>`;
  for (const r of repos) {
    if (!r.prs.length && !r.error) continue;
    html += `<div class="repo"><div class="repo-h">${esc(r.repo)} · ${esc(dl(r.label))}</div>`;
    if (r.error) html += `<div class="msg err">${esc(r.error)}</div>`;
    // Release-please PRs go last: they collect the merged changes until you cut a version.
    for (const p of [...r.prs.filter((x) => !x.release), ...r.prs.filter((x) => x.release)]) {
      const c = p.checks;
      const badge = c.failed ? `<span class="badge b-bad">✗ ${c.failed} failing</span>` : c.pending ? `<span class="badge b-warn">… ${c.pending} pending</span>`
        : `<span class="badge b-ok">✓ ${c.passed} passed</span>`;
      const can = p.green && ME.paired;
      const why = !p.green ? p.reason : !ME.paired ? "Pair this browser (menu) to merge" : p.release ? "Merge to cut this release" : "Squash-merge";
      const status = p.needs_scope ? "blocked: gh token scope" : p.green ? "ready" : p.reason;
      const need = ACT_BY.pr.get(p.ref);
      html += `<div class="pr ${need ? "needs-you" : ""} ${p.release ? "release" : ""}" data-ref="${esc(p.ref)}"><div class="t" title="${esc(p.title)}">${p.release ? `<span class="reltag" title="release-please release PR: merging it tags the version and publishes the release. Leave it open to batch more changes.">release</span> ` : ""}#${p.number} ${esc(p.title)} ${need ? `<span class="needtag ${need.priority >= 3 ? "" : "mid"}">${esc(need.kind === "pr-ready" ? "ready" : "needs you")}</span>` : ""}</div>
        <div class="acts"><button class="ghost" data-open="${esc(p.url)}">${p.needs_scope ? "Merge on GitHub" : "Open"}</button>
        <button class="danger" data-merge="${esc(p.ref)}" ${can ? "" : "disabled"} title="${esc(why)}">Merge</button></div>
        <div class="meta">${badge}<span>${esc(status)}</span><span>${esc(p.author || "")}</span><span class="mono">${esc(p.branch || "")}</span></div>
        ${p.needs_scope ? `<div class="hint">This PR edits workflow files, and the host's <code>gh</code> token lacks the <code>workflow</code> scope. Run <code>gh auth refresh -h github.com -s workflow</code> on ${esc(HOST())}, or merge it on GitHub.</div>` : ""}</div>`;
    }
    html += `</div>`;
  }
  html += `<div class="small muted">updated ${PRS.fetched ? dur(Date.now() / 1000 - PRS.fetched) + " ago" : "never"}${PRS.refreshing ? " · refreshing…" : ""}</div>`;
  $("prs").innerHTML = html;
  for (const b of $("prs").querySelectorAll("[data-open]")) b.onclick = () => window.open(b.dataset.open, "_blank", "noopener");
  for (const b of $("prs").querySelectorAll("[data-merge]")) b.onclick = () => openMerge(b.dataset.merge);
}

function renderToday() {
  const t = STATE.today;
  const top = t.runs.slice(0, 3).map(([k, v]) => `${k} ${v}`).join(", ");
  $("today").innerHTML = `<span title="Sessions active today"><b>${t.sessions}</b> today</span>` +
    `<span title="${esc(top || "no subagent runs yet")}"><b>${t.subagent_runs}</b> runs</span>` +
    `<span title="Editing sessions that ran both mandatory reviewers"><b>${t.reviewed}/${t.edited}</b> reviewed</span>` +
    (t.flagged_projects.length ? `<span class="bad" title="${esc("Release sessions without both reviewers: " + t.flagged_projects.map((x) => x.replace("(workspace)", HOST())).join(", "))}">⚠ <b>${t.flagged_projects.length}</b> unreviewed</span>` : "");
}

// Session-start events carry JSON (title, model, local agent); show it as words.
function feedSummary(e) {
  const s = e.summary || "";
  if (!s.startsWith("{")) return s;
  try {
    const j = JSON.parse(s);
    if (j.local) return `local agent ${j.local}${j.task ? ` (${j.task})` : ""}`;
    return [j.title && `“${j.title}”`, j.model && shortModel(j.model), j.source].filter(Boolean).join(" · ");
  } catch { return s; }
}

function renderFeed() {
  const newest = STATE.feed[0]?.ts || 0;
  const names = new Map(STATE.sessions.map((x) => [x.id, sessName(x)]));
  const wide = VIEW === "events";
  $("feedsub").textContent = wide ? `${STATE.feed.length} most recent events` : "";
  $("feed").innerHTML = STATE.feed.map((e) => {
    const who = e.agent_id ? e.agent_type : "session";
    const sname = names.get(e.session_id) || (e.session_id === "web" ? "dashboard" : `session ${String(e.session_id || "?").slice(0, 4)}`);
    const detail = [e.tool, feedSummary(e)].filter(Boolean).join(" ");
    const cls = LAST_FEED_TS && e.ts > LAST_FEED_TS ? "new" : "";
    if (wide) {
      return `<div class="${cls}" title="${esc(detail)}"><span class="muted">${hhmmss(e.ts)}</span><span class="d">${esc(dl(e.domain))}</span>` +
        `<span>${esc(sname)}</span><span>${esc(who)}</span><span class="muted">${esc(e.event)}</span><span>${esc(detail)}</span></div>`;
    }
    return `<div class="${cls}" title="${esc(detail)}"><span class="muted">${hhmmss(e.ts)}</span> <span class="d">${esc(dl(e.domain))}</span> ${esc(e.agent_id ? e.agent_type : sname)} <span class="muted">${esc(e.event)}</span> ${esc(detail)}</div>`;
  }).join("") || `<div class="empty">No events yet.</div>`;
  LAST_FEED_TS = newest;
}

// ---------- session agent map ----------
// All agents a session could use (from /api/agents), drawn as dashed boxes; the ones that ran are solid and the running
// ones pulse. The recent-activity list below maps each row to nodes and edges. Everything is built with h() / svg().
const AW = 120, AH = 48, AGX = 10, ALW = 104, ALH = 38, AMW = 760, APAD = 14;
const AM = { sid: null, cache: new Map(), failed: null, loading: null, nodes: new Map(), edges: new Map(), seen: null, first: true,
  sel: null, focus: null, rows: [], ids: 0, root: null, pk: null, fx: null, note: null, model: null, selObj: null };
const AM_FLOW = { designer: "develop", implementer: "develop", "security-reviewer": "dual-review", "test-reviewer": "dual-review" };
const AM_BUILTIN = ["general-purpose", "Explore", "Plan"];
const AM_LOCAL_TASK = { search: "local_search", diff: "local_diff_review", ask: "local_ask" };
const amCluster = (type) => AM_FLOW[type] || "agent";
const amLocalTool = (l) => AM_LOCAL_TASK[l.task] || (l.parent_tool && l.parent_tool.startsWith("local_") ? l.parent_tool : AM_LOCAL_TASK[l.parent_tool]) || "local_ask";
const amHm = (ts) => new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false });

function amReset(sid) {
  Object.assign(AM, { sid, failed: null, loading: null, seen: null, first: true, sel: null, focus: null, rows: [], model: null, selObj: null });
  AM.nodes.clear(); AM.edges.clear();
  const root = svg("svg", { role: "img", "aria-label": "Agent architecture map", class: "amsvg" });
  const defs = svg("defs");
  for (const [id, cls] of [["am-ar", "arrow"], ["am-ar-active", "arrow active"], ["am-ar-local", "arrow local"], ["am-ar-handoff", "arrow handoff"]]) {
    const m = svg("marker", { id, viewBox: "0 0 10 10", refX: 9, refY: 5, markerWidth: 6, markerHeight: 6, orient: "auto-start-reverse" });
    m.append(svg("path", { d: "M0,0 L10,5 L0,10 z", class: cls }));
    defs.append(m);
  }
  root.append(defs);
  AM.layers = {};
  for (const k of ["edges", "packets", "nodes", "fx"]) { AM.layers[k] = svg("g", { class: `layer-${k}` }); root.append(AM.layers[k]); }
  AM.pk = AM.layers.packets; AM.fx = AM.layers.fx; AM.root = root;
  AM.note = h("div", "small muted amnote");
  $("amap").replaceChildren(root, AM.note);
}

// The catalog is fetched when the panel opens and cached for 60 s; a failure is not retried until the panel is opened again.
function amCatalog(s) {
  const c = AM.cache.get(s.id);
  if (c && Date.now() - c.at < 60000) return c.data;
  if (AM.failed !== s.id && AM.loading !== s.id) {
    AM.loading = s.id;
    fetch(`/api/agents?sid=${encodeURIComponent(s.id)}`, { credentials: "same-origin" })
      .then((r) => r.ok ? r.json() : Promise.reject(new Error("status")))
      .then((j) => {
        if (!j || !Array.isArray(j.agents)) throw new Error("shape");
        AM.cache.set(s.id, { at: Date.now(), data: j });
      })
      .catch(() => { if (!AM.cache.has(s.id)) AM.failed = s.id; })
      .finally(() => { if (AM.loading === s.id) AM.loading = null; if (SELECTED?.sid === s.id && !$("details").hidden) showDetails(SELECTED, true); });
  }
  return c ? c.data : null;
}

// Nodes, edges and their states (running / used / avail) for one session.
function amModel(s, cat) {
  const live = !gone(s), now = STATE.now;
  const types = new Map();
  for (const a of s.subagents) {
    const t = types.get(a.type) || { n: 0, last: 0, running: 0, since: 0, source: a.source, model: null, src: null };
    t.n++; t.last = Math.max(t.last, a.stop || a.start);
    if (!a.stop && live) { t.running++; t.since = t.since ? Math.min(t.since, a.start) : a.start; }
    if (a.model) { t.model = a.model; t.src = a.model_src; }
    types.set(a.type, t);
  }
  const ltools = new Map();
  const lbump = (name, ts, running) => { const t = ltools.get(name) || { n: 0, last: 0, running: 0 }; t.n++; t.last = Math.max(t.last, ts); if (running) t.running++; ltools.set(name, t); };
  for (const [ts, tool] of s.tools) if (String(tool).startsWith("mcp__niu-local__")) lbump(String(tool).split("__").pop(), ts, false);
  for (const l of localKids()[s.id] || []) lbump(amLocalTool(l), l.first, !l.ended && l.state !== "stale");
  const feedHas = (re) => STATE.feed.some((e) => e.session_id === s.id && e.tool === "Skill" && re.test(e.summary || ""));
  const stage = (n) => (s.pipeline || []).find((p) => p.stage === n)?.status;
  const catAgents = new Map((cat?.agents || []).map((a) => [a.name, a]));
  const skill = (n) => (cat?.skills || []).find((k) => k.name === n);
  const nodes = [], byId = new Map();
  const add = (n) => { nodes.push(n); byId.set(n.id, n); return n; };
  add({ id: "user", kind: "user", label: "You", source: "session", sub: s.state === "waiting" && live ? "waiting for you" : "prompts", used: true, running: s.state === "waiting" && live, tip: "You\nPrompts and approvals" });
  add({ id: "main", kind: "main", label: "Main session", source: "session", model: s.model, modelSrc: "actual", sub: live ? (STATE_LABEL[s.state] || s.state) : "ended", used: true, running: live && s.state === "working", tip: `Main session\n${s.model ? shortModel(s.model) : "model unknown"}` });
  const agentNode = (name) => {
    const d = catAgents.get(name), t = types.get(name), c = amCluster(name);
    const used = !!t, running = !!t?.running;
    const model = t?.model || (d && d.model && d.model !== "inherit" ? d.model : "");
    const modelLbl = model ? shortModel(model) : "inherits";
    const label = d?.effort ? `${modelLbl} · ${d.effort}` : modelLbl;
    const sub = running ? `running ${dur(now - t.since)}` : used ? `ran ${t.n}× · last ${amHm(t.last)}` : "available";
    const hint = AM_FLOW[name] ? `usually started by /${c}` : "";
    return { id: `a:${name}`, kind: "agent", cluster: c, label: name, source: d?.source || t?.source || "session", model: label, guess: !(t?.model && t.src === "actual"), sub, used, running, count: t?.n || 0,
      tip: [name, d ? d.description : "definition not found", `${d?.source || t?.source || "?"} · ${label}`, hint].join("\n") };
  };
  const clusters = [];
  const dispatcher = (c, kids, used) => {
    const sk = skill(c);
    const run = kids.some((k) => k.running);
    const isSkill = c === "develop" || c === "dual-review";
    const d = add({ id: `d:${c}`, kind: "disp", label: isSkill ? `/${c}` : c === "agent" ? "Agent tool" : "niu-local", source: isSkill ? "skill" : c === "local" ? "local" : "session",
      sub: run ? "running" : used ? "used" : "available", used, running: run,
      tip: [isSkill ? `/${c}` : c === "agent" ? "Agent tool" : "niu-local MCP", sk?.description || (c === "agent" ? "Starts a subagent" : c === "local" ? "Ollama-backed tools" : ""), "dispatcher"].join("\n") });
    clusters.push({ id: c, disp: d, kids });
  };
  // /develop: designer -> approval -> implementer
  const dev = ["designer", "implementer"].filter((n) => catAgents.has(n) || types.has(n)).map(agentNode);
  if (dev.length || s.pipeline) {
    const gUsed = stage("approve") === "done" || stage("approve") === "active";
    const gate = { id: "gate:approve", kind: "gate", label: "approval", source: "session", sub: gUsed ? "you approve" : "available", used: gUsed, running: stage("approve") === "active" && live, tip: "Approval\nYou approve the design before implementation" };
    const kids = [];
    for (const k of dev) { if (k.id === "a:implementer" && !kids.some((x) => x.id === "gate:approve")) kids.push(gate); kids.push(k); }
    if (!kids.includes(gate)) kids.push(gate);
    kids.forEach((k) => add(k));
    dispatcher("develop", kids, !!s.pipeline || dev.some((k) => k.used) || feedHas(/develop/));
  }
  const rev = ["security-reviewer", "test-reviewer"].filter((n) => catAgents.has(n) || types.has(n)).map(agentNode);
  if (rev.length) { rev.forEach(add); dispatcher("dual-review", rev, rev.some((k) => k.used) || feedHas(/dual-review/)); }
  const other = [...AM_BUILTIN.filter((n) => catAgents.has(n)), ...[...catAgents.keys()].filter((n) => !AM_FLOW[n] && !AM_BUILTIN.includes(n)),
    ...[...types.keys()].filter((n) => !AM_FLOW[n] && !catAgents.has(n))].filter((n, i, a) => a.indexOf(n) === i).map(agentNode);
  if (other.length) { other.forEach(add); dispatcher("agent", other, other.some((k) => k.used) || s.tools.some(([, t]) => t === "Agent" || t === "Task")); }
  const lnames = [...(cat?.local_tools || []).map((t) => t.name), ...ltools.keys()].filter((n, i, a) => a.indexOf(n) === i);
  const lnodes = lnames.map((n) => {
    const d = (cat?.local_tools || []).find((t) => t.name === n), t = ltools.get(n), unavail = d && d.available === false;
    return { id: `l:${n}`, kind: "local", label: n, source: "local", sub: t?.running ? "running" : t ? `used ${t.n}×` : unavail ? "not in this domain" : "available", used: !!t, running: !!t?.running, small: n === "local_status",
      tip: [n, d?.description || "", unavail ? "not available in this domain" : "niu-local tool"].join("\n") };
  });
  if (lnodes.length) { lnodes.forEach(add); dispatcher("local", lnodes, lnodes.some((k) => k.used)); }
  const usedKid = nodes.some((n) => (n.kind === "agent" || n.kind === "local") && n.used);
  add({ id: "out", kind: "out", label: "Integrate & report", source: "session", sub: "main session", used: usedKid, running: false, tip: "Main session\nIntegrates the results and reports" });
  // edges
  const edges = [], eadd = (a, b, kind) => {
    const A = byId.get(a), B = byId.get(b);
    if (!A || !B) return;
    edges.push({ id: `${a}>${b}`, a, b, kind: kind || (A.kind === "local" || B.kind === "local" || a === "d:local" ? "local" : ""),
      state: A.used && B.used ? "used" : "avail", active: !!B.running && A.id !== "user" && b !== "out" });
  };
  eadd("user", "main");
  for (const c of clusters) {
    eadd("main", c.disp.id);
    if (c.id === "develop") {
      const ids = c.kids.map((k) => k.id);
      eadd(c.disp.id, ids[0]);
      for (let i = 0; i + 1 < ids.length; i++) eadd(ids[i], ids[i + 1]);
      eadd(ids[ids.length - 1], "out");
      if (byId.has("gate:approve")) { eadd("gate:approve", "user", "approval"); }
    } else for (const k of c.kids) { eadd(c.disp.id, k.id); eadd(k.id, "out"); }
  }
  return { nodes, byId, edges, clusters };
}

function amLayout(m) {
  const place = (n, x, y, w, h) => Object.assign(n, { x, y, w, h });
  let y = APAD;
  place(m.byId.get("user"), AMW / 2 - AW - 20, y, AW, AH);
  place(m.byId.get("main"), AMW / 2 + 20, y, AW, AH);
  y += AH + 40;
  for (const c of m.clusters) {
    const loc = c.id === "local";
    c.cw = loc ? ALW : AW; c.ch = loc ? ALH : AH;
    c.cols = c.id === "develop" ? c.kids.length : loc ? 2 : Math.min(3, c.kids.length);
    c.w = Math.max(AW, c.cols * c.cw + (c.cols - 1) * AGX);
  }
  const bands = []; let cur = { cs: [], w: 0 };
  for (const c of m.clusters) {
    if (cur.cs.length && cur.w + 24 + c.w > AMW - 2 * APAD) { bands.push(cur); cur = { cs: [], w: 0 }; }
    cur.w += (cur.cs.length ? 24 : 0) + c.w; cur.cs.push(c);
  }
  if (cur.cs.length) bands.push(cur);
  for (const b of bands) {
    let x = (AMW - b.w) / 2, bottom = y;
    for (const c of b.cs) {
      place(c.disp, x + (c.w - AW) / 2, y, AW, AH);
      const ky = y + AH + 34, rows = Math.ceil(c.kids.length / c.cols), rowW = c.cols * c.cw + (c.cols - 1) * AGX, kx = x + (c.w - rowW) / 2;
      c.kids.forEach((k, i) => place(k, kx + (i % c.cols) * (c.cw + AGX), ky + Math.floor(i / c.cols) * (c.ch + 10), c.cw, c.ch));
      bottom = Math.max(bottom, ky + rows * c.ch + (rows - 1) * 10);
      x += c.w + 24;
    }
    y = bottom + 40;
  }
  place(m.byId.get("out"), AMW / 2 - AW / 2, y, AW, AH);
  m.W = AMW; m.H = y + AH + APAD;
}

// Vertical cubic from the bottom-centre of a to the top-centre of b; a straight line inside a row.
function amPath(a, b) {
  const cx = (n) => n.x + n.w / 2;
  if (Math.abs(a.y - b.y) < 4) {
    const r = a.x < b.x, y = a.y + a.h / 2;
    return `M${r ? a.x + a.w : a.x},${y} L${r ? b.x : b.x + b.w},${b.y + b.h / 2}`;
  }
  const down = b.y > a.y, y1 = down ? a.y + a.h : a.y, y2 = down ? b.y : b.y + b.h, my = (y1 + y2) / 2;
  return `M${cx(a)},${y1} C${cx(a)},${my} ${cx(b)},${my} ${cx(b)},${y2}`;
}

function amNode(n) {
  let g = AM.nodes.get(n.id);
  if (!g) {
    g = svg("g", { class: "amnode", tabindex: "0", role: "button", "data-node": n.id });
    if (n.kind === "gate") g.append(svg("polygon", { class: "ambox amgate", points: `${n.w / 2},2 ${n.w - 2},${n.h / 2} ${n.w / 2},${n.h - 2} 2,${n.h / 2}` }));
    else g.append(svg("rect", { class: "amhalo", width: n.w, height: n.h, rx: 9 }), svg("rect", { class: "ambox", width: n.w, height: n.h, rx: 9 }));
    // Keep the four text elements in a list: an SVG element's `children` is an HTMLCollection (no filter/find).
    const texts = [];
    for (const [c, x, y, extra] of [["badge", 8, 13], ["lbl mdl", n.w - 8, 13, { "text-anchor": "end" }], ["name", 8, 28], ["lbl sub", 8, 41]]) {
      const t = svg("text", { class: `am${c.split(" ")[0]} ${c}`, x, y, ...(extra || {}) });
      texts.push(t); g.append(t);
    }
    if (n.kind === "gate") for (const t of texts) { t.setAttribute("x", n.w / 2); t.setAttribute("text-anchor", "middle"); }
    g.amText = texts;
    g.addEventListener("click", () => amFocus(n.id));
    g.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); amFocus(n.id); } });
    AM.layers.nodes.append(g);
    AM.nodes.set(n.id, g);
  }
  g.setAttribute("transform", `translate(${n.x},${n.y})`);
  const st = n.running ? "running" : n.used ? "used" : "avail";
  g.setAttribute("class", `amnode ${st}${n.kind === "gate" ? " gate" : ""}${n.small ? " small" : ""}${n.kind === "local" ? " alocal" : ""}`);
  g.setAttribute("data-tip", n.tip);
  g.setAttribute("aria-label", `${n.label}, ${n.sub}`);
  const [badge, mdl, name, sub] = g.amText;
  badge.textContent = n.kind === "gate" ? "" : n.source === "session" || n.source === "builtin" ? n.source : n.source;
  badge.setAttribute("class", `ambadge badge src-${n.source}`);
  mdl.textContent = trunc(n.model ? (n.model.length ? shortModel(n.model) : "") : "", 18);
  mdl.setAttribute("class", `amlbl lbl mdl${n.guess ? " guess" : ""}`);
  name.textContent = trunc(n.label, n.small ? 12 : 17);
  sub.textContent = trunc(n.sub, 21);
  n.el = g;
}

function amEdge(e, m) {
  let rec = AM.edges.get(e.id);
  if (!rec) {
    const pid = `am${++AM.ids}`;
    rec = { pid, base: svg("path", { id: pid, class: "amedge" }), flow: svg("path", { class: "gflow" }) };
    AM.layers.edges.append(rec.base, rec.flow);
    AM.edges.set(e.id, rec);
  }
  const d = amPath(m.byId.get(e.a), m.byId.get(e.b));
  rec.base.setAttribute("d", d); rec.flow.setAttribute("d", d);
  rec.kind = e.kind; rec.state = e.state; rec.active = e.active;
  rec.base.setAttribute("marker-end", `url(#${e.active ? "am-ar-active" : e.kind === "local" ? "am-ar-local" : "am-ar"})`);
  rec.alive = true;
  return rec;
}

// Rows of the activity list: newest 40, each with the nodes and edges it highlights.
function amActivity(s) {
  const rows = [], byId = new Map(s.subagents.map((a) => [a.id, a]));
  const sess = new Map(STATE.sessions.map((x) => [x.id, x]));
  const feed = STATE.feed.filter((e) => e.session_id === s.id);
  const sumFor = (ts, tool, aid) => feed.find((e) => Math.abs(e.ts - ts) <= 1.5 && e.tool === tool && (aid ? e.agent_id === aid : !e.agent_id))?.summary || "";
  const spawn = (t) => { const c = amCluster(t); return { nodes: ["main", `d:${c}`, `a:${t}`], edges: [`main>d:${c}`, `d:${c}>a:${t}`] }; };
  const row = (key, ts, text, r) => rows.push({ key, ts, text, nodes: r.nodes || [], edges: r.edges || [] });
  const claimed = new Set();
  s.tools.slice(-120).forEach(([ts, tool], i) => {
    const key = `t:main:${ts}:${i}`, sum = sumFor(ts, tool);
    if (tool === "Agent" || tool === "Task") {
      let best = null;
      for (const a of s.subagents) if (!claimed.has(a.id) && Math.abs(a.start - ts) <= 5 && (!best || Math.abs(a.start - ts) < Math.abs(best.start - ts))) best = a;
      if (best) claimed.add(best.id);
      const t = best?.type || /^([\w.:-]+):/.exec(sum)?.[1];
      return t ? row(key, ts, `spawn ${t}`, spawn(t)) : row(key, ts, "spawn agent", { nodes: ["main", "d:agent"], edges: ["main>d:agent"] });
    }
    if (tool === "Skill") {
      const c = /dual-review/.test(sum) ? "dual-review" : /develop/.test(sum) ? "develop" : null;
      return c ? row(key, ts, `/${c}`, { nodes: ["main", `d:${c}`], edges: [`main>d:${c}`] }) : row(key, ts, `Skill ${sum}`.trim(), { nodes: ["main"] });
    }
    if (String(tool).startsWith("mcp__niu-local__")) {
      const t = tool.split("__").pop();
      return row(key, ts, `${t}${sum ? ` ${trunc(sum, 60)}` : ""}`, { nodes: ["main", "d:local", `l:${t}`], edges: ["main>d:local", `d:local>l:${t}`] });
    }
    if (tool === "AskUserQuestion") {
      const done = s.subagents.some((a) => a.type === "designer" && a.stop && a.stop <= ts + 1);
      return done ? row(key, ts, "asked for approval", { nodes: ["main", "gate:approve", "user", "a:designer"], edges: ["a:designer>gate:approve", "gate:approve>user"] })
        : row(key, ts, "asked you a question", { nodes: ["main", "user"], edges: ["user>main"] });
    }
    row(key, ts, `${tool}${sum ? ` ${trunc(sum, 60)}` : ""}`, { nodes: ["main"] });
  });
  for (const a of s.subagents) {
    const t = a.type, c = amCluster(t);
    a.tools.slice(-30).forEach(([ts, tool], i) => row(`t:${a.id}:${ts}:${i}`, ts, `${t} · ${tool}${sumFor(ts, tool, a.id) ? ` ${trunc(sumFor(ts, tool, a.id), 60)}` : ""}`, { nodes: [`a:${t}`], edges: [`d:${c}>a:${t}`] }));
    row(`s:${a.id}`, a.start, `${t} started`, { nodes: [`a:${t}`], edges: [`d:${c}>a:${t}`] });
    if (a.stop) row(`e:${a.id}`, a.stop, `${t} done in ${dur(a.stop - a.start)}`, { nodes: [`a:${t}`, "out"], edges: [`a:${t}>out`] });
  }
  for (const l of localKids()[s.id] || []) {
    const t = amLocalTool(l), what = `${TASK_NAME[t] || "local agent"}${l.query ? `: “${trunc(l.query, 50)}”` : ""}`;
    row(`l:${l.id}:s`, l.first, what, { nodes: [`l:${t}`], edges: [`d:local>l:${t}`] });
    if (l.ended) row(`l:${l.id}:e`, l.last, `${TASK_NAME[t] || "local agent"} done`, { nodes: [`l:${t}`, "out"], edges: [`l:${t}>out`] });
  }
  s.waits.slice(-10).forEach((ts) => row(`w:${ts}`, ts, "waiting for you", { nodes: ["user", "main"], edges: ["user>main"] }));
  for (const hf of s.chain?.handoffs || []) row(`h:${hf.from}:${hf.to}`, hf.at, `hand-off: ${amNodeOf(hf.from, byId, sess).label} → ${amNodeOf(hf.to, byId, sess).label}`, amHand(hf, byId, sess));
  return rows.sort((a, b) => b.ts - a.ts).slice(0, 40);
}
function amNodeOf(id, byId, sess) {
  const a = byId.get(id);
  if (a) return { id: `a:${a.type}`, label: a.type, type: a.type };
  const l = sess.get(id);
  if (l && l.local) { const t = amLocalTool(l); return { id: `l:${t}`, label: t, local: true }; }
  return { id: "main", label: "main" };
}
function amHand(hf, byId, sess) {
  const f = amNodeOf(hf.from, byId, sess), t = amNodeOf(hf.to, byId, sess);
  if (f.type === "designer" && t.type === "implementer") return { nodes: [f.id, "gate:approve", t.id], edges: [`${f.id}>gate:approve`, `gate:approve>${t.id}`] };
  const nodes = [f.id, t.id], edges = [];
  if (f.id !== "main") edges.push(`${f.id}>out`);
  if (t.type) edges.push(`d:${amCluster(t.type)}>${t.id}`); else if (t.local) edges.push(`d:local>${t.id}`);
  return { nodes, edges };
}

// Highlight: a selected row lights its nodes and edges and fades the rest; a focused node only lights itself.
function amApply() {
  const row = AM.rows.find((r) => r.key === AM.sel);
  const nodes = row ? new Set(row.nodes) : AM.focus ? new Set([AM.focus]) : null, edges = row ? new Set(row.edges) : null;
  for (const [id, g] of AM.nodes) {
    const base = (g.getAttribute("class") || "").split(" ").filter((c) => !["hl", "fade", "focus"].includes(c));
    if (nodes) base.push(nodes.has(id) ? (row ? "hl" : "focus") : (row ? "fade" : ""));
    g.setAttribute("class", base.filter(Boolean).join(" "));
    g.setAttribute("aria-pressed", String(AM.focus === id));
  }
  for (const [id, r] of AM.edges) {
    const on = !!edges?.has(id);
    r.base.setAttribute("class", `amedge ${r.state}${r.kind ? ` ${r.kind}` : ""}${r.active ? " active" : ""}${on ? " hl" : edges ? " fade" : ""}`);
    r.flow.setAttribute("class", `gflow${r.active ? " active" : ""}${r.kind === "local" ? " local" : ""}`);
  }
  const box = $("amfeed");
  for (const el of box.children || []) {
    if (!el || !el.getAttribute) continue;
    const r = AM.rows.find((x) => x.key === el.getAttribute("data-key")), touch = AM.focus && r?.nodes.includes(AM.focus);
    el.setAttribute("class", `amrow${r && r.key === AM.sel ? " sel" : ""}${touch ? " on" : AM.focus ? " mute" : ""}`);
    el.setAttribute("aria-selected", String(!!r && r.key === AM.sel));
  }
}
function amSelect(key) {
  AM.sel = AM.sel === key ? null : key;
  amApply();
  const row = AM.rows.find((r) => r.key === AM.sel);
  if (row && !reducedMotion() && AM.model) for (const id of row.edges) { const e = AM.edges.get(id); if (e) packet(e.pid, "spawn", { layer: AM.pk }); }
}
function amFocus(id) {
  AM.focus = AM.focus === id ? null : id;
  amApply();
  if (AM.focus) for (const el of $("amfeed").children || []) {
    const r = AM.rows.find((x) => x.key === el?.getAttribute?.("data-key"));
    if (r?.nodes.includes(id)) { el.scrollIntoView({ block: "nearest" }); break; }
  }
}
// Keyboard on the activity list: arrows move and select, Home / End jump, Enter / Space toggles, Backspace clears.
function amKey(e) {
  const keys = AM.rows.map((r) => r.key), i = Math.max(0, keys.indexOf(AM.rows.find((r) => r.key === AM.cur)?.key));
  let j = null;
  if (e.key === "ArrowDown") j = Math.min(keys.length - 1, i + 1);
  else if (e.key === "ArrowUp") j = Math.max(0, i - 1);
  else if (e.key === "Home") j = 0;
  else if (e.key === "End") j = keys.length - 1;
  else if (e.key === "Enter" || e.key === " ") { e.preventDefault(); if (keys.length) amSelect(keys[i]); return; }
  else if (e.key === "Backspace") { e.preventDefault(); AM.sel = null; amApply(); return; }
  if (j === null || !keys.length) return;
  e.preventDefault();
  AM.cur = keys[j]; AM.sel = keys[j];
  amApply(); amRoving();
  const el = [...$("amfeed").children].find((c) => c?.getAttribute?.("data-key") === keys[j]);
  if (el) { el.focus(); el.scrollIntoView({ block: "nearest" }); }
}
function amRoving() {
  for (const el of $("amfeed").children || []) if (el?.setAttribute) el.setAttribute("tabindex", el.getAttribute("data-key") === (AM.cur || AM.rows[0]?.key) ? "0" : "-1");
}
function amFeed() {
  const box = $("amfeed");
  box.setAttribute("role", "listbox"); box.setAttribute("aria-label", "Recent activity (select a row to highlight its agents)");
  if (!AM.keyed) { box.addEventListener("keydown", amKey); AM.keyed = true; }
  box.replaceChildren(...AM.rows.map((r) => {
    const el = h("div", "amrow", "", { role: "option", "data-key": r.key, "data-tip": `${r.text}\n${hhmmss(r.ts)}` });
    el.append(h("span", "muted", hhmmss(r.ts)), h("span", "amtxt", r.text));
    el.addEventListener("click", () => { AM.cur = r.key; amSelect(r.key); amRoving(); });
    return el;
  }));
  if (!AM.rows.length) box.append(h("div", "empty", "No activity yet."));
  amRoving(); amApply();
}

// Live sessions only, and never on the first paint: spawn packets go main -> dispatcher -> agent, tool calls burst on the agent,
// results go agent -> end node.
function amAnimate(s, m) {
  const subs = new Map(s.subagents.map((a) => [a.id, { stop: !!a.stop, n: a.tools.length }]));
  const lk = localKids()[s.id] || [];
  const cur = { subs, loc: new Map(lk.map((l) => [l.id, !!l.ended])), hand: new Set((s.chain?.handoffs || []).map((x) => `${x.from}>${x.to}`)), tools: s.tools.length };
  const prev = AM.seen; AM.seen = cur;
  if (!prev || AM.first || gone(s) || reducedMotion()) { AM.first = false; return; }
  const pk = (id, cls, delay) => { const e = AM.edges.get(id); if (e) packet(e.pid, cls, { layer: AM.pk, delay }); };
  const fx = (id, tool) => { const n = m.byId.get(id); if (n) burst(n, TOOL_CLASS(tool), tool, { layer: AM.fx, w: n.w, h: n.h }); };
  for (const a of s.subagents) {
    const p = prev.subs.get(a.id), c = amCluster(a.type);
    if (!p) { pk(`main>d:${c}`, "spawn", 0); pk(`d:${c}>a:${a.type}`, "spawn", 450); }
    else {
      const k = Math.min(3, a.tools.length - p.n);
      for (const [, tool] of k > 0 ? a.tools.slice(-k) : []) fx(`a:${a.type}`, tool);
    }
    if (a.stop && !(p ? p.stop : false)) pk(`a:${a.type}>out`, "result", 0);
  }
  for (const l of lk) {
    const t = amLocalTool(l);
    if (!prev.loc.has(l.id)) pk(`d:local>l:${t}`, "local", 0);
    if (l.ended && prev.loc.get(l.id) === false) pk(`l:${t}>out`, "result", 0);
  }
  const sess = new Map(STATE.sessions.map((x) => [x.id, x])), byId = new Map(s.subagents.map((a) => [a.id, a]));
  for (const hf of s.chain?.handoffs || []) if (!prev.hand.has(`${hf.from}>${hf.to}`)) { const e = amHand(hf, byId, sess).edges.find((x) => AM.edges.has(x)); if (e) pk(e, "handoff", 0); }
  const k = Math.min(3, s.tools.length - prev.tools);
  for (const [, tool] of k > 0 ? s.tools.slice(-k) : []) fx("main", tool);
}

function amRender(s) {
  const cat = amCatalog(s);
  const m = amModel(s, cat);
  amLayout(m);
  AM.model = m;
  AM.root.setAttribute("viewBox", `0 0 ${m.W} ${m.H}`);
  for (const r of AM.edges.values()) r.alive = false;
  const ids = new Set(m.nodes.map((n) => n.id));
  for (const n of m.nodes) amNode(n);
  for (const e of m.edges) amEdge(e, m);
  for (const [id, g] of [...AM.nodes]) if (!ids.has(id)) { g.remove(); AM.nodes.delete(id); }
  const live = new Set(m.edges.map((e) => e.id));
  for (const [id, r] of [...AM.edges]) if (!live.has(id)) { r.base.remove(); r.flow.remove(); AM.edges.delete(id); }
  AM.note.textContent = cat ? (cat.skills || []).filter((k) => !k.flow).length ? `Other skills: ${(cat.skills || []).filter((k) => !k.flow).map((k) => k.name).join(", ")}` : ""
    : AM.failed === s.id ? "Available agents could not be loaded." : "Loading available agents…";
  AM.rows = amActivity(s);
  if (AM.sel && !AM.rows.some((r) => r.key === AM.sel)) AM.sel = null;
  amFeed();
  amAnimate(s, m);
}

function showDetails(sel, refresh) {
  SELECTED = sel;
  const s = STATE.sessions.find((x) => x.id === sel.sid);
  if (!s) { if (!refresh) $("details").hidden = true; return; }
  if (AM.sid !== s.id || $("details").hidden) amReset(s.id);
  const a = sel.aid && s.subagents.find((x) => x.id === sel.aid);
  if (sel !== AM.selObj) { AM.selObj = sel; if (a) AM.focus = `a:${a.type}`; }
  const close = h("button", "ghost x", "Close", { id: "dclose" });
  close.onclick = () => { SELECTED = null; AM.sid = null; $("details").hidden = true; };
  const title = h("h2", "", a ? `${a.type} ` : projLabel(s));
  if (a) title.append(h("span", `src src-${a.source}`, a.source));
  $("amhead").replaceChildren(close, title, h("div", "small muted", `session ${s.id} · ${dl(s.domain)}${a && a.task ? ` · task: ${a.task}` : ""}`));
  $("details").classList.add("wide");
  $("details").hidden = false;
  amRender(s);
}

// ---------- actions ----------
async function openMerge(ref) {
  const [repo, num] = ref.split("#");
  const pr = PRS.repos.flatMap((r) => r.prs).find((p) => p.ref === ref);
  $("mergetitle").textContent = `${ref}: ${pr ? pr.title : ""}`;
  $("mergecode").textContent = "····";
  $("mergeconfirm").value = ""; $("mergemsg").textContent = ""; $("mergemsg").className = "msg";
  $("mergego").disabled = true;
  $("mergedlg").dataset.repo = repo; $("mergedlg").dataset.num = num;
  $("mergedlg").showModal();
  // The host issues a single-use 4-digit code; typing it back confirms the merge.
  const r = await POST("/api/prs/merge-code", { repo, number: Number(num) });
  if (!r.ok) {
    $("mergemsg").textContent = r.data.error || `Couldn't start the merge (${r.status})`; $("mergemsg").className = "msg err";
    if (r.status === 401) loadMe();
    return;
  }
  $("mergecode").textContent = r.data.code;
  $("mergego").disabled = false;
  $("mergeconfirm").focus();
}

$("mergeform").addEventListener("submit", async (ev) => {
  if (ev.submitter && ev.submitter.value !== "merge") return;
  ev.preventDefault();
  const dlg = $("mergedlg"), typed = $("mergeconfirm").value.replace(/\D/g, "");
  if (typed.length !== 4) { $("mergemsg").textContent = "Type the 4-digit code shown."; $("mergemsg").className = "msg err"; return; }
  $("mergego").disabled = true; $("mergemsg").textContent = "Merging (re-checking status)…"; $("mergemsg").className = "msg";
  const r = await POST("/api/prs/merge", { repo: dlg.dataset.repo, number: Number(dlg.dataset.num), confirm: typed });
  if (r.ok) { $("mergemsg").textContent = r.data.message || "Merged."; $("mergemsg").className = "msg ok"; setTimeout(() => dlg.close(), 1200); }
  else {
    $("mergemsg").textContent = r.data.error || `Failed (${r.status})`; $("mergemsg").className = "msg err";
    if (r.status === 400 && /wrong code/.test(r.data.error || "")) { $("mergego").disabled = false; $("mergeconfirm").select(); }
    if (r.status === 401) loadMe();
  }
});
$("mergecancel").addEventListener("click", () => $("mergedlg").close());
$("mergeconfirm").addEventListener("input", () => { $("mergemsg").textContent = ""; });

$("pairform").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const code = $("paircode").value.replace(/\D/g, "");
  if (code.length !== 6) { $("pairmsg").textContent = "Enter the 6-digit code from niu-dash pair."; $("pairmsg").className = "msg err"; return; }
  const r = await POST("/api/pair", { code, name: $("pairname").value.trim() || navigator.platform || "browser" });
  $("pairmsg").textContent = r.ok ? "Paired." : (r.data.error || "Pairing failed"); $("pairmsg").className = `msg ${r.ok ? "ok" : "err"}`;
  if (r.ok) { $("paircode").value = ""; await loadMe(); setTimeout(() => toggleMenu(false), 900); }
});
$("paircode").addEventListener("input", () => { $("pairmsg").textContent = ""; });
$("unpair").addEventListener("click", async () => { await POST("/api/unpair"); loadMe(); });
$("prrefresh").onclick = () => POST("/api/prs/refresh");
$("domain").onchange = () => { G.first = true; connect(); TOK.data = null; loadTokens(); };
$("since").onchange = connect;
$("ended").checked = store.get("niu.ended", false);
$("calm").checked = store.get("niu.calm", false);
$("ended").onchange = () => { store.set("niu.ended", $("ended").checked); if (STATE) render(); };
$("calm").onchange = () => { store.set("niu.calm", $("calm").checked); if (STATE) render(); };
$("notify").checked = store.get("niu.notify", false) && typeof Notification !== "undefined" && Notification.permission === "granted";
$("notify").onchange = () => setNotify($("notify").checked);
let resizeT = 0;
window.addEventListener("resize", () => { clearTimeout(resizeT); resizeT = setTimeout(refit, 120); });

// Local LLM endpoint health (GPU box / local CPU), refreshed every 30 s
const EP_NAME = { remote: "Remote GPU", local: "Local CPU" };
// Claude plan limits (5-hour / weekly): the status line's last report, or the usage API (default on) when that is older.
function claudeLimits(c) {
  if (!c || !c.available) {
    return `<div class="limrow muted small" data-tip="From the niu-statusline status line of Claude Code terminal sessions, or from Anthropic's usage API (read-only, using your Claude Code login; NIU_CLAUDE_USAGE_API=0 turns it off)">` +
      `Claude limits: shown once a terminal Claude Code session reports them (the desktop app does not run it; the dashboard also reads usage from Anthropic unless NIU_CLAUDE_USAGE_API=0)</div>`;
  }
  const fmtReset = (ts) => {
    if (!ts) return "";
    const d = new Date(ts * 1000), today = new Date().toDateString() === d.toDateString();
    return `resets ${today ? d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false })
      : d.toLocaleDateString([], { weekday: "short" }) + " " + d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false })}`;
  };
  const shortReset = (ts) => {
    if (!ts) return "";
    const d = new Date(ts * 1000);
    return "↻ " + (new Date().toDateString() === d.toDateString()
      ? d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false })
      : d.toLocaleDateString([], { weekday: "short" }));
  };
  const rows = c.windows.filter((w) => w.key === "five_hour" || w.key === "seven_day" || w.used >= 50).map((w) => {
    const lvl = w.used >= 90 ? "bad" : w.used >= 70 ? "warn" : "ok";
    return `<div class="limrow" data-tip="${esc(`Claude ${w.label}\n${w.used}% used${w.resets_at ? ` · ${fmtReset(w.resets_at)}` : ""}`)}">` +
      `<span class="llmpill up claude"><i></i>Claude</span><span class="nm">${esc(w.label)}</span>` +
      `<progress class="lim ${lvl}" max="100" value="${Math.min(100, w.used)}"></progress>` +
      `<span class="limpct ${lvl}">${Math.round(w.used)}%</span><span class="mdl">${esc(shortReset(w.resets_at))}</span></div>`;
  }).join("");
  const age = Date.now() / 1000 - (c.updated || 0);
  return rows + (c.stale ? `<div class="llmfoot">Claude limits as of ${hhmmss(c.updated)} (${dur(age)} ago)${c.source === "api" ? " · from the usage API" : " · updates while a terminal Claude Code session runs"}</div>` : "");
}
async function loadLLM(force) {
  const r = await fetch(`/api/llm${force ? "?force=1" : ""}`, { credentials: "same-origin" }).then((x) => x.json()).catch(() => null);
  const card = $("llmcard");
  if (!r) {
    card.innerHTML = `<div class="msg err">Can't read the LLM status from the dashboard server.</div>`;
    return;
  }
  const eps = r.endpoints || [];
  LLM_EPS = eps;
  const state = (e) => !e.up ? "down" : Object.values(e.models || {}).every((m) => m.present) ? "up" : "partial";
  const word = { up: "online", partial: "models missing", down: "offline" };
  card.innerHTML = claudeLimits(r.claude) + eps.map((e) => {
    const st = state(e);
    const models = [...new Set(Object.values(e.models || {}).map((m) => `${m.present ? "" : "✗ "}${m.name}`))].join(", ");
    // The row shows only the state; the detail (and what to do about it) goes in the themed tooltip.
    const loopback = /^https?:\/\/(127\.0\.0\.1|localhost)[:/]/.test(e.url || "");
    const fix = e.up ? "" : loopback
      ? "Not reachable: publish the container on 127.0.0.1:11434 or set OLLAMA_LOCAL_BASE_URL."
      : "Not reachable: check the machine (and OLLAMA_HOST=0.0.0.0, or the gateway's key file). Local agents fall back to the next endpoint.";
    const tip = [`${e.label || EP_NAME[e.name] || e.name}: ${word[st]}`, `${e.url} · ${e.mode} mode`,
      e.up ? `Ollama ${e.version}` : `Error: ${e.error || "unreachable"}`, fix].filter(Boolean).join("\n");
    return `<div class="llmrow ${st}" data-tip="${esc(tip)}"><span class="llmpill ${st}"><i></i>${esc(e.label)}</span>` +
      `<span class="nm">${esc(word[st])}</span><span class="mdl">${esc(models)}</span></div>`;
  }).join("") + `<div class="llmfoot">checked ${hhmmss(Date.now() / 1000)} · <button class="ghost linkish" id="llmrecheck">Re-check</button></div>`;
  $("llmrecheck").onclick = () => loadLLM(true);
}
loadLLM();
setInterval(() => { if (!document.hidden) loadLLM(); }, 30000);

// ---------- Weekly usage panel (Events tab) ----------
// History comes from /api/usage/history (samples recorded by niu-dash). Everything is built with h()/svg() (textContent and
// setAttribute only); the fixed message tables below are the only prose, and no server string is ever shown.
const USAGE = { weeks: [2, 5, 10].includes(store.get("niu.usage.weeks", 5)) ? store.get("niu.usage.weeks", 5) : 5,
  open: store.get("niu.usage.open", true) !== false, h5: store.get("niu.usage.5h", false) === true, data: null, state: "loading", timer: 0,
  zoom: null, view: null, drag: null };   // zoom: {t0, t1} epoch seconds, memory only; view: geometry of the last render; drag: brush in progress
const USAGE_MSG = {
  loading: "Loading…",
  empty: "Collecting: the first sample appears within 5 minutes. Earlier weeks can't be recovered.",
  disabled: "Usage history is off (NIU_USAGE_COLLECT=0).",
  error: "Can't read usage history from the dashboard server.",
};
const USAGE_API_ERR = {
  expired: "the Claude login token has expired. Run any terminal Claude Code command (for example claude -p hi) to renew it.",
  renew_failed: "the Claude login token has expired and automatic renewal (claude auth status) didn't renew it. Run claude -p hi in a terminal.",
  rejected: "Anthropic rejected the Claude login token. Run any terminal Claude Code command to renew it, or sign in again.",
  no_login: "no Claude Code login was found for the usage API. Sign in with the terminal claude command.",
  off: "no terminal Claude session, and the usage API is off (NIU_CLAUDE_USAGE_API=0).",
  unreachable: "no terminal Claude session, and the usage API is unreachable.",
  unknown: "no terminal Claude session, and the usage API is off or unreachable.",
};
const USAGE_REC = { reading: "the last attempt to read the limits failed", database: "the last attempt to save a sample failed", other: "the last sampling attempt failed" };
const USAGE_GAP_TIP = "No samples\ndashboard down, or no terminal session and the usage API off";
const USAGE_SRC = { api: "usage API", statusline: "terminal status line" };
const USAGE_DAY = 86400;
const usageLevel = (v) => (v >= 90 ? "bad" : v >= 70 ? "warn" : "ok");
const USAGE_GLYPH = { ok: "●", warn: "▲", bad: "◆" };
const usageWhen = (ts) => `${new Date(ts * 1000).toLocaleDateString([], { weekday: "short" })} ${hm(ts)}`;
const usageDay = (ts) => new Date(ts * 1000).toLocaleDateString([], { month: "short", day: "numeric" });
const usageNum = (v) => (typeof v === "number" && isFinite(v) ? v : null);

function usageSegments(series) {
  const segs = [[]];
  for (const p of (series && Array.isArray(series.points) ? series.points : [])) {
    if (p === null) { if (segs[segs.length - 1].length) segs.push([]); continue; }
    if (Array.isArray(p) && usageNum(p[0]) !== null && usageNum(p[1]) !== null) segs[segs.length - 1].push([p[0], p[1]]);
  }
  return segs.filter((g) => g.length);
}

const usageWeeks = (u) => (Array.isArray(u.weeks) ? u.weeks : []).filter((w) => w && typeof w === "object" && usageNum(w.ended_pct) !== null && usageNum(w.last_sample) !== null && usageNum(w.resets_at) !== null);
function usageSummary(u) {
  const cur = u.current && typeof u.current === "object" ? u.current : null, bits = [];
  const wk = usageWeeks(u).slice(-4);
  if (!cur || usageNum(cur.used) === null) bits.push("Weekly: no current reading.");
  else {
    let s = `Weekly: ${Math.round(cur.used)}% now`;
    if (usageNum(cur.resets_at) !== null) s += `, resets ${usageWhen(cur.resets_at)}`;
    const pace = usagePace(u);
    if (pace !== null) s += `, on pace for about ${pace}%`;
    bits.push(s + ".");
  }
  if (wk.length) bits.push(`Last weeks ended at ${wk.map((w) => `${w.partial ? "≥" : ""}${Math.round(w.ended_pct)}%`).join(", ")}.`);
  else bits.push("No finished week recorded yet.");
  return bits.join(" ");
}
// Projection of the current week to its reset; null until 12 hours of the window have passed.
function usagePace(u) {
  const c = u.current && typeof u.current === "object" ? u.current : null;
  if (!c || usageNum(c.resets_at) === null || usageNum(c.used) === null || c.resets_at <= u.now) return null;
  const elapsed = u.now - (c.resets_at - 7 * USAGE_DAY);
  if (elapsed < 12 * 3600 || elapsed > 7 * USAGE_DAY) return null;
  return Math.round(Math.min(100, (c.used / elapsed) * 7 * USAGE_DAY));
}

const USAGE_ZOOM_MIN = 2 * 3600, USAGE_HOURLY = 3 * USAGE_DAY;
// The x range shown by default: the chosen number of weeks ending at the reset (or now), but not earlier than the first sample.
function usageDomain(u) {
  const cur = u.current && typeof u.current === "object" ? u.current : null;
  const dmax = cur && usageNum(cur.resets_at) !== null && cur.resets_at > u.now ? cur.resets_at : u.now;
  const rmin = dmax - USAGE.weeks * 7 * USAGE_DAY;
  const firsts = [usageNum(u.collecting_since)];
  for (const seg of usageSegments(u.series && u.series.seven_day)) firsts.push(seg[0][0]);
  const ok = firsts.filter((v) => v !== null);
  if (!ok.length) return { dmin: rmin, dmax };
  const first = Math.min(...ok), pad = Math.max(6 * 3600, 0.03 * (dmax - first));
  return { dmin: Math.max(rmin, Math.min(first - pad, dmax - USAGE_DAY)), dmax };
}
function usageView(dom) {
  const z = USAGE.zoom;
  if (z && usageNum(z.t0) !== null && usageNum(z.t1) !== null) {
    const t0 = Math.max(dom.dmin, z.t0), t1 = Math.min(dom.dmax, z.t1);
    if (t1 - t0 >= USAGE_ZOOM_MIN) return { t0, t1 };
  }
  USAGE.zoom = null;
  return { t0: dom.dmin, t1: dom.dmax };
}
// Axis ticks: hours when the span is 3 days or less, otherwise local midnights every 1/2/5/10 days.
function usageTicks(t0, t1) {
  const out = [], spanD = (t1 - t0) / USAGE_DAY;
  if (t1 - t0 <= USAGE_HOURLY) {
    const step = [1, 2, 3, 6, 12].find((s) => spanD * 24 / s <= 8) || 12;
    const d = new Date(t0 * 1000); d.setMinutes(0, 0, 0);
    while (d.getTime() / 1000 < t0 || d.getHours() % step) d.setHours(d.getHours() + 1);
    for (let n = 0; d.getTime() / 1000 <= t1 && n < 40; d.setHours(d.getHours() + step), n++) {
      const t = d.getTime() / 1000;
      out.push({ t, label: d.getHours() === 0 ? usageDay(t) : hm(t) });
    }
    return out;
  }
  const step = [1, 2, 5, 10].find((s) => spanD / s <= 8) || 10;
  const d0 = new Date(t0 * 1000); d0.setHours(0, 0, 0, 0);
  for (let d = new Date(d0), n = 0; d.getTime() / 1000 <= t1 && n < 40; d.setDate(d.getDate() + step), n++) {
    const t = d.getTime() / 1000;
    if (t >= t0) out.push({ t, label: usageDay(t) });
  }
  return out;
}
// Estimated text box (no DOM measuring): used to keep the graph labels from overlapping.
function usageTextBox(x, y, anchor, text, size, bold) {
  const w = String(text).length * size * (bold ? 0.62 : 0.58), x0 = anchor === "end" ? x - w : anchor === "middle" ? x - w / 2 : x;
  return { x0, x1: x0 + w, y0: y - 0.8 * size, y1: y + 0.25 * size };
}
// First candidate {x, y, anchor, text, size, bold} that lies inside plot {x0, x1, y0, y1} and misses every box in placed; null if none.
function usagePlace(placed, cands, plot) {
  for (const c of cands) {
    const b = usageTextBox(c.x, c.y, c.anchor, c.text, c.size, c.bold);
    if (b.x0 < plot.x0 || b.x1 > plot.x1 || b.y0 < plot.y0 - 2 || b.y1 > plot.y1) continue;
    if (placed.some((p) => b.x0 < p.x1 + 2 && b.x1 > p.x0 - 2 && b.y0 < p.y1 + 2 && b.y1 > p.y0 - 2)) continue;
    placed.push(b);
    return c;
  }
  return null;
}

function renderUsage(u) {
  USAGE.data = u && u.state === "ok" ? u : null;
  USAGE.view = null; $("usagezoomreset").hidden = true;
  const box = $("usagegraph"), sum = $("usagesum"), upd = $("usageupd");
  const note = (text, cls) => { box.replaceChildren(h("div", cls || "empty", text)); sum.textContent = ""; };
  if (u && u.state === "loading") { upd.textContent = ""; return note(USAGE_MSG.loading); }
  if (!u || typeof u !== "object" || !["ok", "empty", "disabled"].includes(u.state) || (u.state === "ok" && usageNum(u.now) === null)) { upd.textContent = ""; return note(USAGE_MSG.error, "msg err"); }
  if (u.state !== "ok") { upd.textContent = ""; return note(USAGE_MSG[u.state]); }
  const W = Math.max(320, Math.round(box.clientWidth || 640)), H = 230, L = 40, R = 14, T = 18, B = 28;
  const cur = u.current && typeof u.current === "object" ? u.current : null;
  const dom = usageDomain(u), { t0: tmin, t1: tmax } = usageView(dom);
  $("usagezoomreset").hidden = !USAGE.zoom;
  const X = (t) => L + ((t - tmin) / (tmax - tmin)) * (W - L - R), Y = (v) => T + (1 - Math.min(100, Math.max(0, v)) / 100) * (H - T - B);
  const root = svg("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": usageSummary(u), class: "usagesvg", preserveAspectRatio: "xMidYMid meet" });
  const defs = svg("defs");
  const pat = svg("pattern", { id: "usagehatch", width: "6", height: "6", patternUnits: "userSpaceOnUse", patternTransform: "rotate(45)" });
  pat.append(svg("line", { x1: "0", y1: "0", x2: "0", y2: "6", class: "hatchln" }));
  const clip = svg("clipPath", { id: "usageclip" });
  clip.append(svg("rect", { x: L, y: T - 5, width: W - L - R, height: H - T - B + 10 }));
  defs.append(pat, clip); root.append(defs);
  const plot = { x0: L, x1: W - R, y0: T, y1: H - B }, placed = [];
  const place = (cands, force) => {
    const c = usagePlace(placed, cands, plot);
    if (c || !force) return c;
    placed.push(usageTextBox(cands[0].x, cands[0].y, cands[0].anchor, cands[0].text, cands[0].size, cands[0].bold));
    return cands[0];
  };
  const lbl = (c, cls) => root.append(svg("text", { x: c.x, y: c.y, class: cls, "text-anchor": c.anchor }, c.text));
  // axes: 0, 50, 90 (the 10% weekly floor of the research gate) and 100
  for (const v of [0, 50, 90, 100]) {
    root.append(svg("line", { x1: L, x2: W - R, y1: Y(v), y2: Y(v), class: `axis${v === 90 ? " guide" : ""}` }));
    root.append(svg("text", { x: L - 5, y: Y(v) + 3.5, class: "lbl", "text-anchor": "end" }, `${v}%`));
  }
  for (const k of usageTicks(tmin, tmax)) {
    root.append(svg("line", { x1: X(k.t), x2: X(k.t), y1: H - B, y2: H - B + 4, class: "axis" }));
    root.append(svg("text", { x: X(k.t), y: H - 10, class: "lbl", "text-anchor": "middle" }, k.label));
  }
  // reset markers (finished weeks and the current reset)
  const resets = [...(Array.isArray(u.resets) ? u.resets : []), ...(cur && usageNum(cur.resets_at) !== null ? [cur.resets_at] : [])].filter((t) => usageNum(t) !== null && t >= tmin && t <= tmax);
  for (const t of [...new Set(resets)].sort((a, b) => b - a)) {   // newest first: the current reset gets the best spot
    root.append(svg("line", { x1: X(t), x2: X(t), y1: T, y2: H - B, class: "reset" }));
    const text = `↻ ${usageWhen(t)}`, c = place([{ x: X(t) + 3, y: T + 9, anchor: "start", text, size: 10 }, { x: X(t) - 3, y: T + 9, anchor: "end", text, size: 10 }]);
    if (c) lbl(c, "lbl resetlbl");
  }
  // gaps: hatched bands between the segments of the weekly line (and up to now when sampling has stopped)
  const sd = usageSegments(u.series && u.series.seven_day);
  const band = (t0, t1) => {
    if (t1 < tmin || t0 > tmax) return;
    const x0 = X(Math.max(t0, tmin)), x1 = X(Math.min(t1, tmax));
    root.append(svg("rect", { x: x0, y: T, width: Math.max(1, x1 - x0), height: H - T - B, class: "gapband", "data-tip": USAGE_GAP_TIP }));
  };
  for (let i = 1; i < sd.length; i++) band(sd[i - 1][sd[i - 1].length - 1][0], sd[i][0][0]);
  if (sd.length && u.now - sd[sd.length - 1][sd[sd.length - 1].length - 1][0] > (u.gap_secs || 1500)) band(sd[sd.length - 1][sd[sd.length - 1].length - 1][0], u.now);
  // lines: weekly (always), Opus/Sonnet weekly (only when reported), 5-hour (toggle)
  const g = svg("g", { "clip-path": "url(#usageclip)" }); root.append(g);
  const line = (key, cls) => {
    const segs = usageSegments(u.series && u.series[key]);
    for (const seg of segs) {
      // the points in view plus one on each side, so the line runs to the edges when zoomed (the clip path trims it)
      const ins = seg.map((p) => p[0] >= tmin && p[0] <= tmax + 1);
      const pts = seg.filter((p, i) => ins[i] || ins[i - 1] || ins[i + 1] || (p[0] < tmin && seg[i + 1] && seg[i + 1][0] > tmax) || (p[0] > tmax && seg[i - 1] && seg[i - 1][0] < tmin));
      if (!pts.length) continue;
      if (pts.length === 1) { g.append(svg("circle", { cx: X(pts[0][0]), cy: Y(pts[0][1]), r: 2.2, class: `udot ${cls}` })); continue; }
      g.append(svg("path", { d: pts.map((p, i) => `${i ? "L" : "M"}${X(p[0]).toFixed(1)} ${Y(p[1]).toFixed(1)}`).join(" "), class: `ul ${cls}` }));
    }
  };
  if (USAGE.h5) line("five_hour", "ul-5h");
  line("seven_day_opus", "ul-opus"); line("seven_day_sonnet", "ul-sonnet"); line("seven_day", "ul-week");
  // weeks that ended: a marker and "ended N%" ("ended ≥N%" when sampling stopped well before the reset)
  for (const w of usageWeeks(u)) {
    if (w.last_sample < tmin || w.last_sample > tmax) continue;
    const lvl = usageLevel(w.ended_pct), x = X(w.last_sample), y = Y(w.ended_pct), left = x > L + 90;
    root.append(svg("circle", { cx: x, cy: y, r: 4, class: `wkend ${lvl}`, "data-tip": `Week ended ${usageWhen(w.resets_at)}\nhighest value ${w.partial ? "at least " : ""}${Math.round(w.ended_pct)}%\n${lvl === "ok" ? "below 70%" : lvl === "warn" ? "70 to 89%" : "90% or more"}${w.partial ? "\nsamples stopped early" : ""}` }));
    const text = `${USAGE_GLYPH[lvl]} ended ${w.partial ? "≥" : ""}${Math.round(w.ended_pct)}%`, ly = Math.max(T + 22, y - 7);
    const a = { x: left ? x - 7 : x + 7, y: ly, anchor: left ? "end" : "start", text, size: 11, bold: true };
    lbl(place([a, { ...a, x: left ? x + 7 : x - 7, anchor: left ? "start" : "end" }, { ...a, y: ly + 12 }], true), `lbl wklbl ${lvl}`);
  }
  // now marker and pace
  if (cur && usageNum(cur.used) !== null && u.now >= tmin) {
    const pace = usagePace(u);
    if (pace !== null) {
      g.append(svg("line", { x1: X(u.now), y1: Y(cur.used), x2: X(cur.resets_at), y2: Y(pace), class: "pace" }));
    }
    const lvl = usageLevel(cur.used);
    if (u.now <= tmax) {
      root.append(svg("circle", { cx: X(u.now), cy: Y(cur.used), r: 4, class: `nowdot ${lvl}` }));
      const text = `now ${Math.round(cur.used)}%`, nx = X(u.now), ny = Y(cur.used), n = { x: nx - 7, y: ny - 7, anchor: "end", text, size: 11, bold: true };
      lbl(place([n, { ...n, y: ny + 15 }, { ...n, x: nx + 7, anchor: "start" }], true), `lbl nowlbl ${lvl}`);
    }
    if (pace !== null && cur.resets_at <= tmax) {
      const text = `pace ≈ ${pace}%`, py = Y(pace), p = { x: X(cur.resets_at) - 6, anchor: "end", text, size: 10 };
      const c = place((pace > 85 ? [13, 25, 37] : [-5, 13, -17]).map((dy) => ({ ...p, y: py + dy })));
      if (c) lbl(c, "lbl pacelbl");
    }
  }
  {
    const text = "10% weekly floor", fy = Y(90), f = { x: L + 4, anchor: "start", text, size: 10 };
    const c = place([{ ...f, y: fy - 3 }, { ...f, y: fy + 11 }]);
    if (c) lbl(c, "lbl guidelbl");
  }
  // tooltips: one transparent rect per x bucket (at most 240)
  const nb = Math.min(240, Math.max(1, Math.floor((W - L - R) / 4))), bw = (tmax - tmin) / nb;
  const lastIn = (key, t0, t1) => { let v = null; for (const seg of usageSegments(u.series && u.series[key])) for (const p of seg) if (p[0] >= t0 && p[0] < t1) v = p[1]; return v; };
  for (let i = 0; i < nb; i++) {
    const t0 = tmin + i * bw, t1 = t0 + bw, wkv = lastIn("seven_day", t0, t1), fh = lastIn("five_hour", t0, t1);
    const lines = [];
    if (wkv !== null) lines.push([`Weekly ${Math.round(wkv)}%`, fh !== null ? `5-hour ${Math.round(fh)}%` : ""].filter(Boolean).join(" · "));
    else lines.push("no samples");
    for (const [k, n] of [["seven_day_opus", "Opus"], ["seven_day_sonnet", "Sonnet"]]) { const v = lastIn(k, t0, t1); if (v !== null) lines.push(`${n} weekly ${Math.round(v)}%`); }
    root.append(svg("rect", { x: X(t0), y: T, width: Math.max(1, (W - L - R) / nb), height: H - T - B, class: "hit", "data-tip": `${usageWhen(t0 + bw / 2)}\n${lines.join("\n")}` }));
  }
  USAGE.view = { t0: tmin, t1: tmax, dmin: dom.dmin, dmax: dom.dmax, W, L, R, T, B, H, root };
  if (USAGE.drag) USAGE.drag.sel = null;
  usageSelSync();
  const kids = [];
  if (cur && cur.stale) kids.push(h("div", "usagenote warn small", `No new readings since ${hm(cur.updated)}: ${USAGE_API_ERR[cur.api_error] || USAGE_API_ERR.unknown}`));
  const rec = u.recorder;
  if (rec && rec.errors > 0 && USAGE_REC[rec.last_error]) kids.push(h("div", "usagenote warn small", `Recorder: ${USAGE_REC[rec.last_error]} (${rec.errors} error${rec.errors === 1 ? "" : "s"} since the dashboard started).`));
  box.replaceChildren(...kids, root);
  sum.textContent = usageSummary(u);
  upd.textContent = [cur ? `updated ${hm(cur.updated)}${USAGE_SRC[cur.source] ? ` · ${USAGE_SRC[cur.source]}` : ""}` : ""].filter(Boolean).join(" · ");
}

function redrawUsage() { if (USAGE.open && USAGE.data) renderUsage(USAGE.data); }
async function loadUsage() {
  if (VIEW !== "events" || document.hidden || !USAGE.open) return;
  const days = USAGE.weeks * 7;
  if (!USAGE.data) renderUsage({ state: "loading" });
  const d = await fetch(`/api/usage/history?days=${days}`, { credentials: "same-origin" }).then((r) => (r.ok ? r.json() : null)).catch(() => null);
  if (VIEW !== "events" || days !== USAGE.weeks * 7) return;
  renderUsage(d && typeof d.state === "string" ? d : null);
}
// One collapse button for the whole LLMs usage panel (Weekly usage and Token usage). `init` skips the token load:
// TOK isn't defined yet on the first call, and the token section loads itself when it is set up.
function setUsageOpen(open, init) {
  USAGE.open = open; store.set("niu.usage.open", open);
  $("usagepanel").classList.toggle("collapsed", !open);
  $("usagetoggle").setAttribute("aria-expanded", String(open));
  $("usagetoggle").setAttribute("aria-label", `${open ? "Collapse" : "Expand"} LLMs usage`);
  $("usagetoggle").textContent = open ? "▾" : "▸";
  if (open) { loadUsage(); if (!init) loadTokens(); }
}
function setUsageWeeks(n) {
  USAGE.weeks = n; store.set("niu.usage.weeks", n);
  for (const w of [2, 5, 10]) { $(`usage-w${w}`).classList.toggle("on", w === n); $(`usage-w${w}`).setAttribute("aria-pressed", String(w === n)); }
  USAGE.zoom = null; USAGE.data = null; loadUsage();
}
$("usagetoggle").onclick = () => setUsageOpen(!USAGE.open);
for (const w of [2, 5, 10]) $(`usage-w${w}`).onclick = () => setUsageWeeks(w);
$("usage5h").checked = USAGE.h5;
$("usage5h").onchange = () => { USAGE.h5 = $("usage5h").checked; store.set("niu.usage.5h", USAGE.h5); redrawUsage(); };
// Drag-to-zoom (brush). Listeners are bound once on the box: the SVG is replaced on every render. Hit-testing is geometric.
function usagePt(e) {
  const v = USAGE.view;
  if (!v) return null;
  const r = v.root.getBoundingClientRect();
  if (!(r.width > 0) || !(r.height > 0)) return null;
  const ux = (e.clientX - r.left) * v.W / r.width, uy = (e.clientY - r.top) * v.H / r.height;
  const cx = Math.min(v.W - v.R, Math.max(v.L, ux));
  return { t: v.t0 + (cx - v.L) / (v.W - v.L - v.R) * (v.t1 - v.t0), inside: ux >= v.L && ux <= v.W - v.R && uy >= v.T && uy <= v.H - v.B };
}
function usageSelSync() {   // draw (or move) the selection rectangle for the drag in progress
  const d = USAGE.drag, v = USAGE.view;
  if (!d || !d.active || !v) return;
  if (!d.sel) { d.sel = svg("rect", { class: "usagesel", y: v.T, height: v.H - v.T - v.B }); v.root.append(d.sel); }
  const px = (t) => v.L + (Math.min(v.t1, Math.max(v.t0, t)) - v.t0) / (v.t1 - v.t0) * (v.W - v.L - v.R);
  const x0 = px(Math.min(d.ta, d.tb)), x1 = px(Math.max(d.ta, d.tb));
  d.sel.setAttribute("x", x0); d.sel.setAttribute("width", Math.max(0, x1 - x0));
}
function usageDragEnd() {
  const d = USAGE.drag, box = $("usagegraph");
  USAGE.drag = null;
  if (!d) return;
  if (d.sel) d.sel.remove();
  box.classList.remove("usagedrag");
  if (d.active) { try { box.releasePointerCapture?.(d.id); } catch (_) { /* not captured */ } }
}
function usageZoomReset() {
  const had = document.activeElement === $("usagezoomreset");
  USAGE.zoom = null; redrawUsage();
  if (had) $(`usage-w${USAGE.weeks}`).focus();
}
{
  const box = $("usagegraph");
  box.addEventListener("pointerdown", (e) => {
    if (!USAGE.view || !e.isPrimary || e.button !== 0) return;
    const pt = usagePt(e);
    if (!pt || !pt.inside) return;
    USAGE.drag = { id: e.pointerId, cx0: e.clientX, ta: pt.t, tb: pt.t, active: false, sel: null };
  });
  box.addEventListener("pointermove", (e) => {
    const d = USAGE.drag;
    if (!d || e.pointerId !== d.id) return;
    if (e.buttons === 0) { usageDragEnd(); return; }   // the button was released outside the page
    if (!d.active) {
      if (Math.abs(e.clientX - d.cx0) < 6) return;
      d.active = true;
      box.setPointerCapture?.(d.id);
      TIP.hidden = true;
      box.classList.add("usagedrag");
    }
    const pt = usagePt(e);
    if (!pt) return;
    d.tb = pt.t; TIP.hidden = true;
    usageSelSync();
  });
  box.addEventListener("pointerup", (e) => {
    const d = USAGE.drag, v = USAGE.view;
    if (!d || e.pointerId !== d.id) return;
    if (!d.active || !v) { usageDragEnd(); return; }
    const pt = usagePt(e);
    if (pt) d.tb = pt.t;
    let a = Math.max(v.t0, Math.min(d.ta, d.tb)), b = Math.min(v.t1, Math.max(d.ta, d.tb));
    usageDragEnd();
    if ((b - a) / (v.t1 - v.t0) * (v.W - v.L - v.R) < 8) return;   // too small a selection
    if (b - a < USAGE_ZOOM_MIN) {
      const c = (a + b) / 2;
      a = c - USAGE_ZOOM_MIN / 2; b = c + USAGE_ZOOM_MIN / 2;
      if (a < v.dmin) { a = v.dmin; b = a + USAGE_ZOOM_MIN; }
      if (b > v.dmax) { b = v.dmax; a = b - USAGE_ZOOM_MIN; }
    }
    USAGE.zoom = { t0: a, t1: b };
    redrawUsage();
  });
  const cancel = (e) => { if (USAGE.drag && e.pointerId === USAGE.drag.id) usageDragEnd(); };
  box.addEventListener("pointercancel", cancel);
  box.addEventListener("lostpointercapture", cancel);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && USAGE.drag) usageDragEnd(); });
  box.addEventListener("dblclick", () => { if (USAGE.zoom) usageZoomReset(); });
  $("usagezoomreset").onclick = usageZoomReset;
}
setUsageOpen(USAGE.open, true);
for (const w of [2, 5, 10]) { $(`usage-w${w}`).classList.toggle("on", w === USAGE.weeks); $(`usage-w${w}`).setAttribute("aria-pressed", String(w === USAGE.weeks)); }
setInterval(loadUsage, 60000);
document.addEventListener("visibilitychange", () => { if (!document.hidden) loadUsage(); });

// ---------- Token usage panel (Events tab) ----------
// Numbers come from /api/usage/tokens (aggregates of Claude Code transcripts, read and stored by niu-dash). Everything is built with
// h()/svg() (textContent and setAttribute), every number is checked first, and class names come from fixed lists: a hostile payload
// can neither inject markup nor break the layout.
const TOK_KINDS = [["input", "input", "tk-in"], ["output", "output", "tk-out"], ["cache_write", "cache write", "tk-cw"], ["cache_read", "cache read", "tk-cr"]];
const TOK_METRICS = { total: ["Total", ["input", "output", "cache_write", "cache_read"]], output: ["Output", ["output"]],
  inout: ["Input + cache write", ["input", "cache_write"]], cache_read: ["Cache read", ["cache_read"]] };
const TOK_RANGES = { today: "Today", "7d": "7 days", "30d": "30 days" };
const FAMILY = ["opus", "sonnet", "haiku", "local", "other"];
const TOK_SOURCES = { all: "All", claude: "Claude", local: "Local" };
const TOK = { range: Object.hasOwn(TOK_RANGES, store.get("niu.tokens.range", "7d")) ? store.get("niu.tokens.range", "7d") : "7d",
  metric: Object.hasOwn(TOK_METRICS, store.get("niu.tokens.metric", "total")) ? store.get("niu.tokens.metric", "total") : "total",
  source: Object.hasOwn(TOK_SOURCES, store.get("niu.tokens.source", "all")) ? store.get("niu.tokens.source", "all") : "all",
  get open() { return USAGE.open; }, data: null, asof: 0, seq: 0, stale: false };
const TOK_MSG = {
  loading: "Loading…",
  empty: "No LLM usage found yet.",
  emptyLocal: "No local-model usage recorded yet (niu-agent / local_* tools).",
  disabled: "Token statistics are off (NIU_USAGE_TOKENS=0 or NIU_USAGE_COLLECT=0).",
  error: "Can't read token usage from the dashboard server.",
};
const tokNum = (v) => (typeof v === "number" && isFinite(v) && v > 0 ? v : 0);
const tokName = (v, d) => (typeof v === "string" && v ? v.slice(0, 80) : d);
const tokFmt = (n) => (n >= 1e9 ? `${(n / 1e9).toFixed(1)}G` : n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e4 ? `${Math.round(n / 1e3)}k` : n >= 1e3 ? `${(n / 1e3).toFixed(1)}k` : String(Math.round(n)));
const tokInt = (n) => Math.round(n).toLocaleString();
const tokPct = (p) => (p > 0 && p < 0.5 ? "<1%" : `${Math.round(p)}%`);
const tokVal = (row, metric) => TOK_METRICS[metric][1].reduce((a, k) => a + tokNum(row[k]), 0);
const tokFam = (f) => (FAMILY.includes(f) ? f : "other");
function tokRows(list, metric, nameKey) {
  const rows = (Array.isArray(list) ? list : []).filter((r) => r && typeof r === "object").map((r) => ({
    name: tokName(r[nameKey], "?"), family: tokFam(r.family), input: tokNum(r.input), output: tokNum(r.output), cache_write: tokNum(r.cache_write),
    cache_read: tokNum(r.cache_read), messages: tokNum(r.messages), sessions: tokNum(r.sessions) }));
  const folded = (r) => /^other \(\d+\)$/.test(r.name);
  rows.sort((a, b) => (folded(a) - folded(b)) || tokVal(b, metric) - tokVal(a, metric) || (a.name < b.name ? -1 : 1));
  const sum = rows.reduce((a, r) => a + tokVal(r, metric), 0);
  for (const r of rows) { r.v = tokVal(r, metric); r.share = sum > 0 ? (r.v / sum) * 100 : 0; r.total = tokVal(r, "total"); }
  return rows;
}
function tokBar(row, max, metric) {
  const root = svg("svg", { class: "tokbar", viewBox: "0 0 100 8", preserveAspectRatio: "none", role: "presentation", "aria-hidden": "true" });
  root.append(svg("rect", { x: 0, y: 0, width: 100, height: 8, class: "tk-bg" }));
  let x = 0;
  for (const [key, , cls] of TOK_KINDS) {
    if (!TOK_METRICS[metric][1].includes(key) || !(max > 0)) continue;
    const w = (tokNum(row[key]) / max) * 100;
    if (w > 0) { root.append(svg("rect", { x, y: 0, width: w, height: 8, class: cls })); x += w; }
  }
  return root;
}
function tokTip(row, what) {
  return [`${row.name} · ${tokPct(row.share)}`, `input ${tokFmt(row.input)} · output ${tokFmt(row.output)}`, `cache write ${tokFmt(row.cache_write)} · cache read ${tokFmt(row.cache_read)}`,
    what === "agent" ? `${tokInt(row.messages)} calls · ${tokInt(row.sessions)} session${row.sessions === 1 ? "" : "s"}` : `${row.family}`].join("\n");
}
function tokSwatch(cls) {
  const sw = svg("svg", { class: "lgsw", viewBox: "0 0 8 8", width: 8, height: 8, "aria-hidden": "true" });
  sw.append(svg("rect", { width: 8, height: 8, class: cls }));
  return sw;
}
function tokRankBlock(title, rows, metric, what) {
  const max = Math.max(0, ...rows.map((r) => r.v));
  const head = h("div", "tokrow tokhead small muted");
  head.append(h("span", "", what === "agent" ? "agent type" : "model"), h("span", ""), h("span", "r", "share"), h("span", "r", "total"), h("span", "r", "out"));
  const kids = [h("h3", "tokh", title), head];
  for (const r of rows) {
    const row = h("div", "tokrow", "", { "data-tip": tokTip(r, what) });
    const nm = h("span", "tokname", r.name);
    if (what === "agent" && r.sessions) nm.append(h("span", "muted small", ` ${tokInt(r.sessions)}`));
    row.append(nm, tokBar(r, max, metric), h("span", "r", tokPct(r.share)), h("span", "r", tokFmt(r.total)), h("span", "r", tokFmt(r.output)));
    kids.push(row);
  }
  if (!rows.length) kids.push(h("div", "empty small", "nothing in this range"));
  return kids;
}
function tokResearchRow() {
  const led = RESEARCH && RESEARCH.state === "ok" && RESEARCH.data && RESEARCH.data.ledger;
  if (!led || typeof led !== "object") return null;
  const days = Array.isArray(led.last_7_days) ? led.last_7_days : [];
  const pick = TOK.range === "today" ? [led.today] : days;
  const rows = pick.filter((d) => d && typeof d === "object");
  const tin = rows.reduce((a, d) => a + tokNum(d.tokens_in), 0), tout = rows.reduce((a, d) => a + tokNum(d.tokens_out), 0);
  const row = h("div", "tokrow tokrs muted small", "", { "data-tip": "Research bot (valkyrie-dispatch)\nSeparate population on the same plan: shown for reference, never included in the totals or shares above." });
  row.append(h("span", "tokname", "research bot"), h("span", "tokrsn", `valkyrie-dispatch (separate, not in totals): in ${tokFmt(tin)} · out ${tokFmt(tout)}${TOK.range === "30d" ? " · last 7 days only" : ""}`));
  const go = h("button", "ghost tiny", "→ Research tab");
  go.onclick = () => setView("research");
  row.append(go);
  return row;
}
function tokDaily(daily, range) {
  const days = (Array.isArray(daily) ? daily : []).filter((d) => d && typeof d === "object" && d.by_family && typeof d.by_family === "object");
  const kids = [h("h3", "tokh", range === "today" ? "Per hour (stacked by model family)" : "Per day (stacked by model family)")];
  if (!days.length) return kids.concat(h("div", "empty small", "no data"));
  const W = Math.max(60, days.length * 14), H = 56, bw = W / days.length;
  const max = Math.max(1, ...days.map((d) => FAMILY.reduce((a, f) => a + tokNum(d.by_family[f]), 0)));
  const root = svg("svg", { class: "tokdaily", viewBox: `0 0 ${W} ${H}`, preserveAspectRatio: "none", role: "img", "aria-label": "Tokens per " + (range === "today" ? "hour" : "day") + ", stacked by model family; see the numbers in the table below" });
  const defs = svg("defs");
  const hatch = svg("pattern", { id: "tokhatch", width: "4", height: "4", patternUnits: "userSpaceOnUse", patternTransform: "rotate(45)" });
  hatch.append(svg("rect", { width: 4, height: 4, class: "pat-sonnet-bg" }), svg("line", { x1: 0, y1: 0, x2: 0, y2: 4, class: "pat-sonnet-ln" }));
  const dots = svg("pattern", { id: "tokdots", width: "4", height: "4", patternUnits: "userSpaceOnUse" });
  dots.append(svg("rect", { width: 4, height: 4, class: "pat-haiku-bg" }), svg("circle", { cx: 2, cy: 2, r: 0.9, class: "pat-haiku-dot" }));
  defs.append(hatch, dots);
  root.append(defs);
  days.forEach((d, i) => {
    let y = H;
    const parts = [];
    for (const f of FAMILY) {
      const v = tokNum(d.by_family[f]);
      if (v <= 0) continue;
      const hh = (v / max) * (H - 4);
      y -= hh;
      root.append(svg("rect", { x: i * bw + 1, y, width: Math.max(1, bw - 2), height: hh, class: `fam-${f}` }));
      parts.push(`${f} ${tokFmt(v)}`);
    }
    root.append(svg("rect", { x: i * bw, y: 0, width: bw, height: H, class: "hit", "data-tip": `${tokName(d.label, "?")}\n${parts.length ? parts.join(" · ") : "no usage"}\ntotal ${tokFmt(FAMILY.reduce((a, f) => a + tokNum(d.by_family[f]), 0))}` }));
  });
  kids.push(root);
  const lg = h("div", "toklegend small muted");
  for (const f of FAMILY) { const it = h("span", "lgitem", f); it.prepend(tokSwatch(`fam-${f}`)); lg.append(it); }
  kids.push(lg);
  kids.push(h("div", "small muted", `${tokName(days[0].label, "")} → ${tokName(days[days.length - 1].label, "")}`));
  return kids;
}
function tokMatrix(m) {
  const det = h("details", "tokmatrix");
  det.append(h("summary", "", "Agent × model table"));
  if (!m || !Array.isArray(m.agents) || !Array.isArray(m.models) || !Array.isArray(m.total)) return det;
  const tbl = h("table", "rstable");
  const hr = h("tr");
  hr.append(h("th", "", "agent type"), ...m.models.map((x) => h("th", "", tokName(x, "?"))));
  const thead = h("thead");
  thead.append(hr);
  const tb = h("tbody");
  m.agents.forEach((a, i) => {
    const tr = h("tr");
    tr.append(h("td", "", tokName(a, "?")));
    const rowv = Array.isArray(m.total[i]) ? m.total[i] : [];
    m.models.forEach((_, j) => tr.append(h("td", "r", tokInt(tokNum(rowv[j])))));
    tb.append(tr);
  });
  tbl.append(thead, tb);
  det.append(tbl);
  return det;
}
function tokCounted() {
  const det = h("details", "tokcounted");
  det.append(h("summary", "", "What is counted"),
    h("p", "small", "Claude Code transcripts on this host (~/.claude/projects), for terminal and desktop sessions and their subagents. With All domains this includes projects outside the workspace."),
    h("p", "small", "niu-agent agent-mode transcripts (state folder ollama-claude/projects)."),
    h("p", "small", "Lite-mode calls of niu-agent and the local_* tools (state folder local-usage.jsonl, counts only)."),
    h("p", "small", "Cache reads dominate the totals: every call re-reads the whole conversation from the cache, so a long session adds many millions of cache-read tokens. Switch the metric to Output to see what the models wrote."),
    h("p", "small", "Not counted: sessions the dashboard did not observe (other devices, claude.ai chats, transcripts already deleted by Claude Code's cleanup, 30 days by default), niu-agent probe, Ollama calls made outside niu-agent, and the research bot, which is shown separately."),
    h("p", "small", "Local tokens cost no plan quota and are not part of the Weekly %."),
    h("p", "small", "Tokens are not the plan's limit units: the weekly % in Weekly usage is the authoritative measure."));
  return det;
}
function tokSummary(t, k) {
  const tot = tokNum(t.totals.total) || 0;
  const bits = [`Token usage, ${TOK_RANGES[TOK.range].toLowerCase()}: ${tokFmt(tot)} tokens`];
  if (tot > 0) bits[0] += ` (cache reads ${tokPct((tokNum(t.totals.cache_read) / tot) * 100)}, output ${tokPct((tokNum(t.totals.output) / tot) * 100)})`;
  const a = k.agents[0], m = k.models[0];
  if (a && m && tot > 0) bits.push(`Most by ${a.name} (${tokPct(a.share)}) and model ${m.name} (${tokPct(m.share)}) by ${TOK_METRICS[TOK.metric][0].toLowerCase()}.`);
  return bits.join(". ").replace(/\.\./g, ".").replace(/([^.])$/, "$1.");
}
function renderTokens(t) {
  const box = $("tokbody"), sum = $("toksum");
  TOK.data = t && (t.state === "ok" || t.state === "scanning") ? t : null;
  const note = (text, cls) => { box.replaceChildren(h("div", cls || "empty", text)); sum.textContent = ""; };
  if (t && t.state === "loading") return note(TOK_MSG.loading);
  const okShape = t && typeof t === "object" && t.totals && typeof t.totals === "object" && t.kpis && typeof t.kpis === "object" && Array.isArray(t.by_agent) && Array.isArray(t.by_model);
  if (!t || typeof t !== "object" || !["ok", "scanning", "empty", "disabled"].includes(t.state) || (["ok", "scanning"].includes(t.state) && !okShape)) { TOK.data = null; return note(TOK_MSG.error, "msg err"); }
  const cov = t.coverage && typeof t.coverage === "object" ? t.coverage : {};
  if (t.state === "empty" || t.state === "disabled") {
    if (t.state === "empty" && cov.scanning) return note(`Reading transcripts for the first time: ${tokInt(tokNum(cov.files_done))} of ${tokInt(tokNum(cov.files_total))} files`);
    return note(t.state === "empty" && TOK.source === "local" ? TOK_MSG.emptyLocal : TOK_MSG[t.state]);
  }
  const metric = TOK.metric;
  const agents = tokRows(t.by_agent, metric, "agent_type"), models = tokRows(t.by_model, metric, "model");
  const kids = [];
  if (t.state === "scanning") kids.push(h("div", "usagenote small", `Reading transcripts for the first time: ${tokInt(tokNum(cov.files_done))} of ${tokInt(tokNum(cov.files_total))} files. The numbers below are partial.`, { role: "status" }));
  if (TOK.stale) kids.push(h("div", "usagenote warn small", `Can't reach the dashboard server: showing the data as of ${hm(TOK.asof)}.`));
  if (tokNum(cov.corrupt_lines) > 0) kids.push(h("div", "usagenote warn small", `${tokInt(cov.corrupt_lines)} transcript line${cov.corrupt_lines === 1 ? "" : "s"} could not be read and ${cov.corrupt_lines === 1 ? "was" : "were"} skipped.`));
  const kp = h("div", "tokkpis");
  for (const r of ["today", "7d", "30d"]) {
    const v = t.kpis[r] && typeof t.kpis[r] === "object" ? t.kpis[r] : {};
    const loc = TOK.source === "all" ? tokNum(v.local) : 0;
    const stat = h("div", `stat tokkpi${r === TOK.range ? " on" : ""}`, "", { "data-tip": `${TOK_RANGES[r]}\ntotal ${tokInt(tokNum(v.total))} tokens\noutput ${tokInt(tokNum(v.output))} tokens${loc > 0 ? `\nlocal ${tokInt(loc)} tokens` : ""}` });
    stat.append(h("span", "small muted", TOK_RANGES[r]), h("b", "", tokFmt(tokNum(v.total))), h("span", "small muted", `output ${tokFmt(tokNum(v.output))}`));
    if (loc > 0) stat.append(h("span", "small muted", `local ${tokFmt(loc)}`));
    kp.append(stat);
  }
  kids.push(kp);
  const T = t.totals, bs = t.by_source && typeof t.by_source === "object" ? t.by_source : {};
  const bsc = bs.claude && typeof bs.claude === "object" ? tokNum(bs.claude.total) : 0, bsl = bs.local && typeof bs.local === "object" ? tokNum(bs.local.total) : 0;
  const split = TOK.source === "all" && bsl > 0 ? ` · Claude ${tokFmt(bsc)} · local ${tokFmt(bsl)}` : "";
  kids.push(h("div", "toktotals small", `${TOK_RANGES[TOK.range]} · in ${tokFmt(tokNum(T.input))} · out ${tokFmt(tokNum(T.output))} · cache write ${tokFmt(tokNum(T.cache_write))} · cache read ${tokFmt(tokNum(T.cache_read))} · ${tokInt(tokNum(T.messages))} calls · ${tokInt(tokNum(T.sessions))} sessions${split} · updated ${hm(TOK.asof || t.now)}${tokNum(cov.oldest) ? ` · data from ${usageDay(cov.oldest)}` : ""}`));
  const lg = h("div", "toklegend small muted");
  for (const [, label, cls] of TOK_KINDS) { const it = h("span", "lgitem", label); it.prepend(tokSwatch(cls)); lg.append(it); }
  kids.push(lg);
  kids.push(...tokRankBlock("By agent type (ranked by " + TOK_METRICS[metric][0].toLowerCase() + ")", agents, metric, "agent"));
  const rs = TOK.source === "local" ? null : tokResearchRow();
  if (rs) kids.push(rs);
  kids.push(...tokRankBlock("By model", models, metric, "model"));
  kids.push(...tokDaily(t.daily, t.range));
  kids.push(tokMatrix(t.matrix), tokCounted());
  box.replaceChildren(...kids);
  sum.textContent = tokSummary(t, { agents, models });
}
function redrawTokens() { if (TOK.open && TOK.data) renderTokens(TOK.data); }
async function loadTokens() {
  if (VIEW !== "events" || document.hidden || !TOK.open) return;
  const mine = ++TOK.seq, range = TOK.range, src = TOK.source, dom = $("domain").value;
  if (!TOK.data) renderTokens({ state: "loading" });
  const url = `/api/usage/tokens?range=${range}${dom ? `&domain=${encodeURIComponent(dom)}` : ""}${src !== "all" ? `&source=${src}` : ""}`;
  const d = await fetch(url, { credentials: "same-origin" }).then((r) => (r.ok ? r.json() : null)).catch(() => null);
  if (VIEW !== "events" || mine !== TOK.seq) return;
  if (d && typeof d.state === "string") { TOK.stale = false; TOK.asof = Date.now() / 1000; renderTokens(d); return; }
  if (TOK.data) { TOK.stale = true; renderTokens(TOK.data); } else renderTokens(null);
}
function setTokRange(r) {
  TOK.range = r; store.set("niu.tokens.range", r);
  for (const k of Object.keys(TOK_RANGES)) { $(`tok-r-${k}`).classList.toggle("on", k === r); $(`tok-r-${k}`).setAttribute("aria-pressed", String(k === r)); }
  TOK.data = null; loadTokens();
}
for (const r of Object.keys(TOK_RANGES)) $(`tok-r-${r}`).onclick = () => setTokRange(r);
function markTokSource() { for (const k of Object.keys(TOK_SOURCES)) { $(`tok-s-${k}`).classList.toggle("on", k === TOK.source); $(`tok-s-${k}`).setAttribute("aria-pressed", String(k === TOK.source)); } }
function setTokSource(v) {
  if (!Object.hasOwn(TOK_SOURCES, v)) return;
  TOK.source = v; store.set("niu.tokens.source", v); markTokSource();
  TOK.data = null; loadTokens();
}
for (const k of Object.keys(TOK_SOURCES)) $(`tok-s-${k}`).onclick = () => setTokSource(k);
markTokSource();
$("tokmetric").value = TOK.metric;
$("tokmetric").onchange = () => { const v = $("tokmetric").value; if (!Object.hasOwn(TOK_METRICS, v)) return; TOK.metric = v; store.set("niu.tokens.metric", v); redrawTokens(); };
loadTokens();
for (const r of Object.keys(TOK_RANGES)) { $(`tok-r-${r}`).classList.toggle("on", r === TOK.range); $(`tok-r-${r}`).setAttribute("aria-pressed", String(r === TOK.range)); }
setInterval(loadTokens, 60000);
document.addEventListener("visibilitychange", () => { if (!document.hidden) loadTokens(); });

// ---------- Research tab (valkyrie-dispatch monitor) ----------
// /api/research is already allowlisted by the server, but nothing from it is ever shown as HTML:
// every value goes through textContent or setAttribute, so a hostile payload cannot run or inject anything.
const RS_STUCK_SECS = 45 * 60;
const RS_REASON = { ok: "quota is fine", ok_conservative: "usage not readable, coarse check: conservative run", weekly_low: "weekly limit nearly used up",
  low_quota: "5-hour window too full", probe_warning: "usage not readable, probe warns of a limit", no_signal: "no usage signal", manual_cap: "manual-run cap reached",
  grace_expired: "gave up after the grace period", kill_switch: "stopped by the kill switch", session_cap: "daily Claude session cap reached" };
const RS_KIND = { standard: "standard", comprehensive: "overnight", delta: "delta", explore: "explore" };
const RS_KIND_LETTER = { standard: "S", comprehensive: "C", delta: "D", explore: "E" };
const RS_BRIEF_STATE = { scheduled: "brief due", in_progress: "brief being built", ready: "brief ready", empty: "brief empty (nothing new)" };
const RS_STALE = { comprehensive_missed: "overnight run did not happen", comprehensive_skipped: "overnight run was skipped",
  comprehensive_failed: "overnight run failed", comprehensive_deadline: "overnight run missed its deadline" };
const RS_DECISION = { run: "Run", run_conservative: "Run conservatively", retry: "Retry later", skip: "Skip" };
const RS_STATUS = { waiting_gate: "waiting for the gate", running: "running", succeeded: "succeeded", partial: "partial", failed: "failed",
  skipped_gate: "skipped (gate)", aborted_quota: "stopped: quota", pending: "pending", skipped: "skipped", timed_out: "timed out" };
const RS_ERROR = { timeout: "agent timed out", aborted_quota: "stopped: quota floor reached", max_turns: "hit the turn cap", max_budget: "hit the budget cap",
  structure_invalid: "output failed validation", no_sources: "no usable sources", rate_limited: "rate limited", auth: "Claude login problem",
  interrupted: "interrupted (service restart)", network: "network error", internal: "internal error" };
const RS_SOURCE = { usage_api: "usage API", probe: "probe", none: "no data", rate_limit_event: "rate-limit event" };
const RS_SOON = { hobby: "arrives in M2", fintrend: "arrives in M4" };
const RS_STATE_MSG = {
  loading: "Checking valkyrie-dispatch…",
  unreachable: "valkyrie-dispatch is not reachable. Is the container running? (docker compose ps in PERSONAL/valkyrie-dispatch)",
  unauthorized: "Token rejected: check API_TOKENS in valkyrie-dispatch and re-run niu-dash research-token.",
  too_powerful: "This token has a scope other than monitor. Remove it with niu-dash research-token --remove and store a monitor token.",
  incompatible: "valkyrie-dispatch has no compatible monitor endpoint (needs monitor.v1).",
  error: "Could not read the valkyrie-dispatch status.",
};
const RS_LEVEL = { succeeded: "ok", running: "ok", partial: "warn", waiting_gate: "warn", pending: "warn", skipped: "warn",
  failed: "bad", timed_out: "bad", aborted_quota: "bad", skipped_gate: "bad" };
const RS_DECISION_LEVEL = { run: "ok", run_conservative: "warn", retry: "bad", skip: "bad" };
const RS_BOT = { up: ["ok", "up"], stale: ["warn", "stale"], unknown: ["muted", "unknown"] };
const RS_OLLAMA = { up: ["ok", "up"], down: ["warn", "down"], disabled: ["muted", "off"], unknown: ["muted", "unknown"] };
const RS_DELIVERY = { bot: "posted by the bot", webhook: "posted by webhook" };
const RS_EXPLORE = { succeeded: ["ok", "succeeded"], quiet: ["ok", "quiet (nothing new)"], skipped_gate: ["warn", "skipped (gate)"],
  failed: ["bad", "failed"], aborted_quota: ["bad", "stopped: quota"] };
const RS_JOB_LETTER = { comprehensive: "C", delta: "D", brief: "B", explore: "E", reflect: "R", discovery: "W", checkins: "W" };
const RS_JOB_LABEL = { comprehensive: "Overnight run", delta: "Delta scout", brief: "Daily brief", explore: "Explore", reflect: "Reflection",
  discovery: "Discovery", checkins: "Check-ins" };
const RS_WEEKDAY = { mon: "Mon", tue: "Tue", wed: "Wed", thu: "Thu", fri: "Fri", sat: "Sat", sun: "Sun" };
const RS_REFLECT_OVERDUE_SECS = 26 * 3600;
const rsMap = (map, k, dflt) => (typeof k === "string" && Object.hasOwn(map, k) ? map[k] : dflt);   // hostile keys (__proto__ ...) never hit the prototype
const rsCount = (n) => (Number.isFinite(n) ? String(n) : "?");
const RS_DIGEST = { not_built: "not built", pending: "pending", held_quiet_hours: "held for quiet hours", sent: "sent", partial: "partly sent",
  failed: "failed", dead: "gave up", nothing_new: "nothing new", suppressed: "suppressed" };

const rsNow = () => Date.now() / 1000 + RS_OFFSET;
const hm = (ts) => ts ? new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false }) : "–";
const rsWhen = (ts) => !ts ? "–" : rsNow() - ts < 18 * 3600 ? hm(ts) : `${new Date(ts * 1000).toLocaleDateString([], { weekday: "short" })} ${hm(ts)}`;
const rsN = (n) => n == null ? "–" : n >= 1e6 ? `${(n / 1e6).toFixed(1)}M` : n >= 1e4 ? `${Math.round(n / 1e3)}k` : n >= 1e3 ? `${(n / 1e3).toFixed(1)}k` : String(n);
const rsUsd = (n) => n == null ? "–" : `$${n.toFixed(2)}`;
const rsUntil = (at) => at - rsNow() <= 0 ? "due now" : `in ${dur(at - rsNow())}`;
function h(tag, cls, text, attrs) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined && text !== null) e.textContent = String(text);
  for (const [k, v] of Object.entries(attrs || {})) e.setAttribute(k, v);
  return e;
}
const rsBadgeEl = (text, lvl) => h("span", `badge b-${lvl}`, text);

async function loadResearch(force) {
  let r = null;
  try {
    if (force) { const x = await POST("/api/research/refresh"); r = x.ok ? x.data : null; }
    else r = await fetch("/api/research", { credentials: "same-origin" }).then((x) => x.json());
  } catch { r = null; }
  if (!r || typeof r !== "object" || typeof r.state !== "string") r = { state: "error", data: null };
  RESEARCH = r;
  if (typeof r.server_now === "number") RS_OFFSET = r.server_now - Date.now() / 1000;
  renderResearch();
  clearTimeout(RS_TIMER);
  if (r.state === "loading") RS_TIMER = setTimeout(() => loadResearch(), 3000);
}

function renderResearch() {
  const r = RESEARCH || { state: "loading", data: null };
  $("tab-research").hidden = r.state === "disabled";
  rsBadge(r);
  if (r.state === "disabled") { if (VIEW === "research") setView("agents"); return; }
  if (VIEW !== "research") return;
  $("rsbody").classList.toggle("stale", !!r.stale); $("rsact").classList.toggle("stale", !!r.stale);
  $("rsstate").replaceChildren(...(r.data ? [] : [rsStateBlock(r)]));   // the logs moved to Activities: keep setup / error text on Status
  rsHead(r); rsGate(r); rsQuota(r); rsComp(r); rsExplore(r); rsSched(r); rsLanes(r); rsGateLog(r); rsLast(r); rsErrors(r);
}

// Tab badge: red = unreachable/unauthorized, a failed or quota-stopped last run, or a gate skip in the last day;
// amber = partial, waiting for the gate, conservative; green + pulse = a run is in progress.
function rsBadge(r) {
  const d = r.state === "ok" ? r.data : null;
  let lvl = "", pulse = false, why = "";
  if (r.state === "unreachable" || r.state === "unauthorized" || r.state === "too_powerful") { lvl = "bad"; why = RS_STATE_MSG[r.state]; }
  else if (d) {
    // the newest research-kind run (not explore), so a later explore run can never hide a failed overnight run
    const last = rsLastResearch(d), now = rsNow();
    const exRun = rsRuns(d).filter((x) => x.kind === "explore").sort((a, b) => (b.finished_at || b.started_at || 0) - (a.finished_at || a.started_at || 0))[0];
    const exBad = exRun && (exRun.status === "failed" || exRun.status === "aborted_quota");
    const skipped = (d.recent_runs || []).some((x) => x.status === "skipped_gate" && now - (x.finished_at || x.started_at || 0) < 86400);
    if (d.current_run?.status === "running") { lvl = "ok"; pulse = true; why = "A research run is in progress"; }
    else if (last && (last.status === "failed" || last.status === "aborted_quota")) { lvl = "bad"; why = `Last run ${RS_STATUS[last.status]}`; }
    else if (skipped) { lvl = "bad"; why = "A run was skipped by the gate in the last 24 h"; }
    else if (last?.status === "partial" || d.gate?.pending || (d.recent_runs || []).some((x) => x.status === "waiting_gate") || last?.mode === "conservative") {
      lvl = "warn"; why = "Partial, waiting for the gate or conservative mode";
    }
    else if (exBad) { lvl = "warn"; why = "The last explore run failed"; }   // explore never makes the badge red
    else if (d.companion?.bot === "stale") { lvl = "warn"; why = "The #hermes bot has no recent heartbeat"; }
    else if (d.companion?.ollama === "down") { lvl = "warn"; why = "Ollama is down"; }
  }
  const dot = $("rsdot");
  dot.hidden = !lvl;
  dot.className = `tabdot ${lvl ? `rs-${lvl}` : ""} ${pulse ? "pulse" : ""}`;
  $("tab-research").setAttribute("title", why);
}

function rsLastResearch(d) {
  return rsRuns(d).filter((x) => x.kind !== "explore").sort((a, b) => (b.finished_at || b.started_at || 0) - (a.finished_at || a.started_at || 0))[0] || null;
}

function rsHealth(r) {
  if (r.state === "ok") {
    const hl = r.data?.service?.health || {};
    const bad = hl.db === false || hl.migrations === false || hl.scheduler === false || hl.claude_auth === false;
    return bad ? ["partial", "valkyrie-dispatch degraded"] : ["up", "valkyrie-dispatch ok"];
  }
  return { loading: ["partial", "checking…"], not_configured: ["partial", "not configured"], unreachable: ["", "valkyrie-dispatch unreachable"],
    unauthorized: ["", "token rejected"], incompatible: ["", "incompatible version"], too_powerful: ["", "token scope too broad"] }[r.state] || ["", "error"];
}

function rsStateText(r) {
  return (typeof r.message === "string" && r.message) || RS_STATE_MSG[r.state] || RS_STATE_MSG.error;
}

function rsHead(r) {
  const d = r.data, [cls, label] = rsHealth(r), kids = [];
  const pill = h("span", `llmpill ${cls}`);
  pill.append(h("i"), h("span", "", label));
  kids.push(pill);
  if (d) {
    const sch = d.schedule || {};
    if (sch.next_slot_at) {
      const next = h("span", "", "", { "data-tip": [`Next scheduled run`, sch.next_slot_key, [sch.cron, sch.timezone].filter(Boolean).join(" ")].filter(Boolean).join("\n") });
      next.append(h("span", "", `Next run ${hm(sch.next_slot_at)}${RS_KIND[sch.next_slot_kind] ? ` (${RS_KIND[sch.next_slot_kind]})` : ""} · `), h("span", "", rsUntil(sch.next_slot_at), { id: "rscount" }));
      kids.push(next);
    }
    const b = d.brief;
    if (b && RS_BRIEF_STATE[b.state]) {
      const at = sch.brief_at || (b.published_at ? hm(b.published_at) : "");
      const stale = b.stale === true && RS_STALE[b.stale_reason];
      const items = b.state === "ready" && b.items != null ? ` · ${b.items} item${b.items === 1 ? "" : "s"}${b.updates ? `, ${b.updates} update${b.updates === 1 ? "" : "s"}` : ""}` : "";
      kids.push(h("span", stale ? "warnnote" : "", stale ? `brief stale: ${RS_STALE[b.stale_reason]}` : `${RS_BRIEF_STATE[b.state]}${at ? ` ${at}` : ""}${items}`,
        { "data-tip": `Daily brief${b.revision ? `\nrevision ${b.revision}` : ""}${b.stale === true && !stale ? "\nstale" : ""}` }));
    } else if (sch.brief_at) kids.push(h("span", "muted", `brief at ${sch.brief_at}`));
    if (d.companion) kids.push(h("span", "muted", `companion ${rsMap(RS_BOT, d.companion.bot, ["muted", "?"])[1]}`));   // details are in the Companion card
    if (d.explore) kids.push(h("span", "muted", `explore ${rsCount(d.explore.posted_today)} posted today`));
    if (sch.enabled === false) kids.push(rsBadgeEl("Scheduler off", "warn"));
    if (sch.manual_runs_today != null && sch.manual_runs_per_day != null) kids.push(h("span", "muted", `Manual runs today ${sch.manual_runs_today}/${sch.manual_runs_per_day}`));
    if (r.state !== "ok") kids.push(h("span", "warnnote", `${rsStateText(r)}${r.ok_at ? ` Showing data as of ${hm(r.ok_at)}.` : ""}`));
    else if (r.stale) kids.push(h("span", "warnnote", `Data as of ${hm(r.ok_at)}`));
  }
  $("rshead").replaceChildren(...kids);
  $("rsupd").textContent = r.fetched_at ? `updated ${hhmmss(r.fetched_at)}` : "";
}

function rsTick() {
  const d = RESEARCH?.data;
  if (!d) return;
  const c = document.getElementById("rscount");
  if (c && d.schedule?.next_slot_at) c.textContent = rsUntil(d.schedule.next_slot_at);
  const e = document.getElementById("rselapsed");
  if (e && d.current_run?.started_at) e.textContent = dur(rsNow() - d.current_run.started_at);
}

// ----- gate card: the decision, the floors as text and the pending gate. The Claude limit meters live in the LLMs card. -----
function rsGate(r) {
  if (VIEW !== "research") return;
  const box = $("rsgate"), d = r?.data;
  if (!d) { box.replaceChildren(); return; }
  const g = d.gate || {}, p = g.preview, kids = [h("h3", "", "Gate")];
  if (p) {
    const line = h("div", "rsline");
    line.append(rsBadgeEl(RS_DECISION[p.decision] || "unknown", RS_DECISION_LEVEL[p.decision] || "warn"), h("span", "", ` ${RS_REASON[p.reason] || ""}`));
    kids.push(line);
    const f = p.floors || {}, e = p.expected_run_pct || 0, pct = (v) => v == null ? "?" : `${v}%`;
    kids.push(h("div", "rsline", `gate floors: 5-hour ≥ ${pct(f.five_hour_min_remaining_pct)} · weekly ≥ ${pct(f.weekly_min_remaining_pct)} remaining`,
      { "data-tip": `Gate floors\n5-hour: stop below ${pct(f.five_hour_min_remaining_pct)} left (plus ${e}% for the run)\nWeekly: stop below ${pct(f.weekly_min_remaining_pct)} left\nAbort a running run below ${pct(f.abort_remaining_pct)} left` }));
    kids.push(h("div", "rsline muted", `${RS_SOURCE[p.source] || "unknown"} · checked ${p.checked_at ? `${dur(rsNow() - p.checked_at)} ago` : "never"}`));
  } else {
    kids.push(h("div", "rsline muted", "No gate preview from valkyrie-dispatch yet."));
  }
  const pend = g.pending;
  if (pend) {
    kids.push(h("div", "rsline", `Waiting for the gate: attempt ${pend.attempts ?? "?"} of ${pend.max_attempts ?? "?"}${RS_KIND[pend.kind] ? ` (${RS_KIND[pend.kind]} run)` : ""}, next check ${hm(pend.next_check_at)}` +
      `, gives up at ${hm(pend.grace_until)}${pend.last_reason ? ` (${RS_REASON[pend.last_reason] || ""})` : ""}`));
  }
  box.replaceChildren(...kids);
}

// ----- gate log: the recent gate checks, linked to the gate dots in the lanes graph through a shared data-gi index -----
const RS_GL = { dots: new Map(), rows: new Map(), list: null, cur: null };
const RS_DOT_R = 4, RS_DOT_R_HL = 7;
function rsGateHl(gi, scroll) {
  const unmark = (k) => {
    RS_GL.rows.get(k)?.classList.remove("hl");
    const dot = RS_GL.dots.get(k);
    if (dot) { dot.classList.remove("hl"); dot.setAttribute("r", RS_DOT_R); }
  };
  if (RS_GL.cur != null) unmark(RS_GL.cur);
  RS_GL.cur = gi;
  if (gi == null) return;
  const row = RS_GL.rows.get(gi), dot = RS_GL.dots.get(gi);
  if (row) row.classList.add("hl");
  if (dot) { dot.classList.add("hl"); dot.setAttribute("r", RS_DOT_R_HL); }
  const list = RS_GL.list;
  if (scroll && row && list) {  // scroll the list itself, never the page
    const top = row.offsetTop, bottom = top + row.offsetHeight;
    if (top < list.scrollTop) list.scrollTop = top;
    else if (bottom > list.scrollTop + list.clientHeight) list.scrollTop = bottom - list.clientHeight;
  }
}
function rsGateRows(d) {  // [{g, gi}] newest first; gi = the position in the data, the same index the dots carry
  return (d.gate?.recent || []).slice(0, 12).map((g, gi) => ({ g, gi })).sort((a, b) => (b.g.taken_at || 0) - (a.g.taken_at || 0) || a.gi - b.gi);
}
for (const [id, scroll] of [["rslanes", true], ["rsgatelog", false]]) {
  const box = $(id), idx = (e) => e.target?.closest?.("[data-gi]")?.getAttribute("data-gi") ?? null;
  for (const t of ["pointerover", "focusin"]) box.addEventListener(t, (e) => { const gi = idx(e); if (gi != null) rsGateHl(gi, scroll); });
  for (const t of ["pointerout", "focusout"]) box.addEventListener(t, (e) => { if (idx(e) != null && !e.relatedTarget?.closest?.("[data-gi]")) rsGateHl(null); });
  box.addEventListener("pointerleave", () => rsGateHl(null));
}
function rsGateLog(r) {
  const box = $("rsgatelog"), d = r.data;
  RS_GL.rows = new Map(); RS_GL.list = null; RS_GL.cur = null;
  if (!d) { box.replaceChildren(); return; }
  const kids = [h("h3", "", "Gate log")], rows = rsGateRows(d);
  if (!rows.length) { box.replaceChildren(...kids, h("div", "rsline muted", "No gate checks recorded yet.")); return; }
  const list = h("div", "rsgl", "", { role: "list" });
  for (const { g, gi } of rows) {
    const row = h("div", "rsgrow", "", { "data-gi": String(gi), tabindex: "0", role: "listitem" });
    row.append(h("span", "gt", rsWhen(g.taken_at)), rsBadgeEl(RS_DECISION[g.decision] || "?", RS_DECISION_LEVEL[g.decision] || "warn"),
      h("span", "gr", g.reason ? RS_REASON[g.reason] || "" : ""), h("span", "gp", `5h ${g.five_hour_used ?? "–"}% · wk ${g.seven_day_used ?? "–"}%`),
      h("span", "gs muted", RS_SOURCE[g.source] || ""));
    RS_GL.rows.set(String(gi), row);
    list.append(row);
  }
  RS_GL.list = list;
  box.replaceChildren(...kids, list);
}

// ----- quota card -----
function rsQuota(r) {
  const box = $("rsquota"), d = r.data;
  if (!d) { box.replaceChildren(); return; }
  const t = d.ledger?.today, kids = [h("h3", "", "Quota today")];
  if (t) {
    const kv = h("dl", "rskv");
    for (const [k, v] of [["Runs", t.runs], ["Sessions", t.sessions], ["Probes", t.probes], ["Web searches", t.web_searches],
      ["Tokens in", rsN(t.tokens_in)], ["Tokens out", rsN(t.tokens_out)]]) kv.append(h("dt", "", k), h("dd", "", v ?? "–"));
    kv.append(h("dt", "", "≈ USD"), h("dd", "", rsUsd(t.equiv_usd), { "data-tip": "API-equivalent estimate\nWhat this usage would cost on the pay-as-you-go API; it is not a bill." }));
    kids.push(kv);
  }
  const days = (d.ledger?.last_7_days || []).slice(-7);
  if (days.length) {
    const W = 140, H = 44, max = Math.max(0.01, ...days.map((x) => x.equiv_usd || 0)), bw = W / 7;
    const sv = svg("svg", { class: "spark", width: "100%", height: H, viewBox: `0 0 ${W} ${H}`, preserveAspectRatio: "none", role: "img", "aria-label": "Daily API-equivalent cost, last 7 days" });
    days.forEach((x, i) => {
      const bh = Math.max(1, ((x.equiv_usd || 0) / max) * (H - 4));
      const bar = svg("rect", { class: "bar-mini", x: i * bw + 2, y: H - bh, width: bw - 4, height: bh, rx: 1 });
      bar.setAttribute("data-tip", `${x.day || "?"}\n${rsUsd(x.equiv_usd)} · ${x.runs ?? 0} run(s)`);
      sv.append(bar);
    });
    kids.push(sv, h("div", "rsline muted", "≈ USD per day, last 7 days (API-equivalent estimate)"));
  }
  const wk = d.gate?.preview?.floors?.weekly_min_remaining_pct ?? 10;
  kids.push(h("div", "rsline muted", `The bot stops when less than ${wk}% of the weekly limit remains.`));
  box.replaceChildren(...kids);
}

// ----- companion card (#hermes bot and learning loop), explore card, and the next-24-hours strip -----
function rsPill(map, k) {
  const [lvl, label] = rsMap(map, k, ["warn", "?"]);
  return rsBadgeEl(label, lvl);
}
function rsRow(kv, name, ...cells) { kv.append(h("dt", "", name)); const dd = h("dd"); dd.append(...cells.map((c) => (typeof c === "string" ? h("span", "", c) : c))); kv.append(dd); }
function rsComp(r) {
  const box = $("rscomp"), d = r.data;
  if (!d) { box.replaceChildren(); return; }
  const c = d.companion, kids = [h("h3", "", "Companion (#hermes)")];
  if (!c) { box.replaceChildren(...kids, h("div", "rsline muted", "Companion not available in this valkyrie-dispatch version.")); return; }
  const kv = h("dl", "rskv"), waiting = (c.pending_proposals || 0) + (c.candidate_notes || 0) > 0;
  rsRow(kv, "Bot", rsPill(RS_BOT, c.bot), h("span", "muted", " Ollama "), rsPill(RS_OLLAMA, c.ollama));
  rsRow(kv, "Brief delivery", rsMap(RS_DELIVERY, c.brief_delivery, "?"));
  rsRow(kv, "Feedback, last 7 days", `${rsCount(c.feedback_7d)} (${rsCount(c.feedback_7d_chat)} from chat)`);
  rsRow(kv, "Waiting in #hermes", waiting ? rsBadgeEl(`${rsCount(c.pending_proposals)} proposals · ${rsCount(c.candidate_notes)} notes`, "warn")
    : `${rsCount(c.pending_proposals)} proposals · ${rsCount(c.candidate_notes)} notes`);
  rsRow(kv, "Active notes", rsCount(c.active_notes));
  const reflectOn = (d.schedule?.jobs || []).some((j) => j.job === "reflect" && j.enabled === true);
  const overdue = reflectOn && c.last_reflect_at && rsNow() - c.last_reflect_at > RS_REFLECT_OVERDUE_SECS;
  rsRow(kv, "Last reflection", c.last_reflect_at ? rsWhen(c.last_reflect_at) : "never", ...(overdue ? [rsBadgeEl("overdue", "warn")] : []));
  if (c.last_discovery_at) rsRow(kv, "Last discovery", rsWhen(c.last_discovery_at));   // null = an older server, or none yet
  kids.push(kv, h("div", "rsline muted", "Proposals, notes and picks are reviewed in #hermes on Discord."));
  box.replaceChildren(...kids);
}
function rsExplore(r) {
  const box = $("rsexplore"), d = r.data;
  if (!d) { box.replaceChildren(); return; }
  const e = d.explore, kids = [h("h3", "", "Explore")];
  if (!e) { box.replaceChildren(...kids, h("div", "rsline muted", "Explore is off (EXPLORE_ENABLED) or not available in this version.")); return; }
  const line = h("div", "rsline");
  line.append(h("span", "", `Last run ${e.last_run_at ? rsWhen(e.last_run_at) : "never"} `));
  if (e.last_status != null) line.append(rsPill(RS_EXPLORE, e.last_status));
  if (e.last_error) line.append(h("span", "msg err", ` ${rsMap(RS_ERROR, e.last_error, "error")}`));
  kids.push(line,
    h("div", "rsline", `Today: ${rsCount(e.queries_today)} queries → ${rsCount(e.results_today)} results → ${rsCount(e.items_today)} picks → ${rsCount(e.posted_today)} posted`),
    h("div", "rsline", `7 days: ${rsCount(e.likes_7d)} liked, ${rsCount(e.dislikes_7d)} disliked`),
    h("div", "rsline muted", `Topic ideas from picks, 30 days: ${rsCount(e.topic_proposals_30d)}`));
  box.replaceChildren(...kids);
}
function rsSlotsFwd(sch, t0, t1) {  // upcoming research slot instants from the cron hours and the next slot: [{t, first}]
  const out = [];
  if (!sch?.next_slot_at) return out;
  const m = /^[0-9]+ ([0-9,]+) \* \* \*$/.exec(sch.cron || "");
  const hours = m ? [...new Set(m[1].split(",").map(Number))].filter((n) => n >= 0 && n < 24).sort((a, b) => a - b) : [];
  const k = /T([0-9]{2}):/.exec(sch.next_slot_key || ""), idx = k ? hours.indexOf(Number(k[1])) : -1;
  const gaps = idx >= 0 ? hours.map((_, i) => ((hours[(i + 1) % hours.length] - hours[i] + 24) % 24) || 24) : [12];
  let t = sch.next_slot_at, i = idx >= 0 ? idx : 0;
  for (let n = 0; n < 40 && t <= t1; n++) {
    if (t >= t0 - 60) out.push({ t, first: n === 0 });
    t += gaps[i] * 3600;
    i = (i + 1) % gaps.length;
  }
  return out;
}
function rsSched(r) {
  if (TIP.dataset.owner === "rssched") TIP.hidden = true;
  const box = $("rssched"), d = r.data;
  box.replaceChildren();
  if (!d) return;
  const sch = d.schedule || {}, jobs = Array.isArray(sch.jobs) ? sch.jobs : null;
  const t0 = rsNow(), t1 = t0 + 24 * 3600, W = Math.max(640, (box.clientWidth || 900) - 20), H = 44, PAD = 14;
  const x = (t) => PAD + ((Math.min(Math.max(t, t0), t1) - t0) / (t1 - t0)) * (W - 2 * PAD);
  const root = svg("svg", { width: W, height: H, viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "Scheduled jobs, next 24 hours" });
  root.append(svg("rect", { x: PAD, y: 4, width: W - 2 * PAD, height: 22, class: "lane", rx: 3 }));
  for (let tk = Math.ceil(t0 / 10800) * 10800; tk < t1; tk += 10800) {   // a tick every 3 hours, on the clock
    root.append(svg("line", { x1: x(tk), x2: x(tk), y1: 4, y2: 30, class: "axis" }), svg("text", { x: x(tk), y: H - 4, class: "lbl", "text-anchor": "middle" }, hm(tk)));
  }
  root.append(svg("line", { x1: x(t0), x2: x(t0), y1: 2, y2: 30, class: "nowline" }));
  const mark = (t, letter, tip, off) => {
    // a plain marker: not focusable and no hover tip (the label is for screen readers only)
    const c = svg("circle", { cx: x(t), cy: 15, r: 9, class: `rsmark${off ? " off" : ""}`, role: "img", "aria-label": tip.replace(/\n/g, ", ") });
    root.append(c, svg("text", { x: x(t), y: 18.5, class: `rsmarktxt${off ? " off" : ""}`, "text-anchor": "middle", "pointer-events": "none" }, letter));
  };
  const tz = sch.timezone ? ` (${sch.timezone})` : "", notes = [];
  if (jobs) {
    const later = new Map();   // jobs outside the window, grouped by their next time
    for (const j of jobs) {
      const label = rsMap(RS_JOB_LABEL, j.job, "?"), off = j.enabled !== true;
      if (j.next_at != null && j.next_at <= t1) {
        mark(j.next_at, rsMap(RS_JOB_LETTER, j.job, "?"), [label + (off ? " · off" : ""), `${j.at || hm(j.next_at)}${tz} · ${rsUntil(j.next_at)}`].join("\n"), off);
      } else {
        const key = `${j.next_at ?? "none"}|${j.at ?? ""}|${j.weekday ?? ""}|${off}`;
        if (!later.has(key)) later.set(key, { j, names: [] });
        later.get(key).names.push(label);
      }
    }
    for (const { j, names } of later.values()) {
      const when = `${j.weekday ? `${rsMap(RS_WEEKDAY, j.weekday, "?")} ` : ""}${j.at || (j.next_at ? hm(j.next_at) : "?")}`;
      notes.push(`${names.map((n, i) => (i ? n.toLowerCase() : n)).join(" + ")}: ${when}${j.next_at ? ` (${rsUntil(j.next_at)})` : ""}${j.enabled === true ? "" : " · off"}`);
    }
  } else {
    for (const { t, first } of rsSlotsFwd(sch, t0, t1)) {
      const kind = first ? rsMap(RS_KIND, sch.next_slot_kind, "research") : "research";
      mark(t, first ? rsMap(RS_KIND_LETTER, sch.next_slot_kind, "S") : "S", `${kind[0].toUpperCase()}${kind.slice(1)} run\n${hm(t)}${tz} · ${rsUntil(t)}`, false);
    }
    if (sch.next_brief_at && sch.next_brief_at <= t1) mark(sch.next_brief_at, "B", `Daily brief\n${sch.brief_at || hm(sch.next_brief_at)}${tz} · ${rsUntil(sch.next_brief_at)}`, false);
    notes.push("Explore, reflection and discovery times need a newer valkyrie-dispatch.");
  }
  box.append(h("h3", "", "Next 24 h"), root);
  for (const n of notes) box.append(h("div", "rsline muted", n));
}

// ----- 36 h lanes -----
function rsRuns(d) {
  const m = new Map();
  [...(d.recent_runs || []), d.last_run, d.current_run].forEach((run, i) => { if (run) m.set(run.id || `anon${i}`, run); });
  return [...m.values()];
}
function rsSlots(sch, t0, t1) {  // earlier slot instants, derived from the cron hours and the next slot
  const out = [];
  if (!sch?.next_slot_at) return out;
  const m = /^[0-9]+ ([0-9,]+) \* \* \*$/.exec(sch.cron || "");
  const hours = m ? [...new Set(m[1].split(",").map(Number))].filter((n) => n >= 0 && n < 24).sort((a, b) => a - b) : [];
  const k = /T([0-9]{2}):/.exec(sch.next_slot_key || ""), idx = k ? hours.indexOf(Number(k[1])) : -1;
  const gaps = idx >= 0 ? hours.map((_, i) => ((hours[i] - hours[(i - 1 + hours.length) % hours.length] + 24) % 24) || 24) : [12];
  let t = sch.next_slot_at, i = idx >= 0 ? idx : 0;
  for (let n = 0; n < 40 && t > t0; n++) {
    if (t <= t1) out.push(t);
    t -= gaps[i] * 3600;
    i = (i - 1 + gaps.length) % gaps.length;
  }
  return out;
}
function rsLanes(r) {
  if (TIP.dataset.owner === "rslanes") TIP.hidden = true;  // rebuilt from scratch
  RS_GL.dots = new Map(); RS_GL.cur = null;
  const box = $("rslanes"), d = r.data;
  box.replaceChildren();
  if (!d) return;
  const feat = d.service?.features || { runs: false, agents: [] };
  const t1 = rsNow(), t0 = t1 - 36 * 3600;
  const lanes = [{ key: "gate", label: "gate" }, ...["general", "hobby", "fintrend"].map((a) => ({ key: a, label: a, off: !(feat.agents || []).includes(a) })),
    { key: "explore", label: "explore" }];
  const LH = 22, LW = 110, W = Math.max(640, (box.clientWidth || 900) - 20), H = lanes.length * LH + 26;
  const x = (t) => LW + ((Math.min(Math.max(t, t0), t1) - t0) / (t1 - t0)) * (W - LW - 10);
  const root = svg("svg", { width: W, height: H, viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "Research run lanes, last 36 hours" });
  for (let tk = Math.ceil(t0 / 3600) * 3600; tk < t1; tk += 3600) {
    const dt = new Date(tk * 1000);
    if (dt.getMinutes() !== 0 || dt.getHours() % 6 !== 0) continue;
    root.append(svg("line", { x1: x(tk), x2: x(tk), y1: 0, y2: H - 20, class: "axis" }), svg("text", { x: x(tk), y: H - 6, class: "lbl", "text-anchor": "middle" }, hm(tk)));
  }
  for (const st of rsSlots(d.schedule, t0, t1)) root.append(svg("line", { x1: x(st), x2: x(st), y1: 0, y2: H - 20, class: "axis slotline" }));
  root.append(svg("line", { x1: x(t1), x2: x(t1), y1: 0, y2: H - 20, class: "nowline" }), svg("text", { x: x(t1), y: H - 6, class: "lbl", "text-anchor": "end" }, "now"));
  lanes.forEach((l, i) => {
    const y = i * LH + 2;
    root.append(svg("rect", { x: LW, y, width: W - LW - 10, height: LH - 4, class: "lane", rx: 3 }));
    root.append(svg("text", { x: 0, y: y + 14, class: l.off ? "muted" : "" }, trunc(l.off ? `${l.label} · ${RS_SOON[l.key] || "not enabled"}` : l.label, 20)));
  });
  const laneOf = (k) => lanes.findIndex((l) => l.key === k);
  for (const run of rsRuns(d)) {
    if (run.kind === "explore") {   // explore runs have no agent rows: one run-level bar in the explore lane
      if (!run.started_at) continue;
      const end = run.finished_at || (run.status === "running" ? t1 : run.started_at);
      if (end < t0) continue;
      const lvl = RS_LEVEL[run.status] || "warn", li = laneOf("explore");
      const bar = svg("rect", { x: x(run.started_at), y: li * LH + 3, width: Math.max(3, x(end) - x(run.started_at)), height: LH - 6, rx: 2,
        class: `bar-sub rs-${lvl} rs-explorebar ${run.status === "running" ? "running" : ""}` });
      bar.setAttribute("data-tip", [`explore · ${RS_STATUS[run.status] || run.status}`, `${hhmmss(run.started_at)} → ${run.finished_at ? hhmmss(run.finished_at) : run.status === "running" ? "running" : "?"}`,
        run.slot_key ? `slot ${run.slot_key}` : ""].filter(Boolean).join("\n"));
      root.append(bar);
      const tx = Math.max(x(end), x(run.started_at) + 3) + 3;
      if (tx + 8 < W) root.append(svg("text", { x: tx, y: li * LH + 3 + (LH - 6) / 2 + 3.5, class: "rs-kindtag", "pointer-events": "none" }, RS_KIND_LETTER.explore));
      continue;
    }
    for (const a of run.agents || []) {
      const li = laneOf(a.agent_type);
      if (li < 0 || !a.started_at) continue;
      const end = a.finished_at || (a.status === "running" ? t1 : a.started_at);
      if (end < t0) continue;
      const lvl = RS_LEVEL[a.status] || "warn", running = a.status === "running";
      const bar = svg("rect", { x: x(a.started_at), y: li * LH + 3, width: Math.max(3, x(end) - x(a.started_at)), height: LH - 6, rx: 2,
        class: `bar-sub rs-${lvl} ${running ? "running" : ""}` });
      bar.setAttribute("data-tip", [`${a.agent_type}${RS_KIND[run.kind] ? ` (${RS_KIND[run.kind]})` : ""} · ${RS_STATUS[a.status] || a.status}`,
        `${hhmmss(a.started_at)} → ${a.finished_at ? hhmmss(a.finished_at) : running ? "running" : "?"}` +
        `${a.num_turns != null ? ` · ${a.num_turns}${a.max_turns != null ? `/${a.max_turns}` : ""} turns` : ""}` +
        `${a.web_searches != null ? ` · ${a.web_searches}${a.max_web_searches != null ? `/${a.max_web_searches}` : ""} searches` : ""}`,
        run.slot_key ? `slot ${run.slot_key}` : "", a.error_code ? RS_ERROR[a.error_code] || a.error_code : ""].filter(Boolean).join("\n"));
      root.append(bar);
      const tx = Math.max(x(end), x(a.started_at) + 3) + 3;  // the run kind as a one-letter tag right after the bar
      if (RS_KIND_LETTER[run.kind] && tx + 8 < W) root.append(svg("text", { x: tx, y: li * LH + 3 + (LH - 6) / 2 + 3.5, class: "rs-kindtag", "pointer-events": "none" }, RS_KIND_LETTER[run.kind]));
    }
  }
  for (const { g, gi } of rsGateRows(d)) {
    if (!g.taken_at || g.taken_at < t0) continue;
    const dot = svg("circle", { cx: x(g.taken_at), cy: 2 + (LH - 4) / 2, r: RS_DOT_R, class: `rs-dot rs-${RS_DECISION_LEVEL[g.decision] || "warn"}`,
      "data-gi": String(gi), tabindex: "0", role: "img", "aria-label": `Gate check ${gi + 1}: ${RS_DECISION[g.decision] || "unknown"} at ${hm(g.taken_at)}` });
    dot.setAttribute("data-tip", `gate · ${RS_DECISION[g.decision] || "?"}\n${hhmmss(g.taken_at)}${g.reason ? ` · ${RS_REASON[g.reason] || ""}` : ""}\n5h ${g.five_hour_used ?? "–"}% · wk ${g.seven_day_used ?? "–"}%`);
    RS_GL.dots.set(String(gi), dot);
    root.append(dot);
  }
  box.append(root);
  if (!feat.runs) box.append(h("div", "rsline muted", "Run history arrives with valkyrie-dispatch M2."));
  else if (!rsRuns(d).some((run) => (run.agents || []).some((a) => a.started_at && (a.finished_at || a.started_at) >= t0)) && !rsRuns(d).some((run) => run.kind === "explore" && run.started_at && (run.finished_at || run.started_at) >= t0)) box.append(h("div", "rsline muted", "No agent runs in the last 36 hours."));
}

// ----- last run + errors -----
function rsStateBlock(r) {
  if (r.state === "not_configured") {
    const box = h("div", "rssetup");
    box.append(h("div", "", "No monitor token is set up yet. One time, in three steps:"));
    const ol = h("ol");
    for (const [a, code, b] of [["In PERSONAL/valkyrie-dispatch run ", "docker compose exec app rd gen-token --name niu-dash --scope monitor", "."],
      ["Add the printed niu-dash:monitor:… entry to API_TOKENS in valkyrie-dispatch's own env file yourself, then run ", "docker compose up -d", "."],
      ["On this machine run ", "niu-dash research-token", " in a terminal and paste the token."]]) {
      const li = h("li");
      li.append(h("span", "", a), h("code", "", code), h("span", "", b));
      ol.append(li);
    }
    box.append(ol);
    return box;
  }
  return h("div", `rsline ${r.state === "loading" ? "muted" : "msg err"}`, rsStateText(r));
}

function rsDigest(dg) {
  if (!dg) return "";
  const n = dg.sections_total != null ? ` ${dg.sections_sent ?? 0}/${dg.sections_total}` : "";
  if (dg.status === "held_quiet_hours") return `digest held for quiet hours${dg.next_attempt_at ? ` until ${hm(dg.next_attempt_at)}` : ""}`;
  if (dg.status === "failed") return `digest failed${dg.last_http_status ? ` (HTTP ${dg.last_http_status})` : ""}`;
  if (dg.status === "sent" || dg.status === "partial") return `digest ${RS_DIGEST[dg.status]}${n}`;
  return `digest ${RS_DIGEST[dg.status] || "unknown"}`;
}

function rsRun(run, title, live) {
  const box = h("div", "rsrun"), head = h("div", "rsrunh");
  head.append(h("b", "", title), h("span", "", run.slot_key || "no slot"));
  if (RS_KIND[run.kind]) head.append(h("span", "kindtag", RS_KIND[run.kind]));
  head.append(h("span", "muted", `${run.trigger} · ${run.mode}`),
    rsBadgeEl(RS_STATUS[run.status] || "unknown", RS_LEVEL[run.status] || "warn"));
  if (live && run.started_at) {
    const el = h("span", "muted", "running for ");
    el.append(h("span", "", dur(rsNow() - run.started_at), { id: "rselapsed" }));
    head.append(el);
  } else if (run.duration_s != null) head.append(h("span", "muted", dur(run.duration_s)));
  if (run.window_share_pct != null) head.append(h("span", "muted", `${run.window_share_pct}% of the 5-hour window`));
  if (run.error_code) head.append(h("span", "msg err", RS_ERROR[run.error_code] || "error"));
  const dg = rsDigest(run.digest);
  if (dg) head.append(h("span", "muted", dg));
  if (live && run.status === "running" && run.started_at && rsNow() - run.started_at > RS_STUCK_SECS) {
    head.append(h("span", "rsstuck", "possibly stuck · run `docker compose restart app` in valkyrie-dispatch; it marks the run interrupted on startup"));
  }
  box.append(head);
  if (run.kind === "explore") { box.append(h("div", "rsline muted", "Explore run: see the Explore card for today's counts.")); return box; }
  const table = h("table", "rstable"), tr = h("tr"), thead = h("thead");
  for (const c of ["Agent", "Status", "Models", "Started", "Took", "Turns", "Searches", "Tokens in/out", "≈ USD", "Kept", "Filtered", "Error"]) tr.append(h("th", "", c));
  thead.append(tr);
  table.append(thead);
  const body = h("tbody");
  for (const a of run.agents || []) {
    const row = h("tr"), fb = a.filtered_by;
    const filt = h("td", "", a.items_filtered ?? "–");
    if (fb) filt.setAttribute("data-tip", `Filtered items\nduplicate: ${fb.duplicate ?? 0}\nexcluded: ${fb.excluded ?? 0}\nno update: ${fb.no_update ?? 0}`);
    row.append(h("td", "", a.agent_type), h("td", "", RS_STATUS[a.status] || a.status),
      h("td", "", [a.research_model, a.structure_model].filter(Boolean).map(shortModel).join(" / ") || "–"), h("td", "", hm(a.started_at)),
      h("td", "", a.duration_s != null ? dur(a.duration_s) : "–"), h("td", "", `${a.num_turns ?? "–"}/${a.max_turns ?? "–"}`),
      h("td", "", `${a.web_searches ?? "–"}/${a.max_web_searches ?? "–"}`), h("td", "", `${rsN(a.tokens_in)} / ${rsN(a.tokens_out)}`),
      h("td", "", rsUsd(a.equiv_usd)), h("td", "", a.items_kept ?? "–"), filt, h("td", "", a.error_code ? RS_ERROR[a.error_code] || "error" : ""));
    body.append(row);
  }
  table.append(body);
  box.append(table);
  return box;
}

function rsLast(r) {
  const box = $("rslast"), d = r.data;
  if (!d) { box.replaceChildren(rsStateBlock(r)); return; }
  const kids = [h("h3", "", "Runs")];
  if (!d.service?.features?.runs) kids.push(h("div", "rsline muted", "No run history yet."));
  else {
    if (d.current_run) kids.push(rsRun(d.current_run, "Now", true));
    if (d.last_run) kids.push(rsRun(d.last_run, "Last run", false));
    if (!d.current_run && !d.last_run) kids.push(h("div", "rsline muted", "No runs recorded yet."));
  }
  box.replaceChildren(...kids);
}

function rsErrors(r) {
  const box = $("rserrors"), d = r.data;
  if (!d || !d.service?.features?.runs) { box.replaceChildren(); return; }
  const items = [];
  for (const run of rsRuns(d)) {
    if (run.status === "skipped_gate") items.push({ ts: run.finished_at || run.started_at, who: "gate", slot: run.slot_key, label: `skipped${run.gate_reason ? `: ${RS_REASON[run.gate_reason] || ""}` : ""}` });
    if (run.kind === "explore" && (run.status === "failed" || run.status === "aborted_quota")) {
      items.push({ ts: run.finished_at || run.started_at, who: "explore", slot: run.slot_key, label: rsMap(RS_ERROR, run.error_code, null) || rsMap(RS_STATUS, run.status, "failed") });
    }
    for (const a of run.agents || []) if (a.error_code) items.push({ ts: a.finished_at || a.started_at, who: a.agent_type, slot: run.slot_key, label: RS_ERROR[a.error_code] || "error" });
  }
  items.sort((a, b) => (b.ts || 0) - (a.ts || 0));
  const kids = [h("h3", "", "Errors")];
  if (!items.length) kids.push(h("div", "rsline muted", "No errors in the last 48 hours."));
  for (const e of items.slice(0, 10)) kids.push(h("div", "rsline", `${rsWhen(e.ts)}  ${e.who}  ${e.slot || ""}  ${e.label}`));
  box.replaceChildren(...kids);
}

// ---------- Topics panel (sub-view of the Research tab) ----------
// Topic text is untrusted (CLI-created topics can hold anything). Everything below builds elements with h()/svg() and sets
// textContent or non-URL attributes: never an HTML string. web_smoke.js checks that this section holds none.
const TP_AGENTS = ["general", "hobby", "fintrend"];
const TP_AGENT_LABEL = { general: "General", hobby: "Hobby", fintrend: "Fintrend" };
const TP_CAP = 200, TP_MAXKW = 20, TP_MAXEX = 50, TP_NAME = 120, TP_TERM = 80;
const RS_VIEWS = ["status", "topics", "activity"];   // Status | Topics | Activities (gate log, runs, errors)
let RSVIEW = RS_VIEWS.includes(store.get("niu.rsview", "status")) ? store.get("niu.rsview", "status") : "status";
const TP_PAGE = 50;
const TP_STATES = ["current", "active", "inactive", "archived", "all"], TP_RUNS = ["any", "in", "below"], TP_ORIGINS = ["any", "chat", "discovery", "explore", "none"];
const TP_SORTS = ["run", "weight", "name", "updated", "newest"], TP_DENSITY = ["compact", "comfortable"];
const TP = { data: null, agent: "all", filter: "", state: "current", run: "any", origin: "any", trial: false, sort: "run", sortUser: false, density: "compact",
  shown: new Map(), collapsed: new Set(), editing: null, orig: null, flash: null, busy: new Set(), rowMsg: new Map(),
  rowNote: new Map(), expanded: new Set(), hold: null, loading: false, saving: false };
(function tpLoadView() {   // filters, sort, density and collapsed groups persist; the search text does not
  const v = store.get("niu.tp.view", null);
  if (!v || typeof v !== "object") return;
  const pick = (x, list, d) => list.includes(x) ? x : d;
  TP.state = pick(v.state, TP_STATES, "current"); TP.run = pick(v.run, TP_RUNS, "any"); TP.origin = pick(v.origin, TP_ORIGINS, "any");
  TP.sort = pick(v.sort, TP_SORTS, "run"); TP.density = pick(v.density, TP_DENSITY, "compact"); TP.trial = v.trial === true;
  if (Array.isArray(v.collapsed)) for (const a of v.collapsed) if (TP_AGENTS.includes(a)) TP.collapsed.add(a);
})();
function tpPersist() {
  store.set("niu.tp.view", { state: TP.state, run: TP.run, origin: TP.origin, trial: TP.trial, sort: TP.sort, density: TP.density, collapsed: [...TP.collapsed] });
}
const TP_STATE_MSG = {
  loading: "Loading topics…",
  not_paired: "Pair this browser to view and edit topics (menu, then Pair). Topics are personal, so they are not shown to unpaired viewers.",
  not_configured: "No topics token yet. Create one with rd gen-token --name niu-dash-topics --scope topics in PERSONAL/valkyrie-dispatch, add it to API_TOKENS, restart, then run niu-dash research-token --scope topics on this machine.",
  disabled: "The Research tab is turned off in its local config.",
  wrong_scope: "This token does not have the topics scope. Remove it with niu-dash research-token --scope topics --remove.",
  too_powerful: "This token is more powerful than the dashboard accepts. Remove it with niu-dash research-token --scope topics --remove.",
  unreachable: "valkyrie-dispatch is not reachable. Is the container running?",
  unauthorized: "Token rejected: check API_TOKENS and re-run niu-dash research-token --scope topics.",
  incompatible: "valkyrie-dispatch is too old for the Topics panel (needs /auth/whoami).",
  error: "Could not read the topics.",
};
const TP_HOLD_MS = 1000;
const TP_BAD_STATE = new Set(["wrong_scope", "too_powerful", "unauthorized"]);
const TP_MSG = {
  changed: "This topic changed elsewhere (CLI, Discord or feedback). Reload it?",
  duplicate: "A topic with this name already exists.",
  rate_limited: "Too many changes in a short time. Try again in a minute.",
  toolarge: "Too much text: shorten keywords or excluded terms.",
  not_found: "That topic no longer exists. Refresh the list.",
  invalid: "valkyrie-dispatch refused some values.",
  unreachable: "valkyrie-dispatch is not reachable; nothing was saved.",
  not_paired: "Pair this browser to edit topics.",
  badjson: "That request could not be read.",
  incompatible: "valkyrie-dispatch is too old to archive separately; nothing was changed.",
  error: "Could not save; nothing was changed.",
};
const TP_FIELD_MSG = {
  required: "Required", too_short: "Required", forbidden_char: "These characters are not allowed: < > ` { } and control characters",
  bad_enum: "Pick one of the listed agents", not_list: "Enter one term per line", empty: "Nothing to save", unknown: "Not an editable field",
  invalid: "Refused by valkyrie-dispatch", not_bool: "Must be on or off", toolarge: "Too much text",
  out_of_range: "Between 0.1 and 5", too_few: "At least 1 keyword", bad_combo: "A topic cannot be active and archived.",
};
const tpFieldMsg = (field, code) => code === "too_many" ? `At most ${field === "keywords" ? TP_MAXKW : TP_MAXEX} ${field === "keywords" ? "keywords" : "excluded terms"}`
  : code === "too_long" ? (field === "name" ? `At most ${TP_NAME} characters` : `Each term can have at most ${TP_TERM} characters`)
  : TP_FIELD_MSG[code] || "Not accepted";
const tpLen = (s) => [...s].length;
const TP_BAD_CHAR = /[<>`{}\u2028\u2029\p{C}]/u;
const tpAgentLabel = (a) => TP_AGENT_LABEL[a] || "That agent";

// ----- fuzzy search (pure functions; web_smoke.js calls them directly) -----
// Matching rules, per query token (case and accent folded via NFKD, split on anything that is not a letter or digit):
//   exact word 100 | word prefix 70-90 | substring of a word 70 (token >= 3 chars) | initials of the field's words 65 (token >= 2)
//   | typo of a word or of a word's prefix: Damerau-Levenshtein (adjacent swap = 1) <= 1 for 4-6 chars, <= 2 for 7+, never under 4 (60/55 minus 10 per edit)
//   | in-order subsequence of a word starting at its first letter, 3+ chars, small gaps (50 minus 4 per gap, min 10).
// Under 3 chars only exact, prefix and initials count. Every token must match (AND); a token's score is the best over name (x1),
// keywords (x0.85) and excluded terms (x0.6); the topic score is the mean, plus 15 when the whole phrase sits in the name.
const FZ_MAXQ = 80, FZ_MAXTOK = 8, FZ_MAXTXT = 300, FZ_MAXWORD = 40, FZ_MAXTERMS = 100;
const FZ_W = { name: 1, keywords: 0.85, excluded: 0.6 };
function fzFold(s) {   // folded text, plus for every folded char the index of the original code point it came from
  let f = "", i = 0;
  const map = [];
  for (const ch of String(s)) {
    if (i >= FZ_MAXTXT) break;
    const g = ch.toLowerCase().normalize("NFKD").replace(/\p{M}/gu, "");
    for (let k = 0; k < g.length; k++) map.push(i);
    f += g; i++;
  }
  return { f, map };
}
function fzWords(f) {
  const out = [], re = /[\p{L}\p{N}]+/gu;
  let m;
  while ((m = re.exec(f))) out.push({ w: m[0], at: m.index });
  return out;
}
function fzPrep(text) {
  const { f, map } = fzFold(text), words = fzWords(f);
  return { f, map, words, init: words.map((x) => x.w[0]).join(""), initAt: words.map((x) => x.at) };
}
function fzDist(a, b, max) {   // Damerau-Levenshtein (adjacent transposition = 1), max + 1 when clearly farther
  const n = a.length, m = b.length;
  if (Math.abs(n - m) > max) return max + 1;
  let p2 = null, p1 = Array.from({ length: m + 1 }, (_, j) => j);
  for (let i = 1; i <= n; i++) {
    const cur = [i];
    for (let j = 1; j <= m; j++) {
      let v = Math.min(p1[j] + 1, cur[j - 1] + 1, p1[j - 1] + (a[i - 1] === b[j - 1] ? 0 : 1));
      if (i > 1 && j > 1 && a[i - 1] === b[j - 2] && a[i - 2] === b[j - 1]) v = Math.min(v, p2[j - 2] + 1);
      cur.push(v);
    }
    p2 = p1; p1 = cur;
  }
  return p1[m];
}
function fzTok(tok, fd) {   // best match of one token inside one prepared field: { s: score (0 = none), idx: folded char indexes }
  const L = tok.length, maxd = L >= 7 ? 2 : L >= 4 ? 1 : 0;
  let best = { s: 0, idx: [] };
  const take = (s, idx) => { if (s > best.s) best = { s, idx }; };
  const rng = (at, n) => Array.from({ length: n }, (_, k) => at + k);
  for (const { w, at } of fd.words) {
    if (w === tok) { take(100, rng(at, L)); continue; }
    if (w.startsWith(tok)) { take(Math.max(70, 90 - (w.length - L) * 0.5), rng(at, L)); continue; }
    if (L < 3) continue;
    const p = w.indexOf(tok);
    if (p > 0) { take(70, rng(at + p, L)); continue; }
    if (w[0] === tok[0]) {
      const hit = [];
      for (let k = 0, j = 0; k < w.length && j < L; k++) if (w[k] === tok[j]) { hit.push(at + k); j++; }
      if (hit.length === L && hit[L - 1] - hit[0] + 1 - L <= Math.max(2, L >> 1)) take(Math.max(10, 50 - (hit[L - 1] - hit[0] + 1 - L) * 4), hit);
    }
    if (maxd && L <= FZ_MAXWORD && w.length <= FZ_MAXWORD) {
      const d = fzDist(tok, w, maxd);
      if (d <= maxd) take(60 - 10 * d, rng(at, w.length));
      for (const pl of [L - 1, L, L + 1]) {
        if (pl < 3 || pl >= w.length) continue;
        const dp = fzDist(tok, w.slice(0, pl), maxd);
        if (dp <= maxd) take(55 - 10 * dp, rng(at, pl));
      }
    }
  }
  if (L >= 2) {
    const p = fd.init.indexOf(tok);
    if (p >= 0) take(p ? 60 : 65, fd.initAt.slice(p, p + L));
  }
  return best;
}
function fzQuery(query) { return fzWords(fzFold(String(query).slice(0, FZ_MAXQ)).f).map((x) => x.w).slice(0, FZ_MAXTOK); }
// parts: [{ key, i, fd }]; returns null unless every token matches somewhere.
function fzRun(tokens, parts) {
  let total = 0;
  const hits = [];
  for (const tok of tokens) {
    let best = null;
    for (const p of parts) {
      const r = fzTok(tok, p.fd), s = r.s * FZ_W[p.key];
      if (r.s && (!best || s > best.s)) best = { s, key: p.key, i: p.i, idx: r.idx, fd: p.fd };
    }
    if (!best) return null;
    total += best.s; hits.push(best);
  }
  return { score: total / tokens.length, hits };
}
// Score of one text against a query: 0 = no match, higher = better. An empty query matches everything (1).
function fuzzyScore(query, text) {
  if (!String(query).trim()) return 1;
  const tokens = fzQuery(query);
  if (!tokens.length) return 0;
  const r = fzRun(tokens, [{ key: "name", i: 0, fd: fzPrep(text) }]);
  return r ? r.score : 0;
}
// Folded text of a topic's name, keywords and excluded terms, cached per topic object (a new object after every load or write,
// so the cache is rebuilt with TP.data; the source references are compared in case a row is changed in place).
const TP_PREP = new WeakMap();
function tpPrep(t) {
  const kw = Array.isArray(t.keywords) ? t.keywords : [], ex = Array.isArray(t.excluded_terms) ? t.excluded_terms : [];
  let c = TP_PREP.get(t);
  if (!c || c.name !== t.name || c.kw !== t.keywords || c.ex !== t.excluded_terms) {
    c = { name: t.name, kw: t.keywords, ex: t.excluded_terms, nameFd: fzPrep(t.name),
      kwFd: kw.slice(0, FZ_MAXTERMS).map((k) => fzPrep(k)), exFd: ex.slice(0, FZ_MAXTERMS).map((k) => fzPrep(k)) };
    TP_PREP.set(t, c);
  }
  return c;
}
// Topic against a query: null = no match, else { score, name: Set of name code-point indexes to highlight (or null), kw: Set, ex: Set }.
function tpMatch(t, query) {
  const none = { score: 1, name: null, kw: new Set(), ex: new Set() };
  if (!String(query).trim()) return none;
  const tokens = fzQuery(query);
  if (!tokens.length) return null;
  const c = tpPrep(t), nameFd = c.nameFd, parts = [{ key: "name", i: 0, fd: nameFd }];
  c.kwFd.forEach((fd, i) => parts.push({ key: "keywords", i, fd }));
  c.exFd.forEach((fd, i) => parts.push({ key: "excluded", i, fd }));
  const r = fzRun(tokens, parts);
  if (!r) return null;
  const out = { score: r.score, name: null, kw: new Set(), ex: new Set() };
  if (tokens.length > 1 && nameFd.f.includes(tokens.join(" "))) out.score += 15;
  for (const x of r.hits) {
    if (x.key === "name") { out.name = out.name || new Set(); for (const k of x.idx) out.name.add(x.fd.map[k]); }
    else (x.key === "keywords" ? out.kw : out.ex).add(x.i);
  }
  return out;
}

// ----- filter, sort and group (pure: web_smoke.js table-tests them) -----
// T = { agent, filter, state, run, origin, trial, sort, sortUser }; rows are shaped topics, `meta` may be null.
const tpState = (t) => t.is_archived ? "archived" : t.is_active ? "active" : "inactive";
const tpHasMeta = (list) => list.some((t) => t.meta && typeof t.meta === "object");
function tpFilter(list, T, matches) {
  const q = String(T.filter || "").trim(), out = [];
  for (const t of list) {
    const st = tpState(t), m = t.meta || null;
    if (T.agent !== "all" && t.agent_type !== T.agent) continue;
    if (T.state === "current" ? st === "archived" : T.state !== "all" && st !== T.state) continue;
    if (T.run === "in" && !(st === "active" && m && m.in_run === true)) continue;
    if (T.run === "below" && !(st === "active" && m && m.in_run === false)) continue;
    if (T.origin === "none" ? (m && m.origin) : T.origin !== "any" && !(m && m.origin === T.origin)) continue;
    if (T.trial && !(m && m.trial_until)) continue;
    if (q) {
      const x = tpMatch(t, q);
      if (!x) continue;
      if (matches) matches.set(t.id, x);
    }
    out.push(t);
  }
  return out;
}
// The sort actually used: a search means best match unless a sort was picked afterwards; run order needs the server's ranks.
function tpSortKey(list, T) {
  if (String(T.filter || "").trim() && !T.sortUser) return "match";
  if (T.sort === "run" && !tpHasMeta(list)) return "weight";
  if (T.sort === "newest" && !tpHasMeta(list)) return "weight";
  return T.sort;
}
function tpSort(list, T, matches) {
  const key = tpSortKey(list, T), cls = (t) => t.is_archived ? 2 : t.is_active ? 0 : 1;
  const name = (a, b) => String(a.name).localeCompare(String(b.name), undefined, { sensitivity: "base" }) || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0);
  const rank = (t) => t.meta && Number.isFinite(t.meta.run_rank) ? t.meta.run_rank : Infinity;
  const score = (t) => (matches && matches.get(t.id) ? matches.get(t.id).score : 0);
  const str = (v) => typeof v === "string" ? v : "";
  const cmp = {
    match: (a, b) => score(b) - score(a),
    run: (a, b) => (cls(a) === 0 ? (rank(a) === rank(b) ? 0 : rank(a) < rank(b) ? -1 : 1) : 0) || name(a, b),
    weight: (a, b) => b.weight - a.weight || name(a, b),
    name,
    updated: (a, b) => (str(b.updated_at) > str(a.updated_at) ? 1 : str(b.updated_at) < str(a.updated_at) ? -1 : 0) || name(a, b),
    newest: (a, b) => { const x = str(a.meta && a.meta.created_at), y = str(b.meta && b.meta.created_at); return (y > x ? 1 : y < x ? -1 : 0) || name(a, b); },
  }[key];
  return list.map((t, i) => ({ t, i })).sort((a, b) => cls(a.t) - cls(b.t) || cmp(a.t, b.t) || a.i - b.i).map((x) => x.t);
}
function tpCounts(rows) {
  const c = { active: 0, inrun: 0, below: 0, inactive: 0, trial: 0 };
  for (const t of rows) {
    const st = tpState(t);
    if (st === "active") { c.active++; if (t.meta && t.meta.in_run === true) c.inrun++; else if (t.meta && t.meta.in_run === false) c.below++; }
    else if (st === "inactive") c.inactive++;
    if (st !== "archived" && t.meta && t.meta.trial_until) c.trial++;
  }
  return c;
}
// One group per agent in TP_AGENTS order; agents with no row left after filtering are omitted. `counts` are over the agent's whole list.
function tpGroups(list, T, matches) {
  const rows = tpSort(tpFilter(list, T, matches), T, matches);
  return TP_AGENTS.map((a) => ({ agent: a, rows: rows.filter((t) => t.agent_type === a), counts: tpCounts(list.filter((t) => t.agent_type === a)) })).filter((g) => g.rows.length);
}

// Client-side mirror of the server's allowlist (the server and valkyrie-dispatch stay authoritative).
function tpTerms(text, lo, hi) {
  const out = [], seen = new Set();
  for (const raw of String(text).split(/\r?\n/)) {
    const s = raw.trim().replace(/\s+/g, " ");
    if (!s) continue;
    if (TP_BAD_CHAR.test(s)) return { err: "forbidden_char" };
    if (tpLen(s) > TP_TERM) return { err: "too_long" };
    if (!seen.has(s.toLowerCase())) { seen.add(s.toLowerCase()); out.push(s); }
  }
  return out.length < lo ? { err: "too_few" } : out.length > hi ? { err: "too_many" } : { val: out };
}
function tpCollect() {
  const val = {}, errs = {};
  const name = $("tpname").value;
  if (TP_BAD_CHAR.test(name)) errs.name = "forbidden_char";
  else {
    const n = name.trim().replace(/\s+/g, " ");
    if (!n) errs.name = "too_short"; else if (tpLen(n) > TP_NAME) errs.name = "too_long"; else val.name = n;
  }
  for (const [id, field, lo, hi] of [["tpkw", "keywords", 1, TP_MAXKW], ["tpex", "excluded_terms", 0, TP_MAXEX]]) {
    const t = tpTerms($(id).value, lo, hi);
    if (t.err) errs[field] = t.err; else val[field] = t.val;
  }
  const w = Math.round(Number($("tpweight").value) * 100) / 100;
  if (String($("tpweight").value).trim() === "" || !Number.isFinite(w) || w < 0.1 || w > 5) errs.weight = "out_of_range"; else val.weight = w;
  return { val, errs };
}

// ----- sub-view switch -----
function setRsView(v, init) {
  RSVIEW = RS_VIEWS.includes(v) ? v : "status";
  store.set("niu.rsview", RSVIEW);
  for (const name of RS_VIEWS) {
    const id = `rsview-${name}`;
    $(id).classList.toggle("on", RSVIEW === name); $(id).setAttribute("aria-pressed", String(RSVIEW === name));
  }
  $("rsbody").hidden = RSVIEW !== "status"; $("tpbody").hidden = RSVIEW !== "topics"; $("rsact").hidden = RSVIEW !== "activity";
  $("rsrefresh").hidden = $("rsupd").hidden = RSVIEW === "topics";
  if (init) return;
  const hash = RSVIEW === "status" ? "#research" : `#research/${RSVIEW}`;
  if (VIEW === "research" && location.hash !== hash) history.replaceState(null, "", hash);
  if (RSVIEW === "topics") loadTopics(); else renderResearch();
}
$("rsview-status").onclick = () => setRsView("status");
$("rsview-topics").onclick = () => setRsView("topics");
$("rsview-activity").onclick = () => setRsView("activity");

// ----- data -----
async function loadTopics(force) {
  TP.loading = true;
  let r = null;
  try {
    if (force) { const x = await POST("/api/topics/refresh"); r = x.data; }
    else r = await fetch("/api/topics", { credentials: "same-origin" }).then((x) => x.json());
  } catch { r = null; }
  TP.loading = false;
  if (!r || typeof r !== "object" || typeof r.state !== "string") r = { state: "error", topics: [] };
  if (!Array.isArray(r.topics)) r.topics = [];
  if (r.state === "not_paired") loadMe();
  TP.data = r;
  renderTopics();
}

// ----- rendering -----
function tpWritable() { return !!TP.data && TP.data.state === "ok" && !TP.data.stale && ME.paired; }
const tpNow = () => (TP.data && Number.isFinite(TP.data.server_now) ? TP.data.server_now : Date.now() / 1000);
const tpTs = (iso) => { const v = typeof iso === "string" ? Date.parse(iso) : NaN; return Number.isFinite(v) ? v / 1000 : null; };
function tpAgo(iso) {
  const ts = tpTs(iso);
  if (ts === null) return "";
  const d = Math.max(0, tpNow() - ts);
  return d < 60 ? "now" : d < 3600 ? `${Math.floor(d / 60)}m ago` : d < 86400 ? `${Math.floor(d / 3600)}h ago` : `${Math.floor(d / 86400)}d ago`;
}
const TP_RUN_TIP = "Overnight and manual runs research the top 20 active topics per agent by decayed weight; the midday delta scouts the top 6 of those.";
const TP_NO_META = "Update valkyrie-dispatch to see run order, the top-20 cut-off and where topics came from.";
let TP_ROWS = [];   // the row elements of the last render, in order (keyboard navigation)
function tpEff(meta) {   // the view settings that apply: the run, origin and trial controls need the server's meta
  return { agent: TP.agent, filter: TP.filter, state: TP.state, run: meta ? TP.run : "any", origin: meta ? TP.origin : "any", trial: meta && TP.trial,
    sort: TP.sort, sortUser: TP.sortUser };
}
function tpReset() { TP.shown.clear(); }
function tpToolbar(r, meta, all, sortKey) {
  const ok = r.state === "ok";
  $("tpstate").value = TP.state;
  for (const id of ["tprun", "tporigin", "tptrial"]) $(id).hidden = !meta;
  $("tprun").value = TP.run; $("tporigin").value = TP.origin;
  const trials = all.filter((t) => !t.is_archived && t.meta && t.meta.trial_until).length;
  $("tptrial").textContent = `On trial (${trials})`;
  $("tptrial").setAttribute("aria-pressed", String(TP.trial));
  $("tptrial").classList.toggle("on", TP.trial);
  $("tpsort-run").hidden = !meta; $("tpsort-newest").hidden = !meta;
  $("tpsort-match").hidden = sortKey !== "match";
  $("tpsort").value = sortKey;
  $("tpdensity").setAttribute("aria-pressed", String(TP.density === "comfortable"));
  $("tpdensity").classList.toggle("on", TP.density === "comfortable");
  $("tpdensity").title = TP.density === "comfortable" ? "Row detail: comfortable (click for compact rows)" : "Row detail: compact (click for taller rows with more detail)";
  $("tpadd").disabled = !tpWritable();
  $("tpadd").setAttribute("title", ME.paired ? (ok ? "Add topic" : "Add topic: unavailable right now") : "Add topic: pair this browser to edit");
}
function tpGroupHead(g, meta, T) {
  const c = g.counts, open = !TP.collapsed.has(g.agent);
  let txt = `${TP_AGENT_LABEL[g.agent]} · ${c.active} active`;
  if (meta) txt += ` · ${c.inrun} in the run · ${c.below} below`;
  if (c.inactive) txt += ` · ${c.inactive} inactive`;
  if (c.trial) txt += ` · ${c.trial} on trial`;
  if (T.run === "below") txt += ` · showing the ${g.rows.length} active topics outside the run`;
  const b = h("button", "tpgroup", "", { "aria-expanded": String(open), "data-agent": g.agent });
  b.append(h("span", "tpchev", open ? "▾" : "▸", { "aria-hidden": "true" }), h("span", "", txt));
  b.onclick = () => { if (open) TP.collapsed.add(g.agent); else TP.collapsed.delete(g.agent); tpPersist(); renderTopics(); };
  return b;
}
function renderTopics() {
  tpHoldCancel();   // a rebuilt list must never leave a stale hold timer behind
  if (VIEW !== "research" || RSVIEW !== "topics") return;
  const r = TP.data || { state: "loading", topics: [] };
  const ok = r.state === "ok", all = r.topics || [];
  const meta = (ok || !!r.stale) && r.meta_supported === true;
  $("tpbody").classList.toggle("stale", !!r.stale);
  // state banner
  const head = [];
  if (!ok) {
    const msg = (typeof r.message === "string" && r.message) || TP_STATE_MSG[r.state] || TP_STATE_MSG.error;
    head.push(h("div", `tpstate ${TP_BAD_STATE.has(r.state) ? "bad" : "warn"}`, msg));
    if (r.stale && r.loaded_at) head.push(h("div", "small muted", `Showing the list as of ${hm(r.loaded_at)}. Changes are disabled.`));
  }
  if (ok && r.states_supported === false) head.push(h("div", "tpstate warn", "valkyrie-dispatch is older than this dashboard: switching a topic off archives it, and Inactive is unavailable."));
  if (ok && r.meta_supported !== true) head.push(h("div", "small muted tpnometa", TP_NO_META));
  if (ok && r.inactive_truncated) head.push(h("div", "small muted", "Showing 1000 inactive topics; use rd topics list --state inactive for the rest."));
  if (ok && r.archived_truncated) head.push(h("div", "small muted", "Showing 1000 archived topics; use rd topics list --all for the rest."));
  if (ok && r.dropped) head.push(h("div", "small muted", `${r.dropped} topic(s) could not be shown (unexpected data).`));
  $("tphead").replaceChildren(...head);
  // toolbar
  const T = tpEff(meta), q = TP.filter.trim();
  tpToolbar(r, meta, all, tpSortKey(all, T));
  const chips = [["all", "All", all.filter((t) => t.is_active).length, null]];
  for (const a of TP_AGENTS) chips.push([a, TP_AGENT_LABEL[a], all.filter((t) => t.is_active && t.agent_type === a).length, TP_CAP]);
  $("tpchips").replaceChildren(...chips.map(([k, label, n, cap]) => {
    const top = meta && k !== "all" ? ` · top ${all.filter((t) => t.agent_type === k && t.meta && t.meta.in_run === true).length}` : "";
    const b = h("button", `tpagent${TP.agent === k ? " on" : ""}`, (cap ? `${label} ${n}/${cap}` : `${label} ${n}`) + top, { "aria-pressed": String(TP.agent === k) });
    b.onclick = () => { TP.agent = k; tpReset(); renderTopics(); };
    return b;
  }));
  $("tpupd").textContent = r.loaded_at ? `loaded ${hm(r.loaded_at)}` : "";
  // list
  const list = [];
  let cnt = "";
  TP_ROWS = [];
  if (ok || r.stale) {
    const matches = new Map();
    const groups = tpGroups(all, T, matches), key = tpSortKey(all, T);
    let vis = 0, total = 0;
    for (const g of groups) {
      total += g.rows.length;
      list.push(tpGroupHead(g, meta, T));
      if (TP.collapsed.has(g.agent)) continue;
      const n = Math.min(g.rows.length, TP.shown.get(g.agent) || TP_PAGE), shown = g.rows.slice(0, n);
      vis += n;
      const last = key === "run" && meta ? g.rows.reduce((a, t, i) => (t.meta && t.meta.in_run === true ? i : a), -1) : -1;
      const cut = last >= 0 && g.rows[last + 1] && tpState(g.rows[last + 1]) === "active" ? last : -1;
      shown.forEach((t, i) => {
        const row = tpRow(t, matches.get(t.id), meta);
        TP_ROWS.push(row); list.push(row);
        if (i === cut) list.push(h("div", "tpcut", `Research cut-off: the ${g.counts.inrun} above are researched each night; the ${g.counts.below} below are active but not in the run`, { role: "separator" }));
      });
      if (n < g.rows.length) {
        const left = g.rows.length - n, bar = h("div", "tpmorebar");
        const more = h("button", "ghost tiny tpmore", `Show ${Math.min(TP_PAGE, left)} more (${left} left)`);
        more.onclick = () => { TP.shown.set(g.agent, n + TP_PAGE); renderTopics(); };
        const allb = h("button", "ghost tiny tpmore tpall", "Show all");
        allb.onclick = () => { TP.shown.set(g.agent, g.rows.length); renderTopics(); };
        bar.append(more, allb);
        list.push(bar);
      }
    }
    if (all.length) cnt = `Showing ${vis} of ${total} · ${all.length} total`;
    if (!groups.length) {
      list.push(h("div", "empty", !all.length ? "No topics yet. Add one, or use rd topics add in valkyrie-dispatch."
        : TP.agent === "fintrend" && !TP.filter && !all.some((t) => t.agent_type === "fintrend") ? "Fintrend agent arrives in M4; topics you add now are kept."
          : q ? `No topic matches "${[...q].slice(0, 40).join("")}${[...q].length > 40 ? "…" : ""}"` : "No topics match."));
    } else if (TP.agent === "all" && !all.some((t) => t.agent_type === "fintrend")) {
      list.push(h("div", "small muted", "Fintrend agent arrives in M4; topics you add now are kept."));
    }
    // roving tabindex: one row is in the tab order, the others are reached with the arrow keys
    const tab = TP_ROWS.find((x) => x.getAttribute("data-id") === TP.focusId) || TP_ROWS[0];
    for (const x of TP_ROWS) x.setAttribute("tabindex", x === tab ? "0" : "-1");
  }
  $("tplist").replaceChildren(...list);
  $("tpcount").textContent = cnt;
}

// Row action icons: monochrome line icons that take the text colour (no coloured glyphs).
const TP_ICON = {
  edit: ["M12 20h9", "M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z"],
  archive: ["M3 4h18v4H3Z", "M5 8v12h14V8", "M10 12h4"],
};
function tpIcon(paths) {
  const el = svg("svg", { class: "tpico", width: "14", height: "14", viewBox: "0 0 24 24", "aria-hidden": "true", focusable: "false" });
  for (const d of paths) el.append(svg("path", { d }));
  return el;
}

function tpTrialBadge(t) {
  const until = t.meta && tpTs(t.meta.trial_until);
  if (!until) return null;
  const left = until - tpNow();
  return left > 0 ? h("span", "badge b-ok tptrial", `trial, ${Math.max(1, Math.ceil(left / 86400))} d left`, { "data-tip": "On trial until the check-in; it is kept if you like it." })
    : h("span", "badge b-warn tptrial", "check-in due", { "data-tip": "The trial period is over; a check-in asks whether to keep this topic." });
}
const TP_ORIGIN_TIP = { chat: "Added from a #hermes chat proposal", discovery: "Found by weekly discovery", explore: "Suggested by the daily explore run" };
function tpRow(t, m, meta) {
  const kws = Array.isArray(t.keywords) ? t.keywords : [], exs = Array.isArray(t.excluded_terms) ? t.excluded_terms : [];
  const can = tpWritable() && !TP.busy.has(t.id);
  const st = tpState(t), mt = meta && t.meta ? t.meta : null, compact = TP.density === "compact";
  const row = h("div", `tprow${compact ? " compact" : ""}${meta ? "" : " nometa"}${st === "archived" ? " tp-archived" : st === "inactive" ? " tp-inactive" : ""}${TP.busy.has(t.id) ? " busy" : ""}${TP.flash === t.id ? " tpflash" : ""}`, "",
    { "data-id": t.id, tabindex: "-1" });
  row.addEventListener("keydown", (e) => tpRowKey(e, row, t));
  row.addEventListener("focus", () => { TP.focusId = t.id; });
  if (st !== "archived") {
    const sw = h("input", "tpswitch", "", { type: "checkbox", role: "switch", "aria-label": `Active: ${t.name}` });
    sw.checked = st === "active"; sw.disabled = !can;
    if (!ME.paired) sw.setAttribute("title", "Pair this browser to edit");
    sw.onchange = () => tpToggle(t, sw.checked);
    row.append(sw);
  } else {
    const b = h("button", "ghost tiny tprestore", "Restore");
    b.disabled = !can;
    b.onclick = () => tpRestore(t);
    row.append(b);
  }
  const rankBadge = () => {
    if (!mt || st !== "active" || typeof mt.in_run !== "boolean") return null;
    return mt.in_run ? h("span", "badge b-ok tprank", mt.run_rank ? `#${mt.run_rank}` : "in the run", { "data-tip": TP_RUN_TIP })
      : h("span", "badge b-warn tprank", "below cut-off", { "data-tip": TP_RUN_TIP });
  };
  const rank = rankBadge();
  if (compact && meta) row.append(rank || h("span", "tprank"));
  const main = h("div", "tpmain");
  const title = h("div", "tptitle");
  const hl = m && m.name && m.name.size;
  const nm = h("span", "tpname", hl ? null : t.name, { "data-tip": t.updated_at ? `${t.name}\nLast edited ${t.updated_at}` : t.name });
  if (hl) {   // matched characters get their own span; every piece is still plain textContent
    let run = "", on = false;
    const flush = () => { if (run) nm.append(h("span", on ? "tpmatch" : "", run)); run = ""; };
    Array.from(t.name).forEach((ch, i) => { const hit = m.name.has(i); if (hit !== on) { flush(); on = hit; } run += ch; });
    flush();
  }
  title.append(nm);
  if (!compact && rank) title.append(rank);
  if (mt && mt.origin) title.append(h("span", "badge b-muted tporigin", mt.origin, { "data-tip": TP_ORIGIN_TIP[mt.origin] || "" }));
  const trial = tpTrialBadge(t);
  if (trial && st !== "archived") title.append(trial);
  if (t.over_api_limits) title.append(h("span", "badge b-warn", "CLI-only limits", { "data-tip": "Created with the CLI; exceeds what the API accepts. Shorten it to edit." }));
  if (st !== "active") title.append(h("span", "badge b-off", st));
  const likes = mt && mt.likes_30d > 0 ? mt.likes_30d : 0, dislikes = mt && mt.dislikes_30d > 0 ? mt.dislikes_30d : 0;
  const notes = mt && mt.notes_active > 0 ? mt.notes_active : 0;
  const fb = () => (likes || dislikes) ? h("span", "tpfb small muted", `+${likes} / −${dislikes}`, { "data-tip": "30 days of feedback" }) : null;
  const nt = () => notes ? h("span", "tpnotes small muted", `${notes} note${notes === 1 ? "" : "s"}`, { "data-tip": `${notes} preference notes in use` }) : null;
  const kw = h("div", "tpchips2");
  kws.slice(0, compact ? 3 : 6).forEach((k, i) => kw.append(h("span", m && m.kw.has(i) ? "tpchip tphit" : "tpchip", k)));
  if (kws.length > (compact ? 3 : 6)) kw.append(h("span", "tpchip more", `+${kws.length - (compact ? 3 : 6)}`));
  if (exs.length) {
    const open = TP.expanded.has(t.id);
    const ex = h("button", "ghost tiny tpex", open ? "hide excluded" : `excludes ${exs.length}`, { "aria-expanded": String(open) });
    ex.onclick = () => { if (open) TP.expanded.delete(t.id); else TP.expanded.add(t.id); renderTopics(); };
    kw.append(ex);
  }
  if (compact) { const line = h("div", "tpline"); line.append(title, kw); main.append(line); }
  else {
    for (const x of [fb(), nt()]) if (x) title.append(x);
    main.append(title, kw);
  }
  if (m && (m.kw.size || m.ex.size)) {
    const say = (label, arr, set) => set.size ? `${label} ${[...set].slice(0, 3).map((i) => String(arr[i]).slice(0, 40)).join(", ")}` : "";
    main.append(h("div", "small muted tphint", "Matches " + [say("keyword", kws, m.kw), say("excluded", exs, m.ex)].filter(Boolean).join(" · ")));
  }
  if (TP.expanded.has(t.id)) {
    const exl = h("div", "tpchips2 tpexl");
    exs.forEach((k, i) => exl.append(h("span", m && m.ex.has(i) ? "tpchip ex tphit" : "tpchip ex", k)));
    main.append(exl);
  }
  const msg = TP.rowMsg.get(t.id);
  if (msg) main.append(h("div", "msg err", msg));
  const note = TP.rowNote.get(t.id);
  if (note) main.append(h("div", "small muted", note));
  row.append(main);
  const eff = mt && Number.isFinite(mt.effective_weight) ? mt.effective_weight : t.weight;
  const w = h("div", "tpw", "", mt && Number.isFinite(mt.effective_weight) ? { "data-tip": `effective ${eff.toFixed(1)}, base ${t.weight.toFixed(1)}, decays toward 1.0` } : {});
  w.append(h("span", "tpwnum", !compact && eff !== t.weight ? `${eff.toFixed(1)} (base ${t.weight.toFixed(1)})` : eff.toFixed(1)));
  const bar = svg("svg", { class: "tpweight", width: "60", height: "6", viewBox: "0 0 60 6", "aria-hidden": "true" });
  bar.append(svg("rect", { class: "tpw-bg", x: "0", y: "0", width: "60", height: "6", rx: "3" }),
    svg("rect", { class: "tpw-fg", x: "0", y: "0", width: String(Math.round(60 * Math.max(0, Math.min(5, eff)) / 5)), height: "6", rx: "3" }));
  w.append(bar);
  row.append(w);
  if (compact && meta) {
    const c1 = h("span", "tpcell"), c2 = h("span", "tpcell"), c3 = h("span", "tpcell small muted", tpAgo(t.updated_at), t.updated_at ? { "data-tip": `Last edited ${t.updated_at}` } : {});
    const f = fb(), n = nt();
    if (f) c1.append(f);
    if (n) c2.append(n);
    row.append(c1, c2, c3);
  }
  if (st !== "archived") {
    const acts = h("div", "tpacts");
    const edit = h("button", "ghost tiny tpicon tpedit", "", { "aria-label": `Edit topic: ${t.name}`, "data-tip": "Edit" });
    edit.append(tpIcon(TP_ICON.edit));
    edit.disabled = !can;
    edit.onclick = () => tpDialog(t);
    acts.append(edit);
    if (!TP.data || TP.data.states_supported !== false) acts.append(tpHoldBtn(t, can));
    row.append(acts);
  }
  return row;
}

// ----- keyboard: / focuses the search; Down from the search enters the rows; arrows, Home and End move between rows; Esc goes back -----
function tpFocusRow(i) {
  const row = TP_ROWS[Math.max(0, Math.min(TP_ROWS.length - 1, i))];
  if (!row) return;
  for (const x of TP_ROWS) x.setAttribute("tabindex", x === row ? "0" : "-1");
  TP.focusId = row.getAttribute("data-id");
  row.focus();
}
function tpRowKey(e, row, t) {
  if (e.target && e.target !== row) return;   // keys on the switch, Edit, Restore and hold buttons keep their own meaning
  const i = TP_ROWS.indexOf(row);
  const go = { ArrowDown: i + 1, ArrowUp: i - 1, Home: 0, End: TP_ROWS.length - 1 }[e.key];
  if (go !== undefined) { e.preventDefault(); tpFocusRow(go); }
  else if (e.key === "Escape") { e.preventDefault(); $("tpfilter").focus(); }
  else if (e.key === "Enter" && tpWritable() && !TP.busy.has(t.id) && !t.is_archived) { e.preventDefault(); tpDialog(t); }
}

// ----- hold to archive: press and hold TP_HOLD_MS (pointer, Space or Enter); release, leave, blur or a re-render cancels -----
function tpHoldCancel() {
  const hd = TP.hold;
  if (!hd) return;
  clearTimeout(hd.timer);
  hd.btn.classList.remove("holding");
  TP.hold = null;
}
function tpHoldStart(t, btn, key) {
  if (TP.hold || TP.busy.has(t.id)) return;
  const hd = { id: t.id, btn, key, timer: 0 };
  hd.timer = setTimeout(() => {
    if (TP.hold !== hd) return;
    tpHoldCancel();
    const cur = ((TP.data && TP.data.topics) || []).find((x) => x.id === t.id);
    if (cur && !TP.busy.has(cur.id) && tpWritable()) tpArchive(cur);
  }, TP_HOLD_MS);
  TP.hold = hd;
  btn.classList.add("holding");
}
function tpHoldBtn(t, can) {
  const b = h("button", "ghost tiny tpicon tphold", "", { "aria-label": `Archive topic: ${t.name}`, "aria-describedby": "tpholdhelp", "data-tip": "Hold to archive" });
  b.append(tpIcon(TP_ICON.archive), h("span", "tphold-fill", "", { "aria-hidden": "true" }));
  b.disabled = !can;
  b.addEventListener("pointerdown", (e) => { if (e.button === 0) tpHoldStart(t, b, null); });
  for (const ev of ["pointerup", "pointerleave", "pointercancel", "blur"]) b.addEventListener(ev, tpHoldCancel);
  b.addEventListener("keydown", (e) => {
    if (e.key !== " " && e.key !== "Enter") return;
    e.preventDefault();
    if (!e.repeat) tpHoldStart(t, b, e.key);
  });
  b.addEventListener("keyup", (e) => { if (TP.hold && TP.hold.btn === b && TP.hold.key === e.key) tpHoldCancel(); });
  b.addEventListener("contextmenu", (e) => e.preventDefault());
  b.addEventListener("click", () => {   // never archives: a short press or Enter's synthetic click only explains the gesture
    const main = b.closest(".tprow") && b.closest(".tprow").querySelector(".tpmain");
    if (main && !main.querySelector(".tpholdhint")) main.append(h("div", "small muted tphint tpholdhint", "Hold to archive"));
  });
  return b;
}

// ----- switch (Active / Inactive), Restore and Archive: confirmed, not optimistic -----
async function tpChange(t, fields, done) {
  TP.busy.add(t.id); TP.rowMsg.delete(t.id); TP.rowNote.delete(t.id); $("tpmsg").textContent = ""; renderTopics();
  const r = await tpPost({ id: t.id, if_match: t.updated_at, fields }, "update").catch(() => null);
  TP.busy.delete(t.id);
  const moved = r && r.ok && ("weight" in fields || "is_active" in fields || "is_archived" in fields);
  if (r && r.ok) { tpApply(r.data.topic); if (done) done(r.data.topic); }
  else TP.rowMsg.set(t.id, tpErrText(r, t));
  renderTopics();
  if (moved) await loadTopics();   // ranks, the cut-off and the counts moved: one fresh read (the server already dropped its cache)
}
const tpToggle = (t, active) => tpChange(t, { is_active: active });
// Restore goes to Inactive (never the cap, never straight back into a run); an old backend only has the switch, so it turns on.
function tpRestore(t) {
  if (TP.data && TP.data.states_supported === false) return tpToggle(t, true);
  return tpChange(t, { is_archived: false }, (topic) => TP.rowNote.set(topic.id, "Restored as inactive. Switch it on to research it again."));
}
function tpArchive(t) {
  return tpChange(t, { is_archived: true }, () => { $("tpmsg").textContent = `Archived "${t.name}". Set State to Archived to restore it.`; });
}
const tpPost = async (body, kind) => {
  const r = await POST(`/api/topics/${kind}`, body);
  if (r.status === 401) { loadMe(); TP.data = { state: "not_paired", topics: [] }; }
  return r;
};
function tpErrText(r, t) {
  const d = (r && r.data) || {};
  if (!r) return TP_MSG.error;
  if (d.code === "cap") return `${tpAgentLabel(t && t.agent_type)} already has ${TP_CAP} active topics. Switch one off first.`;
  return TP_MSG[d.code] || TP_STATE_MSG[d.code] || TP_MSG.error;
}
function tpApply(topic) {
  if (!TP.data || !topic) return;
  const list = TP.data.topics || (TP.data.topics = []);
  const i = list.findIndex((x) => x.id === topic.id);
  if (i >= 0) list[i] = topic; else list.push(topic);
  TP.flash = topic.id;
  setTimeout(() => { if (TP.flash === topic.id) { TP.flash = null; renderTopics(); } }, 1600);
}

// ----- add / edit dialog -----
const TP_FIELDS = ["agent_type", "name", "keywords", "excluded_terms", "weight"];
function tpShowErrs(errs) {
  for (const f of TP_FIELDS) $(`tperr-${f}`).textContent = errs[f] ? tpFieldMsg(f, errs[f]) : "";
}
function tpCounters() {
  const k = tpTerms($("tpkw").value, 0, 999), e = tpTerms($("tpex").value, 0, 999);
  $("tpkwn").textContent = `${(k.val || []).length}/${TP_MAXKW}`;
  $("tpexn").textContent = `${(e.val || []).length}/${TP_MAXEX}`;
}
function tpDialog(t) {
  TP.editing = t || null;
  const blank = { agent_type: TP.agent !== "all" ? TP.agent : "general", name: "", keywords: [], excluded_terms: [], weight: 1 };
  const v = t || blank;
  TP.orig = { name: v.name, keywords: [...v.keywords], excluded_terms: [...v.excluded_terms], weight: v.weight, is_active: v.is_active !== false };
  $("tptitle").textContent = t ? "Edit topic" : "Add topic";
  $("tpagent").value = v.agent_type;
  $("tpname").value = v.name; $("tpkw").value = v.keywords.join("\n"); $("tpex").value = v.excluded_terms.join("\n"); $("tpweight").value = String(v.weight);
  $("tpformmsg").textContent = ""; $("tpformmsg").className = "msg";
  for (const id of ["tprestoredup", "tpreload"]) $(id).hidden = true;
  const note = $("tpnote");
  note.hidden = !(t && t.over_api_limits);
  note.textContent = note.hidden ? "" : "This topic was created with the CLI and exceeds what the API accepts. Shorten the highlighted fields to save.";
  tpShowErrs(t && t.over_api_limits ? tpCollect().errs : {});
  tpBusy(false); tpCounters();
  $("tpdlg").showModal();
  $("tpname").focus();
}
function tpBusy(on) {
  TP.saving = on;
  for (const id of ["tpname", "tpkw", "tpex", "tpweight", "tpsave", "tprestoredup", "tpreload"]) $(id).disabled = on;
  $("tpagent").disabled = on || !!TP.editing;
}
function tpFormFail(r) {
  const d = (r && r.data) || {};
  const msg = $("tpformmsg");
  msg.className = "msg err";
  msg.textContent = tpErrText(r, TP.editing || { agent_type: $("tpagent").value });
  if (d.fields && typeof d.fields === "object") tpShowErrs(Object.fromEntries(Object.entries(d.fields).filter(([f]) => TP_FIELDS.includes(f))));
  $("tprestoredup").hidden = !(d.code === "duplicate" && d.existing_active === false && typeof d.existing_id === "string");
  if (!$("tprestoredup").hidden) {
    $("tprestoredup").setAttribute("data-id", d.existing_id);
    $("tprestoredup").textContent = d.existing_archived === false ? "Switch the existing one on" : "Restore the archived one";
  }
  $("tpreload").hidden = d.code !== "changed";
}
$("tpform").addEventListener("submit", async (ev) => {
  if (ev.submitter && ev.submitter.value !== "save") return;
  ev.preventDefault();
  const { val, errs } = tpCollect();
  tpShowErrs(errs);
  if (Object.keys(errs).length) { $("tpformmsg").textContent = "Fix the highlighted fields."; $("tpformmsg").className = "msg err"; return; }
  let body, kind;
  if (TP.editing) {
    const fields = {};
    for (const f of ["name", "keywords", "excluded_terms", "weight"]) if (JSON.stringify(val[f]) !== JSON.stringify(TP.orig[f])) fields[f] = val[f];
    if (!Object.keys(fields).length) { $("tpformmsg").textContent = "Nothing changed."; $("tpformmsg").className = "msg"; return; }
    body = { id: TP.editing.id, if_match: TP.editing.updated_at, fields }; kind = "update";
  } else { body = { agent_type: $("tpagent").value, ...val }; kind = "create"; }
  tpBusy(true); $("tpformmsg").textContent = "Saving…"; $("tpformmsg").className = "msg";
  const r = await tpPost(body, kind).catch(() => null);
  tpBusy(false);
  if (r && r.ok) {
    tpApply(r.data.topic); $("tpdlg").close(); renderTopics();
    if (kind === "create" || (body.fields && "weight" in body.fields)) await loadTopics();   // a new or re-weighted topic moves the ranks
    return;
  }
  tpFormFail(r);
});
$("tpcancel").addEventListener("click", () => $("tpdlg").close());
for (const id of ["tpkw", "tpex"]) $(id).addEventListener("input", tpCounters);
// 412: re-fetch the list, keep what was typed, and compare against the fresh server state
$("tpreload").addEventListener("click", async () => {
  const id = TP.editing && TP.editing.id;
  await loadTopics(true);
  const fresh = ((TP.data && TP.data.topics) || []).find((x) => x.id === id);
  if (!fresh) { $("tpformmsg").textContent = TP_MSG.not_found; return; }
  TP.editing = fresh;
  TP.orig = { name: fresh.name, keywords: [...fresh.keywords], excluded_terms: [...fresh.excluded_terms], weight: fresh.weight, is_active: fresh.is_active };
  $("tpreload").hidden = true;
  $("tpformmsg").textContent = "Reloaded. Your typed values are kept: review them, then save again.";
  $("tpformmsg").className = "msg";
});
// 409 duplicate with an inactive or archived twin: offer to switch it on (PATCH is_active:true makes it Active)
$("tprestoredup").addEventListener("click", async () => {
  const id = $("tprestoredup").getAttribute("data-id");
  tpBusy(true);
  const r = await tpPost({ id, fields: { is_active: true } }, "update").catch(() => null);
  tpBusy(false);
  if (r && r.ok) { $("tpdlg").close(); await loadTopics(true); TP.flash = id; renderTopics(); return; }
  tpFormFail(r);
});
$("tpadd").onclick = () => tpDialog(null);
$("tprefresh").onclick = () => loadTopics(true);
let tpTimer = 0;
$("tpfilter").addEventListener("input", () => {
  clearTimeout(tpTimer);
  tpTimer = setTimeout(() => {
    const was = !!TP.filter.trim();
    TP.filter = $("tpfilter").value;
    if (!was) TP.sortUser = false;   // a new search starts on best match until a sort is picked
    tpReset(); renderTopics();
  }, 120);
});
$("tpfilter").addEventListener("keydown", (e) => {
  if (e.key === "ArrowDown" && TP_ROWS.length) { e.preventDefault(); tpFocusRow(TP_ROWS.findIndex((x) => x.getAttribute("data-id") === TP.focusId)); return; }
  if (e.key !== "Escape" || !$("tpfilter").value) return;
  e.preventDefault(); clearTimeout(tpTimer);
  $("tpfilter").value = ""; TP.filter = ""; TP.sortUser = false; tpReset(); renderTopics();
});
// Toolbar: every change resets paging and is persisted (not the search text).
for (const [id, field, list] of [["tpstate", "state", TP_STATES], ["tprun", "run", TP_RUNS], ["tporigin", "origin", TP_ORIGINS]]) {
  $(id).addEventListener("change", () => { if (list.includes($(id).value)) TP[field] = $(id).value; tpReset(); tpPersist(); renderTopics(); });
}
$("tpsort").addEventListener("change", () => {
  const v = $("tpsort").value;
  if (v === "match") TP.sortUser = false; else if (TP_SORTS.includes(v)) { TP.sort = v; TP.sortUser = true; }
  tpReset(); tpPersist(); renderTopics();
});
$("tptrial").addEventListener("click", () => { TP.trial = !TP.trial; tpReset(); tpPersist(); renderTopics(); });
$("tpdensity").addEventListener("click", () => { TP.density = TP.density === "compact" ? "comfortable" : "compact"; tpPersist(); renderTopics(); });
// "/" jumps to the search from anywhere in the Topics view, unless a field or the dialog has the focus.
document.addEventListener("keydown", (e) => {
  if (e.key !== "/" || e.ctrlKey || e.metaKey || e.altKey || VIEW !== "research" || RSVIEW !== "topics" || $("tpdlg").open) return;
  const a = document.activeElement, tag = a && String(a.tagName || "").toLowerCase();
  if (tag === "input" || tag === "textarea" || tag === "select" || (a && a.isContentEditable)) return;
  e.preventDefault(); $("tpfilter").focus();
});
document.addEventListener("visibilitychange", () => { if (document.hidden) tpHoldCancel(); });
// Fetched on open, after every write, on Refresh and when the page comes back after 60 s: never polled in the background.
document.addEventListener("visibilitychange", () => {
  if (!document.hidden && VIEW === "research" && RSVIEW === "topics" && TP.data && Date.now() / 1000 - (TP.data.loaded_at || 0) > 60) loadTopics();
});
// ---------- end Topics panel ----------

setInterval(() => {
  if (document.hidden) return;
  if (VIEW === "research") rsTick();
  if (!STATE) return;
  STATE.now = Date.now() / 1000 + CLOCK_OFFSET;
  renderCounters(); renderSessions();
  if (VIEW === "agents") { renderGraph(); renderTimeline(); }
}, 1000);

loadMe();
loadResearch();  // also keeps the tab badge current while another tab is open
setInterval(() => { if (!document.hidden) loadResearch(); }, 30000);
$("rsrefresh").onclick = () => loadResearch(true);
if (location.hash === "#research/topics") RSVIEW = "topics"; else if (location.hash === "#research/activity") RSVIEW = "activity";
setRsView(RSVIEW, true);
setView({ "#events": "events", "#research": "research", "#research/topics": "research", "#research/activity": "research" }[location.hash] || store.get("niu.view", "agents"));
