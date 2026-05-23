"""
doc_convert.py — Convert binary documents (PDF, DOCX) to markdown so the
MemPalace miner (which only reads .md/.txt/code extensions) can pick them
up automatically.

Pre-mine pass for the project-sync daemon: walks each input folder, finds
unsupported binary files, writes companion .md files into a hidden
`.brain-extracted/` subdirectory. Idempotent via (mtime, size) hash so
re-runs are cheap and dedup naturally.

Public surface:
    SUPPORTED_EXTS               — extensions we convert (case-insensitive)
    convert_folder(root) -> dict — sync_status-style summary
    sweep_stale(root)    -> int  — drop converted .md files whose source vanished
    extract_md_path(root, source) -> str — resolve where a source file's md lives

Frontmatter convention written to every produced .md:
    <!-- brain-source: /abs/path/to/original.pdf -->
    <!-- brain-source-mtime: 1730000000 -->
    <!-- brain-source-size: 12345 -->

The frontmatter is HTML-comment so it survives the markdown miner (which
treats lines as text) without polluting drawer content noticeably; the
source-resolver in claude_cli's _build_system_prompt block can grep for
`brain-source:` to map drawer source_files back to original PDFs.

Failures in conversion are isolated per-file: one broken PDF doesn't break
the whole pass.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Iterable

from engine.context import get_request_context

EXTRACT_SUBDIR = ".brain-extracted"

SUPPORTED_EXTS = {".pdf", ".docx", ".pptx", ".xlsx", ".xls", ".csv", ".tsv",
                  ".eml", ".msg", ".epub", ".zip"}

# Ad-hoc cache for files outside any project input folder (chat attachments,
# arbitrary paths the agent reads via read_document). Each entry is a
# companion .md keyed by (abs_path, mtime, size) so re-uploads with the
# same temp filename get a fresh extraction automatically. 30-day LRU
# eviction keyed on atime — see evict_adhoc_cache().
ADHOC_CACHE_DIR = os.path.expanduser("~/.brain-agent/extracted-cache")
ADHOC_CACHE_TTL_SECS = 30 * 24 * 3600

# Per-sheet row policy for xlsx extraction. Bank operations rely on
# medium-to-large lookup tables (retention schedules, role matrices,
# tariff lists) where the rows ARE the policy — a small preview throws
# away exactly the information we want indexed. So we extract every
# row, with two safety guards:
#   * XLSX_WARN_ROWS_PER_SHEET — soft warn in daemon log if exceeded
#     (helps spot accidental CSV-export-as-xlsx file drops).
#   * XLSX_MAX_ROWS_PER_SHEET  — hard cap at extraction time. Anything
#     this large is a database export, not a policy artifact, and the
#     drawer storage would be useless anyway (each row mostly numeric).
XLSX_WARN_ROWS_PER_SHEET = 5_000
XLSX_MAX_ROWS_PER_SHEET = 100_000
# Hard cap per cell so a single megablob doesn't blow up a row.
XLSX_CELL_MAX_CHARS = 200

# Files we should never try to convert even if their extension matches —
# guards against name collisions if a user has e.g. a docx named "file.docx"
# but it's actually corrupt.
SKIP_NAMES = {".DS_Store"}


@dataclass
class ConvertResult:
    converted: int = 0
    skipped_unchanged: int = 0
    skipped_unreadable: int = 0
    failed: int = 0
    stale_removed: int = 0
    seen_total: int = 0
    failures: list[str] = field(default_factory=list)
    elapsed_s: float = 0.0


def _stat_key(path: str) -> tuple[int, int]:
    """Cheap change detector: (mtime_ns, size). Re-conversion only happens
    when one of these changes."""
    try:
        st = os.stat(path)
        return int(st.st_mtime), int(st.st_size)
    except OSError:
        return 0, 0


def _read_md_source_stat(md_path: str) -> tuple[int, int]:
    """Pull the brain-source-mtime / brain-source-size from a converted
    file's frontmatter. Returns (0, 0) if absent."""
    try:
        with open(md_path, "r", encoding="utf-8", errors="replace") as f:
            head = f.read(2048)
    except OSError:
        return 0, 0
    mt = sz = 0
    for line in head.splitlines():
        line = line.strip()
        if line.startswith("<!-- brain-source-mtime:"):
            try:
                mt = int(line.split(":", 1)[1].rstrip(" -->").strip())
            except ValueError:
                pass
        elif line.startswith("<!-- brain-source-size:"):
            try:
                sz = int(line.split(":", 1)[1].rstrip(" -->").strip())
            except ValueError:
                pass
        if mt and sz:
            break
    return mt, sz


def _md_path_for(root: str, source_path: str) -> str:
    """Compute the converted-md path under <root>/.brain-extracted/.
    The relative layout under .brain-extracted/ mirrors the source's
    relative layout under root, with .md appended to the original name
    (so collisions across extensions like file.pdf vs file.docx don't
    overwrite each other).
    """
    rel = os.path.relpath(source_path, root)
    base = os.path.join(root, EXTRACT_SUBDIR, rel) + ".md"
    return base


def _source_for_md(root: str, md_path: str) -> str:
    """Inverse of _md_path_for — used only by sweep_stale to detect orphans."""
    rel = os.path.relpath(md_path, os.path.join(root, EXTRACT_SUBDIR))
    if rel.endswith(".md"):
        rel = rel[:-3]
    return os.path.join(root, rel)


def _frontmatter(source_path: str, mtime: int, size: int,
                 backend: str = "") -> str:
    parts = [
        f"<!-- brain-source: {source_path} -->\n",
        f"<!-- brain-source-mtime: {mtime} -->\n",
        f"<!-- brain-source-size: {size} -->\n",
    ]
    if backend:
        parts.append(f"<!-- brain-converter: {backend} -->\n")
    parts.append("\n")
    return "".join(parts)


# ── Markitdown wrapper (preferred backend when available) ────────────────────
#
# Microsoft markitdown produces materially better markdown for LLM consumption
# than fitz/python-docx/python-pptx — preserves table structure, heading
# hierarchy, OCR fallback, and bullet semantics. Wired via subprocess (the
# CLI is at /opt/homebrew/bin/markitdown installed under its own python; we
# avoid importing the package because its python and ours may not match).
#
# Used as the preferred backend for .pdf/.docx/.pptx/.xlsx; falls through
# to per-format extractors on:
#   - subprocess error / non-zero exit
#   - empty stdout (e.g. binary too damaged for markitdown to handle)
#   - markitdown not on PATH (degraded but functional)
#
# Toggleable via config.json -> conversion.use_markitdown (default true when
# detected on PATH).
import shutil
import subprocess

_MARKITDOWN_BIN = shutil.which("markitdown")
_MARKITDOWN_TIMEOUT_SECS = 120
_MARKITDOWN_EXTS = {".pdf", ".docx", ".pptx", ".xlsx", ".msg", ".epub", ".zip"}

# ── OCR fallback for scanned PDFs ────────────────────────────────────────────
#
# Markitdown + fitz both produce empty output on image-only PDFs (no text
# layer). Without OCR these become unreadable to the agent — the companion
# .md gets a 1-line "no extractable text" marker, MemPalace mines nothing,
# the agent has no content to cite.
#
# Wired to Mistral OCR (`mistral-ocr-latest`): purpose-built model, ~1 USD
# per 1000 pages, ~2-3s per page, returns structured markdown with tables
# preserved. Routed via the configured Mistral provider — same auth path
# as any other Mistral call, so PII routing applies normally.
#
# Triggers ONLY on PDFs that come back nearly empty from markitdown+fitz
# (per-page chars below OCR_TRIGGER_CHARS_PER_PAGE). Per-cycle page cap
# guards against runaway costs from someone pointing at a 10k-page archive.
_OCR_TRIGGER_CHARS_PER_PAGE = 50
_OCR_TIMEOUT_SECS = 300

# Per-cycle counter (bumped by _extract_with_ocr, reset by callers that
# want to rate-limit). Module-global so daemon cycles can read+reset.
_ocr_pages_this_cycle = 0


def _ocr_config() -> dict:
    """Read the `ocr` block from config.json. Returns dict with sensible
    defaults if the section is missing entirely."""
    try:
        cfg_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config.json")
        with open(cfg_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        ocr = cfg.get("ocr") or {}
    except (OSError, ValueError):
        ocr = {}
    # Defaults.
    return {
        "engine": ocr.get("engine", "mistral_ocr"),  # mistral_ocr | local_vision | auto | none
        "provider": ocr.get("provider", "mistral-experimental"),
        "model": ocr.get("model", "mistral-ocr-latest"),
        "max_pages_per_cycle": int(ocr.get("max_pages_per_cycle", 1000)),
        "trigger_chars_per_page": int(ocr.get("trigger_chars_per_page", _OCR_TRIGGER_CHARS_PER_PAGE)),
        # USD per page for billing — Mistral OCR is $1 per 1000 pages today.
        "cost_per_page_usd": float(ocr.get("cost_per_page_usd", 0.001)),
        # Local-vision fallback: render PDF page → image → vision LLM with
        # OCR prompt. Used when engine='local_vision' or when
        # engine='auto' + GDPR/PII gate forces local.
        "local_vision_model": ocr.get("local_vision_model", ""),
        "local_vision_render_dpi": int(ocr.get("local_vision_render_dpi", 200)),
        "local_vision_max_tokens": int(ocr.get("local_vision_max_tokens", 4096)),
    }


def reset_ocr_cycle_counter() -> int:
    """Return + reset pages-OCR'd-this-cycle. Daemon hooks call at cycle
    start to enforce per-cycle caps."""
    global _ocr_pages_this_cycle
    n = _ocr_pages_this_cycle
    _ocr_pages_this_cycle = 0
    return n


def _extract_with_mistral_ocr(path: str) -> tuple[str, str | None, int]:
    """OCR a PDF via Mistral's /v1/ocr endpoint. Returns (markdown, error,
    pages_processed). On any failure returns ("", "<reason>", 0) so the
    caller can fall through to the empty-marker path.

    Reads provider config from config.json -> providers[<provider>] (api_key
    + base_url). Per-cycle page cap honored via _ocr_pages_this_cycle.
    """
    global _ocr_pages_this_cycle
    cfg = _ocr_config()
    if cfg["engine"] != "mistral_ocr":
        return "", "ocr disabled", 0
    if _ocr_pages_this_cycle >= cfg["max_pages_per_cycle"]:
        return "", f"per-cycle cap {cfg['max_pages_per_cycle']} reached", 0

    # Provider lookup.
    try:
        cfg_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config.json")
        with open(cfg_path, "r", encoding="utf-8") as f:
            full = json.load(f)
        prov = (full.get("providers") or {}).get(cfg["provider"]) or {}
        api_key = prov.get("api_key", "")
        base_url = prov.get("base_url", "https://api.mistral.ai/v1")
    except (OSError, ValueError) as e:
        return "", f"provider config read failed: {e}", 0
    if not api_key:
        return "", f"provider {cfg['provider']} has no api_key", 0

    import base64
    import urllib.request
    import urllib.error
    try:
        with open(path, "rb") as f:
            pdf_b64 = base64.b64encode(f.read()).decode("ascii")
    except OSError as e:
        return "", f"read failed: {e}", 0

    payload = {
        "model": cfg["model"],
        "document": {
            "type": "document_url",
            "document_url": f"data:application/pdf;base64,{pdf_b64}",
        },
        "include_image_base64": False,
    }
    req = urllib.request.Request(
        base_url.rstrip("/") + "/ocr",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=_OCR_TIMEOUT_SECS) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        msg = e.read().decode("utf-8", errors="replace")[:200]
        return "", f"ocr http {e.code}: {msg}", 0
    except (urllib.error.URLError, OSError, ValueError) as e:
        return "", f"ocr request failed: {type(e).__name__}: {e}", 0

    pages = body.get("pages") or []
    parts = []
    for p in pages:
        idx = p.get("index", 0)
        md = (p.get("markdown") or "").strip()
        if md:
            parts.append(f"## Page {idx + 1}\n\n{md}")
    text = "\n\n".join(parts)
    pages_processed = int((body.get("usage_info") or {}).get("pages_processed") or len(pages))
    _ocr_pages_this_cycle += pages_processed

    # Cost row to costs.db. Fail-soft: OCR shouldn't error out because
    # billing logging is unavailable.
    try:
        _log_ocr_cost(
            model=cfg["model"],
            provider=cfg["provider"],
            pages=pages_processed,
            cost_usd=pages_processed * cfg["cost_per_page_usd"],
        )
    except Exception as e:
        print(f"[doc-convert] OCR cost-log failed: "
              f"{type(e).__name__}: {e}", flush=True)

    if not text:
        return "", "ocr empty output", pages_processed
    return text + "\n", None, pages_processed


_LOCAL_OCR_PROMPT = (
    "You are an OCR engine. Extract all text from this page image and return "
    "it as clean markdown. Preserve heading levels, list bullets, and table "
    "structure (use markdown tables with | separators). Do not add commentary, "
    "do not describe images. If the page contains a table, output it as a "
    "markdown table. If the page is empty, return an empty response."
)


def _extract_with_local_vision(path: str) -> tuple[str, str | None, int]:
    """OCR a PDF locally by rendering each page to an image and sending it
    to a vision-capable LLM (e.g. Gemma-4 via oMLX). Returns (markdown,
    error, pages_processed). On any failure returns ("", "<reason>", 0).

    Used when ocr.engine='local_vision' or when ocr.engine='auto' and the
    GDPR gate forces a local model. Slower than Mistral OCR (~10-20s/page
    on Gemma-4 26B) and lower quality on tables/multi-column layouts —
    pick this only when data must not leave the host.
    """
    global _ocr_pages_this_cycle
    cfg = _ocr_config()
    model = cfg.get("local_vision_model") or ""
    if not model:
        return "", "local_vision_model not configured", 0
    if _ocr_pages_this_cycle >= cfg["max_pages_per_cycle"]:
        return "", f"per-cycle cap {cfg['max_pages_per_cycle']} reached", 0

    try:
        import fitz  # type: ignore
    except ImportError:
        return "", "pymupdf not installed (pip install pymupdf)", 0

    # Direct provider lookup from config.json — same shape as
    # _extract_with_mistral_ocr. Avoids depending on engine.provider's
    # module-globals being initialized (the daemon thread is fine, but
    # a bare import in a one-shot context isn't).
    try:
        cfg_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config.json")
        with open(cfg_path, "r", encoding="utf-8") as f:
            full = json.load(f)
    except (OSError, ValueError) as e:
        return "", f"config read failed: {e}", 0
    model_cfg = (full.get("models") or {}).get(model) or {}
    provider_name = model_cfg.get("provider", "")
    if not provider_name:
        return "", f"model '{model}' has no provider in config", 0
    prov = (full.get("providers") or {}).get(provider_name) or {}
    api_key = prov.get("api_key", "")
    base_url = prov.get("base_url", "")
    if not base_url:
        return "", f"no base_url for provider '{provider_name}'", 0
    # Strip provider prefix if model id is "provider/model_id" — the
    # actual wire-level model name is base_model_id when present.
    wire_model = model_cfg.get("base_model_id") or model.split("/", 1)[-1]

    import base64
    import urllib.request
    import urllib.error

    try:
        doc = fitz.open(path)
    except Exception as e:
        return "", f"open failed: {type(e).__name__}: {e}", 0

    parts: list[str] = []
    pages_processed = 0
    dpi = cfg["local_vision_render_dpi"]
    max_tokens = cfg["local_vision_max_tokens"]
    zoom = dpi / 72.0
    matrix = fitz.Matrix(zoom, zoom)

    try:
        for i, page in enumerate(doc, 1):
            if _ocr_pages_this_cycle >= cfg["max_pages_per_cycle"]:
                # Don't fail the whole call — return what we have.
                parts.append(f"## Page {i}\n\n_(skipped — OCR cycle cap reached)_")
                continue
            try:
                pix = page.get_pixmap(matrix=matrix, alpha=False)
                png_bytes = pix.tobytes("png")
            except Exception as e:
                parts.append(f"## Page {i}\n\n_(render failed: {type(e).__name__}: {e})_")
                continue
            img_b64 = base64.b64encode(png_bytes).decode("ascii")
            data_uri = f"data:image/png;base64,{img_b64}"

            payload = {
                "model": wire_model,
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": _LOCAL_OCR_PROMPT},
                        {"type": "image_url", "image_url": {"url": data_uri}},
                    ],
                }],
                "max_tokens": max_tokens,
                "temperature": 0.0,
                "stream": False,
            }
            req = urllib.request.Request(
                base_url.rstrip("/") + "/chat/completions",
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
            )
            try:
                with urllib.request.urlopen(req, timeout=_OCR_TIMEOUT_SECS) as resp:
                    body = json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                err_msg = e.read().decode("utf-8", errors="replace")[:200]
                parts.append(f"## Page {i}\n\n_(OCR http {e.code}: {err_msg})_")
                continue
            except (urllib.error.URLError, OSError, ValueError) as e:
                parts.append(f"## Page {i}\n\n_(OCR request failed: {type(e).__name__}: {e})_")
                continue
            choices = body.get("choices") or []
            content = ""
            if choices:
                msg = choices[0].get("message") or {}
                content = (msg.get("content") or "").strip()
            if content:
                parts.append(f"## Page {i}\n\n{content}")
            else:
                parts.append(f"## Page {i}\n\n_(empty OCR output)_")
            pages_processed += 1
            _ocr_pages_this_cycle += 1
    finally:
        doc.close()

    text = "\n\n".join(parts).strip()
    if not text or pages_processed == 0:
        return "", "local-vision: no usable output", pages_processed

    # Local OCR is free at the cost level (own GPU), but we still log it so
    # ops can see throughput / per-cycle volume in the same dashboard.
    try:
        _log_ocr_cost(model=model, provider=provider_name,
                      pages=pages_processed, cost_usd=0.0)
    except Exception as e:
        print(f"[doc-convert] local-vision OCR cost-log failed: "
              f"{type(e).__name__}: {e}", flush=True)

    return text + "\n", None, pages_processed


def _log_ocr_cost(*, model: str, provider: str, pages: int, cost_usd: float) -> None:
    """Forward to brain's live CostTracker. Pulls agent/session/user from
    thread-locals when called from a chat thread; falls back to ('main', '', '')
    for daemon calls."""
    try:
        from brain import _cost_tracker, _current_agent
    except ImportError:
        return
    if _cost_tracker is None:
        return
    agent_id = "main"
    session_id = ""
    user_id = ""
    _ctx = get_request_context()
    if _ctx is not None:
        agent = _ctx.current_agent or _current_agent
        if agent is not None:
            agent_id = getattr(agent, "agent_id", "main")
        session_id = _ctx.current_session_id or ""
        user_id = _ctx.current_user_id or ""
    _cost_tracker.log_ocr(agent=agent_id, session_id=session_id, model=model,
                          provider=provider, pages=pages, cost_usd=cost_usd,
                          user_id=user_id)


def _extract_with_markitdown(path: str) -> tuple[str, str | None]:
    """Returns (markdown_text, error_msg or None). On non-zero exit or
    timeout returns ("", "<reason>") so the caller can fall through to the
    legacy per-format extractor."""
    if not _MARKITDOWN_BIN:
        return "", "markitdown not on PATH"
    try:
        proc = subprocess.run(
            [_MARKITDOWN_BIN, path],
            capture_output=True,
            timeout=_MARKITDOWN_TIMEOUT_SECS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return "", f"markitdown timeout after {_MARKITDOWN_TIMEOUT_SECS}s"
    except OSError as e:
        return "", f"markitdown spawn failed: {type(e).__name__}: {e}"
    if proc.returncode != 0:
        err = (proc.stderr or b"").decode("utf-8", errors="replace").strip()
        return "", f"markitdown exit {proc.returncode}: {err[:200]}"
    text = (proc.stdout or b"").decode("utf-8", errors="replace").strip()
    if not text:
        return "", "markitdown empty output"
    return text + "\n", None


# ── Per-format extractors ────────────────────────────────────────────────────

def _parse_index_selection(spec: str | None) -> set[int] | None:
    """Parse a 1-based selection string like "1,3,5-7" into a set of ints.
    None/empty → None (meaning "all", no filtering). Malformed parts are
    skipped silently rather than raising — a bad selection should degrade to
    fewer pages, never crash the read."""
    if not spec or not str(spec).strip():
        return None
    out: set[int] = set()
    for part in str(spec).split(","):
        part = part.strip()
        if not part:
            continue
        try:
            if "-" in part:
                a, b = part.split("-", 1)
                for i in range(int(a), int(b) + 1):
                    out.add(i)
            else:
                out.add(int(part))
        except ValueError:
            continue
    return out or None


def _render_pdf_tables(path: str, page_sel: set[int] | None) -> dict[int, list[str]]:
    """Per-page pdfplumber table reconstruction → {page_num: [markdown, ...]}.
    Empty dict when pdfplumber is unavailable or no tables found. Ported from
    read_document's inline include_tables path so the unified pipeline keeps
    table fidelity for table-heavy PDFs."""
    tables_by_page: dict[int, list[str]] = {}
    try:
        import pdfplumber  # type: ignore
    except ImportError:
        return tables_by_page
    try:
        with pdfplumber.open(path) as plumb:
            for i, page in enumerate(plumb.pages, 1):
                if page_sel is not None and i not in page_sel:
                    continue
                found = page.extract_tables()
                if not found:
                    continue
                rendered = []
                for t in found:
                    rows = [[(c or "").replace("|", "\\|").replace("\n", " ").strip()
                             for c in row] for row in t]
                    if not rows:
                        continue
                    width = max(len(r) for r in rows)
                    for r in rows:
                        r.extend([""] * (width - len(r)))
                    header = "| " + " | ".join(rows[0]) + " |"
                    sep = "| " + " | ".join("---" for _ in range(width)) + " |"
                    body = "\n".join("| " + " | ".join(r) + " |" for r in rows[1:])
                    rendered.append(header + "\n" + sep + ("\n" + body if body else ""))
                if rendered:
                    tables_by_page[i] = rendered
    except Exception:
        # pdfplumber is best-effort enrichment — never let it fail the read.
        return {}
    return tables_by_page


def _extract_pdf(path: str, *, pages: str | None = None,
                 include_tables: bool = False, emit_meta: bool = False,
                 page_marker: str = "## Page") -> tuple[str, str | None]:
    """Returns (markdown_text, error_msg or None). Empty text + None means
    the PDF had no extractable text (likely scanned image). Empty text +
    error means a real failure.

    Mining calls with defaults → byte-stable legacy output (`# title` +
    `## Page N` headings, no metadata, no tables). read_document/
    read_attachment pass:
      * pages          — 1-based selection "1,3,5-7" (None = all)
      * include_tables — opt-in pdfplumber per-page table reconstruction
      * emit_meta      — prepend a **title/author/page_count** metadata block
      * page_marker    — heading prefix per page ("--- Page" for the
                         read_document `--- Page N ---` format the citation/
                         page logic parses)
    """
    try:
        import fitz  # type: ignore
    except ImportError:
        return "", "pymupdf not installed (pip install pymupdf)"
    try:
        doc = fitz.open(path)
    except Exception as e:
        return "", f"open failed: {type(e).__name__}: {e}"
    page_sel = _parse_index_selection(pages)
    parts = []
    meta = doc.metadata or {}
    title = meta.get("title") or os.path.splitext(os.path.basename(path))[0]
    page_count = doc.page_count
    if emit_meta:
        meta_lines = [f"**title:** {title}"]
        if meta.get("author"):
            meta_lines.append(f"**author:** {meta.get('author')}")
        meta_lines.append(f"**page_count:** {page_count}")
        parts.append("\n".join(meta_lines) + "\n")
    else:
        parts.append(f"# {title}\n")
    # `--- Page N ---` is a flat marker on its own line; `## Page N` is a
    # markdown heading. Match the two historical formats exactly.
    flat_marker = page_marker.strip().startswith("---")
    try:
        for i, page in enumerate(doc, 1):
            if page_sel is not None and i not in page_sel:
                continue
            t = page.get_text("text") or ""
            t = t.strip()
            if t:
                if flat_marker:
                    parts.append(f"\n\n{page_marker} {i} ---\n\n{t}\n")
                else:
                    parts.append(f"\n\n{page_marker} {i}\n\n{t}\n")
    finally:
        doc.close()

    if include_tables:
        tables_by_page = _render_pdf_tables(path, page_sel)
        if tables_by_page:
            for pnum in sorted(tables_by_page):
                for j, md in enumerate(tables_by_page[pnum], 1):
                    parts.append(f"### Table (page {pnum}, #{j})\n{md}")

    text = "\n".join(parts).strip()
    if len(text.splitlines()) <= 1:
        # Only the title/meta line — likely scanned PDF or empty.
        return "", None
    return text + "\n", None


def _extract_docx(path: str) -> tuple[str, str | None]:
    try:
        import docx  # type: ignore
    except ImportError:
        return "", "python-docx not installed (pip install python-docx)"
    try:
        d = docx.Document(path)
    except Exception as e:
        return "", f"open failed: {type(e).__name__}: {e}"
    title = os.path.splitext(os.path.basename(path))[0]
    parts = [f"# {title}\n"]
    for p in d.paragraphs:
        t = (p.text or "").rstrip()
        if not t:
            parts.append("")
            continue
        # Heading levels: bold + first run + style starting with "Heading"
        style_name = (getattr(p.style, "name", "") or "").lower()
        if style_name.startswith("heading"):
            try:
                level = int(style_name.replace("heading", "").strip() or "2")
            except ValueError:
                level = 2
            level = max(2, min(6, level))
            parts.append(f"\n{'#' * level} {t}\n")
        else:
            parts.append(t)
    text = "\n".join(parts).strip()
    if len(text.splitlines()) <= 1:
        return "", None
    return text + "\n", None


def _extract_pptx(path: str, *, slides: str | None = None
                  ) -> tuple[str, str | None]:
    """`slides` (default None = all slides, mining behavior) is a 1-based
    selection string like "1,3,5-7" — read_document/read_attachment `slides=`
    selection. Slides outside the set are skipped."""
    try:
        from pptx import Presentation  # type: ignore
    except ImportError:
        return "", "python-pptx not installed (pip install python-pptx)"
    try:
        prs = Presentation(path)
    except Exception as e:
        return "", f"open failed: {type(e).__name__}: {e}"
    slide_sel = _parse_index_selection(slides)
    title = os.path.splitext(os.path.basename(path))[0]
    parts = [f"# {title}\n"]
    slide_count = 0
    for i, slide in enumerate(prs.slides, 1):
        if slide_sel is not None and i not in slide_sel:
            continue
        slide_count += 1
        # Try to surface the slide title from its title placeholder when
        # present; fall back to "Slide N".
        slide_title = ""
        try:
            if slide.shapes.title and slide.shapes.title.has_text_frame:
                slide_title = (slide.shapes.title.text_frame.text or "").strip()
        except Exception:
            slide_title = ""
        heading = f"## Slide {i}" + (f" — {slide_title}" if slide_title else "")
        parts.append(f"\n{heading}\n")
        for shape in slide.shapes:
            # Skip the title shape we already used.
            if shape == slide.shapes.title:
                continue
            try:
                if not shape.has_text_frame:
                    continue
            except Exception:
                continue
            for para in shape.text_frame.paragraphs:
                t = (para.text or "").rstrip()
                if not t:
                    continue
                # Indent bullets a bit so list structure carries through.
                level = getattr(para, "level", 0) or 0
                bullet = "  " * level + "- " if level >= 0 else ""
                parts.append(f"{bullet}{t}")
        # Speaker notes — often hold the actual policy/explanation text.
        try:
            if slide.has_notes_slide:
                notes = (slide.notes_slide.notes_text_frame.text or "").strip()
                if notes:
                    parts.append(f"\n_Speaker notes:_ {notes}")
        except Exception:
            pass
    text = "\n".join(parts).strip()
    # Title + 0 slides → empty.
    if slide_count == 0 or len(text.splitlines()) <= 1:
        return "", None
    return text + "\n", None


def _extract_xlsx(path: str, *, caps: bool = True,
                  sheet: str | None = None) -> tuple[str, str | None]:
    """Render every sheet's header + first N rows as a markdown table.

    Spreadsheets are mostly numeric/tabular and don't fit the drawer model
    cleanly. We extract a structured preview per sheet — enough for the
    embedding to learn the topic and the KG extractor to see e.g. retention
    schedules, role matrices, threshold tables — and stop. Users who need
    full-fidelity row-by-row analysis should call read_document on the
    original .xlsx rather than relying on the converted markdown.

    `caps` (default True, mining behavior) applies the per-sheet row cap and
    per-cell char cap. read_document/read_attachment pass caps=False for
    full-fidelity row-by-row output. `sheet` restricts output to a single
    named sheet (read_document/read_attachment `sheet=` selection); unknown
    or None name = all sheets. Pipe-escaping, blank-row skipping, and
    per-sheet error isolation apply regardless of caps.
    """
    try:
        import openpyxl  # type: ignore
    except ImportError:
        return "", "openpyxl not installed (pip install openpyxl)"
    try:
        # data_only=True so cached formula values come through (the user
        # sees the displayed value, not the formula expression).
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    except Exception as e:
        return "", f"open failed: {type(e).__name__}: {e}"
    title = os.path.splitext(os.path.basename(path))[0]
    parts = [f"# {title}\n"]
    cell_cap = XLSX_CELL_MAX_CHARS if caps else None
    row_cap = XLSX_MAX_ROWS_PER_SHEET if caps else None

    def _cell(v):
        if v is None:
            return ""
        s = str(v)
        if cell_cap is not None and len(s) > cell_cap:
            s = s[:cell_cap] + "…"
        # Markdown table separator + escape pipes inside cells.
        return s.replace("|", "\\|").replace("\n", " ").strip()

    if sheet and sheet in wb.sheetnames:
        target_sheets = [wb[sheet]]
    else:
        target_sheets = wb.worksheets

    sheet_count = 0
    for sheet in target_sheets:
        try:
            rows = sheet.iter_rows(values_only=True)
            header = next(rows, None)
            if header is None:
                continue
            sheet_count += 1
            parts.append(f"\n## Sheet: {sheet.title}\n")
            cells = [_cell(c) for c in header]
            # Drop trailing empty columns so the table doesn't have ragged
            # padding from sparsely-used right side of the sheet.
            while cells and cells[-1] == "":
                cells.pop()
            if not cells:
                # Empty header row, skip; sheet may be data-without-header.
                cells = ["col_1"]
            n_cols = len(cells)
            parts.append("| " + " | ".join(cells) + " |")
            parts.append("|" + "|".join(["---"] * n_cols) + "|")
            shown = 0
            warned = False
            for r in rows:
                if row_cap is not None and shown >= row_cap:
                    parts.append(
                        f"\n_(truncated at {row_cap:,} "
                        f"rows — use read_document on the original .xlsx "
                        f"for the rest)_\n")
                    break
                rc = [_cell(c) for c in r[:n_cols]]
                # Pad short rows to keep table valid.
                while len(rc) < n_cols:
                    rc.append("")
                if all(c == "" for c in rc):
                    continue  # skip blank rows
                parts.append("| " + " | ".join(rc) + " |")
                shown += 1
                if not warned and shown == XLSX_WARN_ROWS_PER_SHEET:
                    print(
                        f"[doc-convert] {path}: sheet `{sheet.title}` "
                        f"has >{XLSX_WARN_ROWS_PER_SHEET:,} rows — "
                        f"continuing extraction up to "
                        f"{XLSX_MAX_ROWS_PER_SHEET:,}",
                        flush=True)
                    warned = True
        except Exception as e:
            parts.append(f"\n_(failed to read sheet `{sheet.title}`: "
                         f"{type(e).__name__})_\n")
    try:
        wb.close()
    except Exception:
        pass
    if sheet_count == 0:
        return "", None
    return "\n".join(parts) + "\n", None


def _extract_eml(path: str) -> tuple[str, str | None]:
    """Parse RFC 822 / MIME messages. Stdlib `email` only — no extras."""
    import email
    from email import policy as _policy
    try:
        with open(path, "rb") as f:
            msg = email.message_from_binary_file(f, policy=_policy.default)
    except Exception as e:
        return "", f"open failed: {type(e).__name__}: {e}"
    return _email_to_markdown(msg), None


def _extract_msg(path: str) -> tuple[str, str | None]:
    """Outlook .msg files. Requires `extract-msg`; if absent, returns a
    helpful error rather than silently skipping."""
    try:
        import extract_msg  # type: ignore
    except ImportError:
        return "", "extract-msg not installed (pip install extract-msg)"
    try:
        m = extract_msg.Message(path)
    except Exception as e:
        return "", f"open failed: {type(e).__name__}: {e}"
    title = m.subject or os.path.splitext(os.path.basename(path))[0]
    parts = [f"# {title}\n"]
    if m.sender: parts.append(f"**From:** {m.sender}")
    if m.to: parts.append(f"**To:** {m.to}")
    if m.cc: parts.append(f"**Cc:** {m.cc}")
    if m.date: parts.append(f"**Date:** {m.date}")
    if m.subject: parts.append(f"**Subject:** {m.subject}")
    parts.append("")
    body = (m.body or "").strip()
    if body:
        parts.append(body)
    try:
        m.close()
    except Exception:
        pass
    text = "\n".join(parts).strip()
    if len(text.splitlines()) <= 1:
        return "", None
    return text + "\n", None


def _extract_csv(path: str, *, caps: bool = True) -> tuple[str, str | None]:
    """CSV/TSV → one markdown table. Pipe-escaping + newline-strip mirror
    _extract_xlsx so a cell containing `|` or a newline can't corrupt the
    table. `caps` (default True, mining behavior) bounds rows/cells; read
    paths pass caps=False for full fidelity."""
    import csv as _csv
    delimiter = "\t" if path.lower().endswith(".tsv") else ","
    cell_cap = XLSX_CELL_MAX_CHARS if caps else None
    row_cap = XLSX_MAX_ROWS_PER_SHEET if caps else None
    try:
        with open(path, "r", newline="", errors="replace") as f:
            rows = list(_csv.reader(f, delimiter=delimiter))
    except Exception as e:
        return "", f"open failed: {type(e).__name__}: {e}"
    if not rows:
        return "", None

    def _cell(v: str) -> str:
        s = "" if v is None else str(v)
        if cell_cap is not None and len(s) > cell_cap:
            s = s[:cell_cap] + "…"
        return s.replace("|", "\\|").replace("\n", " ").strip()

    n_cols = max(len(r) for r in rows)
    header = [_cell(c) for c in rows[0]]
    while len(header) < n_cols:
        header.append("")
    parts = ["| " + " | ".join(header) + " |",
             "|" + "|".join(["---"] * n_cols) + "|"]
    for r in rows[1:]:
        if row_cap is not None and len(parts) - 2 >= row_cap:
            parts.append(f"\n_(truncated at {row_cap:,} rows)_\n")
            break
        rc = [_cell(c) for c in r[:n_cols]]
        while len(rc) < n_cols:
            rc.append("")
        parts.append("| " + " | ".join(rc) + " |")
    return "\n".join(parts) + "\n", None


def _extract_markitdown_only(path: str) -> tuple[str, str | None]:
    """Fallback for formats that have no native extractor (.epub, .zip).
    By the time _do_extract calls this, markitdown has already been tried
    (these exts are in _MARKITDOWN_EXTS), so reaching here means markitdown
    was absent or failed — return a helpful error, not silent empty."""
    ext = os.path.splitext(path)[1].lower()
    if not _MARKITDOWN_BIN:
        return "", (f"Reading {ext} requires the markitdown package "
                    "(pip3 install 'markitdown[outlook]').")
    return "", f"markitdown could not extract {ext}"


def _email_to_markdown(msg) -> str:
    """Shared rendering for both .eml (parsed via stdlib email) and any
    other future `email.message.Message` source."""
    title = msg.get("Subject") or "(no subject)"
    parts = [f"# {title}\n"]
    for hdr in ("From", "To", "Cc", "Date", "Subject"):
        v = msg.get(hdr)
        if v:
            parts.append(f"**{hdr}:** {v}")
    parts.append("")
    body = ""
    try:
        # Prefer the plain text body part.
        if msg.is_multipart():
            for part in msg.walk():
                ctype = part.get_content_type() or ""
                disp = (part.get("Content-Disposition") or "").lower()
                if "attachment" in disp:
                    continue
                if ctype == "text/plain":
                    body = part.get_content()
                    break
            if not body:
                # Fall back to html → strip tags lightly.
                for part in msg.walk():
                    if part.get_content_type() == "text/html":
                        import re
                        html = part.get_content()
                        body = re.sub(r"<[^>]+>", "", html or "")
                        break
        else:
            body = msg.get_content() or ""
    except Exception:
        body = ""
    body = (body or "").strip()
    if body:
        parts.append(body)
    return "\n".join(parts) + "\n"


_EXTRACTORS = {
    ".pdf": _extract_pdf,
    ".docx": _extract_docx,
    ".pptx": _extract_pptx,
    ".xlsx": _extract_xlsx,
    ".xls": _extract_xlsx,   # legacy alias; true .xls isn't openpyxl-readable
    ".csv": _extract_csv,
    ".tsv": _extract_csv,
    ".eml": _extract_eml,
    ".msg": _extract_msg,
    ".epub": _extract_markitdown_only,
    ".zip": _extract_markitdown_only,
}


def _iter_source_files(root: str) -> Iterable[str]:
    """Yield every supported source path in `root` (recursive). Skips the
    .brain-extracted subdir itself."""
    skip_prefix = os.path.join(root, EXTRACT_SUBDIR)
    for dirpath, _dirs, files in os.walk(root):
        if dirpath == skip_prefix or dirpath.startswith(skip_prefix + os.sep):
            continue
        for fn in files:
            if fn in SKIP_NAMES:
                continue
            ext = os.path.splitext(fn)[1].lower()
            if ext in SUPPORTED_EXTS:
                yield os.path.join(dirpath, fn)


# ── Single-file conversion (used by read_document + convert_folder) ──────────

def _adhoc_cache_path(src: str, mtime: int, size: int) -> str:
    """Companion-md path under ADHOC_CACHE_DIR for a file outside any
    project input folder. Key includes (mtime, size) so re-uploads at the
    same temp path get a fresh extraction automatically."""
    import hashlib
    key = f"{os.path.abspath(src)}:{mtime}:{size}".encode("utf-8")
    return os.path.join(ADHOC_CACHE_DIR, hashlib.sha256(key).hexdigest() + ".md")


def _do_extract(src: str, *, use_markitdown: bool = True,
                caps: bool = True, sheet: str | None = None,
                slides: str | None = None, pages: str | None = None,
                include_tables: bool = False, emit_meta: bool = False,
                page_marker: str = "## Page"
                ) -> tuple[str, str, str | None]:
    """Run markitdown (when enabled+supported) then per-format fallback,
    then OCR if both came back empty for PDFs.
    Returns (text, backend, error). Empty text + None error = no extractable
    content even after OCR — caller writes a marker file.

    Mining calls with all defaults (caps=True, no selection, no meta) → the
    fallback extractors produce byte-stable legacy output. read_document /
    read_attachment pass caps=False + the relevant selection/meta knobs.
    These knobs ONLY affect the per-format fallback — when markitdown
    succeeds (the common case for clean office files) its output is returned
    verbatim and the knobs are inert."""
    ext = os.path.splitext(src)[1].lower()
    extractor = _EXTRACTORS.get(ext)
    if extractor is None:
        return "", "", f"unsupported extension: {ext}"
    md_enabled = bool(use_markitdown and _MARKITDOWN_BIN)
    text = ""
    if md_enabled and ext in _MARKITDOWN_EXTS:
        text, err = _extract_with_markitdown(src)
        if not err and text:
            return text, "markitdown", None
    # Per-format fallback. Pass only the knobs each extractor accepts so a
    # default mining call stays byte-stable.
    extractor_kwargs: dict = {}
    if ext in (".xlsx", ".xls"):
        extractor_kwargs = {"caps": caps, "sheet": sheet}
    elif ext in (".csv", ".tsv"):
        extractor_kwargs = {"caps": caps}
    elif ext == ".pptx":
        extractor_kwargs = {"slides": slides}
    elif ext == ".pdf":
        extractor_kwargs = {"pages": pages, "include_tables": include_tables,
                            "emit_meta": emit_meta, "page_marker": page_marker}
    try:
        text, err = extractor(src, **extractor_kwargs)
    except Exception as e:
        return "", "", f"{type(e).__name__}: {e}"
    if err:
        # Hard error from extractor (file corrupt, missing dep). Don't try
        # OCR — OCR is for image-only PDFs, not broken files.
        return "", "", err
    if text:
        return text, "fitz/legacy", None

    # Empty output from both markitdown and per-format extractor. For PDFs,
    # try OCR — this is the scanned-image-only case where markitdown sees
    # rendered glyphs as pictures, fitz finds no text layer, but the page
    # is full of readable text to a vision model.
    if ext == ".pdf":
        ocr_cfg = _ocr_config()
        engine = ocr_cfg["engine"]
        # Routing matrix:
        #   mistral_ocr   → cloud only, fail → empty marker
        #   local_vision  → local only, fail → empty marker
        #   auto          → cloud first, on failure (timeout / config /
        #                   PII block) → local fallback
        ocr_text = ""
        ocr_err = ""
        pages = 0
        backend = ""
        if engine in ("mistral_ocr", "auto"):
            ocr_text, ocr_err, pages = _extract_with_mistral_ocr(src)
            if ocr_text:
                backend = f"mistral-ocr ({pages}p)"
        if not ocr_text and engine in ("local_vision", "auto"):
            local_text, local_err, local_pages = _extract_with_local_vision(src)
            if local_text:
                ocr_text = local_text
                pages = local_pages
                backend = f"local-vision ({pages}p)"
            else:
                ocr_err = ocr_err or local_err or "no ocr engine produced output"
        if ocr_text:
            return ocr_text, backend, None
        if engine != "none" and ocr_err:
            print(f"[doc-convert] OCR fallback failed for {src}: {ocr_err}",
                  flush=True)
    return "", "fitz/legacy", None


def convert_one(src: str, *, project_root: str | None = None,
                use_markitdown: bool = True) -> tuple[str | None, str | None]:
    """Convert a single binary document to a companion .md, idempotently.

    Returns (md_path, error). md_path is None on hard failure; error is None
    on success.

    Companion location:
      - If `project_root` is given AND `src` is inside it → standard
        `<project_root>/.brain-extracted/<rel>.md` (matches the project-sync
        daemon layout — same byte-for-byte output for the same source).
      - Otherwise → ad-hoc cache under ADHOC_CACHE_DIR keyed by
        (abs_path, mtime, size).

    Idempotent: if the companion exists and its frontmatter (mtime, size)
    matches the source, returns immediately without re-extracting.

    Mirrors the convert_folder() logic for one file so behavior is identical
    between bulk pre-mining and on-demand reads.
    """
    if not src or not os.path.isfile(src):
        return None, f"source not a file: {src}"
    src = os.path.abspath(src)
    cur_mt, cur_sz = _stat_key(src)
    if cur_mt == 0 and cur_sz == 0:
        return None, "stat failed"

    # Pick companion location. Project files reuse the daemon's layout so
    # one PDF never has two companions.
    if project_root and os.path.commonpath([project_root, src]) == os.path.abspath(project_root):
        md_path = _md_path_for(os.path.abspath(project_root), src)
    else:
        md_path = _adhoc_cache_path(src, cur_mt, cur_sz)

    # Fast path: companion exists with matching (mtime, size).
    if os.path.isfile(md_path):
        old_mt, old_sz = _read_md_source_stat(md_path)
        if old_mt == cur_mt and old_sz == cur_sz:
            # Touch atime so LRU eviction sees recent use.
            try:
                os.utime(md_path, None)
            except OSError:
                pass
            return md_path, None

    try:
        os.makedirs(os.path.dirname(md_path), exist_ok=True)
    except OSError as e:
        return None, f"mkdir failed: {e}"

    text, backend, err = _do_extract(src, use_markitdown=use_markitdown)
    if err:
        return None, err
    if not text:
        # Scanned PDF / empty doc — write a marker so we don't retry every
        # call. Same behavior as convert_folder.
        text = (
            "# " + os.path.splitext(os.path.basename(src))[0] + "\n\n"
            "_(no extractable text — possibly a scanned image; OCR not run)_\n"
        )
        backend = backend or "empty"

    body = _frontmatter(src, cur_mt, cur_sz, backend=backend) + text
    try:
        tmp_path = md_path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(body)
        os.replace(tmp_path, md_path)
    except OSError as e:
        return None, f"write failed: {e}"
    return md_path, None


def evict_adhoc_cache(*, ttl_secs: int = ADHOC_CACHE_TTL_SECS,
                      log_prefix: str = "[doc-convert]") -> int:
    """Drop ad-hoc cache entries older than `ttl_secs` (atime-based).
    Project companions under `.brain-extracted/` are never touched here.
    Returns count removed. Safe to call from any periodic daemon."""
    if not os.path.isdir(ADHOC_CACHE_DIR):
        return 0
    cutoff = time.time() - ttl_secs
    removed = 0
    for fn in os.listdir(ADHOC_CACHE_DIR):
        if not fn.endswith(".md"):
            continue
        p = os.path.join(ADHOC_CACHE_DIR, fn)
        try:
            st = os.stat(p)
        except OSError:
            continue
        if st.st_atime < cutoff:
            try:
                os.remove(p)
                removed += 1
            except OSError:
                pass
    if removed:
        print(f"{log_prefix} adhoc-cache evicted={removed}", flush=True)
    return removed


# ── Public API ───────────────────────────────────────────────────────────────

def convert_folder(root: str, *, log_prefix: str = "[doc-convert]",
                   use_markitdown: bool = True) -> ConvertResult:
    """Walk `root` for supported binary docs, write `.md` siblings under
    `<root>/.brain-extracted/<rel>.md`. Idempotent via (mtime, size) hash.
    Returns a result summary; does not raise on per-file failures.

    When `use_markitdown` is true and the markitdown CLI is on PATH, prefer
    it for .pdf/.docx/.pptx/.xlsx (better table + heading fidelity for LLM
    retrieval). Falls through to the per-format extractor on any failure.
    """
    res = ConvertResult()
    if not root or not os.path.isdir(root):
        return res
    t0 = time.time()
    md_enabled = bool(use_markitdown and _MARKITDOWN_BIN)
    md_used = 0
    md_fallback = 0
    extract_root = os.path.join(root, EXTRACT_SUBDIR)
    for src in _iter_source_files(root):
        res.seen_total += 1
        ext = os.path.splitext(src)[1].lower()
        extractor = _EXTRACTORS.get(ext)
        if extractor is None:
            continue
        cur_mt, cur_sz = _stat_key(src)
        if cur_mt == 0 and cur_sz == 0:
            res.skipped_unreadable += 1
            continue
        md_path = _md_path_for(root, src)
        if os.path.isfile(md_path):
            old_mt, old_sz = _read_md_source_stat(md_path)
            if old_mt == cur_mt and old_sz == cur_sz:
                res.skipped_unchanged += 1
                continue
        # (Re)convert.
        try:
            os.makedirs(os.path.dirname(md_path), exist_ok=True)
        except OSError as e:
            res.failed += 1
            res.failures.append(f"{src}: mkdir failed {e}")
            continue
        text = ""
        err: str | None = None
        backend = ""
        if md_enabled and ext in _MARKITDOWN_EXTS:
            text, err = _extract_with_markitdown(src)
            if err or not text:
                md_fallback += 1
                text = ""
                err = None
            else:
                backend = "markitdown"
                md_used += 1
        if not text:
            try:
                text, err = extractor(src)
            except Exception as e:
                res.failed += 1
                res.failures.append(f"{src}: {type(e).__name__}: {e}")
                continue
            if err:
                res.failed += 1
                res.failures.append(f"{src}: {err}")
                continue
            if text:
                backend = backend or "fitz/legacy"
        if not text:
            # Empty after extraction — e.g. scanned PDF. Write a marker file
            # so we don't retry every cycle, but keep it small enough that the
            # miner doesn't flood drawer storage.
            text = (
                "# " + os.path.splitext(os.path.basename(src))[0] + "\n\n"
                "_(no extractable text — possibly a scanned image; OCR not run)_\n"
            )
            backend = backend or "empty"
        body = _frontmatter(src, cur_mt, cur_sz, backend=backend) + text
        try:
            tmp_path = md_path + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                f.write(body)
            os.replace(tmp_path, md_path)
            res.converted += 1
        except OSError as e:
            res.failed += 1
            res.failures.append(f"{src}: write failed {e}")
            continue

    res.elapsed_s = time.time() - t0
    if res.converted or res.failed:
        md_note = ""
        if md_enabled:
            md_note = f" markitdown={md_used} fallback={md_fallback}"
        elif use_markitdown and not _MARKITDOWN_BIN:
            md_note = " markitdown=unavailable"
        print(
            f"{log_prefix} {root}: converted={res.converted} "
            f"unchanged={res.skipped_unchanged} failed={res.failed} "
            f"seen={res.seen_total} elapsed={res.elapsed_s:.1f}s{md_note}",
            flush=True)
    return res


def sweep_stale(root: str, *, log_prefix: str = "[doc-convert]") -> int:
    """Walk `<root>/.brain-extracted/` and drop any .md whose source path
    no longer exists. Returns count removed.
    """
    extract_root = os.path.join(root, EXTRACT_SUBDIR)
    if not os.path.isdir(extract_root):
        return 0
    removed = 0
    for dirpath, _dirs, files in os.walk(extract_root):
        for fn in files:
            if not fn.endswith(".md"):
                continue
            md_path = os.path.join(dirpath, fn)
            src = _source_for_md(root, md_path)
            if not os.path.isfile(src):
                try:
                    os.remove(md_path)
                    removed += 1
                except OSError:
                    pass
    if removed:
        print(f"{log_prefix} {root}: stale_removed={removed}", flush=True)
    return removed


def resolve_original_source(md_path: str) -> str | None:
    """Given a converted .md path, read its frontmatter and return the
    original source path. None if no marker present."""
    try:
        with open(md_path, "r", encoding="utf-8", errors="replace") as f:
            head = f.read(1024)
    except OSError:
        return None
    for line in head.splitlines():
        line = line.strip()
        if line.startswith("<!-- brain-source:"):
            return line.split(":", 1)[1].rstrip(" -->").strip() or None
    return None
