"use strict";
// niu-heimar web dashboard. All server strings are escaped before rendering.

const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const SVGNS = "http://www.w3.org/2000/svg";
const POST = (url, body) => fetch(url, {
  method: "POST", credentials: "same-origin",
  headers: { "Content-Type": "application/json", "X-Niu-CSRF": "1" },
  body: JSON.stringify(body || {}),
}).then(async (r) => ({ ok: r.ok, status: r.status, data: await r.json().catch(() => ({})) }));

let STATE = null, PRS = null, ME = { paired: false }, ES = null, SELECTED = null;
const STATE_LABEL = { working: "working", waiting: "waiting for you", idle: "idle", ended: "ended", stale: "stale" };
const TOOL_CLASS = (t) => t === "Bash" ? "bash" : ["Read", "Grep", "Glob"].includes(t) ? "read"
  : ["Edit", "MultiEdit", "Write", "NotebookEdit"].includes(t) ? "edit" : ["Agent", "Task", "SendMessage"].includes(t) ? "agent"
  : ["WebFetch", "WebSearch"].includes(t) ? "web" : "other";

function dur(secs) {
  secs = Math.max(0, Math.round(secs));
  if (secs < 60) return `${secs}s`;
  if (secs < 3600) return `${Math.floor(secs / 60)}m${String(secs % 60).padStart(2, "0")}s`;
  if (secs < 86400) return `${Math.floor(secs / 3600)}h${String(Math.floor((secs % 3600) / 60)).padStart(2, "0")}m`;
  return `${Math.floor(secs / 86400)}d${Math.floor((secs % 86400) / 3600)}h`;
}
const hhmmss = (ts) => new Date(ts * 1000).toLocaleTimeString([], { hour12: false });
const visible = () => (STATE?.sessions || []).filter((s) => $("ended").checked || !s.ended);

// ---------- connection ----------
function connect() {
  if (ES) ES.close();
  const q = new URLSearchParams({ since: $("since").value });
  if ($("domain").value) q.set("domain", $("domain").value);
  ES = new EventSource(`/api/stream?${q}`);
  ES.onopen = () => { $("conn").className = "dot on"; };
  ES.onerror = () => { $("conn").className = "dot off"; };
  ES.onmessage = (ev) => {
    const s = JSON.parse(ev.data);
    PRS = s.prs; delete s.prs; STATE = s;
    render();
  };
}

async function loadMe() {
  ME = await fetch("/api/me", { credentials: "same-origin" }).then((r) => r.json()).catch(() => ({ paired: false }));
  $("pairbox").hidden = ME.paired;
  $("me").textContent = ME.paired ? `paired: ${ME.device.name}` : "not paired";
  if (PRS) renderPRs();
}

// ---------- render ----------
function render() {
  renderDomains(); renderCounters(); renderSessions(); renderGraph(); renderTimeline(); renderPRs(); renderToday(); renderFeed();
  if (SELECTED) showDetails(SELECTED, true);
}

function renderDomains() {
  const sel = $("domain"), cur = sel.value;
  const have = new Set([...sel.options].map((o) => o.value));
  for (const d of STATE.domains) if (!have.has(d)) sel.add(new Option(d, d));
  sel.value = cur;
}

function renderCounters() {
  const live = STATE.sessions.filter((s) => !s.ended);
  const subs = live.reduce((n, s) => n + s.subagents.filter((a) => !a.stop).length, 0);
  const waiting = live.filter((s) => s.state === "waiting").length;
  const green = (PRS?.repos || []).reduce((n, r) => n + r.prs.filter((p) => p.green).length, 0);
  $("counters").innerHTML = `<span><b>${live.length}</b> sessions</span><span><b>${subs}</b> subagents running</span>` +
    `<span><b>${waiting}</b> waiting for you</span><span><b>${green}</b> PRs ready</span>`;
}

function renderSessions() {
  const list = visible();
  if (!list.length) { $("sessions").innerHTML = `<div class="empty">No active sessions in the workspace.</div>`; return; }
  const byDom = {};
  for (const s of list) (byDom[s.domain] ||= []).push(s);
  let html = "";
  for (const [dom, ss] of Object.entries(byDom).sort()) {
    html += `<div class="domain-h">${esc(dom)}</div>`;
    for (const s of ss) {
      const cur = s.state === "waiting" && s.note ? `waiting: ${s.note}` : s.current ? `▸ ${s.current[0]}: ${s.current[1]}` : "";
      const chips = s.subagents.filter((a) => !a.stop || STATE.now - a.stop < 600).map((a) =>
        `<span class="chip ${a.stop ? "" : "run"}" title="${esc(a.task)}">${a.stop ? "✓" : "●"} ${esc(a.type)} <span class="src src-${esc(a.source)}">${esc(a.source)}</span>` +
        `${!a.stop && a.current ? ` <span class="muted">${esc(a.current[0])}</span>` : ""}</span>`).join("") +
        s.pending.map((p) => `<span class="chip pend">○ ${esc(p.type)} <span class="src src-${esc(p.source)}">${esc(p.source)}</span></span>`).join("");
      html += `<div class="sess ${s.flagged ? "flag" : ""}" data-sid="${esc(s.id)}">
        <div class="top"><span class="proj">${esc(s.project || (dom === "(workspace)" ? "niu-heimar config" : "(domain root)"))}</span>
        <span class="pill st-${esc(s.state)}">${esc(STATE_LABEL[s.state] || s.state)} · ${dur(STATE.now - s.since)}</span></div>
        <div class="muted small">session ${esc(s.id.slice(0, 8))}</div>
        <div class="cur" title="${esc(cur)}">${esc(cur) || "&nbsp;"}</div>
        <div class="chips">${chips}</div>
        ${s.flagged ? `<div class="flagmsg">⚠ edited files without both reviewers</div>` : ""}</div>`;
    }
  }
  $("sessions").innerHTML = html;
  for (const el of $("sessions").querySelectorAll(".sess")) el.onclick = () => showDetails({ sid: el.dataset.sid });
}

function svg(tag, attrs, text) {
  const el = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs || {})) el.setAttribute(k, v);
  if (text !== undefined) el.textContent = text;
  return el;
}
const trunc = (s, n) => (s = String(s || ""), s.length > n ? s.slice(0, n - 1) + "…" : s);

function renderGraph() {
  const box = $("graph"); box.textContent = "";
  const list = visible().slice(0, 8);
  if (!list.length) { box.innerHTML = `<div class="empty">No interactions to show yet.</div>`; return; }
  const NW = 190, NH = 44, XG = 70, YG = 14, PAD = 10;
  let y = PAD; const nodes = [], edges = [];
  for (const s of list) {
    const kids = {};
    for (const a of s.subagents) (kids[a.parent] ||= []).push(a);
    const place = (id, depth, label, sub, meta) => {
      const node = { id, x: PAD + depth * (NW + XG), y, label, sub, meta };
      nodes.push(node);
      const children = kids[id] || [];
      if (!children.length) y += NH + YG;
      for (const c of children) {
        const child = place(c.id, depth + 1, c.type, c.stop ? `done · ${dur(c.stop - c.start)}` : `running · ${dur(STATE.now - c.start)}`,
          { kind: "sub", sid: s.id, aid: c.id, running: !c.stop, source: c.source });
        edges.push({ a: node, b: child, label: c.task });
      }
      return node;
    };
    const root = place(s.id, 0, s.project || s.domain, `${STATE_LABEL[s.state] || s.state} · ${s.domain}`,
      { kind: "session", sid: s.id, running: s.state === "working" });
    s.messages.forEach((m, i) => {
      const from = nodes.find((n) => n.id === m.from) || root;
      const ext = { id: `msg-${s.id}-${i}`, x: from.x + NW + XG, y: from.y + (i + 1) * 6, label: `→ ${m.to}`, sub: "message", meta: { kind: "ext" } };
      nodes.push(ext); edges.push({ a: from, b: ext, msg: true });
    });
    y += YG;
  }
  const W = Math.max(...nodes.map((n) => n.x + NW)) + PAD, H = y + PAD;
  const root = svg("svg", { width: W, height: H, viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "Agent interaction graph" });
  for (const e of edges) {
    const x1 = e.a.x + NW, y1 = e.a.y + NH / 2, x2 = e.b.x, y2 = e.b.y + NH / 2, mx = (x1 + x2) / 2;
    root.append(svg("path", { d: `M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`, class: `edge ${e.msg ? "msg" : ""}` }));
    if (e.label) root.append(svg("text", { x: x1 + 6, y: y2 - 6, class: "lbl" }, trunc(e.label, 14)));
  }
  for (const n of nodes) {
    const g = svg("g", { class: `node ${n.meta.kind === "session" ? "session" : ""} ${n.meta.running ? "running" : ""}`, transform: `translate(${n.x},${n.y})` });
    g.append(svg("rect", { width: NW, height: NH }));
    g.append(svg("text", { x: 10, y: 18 }, trunc(n.label, 24)));
    g.append(svg("text", { x: 10, y: 34, class: "lbl" }, trunc((n.meta.source ? `[${n.meta.source}] ` : "") + n.sub, 30)));
    g.append(svg("title", {}, n.label));
    if (n.meta.sid) g.addEventListener("click", () => showDetails({ sid: n.meta.sid, aid: n.meta.aid }));
    root.append(g);
  }
  box.append(root);
}

function renderTimeline() {
  const box = $("timeline"); box.textContent = "";
  $("legend").innerHTML = ["bash", "read", "edit", "agent", "web", "other"].map((c) =>
    `<span><i class="lg-${c}"></i>${c}</span>`).join("") + `<span><i class="lg-wait"></i>waiting</span>`;
  const list = visible().slice(0, 8);
  if (!list.length) { box.innerHTML = `<div class="empty">Nothing on the timeline yet.</div>`; return; }
  const lanes = [];
  for (const s of list) {
    lanes.push({ label: s.project || s.domain, sub: `session ${s.id.slice(0, 6)}`, start: s.first, stop: s.ended ? s.last : null, tools: s.tools, waits: s.waits, sid: s.id, session: true });
    for (const a of s.subagents) lanes.push({ label: a.type, sub: a.source, start: a.start, stop: a.stop, tools: a.tools, sid: s.id, aid: a.id });
  }
  const t0 = Math.max(Math.min(...lanes.map((l) => l.start)), STATE.now - 6 * 3600), t1 = STATE.now;
  const LW = 170, W = Math.max(640, box.clientWidth || 900), LH = 24, H = lanes.length * LH + 26;
  const x = (t) => LW + ((Math.max(t, t0) - t0) / Math.max(1, t1 - t0)) * (W - LW - 10);
  const root = svg("svg", { width: W, height: H, viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "Agent timeline" });
  for (let i = 0; i <= 4; i++) {
    const t = t0 + ((t1 - t0) * i) / 4, xx = x(t);
    root.append(svg("line", { x1: xx, x2: xx, y1: 0, y2: H - 20, class: "axis" }));
    root.append(svg("text", { x: xx, y: H - 6, class: "lbl", "text-anchor": i === 4 ? "end" : "middle" }, hhmmss(t)));
  }
  lanes.forEach((l, i) => {
    const y = i * LH + 2;
    root.append(svg("rect", { x: LW, y, width: W - LW - 10, height: LH - 6, class: "lane", rx: 4 }));
    root.append(svg("text", { x: l.session ? 0 : 12, y: y + 14, class: l.session ? "" : "muted" }, trunc(l.label, l.session ? 22 : 20)));
    if (!l.session) {
      const bar = svg("rect", { x: x(l.start), y: y + 2, width: Math.max(3, x(l.stop || t1) - x(l.start)), height: LH - 10, rx: 3, class: `bar-sub ${l.stop ? "" : "running"}` });
      bar.append(svg("title", {}, `${l.label}: ${hhmmss(l.start)} → ${l.stop ? hhmmss(l.stop) : "running"}`));
      root.append(bar);
    }
    for (const [ts, tool] of l.tools || []) {
      if (ts < t0) continue;
      const tick = svg("rect", { x: x(ts) - 1, y: y + 3, width: 2.5, height: LH - 12, class: `tk-${TOOL_CLASS(tool)}` });
      tick.append(svg("title", {}, `${hhmmss(ts)} ${tool}`));
      root.append(tick);
    }
    for (const ts of l.waits || []) if (ts >= t0) root.append(svg("circle", { cx: x(ts), cy: y + LH / 2 - 3, r: 4, class: "wait" }));
  });
  box.append(root);
}

function renderPRs() {
  if (!PRS) { $("prs").innerHTML = `<div class="empty">Loading…</div>`; return; }
  const dom = $("domain").value;
  const repos = PRS.repos.filter((r) => !dom || r.label === dom || r.label.startsWith(dom + "/"));
  let html = PRS.error ? `<div class="msg err">${esc(PRS.error)}</div>` : "";
  if (!repos.some((r) => r.prs.length || r.error)) html += `<div class="empty">No open pull requests.</div>`;
  for (const r of repos) {
    if (!r.prs.length && !r.error) continue;
    html += `<div class="repo"><div class="repo-h">${esc(r.repo)} · ${esc(r.label)}</div>`;
    if (r.error) html += `<div class="msg err">${esc(r.error)}</div>`;
    for (const p of r.prs) {
      const c = p.checks;
      const badge = c.failed ? `<span class="badge b-bad">✗ ${c.failed} failing</span>` : c.pending ? `<span class="badge b-warn">… ${c.pending} pending</span>`
        : `<span class="badge b-ok">✓ ${c.passed} passed</span>`;
      const can = p.green && ME.paired;
      const why = !p.green ? p.reason : !ME.paired ? "pair this browser to merge" : "squash-merge";
      html += `<div class="pr"><div class="t" title="${esc(p.title)}">#${p.number} ${esc(p.title)}</div>
        <div class="acts"><button class="ghost" data-open="${esc(p.url)}">Open</button>
        <button class="danger" data-merge="${esc(p.ref)}" ${can ? "" : "disabled"} title="${esc(why)}">Merge</button></div>
        <div class="meta">${badge}<span>${esc(p.green ? "ready" : p.reason)}</span><span>${esc(p.author || "")}</span><span class="mono">${esc(p.branch || "")}</span></div></div>`;
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
  const runs = t.runs.slice(0, 6).map(([k, v]) => `<span class="chip">${esc(k)} <b>${v}</b></span>`).join("") || `<span class="muted">none yet</span>`;
  $("today").innerHTML = `<div class="stats">
    <div class="stat"><b>${t.sessions}</b><span class="muted small">sessions</span></div>
    <div class="stat"><b>${t.subagent_runs}</b><span class="muted small">subagent runs</span></div>
    <div class="stat"><b>${t.reviewed}/${t.edited}</b><span class="muted small">editing sessions reviewed</span></div></div>
    <div class="chips mt8">${runs}</div>
    ${t.flagged_projects.length ? `<div class="flagmsg">⚠ without both reviewers: ${esc(t.flagged_projects.join(", "))}</div>` : ""}`;
}

function renderFeed() {
  $("feed").innerHTML = STATE.feed.map((e) => {
    const who = e.agent_id ? e.agent_type : `session ${String(e.session_id || "?").slice(0, 4)}`;
    const detail = [e.tool, e.summary].filter(Boolean).join(" ");
    return `<div title="${esc(detail)}"><span class="muted">${hhmmss(e.ts)}</span> <span class="d">${esc(e.domain)}</span> ${esc(who)} <span class="muted">${esc(e.event)}</span> ${esc(detail)}</div>`;
  }).join("") || `<div class="empty">No events yet.</div>`;
}

function showDetails(sel, refresh) {
  SELECTED = sel;
  const s = STATE.sessions.find((x) => x.id === sel.sid);
  if (!s) { if (!refresh) $("details").hidden = true; return; }
  const a = sel.aid && s.subagents.find((x) => x.id === sel.aid);
  const title = a ? `${a.type} <span class="src src-${esc(a.source)}">${esc(a.source)}</span>` : esc(s.project || s.domain);
  const tools = (a ? a.tools : s.tools).slice(-15).reverse();
  const feed = STATE.feed.filter((e) => e.session_id === s.id && (!a || e.agent_id === a.id)).slice(0, 15);
  $("details").innerHTML = `<button class="ghost x" id="dclose">Close</button><h2>${title}</h2>
    <div class="small muted">session ${esc(s.id)} · ${esc(s.domain)}${a && a.task ? ` · task: ${esc(a.task)}` : ""}</div>
    <h2 class="mt">Recent activity</h2><div class="feed">${feed.map((e) =>
      `<div><span class="muted">${hhmmss(e.ts)}</span> ${esc(e.event)} ${esc([e.tool, e.summary].filter(Boolean).join(" "))}</div>`).join("") ||
      tools.map(([ts, t]) => `<div><span class="muted">${hhmmss(ts)}</span> ${esc(t)}</div>`).join("") || `<div class="empty">No tool calls yet.</div>`}</div>`;
  $("details").hidden = false;
  $("dclose").onclick = () => { SELECTED = null; $("details").hidden = true; };
}

// ---------- actions ----------
function openMerge(ref) {
  const [repo, num] = ref.split("#");
  const pr = PRS.repos.flatMap((r) => r.prs).find((p) => p.ref === ref);
  $("mergetitle").textContent = `${ref}: ${pr ? pr.title : ""}`;
  $("mergeref").textContent = ref;
  $("mergeconfirm").value = ""; $("mergemsg").textContent = ""; $("mergemsg").className = "msg";
  $("mergego").disabled = false;
  $("mergedlg").dataset.repo = repo; $("mergedlg").dataset.num = num;
  $("mergedlg").showModal();
  $("mergeconfirm").focus();
}

$("mergeform").addEventListener("submit", async (ev) => {
  if (ev.submitter && ev.submitter.value !== "merge") return;
  ev.preventDefault();
  const dlg = $("mergedlg"), ref = `${dlg.dataset.repo}#${dlg.dataset.num}`, typed = $("mergeconfirm").value.trim();
  if (typed !== ref) { $("mergemsg").textContent = `Type ${ref} exactly to confirm.`; $("mergemsg").className = "msg err"; return; }
  $("mergego").disabled = true; $("mergemsg").textContent = "Merging (re-checking status)…"; $("mergemsg").className = "msg";
  const r = await POST("/api/prs/merge", { repo: dlg.dataset.repo, number: Number(dlg.dataset.num), confirm: typed });
  if (r.ok) { $("mergemsg").textContent = r.data.message || "Merged."; $("mergemsg").className = "msg ok"; setTimeout(() => dlg.close(), 1200); }
  else {
    $("mergemsg").textContent = r.data.error || `Failed (${r.status})`; $("mergemsg").className = "msg err"; $("mergego").disabled = false;
    if (r.status === 401) loadMe();
  }
});
$("mergeconfirm").addEventListener("input", () => { $("mergemsg").textContent = ""; });

$("pairform").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const code = $("paircode").value.replace(/\D/g, "");
  if (code.length !== 6) { $("pairmsg").textContent = "Enter the 6-digit code from niu-dash pair."; $("pairmsg").className = "msg err"; return; }
  const r = await POST("/api/pair", { code, name: $("pairname").value.trim() || navigator.platform || "browser" });
  $("pairmsg").textContent = r.ok ? "Paired." : (r.data.error || "Pairing failed"); $("pairmsg").className = `msg ${r.ok ? "ok" : "err"}`;
  if (r.ok) loadMe();
});
$("paircode").addEventListener("input", () => { $("pairmsg").textContent = ""; });
$("prrefresh").onclick = () => POST("/api/prs/refresh");
$("domain").onchange = connect;
$("since").onchange = connect;
$("ended").onchange = () => STATE && render();
window.addEventListener("resize", () => STATE && renderTimeline());

loadMe();
connect();
