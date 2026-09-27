#!/usr/bin/env python3
"""KaggleCompute stage runner. Executes one job inside a session.

Usage: run.py --profile <profile_dir> --job <job.yaml> [--config farm.config.yaml]

Behavior:
- Resolves params: profile defaults < config < job params. Missing required
  params fail fast (exit 2).
- Runs stages in order via the profile's run.sh; each stage gets the work dir
  and KC_PARAMS (JSON). A stage with a .done checkpoint marker is skipped, so
  resubmitting after a session cap resumes where it stopped.
- GPU-flagged stages are admission-checked against the budget ledger before
  starting, and their wall time (x drain_multiplier) is recorded after.
- Always writes job-manifest.json with per-stage status (see CONNECTOR.md).
Exit codes: 0 complete, 2 validation error, 3 budget refusal, 5 stage failure.
"""
import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ledger  # noqa: E402

try:
    import yaml
except ImportError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "pyyaml"])
    import yaml

KC_WORK = Path(os.environ.get("KC_WORK", "/kaggle/working" if Path("/kaggle/working").exists() else "./kc-work"))
REPO_ROOT = Path(__file__).resolve().parent.parent


def load_yaml(path):
    with open(path) as f:
        return yaml.safe_load(f) or {}


def utcnow():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_manifest(work_dir, manifest):
    manifest["updated_at"] = utcnow()
    (work_dir / "job-manifest.json").write_text(json.dumps(manifest, indent=1))


def resolve_params(profile, config, job):
    declared = profile.get("params", {})
    resolved = {}
    missing = []
    for key, default in declared.items():
        if key in job.get("params", {}):
            resolved[key] = job["params"][key]
        elif key in config.get("profile_defaults", {}).get(profile["name"], {}):
            resolved[key] = config["profile_defaults"][profile["name"]][key]
        elif default is not None:
            resolved[key] = default
        else:
            missing.append(key)
    # pass through undeclared job params too (profiles may accept extras)
    for key, val in job.get("params", {}).items():
        resolved.setdefault(key, val)
    if missing:
        print(f"ERROR: missing required params: {', '.join(missing)}", file=sys.stderr)
        sys.exit(2)
    return resolved


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", required=True, help="profile directory")
    ap.add_argument("--job", required=True, help="job YAML file")
    ap.add_argument("--config", default=str(REPO_ROOT / "config" / "farm.config.yaml"))
    args = ap.parse_args()

    profile_dir = Path(args.profile).resolve()
    profile = load_yaml(profile_dir / "profile.yaml")
    config = load_yaml(args.config) if Path(args.config).exists() else {}
    job = load_yaml(args.job)

    job_id = job.get("job_id")
    if not job_id or not all(c.isalnum() or c == "-" for c in job_id):
        print("ERROR: job_id required, [a-z0-9-] only", file=sys.stderr)
        sys.exit(2)

    path = profile.get("path", "batch")
    params = resolve_params(profile, config, job)
    stages = profile.get("stages", [])
    run_sh = profile_dir / "run.sh"

    quota_cfg = config.get("quota", {})
    weekly_budget = quota_cfg.get("weekly_gpu_minutes", 1800)
    drain = quota_cfg.get("drain_multiplier", 1)
    path_budget = config.get("paths", {}).get(path, {}).get("weekly_gpu_minutes")

    work_dir = KC_WORK / job_id
    state_dir = work_dir / ".kc-state"
    state_dir.mkdir(parents=True, exist_ok=True)
    ledger_path = work_dir / "ledger.json"

    manifest = {
        "job_id": job_id, "profile": profile["name"], "status": "running",
        "stages": [], "gpu_minutes_total": 0.0, "outputs": {}, "error": None,
    }
    write_manifest(work_dir, manifest)

    env = dict(os.environ)
    env["KC_PARAMS"] = json.dumps(params)
    env["KC_JOB_ID"] = job_id
    env["KC_WORK"] = str(work_dir)

    for stage in stages:
        name, is_gpu = stage["name"], bool(stage.get("gpu"))
        marker = state_dir / f"{name}.done"
        entry = {"name": name, "gpu": is_gpu, "status": "pending", "minutes": 0.0}

        if marker.exists():
            entry["status"] = "skipped-checkpoint"
            manifest["stages"].append(entry)
            write_manifest(work_dir, manifest)
            print(f"[{name}] checkpoint found, skipping")
            continue

        if is_gpu:
            allowed, why = ledger.check(
                ledger_path, path, params.get("gpu_minutes_estimate"),
                weekly_budget, path_budget)
            if not allowed:
                manifest["status"] = "queued-budget"
                manifest["error"] = why
                entry["status"] = "blocked-budget"
                manifest["stages"].append(entry)
                write_manifest(work_dir, manifest)
                print(f"[{name}] REFUSED by budget: {why}")
                sys.exit(3)

        print(f"[{name}] starting (gpu={is_gpu})")
        entry["status"] = "running"
        write_manifest(work_dir, manifest)
        t0 = time.time()
        proc = subprocess.run(["bash", str(run_sh), name, str(work_dir)], env=env)
        minutes = (time.time() - t0) / 60.0
        entry["minutes"] = round(minutes, 2)

        if proc.returncode != 0:
            entry["status"] = "failed"
            manifest["stages"].append(entry)
            manifest["status"] = "failed"
            manifest["error"] = f"stage '{name}' exited {proc.returncode}"
            write_manifest(work_dir, manifest)
            sys.exit(5)

        entry["status"] = "done"
        marker.touch()
        if is_gpu:
            charged = minutes * drain
            ledger.record(ledger_path, job_id, path, name, charged)
            manifest["gpu_minutes_total"] += charged
        manifest["stages"].append(entry)
        write_manifest(work_dir, manifest)
        print(f"[{name}] done in {minutes:.1f} min")

    manifest["status"] = "complete"
    manifest["gpu_minutes_total"] = round(manifest["gpu_minutes_total"], 2)
    out_dir = work_dir / "output"
    if out_dir.exists():
        manifest["outputs"]["files"] = sorted(
            str(p.relative_to(out_dir)) for p in out_dir.rglob("*") if p.is_file())
    write_manifest(work_dir, manifest)
    print(f"job {job_id} complete; GPU minutes charged: {manifest['gpu_minutes_total']}")


if __name__ == "__main__":
    main()
