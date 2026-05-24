"""SearXNG per-engine health probing + last-test state.

Self-contained, in-memory status store for the bundled SearXNG instance's
search *engines* (distinct from the SearxngSupervisor, which tracks the
*process*). An hourly daemon (server_daemons._searxng_engine_health_loop)
and the manual "Test now" button (POST /v1/searxng/test-engines) both call
`run_health_check()`; the Settings panel reads `last_snapshot()`.

State is intentionally ephemeral (module globals, not SQLite) — it's live
status, not a record worth surviving a restart; it rebuilds on the first
hourly probe (or the first manual test) after boot.

How a probe works: each enabled general-web engine is queried in ISOLATION
via its `!shortcut`, so a failure is attributable to that one engine.
SearXNG reports a failed engine in the response's `unresponsive_engines`
list; otherwise results>0 means the engine answered. results==0 with no
error means the engine is alive but had no match for the probe query
("empty") — normal for situational engines like wikipedia on a generic
query, NOT a failure.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.parse
import urllib.request

# Query that any healthy general-web engine should return results for.
_PROBE_QUERY = "open source software"
_PROBE_TIMEOUT = 20

# Per-engine probe results from the most recent run. Shape:
#   {"tested_at": float, "engines": [
#       {"name","shortcut","state","latency_ms","detail"}, ...]}
# state ∈ {"ok","fail","empty","error"}.
_lock = threading.Lock()
_snapshot: dict = {"tested_at": 0.0, "engines": [], "running": False}

# Epoch seconds of the next *automatic* probe, published by the hourly daemon
# (NOT touched by manual 'Test now' runs, so the panel shows the true auto
# cadence regardless of manual testing). 0 = unknown / not yet scheduled.
_next_auto_at: float = 0.0


def set_next_auto_at(ts: float) -> None:
    global _next_auto_at
    with _lock:
        _next_auto_at = float(ts)


def get_next_auto_at() -> float:
    with _lock:
        return _next_auto_at


def _searxng_config(base: str) -> dict:
    """Fetch SearXNG's resolved /config (source of truth for which engines are
    actually enabled, after our settings overlay)."""
    req = urllib.request.Request(
        base.rstrip("/") + "/config",
        headers={"Accept": "application/json", "User-Agent": "brain-agent/health"},
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def enabled_web_engines(base: str) -> list[dict]:
    """Enabled engines that participate in general WEB search (the ones whose
    health affects searxng_search quality): categories include both 'general'
    and 'web'. Plus wikipedia/wikidata, which contribute authoritative results
    on encyclopedic queries. Returns [{name, shortcut}] sorted by name."""
    cfg = _searxng_config(base)
    out = []
    for e in cfg.get("engines", []):
        if not e.get("enabled"):
            continue
        cats = e.get("categories") or []
        is_web = "general" in cats and "web" in cats
        is_wiki = e.get("name") in ("wikipedia", "wikidata")
        if is_web or is_wiki:
            out.append({"name": e.get("name", ""), "shortcut": e.get("shortcut", "")})
    return sorted(out, key=lambda x: x["name"])


def _probe_one(base: str, shortcut: str, name: str) -> dict:
    """Probe a single engine in isolation via its !shortcut."""
    q = f"!{shortcut} {_PROBE_QUERY}" if shortcut else _PROBE_QUERY
    url = base.rstrip("/") + "/search?" + urllib.parse.urlencode({"q": q, "format": "json"})
    req = urllib.request.Request(
        url, headers={"Accept": "application/json", "User-Agent": "brain-agent/health"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=_PROBE_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return {"name": name, "shortcut": shortcut, "state": "error",
                "latency_ms": int((time.time() - t0) * 1000), "detail": str(e)[:200]}
    ms = int((time.time() - t0) * 1000)
    unresponsive = [u[0] for u in (data.get("unresponsive_engines") or [])]
    n = len(data.get("results", []))
    if name in unresponsive or unresponsive:
        # The isolated query only ran this one engine, so any unresponsive
        # entry is this engine failing.
        reason = ""
        for u in (data.get("unresponsive_engines") or []):
            if len(u) > 1 and u[1]:
                reason = str(u[1])
                break
        return {"name": name, "shortcut": shortcut, "state": "fail",
                "latency_ms": ms, "detail": reason or "unresponsive"}
    if n > 0:
        return {"name": name, "shortcut": shortcut, "state": "ok",
                "latency_ms": ms, "detail": f"{n} results"}
    return {"name": name, "shortcut": shortcut, "state": "empty",
            "latency_ms": ms, "detail": "no results for probe query"}


def run_health_check(base: str) -> dict:
    """Probe every enabled web/wiki engine in isolation, store + return the
    snapshot. Caller supplies the SearXNG base URL (brain._searxng_base_url())."""
    with _lock:
        _snapshot["running"] = True
    try:
        engines = enabled_web_engines(base) if base else []
        results = [_probe_one(base, e["shortcut"], e["name"]) for e in engines]
        snap = {"tested_at": time.time(), "engines": results, "running": False,
                "base_url": base}
        if not base:
            snap["error"] = "no SearXNG instance configured"
        with _lock:
            _snapshot.clear()
            _snapshot.update(snap)
        return dict(snap)
    except Exception as e:
        snap = {"tested_at": time.time(), "engines": [], "running": False,
                "base_url": base, "error": f"{type(e).__name__}: {e}"}
        with _lock:
            _snapshot.clear()
            _snapshot.update(snap)
        return dict(snap)


def last_snapshot() -> dict:
    """The most recent probe results (empty 'engines' until the first run),
    plus the next scheduled automatic-probe time."""
    with _lock:
        snap = dict(_snapshot)
        snap["next_auto_at"] = _next_auto_at
        return snap
