#!/usr/bin/env python3
"""KaggleCompute budget ledger.

Kaggle's API does not expose remaining weekly quota, so this ledger
self-accounts: every GPU stage's wall time (x drain_multiplier) is recorded,
and admission control refuses work that would overrun the rolling 7-day
budget. Free tier only - there is no paid overflow path.

State file: ledger.json (locally, or synced via the kagglecompute-state
dataset when run inside Kaggle).
"""
import json
import sys
import time
from pathlib import Path

WEEK_SECONDS = 7 * 24 * 3600
DEFAULT_WEEKLY_GPU_MINUTES = 1800  # 30 h free tier


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
    now = now or time.time()
    state["entries"] = [e for e in state["entries"] if now - e["ts"] < WEEK_SECONDS]
    return state


def usage_minutes(state, path=None, now=None):
    now = now or time.time()
    return sum(
        e["gpu_minutes"]
        for e in state["entries"]
        if now - e["ts"] < WEEK_SECONDS and (path is None or e.get("path") == path)
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
                       f"{weekly_budget} min rolling weekly budget")
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
    lines = [f"rolling 7-day GPU usage: {used:.1f} / {weekly_budget} min "
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
            print(report(lp))
            print("ledger selftest OK")
    elif len(sys.argv) >= 2 and sys.argv[1] == "report":
        print(report(sys.argv[2] if len(sys.argv) > 2 else "ledger.json"))
    else:
        print(__doc__)
