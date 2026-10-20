"""Local bridge for the browser session: serves spec.json and saves posted logs to disk. Listens on localhost only."""
import json
import os
import re
from http.server import BaseHTTPRequestHandler, HTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data")
LOGS = os.path.join(OUT, "logs")
os.makedirs(LOGS, exist_ok=True)


class H(BaseHTTPRequestHandler):
    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Private-Network", "true")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        if self.path.startswith("/ingest"):
            page = (b"<!doctype html><title>ingest</title><body>working<script>"
                    b"(async () => {"
                    b"  const h = location.hash.slice(1).replace(/-/g, '+').replace(/_/g, '/');"
                    b"  const bin = Uint8Array.from(atob(h), c => c.charCodeAt(0));"
                    b"  const txt = await new Response(new Blob([bin]).stream().pipeThrough(new DecompressionStream('gzip'))).text();"
                    b"  const items = JSON.parse(txt);"
                    b"  for (const it of items) await fetch('/logs/' + it.name, {method: 'POST', body: JSON.stringify(it.data)});"
                    b"  document.body.textContent = 'saved ' + items.length;"
                    b"})().catch(e => { document.body.textContent = 'error ' + e; });"
                    b"</script></body>")
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(page)
            return
        if self.path.startswith("/receiver"):
            page = (b"<!doctype html><title>bridge</title><body>ready<script>"
                    b"window.addEventListener('message', async (e) => {"
                    b"  const m = e.data || {};"
                    b"  if (!m.name) return;"
                    b"  const r = await fetch('/logs/' + m.name, {method: 'POST', body: m.body});"
                    b"  e.source.postMessage({ack: m.name, status: r.status}, '*');"
                    b"});"
                    b"</script></body>")
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(page)
            return
        path = os.path.join(OUT, "spec.json") if self.path.startswith("/spec") else None
        if not path or not os.path.exists(path):
            self.send_response(404)
            self._cors()
            self.end_headers()
            return
        data = open(path, "rb").read()
        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(n)
        name = re.sub(r"[^A-Za-z0-9_.-]", "_", self.path.strip("/").split("/")[-1] or "log")
        open(os.path.join(LOGS, name + ".json"), "wb").write(body)
        self.send_response(200)
        self._cors()
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    HTTPServer(("127.0.0.1", 8765), H).serve_forever()
