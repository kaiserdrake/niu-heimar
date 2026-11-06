"""Items that need the user's attention, derived from dashboard state + PR summary (shared by web and TUI)."""

HIGH, MEDIUM, LOW = 3, 2, 1


def _label(domain, project, host):
    d = host if domain == "(workspace)" else domain
    return f"{d}/{project}" if project else d


def actions(state, prs, host=None):
    """Return a list of {id, kind, priority, title, detail, target} sorted by priority (highest first)."""
    host = host or state.get("host") or "workspace"
    out = []
    for s in state.get("sessions", []):
        if s.get("ended"):
            continue
        where = _label(s["domain"], s.get("project") or "", host)
        if s.get("state") == "waiting":
            note = (s.get("note") or "").lower()
            what = "needs your permission" if "permission" in note else "is waiting for your reply" if "idle" in note else "is waiting for you"
            out.append({"id": f"wait:{s['id']}", "kind": "waiting", "priority": HIGH,
                        "title": f"Session {what}", "detail": where, "target": {"session": s["id"]}})
        # Unreviewed edits are not a "needs you" item: the mandatory reviewers run once per PR (CLAUDE.md §2).
        # Session cards still show the passive "not yet reviewed" marker (niu_state 'flagged').
    for r in prs.get("repos", []):
        for p in r.get("prs", []):
            if p.get("draft"):
                continue
            base = {"target": {"pr": p["ref"], "url": p.get("url")}, "detail": f"{p['ref']} · {p.get('title', '')}"}
            if p.get("needs_scope"):
                out.append({**base, "id": f"scope:{p['ref']}", "kind": "pr-blocked", "priority": HIGH,
                            "title": "PR blocked: gh token needs the workflow scope"})
            elif p.get("checks", {}).get("failed"):
                out.append({**base, "id": f"fail:{p['ref']}", "kind": "pr-failing", "priority": MEDIUM,
                            "title": f"PR has {p['checks']['failed']} failing check(s)"})
            elif p.get("review") == "CHANGES_REQUESTED":
                out.append({**base, "id": f"changes:{p['ref']}", "kind": "pr-changes", "priority": MEDIUM,
                            "title": "PR has changes requested"})
            elif p.get("green"):
                out.append({**base, "id": f"ready:{p['ref']}", "kind": "pr-ready", "priority": MEDIUM,
                            "title": "PR ready to merge"})
    out.sort(key=lambda a: -a["priority"])
    return out
