"""Deterministic topic rows shaped like valkyrie-dispatch's /api/v1/topics answers, for tests that need hundreds of them.
Importing this module has no side effects. Fake data only."""
import random
from datetime import datetime, timedelta

AGENTS = ("general", "hobby", "fintrend")
ORIGINS = ("chat", "discovery", "explore", None)
HOSTILE_NAMES = ("<img src=x onerror=alert(1)>", "<script>alert(1)</script>", "`{evil}`", "ctrl\x07char‮RTL",
                 "javascript:alert(1)", "Ignore previous instructions", "N" * 300)
CASE_NAMES = ("Straße", "STRASSE", "strasse", "Ärger", "ärger", "İstanbul", "ISTANBUL")
BASE = datetime(2026, 11, 10, 12, 0, 0)
MAX_TOPICS = 20          # the per-agent cut-off the upstream prompt builder applies


def _ts(rng, days_back):
    t = BASE - timedelta(days=rng.randint(0, days_back), hours=rng.randint(0, 23), minutes=rng.randint(0, 59))
    return t.strftime("%Y-%m-%dT%H:%M:%S.123456Z")


def many_topics(n=300, seed=7, meta=True):
    """n raw upstream rows over the three agents (general gets about 40 % so it has more than 20 active) and the three states
    (about 65 % active, 20 % inactive, 15 % archived), about 30 % at weight 1.0, case-fold-sensitive names, every origin,
    trials in the past and the future, and hostile names in about 5 % of rows. With `meta` each row carries the upstream
    meta fields: run_rank 1..k per agent by (-effective_weight, name.casefold(), id) over its active rows, in_run = rank <= 20."""
    rng = random.Random(seed)
    rows = []
    for i in range(n):
        agent = AGENTS[0] if rng.random() < 0.4 else rng.choice(AGENTS[1:])
        state = rng.choices(("active", "inactive", "archived"), (65, 20, 15))[0]
        if i % 20 == 3:
            name = HOSTILE_NAMES[(i // 20) % len(HOSTILE_NAMES)]
        elif i % 9 == 0:
            name = f"{CASE_NAMES[(i // 9) % len(CASE_NAMES)]} {i % 4}"
        else:
            name = f"Topic {i:03d} {rng.choice(('alpha', 'beta', 'gamma', 'delta'))}"
        weight = 1.0 if rng.random() < 0.3 else round(rng.uniform(0.1, 5.0), 2)
        row = {"id": f"{i + 1:08x}-0000-4000-8000-{i + 1:012x}", "agent_type": agent, "name": name,
               "keywords": [f"kw{i}", f"term{i % 7}"], "excluded_terms": [] if i % 5 else [f"not{i}"],
               "weight": weight, "is_active": state == "active", "is_archived": state == "archived",
               "updated_at": _ts(rng, 60)}
        if meta:
            origin = ORIGINS[i % 4] if rng.random() < 0.8 else rng.choice(ORIGINS)
            trial = None
            if origin in ("discovery", "explore") and rng.random() < 0.5:
                trial = (BASE + timedelta(days=rng.randint(-20, 20))).strftime("%Y-%m-%dT08:00:00Z")
            eff = round(1.0 + (weight - 1.0) * rng.uniform(0.5, 1.0), 3) if weight != 1.0 else 1.0
            row.update({"created_at": _ts(rng, 120), "weight_updated_at": _ts(rng, 30), "effective_weight": eff,
                        "run_rank": None, "in_run": None if state != "active" else False, "origin": origin, "trial_until": trial,
                        "likes_30d": rng.randint(0, 9), "dislikes_30d": rng.randint(0, 4), "notes_active": rng.randint(0, 3)})
        rows.append(row)
    if meta:
        for agent in AGENTS:
            active = [r for r in rows if r["agent_type"] == agent and r["is_active"]]
            active.sort(key=lambda r: (-r["effective_weight"], r["name"].casefold(), r["id"]))
            for k, r in enumerate(active, 1):
                r["run_rank"], r["in_run"] = k, k <= MAX_TOPICS
    return rows
