#!/usr/bin/env python3
"""KaggleCompute LLM proxy CLI: route plain chat calls through Kaggle's
hosted Model Proxy (Kaggle Benchmarks AI credit) instead of a GPU kernel.

  python proxy/cli.py auth [--kaggle-key KEY]   mint/refresh proxy credentials now
  python proxy/cli.py quota                     show Kaggle's AI-credit quota
  python proxy/cli.py models                    list models the catalog serves
  python proxy/cli.py chat --prompt "..." [--model M] [--system S]

Kaggle key priority: --kaggle-key > KC_KAGGLE_KEY > config llm_proxy.kaggle_key
> KAGGLE_API_TOKEN > ~/.kaggle/access_token > ~/.kaggle/kaggle.json.

Exit codes match the connector: 0 ok, 2 validation, 3 budget refusal,
4 Kaggle API error, 5 internal.
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from proxy import auth, client, quota  # noqa: E402


def eprint(*a):
    print(*a, file=sys.stderr)


def cmd_auth(args):
    vals = auth.mint(args.kaggle_key)
    print(json.dumps({k: ("..." + v[-4:] if k == "MODEL_PROXY_API_KEY" else v)
                      for k, v in vals.items()}, indent=1))


def cmd_quota(_args):
    q = quota.server_quota()
    if q is None:
        print("quota unreadable - your kaggle CLI may predate `kaggle b quota`.")
        print("upgrade with: pip install -U kaggle")
        sys.exit(4)
    print(json.dumps(q, indent=1))
    ok, reason = quota.admission()
    print(f"admission: {'OK' if ok else 'REFUSED'} - {reason}")
    if not ok:
        sys.exit(3)


def cmd_models(_args):
    proc = subprocess.run(["kaggle", "benchmarks", "tasks", "models"],
                          capture_output=True, text=True)
    if proc.returncode != 0:
        eprint(proc.stdout, proc.stderr)
        sys.exit(4)
    slugs = []
    for line in proc.stdout.splitlines():
        parts = line.split()
        if parts and parts[0] not in ("Slug",) and not set(line) <= {"-", " "}:
            slugs.append(parts[0])
    print(proc.stdout.rstrip())
    cfg_default = auth.llm_config().get("default_model")
    if cfg_default:
        mark = "reachable" if cfg_default in slugs else "NOT in catalog"
        eprint(f"\nconfigured default_model '{cfg_default}': {mark} in full catalog")
        eprint("NOTE: catalog listing is the FULL list; local tokens serve only the")
        eprint("curated LLMS_AVAILABLE subset (run `kaggle b init -y` to see it).")


def cmd_chat(args):
    ok, reason = quota.admission()
    if not ok:
        eprint(f"budget refusal: {reason}")
        sys.exit(3)
    if reason != "within caps":
        eprint(f"warning: {reason}")
    messages = []
    if args.system:
        messages.append({"role": "system", "content": args.system})
    messages.append({"role": "user", "content": args.prompt})
    try:
        resp = client.chat(messages, model=args.model,
                           temperature=args.temperature,
                           max_tokens=args.max_tokens,
                           explicit_key=args.kaggle_key)
    except client.ProxyError as e:
        eprint(str(e))
        if e.status == 404:
            eprint("hint: local proxy tokens serve a curated subset, not the full catalog.")
            eprint("see LLMS_AVAILABLE via `kaggle b init -y`, or llm_proxy notes in farm.config.yaml.")
        sys.exit(4)
    choice = (resp.get("choices") or [{}])[0]
    content = (choice.get("message") or {}).get("content", "")
    print(content)
    usage = resp.get("usage")
    quota.log_usage(resp.get("model", args.model), usage)
    eprint(json.dumps({"model": resp.get("model"), "usage": usage}))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, fn in (("auth", cmd_auth), ("quota", cmd_quota), ("models", cmd_models)):
        s = sub.add_parser(name)
        s.add_argument("--kaggle-key", default=None)
        s.set_defaults(fn=fn)
    s = sub.add_parser("chat")
    s.add_argument("--prompt", required=True)
    s.add_argument("--model", default=None)
    s.add_argument("--system", default=None)
    s.add_argument("--temperature", type=float, default=None)
    s.add_argument("--max-tokens", type=int, default=None)
    s.add_argument("--kaggle-key", default=None)
    s.set_defaults(fn=cmd_chat)
    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
