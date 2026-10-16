"""Disposable runner fixture, independent of any consuming application."""

import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit


class FixtureHandler(BaseHTTPRequestHandler):
    def respond(self, status, body):
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        if status == 401:
            self.send_header("WWW-Authenticate", "Bearer")
        if status == 405:
            self.send_header("Allow", "GET")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/ready":
            self.respond(200, {"ready": True})
        elif path == "/secured":
            if self.headers.get("Authorization") != "Bearer runner-fixture-token":
                self.respond(401, {"error": "Unauthorized"})
            else:
                value = 123 if os.environ.get("FIXTURE_BROKEN") == "true" else "ok"
                self.respond(200, {"status": value})
        else:
            self.respond(404, {"error": "Not found"})

    def unsupported(self):
        self.respond(405, {"error": "Method not allowed"})

    do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = do_HEAD = do_TRACE = unsupported

    def log_message(self, format, *args):
        pass


ThreadingHTTPServer(("0.0.0.0", 8080), FixtureHandler).serve_forever()
