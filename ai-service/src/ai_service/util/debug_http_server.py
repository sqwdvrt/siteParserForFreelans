"""Minimal internal HTTP server for debug-match diagnostics."""

from __future__ import annotations

import json
import logging
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

logger = logging.getLogger(__name__)


def build_debug_handler(use_case):
    class _DebugHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args) -> None:  # noqa: A003
            logger.debug("debug-http: " + format, *args)

        def do_GET(self) -> None:  # noqa: N802
            parsed = urllib.parse.urlsplit(self.path)
            if parsed.path != "/internal/debug/match":
                self.send_error(404, "not found")
                return
            params = urllib.parse.parse_qs(parsed.query)
            raw_user_id = (params.get("user_id") or [""])[0]
            job_url = (params.get("job_url") or [""])[0]
            try:
                user_id = int(raw_user_id)
            except ValueError:
                self.send_error(400, "invalid user_id")
                return
            if user_id <= 0 or not job_url.strip():
                self.send_error(400, "missing query params")
                return
            try:
                payload = use_case.execute(user_id, job_url.strip())
            except Exception as exc:  # noqa: BLE001
                logger.exception("debug-match handler failed: %s", exc)
                self.send_error(500, "internal error")
                return
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return _DebugHandler


def start_debug_http_server(port: int, use_case) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("0.0.0.0", port), build_debug_handler(use_case))
    server.daemon_threads = True
    return server
