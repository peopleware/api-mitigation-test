from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import sys

SCHEMA = {
    "openapi": "3.0.3",
    "info": {"title": "Fixture", "version": "1"},
    "paths": {
        "/items": {
            "get": {"responses": {"200": {"description": "ok", "content": {"application/json": {"schema": {"type": "array", "items": {"type": "string"}}}}}}},
            "post": {
                "requestBody": {"required": True, "content": {"application/json": {"schema": {"type": "object", "required": ["name"], "properties": {"name": {"type": "string"}}}}}},
                "responses": {"201": {"description": "created"}},
            },
        }
    },
}

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/openapi.json":
            self.send_json(200, SCHEMA)
        elif self.path == "/items":
            if not self.authorized():
                return
            self.send_json(200, [])
        else:
            self.send_json(404, {})

    def do_POST(self):
        if not self.authorized():
            return
        self.send_json(201, {})

    def authorized(self):
        token = os.getenv("FIXTURE_TOKEN")
        if token and self.headers.get("Authorization") != f"Bearer {token}":
            self.send_json(401, {})
            return False
        return True

    def send_json(self, code, value):
        body = json.dumps(value).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass

if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), Handler).serve_forever()
