#!/usr/bin/env python3
"""Phase 1 standalone tool server. Exposes /v1/tools/call with the same
function bodies the eval/sdk_harness/run.py harness uses.

Brain is NOT involved in Phase 1 — this stub stands in for what
server_lib/tool_mcp.py will be in Phase 2.

Listens on 127.0.0.1:8430 by default. Brain (8420) can stay up; this stub
is intentionally on a separate port so Phase 1 is fully isolated.
"""

import argparse
import http.server
import json
import os
import socketserver
import sys
import time
import traceback


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "eval", "sdk_harness"))
import run as harness_tools  # noqa: E402  — reuses _dispatch and _WRITE_GATE


# --- write_file gating for this server (defaults to /tmp/sidecar_phase1) ---
_WRITE_BASE_DIR = "/tmp/sidecar_phase1"
os.makedirs(_WRITE_BASE_DIR, exist_ok=True)
harness_tools._WRITE_GATE["allow"] = True
harness_tools._WRITE_GATE["base_dir"] = _WRITE_BASE_DIR


# --- palace defaults ---
DEFAULT_PALACE = "/Users/alexander/.mempalace/brain"
DEFAULT_WING = "project__f201b24ff6a2"


class ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class ToolStubHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        sys.stderr.write(f"[tool-stub] {self.address_string()} - {fmt % args}\n")

    def do_POST(self):
        if self.path != "/v1/tools/call":
            self.send_error(404, "not found")
            return
        length = int(self.headers.get("Content-Length") or "0")
        try:
            req = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        except Exception as e:
            self.send_error(400, f"invalid JSON: {e}")
            return
        name = req.get("name") or ""
        args = req.get("args") or {}
        if not name:
            self.send_error(400, "missing tool name")
            return
        t0 = time.time()
        try:
            result = harness_tools._dispatch(name, args, DEFAULT_PALACE, DEFAULT_WING)
        except Exception as e:
            result = {"error": f"tool crashed: {type(e).__name__}: {e}",
                      "traceback": traceback.format_exc()[-1500:]}
        elapsed = time.time() - t0
        is_error = isinstance(result, dict) and "error" in result
        # Stringify result so wire shape stays uniform — same as Brain will do in Phase 2
        if not isinstance(result, str):
            result = json.dumps(result, ensure_ascii=False)
        body = json.dumps({
            "result": result,
            "is_error": is_error,
            "elapsed_ms": int(elapsed * 1000),
        }, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8430)
    ap.add_argument("--write-base-dir", default=_WRITE_BASE_DIR)
    args = ap.parse_args()
    harness_tools._WRITE_GATE["base_dir"] = args.write_base_dir
    os.makedirs(args.write_base_dir, exist_ok=True)
    srv = ThreadingHTTPServer((args.host, args.port), ToolStubHandler)
    print(f"[tool-stub] listening on http://{args.host}:{args.port}  "
          f"write_base_dir={args.write_base_dir}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n[tool-stub] shutting down", flush=True)
        srv.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
