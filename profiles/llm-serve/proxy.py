#!/usr/bin/env python3
"""Key-checking proxy in front of Ollama, with idle auto-shutdown.

Clients send X-API-Key: <api_key param>. Anything without it gets 401 - the
quick-tunnel URL alone must never be enough to burn quota. Shuts down after
KC_IDLE_MIN minutes with no authenticated request, ending the session's
quota drain.
"""
import json
import os
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

KEY = os.environ["KC_API_KEY"]
IDLE_S = float(os.environ.get("KC_IDLE_MIN", "30")) * 60
UPSTREAM = "http://127.0.0.1:11434"
last_auth = time.time()


class Handler(BaseHTTPRequestHandler):
    def _deny(self):
        self.send_response(401)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"error":"missing or bad X-API-Key"}')

    def _proxy(self):
        global last_auth
        if self.headers.get("X-API-Key") != KEY:
            return self._deny()
        last_auth = time.time()
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None
        req = urllib.request.Request(UPSTREAM + self.path, data=body, method=self.command)
        for h, v in self.headers.items():
            if h.lower() not in ("host", "content-length", "x-api-key"):
                req.add_header(h, v)
        try:
            with urllib.request.urlopen(req, timeout=600) as resp:
                payload = resp.read()
                self.send_response(resp.status)
                self.send_header("Content-Type", resp.headers.get("Content-Type", "application/json"))
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
        except Exception as exc:  # upstream hiccup -> 502, keep serving
            self.send_response(502)
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(exc)}).encode())

    do_GET = do_POST = do_DELETE = _proxy

    def log_message(self, *a):  # quiet
        pass


server = ThreadingHTTPServer(("127.0.0.1", 8080), Handler)
server.timeout = 10
while time.time() - last_auth < IDLE_S:
    server.handle_request()
print(f"idle for {IDLE_S/60:.0f} min - shutting down to stop quota drain")
