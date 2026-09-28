#!/usr/bin/env python3
"""KaggleCompute LLM proxy auth: mint and refresh Model Proxy credentials.

The Kaggle Model Proxy token is short-lived (TTL is server-set and returned
as MODEL_PROXY_EXPIRY_TIME). This module owns its lifecycle: resolve which
Kaggle API key to use, mint proxy credentials via `kaggle benchmarks auth`,
cache them in a local gitignored state dir, and re-mint before expiry.

Kaggle key resolution priority (project decision 2026-09-28):
  1. explicit input: --kaggle-key flag, KC_KAGGLE_KEY env var, or config
     llm_proxy.kaggle_key (checked in that order)
  2. KAGGLE_API_TOKEN env var (the kaggle CLI's own convention)
  3. ~/.kaggle/access_token  (newer kaggle CLI raw-token file)
  4. ~/.kaggle/kaggle.json   (classic {"username","key"} JSON, or a raw token)

Nothing is ever written back to those files, and keys never enter the repo.
"""
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def eprint(*a):
    print(*a, file=sys.stderr)


def repo_config():
    cfg_path = REPO_ROOT / "config" / "farm.config.yaml"
    if not cfg_path.exists():
        return {}
    import yaml
    return yaml.safe_load(cfg_path.read_text()) or {}


def llm_config():
    return repo_config().get("llm_proxy", {}) or {}


def state_dir():
    d = REPO_ROOT / llm_config().get("state_dir", ".kc-llm-state")
    d.mkdir(parents=True, exist_ok=True)
    return d


def state_file():
    return state_dir() / "proxy.env"


def resolve_kaggle_key(explicit=None):
    """Return (key, source) honoring the input-over-file priority, or (None, None)."""
    if explicit:
        return explicit.strip(), "flag"
    env_key = os.environ.get("KC_KAGGLE_KEY")
    if env_key:
        return env_key.strip(), "KC_KAGGLE_KEY"
    cfg_key = llm_config().get("kaggle_key")
    if cfg_key:
        return str(cfg_key).strip(), "config llm_proxy.kaggle_key"
    env_key = os.environ.get("KAGGLE_API_TOKEN")
    if env_key:
        return env_key.strip(), "KAGGLE_API_TOKEN"
    raw = Path.home() / ".kaggle" / "access_token"
    if raw.exists():
        token = raw.read_text().strip()
        if token:
            return token, "~/.kaggle/access_token"
    cfg = Path.home() / ".kaggle" / "kaggle.json"
    if cfg.exists():
        text = cfg.read_text().strip()
        try:
            data = json.loads(text)
            if data.get("key"):
                return data["key"].strip(), "~/.kaggle/kaggle.json"
        except json.JSONDecodeError:
            if text:  # the file holds a raw token, not JSON
                return text, "~/.kaggle/kaggle.json (raw token)"
    return None, None


def parse_env_file(path):
    vals = {}
    for line in Path(path).read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            vals[k.strip()] = v.strip().strip('"').strip("'")
    return vals


def _expiry_epoch(iso):
    try:
        return datetime.fromisoformat(iso.rstrip("Z")).replace(tzinfo=timezone.utc).timestamp()
    except (ValueError, AttributeError):
        return 0


def current_credentials():
    """Return cached creds dict, or None if missing/unusable."""
    f = state_file()
    if not f.exists():
        return None
    vals = parse_env_file(f)
    if vals.get("MODEL_PROXY_URL") and vals.get("MODEL_PROXY_API_KEY"):
        return vals
    return None


def mint(explicit_key=None, quiet=False):
    """Mint fresh Model Proxy credentials via the kaggle CLI."""
    key, source = resolve_kaggle_key(explicit_key)
    if not key:
        eprint("ERROR: no Kaggle API key found. Pass --kaggle-key, set KC_KAGGLE_KEY or")
        eprint("KAGGLE_API_TOKEN, or place ~/.kaggle/access_token / ~/.kaggle/kaggle.json")
        sys.exit(4)
    env = dict(os.environ)
    env["KAGGLE_API_TOKEN"] = key  # input key wins for the CLI too
    f = state_file()
    proc = subprocess.run(
        ["kaggle", "benchmarks", "auth", "-y", "--env-file", str(f)],
        capture_output=True, text=True, env=env)
    if proc.returncode != 0:
        eprint(proc.stdout, proc.stderr)
        eprint("NOTE: 404 = Benchmarks beta not enabled; 403 = phone verification or stale key.")
        sys.exit(4)
    vals = parse_env_file(f)
    if not vals.get("MODEL_PROXY_API_KEY"):
        eprint(f"ERROR: auth ran but {f} has no MODEL_PROXY_API_KEY")
        eprint(proc.stdout, proc.stderr)
        sys.exit(5)
    if not quiet:
        exp = vals.get("MODEL_PROXY_EXPIRY_TIME", "?")
        eprint(f"minted proxy credentials (key source: {source}, expires: {exp})")
    return vals


def get_credentials(explicit_key=None, force_refresh=False):
    """Return creds dict with MODEL_PROXY_URL/API_KEY, re-minting near expiry.

    Re-mints when the token is missing, forced, or inside the configured
    refresh margin (default 15 min before MODEL_PROXY_EXPIRY_TIME).
    """
    margin = int(llm_config().get("refresh_margin_seconds", 900))
    vals = None if force_refresh else current_credentials()
    if vals:
        exp = _expiry_epoch(vals.get("MODEL_PROXY_EXPIRY_TIME", ""))
        if exp and exp - time.time() > margin:
            return vals
    return mint(explicit_key, quiet=True)
