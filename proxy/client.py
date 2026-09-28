#!/usr/bin/env python3
"""OpenAI-compatible chat client for the Kaggle Model Proxy.

The proxy exposes two API flavors off MODEL_PROXY_URL (per the
kaggle-benchmarks source): <base>/openapi (OpenAI chat completions) and
<base>/genai (Google GenAI). This client uses the OpenAI flavor with the
stdlib only - no openai SDK dependency.

Notes verified against Kaggle's own client code:
- temperature is not currently supported by the proxy; it is omitted
  unless explicitly passed.
- streaming is disabled here (stream_responses = False upstream too).
"""
import json
import sys
import urllib.error
import urllib.request

from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from proxy import auth  # noqa: E402


class ProxyError(Exception):
    def __init__(self, status, body):
        self.status = status
        super().__init__(f"model proxy HTTP {status}: {body[:500]}")


def _base(url):
    url = url.rstrip("/")
    for suffix in ("/openapi", "/genai"):
        if url.endswith(suffix):
            url = url[: -len(suffix)]
    return url


def _post(url, key, payload):
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        raise ProxyError(e.code, body) from None
    except urllib.error.URLError as e:
        raise ProxyError(0, str(e)) from None


def chat(messages, model=None, temperature=None, max_tokens=None,
         explicit_key=None, extra=None):
    """One chat completion. Returns the full OpenAI-shaped response dict.

    `messages` is the standard [{"role": ..., "content": ...}] list.
    Re-mints credentials once and retries on 401/403.
    """
    cfg = auth.llm_config()
    model = model or cfg.get("default_model") or "gemini-3.1-flash-lite-preview"
    allowed = cfg.get("allowed_models") or []
    if allowed and model not in allowed:
        raise SystemExit(f"model '{model}' not in llm_proxy.allowed_models")

    payload = {"model": model, "messages": messages, "stream": False}
    if temperature is not None:
        payload["temperature"] = temperature
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    if extra:
        payload.update(extra)

    for attempt in (False, True):
        creds = auth.get_credentials(explicit_key, force_refresh=attempt)
        url = _base(creds["MODEL_PROXY_URL"]) + "/openapi/chat/completions"
        try:
            return _post(url, creds["MODEL_PROXY_API_KEY"], payload)
        except ProxyError as e:
            if e.status in (401, 403) and not attempt:
                continue  # token died early: re-mint once and retry
            raise
