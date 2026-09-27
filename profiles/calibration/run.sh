#!/usr/bin/env bash
# KaggleCompute calibration profile - measures what the farm needs to know.
# Prints everything to stdout so a paste-back of the cell output is complete.
set -uo pipefail
STAGE="$1"; WORK="$2"
mkdir -p "$WORK/cal"; cd "$WORK/cal"

case "$STAGE" in
  env)
    echo "===== ENV ====="
    date -u '+UTC START: %Y-%m-%dT%H:%M:%SZ'
    nvidia-smi || echo "NO GPU VISIBLE"
    echo "cpus: $(nproc)"; free -g | head -2
    df -h /kaggle/working 2>/dev/null | tail -1
    python3 --version
    echo "--- kaggle env vars (secrets redacted) ---"
    env | grep -i '^KAGGLE' | sed -E 's/(KEY|TOKEN|SECRET)=.*/\1=<redacted>/I' || true
    ;;
  colmap_install)
    echo "===== COLMAP INSTALL ====="
    t0=$SECONDS
    export DEBIAN_FRONTEND=noninteractive
    (apt-get update -qq && apt-get install -y -qq colmap) >apt.log 2>&1 \
      || (sudo apt-get update -qq && sudo apt-get install -y -qq colmap) >>apt.log 2>&1
    rc=$?; t1=$SECONDS
    echo "apt colmap install: rc=$rc took $((t1-t0))s"
    pip install -q open3d >>apt.log 2>&1 && echo "open3d pip: took $(($SECONDS-t1))s" || echo "open3d pip FAILED (non-fatal)"
    colmap -h 2>&1 | head -2
    ;;
  colmap_gpu_smoke)
    echo "===== COLMAP GPU SMOKE ====="
    python3 - <<'PY'
import random, os
os.makedirs('smoke/img', exist_ok=True)
random.seed(7)
try:
    from PIL import Image, ImageDraw
    for n in range(8):
        im = Image.new('RGB', (640, 480), (30, 30, 30)); d = ImageDraw.Draw(im)
        for i in range(300):
            x, y = random.randint(20, 620), random.randint(20, 460)
            dx = n * 3
            d.ellipse([x+dx-2, y-2, x+dx+2, y+2], fill=(255, 255, (i*37) % 256))
        im.save(f'smoke/img/{n:02d}.png')
    print('8 synthetic images written (PIL)')
except Exception as e:
    print('PIL unavailable:', e)
PY
    t0=$SECONDS
    colmap feature_extractor --database_path smoke/db.db --image_path smoke/img \
      --SiftExtraction.use_gpu 1 --SiftExtraction.max_image_size 640 > smoke/fe.log 2>&1
    rc=$?; t1=$SECONDS
    echo "feature_extractor GPU rc=$rc took $((t1-t0))s on 8 images"
    python3 - <<'PY'
import sqlite3
try:
    c = sqlite3.connect('smoke/db.db')
    n = c.execute('select count(*) from keypoints').fetchone()[0]
    k = c.execute('select coalesce(sum(rows),0) from keypoints').fetchone()[0]
    print(f'keypoints table: {n} images, {k} total keypoints')
except Exception as e:
    print('db check failed:', e)
PY
    grep -im1 'cuda\|gpu' smoke/fe.log || echo "(no gpu mention in log)"
    ;;
  tunnel_test)
    echo "===== TUNNEL + API-KEY TEST ====="
    curl -fsSL -o /tmp/cloudflared https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 \
      && chmod +x /tmp/cloudflared && echo "cloudflared downloaded"
    python3 - <<'PY' &
import http.server, json
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.headers.get('X-API-Key') == 'kc-cal':
            body, code = json.dumps({'ok': True, 'msg': 'tunnel+key works'}).encode(), 200
        else:
            body, code = b'{"error":"unauthorized"}', 401
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers(); self.wfile.write(body)
    def log_message(self, *a): pass
http.server.HTTPServer(('127.0.0.1', 8080), H).serve_forever()
PY
    SRV=$!
    nohup /tmp/cloudflared tunnel --url http://127.0.0.1:8080 --no-autoupdate > tunnel.log 2>&1 &
    URL=""
    for i in $(seq 1 30); do
      URL=$(grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' tunnel.log | head -1 || true)
      [ -n "$URL" ] && break; sleep 2
    done
    echo "tunnel URL: ${URL:-FAILED}"
    if [ -n "$URL" ]; then
      echo "request WITHOUT key -> HTTP $(curl -s -o /dev/null -w '%{http_code}' "$URL/") (expect 401)"
      echo "request WITH key    -> $(curl -s -H 'X-API-Key: kc-cal' "$URL/") (expect ok:true)"
    else
      tail -10 tunnel.log
    fi
    kill $SRV 2>/dev/null || true
    ;;
  quota_probe)
    echo "===== QUOTA PROBE ====="
    date -u '+UTC END: %Y-%m-%dT%H:%M:%SZ'
    echo "This session ran ONE short GPU stage (colmap_gpu_smoke) plus CPU stages."
    echo "ACTION NEEDED: read the weekly GPU quota number shown on https://www.kaggle.com/settings"
    echo "or the notebooks page, and compare with the pre-run reading."
    echo "quota drop ~= session wall minutes  => T4x2 drains at 1x"
    echo "quota drop ~= 2x session minutes    => T4x2 drains at 2x (set drain_multiplier: 2 in config)"
    ;;
  *) echo "unknown stage: $STAGE" >&2; exit 1 ;;
esac
