#!/usr/bin/env bash
# KaggleCompute calibration profile v2 - measures what the farm needs to know.
set -uo pipefail
STAGE="$1"; WORK="$2"
mkdir -p "$WORK/cal"; cd "$WORK/cal"
exec > >(tee -a "$WORK/results.txt") 2>&1
echo "----- stage: $STAGE -----"

case "$STAGE" in
  env)
    echo "===== ENV ====="
    date -u '+UTC START: %Y-%m-%dT%H:%M:%SZ'
    nvidia-smi || echo "NO GPU VISIBLE"
    echo "cpus: $(nproc)"; free -g | head -2
    python3 --version
    python3 -c "import torch; print('torch', torch.__version__, 'cuda devices:', torch.cuda.device_count())" 2>/dev/null || echo "torch not preinstalled"
    ;;
  colmap_install)
    echo "===== PYCOLMAP INSTALL (CUDA wheels) ====="
    t0=$SECONDS
    pip install -q pycolmap-cuda12 2>&1 | tail -2
    rc=$?; t1=$SECONDS
    echo "pip pycolmap-cuda12: rc=$rc took $((t1-t0))s"
    python3 - <<'PY'
import pycolmap
print('pycolmap version:', pycolmap.get_version() if hasattr(pycolmap, 'get_version') else 'unknown')
print('has_cuda:', pycolmap.has_cuda if hasattr(pycolmap, 'has_cuda') else 'attr-missing')
PY
    ;;
  colmap_gpu_smoke)
    echo "===== PYCOLMAP GPU SMOKE ====="
    python3 - <<'PY'
import random, os, time, sqlite3
os.makedirs('smoke/img', exist_ok=True)
random.seed(7)
from PIL import Image, ImageDraw
for n in range(8):
    im = Image.new('RGB', (640, 480), (30, 30, 30)); d = ImageDraw.Draw(im)
    for i in range(300):
        x, y = random.randint(20, 620), random.randint(20, 460)
        dx = n * 3
        d.ellipse([x+dx-2, y-2, x+dx+2, y+2], fill=(255, 255, (i*37) % 256))
    im.save(f'smoke/img/{n:02d}.png')
import pycolmap
db, imgs = 'smoke/db.db', 'smoke/img'
if os.path.exists(db): os.remove(db)
t0 = time.time()
device_used = 'cpu-fallback'
try:
    pycolmap.extract_features(db, imgs, device=pycolmap.Device.cuda)
    device_used = 'cuda'
except TypeError:
    pycolmap.extract_features(db, imgs)
except Exception as e:
    print('cuda extraction failed:', type(e).__name__, str(e)[:200])
    pycolmap.extract_features(db, imgs)
t1 = time.time()
try:
    pycolmap.match_exhaustive(db, device=pycolmap.Device.cuda)
except TypeError:
    pycolmap.match_exhaustive(db)
except Exception as e:
    print('cuda matching failed:', type(e).__name__, str(e)[:200])
t2 = time.time()
c = sqlite3.connect(db)
n = c.execute('select count(*) from keypoints').fetchone()[0]
k = c.execute('select coalesce(sum(rows),0) from keypoints').fetchone()[0]
m = c.execute('select count(*) from two_view_geometries').fetchone()[0]
print(f'DEVICE USED: {device_used}')
print(f'extract: {t1-t0:.1f}s | match: {t2-t1:.1f}s | {n} images, {k} keypoints, {m} matched pairs')
PY
    ;;
  gpu_load)
    echo "===== SUSTAINED GPU LOAD (10 min, both T4s) ====="
    date -u '+GPU LOAD START: %Y-%m-%dT%H:%M:%SZ'
    python3 - <<'PY'
import torch, time
devs = [torch.device(f'cuda:{i}') for i in range(torch.cuda.device_count())]
print('loading', len(devs), 'gpus')
t0 = time.time()
iters = 0
while time.time() - t0 < 600:
    for d in devs:
        x = torch.randn(4096, 4096, device=d)
        y = x @ x
    torch.cuda.synchronize()
    iters += 1
el = time.time() - t0
print(f'GPU LOAD DONE: {el/60:.2f} min, {iters} iterations')
PY
    date -u '+GPU LOAD END: %Y-%m-%dT%H:%M:%SZ'
    ;;
  tunnel_test)
    echo "===== TUNNEL + API-KEY TEST (fixed wait) ====="
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
    for i in $(seq 1 45); do grep -q 'Registered tunnel connection' tunnel.log && break; sleep 2; done
    URL=$(grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' tunnel.log | head -1 || true)
    echo "tunnel URL: ${URL:-FAILED}"
    CODE=000
    for i in $(seq 1 8); do
      [ -z "$URL" ] && break
      CODE=$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 "$URL/")
      [ "$CODE" != "000" ] && break; sleep 5
    done
    echo "request WITHOUT key -> HTTP $CODE (expect 401)"
    [ -n "$URL" ] && echo "request WITH key    -> $(curl -s --max-time 15 -H 'X-API-Key: kc-cal' "$URL/") (expect ok:true)"
    kill $SRV 2>/dev/null || true
    ;;
  quota_probe)
    echo "===== QUOTA PROBE ====="
    date -u '+UTC END: %Y-%m-%dT%H:%M:%SZ'
    echo "GPU LOAD ran a measured 10-minute window on both T4s (timestamps above)."
    echo "ACTION NEEDED: read weekly GPU quota at https://www.kaggle.com/settings and compare with the pre-run reading."
    echo "quota drop ~= 10 min  => T4x2 drains at 1x ;  ~= 20 min => 2x (set drain_multiplier accordingly)"
    ;;
  *) echo "unknown stage: $STAGE" >&2; exit 1 ;;
esac
