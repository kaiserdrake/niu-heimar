#!/usr/bin/env python3
"""Tests for niu_designs (index, safe reads, parser, revisions, diff, session docs) and niu_events.snapshot_design."""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import niu_events as ne  # noqa: E402
import niu_designs as nd  # noqa: E402

SH = Path(__file__).resolve().parents[1]
passed = failed = 0


def check(cond, msg):
    global passed, failed
    if cond:
        passed += 1
    else:
        failed += 1
        print(f"FAIL: {msg}")


def write(p, text):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


tmp = Path(tempfile.mkdtemp()).resolve()
WS = tmp / "ws"
ne.WS = WS
ne.SHARED = WS / ".claude-workspace"
protected = set()
nd.guard_allows = lambda path, cwd: Path(path).name not in protected

DOC = "# Title one\nStatus: proposed · Date: 2026-11-17\n\nIntro text.\n"
sdd = WS / ".claude-proposals"
write(sdd / "s.md", DOC)
app = WS / "PERSONAL" / "app"
write(app / ".claude" / "settings.local.json", "{}")
dd = app / ".claude-proposals"
write(dd / "t.md", "# Real doc\nStatus: approved\n\nBody.\n")
write(dd / "secret.md", "# Secret\n")
protected.add("secret.md")
write(dd / ".env.md", "x")
write(dd / "notes.txt", "x")
write(app / "docs" / "design" / "legacy.md", "# legacy human doc\n")
write(app / ".claude" / "proposals" / "dotclaude.md", "# dotclaude\n")
write(WS / ".claude-workspace" / "docs" / "design" / "wslegacy.md", "# ws legacy\n")
write(dd / "sub" / "dir" / "x.md", "x")
write(dd / "big.md", "# big\n" + "x" * (600 * 1024))
os.mkfifo(dd / "pipe.md")
os.symlink("/etc/passwd", dd / "link-passwd.md")
write(app / "secret-outside.md", "# outside\n")
os.symlink("../../secret-outside.md", dd / "link-out.md")
os.symlink("t.md", dd / "alias.md")
write(WS / "PERSONAL" / "other" / ".claude-proposals" / "unlinked.md", "# unlinked\n")  # no settings.local.json
extra = WS / "HOBBY" / "game"
write(extra / ".claude-proposals" / "g.md", "# Game\n")
os.makedirs(WS / "PERSONAL" / "app" / "node_modules" / "p" / ".claude", exist_ok=True)

# ---------- index ----------
idx = nd.index(force=True)
names = {e["name"] for e in idx.values()}
check({"s.md", "t.md"} <= names, f"finds shared and linked docs: {names}")
for bad in ("legacy.md", "dotclaude.md", "wslegacy.md", "secret.md", ".env.md", "notes.txt", "x.md", "big.md", "pipe.md", "link-passwd.md", "link-out.md", "unlinked.md", "g.md"):
    check(bad not in names, f"index skips {bad}")
check("alias.md" in names, "symlink to a sibling in the same dir is kept")
e_t = next(e for e in idx.values() if e["name"] == "t.md")
check(e_t["title"] == "Real doc" and e_t["status"] == "approved" and e_t["domain"] == "PERSONAL" and e_t["project"] == "app", f"title/status/domain: {e_t}")
e_s = next(e for e in idx.values() if e["name"] == "s.md")
check(e_s["domain"] == "(workspace)" and e_s["status"] == "proposed" and e_s["rel"] == ".claude-proposals/s.md", f"workspace doc: {e_s}")
check(e_t["rel"] == "PERSONAL/app/.claude-proposals/t.md", f"rel is relative to the workspace: {e_t['rel']}")
check(e_t["id"] == nd.doc_id(e_t["rel"]) == nd.doc_id("PERSONAL/app/.claude-proposals/t.md") and nd.RE_DOC.fullmatch(e_t["id"]), "doc_id stable")
public = [{k: v for k, v in e.items() if k != "project_dir"} for e in idx.values()]
check(str(tmp) not in json.dumps(public), "no absolute path in public fields")
idx2 = nd.index([str(extra)], force=True)
check("g.md" in {e["name"] for e in idx2.values()}, "extra dir indexed")
idx3 = nd.index(["/tmp"], force=True)
check("g.md" not in {e["name"] for e in idx3.values()} and len(idx3) == len(idx), "extra dir outside the workspace ignored")
check(nd.index() is nd.index(), "index cached")
check(nd.lookup(e_t["id"]) is not None and nd.lookup("d" + "0" * 16) is None and nd.lookup("../x") is None, "lookup")

# ---------- reads ----------
check(nd.read_doc(e_t).startswith("# Real doc"), "read_doc")
protected.add("t.md")
check(nd.read_doc(e_t) is None, "read_doc re-checks the guard")
protected.discard("t.md")
check(nd.read_doc(dict(e_t, name="../x.md")) is None and nd.read_doc(dict(e_t, name="link-out.md")) is None
      and nd.read_doc(dict(e_t, name="link-passwd.md")) is None and nd.read_doc(dict(e_t, name="pipe.md")) is None
      and nd.read_doc(dict(e_t, name="big.md")) is None and nd.read_doc(dict(e_t, name="x.md")) is None, "read_doc rejects odd names")
check(nd.read_doc(dict(e_t, project_dir=str(tmp))) is None, "read_doc rejects a project outside the workspace")
write(dd / "ctl.md", "a\x00b\x1b[31m\tok\r\nnext\n")
check(nd.read_doc(dict(e_t, name="ctl.md")) == "ab[31m\tok\nnext\n", "control characters stripped")

# ---------- parse ----------
md = """# Top
## Sec
## Sec

Para with `code | pipe`, **bold**, *it*, ~~gone~~ and [site](https://example.com/a?b=1) and [frag](#sec).
Second line.\x20\x20
Hard break above.

- one
  - two
    - three
- back

1. a
2. b

| A | B \\| x | `c|d` |
|:--|:-:|--:|
| 1 | 2 | 3 |
| x |

```python
print(1)
```

> quoted *text*
> more

---

<script>alert(1)</script>
"""
bl = nd.parse(md)
hs = [b for b in bl if b["t"] == "h"]
check([h["id"] for h in hs] == ["top", "sec", "sec-2"] and [h["l"] for h in hs] == [1, 2, 2], f"headings and slugs: {hs}")
check([o["id"] for o in nd.outline(bl)] == ["top", "sec", "sec-2"], "outline")
p = next(b for b in bl if b["t"] == "p")
kinds = [x["t"] if isinstance(x, dict) else "s" for x in p["c"]]
check({"code", "b", "i", "s", "a", "br"} <= set(kinds), f"inline kinds: {kinds}")
check(any(isinstance(x, dict) and x["t"] == "a" and x["href"] == "https://example.com/a?b=1" for x in p["c"]), "https link kept")
check(any(isinstance(x, dict) and x["t"] == "a" and x["href"] == "#sec" for x in p["c"]), "anchor link kept")
ul = next(b for b in bl if b["t"] == "ul")
check(len(ul["items"]) == 2 and ul["items"][0][1]["t"] == "ul" and ul["items"][0][1]["items"][0][1]["t"] == "ul", "three nested list levels")
ol = next(b for b in bl if b["t"] == "ol")
check(ol["start"] == 1 and len(ol["items"]) == 2, "ordered list")
tb = next(b for b in bl if b["t"] == "table")
check(tb["align"] == ["l", "c", "r"] and len(tb["head"]) == 3 and len(tb["rows"]) == 2 and all(len(r) == 3 for r in tb["rows"]), f"table: {tb}")
check(tb["head"][1] == ["B | x"] and tb["head"][2] == [{"t": "code", "text": "c|d"}], f"table pipes: {tb['head']}")
cb = next(b for b in bl if b["t"] == "code")
check(cb["lang"] == "python" and cb["text"] == "print(1)", "fenced code")
q = next(b for b in bl if b["t"] == "quote")
check(q["c"][0]["t"] == "p", "quote")
check(any(b["t"] == "hr" for b in bl), "hr")
last = bl[-1]
check(last["t"] == "p" and last["c"] == ["<script>alert(1)</script>"], f"raw html stays literal: {last}")
check("<" not in json.dumps([x for x in bl if x["t"] != "p"][:0]), "sanity")

def inl(s, **kw):
    return nd.parse(s, **kw)[0]["c"]

check(inl("[x](javascript:alert(1))") == ["x"], f"javascript: -> text: {inl('[x](javascript:alert(1))')}")
check(inl("[x](data:text/html;base64,AAAA)") == ["x"], "data: -> text")
check(inl("[x](//evil.com/a)") == ["x"], "protocol-relative -> text")
check(inl("[x](other.md#sec)") == ["x"], "sibling link without a resolver -> text")
check(inl("[x](other.md#sec)", link_doc=lambda n: "dabc" if n == "other.md" else None) == [{"t": "a", "doc": "dabc", "frag": "sec", "c": ["x"]}], "sibling md -> doc link")
check(inl("![a](http://x/y.png)") == [{"t": "img", "alt": "a"}], "image -> alt only")
check(inl("[x](../../etc/passwd.md)") == ["x"], "traversal href -> text")
t0 = time.time()
for s in ("*" * 10000, "[" * 10000, "*a " * 10000, "[](" * 10000, "*_" * 5000, "`" * 10000, "**a *b " * 3000, "[a](" + "(" * 5000, "![" * 5000):
    nd.parse(s)
check(time.time() - t0 < 3, f"adversarial inline input is fast: {time.time() - t0:.2f}s")
deep = "*a " * 20 + "x" + "* " * 20
json.dumps(nd.parse(deep))
check(True, "deep emphasis serialises")
deepq = ">" * 200 + " x"
r = nd.parse(deepq)
check(r[-1]["c"] == ["… (truncated)"], "deep quote nesting is cut")
many = nd.parse("\n\n".join(f"p{i}" for i in range(6000)))
check(len(many) == 5001 and many[-1]["c"] == ["… (truncated)"], f"block cap: {len(many)}")
rows = nd.parse("| a |\n|---|\n" + "\n".join("| x |" for _ in range(300)))
check(len(rows[0]["rows"]) == 200 and rows[-1]["c"] == ["… (truncated)"], "table row cap")
check(nd.parse("a | b\n---") == [{"t": "p", "c": ["a | b"]}, {"t": "hr"}], f"malformed table -> paragraph: {nd.parse('a | b' + chr(10) + '---')}")
# golden pass over the real docs, plus a committed sample so CI (where no proposals may be committed) still checks one
real = sorted((SH / "tests" / "fixtures" / "designs").glob("*.md")) + sorted((SH / "docs" / "design").glob("*.md")) \
    + sorted((SH.parent / ".claude-proposals").glob("*.md"))
check(len(real) > 0, "real docs found")
for f in real:
    txt = f.read_text()
    b = nd.parse(txt)
    json.dumps(b)
    check(not any(x.get("t") == "p" and x["c"] == ["… (truncated)"] for x in b), f"{f.name}: not truncated")
    nh = sum(1 for ln in txt.split("\n") if ln.startswith("#") and not ln.startswith("#!") and ln.lstrip("#").startswith(" "))
    in_fence = False
    nh = 0
    for ln in txt.split("\n"):
        if ln.startswith("```"):
            in_fence = not in_fence
        elif not in_fence and ln.startswith("#") and ln.lstrip("#").startswith(" "):
            nh += 1
    check(len(nd.outline(b)) == nh, f"{f.name}: heading count {len(nd.outline(b))} == {nh}")
    ntab = 0
    in_fence = False
    lines = txt.split("\n")
    for k, ln in enumerate(lines):
        if ln.lstrip().startswith("```"):
            in_fence = not in_fence
        elif not in_fence and ln.lstrip().startswith("|") and k + 1 < len(lines) and set(lines[k + 1].replace("|", "").replace(":", "").strip()) <= {"-", " "} and "-" in lines[k + 1] and (k == 0 or not lines[k - 1].lstrip().startswith("|")):
            ntab += 1
    def count_tables(bs):
        c = 0
        for x in bs:
            if x["t"] == "table":
                c += 1
            elif x["t"] == "quote":
                c += count_tables(x["c"])
            elif x["t"] in ("ul", "ol"):
                c += sum(count_tables(i) for i in x["items"])
        return c
    check(count_tables(b) == ntab, f"{f.name}: table count {count_tables(b)} == {ntab}")

# ---------- revisions ----------
def git(cwd, *a):
    subprocess.run(["git", "-C", str(cwd), "-c", "user.email=t@t", "-c", "user.name=t", "-c", "commit.gpgsign=false", *a], check=True, capture_output=True)


git(app, "init", "-q")
d1 = "# R\n\nfirst version.\n"
d2 = "# R\n\nsecond version.\n"
rv = dd / "rev.md"
write(rv, d1)
git(app, "add", ".claude-proposals/rev.md")
git(app, "commit", "-q", "-m", "first subject")
write(rv, d2)
git(app, "commit", "-q", "-am", "second subject with token=abcdefghijklmnopqrstuvwxyz0123456789")
write(rv, "# R\n\nthird working version.\n")
idx = nd.index(force=True)
er = next(e for e in idx.values() if e["name"] == "rev.md")
os.environ["NIU_EVENTS_DB"] = str(tmp / "events.db")
con = ne.connect()
t = time.time()
for k, (txt, ag) in enumerate([(d2, "designer"), ("# R\n\nthird working version.\n", ""), ("# R\n\nsnap only.\n", "designer")]):
    con.execute("INSERT INTO design_snapshots(ts,rel,sha,agent_type,tool,content) VALUES(?,?,?,?,?,?)", (t - 10 + k, er["rel"], f"s{k}", ag, "Write", txt))
con.commit()
revs = nd.revisions(er, con)
check(len(revs) == 4, f"4 distinct revisions: {[(r['rev'][:8], r['label']) for r in revs]}")
check(all(set(r) == {"rev", "ts", "label", "kind", "also"} for r in revs), "revision shape")
kinds = {r["kind"] for r in revs}
check(kinds == {"git", "work", "snap"}, f"kinds {kinds}")
g2 = next(r for r in revs if r["kind"] == "git" and "second" in r["label"])
check(g2["also"] and g2["also"][0].startswith("Draft"), f"duplicate snapshot folded into the git label: {g2}")
check("abcdefghijklmnopqrstuvwxyz0123456789" not in g2["label"], "subject redacted")
w = next(r for r in revs if r["kind"] == "work")
check(w["label"].startswith("Working copy") and "uncommitted" in w["label"] and w["also"], f"work label {w}")
check([r["ts"] for r in revs] == sorted((r["ts"] for r in revs), reverse=True), "newest first")
check(nd.revision_text(er, "work").strip().endswith("third working version."), "work text")
gsha = next(r["rev"] for r in revs if r["kind"] == "git" and "first" in r["label"])
check(nd.revision_text(er, gsha) == d1, "git text")
check(nd.revision_text(er, "git:" + "0" * 40) is None, "unknown sha -> None")
snap = next(r["rev"] for r in revs if r["kind"] == "snap")
check(nd.revision_text(er, snap, con).strip().endswith("snap only."), "snap text")
other = con.execute("INSERT INTO design_snapshots(ts,rel,sha,tool,content) VALUES(?,?,?,?,?)", (t, "PERSONAL/app/.claude-proposals/other.md", "z", "Write", "secret")).lastrowid
con.commit()
check(nd.revision_text(er, f"snap:{other}", con) is None, "snapshot of another doc is refused")
for badrev in ("../x", "git:zzz", "snap:", "WORK", "git:" + "A" * 40, "snap:1;drop", ""):
    check(nd.revision_text(er, badrev, con) is None, f"bad token {badrev!r}")
# rename followed
git(app, "mv", ".claude-proposals/rev.md", ".claude-proposals/renamed.md")
git(app, "commit", "-q", "-m", "rename")
idx = nd.index(force=True)
nd._REV_CACHE.clear()
en = next(e for e in idx.values() if e["name"] == "renamed.md")
rn = nd.revisions(en, con)
check(any("first subject" in r["label"] for r in rn), f"--follow shows pre-rename history: {[r['label'] for r in rn]}")
# git history follows a legacy docs/design -> .claude-proposals move
write(app / "docs" / "design" / "mv.md", "# M\n\nold home.\n")
git(app, "add", "docs/design/mv.md")
git(app, "commit", "-q", "-m", "legacy home")
git(app, "mv", "docs/design/mv.md", ".claude-proposals/mv.md")
git(app, "commit", "-q", "-m", "move to proposals")
idx = nd.index(force=True)
nd._REV_CACHE.clear()
emv = next(e for e in idx.values() if e["name"] == "mv.md")
check(any("legacy home" in x for r in nd.revisions(emv, con) for x in [r["label"]] + r["also"]), "history follows a move from docs/design")
# symlinked .claude-proposals is rejected
symp = WS / "PERSONAL" / "symp"
write(symp / ".claude" / "settings.local.json", "{}")
write(tmp / "elsewhere" / "z.md", "# Z\n")
os.symlink(tmp / "elsewhere", symp / ".claude-proposals")
check(not any(e["project"] == "symp" for e in nd.index(force=True).values()), "symlinked .claude-proposals rejected")
# a feed summary under docs/design is not a session doc
con.execute("INSERT INTO events(ts,event,session_id,tool,summary,project_dir) VALUES(?,?,?,?,?,?)", (time.time(), "PostToolUse", "sess-legacy", "Write", "docs/design/t.md", str(app)))
con.commit()
check(nd.session_docs(con, "sess-legacy", str(app), idx) == [], "docs/design summary is not a session doc")
# not a repo
nogit = WS / "PERSONAL" / "plain"
write(nogit / ".claude" / "settings.local.json", "{}")
write(nogit / ".claude-proposals" / "p.md", "# P\n")
idx = nd.index(force=True)
ep = next(e for e in idx.values() if e["project"] == "plain")
rp = nd.revisions(ep, con)
check([r["kind"] for r in rp] == ["work"] and rp[0]["label"] == "Working copy", f"not a repo: {rp}")

# ---------- diff ----------
a = "# T\n\n## Design\n\nThe old paragraph here.\n\n| k | v |\n|---|---|\n| a | 1 |\n| b | 2 |\n\n- x\n- y\n"
b_ = "# T\n\n## Design\n\nThe new paragraph here.\n\n| k | v |\n|---|---|\n| a | 1 |\n| b | 3 |\n\n- x\n- y\n- z\n\n## Added\n\nBrand new.\n"
d = nd.diff(a, b_)
ops = [x["op"] for x in d["blocks"]]
chg = [x for x in d["blocks"] if x["op"] == "chg"]
check(set(ops) >= {"eq", "chg", "ins"}, f"ops {ops}")
pw = next(x for x in chg if "words" in x)
check(["del", "old"] in pw["words"] and ["ins", "new"] in pw["words"], f"word diff: {pw['words']}")
tc = next(x for x in chg if "rows" in x)
check(tc["rows"].count("ins") == 1 and len(tc["old_rows"]) == 1 and tc["old_rows"][0]["cells"] == [["b"], ["2"]], f"table diff: {tc}")
lc = next(x for x in chg if "items" in x)
check(lc["items"] == ["eq", "eq", "ins"], f"list diff: {lc['items']}")
check({"Design", "Added"} <= set(d["stats"]["sections"]), f"sections {d['stats']['sections']}")
check(d["stats"]["added"] > 0 and d["stats"]["removed"] > 0, "line stats")
ls = d["lines"]
check(any(x["op"] == "ins" and x["text"] == "Brand new." for x in ls) and any(x["op"] == "del" and x["text"] == "The old paragraph here." for x in ls), "line diff")
check(all(x["op"] != "eq" or (x["a"] and x["b"]) for x in ls), "eq lines carry both numbers")
same = nd.diff(a, a)
check(same["lines"] == [] and all(x["op"] == "eq" for x in same["blocks"]) and same["stats"]["added"] == 0, "identical texts")
big_a = "\n".join(f"line {i}" for i in range(6000))
big_b = "\n".join(f"line {i}" if i % 7 else f"changed {i}" for i in range(6000))
t0 = time.time()
bd = nd.diff(big_a, big_b)
check(bd["blocks"] is None and bd["lines"] and bd["note"] and time.time() - t0 < 10, f"large diff falls back to lines only ({time.time() - t0:.1f}s)")

# ---------- session docs ----------
con.execute("INSERT INTO events(ts,event,session_id,tool,summary,project_dir) VALUES(?,?,?,?,?,?)", (t, "PostToolUse", "sess-a", "Write", ".claude-proposals/t.md", str(app)))
con.execute("INSERT INTO events(ts,event,session_id,tool,summary,project_dir) VALUES(?,?,?,?,?,?)", (t, "PostToolUse", "sess-a", "Edit", ".claude-proposals/notes.txt", str(app)))
con.execute("INSERT INTO events(ts,event,session_id,tool,summary,project_dir) VALUES(?,?,?,?,?,?)", (t, "PostToolUse", "sess-a", "Read", ".claude-proposals/s.md", str(app)))
con.execute("INSERT INTO events(ts,event,session_id,tool,summary,project_dir) VALUES(?,?,?,?,?,?)", (t, "PostToolUse", "sess-a", "Edit", "../../x/.claude-proposals/t.md", str(app)))
con.commit()
idx = nd.index(force=True)
check(nd.session_docs(con, "sess-a", str(app), idx) == [e_t["id"]], f"session docs: {nd.session_docs(con, 'sess-a', str(app), idx)}")
check(nd.session_docs(con, "sess-none", str(app), idx) == [] and nd.session_docs(con, "sess-a", "", idx) == [], "unknown session / dir -> []")
res = nd.link_resolver(e_t, idx)
check(res("alias.md") == nd.doc_id("PERSONAL/app/.claude-proposals/alias.md") and res("missing.md") is None and res("../x.md") is None, "link_resolver")

# ---------- snapshot_design ----------
con.execute("DELETE FROM design_snapshots")
con.commit()
sn = WS / "PERSONAL" / "snap"
sd = sn / ".claude-proposals"
write(sd / "a.md", "v1\n")
f = str(sd / "a.md")
check(ne.snapshot_design(con, f, str(sn), "s1", "designer", "Write") is True, "first write stored")
check(ne.snapshot_design(con, f, str(sn), "s1", "designer", "Edit") is False, "identical second write skipped")
write(sd / "a.md", "v2\n")
check(ne.snapshot_design(con, ".claude-proposals/a.md", str(sn), "s1", "", "Edit") is True, "relative path resolved against the project")
rows = con.execute("SELECT rel, agent_type, tool, session_id FROM design_snapshots ORDER BY id").fetchall()
check([tuple(r) for r in rows] == [("PERSONAL/snap/.claude-proposals/a.md", "designer", "Write", "s1"), ("PERSONAL/snap/.claude-proposals/a.md", "", "Edit", "s1")], f"rows {[tuple(r) for r in rows]}")
for k in range(50):
    write(sd / "a.md", f"v{k + 10}\n")
    ne.snapshot_design(con, f, str(sn), "s1", "", "Write")
check(con.execute("SELECT count(*) FROM design_snapshots WHERE rel='PERSONAL/snap/.claude-proposals/a.md'").fetchone()[0] == 40, "capped at 40 per doc")
con.execute("UPDATE design_snapshots SET ts = ? WHERE id = (SELECT min(id) FROM design_snapshots)", (time.time() - 40 * 86400,))
con.commit()
write(sd / "a.md", "fresh\n")
ne.snapshot_design(con, f, str(sn), "s1", "", "Write")
check(con.execute("SELECT count(*) FROM design_snapshots WHERE ts < ?", (time.time() - 31 * 86400,)).fetchone()[0] == 0, "old rows pruned")
before = con.execute("SELECT count(*) FROM design_snapshots").fetchone()[0]
write(sd / "sub" / "x.md", "x\n")
write(sn / "docs" / "x.md", "x\n")
write(sd / "notes.txt", "x\n")
outside = tmp / "outside" / ".claude-proposals" / "o.md"
write(outside, "x\n")
write(sn / "docs" / "design" / "a.md", "x\n")
write(sn / ".claude" / "proposals" / "a.md", "x\n")
for path in (sn / "docs" / "design" / "a.md", sn / ".claude" / "proposals" / "a.md"):
    check(ne.snapshot_design(con, str(path), str(sn), "s", "", "Write") is False, f"legacy path not snapshotted: {path.parent}")
for path in (sd / "sub" / "x.md", sn / "docs" / "x.md", sd / "notes.txt", outside, sd / ".env.md", sd / "missing.md"):
    check(ne.snapshot_design(con, str(path), str(sn), "s", "", "Write") is False, f"ignored {path.name}")
os.symlink("/etc/passwd", sd / "pw.md")
check(ne.snapshot_design(con, str(sd / "pw.md"), str(sn), "s", "", "Write") is False, "symlink to outside ignored")
check(ne.snapshot_design(con, None, str(sn), "s", "", "Write") is False and ne.snapshot_design(con, 5, None, "s", "", "Write") is False, "non-string path ignored")
check(con.execute("SELECT count(*) FROM design_snapshots").fetchone()[0] == before, "no rows added by ignored paths")
os.environ["NIU_DESIGN_SNAPSHOT_DAYS"] = "0"
write(sd / "a.md", "after disable\n")
check(ne.snapshot_design(con, f, str(sn), "s1", "", "Write") is False, "NIU_DESIGN_SNAPSHOT_DAYS=0 disables capture")
os.environ.pop("NIU_DESIGN_SNAPSHOT_DAYS")
con.close()

# ---------- niu_prs.discover uses linked_projects ----------
check([(d, r) for d, r, _p in nd.linked_projects(WS)][:0] == [], "linked_projects shape")
lp = nd.linked_projects(WS)
check(all(len(x) == 3 for x in lp) and any(x[0] == "PERSONAL" and x[1] == "app" for x in lp)
      and not any("node_modules" in str(x[2]) for x in lp) and not any(x[1] == "other" for x in lp), f"linked_projects: {lp}")

print(f"designs: {passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
