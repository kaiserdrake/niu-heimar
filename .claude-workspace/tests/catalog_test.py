#!/usr/bin/env python3
"""Tests for niu_events.agent_catalog / _frontmatter / agent_model (the session agent map's server side)."""
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import niu_events as ne  # noqa: E402

SH = Path(__file__).resolve().parents[1]
passed = failed = 0


def check(cond, msg):
    global passed, failed
    if cond:
        passed += 1
    else:
        failed += 1
        print(f"FAIL: {msg}")


tmp = Path(tempfile.mkdtemp())
proj, dom, shr, usr = (tmp / n / "agents" for n in ("proj", "dom", "shr", "usr"))
for d in (proj, dom, shr, usr):
    d.mkdir(parents=True)
roots = {"project": proj, "domain": dom, "shared": shr, "user": usr, "allowed": [tmp]}


def agent(d, name, model=None, effort=None, desc="Does a thing. More text.", extra=""):
    lines = ["---", f"name: {name}", f"description: {desc}"]
    if model:
        lines.append(f"model: {model}")
    if effort:
        lines.append(f"effort: {effort}")
    (d / f"{name}.md").write_text("\n".join(lines) + "\n---\n" + extra + "body\n")


def cat(**kw):
    return ne.agent_catalog(str(tmp / "proj"), kw.pop("domain", "WORK"), roots)


def by(c, key="agents"):
    return {x["name"]: x for x in c[key]}


# precedence and builtins
agent(shr, "designer", "opus")
agent(shr, "implementer", "sonnet", "high")
agent(proj, "designer", "haiku")
agent(dom, "dom-only", "sonnet")
agent(usr, "mine", "opus")
a = by(cat())
check(a["designer"]["model"] == "haiku" and a["designer"]["source"] == "project", "project designer overrides shared")
check(a["implementer"]["source"] == "shared" and a["implementer"]["effort"] == "high", "shared implementer with effort")
check(a["dom-only"]["source"] == "domain" and a["mine"]["source"] == "user", "domain and user sources")
check(all(a[n]["source"] == "builtin" and a[n]["model"] == "inherit" for n in ("general-purpose", "Explore", "Plan")), "builtins inherit")
check(sum(1 for x in cat()["agents"] if x["name"] == "designer") == 1, "no duplicate names")

# project symlink to a shared agent is labelled shared, to somewhere else domain
os.symlink(shr / "implementer.md", proj / "implementer.md")
os.symlink(dom / "dom-only.md", proj / "dom-only.md")
a = by(cat())
check(a["implementer"]["source"] == "shared" and a["dom-only"]["source"] == "domain", f"symlink sources {a['implementer']['source']} {a['dom-only']['source']}")

# frontmatter parsing
agent(proj, "bad-effort", "sonnet", "extreme")
(proj / "plain.md").write_text("no frontmatter here\n")
agent(proj, "long", desc="x" * 400)
agent(proj, "two", desc="First sentence. Second sentence.")
agent(proj, "evil", desc="<script>alert(1)</script> hi\x07there token=abc123def456ghi789jkl012mno345pqr678")
agent(proj, "inh", "inherit")
a = by(cat())
check(a["bad-effort"]["effort"] is None and a["bad-effort"]["model"] == "sonnet", "bad effort becomes None")
check(a["plain"]["model"] is None and a["plain"]["effort"] is None, "no frontmatter gives model None")
check(len(a["long"]["description"]) <= 160, "description capped at 160")
check(a["two"]["description"] == "First sentence", f"first sentence only: {a['two']['description']!r}")
d = a["evil"]["description"]
check("<" not in d and ">" not in d and "\x07" not in d and "abc123def456ghi789jkl012mno345pqr678" not in d, f"evil description cleaned: {d!r}")
check(a["inh"]["model"] == "inherit", "model inherit kept")

# hostile files
(proj / "big.md").write_text("---\nname: big\ndescription: ok\n---\n" + "A" * 1_000_000)
(proj / ".env.md").write_text("---\nname: x\n---\n")
(proj / "bad name.md").write_text("---\nname: x\n---\n")
(proj / "notmd.txt").write_text("x")
os.symlink("/etc/passwd", proj / "passwd.md")
os.symlink(Path("/etc") / "hostname", proj / "host.md")
(proj / "subdir.md").mkdir()
a = by(cat())
check("big" in a and a["big"]["description"] == "ok", "huge file still parsed from its head")
fm = ne._frontmatter(proj / "big.md")
check(fm is not None and len(json.dumps(fm)) < 200, "huge file read is capped")
for n in (".env", "bad name", "notmd", "passwd", "host", "subdir", "../x"):
    check(n not in a, f"skipped: {n}")
check(not ne.NAME_RE.fullmatch("../x") and not ne.NAME_RE.fullmatch(".env"), "NAME_RE rejects traversal and dotfiles")
for f in list(proj.glob("many*.md")):
    f.unlink()
many = tmp / "many" / "agents"
many.mkdir(parents=True)
for i in range(100):
    agent(many, f"m{i:03d}")
c = ne.agent_catalog("", "", {"project": many, "domain": None, "shared": tmp / "none", "user": tmp / "none2", "allowed": [tmp]})
check(sum(1 for x in c["agents"] if x["name"].startswith("m")) == 64, "at most 64 files per directory")
check(len(json.dumps(c)) < 16384, f"catalog under 16 KB ({len(json.dumps(c))})")
check(str(tmp) not in json.dumps(cat()) and "/home/" not in json.dumps(cat()), "no absolute paths in the catalog")

# the real workspace catalog is also small and path free
real = ne.agent_catalog(str(ne.WS), "WORK")
rj = json.dumps(real)
check(len(rj) < 16384 and str(ne.WS) not in rj, "real catalog small and without paths")
check({"designer", "implementer", "security-reviewer", "test-reviewer", "Explore", "Plan", "general-purpose"} <= set(by(real)), "real catalog has the shared agents")

# skills
sk = shr.parent / "skills" / "develop"
sk.mkdir(parents=True)
(sk / "SKILL.md").write_text("---\nname: develop\ndescription: Design first. Then build.\n---\n")
s = by(cat(), "skills")
check(s["develop"]["flow"] is True and s["develop"]["description"] == "Design first", "flow skill listed")

# cache
f = proj / "cached.md"
agent(proj, "cached", "sonnet")
ne._frontmatter(f)
n0 = ne._FM_CACHE[str(f)]
ne._frontmatter(f)
check(ne._FM_CACHE[str(f)] is n0, "cache hit keeps the same entry")
time.sleep(0.01)
f.write_text("---\nmodel: opus\n---\n")
os.utime(f, (time.time() + 5, time.time() + 5))
check(ne._frontmatter(f).get("model") == "opus" and ne._FM_CACHE[str(f)] is not n0, "re-read after the file changed")
check(ne._frontmatter(tmp / "missing.md") is None, "missing file gives None")

# local tools stay in sync with niu-local-mcp
src = (SH / "bin" / "niu-local-mcp").read_text()
mcp = re.findall(r'"name":\s*"(local_[a-z_]+)"', src)
check([n for n, _ in ne.LOCAL_TOOLS] == mcp, f"LOCAL_TOOLS match niu-local-mcp: {mcp}")
ci = lambda dm: next(x for x in ne.agent_catalog("", dm, {"project": None, "domain": None, "shared": tmp / "n", "user": tmp / "n2"})["local_tools"] if x["name"] == "local_ci")
check(ci("WORK")["available"] is False and ci("PERSONAL")["available"] is True, "local_ci unavailable in WORK only")
check(next(x for x in cat()["local_tools"] if x["name"] == "local_status"), "local_status listed")

# agent_model compatibility on the shared agents
for name, want in (("designer", "opus"), ("implementer", "sonnet"), ("security-reviewer", None), ("test-reviewer", None)):
    got = ne.agent_model("", name)
    txt = (SH / "agents" / f"{name}.md").read_text()
    m = re.search(r"(?m)^model:\s*[\"']?([A-Za-z0-9._:\[\]-]{1,60})", txt.split("\n---", 1)[0])
    check(ne.agent_model(str(ne.WS), name) == (m.group(1) if m else None) or not (ne.WS / ".claude" / "agents" / f"{name}.md").exists(),
          f"agent_model({name}) as before")
check(ne.agent_model(str(tmp / "proj" / ".."), "../x") is None, "agent_model rejects bad names")
d2 = tmp / "pj" / ".claude" / "agents"
d2.mkdir(parents=True)
agent(d2, "designer", "opus")
(d2 / "nomodel.md").write_text("---\nname: nomodel\n---\n")
check(ne.agent_model(str(tmp / "pj"), "designer") == "opus" and ne.agent_model(str(tmp / "pj"), "nomodel") is None, "agent_model project lookup")

print(f"catalog: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
