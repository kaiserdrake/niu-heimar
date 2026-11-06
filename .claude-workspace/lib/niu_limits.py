"""Claude plan usage limits (5-hour and weekly), as last reported by Claude Code's status line (bin/niu-statusline)."""
import json
import time
from datetime import datetime

from niu_events import db_path

STALE_SECS = 15 * 60


def _epoch(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v) / (1000 if v > 1e12 else 1)
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def claude_limits(now=None):
    """{'available': bool, 'updated', 'stale', 'windows': [{key, label, used, resets_at}]}."""
    now = now or time.time()
    try:
        data = json.loads((db_path().parent / "claude-limits.json").read_text())
    except (OSError, ValueError):
        return {"available": False}
    rl = data.get("rate_limits") or {}
    windows = []
    for key, label in (("five_hour", "5-hour"), ("seven_day", "Weekly"), ("seven_day_opus", "Weekly Opus"),
                       ("seven_day_sonnet", "Weekly Sonnet")):
        b = rl.get(key)
        if not isinstance(b, dict):
            continue
        try:
            used = float(b.get("used_percentage"))
        except (TypeError, ValueError):
            continue
        reset = _epoch(b.get("resets_at"))
        if reset and reset < now:
            used = 0.0  # the window has rolled over since the last report
        windows.append({"key": key, "label": label, "used": round(used, 1), "resets_at": reset})
    updated = data.get("updated", 0)
    return {"available": bool(windows), "updated": updated, "stale": now - updated > STALE_SECS, "windows": windows}
