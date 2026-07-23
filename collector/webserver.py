"""Tiny read-only HTTP publisher for the collector.

Serves `data/*.json` (state/flows/prices) with permissive CORS so the static
web console can poll them, plus a `/healthz` endpoint for the platform health
check (Fly.io needs *something* listening on the service port or it crash-loops
the machine). Stdlib only; runs in a daemon thread so it never blocks the
asyncio collector loop.

Security posture: read-only, GET/HEAD only, and it will only serve a bare
`<name>.json` in the data directory — no subpaths, no traversal.
"""
from __future__ import annotations

import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

log = logging.getLogger("webserver")


def make_server(data_dir: Path, port: int) -> ThreadingHTTPServer:
    data_dir = Path(data_dir)

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, body: bytes = b"", ctype: str = "application/json") -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if body and self.command != "HEAD":
                self.wfile.write(body)

        def do_GET(self) -> None:
            path = self.path.split("?", 1)[0].strip("/")
            if path in ("", "healthz", "health"):
                self._send(200, b'{"status":"ok"}')
                return
            # only a bare "<name>.json" in data_dir — no slashes, no traversal
            if path.endswith(".json") and "/" not in path and ".." not in path:
                f = data_dir / path
                if f.is_file():
                    self._send(200, f.read_bytes())
                    return
            self._send(404, b'{"error":"not found"}')

        do_HEAD = do_GET

        def log_message(self, *args) -> None:  # keep the collector log clean
            return

    return ThreadingHTTPServer(("0.0.0.0", port), Handler)


def serve_in_thread(data_dir: Path, port: int) -> ThreadingHTTPServer | None:
    """Start the publisher in a daemon thread. Bind failures (e.g. port in use)
    are non-fatal: log a warning and return None so the collector keeps streaming."""
    try:
        srv = make_server(data_dir, port)
    except OSError as e:
        log.warning("HTTP publisher disabled: could not bind :%d (%s)", port, e)
        return None
    threading.Thread(target=srv.serve_forever, name="webserver", daemon=True).start()
    log.info("serving %s/*.json + /healthz on :%d (CORS *)", data_dir, port)
    return srv
