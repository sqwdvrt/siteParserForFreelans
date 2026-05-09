"""Tiny HTTP health server for standalone consumers."""

from __future__ import annotations

import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

logger = logging.getLogger(__name__)


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path not in {"/healthz", "/readyz", "/metrics"}:
            self.send_error(404)
            return
        if self.path == "/metrics":
            payload = b"site_parser_health_up 1\n"
            content_type = "text/plain; version=0.0.4; charset=utf-8"
        else:
            payload = b"ok\n"
            content_type = "text/plain; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, format: str, *args: object) -> None:
        logger.debug("health server: " + format, *args)


def start_health_server(port: int) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("0.0.0.0", port), _HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True, name=f"health-server-{port}")
    thread.start()
    return server
