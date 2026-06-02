"""Admin, agent-management, workflow, skills, KG, and system handlers."""
from __future__ import annotations

import json
import os
import re
import shutil
import time
import threading
import urllib.request
import urllib.error
import uuid
from urllib.parse import unquote, urlencode

from handlers.admin_workflows import AdminWorkflowHandlers
from handlers.admin_agents import AdminAgentsHandlers
from handlers.admin_costs import AdminCostsHandlers
from handlers.admin_config import AdminConfigHandlers
from handlers.admin_observability import AdminObservabilityHandlers
from handlers.admin_artifacts import AdminArtifactsHandlers


class AdminHandlerMixin(
    AdminWorkflowHandlers,
    AdminAgentsHandlers,
    AdminCostsHandlers,
    AdminConfigHandlers,
    AdminObservabilityHandlers,
    AdminArtifactsHandlers,
):
    """Thin core mixin — inherits the flat area sub-mixins (handlers/admin_*.py).

    Each area's `_handle_*` methods live in their own sub-mixin module; inheriting
    them keeps the combined BrainAgentHandler MRO and all `self.`/global name
    resolution unchanged. EVERY sub-mixin module MUST be in
    server._inject_server_globals' injection list or bare-name globals NameError
    at runtime.
    """

    def _serve_static(self, path):
        """Serve static files from web/ directory."""
        if path == "/":
            path = "/web/index.html"
        elif not path.startswith("/web/"):
            path = "/web" + path

        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        filepath = os.path.join(base, path.lstrip("/"))

        if not os.path.isfile(filepath):
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"Not found")
            return

        ext = filepath.rsplit(".", 1)[-1].lower()
        content_types = {
            "html": "text/html", "css": "text/css", "js": "application/javascript",
            "json": "application/json", "png": "image/png", "svg": "image/svg+xml",
            "ico": "image/x-icon",
            "woff2": "font/woff2", "woff": "font/woff", "ttf": "font/ttf",
        }
        ct = content_types.get(ext, "application/octet-stream")

        with open(filepath, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ct)
        self.send_header("Content-Length", len(data))
        if ext in ("html", "css", "js"):
            # `no-store` (not just `no-cache`): this handler sends no ETag /
            # Last-Modified, so a browser can't revalidate and may serve a
            # stale cached body on soft reload — which silently shipped old
            # chat.js/main.css after a deploy. Force a fresh fetch every load.
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        elif ext in ("woff2", "woff", "ttf"):
            self.send_header("Cache-Control", "public, max-age=31536000, immutable")
        self.end_headers()
        self.wfile.write(data)
