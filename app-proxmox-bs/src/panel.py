"""The app's page in Home Assistant's sidebar: PBS's own web interface, inside Home Assistant.

PBS's interface can't be served through Home Assistant's ingress (it expects to be at the root of
its own address, with its own certificate and websockets), so this page loads it in a frame
straight from port 8007, from the browser. The page itself only says whether PBS answers.
"""

import json
import logging
import socket
import ssl
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .config import APP_VERSION, PANEL_PORT, PBS_PORT

logger = logging.getLogger(__name__)

WEB_DIR = Path(__file__).resolve().parent / "web"
STATIC = {
    "app.css": "text/css; charset=utf-8",
    "app.js": "text/javascript; charset=utf-8",
}


def pbs_answers(host: str = "127.0.0.1", port: int | None = None, timeout: float = 5.0) -> bool:
    """Whether PBS's proxy answers on its port: a finished TLS handshake counts (its certificate is
    its own), so a slow reply from a busy PBS doesn't read as down."""
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    try:
        with (
            socket.create_connection((host, port or PBS_PORT), timeout=timeout) as raw,
            context.wrap_socket(raw, server_hostname=host),
        ):
            return True
    except OSError:
        return False


class Handler(BaseHTTPRequestHandler):
    server_version = "pbsapp"

    def log_message(self, format: str, *args) -> None:
        logger.debug("Panel: " + format % args)

    def _send(self, status: int, body: bytes, content_type: str, cache: str = "no-cache") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            page = (WEB_DIR / "index.html").read_text().replace("__VERSION__", APP_VERSION)
            self._send(HTTPStatus.OK, page.encode(), "text/html; charset=utf-8", "no-cache, must-revalidate")
        elif path == "/api/status":
            body = json.dumps({"version": APP_VERSION, "port": PBS_PORT, "pbs": pbs_answers()}).encode()
            self._send(HTTPStatus.OK, body, "application/json", "no-store")
        elif path.startswith("/static/") and path[len("/static/") :] in STATIC:
            name = path[len("/static/") :]
            self._send(HTTPStatus.OK, (WEB_DIR / name).read_bytes(), STATIC[name])
        else:
            self._send(HTTPStatus.NOT_FOUND, b"not found", "text/plain")

    do_HEAD = do_GET


def start(port: int = PANEL_PORT) -> ThreadingHTTPServer:
    """Serve the page in a background thread."""
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, name="panel", daemon=True).start()
    logger.info(f"Sidebar page on port {port}")
    return server
