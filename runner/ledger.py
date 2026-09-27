#!/usr/bin/env python3
"""KaggleCompute budget ledger.

Kaggle's API does not expose remaining weekly quota, so this ledger
self-accounts: every GPU stage's wall time (x drain_multiplier) is recorded,
and admission control refuses work that would overrun the current quota
week. Kaggle resets the weekly GPU quota Saturday 00:00 UTC (official
announcement: kaggle.com/product-feedback/173129), so the ledger buckets
usage by that quota week instead of a rolling 7-day window. Free tier only -
there is no paid overflow path.

State file: ledger.json (locally, or synced via the kagglecompute-state
dataset when run inside Kaggle).
"""
import json
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

DEFAULT_WEEKLY_GPU_MINUTES = 1800  # 30 h free tier


def week_start(now=None):
    """Start (epoch seconds) of the Kaggle quota week containing `now`.

    Kaggle resets weekly GPU quota Saturday 00:00 UTC.
    """
    dt = datetime.fromtimestamp(now or time.time(), tz=timezone.utc)
    days_since_saturday = (dt.weekday() - 5) % 7
    anchor = (dt - timedelta(days=days_since_saturday)).replace(
        hour=0, minute=0, second=0, microsecond=0)
    return anchor.timestamp()


def load(path):
    p = Path(path)
    if p.exists():
        try:
            return json.loads(p.read_text())
        except json.JSONDecodeError:
            pass  # corrupt state: start clean rather than block all work
    return {"entries": []}


def save(path, state):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=1))
    tmp.replace(p)  # atomic


def _prune(state, now=None):
    ws = week_start(now)
    state["entries"] = [e for e in state["entries"] if e["ts"] >= ws]
    return state


def usage_minutes(state, path=None, now=None):
    ws = week_start(now)
    return sum(
        e["gpu_minutes"]
        for e in state["entries"]
        if e["ts"] >= ws and (path is None or e.get("path") == path)
    )


def record(path_to_ledger, job_id, path, stage, gpu_minutes, ts=None):
    state = _prune(load(path_to_ledger))
    state["entries"].append({
        "job_id": job_id, "path": path, "stage": stage,
        "gpu_minutes": round(gpu_minutes, 2), "ts": ts or time.time(),
    })
    save(path_to_ledger, state)


def check(path_to_ledger, path, planned_minutes, weekly_budget=DEFAULT_WEEKLY_GPU_MINUTES,
          path_budget=None):
    """Return (allowed, reason). planned_minutes may be None (= admit, record actuals)."""
    state = _prune(load(path_to_ledger))
    planned = planned_minutes or 0
    used = usage_minutes(state)
    if used + planned > weekly_budget:
        return False, (f"global budget: {used:.0f}+{planned:.0f} min would exceed "
                       f"{weekly_budget} min quota-week budget (resets Sat 00:00 UTC)")
    if path_budget is not None:
        used_path = usage_minutes(state, path)
        if used_path + planned > path_budget:
            return False, (f"path '{path}' budget: {used_path:.0f}+{planned:.0f} min "
                           f"would exceed {path_budget} min")
    return True, "ok"


def report(path_to_ledger, weekly_budget=DEFAULT_WEEKLY_GPU_MINUTES):
    state = _prune(load(path_to_ledger))
    by_path = {}
    for e in state["entries"]:
        by_path[e.get("path", "?")] = by_path.get(e.get("path", "?"), 0) + e["gpu_minutes"]
    used = usage_minutes(state)
    lines = [f"quota week (from Sat 00:00 UTC) GPU usage: {used:.1f} / {weekly_budget} min "
             f"({100*used/weekly_budget:.0f}%)"]
    for p, m in sorted(by_path.items()):
        lines.append(f"  {p}: {m:.1f} min")
    return "\n".join(lines)


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "selftest":
        import tempfile, os
        with tempfile.TemporaryDirectory() as d:
            lp = os.path.join(d, "ledger.json")
            record(lp, "j1", "batch", "extract", 100.0)
            record(lp, "j1", "batch", "stereo", 200.0)
            record(lp, "j2", "interactive", "serve", 50.0)
            ok, _ = check(lp, "batch", 1400.0)
            assert ok, "should admit within budget"
            ok, why = check(lp, "batch", 1500.0)
            assert not ok and "global budget" in why
            ok, why = check(lp, "interactive", 100.0, path_budget=100.0)
            assert not ok and "path" in why
            # quota-week boundary: entries before Saturday 00:00 UTC must not count
            from datetime import datetime, timezone
            old_ts = week_start() - 3600  # one hour before this week's reset
            record(lp, "j0", "batch", "extract", 1700.0, ts=old_ts)
            # 350 (this week) + 1400 planned = 1750 < 1800; if the stale 1700
            # from the previous quota week leaked in, this would refuse.
            ok, why = check(lp, "batch", 1400.0)
            assert ok, f"previous quota week must not count: {why}"
            assert abs(week_start(old_ts) - (week_start() - 7 * 24 * 3600)) < 1
            print(report(lp))
            print("ledger selftest OK")
    elif len(sys.argv) >= 2 and sys.argv[1] == "report":
        print(report(sys.argv[2] if len(sys.argv) > 2 else "ledger.json"))
    else:
        print(__doc__)
