"""text_diff — deterministic file comparison for text/code/config files.

WHY (the data-file diff gap, 2026-07-13): xlsx_diff covers tabular data
(xlsx/csv/json/xml grids), but there was NO deterministic way to compare two
source/config/text files — models fell back to reading both files into chat
(token bloat, misread lines) or python_exec. Like the xlsx toolset, the model
supplies only intent (two paths), the server computes the diff:

  text_diff — unified diff + stats for any two text files; mode='json' diffs
              JSON structurally (path→value, order-independent for objects);
              out='name.html' saves a side-by-side review artifact.

Wired per the 4-site rule (TOOL_DEFINITIONS / TOOL_GROUPS / impl here /
TOOL_DISPATCH). Reaches brain runtime via lazy `import brain as _brain`.
"""

from __future__ import annotations

import difflib
import json
import os

from engine.context import get_request_context
from engine.tool_exec import _ok, _err

DIFF_MAX_FILE_MB = 10
DIFF_REPORT_MAX_LINES = 400
JSON_DETAIL_CAP = 200


def _read_text(path: str) -> str:
    size_mb = os.path.getsize(path) / (1024 * 1024)
    if size_mb > DIFF_MAX_FILE_MB:
        raise ValueError(
            f"{os.path.basename(path)} is {size_mb:.0f} MB (> "
            f"{DIFF_MAX_FILE_MB} MB) — text_diff is for source/config/text "
            f"files; use xlsx_diff for data files")
    with open(path, "rb") as f:
        head = f.read(8192)
    if b"\x00" in head:
        ext = os.path.splitext(path)[1].lower()
        hint = (" — use xlsx_diff for spreadsheets/data files"
                if ext in (".xlsx", ".xlsm", ".xls", ".ods") else "")
        raise ValueError(f"{os.path.basename(path)} is binary{hint}")
    with open(path, encoding="utf-8-sig", errors="replace") as f:
        return f.read()


def _flatten_json_paths(obj, prefix: str = "", out: dict | None = None) -> dict:
    """{json.path[i].key: scalar} — object keys are order-independent, array
    positions are part of the path (an order change in a list IS a change)."""
    if out is None:
        out = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            _flatten_json_paths(v, f"{prefix}.{k}" if prefix else str(k), out)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            _flatten_json_paths(v, f"{prefix}[{i}]", out)
    else:
        out[prefix or "(root)"] = obj
    return out


def _parse_json_side(path: str, text: str):
    if os.path.splitext(path)[1].lower() in (".jsonl", ".ndjson"):
        return [json.loads(ln) for ln in text.splitlines() if ln.strip()]
    return json.loads(text)


def _fmt_val(v) -> str:
    s = json.dumps(v, ensure_ascii=False) if not isinstance(v, str) else v
    return s if len(s) <= 120 else s[:117] + "…"


def _json_structural_diff(la: str, lb: str, a, b):
    """(summary_lines, details) — details = [(path, value_a, value_b)] with
    None for absent sides (added/removed)."""
    fa, fb = _flatten_json_paths(a), _flatten_json_paths(b)
    added = [p for p in fb if p not in fa]
    removed = [p for p in fa if p not in fb]
    changed = [p for p in fa if p in fb and fa[p] != fb[p]]
    lines = [f"- paths: {la} {len(fa)} / {lb} {len(fb)} — "
             f"{len(added)} added, {len(removed)} removed, "
             f"{len(changed)} changed"]
    details = ([(p, None, fb[p]) for p in added]
               + [(p, fa[p], None) for p in removed]
               + [(p, fa[p], fb[p]) for p in changed])
    for p, va, vb in details[:JSON_DETAIL_CAP]:
        if va is None and p in fb:
            lines.append(f"  + `{p}` = {_fmt_val(vb)}")
        elif vb is None and p in fa:
            lines.append(f"  - `{p}` (war {_fmt_val(va)})")
        else:
            lines.append(f"  ~ `{p}`: {_fmt_val(va)} → {_fmt_val(vb)}")
    if len(details) > JSON_DETAIL_CAP:
        lines.append(f"  … {len(details) - JSON_DETAIL_CAP} more (pass "
                     f"out='diff.txt' for the full list)")
    return lines, details


def tool_text_diff(args: dict) -> str:
    import brain as _brain
    from engine.tools.xlsx_tools import _resolve_input_path
    pa, pb = args.get("path_a") or "", args.get("path_b") or ""
    if not pa or not pb:
        return _err("text_diff: 'path_a' and 'path_b' are required")
    mode = (args.get("mode") or "").strip().lower()
    try:
        context = max(0, min(20, int(args.get("context") or 3)))
    except (TypeError, ValueError):
        context = 3
    try:
        ra, rb = _resolve_input_path(pa), _resolve_input_path(pb)
        for p, rp in ((pa, ra), (pb, rb)):
            if not os.path.isfile(rp):
                return _err(f"text_diff: File not found: {p}")
        ta, tb = _read_text(ra), _read_text(rb)
        la, lb = os.path.basename(ra), os.path.basename(rb)
        parts = [f"# Diff: {la} ↔ {lb}"]
        out_name = (args.get("out") or "").strip()
        full_text = None  # what out='name.txt' saves

        if mode == "json":
            try:
                lines, details = _json_structural_diff(
                    la, lb, _parse_json_side(ra, ta), _parse_json_side(rb, tb))
            except json.JSONDecodeError as e:
                return _err(f"text_diff: invalid JSON: {e}")
            n_diffs = len(details)
            parts.append("(struktureller JSON-Vergleich — Pfad → Wert)")
            parts.extend(lines)
            full_text = "\n".join(
                f"{p}\t{_fmt_val(va) if va is not None else ''}\t"
                f"{_fmt_val(vb) if vb is not None else ''}"
                for p, va, vb in details)
        else:
            a_lines = ta.splitlines(keepends=True)
            b_lines = tb.splitlines(keepends=True)
            diff = list(difflib.unified_diff(a_lines, b_lines,
                                             fromfile=la, tofile=lb,
                                             n=context))
            plus = sum(1 for d in diff
                       if d.startswith("+") and not d.startswith("+++"))
            minus = sum(1 for d in diff
                        if d.startswith("-") and not d.startswith("---"))
            hunks = sum(1 for d in diff if d.startswith("@@"))
            n_diffs = plus + minus
            ratio = difflib.SequenceMatcher(None, ta, tb).quick_ratio()
            parts.append(f"- lines: {la} {len(a_lines)} / {lb} {len(b_lines)} "
                         f"— +{plus} / -{minus} in {hunks} hunk(s), "
                         f"similarity {ratio:.0%}")
            if not diff:
                parts.append("Dateien sind inhaltlich identisch.")
            body = "".join(diff)
            shown = body.splitlines()
            if len(shown) > DIFF_REPORT_MAX_LINES:
                shown = shown[:DIFF_REPORT_MAX_LINES] + [
                    f"… gekürzt ({len(body.splitlines())} Diff-Zeilen gesamt "
                    f"— pass out='diff.html' for the full side-by-side)"]
            if diff:
                parts.append("```diff\n" + "\n".join(shown) + "\n```")
            full_text = body

        out_info = None
        if out_name and n_diffs:
            from engine.tools.file_tools import _enforce_artifact_path
            if not out_name.lower().endswith((".html", ".txt", ".diff", ".patch")):
                out_name += ".html"
            out_path, perr = _enforce_artifact_path(out_name, "text_diff")
            if perr:
                return perr
            if out_path.lower().endswith(".html"):
                hd = difflib.HtmlDiff(wrapcolumn=100)
                html = hd.make_file(ta.splitlines(), tb.splitlines(),
                                    la, lb, context=True, numlines=context)
                with open(out_path, "w", encoding="utf-8") as f:
                    f.write(html)
            else:
                with open(out_path, "w", encoding="utf-8") as f:
                    f.write(full_text or "")
            agent = get_request_context().current_agent
            _brain._after_file_write(
                out_path, "created", agent.agent_id if agent else "main")
            out_info = {"path": out_path}

        report = "\n".join(parts)
        report = _brain._gdpr_anon_tool_text(report, f"text_diff:{la},{lb}")
        res = {"differences": n_diffs, "report": report}
        if out_info:
            res["saved"] = out_info
        return _ok(res)
    except (ValueError, FileNotFoundError) as e:
        return _err(f"text_diff: {e}")
    except Exception as e:
        return _err(f"text_diff: {type(e).__name__}: {e}")
