"""engine/lib_versions.py — installed versions of the external libraries Brain
depends on, surfaced read-only in General Settings → "Bibliotheken".

The libraries live across FOUR Python environments:
  - server homebrew python (markitdown, mlx-*, spacy, tree-sitter, …) — in-process
  - the mempalace venv (`mempalace`) — sys.path-injected lazily by mempalace_glue
  - the SDK / sidecar venv `.venv_sdk` (`anthropic`) — separate subprocess
  - the crawl4ai render venv `.venv_crawl4ai` (`crawl4ai`, `playwright`, `scrapling`) — separate

So a single in-process `importlib.metadata` sweep can't see them all. We probe
the in-process ones directly and shell the venv interpreters for theirs. The
"installed" date is the dist-info RECORD mtime (≈ pip install time) — a local,
network-free signal of when each lib was last updated on this machine. No live
PyPI lookup (that would be slow + flaky and isn't what the page is for).
"""
from __future__ import annotations

import datetime
import importlib.metadata as _md
import json
import os
import subprocess
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _installed_date(dist: "_md.Distribution") -> str:
    """Best-effort install date = mtime of the dist-info RECORD (or its dir)."""
    try:
        target = None
        for f in dist.files or []:
            if str(f).endswith("RECORD"):
                target = dist.locate_file(f)
                break
        if target is None:
            target = dist.locate_file("")
        return datetime.date.fromtimestamp(os.path.getmtime(target)).isoformat()
    except Exception:
        return ""


def _probe_in_process(name: str) -> dict:
    """Version + install date for a package importable in the server process."""
    try:
        dist = _md.distribution(name)
        return {"version": dist.version, "installed": _installed_date(dist),
                "status": "ok"}
    except _md.PackageNotFoundError:
        return {"version": None, "installed": "", "status": "missing"}
    except Exception as e:  # pragma: no cover — defensive
        return {"version": None, "installed": "", "status": f"error: {e}"}


# Tiny script run inside a venv interpreter: emit {name: {version, installed}}
# for the requested packages. Mirrors _installed_date / _probe_in_process so the
# venv answers carry the same RECORD-mtime install date.
_VENV_PROBE = r"""
import importlib.metadata as m, os, datetime, json, sys
def date(d):
    try:
        t=None
        for f in (d.files or []):
            if str(f).endswith('RECORD'): t=d.locate_file(f); break
        if t is None: t=d.locate_file('')
        return datetime.date.fromtimestamp(os.path.getmtime(t)).isoformat()
    except Exception: return ''
out={}
for n in sys.argv[1:]:
    try:
        d=m.distribution(n); out[n]={'version':d.version,'installed':date(d),'status':'ok'}
    except m.PackageNotFoundError:
        out[n]={'version':None,'installed':'','status':'missing'}
    except Exception as e:
        out[n]={'version':None,'installed':'','status':f'error: {e}'}
print(json.dumps(out))
"""


def _probe_in_venv(py: str, names: list[str]) -> dict:
    """Run the probe inside another venv interpreter. Missing interpreter →
    every requested name marked unreachable (status carries the reason)."""
    if not (py and os.path.isfile(py)):
        why = f"interpreter not found: {py}"
        return {n: {"version": None, "installed": "", "status": why} for n in names}
    try:
        r = subprocess.run([py, "-c", _VENV_PROBE, *names],
                           capture_output=True, text=True, timeout=20)
        if r.returncode != 0:
            why = f"probe failed: {(r.stderr or '').strip()[:120]}"
            return {n: {"version": None, "installed": "", "status": why} for n in names}
        return json.loads(r.stdout)
    except Exception as e:
        why = f"probe error: {e}"
        return {n: {"version": None, "installed": "", "status": why} for n in names}


def _mempalace_version() -> dict:
    """Make mempalace importable (sys.path inject) then probe it in-process."""
    try:
        from engine.mempalace_glue import _ensure_mempalace_importable
        ok, err = _ensure_mempalace_importable()
        if not ok:
            return {"version": None, "installed": "", "status": err or "unavailable"}
    except Exception as e:
        return {"version": None, "installed": "", "status": f"error: {e}"}
    return _probe_in_process("mempalace")


def _cbm_version() -> dict:
    """Probe the codebase-memory-mcp binary (standalone, not a pip lib).
    Version via `<bin> --version`; installed = binary mtime; path from config."""
    try:
        import brain as _brain
        cfg = (_brain._server_config() or {}).get("codebase_memory", {}) or {}
    except Exception:
        cfg = {}
    binp = (cfg.get("bin") or "").strip()
    if not binp or not os.path.exists(binp):
        return {"version": None, "installed": "", "status": "missing",
                "path": binp or "(not configured)"}
    try:
        import subprocess
        out = subprocess.run([binp, "--version"], capture_output=True, text=True,
                             timeout=10).stdout.strip()
        # "codebase-memory-mcp 0.8.1" → "0.8.1"
        ver = out.split()[-1] if out else None
        try:
            mt = os.path.getmtime(binp)
            import datetime
            installed = datetime.date.fromtimestamp(mt).isoformat()
        except Exception:
            installed = ""
        return {"version": ver, "installed": installed,
                "status": "ok" if ver else "error: no version output", "path": binp}
    except Exception as e:
        return {"version": None, "installed": "", "status": f"error: {e}", "path": binp}


# Registry: ordered groups → list of (dist-name, friendly label). Edit here when
# a dependency is added/retired. Grouped by the component each lib serves, NOT by
# venv (that's an implementation detail; `source` below names the venv).
_GROUPS = [
    ("Dokument-Konvertierung (Lesen)", "in_process", [
        ("markitdown", "markitdown"),
        ("pdfminer.six", "pdfminer.six"),
        ("beautifulsoup4", "BeautifulSoup4"),
    ]),
    ("Dokument-Erzeugung (write_document)", "in_process", [
        ("python-docx", "python-docx (.docx)"),
        ("reportlab", "reportlab (.pdf)"),
        ("openpyxl", "openpyxl (.xlsx)"),
        ("python-pptx", "python-pptx (.pptx)"),
        ("markdown-it-py", "markdown-it-py (Markdown-Parser)"),
    ]),
    ("Lokale Inferenz (MLX)", "in_process", [
        ("mlx", "mlx"),
        ("mlx-metal", "mlx-metal"),
        ("mlx-lm", "mlx-lm"),
        ("mlx-vlm", "mlx-vlm"),
    ]),
    ("NLP / Code-Graph", "in_process", [
        ("spacy", "spaCy (NER)"),
        ("tree-sitter", "tree-sitter"),
        ("onnxruntime", "onnxruntime"),
        ("numpy", "NumPy"),
        ("requests", "requests"),
    ]),
    ("Gedächtnis (MemPalace)", "mempalace", [
        ("mempalace", "mempalace"),
    ]),
    ("Anthropic SDK (Sidecar)", "venv_sdk", [
        ("anthropic", "anthropic"),
    ]),
    ("Web-Rendering (crawl4ai + Scrapling)", "venv_crawl4ai", [
        ("crawl4ai", "crawl4ai"),
        ("playwright", "playwright"),
        ("scrapling", "scrapling (Stealth-Render)"),
    ]),
    ("Code-Intelligenz (Code-Mode)", "binary", [
        ("codebase-memory-mcp", "codebase-memory-mcp"),
    ]),
]

_SOURCE_LABELS = {
    "in_process": "Server-Python",
    "mempalace": "MemPalace-venv",
    "venv_sdk": ".venv_sdk",
    "venv_crawl4ai": ".venv_crawl4ai",
    "binary": "Standalone-Binary",
}


def _remote_inference() -> dict:
    """Which MLX-served roles are configured REMOTE (on a Mac mini) and where.
    When embedding/reranker/OCR run remote, the local MLX libs are legitimately
    absent on this host — the 'Lokale Inferenz (MLX)' rows must then read
    'läuft remote' instead of a red 'nicht installiert'. Reads config.json on
    disk (server_config omits the mempalace/ocr sub-configs)."""
    import re
    info = {"any": False, "roles": [], "host": None}
    try:
        cfg_path = os.path.join(_ROOT, "config.json")
        with open(cfg_path, encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        return info
    mp = cfg.get("mempalace", {}) or {}
    urls = []
    if (os.environ.get("MEMPALACE_EMBEDDING_DEVICE")
            or mp.get("embedding_device") or "").lower() == "remote":
        info["roles"].append("Embedding"); urls.append(mp.get("embedding_url", ""))
    rr = mp.get("reranker", {}) or {}
    if rr.get("enabled") and (rr.get("device") or "").lower() == "remote":
        info["roles"].append("Reranker"); urls.append(rr.get("url", ""))
    ocr = cfg.get("ocr", {}) or {}
    if ocr.get("engine") == "mlx_ocr" and (ocr.get("mlx_ocr_url") or ""):
        info["roles"].append("OCR"); urls.append(ocr.get("mlx_ocr_url", ""))
    info["any"] = bool(info["roles"])
    for u in urls:
        m = re.search(r"//([^:/]+)", u or "")
        if m:
            info["host"] = m.group(1); break
    return info


def collect() -> dict:
    """Build the full library-versions report for the settings page."""
    sdk_py = os.path.join(_ROOT, ".venv_sdk", "bin", "python")
    c4_py = os.path.join(_ROOT, ".venv_crawl4ai", "bin", "python")
    remote = _remote_inference()

    # One subprocess per venv, batching all that venv's packages.
    sdk_names = [n for _, src, libs in _GROUPS if src == "venv_sdk" for n, _ in libs]
    c4_names = [n for _, src, libs in _GROUPS if src == "venv_crawl4ai" for n, _ in libs]
    sdk_probe = _probe_in_venv(sdk_py, sdk_names) if sdk_names else {}
    c4_probe = _probe_in_venv(c4_py, c4_names) if c4_names else {}

    groups = []
    for title, src, libs in _GROUPS:
        rows = []
        for dist_name, label in libs:
            if src == "in_process":
                info = _probe_in_process(dist_name)
            elif src == "mempalace":
                info = _mempalace_version()
            elif src == "venv_sdk":
                info = sdk_probe.get(dist_name, {"version": None, "installed": "",
                                                 "status": "unprobed"})
            elif src == "venv_crawl4ai":
                info = c4_probe.get(dist_name, {"version": None, "installed": "",
                                                "status": "unprobed"})
            elif src == "binary":
                info = _cbm_version()
            else:  # pragma: no cover
                info = {"version": None, "installed": "", "status": "unknown source"}
            # MLX group under remote inference: these libs are NOT used anymore —
            # embedding/reranker/OCR run on the Mac mini. Mark every row 'remote'
            # regardless of whether the lib is still installed locally, so the UI
            # doesn't imply the local MLX stack is active. Missing → simply remote;
            # installed → note that it's present but unused.
            if title == "Lokale Inferenz (MLX)" and remote["any"]:
                host = remote["host"] or "dem Mac mini"
                if info.get("status") == "ok":
                    info = {"version": info.get("version"), "installed": "",
                            "status": "remote",
                            "note": f"installiert, aber ungenutzt — läuft remote auf {host}"}
                elif info.get("status") == "missing":
                    info = {"version": None, "installed": "", "status": "remote",
                            "note": f"läuft remote auf {host}"}
            rows.append({"name": label, "dist": dist_name, **info})
        group = {"title": title, "source": _SOURCE_LABELS.get(src, src), "libs": rows}
        if title == "Lokale Inferenz (MLX)" and remote["any"]:
            group["note"] = ("Inferenz ausgelagert (" + ", ".join(remote["roles"])
                             + ") auf " + (remote["host"] or "den Mac mini")
                             + " — lokale MLX-Bibliotheken hier nicht erforderlich.")
        groups.append(group)

    return {
        "python": sys.version.split()[0],
        "platform": sys.platform,
        "groups": groups,
    }
