"""Web console (/panel): static assets and JSON API gateway."""
import os
from urllib.parse import urlparse, parse_qs

from ..gemini import log

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
}

_STATIC_FILES = {
    "/panel": "index.html",
    "/panel/": "index.html",
    "/panel/index.html": "index.html",
    "/panel/app.css": "app.css",
    "/panel/app.js": "app.js",
}


def serve_static(handler, path_only: str):
    """Serve the panel's static assets (no auth: the page contains no secrets)."""
    if path_only == "/panel":
        # 补尾斜杠，保证页面内相对路径（app.css/app.js）解析正确
        handler.send_response(301)
        handler.send_header("Location", "/panel/")
        handler.send_header("Content-Length", "0")
        handler.end_headers()
        return
    name = _STATIC_FILES.get(path_only)
    if not name:
        handler.send_json({"error": "not found"}, 404)
        return
    full = os.path.join(STATIC_DIR, name)
    try:
        with open(full, "rb") as f:
            body = f.read()
    except OSError:
        handler.send_json({"error": f"missing static asset: {name}"}, 500)
        return
    handler.send_response(200)
    handler.send_header("Content-Type", _CONTENT_TYPES.get(os.path.splitext(name)[1], "application/octet-stream"))
    handler.send_header("Cache-Control", "no-cache")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def handle_api(handler, method: str, raw_path: str, body: bytes):
    """Dispatch /panel/api/* requests. Caller already enforced API-key auth."""
    from . import api as panel_api

    parsed = urlparse(raw_path)
    endpoint = parsed.path[len("/panel/api/"):].strip("/")
    query = parse_qs(parsed.query)
    try:
        status, payload = panel_api.dispatch(method, endpoint, body, query)
    except panel_api.ApiError as e:
        status, payload = e.status, {"error": e.message}
    except Exception as e:  # noqa: BLE001 - never let one bad request kill the thread
        log(f"panel api error [{method} {endpoint}]: {type(e).__name__}: {e}")
        status, payload = 500, {"error": f"{type(e).__name__}: {e}"}
    handler.send_json(payload, status)
