# File / shell / python / document tool bodies (extracted from brain.py, E1).
#
# The 10 file-system tools (read/write/edit_file, list_directory, search_files,
# execute_command, python_exec, read/write/edit_document) plus the helpers
# private to this cluster. This is a PURE relocation — JSON envelopes, error
# strings, security gates, and side-effects (artifact registration, GDPR
# anonymisation) are byte-identical to the pre-E1 brain.py.
#
# Seams:
#   - `_ok` / `_err` / `_get_artifact_session_folder` / `_record_session_read_path`
#     come from engine.tool_exec (the C2 extraction; re-exported on brain too).
#   - `_thread_local` comes from engine.context (low-level base, no cycle).
#   - brain-runtime symbols (`_after_file_write`, `_gdpr_anon_tool_text`,
#     `_route_to_node`, `get_tool_config`, `AGENTS_DIR`, `_current_agent`,
#     `DocumentParser`) are reached lazily via `import brain as _brain` inside
#     function bodies — a top-level `import brain` would be a cycle (brain.py
#     imports this module for TOOL_DISPATCH).
#   - `engine.doc_convert._do_extract` (read_document's binary path) imports
#     directly from engine/.
#
# brain.py re-exports all 10 tool functions via
# `from engine.tools.file_tools import (...)` so `brain.tool_read_file` etc.,
# the TOOL_DISPATCH entries, and any in-brain bare-name calls resolve unchanged.

from __future__ import annotations

import fnmatch
import glob as globmod
import json
import os
import re
import subprocess
import sys
import time

from engine.context import _thread_local
from engine.tool_exec import (
    _ok,
    _err,
    _get_artifact_session_folder,
    _record_session_read_path,
)


def tool_read_file(args: dict) -> str:
    import brain as _brain
    node_result = _brain._route_to_node("read_file", args)
    if node_result is not None:
        return node_result
    path = args.get("path", "")
    offset = args.get("offset", 1)
    limit = args.get("limit", 400)
    try:
        path = os.path.expanduser(path)
        if not os.path.isabs(path):
            path = os.path.abspath(path)
        if not os.path.exists(path):
            return _err(
                f"File not found: {path}. "
                "Do NOT retry with the same path — the file does not exist on disk. "
                "Hallucinated paths are a frequent cause: the path was inferred "
                "from the project's folder structure or filename schema rather "
                "than copied from a real source. "
                "USE ONLY paths returned in `read_path` (or `read_path_original` "
                "as fallback) of a prior `mempalace_query` drawer. "
                "If no drawer pointed at this content, the project does not "
                "contain it — refuse per REFUSAL DISCIPLINE instead of guessing "
                "another path."
            )
        with open(path, "r", errors="replace") as f:
            lines = f.readlines()
        total = len(lines)
        start = max(0, offset - 1)
        end = start + limit if limit else total
        selected = lines[start:end]
        # Number lines
        numbered = []
        for i, line in enumerate(selected, start=start + 1):
            numbered.append(f"{i:>6}\t{line.rstrip()}")
        content = "\n".join(numbered)
        _record_session_read_path(path)
        content_anon = _brain._gdpr_anon_tool_text(content, f"file:{os.path.basename(path)}")
        return _ok({"path": path, "total_lines": total, "showing": f"{start+1}-{min(end, total)}", "content": content_anon})
    except Exception as e:
        return _err(f"read_file: {e}")


def tool_write_file(args: dict) -> str:
    import brain as _brain
    node_result = _brain._route_to_node("write_file", args)
    if node_result is not None:
        return node_result
    path = args.get("path", "")
    content = args.get("content", "")
    try:
        path = os.path.expanduser(path)
        # Resolve the session's artifact dir (when in a chat) so we can both
        # default relative paths into it AND warn on an absolute path that
        # lands outside it.
        session_id = getattr(_thread_local, 'current_session_id', None)
        agent = getattr(_thread_local, 'current_agent', None) or _brain._current_agent
        artifact_dir = ""
        if session_id and agent:
            folder = _get_artifact_session_folder(session_id)
            artifact_dir = os.path.join(_brain.AGENTS_DIR, agent.agent_id, "artifacts", folder)
        was_absolute = os.path.isabs(path)
        if not was_absolute:
            # Default relative paths to artifacts session folder during chat
            if artifact_dir:
                os.makedirs(artifact_dir, exist_ok=True)
                path = os.path.join(artifact_dir, path)
            else:
                path = os.path.abspath(path)
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w") as f:
            f.write(content)
        size = os.path.getsize(path)
        agent = getattr(_thread_local, 'current_agent', None) or _brain._current_agent
        _brain._after_file_write(path, "created", agent.agent_id if agent else "main")
        result = {"path": path, "size": size, "status": "written"}
        # Warn when an ABSOLUTE path was written outside the artifact folder —
        # it won't appear in the Artifacts panel. (Relative paths always land
        # inside, so only the absolute case can stray.)
        if was_absolute and artifact_dir:
            try:
                if not os.path.realpath(path).startswith(os.path.realpath(artifact_dir)):
                    result["warning"] = (
                        "Wrote to an absolute path OUTSIDE your session artifact "
                        "folder — this file does NOT appear in the Artifacts panel "
                        "and the user cannot see or download it. Re-save with a "
                        "RELATIVE filename (e.g. `report.docx`) so it lands in your "
                        "artifact folder, unless the user explicitly asked for "
                        "this absolute path.")
            except OSError:
                pass
        return _ok(result)
    except Exception as e:
        return _err(f"write_file: {e}")


def tool_edit_file(args: dict) -> str:
    import brain as _brain
    path = args.get("path", "")
    old_string = args.get("old_string", "")
    new_string = args.get("new_string", "")
    replace_all = args.get("replace_all", False)
    try:
        path = os.path.expanduser(path)
        if not os.path.isabs(path):
            path = os.path.abspath(path)
        with open(path, "r") as f:
            content = f.read()
        count = content.count(old_string)
        if count == 0:
            return _err(f"edit_file: old_string not found in {path}")
        if count > 1 and not replace_all:
            return _err(f"edit_file: old_string found {count} times — use replace_all=true or provide a more specific match")
        if replace_all:
            new_content = content.replace(old_string, new_string)
        else:
            new_content = content.replace(old_string, new_string, 1)
        with open(path, "w") as f:
            f.write(new_content)
        agent = getattr(_thread_local, 'current_agent', None) or _brain._current_agent
        _brain._after_file_write(path, "modified", agent.agent_id if agent else "main")
        return _ok({"path": path, "replacements": count if replace_all else 1, "status": "edited"})
    except Exception as e:
        return _err(f"edit_file: {e}")


def tool_read_document(args: dict) -> str:
    """Format-aware document reader."""
    import brain as _brain
    # Accept legacy / drawer-vocab synonyms — drawers from mempalace_query
    # carry the field as `source_file`, and the model frequently passes that
    # name verbatim. Treating an empty `path` as CWD silently expanded into
    # "Is a directory" errors that the model then ignored.
    path = args.get("path", "") or args.get("source_file", "") or args.get("file", "")
    if not path or not str(path).strip():
        return _err(
            "read_document: 'path' is required (got empty). When following up "
            "on a mempalace_query drawer, take its `source_file` value and "
            "pass it as the `path` argument — JOIN with the input-folder "
            "absolute path if `source_file` is relative.")
    path = str(path).strip()
    try:
        path = os.path.expanduser(path)
        if not os.path.isabs(path):
            path = os.path.abspath(path)
        if os.path.isdir(path):
            return _err(
                f"read_document: '{path}' is a directory, not a file. The "
                "model commonly passes a base path here when it meant to "
                "pass a specific document — re-issue the call with the full "
                "file path including extension.")
        if not os.path.exists(path):
            return _err(
                f"File not found: {path}. "
                "Do NOT retry with the same path — the file does not exist on disk. "
                "Hallucinated paths are a frequent cause: the path was inferred "
                "from the project's folder structure or filename schema rather "
                "than copied from a real source. "
                "USE ONLY paths returned in `read_path` (or `read_path_original` "
                "as fallback) of a prior `mempalace_query` drawer. "
                "If no drawer pointed at this content, the project does not "
                "contain it — refuse per REFUSAL DISCIPLINE instead of guessing "
                "another path."
            )

        def _ok_and_cache(payload: dict) -> str:
            # Record path so the citation validator can grep this file for
            # verbatim quotes. Anonymisation applies on return so the model
            # sees pseudonymised text in cross-session reads.
            _record_session_read_path(path)
            _src = f"attachment:{os.path.basename(path)}"
            raw = str(payload.get("content", "") or "")
            anon = _brain._gdpr_anon_tool_text(raw, _src)
            if anon is not raw:
                payload = dict(payload)
                payload["content"] = anon
            return _ok(payload)

        ext = os.path.splitext(path)[1].lower()

        # Office/PDF formats route through the unified doc_convert pipeline
        # (markitdown-first → per-format fallback), shared with project mining.
        # read_document passes caps=False (full fidelity) + the selection/meta
        # knobs each format supports.
        # All doc_convert-supported binary/tabular formats route through the
        # one unified pipeline (markitdown-first → per-format fallback), shared
        # with project mining + classification scan. read_document passes
        # caps=False (full fidelity) + the selection/meta knobs each format
        # supports. .xls aliases to the xlsx extractor; .msg/.epub/.zip are
        # markitdown-first (no inline MarkItDown() call here anymore).
        if ext in (".pdf", ".docx", ".pptx", ".xlsx", ".xls",
                   ".csv", ".tsv", ".eml", ".msg", ".epub", ".zip"):
            from engine.doc_convert import _do_extract
            kwargs: dict = {"caps": False}
            if ext in (".xlsx", ".xls"):
                kwargs["sheet"] = args.get("sheet")
            elif ext == ".pptx":
                kwargs["slides"] = args.get("slides")
            elif ext == ".pdf":
                kwargs["pages"] = args.get("pages", "") or None
                kwargs["include_tables"] = bool(args.get("include_tables"))
                kwargs["emit_meta"] = True
                kwargs["page_marker"] = "--- Page"
            text, backend, err = _do_extract(path, **kwargs)
            if err:
                return _err(f"read_document: {err}")
            # Normalise the reported format for the aliases.
            fmt = {".xls": "xlsx", ".tsv": "csv"}.get(ext, ext.lstrip("."))
            # `backend` ("markitdown" / "fitz/legacy" / "mistral-ocr (Np)" / …)
            # records which of the two extraction surfaces produced this text.
            # Surfaced in the chat tool-block + persisted via metadata.tools.
            return _ok_and_cache({"path": path, "format": fmt,
                                  "backend": backend, "content": text})

        elif ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"):
            meta_text = _brain.DocumentParser.parse_image(path)
            vision_note = "\n\n*(For AI-powered image description, include this image directly in your chat message)*"
            _content = _brain._gdpr_anon_tool_text(
                meta_text, f"attachment:{os.path.basename(path)}") + vision_note
            return _ok({"path": path, "format": "image", "content": _content})

        elif ext == ".svg":
            content = _brain.DocumentParser.parse_svg(path)
            return _ok_and_cache({"path": path, "format": "svg", "content": content})

        else:
            # Plain-text / markdown / unknown-extension read. Honor explicit
            # offset+limit when the model paginates; otherwise read the whole
            # file. The previous hard-cap at 500 lines truncated mid-document
            # silently — on the kg-real-policies ISMS Handbuch (1903 lines)
            # the section 2.13 percent-to-score table sits at line 1267,
            # WAY past line 500, so the model never saw the table even when
            # it correctly chose read_document on the .md companion. The
            # tool_result_char_limit middleware will compact downstream if
            # the content is too large for the round budget.
            offset = int(args.get("offset", 1) or 1)
            limit = args.get("limit")
            with open(path, "r", errors="replace") as f:
                lines = f.readlines()
            total = len(lines)
            start = max(0, offset - 1)
            end = start + int(limit) if limit else total
            selected = lines[start:end]
            numbered = []
            for i, line in enumerate(selected, start=start + 1):
                numbered.append(f"{i:>6}\t{line.rstrip()}")
            content = "\n".join(numbered)
            shown = f"{start+1}-{min(end, total)}"
            return _ok_and_cache({"path": path, "format": "text",
                        "total_lines": total, "showing": shown,
                        "content": content})
    except ImportError as e:
        return _err(str(e))
    except Exception as e:
        return _err(f"read_document: {e}")


def tool_write_document(args: dict) -> str:
    """Create documents from markdown content."""
    import brain as _brain
    path = args.get("path", "")
    content = args.get("content", "")
    try:
        path = os.path.expanduser(path)
        if not os.path.isabs(path):
            path = os.path.abspath(path)
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        ext = os.path.splitext(path)[1].lower()

        if ext == ".docx":
            try:
                import docx
            except ImportError:
                return _err("Install python-docx: pip3 install python-docx")
            doc = docx.Document()
            lines = content.split("\n")
            i = 0
            while i < len(lines):
                line = lines[i]
                # Headings
                heading_match = re.match(r'^(#{1,6})\s+(.*)', line)
                if heading_match:
                    level = len(heading_match.group(1))
                    doc.add_heading(heading_match.group(2), level=level)
                    i += 1
                    continue
                # Table detection
                if "|" in line and i + 1 < len(lines) and re.match(r'^\|[\s\-:|]+\|', lines[i + 1]):
                    table_rows = []
                    while i < len(lines) and "|" in lines[i]:
                        cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                        if not re.match(r'^[\s\-:|]+$', lines[i].strip().strip("|")):
                            table_rows.append(cells)
                        i += 1
                    if table_rows:
                        max_cols = max(len(r) for r in table_rows)
                        table = doc.add_table(rows=len(table_rows), cols=max_cols)
                        table.style = "Table Grid"
                        for ri, row_data in enumerate(table_rows):
                            for ci, cell_val in enumerate(row_data):
                                if ci < max_cols:
                                    table.rows[ri].cells[ci].text = cell_val
                    continue
                # Regular paragraph with inline formatting
                stripped = line.strip()
                if stripped:
                    para = doc.add_paragraph()
                    parts = re.split(r'(\*\*\*.*?\*\*\*|\*\*.*?\*\*|\*.*?\*)', stripped)
                    for part in parts:
                        if part.startswith("***") and part.endswith("***"):
                            run = para.add_run(part[3:-3])
                            run.bold = True
                            run.italic = True
                        elif part.startswith("**") and part.endswith("**"):
                            run = para.add_run(part[2:-2])
                            run.bold = True
                        elif part.startswith("*") and part.endswith("*") and len(part) > 2:
                            run = para.add_run(part[1:-1])
                            run.italic = True
                        else:
                            para.add_run(part)
                i += 1
            doc.save(path)

        elif ext == ".xlsx":
            try:
                import openpyxl
            except ImportError:
                return _err("Install openpyxl: pip3 install openpyxl")
            wb = openpyxl.Workbook()
            wb.remove(wb.active)
            sections = re.split(r'^##\s+(.+)$', content, flags=re.MULTILINE)
            if len(sections) < 3:
                ws = wb.create_sheet("Sheet1")
                _write_md_table_to_sheet(ws, content)
            else:
                for si in range(1, len(sections), 2):
                    sheet_name = sections[si].strip()
                    if sheet_name.lower().startswith("sheet:"):
                        sheet_name = sheet_name[6:].strip()
                    sheet_content = sections[si + 1] if si + 1 < len(sections) else ""
                    ws = wb.create_sheet(sheet_name[:31])
                    _write_md_table_to_sheet(ws, sheet_content)
            if not wb.sheetnames:
                wb.create_sheet("Sheet1")
            wb.save(path)

        elif ext == ".pptx":
            try:
                from pptx import Presentation
            except ImportError:
                return _err("Install python-pptx: pip3 install python-pptx")
            prs = Presentation()
            slides_content = re.split(r'^#\s+(.+)$', content, flags=re.MULTILINE)
            if len(slides_content) < 3:
                slide_layout = prs.slide_layouts[1]
                slide = prs.slides.add_slide(slide_layout)
                slide.shapes.title.text = "Slide 1"
                slide.placeholders[1].text = content.strip()
            else:
                for si in range(1, len(slides_content), 2):
                    title = slides_content[si].strip()
                    body = slides_content[si + 1].strip() if si + 1 < len(slides_content) else ""
                    slide_layout = prs.slide_layouts[1]
                    slide = prs.slides.add_slide(slide_layout)
                    slide.shapes.title.text = title
                    tf = slide.placeholders[1].text_frame
                    tf.clear()
                    body_lines = [l for l in body.split("\n") if l.strip()]
                    for li, bline in enumerate(body_lines):
                        bline = bline.strip()
                        bline = re.sub(r'^[-*]\s+', '', bline)
                        if li == 0:
                            tf.text = bline
                        else:
                            p = tf.add_paragraph()
                            p.text = bline
            prs.save(path)

        elif ext == ".pdf":
            try:
                from reportlab.lib.pagesizes import letter
                from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
                from reportlab.lib.styles import getSampleStyleSheet
            except ImportError:
                return _err("Install reportlab: pip3 install reportlab")
            doc_pdf = SimpleDocTemplate(path, pagesize=letter)
            styles = getSampleStyleSheet()
            story = []
            for line in content.split("\n"):
                line = line.strip()
                if not line:
                    story.append(Spacer(1, 12))
                    continue
                heading_match = re.match(r'^(#{1,6})\s+(.*)', line)
                if heading_match:
                    level = len(heading_match.group(1))
                    style_name = f"Heading{min(level, 6)}"
                    if style_name not in styles:
                        style_name = "Heading1"
                    story.append(Paragraph(heading_match.group(2), styles[style_name]))
                else:
                    line = re.sub(r'\*\*\*(.+?)\*\*\*', r'<b><i>\1</i></b>', line)
                    line = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', line)
                    line = re.sub(r'\*(.+?)\*', r'<i>\1</i>', line)
                    story.append(Paragraph(line, styles["Normal"]))
            doc_pdf.build(story)

        else:
            return _err(f"write_document: unsupported format '{ext}'. Supported: .docx, .xlsx, .pptx, .pdf")

        size = os.path.getsize(path)
        agent = getattr(_thread_local, 'current_agent', None) or _brain._current_agent
        _brain._after_file_write(path, "created", agent.agent_id if agent else "main")
        return _ok({"path": path, "size": size, "format": ext.lstrip("."), "status": "written"})
    except ImportError as e:
        return _err(str(e))
    except Exception as e:
        return _err(f"write_document: {e}")


def _write_md_table_to_sheet(ws, md_text: str) -> None:
    """Helper: parse markdown table text and write rows to an openpyxl worksheet."""
    row_idx = 1
    for line in md_text.split("\n"):
        line = line.strip()
        if not line or not line.startswith("|"):
            continue
        if re.match(r'^\|[\s\-:|]+\|$', line):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        for ci, val in enumerate(cells, 1):
            try:
                ws.cell(row=row_idx, column=ci, value=float(val) if "." in val else int(val))
            except (ValueError, TypeError):
                ws.cell(row=row_idx, column=ci, value=val)
        row_idx += 1


def tool_edit_document(args: dict) -> str:
    """Targeted edits to existing documents."""
    import brain as _brain
    path = args.get("path", "")
    action = args.get("action", "")
    params = args.get("params", {})
    try:
        path = os.path.expanduser(path)
        if not os.path.isabs(path):
            path = os.path.abspath(path)
        if not os.path.exists(path):
            return _err(f"File not found: {path}")
        ext = os.path.splitext(path)[1].lower()

        if ext == ".docx":
            if action != "replace_text":
                return _err(f"edit_document: unsupported action '{action}' for DOCX. Use: replace_text")
            try:
                import docx
            except ImportError:
                return _err("Install python-docx: pip3 install python-docx")
            old_text = params.get("old_text", "")
            new_text = params.get("new_text", "")
            if not old_text:
                return _err("edit_document: 'old_text' required in params")
            doc = docx.Document(path)
            count = 0
            for para in doc.paragraphs:
                if old_text in para.text:
                    full_text = para.text
                    new_full = full_text.replace(old_text, new_text)
                    for run in para.runs:
                        run.text = ""
                    if para.runs:
                        para.runs[0].text = new_full
                    else:
                        para.add_run(new_full)
                    count += 1
            doc.save(path)
            agent = getattr(_thread_local, 'current_agent', None) or _brain._current_agent
            _brain._after_file_write(path, "modified", agent.agent_id if agent else "main")
            return _ok({"path": path, "action": action, "replacements": count, "status": "edited"})

        elif ext in (".xlsx", ".xls"):
            try:
                import openpyxl
            except ImportError:
                return _err("Install openpyxl: pip3 install openpyxl")
            wb = openpyxl.load_workbook(path)

            if action == "update_cell":
                sheet_name = params.get("sheet", wb.sheetnames[0])
                cell_ref = params.get("cell", "")
                value = params.get("value", "")
                if not cell_ref:
                    return _err("edit_document: 'cell' required (e.g. 'A1')")
                if sheet_name not in wb.sheetnames:
                    return _err(f"Sheet '{sheet_name}' not found. Available: {wb.sheetnames}")
                ws = wb[sheet_name]
                try:
                    ws[cell_ref] = float(value) if isinstance(value, str) and value.replace(".", "", 1).replace("-", "", 1).isdigit() else value
                except Exception:
                    ws[cell_ref] = value
                wb.save(path)
                agent = getattr(_thread_local, 'current_agent', None) or _brain._current_agent
                _brain._after_file_write(path, "modified", agent.agent_id if agent else "main")
                return _ok({"path": path, "action": action, "cell": cell_ref, "sheet": sheet_name, "status": "edited"})

            elif action == "add_row":
                sheet_name = params.get("sheet", wb.sheetnames[0])
                values = params.get("values", [])
                if not values:
                    return _err("edit_document: 'values' array required")
                if sheet_name not in wb.sheetnames:
                    return _err(f"Sheet '{sheet_name}' not found. Available: {wb.sheetnames}")
                ws = wb[sheet_name]
                ws.append(values)
                wb.save(path)
                agent = getattr(_thread_local, 'current_agent', None) or _brain._current_agent
                _brain._after_file_write(path, "modified", agent.agent_id if agent else "main")
                return _ok({"path": path, "action": action, "sheet": sheet_name, "row_added": len(values), "status": "edited"})

            else:
                return _err(f"edit_document: unsupported action '{action}' for XLSX. Use: update_cell, add_row")

        elif ext == ".pptx":
            try:
                from pptx import Presentation
            except ImportError:
                return _err("Install python-pptx: pip3 install python-pptx")
            prs = Presentation(path)

            if action == "update_slide":
                slide_index = int(params.get("slide_index", 1))
                if slide_index < 1 or slide_index > len(prs.slides):
                    return _err(f"Slide index {slide_index} out of range (1-{len(prs.slides)})")
                slide = prs.slides[slide_index - 1]
                title = params.get("title")
                body = params.get("body")
                if title and slide.shapes.title:
                    slide.shapes.title.text = title
                if body:
                    for shape in slide.shapes:
                        if shape.has_text_frame and shape != slide.shapes.title:
                            shape.text_frame.text = body
                            break
                prs.save(path)
                agent = getattr(_thread_local, 'current_agent', None) or _brain._current_agent
                _brain._after_file_write(path, "modified", agent.agent_id if agent else "main")
                return _ok({"path": path, "action": action, "slide_index": slide_index, "status": "edited"})

            elif action == "add_slide":
                title = params.get("title", "New Slide")
                body = params.get("body", "")
                slide_layout = prs.slide_layouts[1]
                slide = prs.slides.add_slide(slide_layout)
                slide.shapes.title.text = title
                if body:
                    slide.placeholders[1].text = body
                prs.save(path)
                agent = getattr(_thread_local, 'current_agent', None) or _brain._current_agent
                _brain._after_file_write(path, "modified", agent.agent_id if agent else "main")
                return _ok({"path": path, "action": action, "slide_count": len(prs.slides), "status": "edited"})

            else:
                return _err(f"edit_document: unsupported action '{action}' for PPTX. Use: update_slide, add_slide")

        else:
            return _err(f"edit_document: unsupported format '{ext}'. Supported: .docx, .xlsx, .xls, .pptx")
    except ImportError as e:
        return _err(str(e))
    except Exception as e:
        return _err(f"edit_document: {e}")


def tool_list_directory(args: dict) -> str:
    import brain as _brain
    node_result = _brain._route_to_node("list_directory", args)
    if node_result is not None:
        return node_result
    path = args.get("path", ".")
    pattern = args.get("pattern")
    recursive = args.get("recursive", False)
    try:
        path = os.path.expanduser(path)
        if not os.path.isabs(path):
            path = os.path.abspath(path)

        if pattern:
            if recursive or "**" in pattern:
                full_pattern = os.path.join(path, pattern)
                entries = globmod.glob(full_pattern, recursive=True)
            else:
                full_pattern = os.path.join(path, pattern)
                entries = globmod.glob(full_pattern)
        elif recursive:
            entries = []
            for root, dirs, files in os.walk(path):
                # Skip hidden dirs
                dirs[:] = [d for d in dirs if not d.startswith(".")]
                for f in files:
                    if not f.startswith("."):
                        entries.append(os.path.join(root, f))
        else:
            entries = [os.path.join(path, e) for e in os.listdir(path)]

        results = []
        for entry in sorted(entries)[:500]:
            try:
                st = os.stat(entry)
                is_dir = os.path.isdir(entry)
                results.append({
                    "name": os.path.relpath(entry, path),
                    "type": "directory" if is_dir else "file",
                    "size": st.st_size if not is_dir else None,
                })
            except OSError:
                results.append({"name": os.path.relpath(entry, path), "type": "unknown"})

        return _ok({"path": path, "count": len(results), "entries": results})
    except Exception as e:
        return _err(f"list_directory: {e}")


def tool_search_files(args: dict) -> str:
    pattern = args.get("pattern", "")
    path = args.get("path", ".")
    file_glob = args.get("glob")
    case_insensitive = args.get("case_insensitive", False)
    max_results = args.get("max_results", 50)
    try:
        path = os.path.expanduser(path)
        if not os.path.isabs(path):
            path = os.path.abspath(path)

        flags = re.IGNORECASE if case_insensitive else 0
        regex = re.compile(pattern, flags)

        matches = []
        files_searched = 0

        if os.path.isfile(path):
            file_list = [path]
        else:
            file_list = []
            for root, dirs, files in os.walk(path):
                dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("node_modules", "__pycache__", ".git")]
                for f in files:
                    if f.startswith("."):
                        continue
                    fp = os.path.join(root, f)
                    if file_glob and not fnmatch.fnmatch(f, file_glob):
                        continue
                    file_list.append(fp)

        for fp in file_list:
            if len(matches) >= max_results:
                break
            files_searched += 1
            try:
                with open(fp, "r", errors="replace") as fh:
                    for lineno, line in enumerate(fh, 1):
                        if regex.search(line):
                            matches.append({
                                "file": os.path.relpath(fp, path) if not os.path.isfile(path) else fp,
                                "line": lineno,
                                "text": line.rstrip()[:500],
                            })
                            if len(matches) >= max_results:
                                break
            except (OSError, UnicodeDecodeError):
                continue

        return _ok({"pattern": pattern, "path": path, "files_searched": files_searched,
                     "match_count": len(matches), "matches": matches})
    except re.error as e:
        return _err(f"search_files: invalid regex: {e}")
    except Exception as e:
        return _err(f"search_files: {e}")


def _strip_ansi(text: str) -> str:
    """Remove all ANSI escape sequences, control chars, and normalize whitespace."""
    text = re.sub(r"\033\[[0-9;]*[a-zA-Z]", "", text)
    text = re.sub(r"\033\[\?[0-9;]*[a-zA-Z]", "", text)
    text = re.sub(r"\033\([A-Z]", "", text)
    text = re.sub(r"\033][^\a]*\a", "", text)  # OSC sequences
    text = re.sub(r"\r", "", text)  # Carriage returns
    text = text.replace("\t", "    ")  # Expand tabs
    return text


def _build_shell_command(command: str) -> tuple[list | str, bool]:
    """Build the shell invocation based on execute_command config.

    Returns (cmd, shell_flag) for subprocess.Popen.
    If login_shell is True, wraps the command in a login shell invocation
    so that ~/.zprofile, ~/.zshrc etc. are sourced (giving full PATH).
    """
    import brain as _brain
    _exec_cfg = _brain.get_tool_config().get("execute_command", {})
    use_login_shell = _exec_cfg.get("login_shell", True)
    if use_login_shell:
        shell_path = _exec_cfg.get("shell_path", "") or os.environ.get("SHELL", "/bin/zsh")
        return [shell_path, "-l", "-c", command], False
    return command, True


def _streaming_execute_command(command: str, timeout: int, cwd: str | None,
                               event_callback, tool_use_id: str) -> str:
    """Execute command with streaming output via event_callback."""
    import brain as _brain
    env = os.environ.copy()
    env["TERM"] = "dumb"
    env["NO_COLOR"] = "1"
    env["PAGER"] = "cat"
    env["COLUMNS"] = "200"
    env["LINES"] = "50"

    shell_cmd, shell_flag = _build_shell_command(command)
    proc = subprocess.Popen(
        shell_cmd, shell=shell_flag, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL, cwd=cwd, env=env,
        start_new_session=True,
    )
    output_lines = []
    import io
    deadline = time.time() + timeout
    try:
        # Read stdout line by line, emitting events
        for raw_line in iter(proc.stdout.readline, b''):
            if time.time() > deadline:
                import signal as sig
                try:
                    os.killpg(proc.pid, sig.SIGKILL)
                except OSError:
                    proc.kill()
                proc.wait(timeout=5)
                line_text = _strip_ansi(raw_line.decode("utf-8", errors="replace"))
                output_lines.append(line_text)
                output = "".join(output_lines)
                if len(output) > 50000:
                    output = output[:50000] + "\n... (truncated)"
                return _err(f"execute_command: timed out after {timeout}s\n{output}")
            line_text = _strip_ansi(raw_line.decode("utf-8", errors="replace"))
            output_lines.append(line_text)
            if event_callback:
                event_callback("tool_output", {
                    "tool_use_id": tool_use_id,
                    "line": line_text.rstrip("\n"),
                })
        proc.stdout.close()
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        import signal as sig
        try:
            os.killpg(proc.pid, sig.SIGKILL)
        except OSError:
            proc.kill()
        proc.wait(timeout=5)

    output = "".join(output_lines)
    stderr_data = proc.stderr.read() if proc.stderr else b""
    proc.stderr.close()
    if stderr_data:
        err_text = _strip_ansi(stderr_data.decode("utf-8", errors="replace"))
        output += ("\n--- stderr ---\n" + err_text) if output else err_text
    if len(output) > 50000:
        output = output[:50000] + "\n... (truncated)"
    output = _brain._gdpr_anon_tool_text(output, "execute_command:stdout")
    return _ok({"command": command, "exit_code": proc.returncode, "output": output})


def tool_execute_command(args: dict) -> str:
    import brain as _brain
    node_result = _brain._route_to_node("execute_command", args)
    if node_result is not None:
        return node_result
    command = args.get("command", "")
    cwd = args.get("cwd")
    # Read default timeout from tools_config (per-call timeout still overrides)
    _exec_cfg = _brain.get_tool_config().get("execute_command", {})
    default_timeout = _exec_cfg.get("timeout", 15)
    timeout = args.get("timeout", default_timeout)
    # Check banned commands
    banned = _exec_cfg.get("banned_commands", [])
    for b in banned:
        if b and b in command:
            return _err(f"execute_command: command contains banned pattern '{b}'")
    try:
        if cwd:
            cwd = os.path.expanduser(cwd)
        else:
            # Default cwd = session artifact folder (matches python_exec) so
            # outputs land in the Artifacts panel without the model having to
            # guess the path. Falls back to Brain's cwd when there's no
            # session (background jobs, etc.).
            session_id = getattr(_thread_local, 'current_session_id', None)
            agent = getattr(_thread_local, 'current_agent', None) or _brain._current_agent
            if session_id and agent:
                folder = _get_artifact_session_folder(session_id)
                cwd = os.path.join(_brain.AGENTS_DIR, agent.agent_id, "artifacts", folder)
                os.makedirs(cwd, exist_ok=True)

        # Snapshot artifact folder if cwd lands inside it — files appearing
        # post-exec auto-register as artifacts (mirrors tool_python_exec). We
        # only snapshot when cwd IS the artifact folder; commands run outside
        # it shouldn't pollute the artifact panel.
        _artifact_cwd = None
        _pre_files: set[str] = set()
        _session_id = getattr(_thread_local, 'current_session_id', None)
        _agent = getattr(_thread_local, 'current_agent', None) or _brain._current_agent
        if cwd and _session_id and _agent:
            _expected = os.path.join(_brain.AGENTS_DIR, _agent.agent_id, "artifacts",
                                     _get_artifact_session_folder(_session_id))
            if os.path.realpath(cwd) == os.path.realpath(_expected):
                _artifact_cwd = _expected
                try:
                    _pre_files = set(os.listdir(_artifact_cwd))
                except OSError:
                    _pre_files = set()

        # Use streaming version if event_callback is available
        ecb = getattr(_thread_local, 'event_callback', None)
        tuid = getattr(_thread_local, 'tool_use_id', None)
        if ecb and tuid:
            _res = _streaming_execute_command(command, timeout, cwd, ecb, tuid)
            _register_new_artifacts(_artifact_cwd, _pre_files, _agent)
            _stray = _stray_write_warning(command, _artifact_cwd)
            if _stray:
                _res = _append_to_tool_result(_res, _stray)
            return _res

        # Force non-interactive environment
        env = os.environ.copy()
        env["TERM"] = "dumb"
        env["NO_COLOR"] = "1"
        env["PAGER"] = "cat"
        env["COLUMNS"] = "200"
        env["LINES"] = "50"

        shell_cmd, shell_flag = _build_shell_command(command)
        proc = subprocess.Popen(
            shell_cmd, shell=shell_flag, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL, cwd=cwd, env=env,
            start_new_session=True,  # own process group so we can kill the tree
        )
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            # Kill the entire process group
            import signal as sig
            try:
                os.killpg(proc.pid, sig.SIGKILL)
            except OSError:
                proc.kill()
            stdout, stderr = proc.communicate(timeout=5)
            output = _strip_ansi(stdout.decode("utf-8", errors="replace"))
            if stderr:
                output += "\n--- stderr ---\n" + _strip_ansi(stderr.decode("utf-8", errors="replace"))
            if len(output) > 50000:
                output = output[:50000] + "\n... (truncated)"
            return _err(f"execute_command: timed out after {timeout}s (partial output below). Use non-interactive commands, e.g. 'top -l 1' not 'top'.\n{output}")

        output = _strip_ansi(stdout.decode("utf-8", errors="replace"))
        if stderr:
            err_text = _strip_ansi(stderr.decode("utf-8", errors="replace"))
            output += ("\n--- stderr ---\n" + err_text) if output else err_text
        if len(output) > 50000:
            output = output[:50000] + "\n... (truncated)"
        # Transparent-anonymisation seam (same rationale as tool_python_exec):
        # `cat /tmp/brain-attachments/.../report.docx` or `grep -r foo /tmp`
        # could surface raw PII bytes to the LLM. Wrap stdout through the
        # same mapping that read_document uses.
        output = _brain._gdpr_anon_tool_text(output, "execute_command:stdout")
        _new_artifacts = _register_new_artifacts(_artifact_cwd, _pre_files, _agent)
        result = {"command": command, "exit_code": proc.returncode, "output": output}
        if _new_artifacts:
            result["artifacts"] = _new_artifacts
        _stray = _stray_write_warning(command, _artifact_cwd)
        if _stray:
            result["output"] = (result.get("output") or "") + _stray
        return _ok(result)
    except Exception as e:
        return _err(f"execute_command: {e}")


def _register_new_artifacts(artifact_cwd: str | None, pre_files: set,
                            agent) -> list[str]:
    """Diff artifact_cwd against pre_files; register each new file via
    `_after_file_write` so it shows in the Artifacts panel. Returns the list
    of newly registered basenames (for inclusion in the tool result)."""
    import brain as _brain
    if not artifact_cwd or not agent:
        return []
    try:
        post_files = set(os.listdir(artifact_cwd))
    except OSError:
        return []
    new_files = sorted(post_files - pre_files)
    if not new_files:
        return []
    agent_id = agent.agent_id
    created = []
    for fname in new_files:
        fpath = os.path.join(artifact_cwd, fname)
        if os.path.isfile(fpath):
            try:
                _brain._after_file_write(fpath, "created", agent_id)
                created.append(fname)
            except Exception:
                pass
    return created


# Absolute path literals in python_exec code / execute_command commands.
# We don't redirect (the user may legitimately want an absolute write) — we
# only WARN, so the model self-corrects on the next turn. See _stray_write_warning.
_ABS_PATH_RE = re.compile(r"""['"`(\s=](/(?:[\w.\-+ ]+/)*[\w.\-+]+\.[\w]{1,8})\b""")


def _stray_write_warning(text: str, artifact_dir: str | None) -> str:
    """Return a warning string when `text` (python_exec code or a shell command)
    references absolute file paths that point OUTSIDE the session artifact
    folder, OR "" when there's nothing to warn about.

    Rationale: python_exec / execute_command run with cwd = artifact folder and
    only diff THAT folder for new artifacts. If the model's script does
    `doc.save("/Users/.../repo/report.docx")` (an absolute path it invented from
    the old, misleading "Current working directory" line), the file lands outside
    the artifact folder, never appears in the post-exec diff, and is invisible to
    the Artifacts panel — while the model believes it succeeded. We surface that
    back into the tool result so the model re-saves with a relative filename.

    Non-invasive by design (user chose "warn, don't redirect"): an absolute path
    the user explicitly asked for is left alone; the warning just informs.
    """
    if not text or not artifact_dir:
        return ""
    try:
        art_real = os.path.realpath(artifact_dir)
    except OSError:
        return ""
    stray: list[str] = []
    seen: set[str] = set()
    for m in _ABS_PATH_RE.finditer(text):
        p = m.group(1)
        if p in seen:
            continue
        seen.add(p)
        # Inside the artifact folder? fine. /tmp, /dev, /proc etc. are not
        # user-facing artifact writes — skip them (avoid noise on temp files).
        if p.startswith(("/tmp/", "/private/tmp/", "/var/", "/dev/", "/proc/",
                         "/usr/", "/etc/", "/System/", "/Library/")):
            continue
        try:
            if os.path.realpath(p).startswith(art_real):
                continue
        except OSError:
            pass
        stray.append(p)
    if not stray:
        return ""
    paths = ", ".join(stray[:5])
    return (
        f"\n\n⚠️ WARNING: this wrote to an absolute path OUTSIDE your session "
        f"artifact folder ({paths}). Files written there do NOT appear in the "
        f"Artifacts panel and the user cannot see or download them. Re-save the "
        f"output using a RELATIVE filename (just the filename, e.g. "
        f"`report.docx`) so it lands in your artifact folder — unless the user "
        f"explicitly asked for that absolute path."
    )


def _append_to_tool_result(res_json: str, suffix: str) -> str:
    """Append `suffix` to the `output` field of a JSON tool result string
    (as produced by `_ok`). Falls back to a raw string append if the result
    isn't the expected JSON-object-with-output shape."""
    if not suffix:
        return res_json
    try:
        obj = json.loads(res_json)
        if isinstance(obj, dict):
            obj["output"] = (obj.get("output") or "") + suffix
            return json.dumps(obj, ensure_ascii=False)
    except (ValueError, TypeError):
        pass
    return res_json + suffix


def tool_python_exec(args: dict) -> str:
    """Execute Python code in an isolated subprocess with artifact folder as cwd."""
    import brain as _brain
    code = args.get("code", "")
    if not code.strip():
        return _err("python_exec: no code provided")

    _cfg = _brain.get_tool_config().get("python_exec", {})
    timeout = args.get("timeout", _cfg.get("timeout", 30))
    max_output = _cfg.get("max_output_chars", 50000)
    venv_path = _cfg.get("venv_path", "")

    # Working dir = session artifact folder so files written by code become artifacts
    session_id = getattr(_thread_local, 'current_session_id', None)
    agent = getattr(_thread_local, 'current_agent', None) or _brain._current_agent
    if session_id and agent:
        folder = _get_artifact_session_folder(session_id)
        work_dir = os.path.join(_brain.AGENTS_DIR, agent.agent_id, "artifacts", folder)
    else:
        import tempfile
        work_dir = os.path.join(tempfile.gettempdir(), "brain-pyexec")
    os.makedirs(work_dir, exist_ok=True)

    # Snapshot existing files before execution
    pre_files = set(os.listdir(work_dir))

    # Save script as a numbered artifact (persisted for reuse)
    counter = 1
    while os.path.exists(os.path.join(work_dir, f"script_{counter}.py")):
        counter += 1
    script_name = f"script_{counter}.py"
    script_path = os.path.join(work_dir, script_name)
    with open(script_path, "w") as f:
        f.write(code)
    agent_id = (agent.agent_id if agent else "main")
    _brain._after_file_write(script_path, "created", agent_id)

    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONUNBUFFERED"] = "1"
    if venv_path and os.path.isdir(venv_path):
        env["PYTHONPATH"] = venv_path + ((":" + env.get("PYTHONPATH", "")) if env.get("PYTHONPATH") else "")

    try:
        proc = subprocess.Popen(
            [sys.executable, script_path],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL, cwd=work_dir, env=env,
            start_new_session=True,
        )
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            import signal as sig
            try:
                os.killpg(proc.pid, sig.SIGKILL)
            except OSError:
                proc.kill()
            stdout, stderr = proc.communicate(timeout=5)
            output = stdout.decode("utf-8", errors="replace")
            if stderr:
                output += "\n--- stderr ---\n" + stderr.decode("utf-8", errors="replace")
            if len(output) > max_output:
                output = output[:max_output] + "\n... (truncated)"
            return _err(f"python_exec: timed out after {timeout}s\n{output}")

        output = stdout.decode("utf-8", errors="replace")
        if stderr:
            err_text = stderr.decode("utf-8", errors="replace")
            # Clean tracebacks: replace script path, remove echoed source lines
            err_text = err_text.replace(script_path, "<python_exec>")
            err_lines = err_text.splitlines()
            cleaned = []
            skip_next = False
            for line in err_lines:
                if '<python_exec>' in line:
                    cleaned.append(line)
                    skip_next = True
                elif skip_next and line.startswith('    ') and not line.strip().startswith('File '):
                    skip_next = False
                    continue
                else:
                    skip_next = False
                    cleaned.append(line)
            err_text = "\n".join(cleaned)
            output += ("\n--- stderr ---\n" + err_text) if output else err_text
        if len(output) > max_output:
            output = output[:max_output] + "\n... (truncated)"

        # Transparent-anonymisation seam: any PII the script printed via
        # stdout reaches the LLM through this `output` field. Wrap it
        # through the same `_gdpr_anon_tool_text` helper that read_document
        # / read_file use — closes the python_exec end-around where the
        # model bypasses read_document by `open(path, 'rb')` and printing
        # the bytes back. Note: artifacts written to disk get their own
        # post-write deanonymisation pass via `_after_file_write` →
        # `make_gdpr_after_file_write_cb`, so a script that writes a file
        # with restored values still gets surfaced to the user correctly.
        output = _brain._gdpr_anon_tool_text(output, "python_exec:stdout")

        result = {"exit_code": proc.returncode, "output": output, "script": script_name}

        # Warn when the script saved to an absolute path outside the artifact
        # folder — such files never show up in the post-exec diff below and are
        # invisible to the Artifacts panel (the recurring "model wrote to repo
        # root" failure). Non-invasive: we warn, we don't move the file.
        _stray = _stray_write_warning(code, work_dir if (session_id and agent) else None)
        if _stray:
            result["output"] = (result.get("output") or "") + _stray

        # Register any new files created by the script as artifacts
        post_files = set(os.listdir(work_dir))
        new_files = sorted(post_files - pre_files - {script_name})
        if new_files and agent:
            created = []
            for fname in new_files:
                fpath = os.path.join(work_dir, fname)
                if os.path.isfile(fpath):
                    _brain._after_file_write(fpath, "created", agent_id)
                    created.append(fname)
            if created:
                result["artifacts"] = created

        # Always save stdout as an artifact when the script didn't write any files
        if output and proc.returncode == 0 and not new_files and agent:
            try:
                artifact_path = os.path.join(work_dir, "output.txt")
                counter = 1
                while os.path.exists(artifact_path):
                    artifact_path = os.path.join(work_dir, f"output_{counter}.txt")
                    counter += 1
                with open(artifact_path, "w") as af:
                    af.write(output)
                _brain._after_file_write(artifact_path, "created", agent_id)
                result["artifacts"] = [os.path.basename(artifact_path)]
                # For large outputs, replace inline data with a reference so the
                # summariser doesn't ingest a megabyte of stdout. Small outputs
                # stay inline so the summariser can describe them meaningfully.
                if len(output) > 1000:
                    lines = output.splitlines()
                    result["output"] = (
                        f"Output saved as artifact {os.path.basename(artifact_path)} "
                        f"({len(lines)} lines, {len(output):,} chars). "
                        f"The user can view it directly. Summarize what was computed, do NOT repeat the data."
                    )
            except Exception:
                pass

        return _ok(result)
    except Exception as e:
        return _err(f"python_exec: {e}")
