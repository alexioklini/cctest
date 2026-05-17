"""Document translation — docx / pptx / pdf pipelines.

Single source of truth for document translation. All entry points (the
`translate_document` tool, the HTTP `/v1/translate/document` endpoint, future
workflow nodes / scheduled tasks) hit `translate_document_file` here.

Design:
- DOCX / PPTX: open as zip, walk the XML, collect every `<w:t>` / `<a:t>` run
  in document order, translate in chunks, write the translations back into
  the same elements, re-zip. Layout, fonts, tables, images stay untouched.
- PDF: best-effort via markitdown — no library round-trips PDF without
  destroying layout. We extract markdown, translate, emit a `.docx` of the
  same name. Caller (and UI) needs to surface that PDFs become DOCX output.
- Chunked translation: ~50 runs per chunk via a single `_run_delegate` call
  with a numbered-list system prompt. Glossary applied per chunk so terms
  stay consistent across chunks. Empty / whitespace runs are skipped.

Why not stream every run individually? Per-run calls would multiply latency
by 100-1000× on a normal contract and lose cross-run coherence (e.g. pronoun
references). Why not one giant call? Bigger than ~4-8K input tokens hits
context / cost / failure-recovery problems. 50 runs/chunk is the balance.
"""
from __future__ import annotations

import io
import json
import os
import re
import shutil
import zipfile
from typing import Callable
from xml.etree import ElementTree as ET

from .detect import LANG_NAMES, detect_language
from .glossary import load_glossary, glossary_to_system_block

# Namespaces — declared on the documents but ElementTree's parse drops the
# prefix mapping. We restore them when re-writing so Word/PowerPoint don't
# choke on `ns0:` aliases.
DOCX_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
PPTX_A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"

# Tags we rewrite inside each format's main XML files.
DOCX_TEXT_TAG = f"{{{DOCX_NS}}}t"
PPTX_TEXT_TAG = f"{{{PPTX_A_NS}}}t"

# Chunk size = number of text runs sent to the LLM in one delegate call.
CHUNK_RUNS = 50

# Cap on chars per run we accept — runaway pasted blob in a single <w:t>
# would blow the chunk budget. 8K is generous for prose.
MAX_RUN_CHARS = 8000


# ─── Chunked translation primitive ──────────────────────────────────────────

_NUMBERED_PREFIX_RE = re.compile(r"^\s*\[?(\d+)\]?[\.\):\-]\s*", re.MULTILINE)


def _build_chunk_system_prompt(source_lang: str, target_lang: str,
                               glossary: dict | None) -> str:
    src = LANG_NAMES.get((source_lang or "").lower(), source_lang or "the source language")
    tgt = LANG_NAMES.get((target_lang or "").lower(), target_lang or target_lang)
    parts = [
        f"You are a professional translator. Translate the numbered text segments "
        f"from {src} into {tgt}.",
        "",
        "FORMAT — read this carefully:",
        "- Input is a numbered list, each entry on its own line: `[1] ...`, `[2] ...`, etc.",
        "- Output the SAME numbered list with the translation on each line.",
        "- Output EXACTLY the same number of entries as the input. Do not merge, split, or skip any.",
        "- Preserve every entry's index. `[7]` in the input must be `[7]` in the output.",
        "- An entry's content may be a single word, a phrase, a fragment, or a full sentence — translate each in isolation.",
        "- Inside an entry, preserve numbers, dates, units, URLs, emails, proper nouns, and trailing/leading whitespace verbatim.",
        "- If an entry is already in the target language, repeat it unchanged.",
        "- If an entry is empty after the index, output the index alone (e.g. `[3]`).",
        "- Output ONLY the numbered list. No preamble, no explanation, no surrounding quotes, no code fence.",
    ]
    block = glossary_to_system_block(glossary)
    if block:
        parts.append(block)
    return "\n".join(parts)


def _format_chunk_for_prompt(runs: list[str]) -> str:
    """Build the `[1] ... [2] ...` numbered-list user message for one chunk."""
    return "\n".join(f"[{i + 1}] {r}" for i, r in enumerate(runs))


def _parse_chunk_response(response: str, expected: int) -> list[str] | None:
    """Parse `[1] foo\n[2] bar\n...` back into a list of strings.

    Returns None if the response is malformed. Caller falls back to
    per-run translation in that case so we never lose content.
    """
    if not response:
        return None
    text = response.strip()
    # Strip optional code-fence the model sometimes adds despite the rule.
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n?", "", text)
        text = re.sub(r"\n?```$", "", text)
    out: dict[int, str] = {}
    current_idx: int | None = None
    current_lines: list[str] = []

    def _flush():
        if current_idx is not None:
            out[current_idx] = "\n".join(current_lines).rstrip()

    for line in text.split("\n"):
        m = re.match(r"^\s*\[?(\d+)\]?[\.\):\-]?\s?(.*)$", line)
        if m and m.group(1).isdigit():
            idx = int(m.group(1))
            # Only treat as a fresh entry if it's plausible — the model
            # sometimes writes "[2014]" inside an entry and we'd misread it.
            if 1 <= idx <= expected and (current_idx is None or idx == current_idx + 1):
                _flush()
                current_idx = idx
                current_lines = [m.group(2)]
                continue
        if current_idx is not None:
            current_lines.append(line)
    _flush()

    if not out:
        return None
    # Allow tolerant fill: missing indices fall back to None and caller fills
    # via per-run retry. But require at least 60% to consider parsing successful.
    if len(out) < max(1, int(expected * 0.6)):
        return None
    return [out.get(i + 1, "") for i in range(expected)]


def _build_rewrite_chunk_system_prompt(lang: str, tone: str) -> str:
    from .text import build_rewrite_system_prompt
    base = build_rewrite_system_prompt(lang, tone)
    chunk_rules = "\n".join([
        "",
        "FORMAT:",
        "- Input is a numbered list: `[1] ...`, `[2] ...`, etc.",
        "- Output the SAME numbered list with the rewritten text on each line.",
        "- Output EXACTLY the same number of entries. Do not merge, split, or skip any.",
        "- Output ONLY the numbered list. No preamble, no explanation.",
    ])
    return base + chunk_rules


def _resolve_rewrite_model(fallback: str) -> str:
    """Prefer refinement model for tone rewrites; fall back to translation model."""
    try:
        import brain
        tcfg = brain.get_tool_config() or {}
        m = ((tcfg.get("refinement") or {}).get("model") or "").strip()
        if m:
            return m
    except Exception:
        pass
    return fallback


def _rewrite_chunks(runs: list[str], *,
                    lang: str,
                    tone: str,
                    model: str,
                    progress: Callable[[int, int], None] | None = None) -> list[str]:
    """Rewrite `runs` in-place for tone, preserving count and order."""
    from handlers import sidecar_proxy as _sidecar_proxy
    import brain as _brain
    if not runs:
        return []
    model = _resolve_rewrite_model(model)
    # GDPR policy gate: scan every run once so the mapping is stable across
    # every chunk of this document. Pseudonymised runs go to the wire; the
    # deanon callback restores originals on each translated entry before we
    # write it back. Cloud-bound runs ride to the same model the user picked
    # (modulo admin swap policy).
    _rewrite_deanon = _brain._identity_deanon
    try:
        model, _new_runs, _rewrite_deanon = _brain.gdpr_pick_model_for_background(
            model, runs, purpose="translate_document_rewrite")
        wire_runs = list(_new_runs)
    except _brain.GDPRBlockedError:
        # Treat as soft-skip — keep the originals, don't fire the rewrite.
        return list(runs)
    system_prompt = _build_rewrite_chunk_system_prompt(lang, tone)
    out: list[str] = list(runs)
    total = len(runs)
    done = 0
    for start in range(0, len(wire_runs), CHUNK_RUNS):
        chunk = wire_runs[start:start + CHUNK_RUNS]
        orig_chunk = runs[start:start + CHUNK_RUNS]
        if all(not r.strip() for r in chunk):
            done += len(chunk)
            if progress:
                progress(done, total)
            continue
        prompt = _format_chunk_for_prompt(chunk)
        _res = _sidecar_proxy.background_call(
            messages=[{"role": "user", "content": prompt}],
            model=model,
            system_prompt=system_prompt,
        )
        response = _res.get("reply") or ""
        if response and not _res.get("error"):
            parsed = _parse_chunk_response(response, len(chunk))
            if parsed:
                for j, r in enumerate(orig_chunk):
                    if not r.strip():
                        continue
                    t = _rewrite_deanon(parsed[j] if j < len(parsed) else "")
                    lead = r[:len(r) - len(r.lstrip())]
                    trail = r[len(r.rstrip()):]
                    out[start + j] = f"{lead}{t.strip()}{trail}" if t.strip() else r
        done += len(chunk)
        if progress:
            progress(done, total)
    return out


def _translate_chunks(runs: list[str], *,
                      source_lang: str, target_lang: str,
                      glossary: dict | None,
                      model: str,
                      progress: Callable[[int, int], None] | None = None) -> list[str]:
    """Translate `runs` (one entry per text run) preserving order + count.

    On chunk-parse failure: retry that chunk one-by-one via translate_text so
    we never silently drop runs.
    """
    from handlers import sidecar_proxy as _sidecar_proxy
    from .text import translate_text
    import brain as _brain

    if not runs:
        return []

    # GDPR policy gate: anonymise every run once with a single shared
    # mapping so tokens stay consistent across chunks of the same document.
    # Cloud calls below see pseudonymised text; the deanon callback runs on
    # each translated entry before we write it back. Per-run fallback uses
    # the ORIGINAL run text — translate_text has its own gate that reuses
    # the same session mapping when current_session_id is set.
    _xlate_deanon = _brain._identity_deanon
    try:
        model, _new_runs, _xlate_deanon = _brain.gdpr_pick_model_for_background(
            model, runs, purpose="translate_document")
        wire_runs = list(_new_runs)
    except _brain.GDPRBlockedError as e:
        raise RuntimeError(f"document translation blocked by GDPR policy: {e}")

    system_prompt = _build_chunk_system_prompt(source_lang, target_lang, glossary)
    out: list[str] = [""] * len(runs)
    total = len(runs)
    done = 0

    for start in range(0, len(wire_runs), CHUNK_RUNS):
        chunk = wire_runs[start:start + CHUNK_RUNS]
        orig_chunk = runs[start:start + CHUNK_RUNS]
        # Fast-path: chunks that are entirely empty/whitespace go through unchanged.
        if all(not r.strip() for r in chunk):
            for j, r in enumerate(orig_chunk):
                out[start + j] = r
            done += len(chunk)
            if progress:
                progress(done, total)
            continue

        prompt = _format_chunk_for_prompt(chunk)
        _res = _sidecar_proxy.background_call(
            messages=[{"role": "user", "content": prompt}],
            model=model,
            system_prompt=system_prompt,
        )
        response = _res.get("reply") or ""
        parsed: list[str] | None = None
        if response and not _res.get("error"):
            parsed = _parse_chunk_response(response, len(chunk))

        if parsed is None:
            # Per-run fallback so a flaky chunk never zeros out content.
            # Pass the ORIGINAL text — translate_text re-anonymises with the
            # same session-scoped mapping where applicable.
            for j, r in enumerate(orig_chunk):
                if not r.strip():
                    out[start + j] = r
                    continue
                try:
                    res = translate_text(
                        r, target_lang,
                        source_lang=source_lang,
                        glossary_slug="",
                        model=model,
                    )
                    out[start + j] = res.get("translation") or r
                except Exception:
                    out[start + j] = r
        else:
            for j, r in enumerate(orig_chunk):
                # Empty original → keep empty (don't let model invent text).
                if not r.strip():
                    out[start + j] = r
                    continue
                t = _xlate_deanon(parsed[j] if j < len(parsed) else "")
                # Preserve leading/trailing whitespace from the original — the
                # numbered-list framing strips it on the wire.
                lead = r[:len(r) - len(r.lstrip())]
                trail = r[len(r.rstrip()):]
                out[start + j] = f"{lead}{t.strip()}{trail}" if t.strip() else r
        done += len(chunk)
        if progress:
            progress(done, total)
    return out


# ─── DOCX ───────────────────────────────────────────────────────────────────

def _docx_xml_targets(zf: zipfile.ZipFile) -> list[str]:
    """Files inside a .docx that contain user-visible text runs.

    `document.xml` is the body. Headers/footers/footnotes/endnotes/comments
    are separate parts that all use the same `<w:t>` tag.
    """
    names = set(zf.namelist())
    targets = []
    for n in sorted(names):
        if n == "word/document.xml":
            targets.append(n)
        elif (n.startswith("word/header") or n.startswith("word/footer")
              or n.startswith("word/footnotes") or n.startswith("word/endnotes")
              or n.startswith("word/comments")) and n.endswith(".xml"):
            targets.append(n)
    return targets


def _pptx_xml_targets(zf: zipfile.ZipFile) -> list[str]:
    """Files inside a .pptx with text runs: slides, layouts, masters, notes."""
    targets = []
    for n in sorted(zf.namelist()):
        if (n.startswith("ppt/slides/slide") or n.startswith("ppt/notesSlides/notesSlide")
                or n.startswith("ppt/slideLayouts/slideLayout")
                or n.startswith("ppt/slideMasters/slideMaster")) and n.endswith(".xml"):
            targets.append(n)
    return targets


def _collect_office_runs(xml_bytes: bytes, text_tag: str) -> tuple[ET.ElementTree, list[ET.Element]]:
    """Parse one office-XML part, return tree + list of text-run elements.

    Namespace prefixes (`w:`, `a:`) are registered globally before this is
    called so the ET round-trip emits the canonical aliases Word/PowerPoint
    expect — without that, default-namespace parts come back with `ns0:`
    aliases and the file fails to open.
    """
    root = ET.fromstring(xml_bytes)
    et = ET.ElementTree(root)
    runs = [el for el in root.iter(text_tag)]
    return et, runs


def _translate_office_zip(src_path: str, dst_path: str, *,
                          text_tag: str,
                          target_files_fn: Callable[[zipfile.ZipFile], list[str]],
                          ns_map: dict[str, str],
                          source_lang: str, target_lang: str,
                          glossary: dict | None,
                          model: str,
                          tone: str = "",
                          progress: Callable[[int, int], None] | None) -> dict:
    """Generic docx/pptx pipeline — collect runs across all parts, translate
    once with a single shared chunk pass, write back, re-zip.

    Single-pass cross-part translation matters: a header repeats on every
    page, a slide master shadows every layout. Translating each part in
    isolation would burn N× tokens on the same string.
    """
    # Register namespaces so ET emits canonical prefixes (`w:`, `a:`).
    for prefix, uri in ns_map.items():
        ET.register_namespace(prefix, uri)

    # Pass 1 — collect every run across every target file.
    parts: dict[str, tuple[ET.ElementTree, list[ET.Element]]] = {}
    all_runs: list[str] = []
    # Track (part_name, run_index_in_part) for every collected run so we can
    # write translations back in one sweep.
    run_origin: list[tuple[str, int]] = []
    with zipfile.ZipFile(src_path, "r") as zf:
        members = list(zf.namelist())
        targets = target_files_fn(zf)
        for name in targets:
            data = zf.read(name)
            tree, runs = _collect_office_runs(data, text_tag)
            parts[name] = (tree, runs)
            for idx, run in enumerate(runs):
                txt = run.text or ""
                if len(txt) > MAX_RUN_CHARS:
                    txt = txt[:MAX_RUN_CHARS]
                all_runs.append(txt)
                run_origin.append((name, idx))

    if not all_runs:
        # Nothing to translate — copy the file as-is.
        shutil.copyfile(src_path, dst_path)
        return {"runs": 0, "parts": len(parts), "fallback": False}

    translated = _translate_chunks(
        all_runs,
        source_lang=source_lang,
        target_lang=target_lang,
        glossary=glossary,
        model=model,
        progress=None if tone else progress,
    )

    if tone:
        translated = _rewrite_chunks(
            translated,
            lang=target_lang,
            tone=tone,
            model=model,
            progress=progress,
        )

    # Pass 2 — write translations back into their parts.
    for (part_name, idx), new_text in zip(run_origin, translated):
        tree, runs = parts[part_name]
        runs[idx].text = new_text
        # Preserve `xml:space="preserve"` semantics: if the run has leading
        # or trailing whitespace, OOXML requires that attribute or Word
        # collapses the spaces on render.
        if (new_text and (new_text != new_text.strip())):
            runs[idx].set("{http://www.w3.org/XML/1998/namespace}space", "preserve")

    # Pass 3 — write a new zip mirroring the original, replacing the parts
    # we touched. ZipFile.write order roughly matches namelist() so layout
    # apps that key off ordering stay happy.
    with zipfile.ZipFile(src_path, "r") as zin, zipfile.ZipFile(
            dst_path, "w", zipfile.ZIP_DEFLATED) as zout:
        for name in zin.namelist():
            if name in parts:
                tree, _runs = parts[name]
                buf = io.BytesIO()
                tree.write(buf, xml_declaration=True, encoding="UTF-8", default_namespace=None)
                zout.writestr(name, buf.getvalue())
            else:
                zout.writestr(name, zin.read(name))

    return {"runs": len(all_runs), "parts": len(parts), "fallback": False}


def translate_docx(src_path: str, dst_path: str, **kwargs) -> dict:
    return _translate_office_zip(
        src_path, dst_path,
        text_tag=DOCX_TEXT_TAG,
        target_files_fn=_docx_xml_targets,
        ns_map={"w": DOCX_NS},
        **kwargs,
    )


def translate_pptx(src_path: str, dst_path: str, **kwargs) -> dict:
    return _translate_office_zip(
        src_path, dst_path,
        text_tag=PPTX_TEXT_TAG,
        target_files_fn=_pptx_xml_targets,
        ns_map={"a": PPTX_A_NS},
        **kwargs,
    )


# ─── PDF ────────────────────────────────────────────────────────────────────

def _pdf_extract_text(src_path: str) -> str:
    """Pull text out of a PDF, trying multiple backends.

    markitdown is preferred (markdown structure: headings, lists, tables).
    Falls back to pymupdf (fitz) for PDFs whose font encoding markitdown's
    pdfminer backend can't decode — Brain's `read_document` already uses
    fitz as its primary PDF reader for the same reason. pdfplumber is a
    last-ditch backup; on text-light PDFs it sometimes wins where the
    others lose.
    """
    # 1. markitdown — best layout, but pdfminer backend chokes on some fonts.
    try:
        from markitdown import MarkItDown
        md = MarkItDown(enable_plugins=False)
        text_md = (md.convert(src_path).text_content or "").strip()
        if text_md:
            return text_md
    except Exception:
        pass

    # 2. pymupdf — robust against unusual font encodings.
    try:
        import fitz  # pymupdf
        with fitz.open(src_path) as doc:
            chunks = []
            for page in doc:
                t = page.get_text("text") or ""
                if t.strip():
                    chunks.append(t.rstrip())
            if chunks:
                # Each page becomes its own block separated by blank lines so
                # the paragraph splitter downstream still sees structure.
                return "\n\n".join(chunks).strip()
    except Exception:
        pass

    # 3. pdfplumber — slower but sometimes finds text the others miss.
    try:
        import pdfplumber
        with pdfplumber.open(src_path) as plumb:
            chunks = []
            for page in plumb.pages:
                t = page.extract_text() or ""
                if t.strip():
                    chunks.append(t.rstrip())
            if chunks:
                return "\n\n".join(chunks).strip()
    except Exception:
        pass

    return ""


def translate_pdf(src_path: str, dst_path: str, *,
                  source_lang: str, target_lang: str,
                  glossary: dict | None,
                  model: str,
                  tone: str = "",
                  progress: Callable[[int, int], None] | None) -> dict:
    """PDFs translate to DOCX with layout preserved.

    Pipeline: pdf2docx pre-converts the PDF into a layout-faithful DOCX
    (text-boxes, tables, images, columns become real DOCX equivalents),
    then we run the standard DOCX in-place run-rewrite pass on it. The
    user gets a .docx that visually matches the original PDF.

    Falls back to plain text → markdown → docx if pdf2docx isn't available
    or the conversion fails — readable output for PDFs that pdf2docx can't
    cope with (heavily form-based, weird font encodings).

    `dst_path` should already be `<basename>.docx` — the caller (translate_
    document_file) handles the extension swap.
    """
    try:
        from pdf2docx import Converter
    except ImportError:
        Converter = None  # type: ignore

    if Converter is not None:
        # Stage the layout DOCX next to the destination so we can clean it
        # up after the in-place translation pass.
        stem, _ = os.path.splitext(dst_path)
        intermediate_docx = f"{stem}.layout.docx"
        try:
            # pdf2docx prints "[INFO] (N/M) Page N" to stdout/stderr directly
            # (not via the logging module) — eats the daemon log otherwise.
            # Redirect both so progress lines disappear from the server log.
            import contextlib, io as _io
            _devnull = _io.StringIO()
            with contextlib.redirect_stdout(_devnull), \
                 contextlib.redirect_stderr(_devnull):
                cv = Converter(src_path)
                try:
                    cv.convert(intermediate_docx)
                finally:
                    cv.close()
            if os.path.exists(intermediate_docx) and os.path.getsize(intermediate_docx) > 0:
                # Run the standard DOCX pipeline against the converted file —
                # same in-place run rewrite, layout / tables / images stay.
                info = translate_docx(
                    intermediate_docx, dst_path,
                    source_lang=source_lang,
                    target_lang=target_lang,
                    glossary=glossary,
                    model=model,
                    tone=tone,
                    progress=progress,
                )
                try:
                    os.unlink(intermediate_docx)
                except OSError:
                    pass
                return {"runs": info["runs"], "parts": info["parts"], "fallback": True}
        except Exception as e:
            print(f"[translate_pdf] pdf2docx failed ({e}) — falling back to "
                  f"plain-text pipeline", flush=True)
            if os.path.exists(intermediate_docx):
                try:
                    os.unlink(intermediate_docx)
                except OSError:
                    pass

    # ── Fallback: plain text → markdown → docx (layout NOT preserved) ──
    text_md = _pdf_extract_text(src_path)
    if not text_md:
        raise RuntimeError(
            "PDF produced no extractable text after trying markitdown, "
            "pymupdf, and pdfplumber — likely a scanned image-only PDF. "
            "Run OCR on it first, then re-upload."
        )

    paragraphs: list[str] = []
    para_buf: list[str] = []
    for line in text_md.split("\n"):
        if not line.strip():
            if para_buf:
                paragraphs.append("\n".join(para_buf))
                para_buf = []
            paragraphs.append("")
        else:
            para_buf.append(line)
    if para_buf:
        paragraphs.append("\n".join(para_buf))

    translated = _translate_chunks(
        paragraphs,
        source_lang=source_lang,
        target_lang=target_lang,
        glossary=glossary,
        model=model,
        progress=None if tone else progress,
    )
    if tone:
        translated = _rewrite_chunks(
            translated,
            lang=target_lang,
            tone=tone,
            model=model,
            progress=progress,
        )
    new_md = "\n\n".join(t for t in translated if t is not None)

    import brain  # late import
    out = brain.tool_write_document({"path": dst_path, "content": new_md})
    try:
        parsed = json.loads(out)
        if "error" in parsed:
            raise RuntimeError(parsed["error"])
    except json.JSONDecodeError:
        if not os.path.exists(dst_path):
            raise RuntimeError(f"PDF→DOCX render failed: {out[:200]}")

    return {
        "runs": len([p for p in paragraphs if p.strip()]),
        "parts": 1,
        "fallback": True,
    }


# ─── Public entry point ─────────────────────────────────────────────────────

SUPPORTED_EXTS = {".docx", ".pptx", ".pdf"}


def _resolve_model(explicit: str) -> str:
    import brain  # late import
    chosen = (explicit or "").strip()
    if chosen:
        return chosen
    try:
        tcfg = brain.get_tool_config() or {}
        chosen = ((tcfg.get("translation") or {}).get("default_model") or "").strip()
    except Exception:
        chosen = ""
    if not chosen:
        try:
            tcfg = brain.get_tool_config() or {}
            chosen = ((tcfg.get("refinement") or {}).get("model") or "").strip()
        except Exception:
            chosen = ""
    if not chosen:
        chosen = getattr(brain, "_delegate_fallback_model", "") or ""
    if not chosen:
        raise RuntimeError("no model available — set tools_config.translation.default_model")
    return chosen


def translate_document_file(src_path: str, *,
                            target_lang: str,
                            source_lang: str = "",
                            glossary_slug: str = "",
                            model: str = "",
                            tone: str = "",
                            output_dir: str = "",
                            progress: Callable[[int, int], None] | None = None,
                            ) -> dict:
    """Translate one document (docx/pptx/pdf) into `target_lang`.

    - `output_dir`: if set, write the translated file there. Otherwise next to
      the source. Filename is `<stem>.<target>.<ext>` (PDFs become `.docx`).
    - `progress(done, total)`: called as runs translate.

    Returns: {output_path, format, runs, source_lang, target_lang, glossary,
              model, fallback (bool — true for PDF), detected (dict or None)}.
    """
    if not os.path.isfile(src_path):
        raise FileNotFoundError(src_path)
    target_lang = (target_lang or "").strip().lower()
    if not target_lang:
        raise ValueError("target_lang is required")

    ext = os.path.splitext(src_path)[1].lower()
    if ext not in SUPPORTED_EXTS:
        raise ValueError(f"unsupported format '{ext}' — supported: {sorted(SUPPORTED_EXTS)}")

    # Detect source language if not given. We sample text from the doc for
    # detection — full extraction would cost a full markitdown pass.
    detected = None
    src = (source_lang or "").strip().lower()
    if not src:
        try:
            sample = _sample_text_for_detection(src_path, ext)
            if sample.strip():
                detected = detect_language(sample)
                src = detected.get("lang", "") if detected else ""
        except Exception:
            detected = None

    same_lang = bool(src and src == target_lang)

    if same_lang and not tone:
        # No-op — copy and return. Callers can show "already in target".
        os.makedirs(output_dir or os.path.dirname(src_path) or ".", exist_ok=True)
        out_path = _build_output_path(src_path, target_lang, ext, output_dir)
        shutil.copyfile(src_path, out_path)
        return {
            "output_path": out_path,
            "format": ext.lstrip("."),
            "runs": 0,
            "source_lang": src,
            "target_lang": target_lang,
            "glossary": glossary_slug or "",
            "model": "",
            "fallback": False,
            "detected": detected,
            "noop": True,
        }

    glossary = load_glossary(glossary_slug) if glossary_slug else None
    chosen_model = _resolve_model(model)

    # PDFs land as .docx — surface it in the path so the user isn't surprised.
    out_ext = ".docx" if ext == ".pdf" else ext
    out_path = _build_output_path(src_path, target_lang, out_ext, output_dir)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)

    common = dict(
        source_lang=src,
        target_lang=target_lang,
        glossary=glossary,
        model=chosen_model,
        tone=tone,
        progress=progress,
    )
    if ext == ".docx":
        info = translate_docx(src_path, out_path, **common)
    elif ext == ".pptx":
        info = translate_pptx(src_path, out_path, **common)
    else:  # .pdf
        info = translate_pdf(src_path, out_path, **common)

    return {
        "output_path": out_path,
        "format": ext.lstrip("."),
        "runs": info["runs"],
        "source_lang": src,
        "target_lang": target_lang,
        "glossary": glossary_slug or "",
        "model": chosen_model,
        "fallback": info.get("fallback", False),
        "detected": detected,
        "noop": False,
    }


def _build_output_path(src_path: str, target_lang: str, out_ext: str,
                       output_dir: str) -> str:
    base = os.path.basename(src_path)
    stem, _ = os.path.splitext(base)
    out_name = f"{stem}.{target_lang}{out_ext}"
    parent = output_dir or os.path.dirname(src_path) or "."
    return os.path.join(parent, out_name)


def _sample_text_for_detection(path: str, ext: str, max_chars: int = 1500) -> str:
    """Pull a short text sample from a doc for language detection.

    Cheap: scans only the first text-bearing part. Avoids loading the full
    markitdown pipeline just to guess a language.
    """
    if ext == ".docx":
        try:
            with zipfile.ZipFile(path, "r") as zf:
                if "word/document.xml" not in zf.namelist():
                    return ""
                data = zf.read("word/document.xml")
            root = ET.fromstring(data)
            chunks: list[str] = []
            total = 0
            for el in root.iter(DOCX_TEXT_TAG):
                t = (el.text or "").strip()
                if t:
                    chunks.append(t)
                    total += len(t)
                    if total >= max_chars:
                        break
            return " ".join(chunks)[:max_chars]
        except Exception:
            return ""
    if ext == ".pptx":
        try:
            with zipfile.ZipFile(path, "r") as zf:
                slide_names = sorted(n for n in zf.namelist()
                                     if n.startswith("ppt/slides/slide") and n.endswith(".xml"))
                chunks: list[str] = []
                total = 0
                for n in slide_names:
                    root = ET.fromstring(zf.read(n))
                    for el in root.iter(PPTX_TEXT_TAG):
                        t = (el.text or "").strip()
                        if t:
                            chunks.append(t)
                            total += len(t)
                            if total >= max_chars:
                                return " ".join(chunks)[:max_chars]
                return " ".join(chunks)[:max_chars]
        except Exception:
            return ""
    if ext == ".pdf":
        # Reuse the multi-backend extractor so detection sees text whenever
        # the real translation pass would. Truncate to keep this cheap.
        try:
            return _pdf_extract_text(path)[:max_chars]
        except Exception:
            return ""
    return ""
