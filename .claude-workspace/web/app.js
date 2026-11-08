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
  const hash = VIEW === "agents" ? "" : VIEW === "research" && RSVIEW === "topics" ? "#research/topics" : `#${VIEW}`;
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
for (const id of ["graph", "timeline", "llmcard", "usagegraph", "tokbody", "rshead", "rsgate", "rsquota", "rslanes", "rslast", "tplist"]) {
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

function packet(pathId, cls, { reverse = false, delay = 0, dur = 0.95 } = {}) {
  if (reducedMotion()) return;
  for (const [r, extra] of [[7, " pk-trail"], [4, ""]]) {
    const c = svg("circle", { r, class: `pk pk-${cls}${extra}`, cx: 0, cy: 0, opacity: 0 });
    const m = svg("animateMotion", { dur: `${dur}s`, begin: "indefinite", fill: "freeze", calcMode: "spline",
      keyPoints: reverse ? "1;0" : "0;1", keyTimes: "0;1", keySplines: ".4 0 .2 1" });
    m.append(svg("mpath", { href: `#${pathId}` }));
    c.append(m);
    G.layers.packets.append(c);
    setTimeout(() => { c.setAttribute("opacity", 1); try { m.beginElement(); } catch { /* SMIL unsupported */ } }, delay);
    setTimeout(() => c.remove(), delay + dur * 1000 + 80);
  }
}

function burst(n, cls, text) {
  if (reducedMotion() || !n) return;
  const ring = svg("rect", { class: `ripple rp-${cls}`, x: n.x, y: n.y, width: NW, height: NH, rx: 10 });
  G.layers.fx.append(ring);
  setTimeout(() => ring.remove(), 950);
  if (text) {
    const t = svg("text", { class: `toolpop pk-${cls}`, x: n.x + NW - 8, y: n.y - 4, "text-anchor": "end" }, text);
    G.layers.fx.append(t);
    setTimeout(() => t.remove(), 1450);
  }
}

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
  const scale = Math.max(0.55, Math.min(1.15, cw / W, ch / H));
  G.root.setAttribute("width", Math.round(W * scale)); G.root.setAttribute("height", Math.round(H * scale));
  G.zoom.setAttribute("transform", `scale(${scale.toFixed(4)})`);
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
function renderPRs() {
  if (!PRS) { $("prs").innerHTML = `<div class="empty">Loading…</div>`; return; }
  const dom = $("domain").value;
  const repos = PRS.repos.filter((r) => !dom || r.label === dom || r.label.startsWith(dom + "/"));
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

function showDetails(sel, refresh) {
  SELECTED = sel;
  const s = STATE.sessions.find((x) => x.id === sel.sid);
  if (!s) { if (!refresh) $("details").hidden = true; return; }
  const a = sel.aid && s.subagents.find((x) => x.id === sel.aid);
  const title = a ? `${esc(a.type)} <span class="src src-${esc(a.source)}">${esc(a.source)}</span>` : esc(projLabel(s));
  const tools = (a ? a.tools : s.tools).slice(-15).reverse();
  const feed = STATE.feed.filter((e) => e.session_id === s.id && (!a || e.agent_id === a.id)).slice(0, 15);
  $("details").innerHTML = `<button class="ghost x" id="dclose">Close</button><h2>${title}</h2>
    <div class="small muted">session ${esc(s.id)} · ${esc(dl(s.domain))}${a && a.task ? ` · task: ${esc(a.task)}` : ""}</div>
    <h2 class="mt">Recent activity</h2><div class="feed">${feed.map((e) =>
      `<div><span class="muted">${hhmmss(e.ts)}</span> ${esc(e.event)} ${esc([e.tool, e.summary].filter(Boolean).join(" "))}</div>`).join("") ||
      tools.map(([ts, t]) => `<div><span class="muted">${hhmmss(ts)}</span> ${esc(t)}</div>`).join("") || `<div class="empty">No tool calls yet.</div>`}</div>`;
  $("details").hidden = false;
  $("dclose").onclick = () => { SELECTED = null; $("details").hidden = true; };
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
    const fix = e.up ? "" : e.name === "local"
      ? "Not reachable: publish the container on 127.0.0.1:11434 or set OLLAMA_LOCAL_BASE_URL."
      : "Not reachable: check the machine and OLLAMA_HOST=0.0.0.0. Local agents fall back to the CPU.";
    const tip = [`${EP_NAME[e.name] || e.label}: ${word[st]}`, `${e.url} · ${e.mode} mode`,
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
  open: store.get("niu.usage.open", true) !== false, h5: store.get("niu.usage.5h", false) === true, data: null, state: "loading", timer: 0 };
const USAGE_MSG = {
  loading: "Loading…",
  empty: "Collecting: the first sample appears within 5 minutes. Earlier weeks can't be recovered.",
  disabled: "Usage history is off (NIU_USAGE_COLLECT=0).",
  error: "Can't read usage history from the dashboard server.",
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

function renderUsage(u) {
  USAGE.data = u && u.state === "ok" ? u : null;
  const box = $("usagegraph"), sum = $("usagesum"), upd = $("usageupd");
  const note = (text, cls) => { box.replaceChildren(h("div", cls || "empty", text)); sum.textContent = ""; };
  if (u && u.state === "loading") { upd.textContent = ""; return note(USAGE_MSG.loading); }
  if (!u || typeof u !== "object" || !["ok", "empty", "disabled"].includes(u.state) || (u.state === "ok" && usageNum(u.now) === null)) { upd.textContent = ""; return note(USAGE_MSG.error, "msg err"); }
  if (u.state !== "ok") { upd.textContent = ""; return note(USAGE_MSG[u.state]); }
  const W = Math.max(320, Math.round(box.clientWidth || 640)), H = 230, L = 40, R = 14, T = 18, B = 28;
  const days = USAGE.weeks * 7, cur = u.current && typeof u.current === "object" ? u.current : null;
  const tmax = cur && usageNum(cur.resets_at) !== null && cur.resets_at > u.now ? cur.resets_at : u.now, tmin = tmax - days * USAGE_DAY;
  const X = (t) => L + ((t - tmin) / (tmax - tmin)) * (W - L - R), Y = (v) => T + (1 - Math.min(100, Math.max(0, v)) / 100) * (H - T - B);
  const root = svg("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": usageSummary(u), class: "usagesvg", preserveAspectRatio: "xMidYMid meet" });
  const defs = svg("defs");
  const pat = svg("pattern", { id: "usagehatch", width: "6", height: "6", patternUnits: "userSpaceOnUse", patternTransform: "rotate(45)" });
  pat.append(svg("line", { x1: "0", y1: "0", x2: "0", y2: "6", class: "hatchln" }));
  defs.append(pat); root.append(defs);
  // axes: 0, 50, 80 (the 20% weekly floor of the research gate) and 100
  for (const v of [0, 50, 80, 100]) {
    root.append(svg("line", { x1: L, x2: W - R, y1: Y(v), y2: Y(v), class: `axis${v === 80 ? " guide" : ""}` }));
    root.append(svg("text", { x: L - 5, y: Y(v) + 3.5, class: "lbl", "text-anchor": "end" }, `${v}%`));
  }
  root.append(svg("text", { x: W - R, y: Y(80) - 3, class: "lbl guidelbl", "text-anchor": "end" }, "20% weekly floor"));
  const step = USAGE.weeks <= 2 ? 2 : USAGE.weeks <= 5 ? 5 : 10;
  const d0 = new Date(tmin * 1000); d0.setHours(0, 0, 0, 0);
  for (let d = new Date(d0), n = 0; d.getTime() / 1000 <= tmax && n < 40; d.setDate(d.getDate() + step), n++) {
    const t = d.getTime() / 1000;
    if (t < tmin) continue;
    root.append(svg("line", { x1: X(t), x2: X(t), y1: H - B, y2: H - B + 4, class: "axis" }));
    root.append(svg("text", { x: X(t), y: H - 10, class: "lbl", "text-anchor": "middle" }, usageDay(t)));
  }
  // reset markers (finished weeks and the current reset)
  const resets = [...(Array.isArray(u.resets) ? u.resets : []), ...(cur && usageNum(cur.resets_at) !== null ? [cur.resets_at] : [])].filter((t) => usageNum(t) !== null && t >= tmin && t <= tmax);
  for (const t of new Set(resets)) {
    root.append(svg("line", { x1: X(t), x2: X(t), y1: T, y2: H - B, class: "reset" }));
    root.append(svg("text", { x: X(t) + 3, y: T + 9, class: "lbl resetlbl" }, `↻ ${usageWhen(t)}`));
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
  const line = (key, cls) => {
    const segs = usageSegments(u.series && u.series[key]);
    for (const seg of segs) {
      const pts = seg.filter((p) => p[0] >= tmin && p[0] <= tmax + 1);
      if (!pts.length) continue;
      if (pts.length === 1) { root.append(svg("circle", { cx: X(pts[0][0]), cy: Y(pts[0][1]), r: 2.2, class: `udot ${cls}` })); continue; }
      root.append(svg("path", { d: pts.map((p, i) => `${i ? "L" : "M"}${X(p[0]).toFixed(1)} ${Y(p[1]).toFixed(1)}`).join(" "), class: `ul ${cls}` }));
    }
  };
  if (USAGE.h5) line("five_hour", "ul-5h");
  line("seven_day_opus", "ul-opus"); line("seven_day_sonnet", "ul-sonnet"); line("seven_day", "ul-week");
  // weeks that ended: a marker and "ended N%" ("ended ≥N%" when sampling stopped well before the reset)
  for (const w of usageWeeks(u)) {
    if (w.last_sample < tmin) continue;
    const lvl = usageLevel(w.ended_pct), x = X(w.last_sample), y = Y(w.ended_pct), left = x > L + 90;
    root.append(svg("circle", { cx: x, cy: y, r: 4, class: `wkend ${lvl}`, "data-tip": `Week ended ${usageWhen(w.resets_at)}\nhighest value ${w.partial ? "at least " : ""}${Math.round(w.ended_pct)}%\n${lvl === "ok" ? "below 70%" : lvl === "warn" ? "70 to 89%" : "90% or more"}${w.partial ? "\nsamples stopped early" : ""}` }));
    root.append(svg("text", { x: left ? x - 7 : x + 7, y: Math.max(T + 22, y - 7), class: `lbl wklbl ${lvl}`, "text-anchor": left ? "end" : "start" },
      `${USAGE_GLYPH[lvl]} ended ${w.partial ? "≥" : ""}${Math.round(w.ended_pct)}%`));
  }
  // now marker and pace
  if (cur && usageNum(cur.used) !== null && u.now >= tmin) {
    const pace = usagePace(u);
    if (pace !== null) {
      root.append(svg("line", { x1: X(u.now), y1: Y(cur.used), x2: X(cur.resets_at), y2: Y(pace), class: "pace" }));
      root.append(svg("text", { x: X(cur.resets_at) - 4, y: Y(pace) + (pace > 85 ? 13 : -5), class: "lbl pacelbl", "text-anchor": "end" }, `pace ≈ ${pace}%`));
    }
    const lvl = usageLevel(cur.used);
    root.append(svg("circle", { cx: X(u.now), cy: Y(cur.used), r: 4, class: `nowdot ${lvl}` }));
    root.append(svg("text", { x: X(u.now) - 7, y: Y(cur.used) - 7, class: `lbl nowlbl ${lvl}`, "text-anchor": "end" }, `now ${Math.round(cur.used)}%`));
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
  const kids = [];
  if (cur && cur.stale) kids.push(h("div", "usagenote warn small", `No new readings since ${hm(cur.updated)}: no terminal Claude session, and the usage API is off or unreachable.`));
  const rec = u.recorder;
  if (rec && rec.errors > 0 && USAGE_REC[rec.last_error]) kids.push(h("div", "usagenote warn small", `Recorder: ${USAGE_REC[rec.last_error]} (${rec.errors} error${rec.errors === 1 ? "" : "s"} since the dashboard started).`));
  box.replaceChildren(...kids, root);
  sum.textContent = usageSummary(u);
  upd.textContent = [u.collecting_since ? `collecting since ${usageDay(u.collecting_since)}` : "", cur ? `updated ${hm(cur.updated)}${USAGE_SRC[cur.source] ? ` · ${USAGE_SRC[cur.source]}` : ""}` : ""].filter(Boolean).join(" · ");
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
function setUsageOpen(open) {
  USAGE.open = open; store.set("niu.usage.open", open);
  $("usagepanel").classList.toggle("collapsed", !open);
  $("usagetoggle").setAttribute("aria-expanded", String(open));
  $("usagetoggle").setAttribute("aria-label", `${open ? "Collapse" : "Expand"} Weekly usage`);
  $("usagetoggle").textContent = open ? "▾" : "▸";
  if (open) loadUsage();
}
function setUsageWeeks(n) {
  USAGE.weeks = n; store.set("niu.usage.weeks", n);
  for (const w of [2, 5, 10]) { $(`usage-w${w}`).classList.toggle("on", w === n); $(`usage-w${w}`).setAttribute("aria-pressed", String(w === n)); }
  USAGE.data = null; loadUsage();
}
$("usagetoggle").onclick = () => setUsageOpen(!USAGE.open);
for (const w of [2, 5, 10]) $(`usage-w${w}`).onclick = () => setUsageWeeks(w);
$("usage5h").checked = USAGE.h5;
$("usage5h").onchange = () => { USAGE.h5 = $("usage5h").checked; store.set("niu.usage.5h", USAGE.h5); redrawUsage(); };
setUsageOpen(USAGE.open);
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
const FAMILY = ["opus", "sonnet", "haiku", "other"];
const TOK = { range: Object.hasOwn(TOK_RANGES, store.get("niu.tokens.range", "7d")) ? store.get("niu.tokens.range", "7d") : "7d",
  metric: Object.hasOwn(TOK_METRICS, store.get("niu.tokens.metric", "total")) ? store.get("niu.tokens.metric", "total") : "total",
  open: store.get("niu.tokens.open", true) !== false, data: null, asof: 0, seq: 0, stale: false };
const TOK_MSG = {
  loading: "Loading…",
  empty: "No Claude usage found in the transcripts yet.",
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
    h("p", "small", "Counted from Claude Code transcripts on this host (~/.claude/projects), for terminal and desktop sessions and their subagents. With All domains this includes projects outside the workspace."),
    h("p", "small", "Cache reads dominate the totals: every call re-reads the whole conversation from the cache, so a long session adds many millions of cache-read tokens. Switch the metric to Output to see what the models wrote."),
    h("p", "small", "Not counted: sessions the dashboard did not observe (other devices, claude.ai chats, transcripts already deleted by Claude Code's cleanup, 30 days by default), local-model agents (niu-agent), and the research bot, which is shown separately."),
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
  const box = $("tokbody"), sum = $("toksum"), upd = $("tokupd");
  TOK.data = t && (t.state === "ok" || t.state === "scanning") ? t : null;
  const note = (text, cls) => { box.replaceChildren(h("div", cls || "empty", text)); sum.textContent = ""; };
  if (t && t.state === "loading") { upd.textContent = ""; return note(TOK_MSG.loading); }
  const okShape = t && typeof t === "object" && t.totals && typeof t.totals === "object" && t.kpis && typeof t.kpis === "object" && Array.isArray(t.by_agent) && Array.isArray(t.by_model);
  if (!t || typeof t !== "object" || !["ok", "scanning", "empty", "disabled"].includes(t.state) || (["ok", "scanning"].includes(t.state) && !okShape)) { TOK.data = null; upd.textContent = ""; return note(TOK_MSG.error, "msg err"); }
  const cov = t.coverage && typeof t.coverage === "object" ? t.coverage : {};
  if (t.state === "empty" || t.state === "disabled") {
    upd.textContent = "";
    if (t.state === "empty" && cov.scanning) return note(`Reading transcripts for the first time: ${tokInt(tokNum(cov.files_done))} of ${tokInt(tokNum(cov.files_total))} files`);
    return note(TOK_MSG[t.state]);
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
    const stat = h("div", `stat tokkpi${r === TOK.range ? " on" : ""}`, "", { "data-tip": `${TOK_RANGES[r]}\ntotal ${tokInt(tokNum(v.total))} tokens\noutput ${tokInt(tokNum(v.output))} tokens` });
    stat.append(h("span", "small muted", TOK_RANGES[r]), h("b", "", tokFmt(tokNum(v.total))), h("span", "small muted", `output ${tokFmt(tokNum(v.output))}`));
    kp.append(stat);
  }
  kids.push(kp);
  const T = t.totals;
  kids.push(h("div", "toktotals small", `${TOK_RANGES[TOK.range]} · in ${tokFmt(tokNum(T.input))} · out ${tokFmt(tokNum(T.output))} · cache write ${tokFmt(tokNum(T.cache_write))} · cache read ${tokFmt(tokNum(T.cache_read))} · ${tokInt(tokNum(T.messages))} calls · ${tokInt(tokNum(T.sessions))} sessions`));
  const lg = h("div", "toklegend small muted");
  for (const [, label, cls] of TOK_KINDS) { const it = h("span", "lgitem", label); it.prepend(tokSwatch(cls)); lg.append(it); }
  kids.push(lg);
  kids.push(...tokRankBlock("By agent type (ranked by " + TOK_METRICS[metric][0].toLowerCase() + ")", agents, metric, "agent"));
  const rs = tokResearchRow();
  if (rs) kids.push(rs);
  kids.push(...tokRankBlock("By model", models, metric, "model"));
  kids.push(...tokDaily(t.daily, t.range));
  kids.push(tokMatrix(t.matrix), tokCounted());
  box.replaceChildren(...kids);
  sum.textContent = tokSummary(t, { agents, models });
  upd.textContent = [`updated ${hm(TOK.asof || t.now)}`, tokNum(cov.oldest) ? `data from ${usageDay(cov.oldest)}` : ""].filter(Boolean).join(" · ");
}
function redrawTokens() { if (TOK.open && TOK.data) renderTokens(TOK.data); }
async function loadTokens() {
  if (VIEW !== "events" || document.hidden || !TOK.open) return;
  const mine = ++TOK.seq, range = TOK.range, dom = $("domain").value;
  if (!TOK.data) renderTokens({ state: "loading" });
  const url = `/api/usage/tokens?range=${range}${dom ? `&domain=${encodeURIComponent(dom)}` : ""}`;
  const d = await fetch(url, { credentials: "same-origin" }).then((r) => (r.ok ? r.json() : null)).catch(() => null);
  if (VIEW !== "events" || mine !== TOK.seq) return;
  if (d && typeof d.state === "string") { TOK.stale = false; TOK.asof = Date.now() / 1000; renderTokens(d); return; }
  if (TOK.data) { TOK.stale = true; renderTokens(TOK.data); } else renderTokens(null);
}
function setTokOpen(open) {
  TOK.open = open; store.set("niu.tokens.open", open);
  $("tokpanel").classList.toggle("collapsed", !open);
  $("toktoggle").setAttribute("aria-expanded", String(open));
  $("toktoggle").setAttribute("aria-label", `${open ? "Collapse" : "Expand"} Token usage`);
  $("toktoggle").textContent = open ? "▾" : "▸";
  if (open) loadTokens();
}
function setTokRange(r) {
  TOK.range = r; store.set("niu.tokens.range", r);
  for (const k of Object.keys(TOK_RANGES)) { $(`tok-r-${k}`).classList.toggle("on", k === r); $(`tok-r-${k}`).setAttribute("aria-pressed", String(k === r)); }
  TOK.data = null; loadTokens();
}
$("toktoggle").onclick = () => setTokOpen(!TOK.open);
for (const r of Object.keys(TOK_RANGES)) $(`tok-r-${r}`).onclick = () => setTokRange(r);
$("tokmetric").value = TOK.metric;
$("tokmetric").onchange = () => { const v = $("tokmetric").value; if (!Object.hasOwn(TOK_METRICS, v)) return; TOK.metric = v; store.set("niu.tokens.metric", v); redrawTokens(); };
setTokOpen(TOK.open);
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
const RS_KIND = { standard: "standard", comprehensive: "overnight", delta: "delta" };
const RS_KIND_LETTER = { standard: "S", comprehensive: "C", delta: "D" };
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
  $("rsbody").classList.toggle("stale", !!r.stale);
  rsHead(r); rsGate(r); rsQuota(r); rsLanes(r); rsGateLog(r); rsLast(r); rsErrors(r);
}

// Tab badge: red = unreachable/unauthorized, a failed or quota-stopped last run, or a gate skip in the last day;
// amber = partial, waiting for the gate, conservative; green + pulse = a run is in progress.
function rsBadge(r) {
  const d = r.state === "ok" ? r.data : null;
  let lvl = "", pulse = false, why = "";
  if (r.state === "unreachable" || r.state === "unauthorized" || r.state === "too_powerful") { lvl = "bad"; why = RS_STATE_MSG[r.state]; }
  else if (d) {
    const last = d.last_run, now = rsNow();
    const skipped = (d.recent_runs || []).some((x) => x.status === "skipped_gate" && now - (x.finished_at || x.started_at || 0) < 86400);
    if (d.current_run?.status === "running") { lvl = "ok"; pulse = true; why = "A research run is in progress"; }
    else if (last && (last.status === "failed" || last.status === "aborted_quota")) { lvl = "bad"; why = `Last run ${RS_STATUS[last.status]}`; }
    else if (skipped) { lvl = "bad"; why = "A run was skipped by the gate in the last 24 h"; }
    else if (last?.status === "partial" || d.gate?.pending || (d.recent_runs || []).some((x) => x.status === "waiting_gate") || last?.mode === "conservative") {
      lvl = "warn"; why = "Partial, waiting for the gate or conservative mode";
    }
  }
  const dot = $("rsdot");
  dot.hidden = !lvl;
  dot.className = `tabdot ${lvl ? `rs-${lvl}` : ""} ${pulse ? "pulse" : ""}`;
  $("tab-research").setAttribute("title", why);
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
  const wk = d.gate?.preview?.floors?.weekly_min_remaining_pct ?? 20;
  kids.push(h("div", "rsline muted", `The bot stops when less than ${wk}% of the weekly limit remains.`));
  box.replaceChildren(...kids);
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
  const lanes = [{ key: "gate", label: "gate" }, ...["general", "hobby", "fintrend"].map((a) => ({ key: a, label: a, off: !(feat.agents || []).includes(a) }))];
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
  else if (!rsRuns(d).some((run) => (run.agents || []).some((a) => a.started_at && (a.finished_at || a.started_at) >= t0))) box.append(h("div", "rsline muted", "No agent runs in the last 36 hours."));
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
let RSVIEW = store.get("niu.rsview", "status") === "topics" ? "topics" : "status";
const TP = { data: null, agent: "all", filter: "", archived: false, editing: null, orig: null, flash: null, busy: new Set(), rowMsg: new Map(),
  rowNote: new Map(), expanded: new Set(), hold: null, loading: false, saving: false };
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
  changed: "This topic changed elsewhere (CLI, duenext or feedback). Reload it?",
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
// Topic against a query: null = no match, else { score, name: Set of name code-point indexes to highlight (or null), kw: Set, ex: Set }.
function tpMatch(t, query) {
  const none = { score: 1, name: null, kw: new Set(), ex: new Set() };
  if (!String(query).trim()) return none;
  const tokens = fzQuery(query);
  if (!tokens.length) return null;
  const nameFd = fzPrep(t.name), parts = [{ key: "name", i: 0, fd: nameFd }];
  (Array.isArray(t.keywords) ? t.keywords : []).slice(0, FZ_MAXTERMS).forEach((k, i) => parts.push({ key: "keywords", i, fd: fzPrep(k) }));
  (Array.isArray(t.excluded_terms) ? t.excluded_terms : []).slice(0, FZ_MAXTERMS).forEach((k, i) => parts.push({ key: "excluded", i, fd: fzPrep(k) }));
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
  RSVIEW = v === "topics" ? "topics" : "status";
  store.set("niu.rsview", RSVIEW);
  for (const [id, name] of [["rsview-status", "status"], ["rsview-topics", "topics"]]) {
    $(id).classList.toggle("on", RSVIEW === name); $(id).setAttribute("aria-pressed", String(RSVIEW === name));
  }
  $("rsbody").hidden = RSVIEW === "topics"; $("tpbody").hidden = RSVIEW !== "topics";
  $("rsrefresh").hidden = $("rsupd").hidden = RSVIEW === "topics";
  if (init) return;
  const hash = RSVIEW === "topics" ? "#research/topics" : "#research";
  if (VIEW === "research" && location.hash !== hash) history.replaceState(null, "", hash);
  if (RSVIEW === "topics") loadTopics(); else renderResearch();
}
$("rsview-status").onclick = () => setRsView("status");
$("rsview-topics").onclick = () => setRsView("topics");

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
function renderTopics() {
  tpHoldCancel();   // a rebuilt list must never leave a stale hold timer behind
  if (VIEW !== "research" || RSVIEW !== "topics") return;
  const r = TP.data || { state: "loading", topics: [] };
  const ok = r.state === "ok", all = r.topics || [];
  $("tpbody").classList.toggle("stale", !!r.stale);
  // state banner
  const head = [];
  if (!ok) {
    const msg = (typeof r.message === "string" && r.message) || TP_STATE_MSG[r.state] || TP_STATE_MSG.error;
    head.push(h("div", `tpstate ${TP_BAD_STATE.has(r.state) ? "bad" : "warn"}`, msg));
    if (r.stale && r.loaded_at) head.push(h("div", "small muted", `Showing the list as of ${hm(r.loaded_at)}. Changes are disabled.`));
  }
  if (ok && r.states_supported === false) head.push(h("div", "tpstate warn", "valkyrie-dispatch is older than this dashboard: switching a topic off archives it, and Inactive is unavailable."));
  if (ok && r.inactive_truncated) head.push(h("div", "small muted", "Showing 200 inactive topics; use rd topics list --state inactive for the rest."));
  if (ok && r.archived_truncated) head.push(h("div", "small muted", "Showing 200 archived topics; use rd topics list --all for the rest."));
  if (ok && r.dropped) head.push(h("div", "small muted", `${r.dropped} topic(s) could not be shown (unexpected data).`));
  $("tphead").replaceChildren(...head);
  // toolbar
  const chips = [["all", "All", all.filter((t) => t.is_active).length, null]];
  for (const a of TP_AGENTS) chips.push([a, TP_AGENT_LABEL[a], all.filter((t) => t.is_active && t.agent_type === a).length, TP_CAP]);
  $("tpchips").replaceChildren(...chips.map(([k, label, n, cap]) => {
    const b = h("button", `tpagent${TP.agent === k ? " on" : ""}`, cap ? `${label} ${n}/${cap}` : `${label} ${n}`, { "aria-pressed": String(TP.agent === k) });
    b.onclick = () => { TP.agent = k; renderTopics(); };
    return b;
  }));
  const add = $("tpadd");
  add.disabled = !tpWritable();
  add.setAttribute("title", ME.paired ? (ok ? "" : "Unavailable right now") : "Pair this browser to edit");
  $("tpupd").textContent = r.loaded_at ? `loaded ${hm(r.loaded_at)}` : "";
  // list
  const list = [], q = TP.filter.trim();
  let cnt = "";
  if (ok || r.stale) {
    const pool = all.filter((t) => (TP.archived || !t.is_archived) && (TP.agent === "all" || t.agent_type === TP.agent));
    let shown = pool;
    const matches = new Map();
    if (q) {
      shown = pool.map((t, i) => ({ t, i, m: tpMatch(t, q) })).filter((x) => x.m)
        .sort((a, b) => b.m.score - a.m.score || a.i - b.i).map((x) => (matches.set(x.t.id, x.m), x.t));
      cnt = `${shown.length} of ${pool.length} topics`;
    }
    for (const a of TP_AGENTS) {
      const rows = shown.filter((t) => t.agent_type === a);
      if (!rows.length) continue;
      const active = all.filter((t) => t.is_active && t.agent_type === a).length;
      const idle = all.filter((t) => !t.is_active && !t.is_archived && t.agent_type === a).length;
      list.push(h("h3", "tpgroup", `${TP_AGENT_LABEL[a]} · ${active}/${TP_CAP}${idle ? ` · ${idle} inactive` : ""}`));
      const rank = (x) => x.is_archived ? 2 : x.is_active ? 0 : 1;
      for (const t of [0, 1, 2].flatMap((k) => rows.filter((x) => rank(x) === k))) list.push(tpRow(t, matches.get(t.id)));
    }
    if (!shown.length) {
      list.push(h("div", "empty", !all.length ? "No topics yet. Add one, or use rd topics add in valkyrie-dispatch."
        : TP.agent === "fintrend" && !TP.filter && !all.some((t) => t.agent_type === "fintrend") ? "Fintrend agent arrives in M4; topics you add now are kept."
          : q ? `No topic matches "${[...q].slice(0, 40).join("")}${[...q].length > 40 ? "…" : ""}"` : "No topics match."));
    } else if (TP.agent === "all" && !all.some((t) => t.agent_type === "fintrend")) {
      list.push(h("div", "small muted", "Fintrend agent arrives in M4; topics you add now are kept."));
    }
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

function tpRow(t, m) {
  const kws = Array.isArray(t.keywords) ? t.keywords : [], exs = Array.isArray(t.excluded_terms) ? t.excluded_terms : [];
  const can = tpWritable() && !TP.busy.has(t.id);
  const st = t.is_archived ? "archived" : t.is_active ? "active" : "inactive";
  const row = h("div", `tprow${st === "archived" ? " tp-archived" : st === "inactive" ? " tp-inactive" : ""}${TP.busy.has(t.id) ? " busy" : ""}${TP.flash === t.id ? " tpflash" : ""}`, "", { "data-id": t.id });
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
  const main = h("div", "tpmain");
  const title = h("div", "tptitle");
  const hl = m && m.name && m.name.size;
  const nm = h("span", "tpname", hl ? null : t.name, t.updated_at ? { "data-tip": `Last edited ${t.updated_at}` } : {});
  if (hl) {   // matched characters get their own span; every piece is still plain textContent
    let run = "", on = false;
    const flush = () => { if (run) nm.append(h("span", on ? "tpmatch" : "", run)); run = ""; };
    Array.from(t.name).forEach((ch, i) => { const hit = m.name.has(i); if (hit !== on) { flush(); on = hit; } run += ch; });
    flush();
  }
  title.append(nm);
  if (t.over_api_limits) title.append(h("span", "badge b-warn", "CLI-only limits", { "data-tip": "Created with the CLI; exceeds what the API accepts. Shorten it to edit." }));
  if (st !== "active") title.append(h("span", "badge b-off", st));
  main.append(title);
  const kw = h("div", "tpchips2");
  kws.slice(0, 6).forEach((k, i) => kw.append(h("span", m && m.kw.has(i) ? "tpchip tphit" : "tpchip", k)));
  if (kws.length > 6) kw.append(h("span", "tpchip more", `+${kws.length - 6}`));
  if (exs.length) {
    const open = TP.expanded.has(t.id);
    const ex = h("button", "ghost tiny tpex", open ? "hide excluded" : `excludes ${exs.length}`, { "aria-expanded": String(open) });
    ex.onclick = () => { if (open) TP.expanded.delete(t.id); else TP.expanded.add(t.id); renderTopics(); };
    kw.append(ex);
  }
  main.append(kw);
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
  const w = h("div", "tpw");
  w.append(h("span", "tpwnum", t.weight.toFixed(1)));
  const bar = svg("svg", { class: "tpweight", width: "60", height: "6", viewBox: "0 0 60 6", "aria-hidden": "true" });
  bar.append(svg("rect", { class: "tpw-bg", x: "0", y: "0", width: "60", height: "6", rx: "3" }),
    svg("rect", { class: "tpw-fg", x: "0", y: "0", width: String(Math.round(60 * Math.max(0, Math.min(5, t.weight)) / 5)), height: "6", rx: "3" }));
  w.append(bar);
  row.append(w);
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
  if (r && r.ok) { tpApply(r.data.topic); if (done) done(r.data.topic); }
  else TP.rowMsg.set(t.id, tpErrText(r, t));
  renderTopics();
}
const tpToggle = (t, active) => tpChange(t, { is_active: active });
// Restore goes to Inactive (never the cap, never straight back into a run); an old backend only has the switch, so it turns on.
function tpRestore(t) {
  if (TP.data && TP.data.states_supported === false) return tpToggle(t, true);
  return tpChange(t, { is_archived: false }, (topic) => TP.rowNote.set(topic.id, "Restored as inactive. Switch it on to research it again."));
}
function tpArchive(t) {
  return tpChange(t, { is_archived: true }, () => { $("tpmsg").textContent = `Archived "${t.name}". Turn on Show archived to restore it.`; });
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
  if (r && r.ok) { tpApply(r.data.topic); $("tpdlg").close(); renderTopics(); return; }
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
  tpTimer = setTimeout(() => { TP.filter = $("tpfilter").value; renderTopics(); }, 120);
});
$("tpfilter").addEventListener("keydown", (e) => {
  if (e.key !== "Escape" || !$("tpfilter").value) return;
  e.preventDefault(); clearTimeout(tpTimer);
  $("tpfilter").value = ""; TP.filter = ""; renderTopics();
});
$("tparch").addEventListener("change", () => { TP.archived = $("tparch").checked; renderTopics(); });
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
if (location.hash === "#research/topics") RSVIEW = "topics";
setRsView(RSVIEW, true);
setView({ "#events": "events", "#research": "research", "#research/topics": "research" }[location.hash] || store.get("niu.view", "agents"));
