"""Read-only local HTTP dashboard; no serial or MQTT commands are exposed."""
import base64
import hmac
import ipaddress
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlsplit, parse_qs
from socketserver import ThreadingMixIn

LOG = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parent / "web"


class SnapshotStore:
    def __init__(self, catalog=None):
        self.catalog = catalog
        self.lock = threading.Lock()
        self.payload = b'{"optolink_ready":false,"mqtt_connected":false}'

    def update(self, snapshot):
        payload = json.dumps(snapshot, allow_nan=False, default=str, separators=(",", ":")).encode()
        with self.lock:
            self.payload = payload

    def read(self):
        with self.lock:
            return self.payload


class LimitedServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, *args, **kwargs):
        self.slots = threading.BoundedSemaphore(8)
        super().__init__(*args, **kwargs)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()


def make_handler(store, password):
    expected = "Basic " + base64.b64encode(("optolink:" + password).encode()).decode() if password else None

    class Handler(BaseHTTPRequestHandler):
        server_version = "OptolinkDashboard"
        sys_version = ""

        def setup(self):
            self.request.settimeout(5)
            super().setup()

        def log_message(self, *args):
            pass

        def reply(self, status, body, mime):
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self' data:; base-uri 'none'; frame-ancestors 'none'")
            if status == 401:
                self.send_header("WWW-Authenticate", 'Basic realm="Optolink", charset="UTF-8"')
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_GET(self):
            if expected and not hmac.compare_digest(self.headers.get("Authorization", "").encode(), expected.encode()):
                return self.reply(401, b"Authentication required", "text/plain")
            # Refuse cross-origin browser reads; no CORS permission is sent.
            if self.headers.get("Sec-Fetch-Site") == "cross-site":
                return self.reply(403, b"Forbidden", "text/plain")
            if self.path == "/api/status":
                return self.reply(200, store.read(), "application/json; charset=utf-8")
            path = urlsplit(self.path)
            if path.path == "/api/catalog":
                catalog = store.catalog
                params = parse_qs(path.query)
                if not catalog:
                    value = {"lists": [], "errors": []}
                elif "model" not in params:
                    value = catalog.describe()
                else:
                    model = params["model"][0]
                    query = params.get("q", [""])[0].casefold()[:120]
                    access = params.get("access", [""])[0]
                    try:
                        offset = max(0, int(params.get("offset", ["0"])[0]))
                    except ValueError:
                        return self.reply(400, b"Invalid offset", "text/plain")
                    entries = [row for row in catalog.lists.get(model, ())
                               if (not access or row["access"] == access)
                               and query in " ".join(str(row[k]) for k in ("name","address","conversion","unit")).casefold()]
                    value = {"model": model, "count": len(entries), "rows": entries[offset:offset+100]}
                return self.reply(200, json.dumps(value, ensure_ascii=False).encode(), "application/json; charset=utf-8")
            paths = {"/": ("index.html", "text/html"), "/app.js": ("app.js", "text/javascript"),
                     "/style.css": ("style.css", "text/css")}
            item = paths.get(self.path)
            if item is None:
                return self.reply(404, b"Not found", "text/plain")
            try:
                body = (ROOT / item[0]).read_bytes()
            except OSError:
                return self.reply(503, b"Dashboard asset unavailable", "text/plain")
            self.reply(200, body, item[1] + "; charset=utf-8")

        def do_POST(self):
            self.reply(405, b"Read-only dashboard", "text/plain")

    return Handler


def start_web(config, store):
    if not config.enabled or not config.web_enabled:
        return None
    password = config.web_password()
    try:
        local = ipaddress.ip_address(config.web_host).is_loopback
    except ValueError:
        local = False
    if not local and len(password) < 12:
        raise ValueError("Non-loopback dashboard requires a password of at least 12 characters")
    server = LimitedServer((config.web_host, config.web_port), make_handler(store, password))
    thread = threading.Thread(target=server.serve_forever, name="optolink-web", daemon=True)
    thread.start()
    return server, thread


def stop_web(web):
    if web:
        server, thread = web
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
