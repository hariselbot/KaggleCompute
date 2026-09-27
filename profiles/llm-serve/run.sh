#!/usr/bin/env bash
# KaggleCompute llm-serve profile - Ollama + key-protected cloudflared tunnel.
set -euo pipefail
STAGE="$1"; WORK="$2"
P() { python3 -c "import json,os;print(json.loads(os.environ['KC_PARAMS']).get('$1','${2:-}'))"; }
HERE="$(cd "$(dirname "$0")" && pwd)"

case "$STAGE" in
  install)
    curl -fsSL https://ollama.com/install.sh | sh
    curl -fsSL -o /tmp/cloudflared https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64
    chmod +x /tmp/cloudflared
    export OLLAMA_MODELS="$WORK/models"; mkdir -p "$OLLAMA_MODELS"
    nohup env OLLAMA_MODELS="$OLLAMA_MODELS" ollama serve >"$WORK/ollama.log" 2>&1 &
    sleep 5
    ;;
  pull)
    export OLLAMA_MODELS="$WORK/models"
    ollama pull "$(P model)"
    ;;
  serve)
    export OLLAMA_MODELS="$WORK/models"
    export KC_API_KEY="$(P api_key)" KC_MODEL="$(P model)" KC_CTX="$(P num_ctx 65536)" \
           KC_KEEP_ALIVE="$(P keep_alive -1)" KC_IDLE_MIN="$(P idle_shutdown_minutes 30)"
    [ "$KC_API_KEY" = "None" ] && { echo "api_key param is required" >&2; exit 1; }
    # warm the model once so it loads into both GPUs
    curl -s localhost:11434/api/generate -d "{\"model\":\"$KC_MODEL\",\"keep_alive\":$KC_KEEP_ALIVE,\"options\":{\"num_ctx\":$KC_CTX},\"prompt\":\"warm\",\"stream\":false}" >/dev/null
    ollama ps
    nvidia-smi || true
    nohup python3 "$HERE/proxy.py" >"$WORK/proxy.log" 2>&1 &
    sleep 2
    nohup /tmp/cloudflared tunnel --url http://127.0.0.1:8080 --no-autoupdate >"$WORK/tunnel.log" 2>&1 &
    for i in $(seq 1 30); do
      URL=$(grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' "$WORK/tunnel.log" | head -1 || true)
      [ -n "$URL" ] && break; sleep 2
    done
    [ -z "${URL:-}" ] && { echo "tunnel failed; see tunnel.log"; tail -20 "$WORK/tunnel.log"; exit 1; }
    mkdir -p "$WORK/output"
    cat > "$WORK/output/endpoint.json" <<JSON
{"base_url": "$URL/v1", "api_key_header": "X-API-Key", "model": "$KC_MODEL",
 "note": "OpenAI-compatible. Send X-API-Key with the key set in the job params. Quick-tunnel URL is unguessable but treat it as sensitive; it dies with the session."}
JSON
    cat "$WORK/output/endpoint.json"
    # hold the session until idle timeout (proxy.py exits on idle) 
    wait || true
    ;;
  *) echo "unknown stage: $STAGE" >&2; exit 1 ;;
esac
