#!/usr/bin/env python3
"""KaggleCompute kernel artifact downloader.

The kaggle CLI's `kernels output` uses basic auth (kaggle.json
username:key), which Kaggle rejects with "Permission 'kernels.get' was
denied" for our kernels. The same endpoint with the KGAT_ API token as a
Bearer token succeeds and returns a file listing with signed download
URLs, so this helper speaks Bearer and skips the CLI.

Usage:
  kc_download.py <username>/<kernel-slug> [--list] [--out DIR]
                 [--include SUBSTR ...] [--exclude SUBSTR ...]

Auth: $KAGGLE_API_TOKEN, else the "key" from ~/.kaggle/kaggle.json.
"""
import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path

API = "https://www.kaggle.com/api/v1/kernels/output"


def token():
    tok = os.environ.get("KAGGLE_API_TOKEN")
    if tok:
        return tok
    kj = Path.home() / ".kaggle" / "kaggle.json"
    if kj.exists():
        return json.loads(kj.read_text())["key"]
    sys.exit("no KAGGLE_API_TOKEN and no ~/.kaggle/kaggle.json")


def output_listing(ref, tok):
    user, slug = ref.split("/", 1)
    req = urllib.request.Request(
        f"{API}?username={user}&kernelslug={slug}",
        headers={"Authorization": f"Bearer {tok}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


def fname(f):
    return f.get("fileName") or f.get("fileNameNullable")


def furl(f):
    return f.get("url") or f.get("urlNullable")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("kernel", help="username/kernel-slug")
    ap.add_argument("--list", action="store_true", help="list files, don't download")
    ap.add_argument("--out", default=".", help="download directory")
    ap.add_argument("--include", action="append", default=[],
                    help="only files containing this substring (repeatable)")
    ap.add_argument("--exclude", action="append", default=[],
                    help="skip files containing this substring (repeatable)")
    args = ap.parse_args()

    listing = output_listing(args.kernel, token())
    files = [f for f in listing.get("files", []) if fname(f) and furl(f)]
    if args.include:
        files = [f for f in files if any(s in fname(f) for s in args.include)]
    if args.exclude:
        files = [f for f in files if not any(s in fname(f) for s in args.exclude)]

    if args.list:
        for f in files:
            print(fname(f))
        print(f"{len(files)} files", file=sys.stderr)
        return

    out = Path(args.out)
    for i, f in enumerate(files, 1):
        rel = fname(f)
        dest = out / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        print(f"[{i}/{len(files)}] {rel}", flush=True)
        with urllib.request.urlopen(furl(f), timeout=300) as r, open(dest, "wb") as w:
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                w.write(chunk)
    print(f"downloaded {len(files)} files to {out}")


if __name__ == "__main__":
    main()
