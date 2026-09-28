#!/usr/bin/env python3
"""Dollar-denominated quota ledger for the Kaggle Model Proxy.

Unlike GPU hours, real remaining AI-credit quota IS exposed by Kaggle:
`kaggle benchmarks quota` reports Daily and Monthly rows (used / remaining /
total / refillAt). That command needs a recent kaggle CLI; when it is
unavailable we degrade to "unknown" and admission control passes with a
warning instead of guessing.

Admission control mirrors the GPU ledger's semantics: llm_proxy.daily_usd_cap
and monthly_usd_cap in farm.config.yaml are OUR caps (set them at or below
Kaggle's own refill amounts); work is refused once Kaggle reports usage at
or past a cap, and frees up at the next refill. There is no paid overflow -
the server-side budget is a hard wall at $0 remaining.

Per-call usage (model, token counts) is appended to
.kc-llm-state/llm-usage.jsonl for attribution only; it is never used to
estimate dollars, because per-model pricing is not exposed.
"""
import json
import subprocess
import time
from pathlib import Path

from proxy import auth


def server_quota():
    """Return {"Daily": {...}, "Monthly": {...}} from the CLI, or None."""
    proc = subprocess.run(["kaggle", "benchmarks", "quota", "--format", "json"],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        return None  # older CLI without the quota command, or API error
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None
    rows = data if isinstance(data, list) else data.get("rows", data.get("quota", []))
    out = {}
    for r in rows if isinstance(rows, list) else []:
        norm = {str(k).lower(): v for k, v in r.items()}
        period = str(norm.get("period", "")).strip()
        if period:
            out[period] = norm
    return out or None


def _to_float(v):
    if v is None:
        return None
    try:
        return float(str(v).replace("$", "").strip())
    except ValueError:
        return None


def local_spend_usd():
    """(daily, monthly) dollar spend summed from logged response costs.

    Model Proxy responses carry cost.input/output_tokens_cost_nanodollars,
    so exact local dollar accounting is possible from llm-usage.jsonl.
    """
    f = usage_log_path()
    if not f.exists():
        return 0.0, 0.0
    today = time.strftime("%Y-%m-%d", time.gmtime())
    month = today[:7]
    daily = monthly = 0
    for line in f.read_text().splitlines():
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        cost = (e.get("usage") or {}).get("cost") or {}
        nano = int(cost.get("input_tokens_cost_nanodollars", 0)) + \
               int(cost.get("output_tokens_cost_nanodollars", 0))
        ts = e.get("ts", "")
        if ts.startswith(month):
            monthly += nano
        if ts.startswith(today):
            daily += nano
    return daily / 1e9, monthly / 1e9


def admission():
    """(allowed, reason). Caps come from llm_proxy.* in farm.config.yaml."""
    cfg = auth.llm_config()
    daily_cap = _to_float(cfg.get("daily_usd_cap"))
    monthly_cap = _to_float(cfg.get("monthly_usd_cap"))
    q = server_quota()
    if q is None:
        # fall back to exact local spend from logged response costs
        day_usd, month_usd = local_spend_usd()
        for label, spent, cap in (("daily", day_usd, daily_cap),
                                  ("monthly", month_usd, monthly_cap)):
            if cap is not None and spent >= cap:
                return False, (f"{label} cap reached (local accounting): "
                               f"spent ${spent:.4f} of our ${cap:.2f} cap")
        return True, ("server quota unreadable (older kaggle CLI?); using local "
                      f"accounting: ${day_usd:.4f} today, ${month_usd:.4f} this month")
    for period, cap in (("Daily", daily_cap), ("Monthly", monthly_cap)):
        row = q.get(period)
        if row is None or cap is None:
            continue
        used = _to_float(row.get("used"))
        if used is not None and used >= cap:
            return False, (f"{period.lower()} cap reached: used ${used:.2f} of our "
                           f"${cap:.2f} cap (refills {row.get('refillat', '?')})")
    return True, "within caps"


def log_usage(model, usage, source="cli"):
    """Append one attribution record. Dollars are never estimated locally."""
    entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
             "model": model, "source": source,
             "usage": usage or {}}
    f = auth.state_dir() / "llm-usage.jsonl"
    with f.open("a") as fh:
        fh.write(json.dumps(entry) + "\n")


def usage_log_path():
    return Path(auth.state_dir()) / "llm-usage.jsonl"
