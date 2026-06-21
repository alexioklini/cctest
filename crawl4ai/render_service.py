"""crawl4ai render service — headless-browser HTML→markdown for Brain.

A tiny stdlib HTTP server (no framework dep) that renders a URL in a real
headless Chromium (via crawl4ai) so JavaScript-built pages — SvelteKit/React
apps, anything client-rendered — yield their actual content, which the plain
HTTP `web_fetch` cannot (it returns the empty JS shell).

Runs under its own venv (.venv_crawl4ai, Python 3.13) and is supervised by
`Crawl4aiSupervisor` (server_lib/sidecar_supervisor.py) exactly like the
sidecar and SearXNG. Brain calls it only as a FALLBACK — when the cheap HTTP
fetch returns an empty / JS-shell result — so static pages stay fast.

Endpoints:
  GET  /health                   -> {"status": "ok"}
  POST /render {url,...}          -> headless Chromium (crawl4ai). {"success",
                                     "markdown", "length", "url", "error"}
  POST /render_stealth {url,...}  -> stealth Firefox (Scrapling StealthyFetcher,
                                     Cloudflare Turnstile bypass) -> HTML then
                                     crawl4ai-markdown. SECOND fallback, tried by
                                     Brain only after /render still comes back
                                     thin/blocked. Same result shape as /render.
"""
import argparse
import asyncio
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

try:
    from crawl4ai import AsyncWebCrawler, CrawlerRunConfig, BrowserConfig
    from crawl4ai.async_configs import CacheMode
except Exception as e:  # pragma: no cover
    print(f"[crawl4ai] FATAL: import failed: {e}", flush=True)
    sys.exit(1)

# Scrapling's StealthyFetcher is the SECOND-fallback render path (POST
# /render_stealth): a stealth Firefox (Camoufox) that bypasses Cloudflare
# Turnstile/Interstitial + bot-detection that plain headless Chromium (the
# crawl4ai path above) gets stopped by. Soft import — if Scrapling isn't
# installed in this venv the service still serves /render; /render_stealth
# just reports it's unavailable so Brain falls through to the HTTP result.
try:
    from scrapling.fetchers import StealthyFetcher
    _STEALTH_OK = True
except Exception as e:  # pragma: no cover
    StealthyFetcher = None
    _STEALTH_OK = False
    print(f"[crawl4ai] scrapling stealth unavailable: {e}", flush=True)

# One event loop + one browser, shared across requests (browser startup is the
# expensive part — keep it warm). All renders are marshalled onto this loop.
_loop = asyncio.new_event_loop()
_crawler = None
_crawler_lock = threading.Lock()


def _loop_runner():
    asyncio.set_event_loop(_loop)
    _loop.run_forever()


async def _ensure_crawler():
    global _crawler
    if _crawler is None:
        bcfg = BrowserConfig(headless=True, verbose=False)
        _crawler = AsyncWebCrawler(config=bcfg)
        await _crawler.start()
    return _crawler


async def _render(url: str, wait_until: str, delay: float, timeout_ms: int) -> dict:
    """Render one URL and return its markdown. The wait config is the load-
    bearing detail: client-rendered pages return nothing without waiting for
    the JS to paint (networkidle + a short settle delay)."""
    crawler = await _ensure_crawler()
    cfg = CrawlerRunConfig(
        wait_until=wait_until,
        delay_before_return_html=delay,
        page_timeout=timeout_ms,
        cache_mode=CacheMode.BYPASS,  # always fresh — Brain owns its own cache
        verbose=False,
    )
    r = await crawler.arun(url=url, config=cfg)
    md = (r.markdown or "") if r.success else ""
    return {
        "success": bool(r.success) and bool(md.strip()),
        "markdown": md,
        "length": len(md),
        "url": getattr(r, "url", url) or url,
        "error": "" if r.success else (r.error_message or "render failed")[:500],
    }


async def _html_to_markdown(html: str) -> str:
    """Convert a raw HTML string to markdown using crawl4ai's OWN markdown
    generator (via its `raw:` URL scheme — no network) so the stealth path's
    output is byte-identical in format to the primary /render path. Reuses the
    warm crawler; this is a pure in-memory conversion."""
    if not (html or "").strip():
        return ""
    crawler = await _ensure_crawler()
    r = await crawler.arun(
        url="raw:" + html,
        config=CrawlerRunConfig(cache_mode=CacheMode.BYPASS, verbose=False),
    )
    return (r.markdown or "") if r.success else ""


async def _render_stealth(url: str, network_idle: bool, timeout_ms: int) -> dict:
    """Render one URL with Scrapling's StealthyFetcher (stealth Firefox), then
    convert the rendered HTML to markdown. The whole point of this path is
    anti-bot bypass (Cloudflare Turnstile/Interstitial) that the crawl4ai
    Chromium path can't get through — so it's tried only AFTER /render."""
    if not _STEALTH_OK:
        return {"success": False, "markdown": "", "length": 0, "url": url,
                "error": "scrapling not installed in this venv"}
    page = await StealthyFetcher.async_fetch(
        url,
        headless=True,
        network_idle=network_idle,
        timeout=timeout_ms,
        # solve_cloudflare wires Scrapling's Turnstile/Interstitial bypass —
        # the reason this fallback exists.
        solve_cloudflare=True,
    )
    html = page.html_content or ""
    md = await _html_to_markdown(html)
    status = getattr(page, "status", 0) or 0
    ok = bool(md.strip()) and 200 <= status < 400
    return {
        "success": ok,
        "markdown": md,
        "length": len(md),
        "url": getattr(page, "url", url) or url,
        "status": status,
        "error": "" if ok else f"stealth render: status={status}, md_len={len(md.strip())}",
    }


def _run_coro(coro):
    """Submit a coroutine to the shared loop from a request thread and block."""
    fut = asyncio.run_coroutine_threadsafe(coro, _loop)
    return fut.result()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet — supervisor tags our stdout
        pass

    def _send(self, code, obj):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.split("?")[0] == "/health":
            self._send(200, {"status": "ok"})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        path = self.path.split("?")[0]
        if path not in ("/render", "/render_stealth"):
            self._send(404, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, TypeError):
            self._send(400, {"error": "invalid JSON"})
            return
        url = (body.get("url") or "").strip()
        if not url:
            self._send(400, {"error": "no url"})
            return
        try:
            if path == "/render_stealth":
                network_idle = bool(body.get("network_idle", True))
                timeout_ms = int(body.get("timeout_ms", 60000))
                coro = _render_stealth(url, network_idle, timeout_ms)
            else:
                wait_until = body.get("wait_until") or "networkidle"
                delay = float(body.get("delay", 2.5))
                timeout_ms = int(body.get("timeout_ms", 30000))
                coro = _render(url, wait_until, delay, timeout_ms)
            self._send(200, _run_coro(coro))
        except Exception as e:
            self._send(200, {"success": False, "markdown": "", "length": 0,
                             "url": url, "error": f"{type(e).__name__}: {e}"[:500]})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8422)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args()

    threading.Thread(target=_loop_runner, daemon=True).start()
    # Warm the browser at boot so the first real render isn't slow.
    try:
        _run_coro(_ensure_crawler())
    except Exception as e:
        print(f"[crawl4ai] browser warmup failed (will retry per-request): {e}", flush=True)

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"[crawl4ai] render service listening on {args.host}:{args.port}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
