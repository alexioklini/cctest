"""HTTP handlers for document classification.

Phase A endpoints — pure detection + audit surface. No enforcement here;
that lives in the GDPR/attachment seams in Phase B.

Routes (all authenticated; admin-only for config endpoints):
  POST   /v1/classification/scan-files       multipart, returns per-file result
  POST   /v1/classification/scan-folder      body {path}, server-side walk
  POST   /v1/classification/scan-project     body {agent_id, project_name}
  GET    /v1/classification/scans            list user's scan history
  GET    /v1/classification/scans/<id>       full scan detail
  GET    /v1/classification/scans/<id>.csv   CSV export
  DELETE /v1/classification/scans/<id>       cleanup
  GET    /v1/classification/config           admin: keyword + regex config
  POST   /v1/classification/config           admin: save config
"""

from __future__ import annotations

import csv
import io
import json
import os
import tempfile
import time
import uuid
import sqlite3
from urllib.parse import urlparse, parse_qs

import brain as engine
from engine import classification as cls
from server_lib.db import ClassificationDB
from server_lib import pathsafe


# Cap on persisted evidence size per scan (50KB JSON)
_MAX_EVIDENCE_BYTES = 50 * 1024
# Cap on files per scan (server-side limit, separate from UI hint)
_MAX_FILES_PER_SCAN = 500
# Cap on raw text per file (1 MB plain-text after conversion)
_MAX_TEXT_BYTES = 1 * 1024 * 1024


def _config() -> dict:
    """Pull the live config dict — same path the GDPR scanner uses."""
    try:
        from server import server_config  # late import to avoid cycle
        return server_config or {}
    except Exception:
        return {}


def _safe_root_allowlist(user_id: str) -> list[str]:
    """Roots a folder scan may walk under, per user.

    Mirrors the project input-folders rule: only paths inside repo, agents/,
    cwd, or any project input_folders[] the user can see. Realpath-normalized
    so /tmp ↔ /private/tmp doesn't break the prefix check.
    """
    roots: set[str] = set()
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    roots.add(os.path.realpath(repo_root))
    roots.add(os.path.realpath(os.path.join(repo_root, "agents")))
    roots.add(os.path.realpath(os.getcwd()))
    # Project input_folders[] for projects this user can see
    try:
        for agent_id in os.listdir(os.path.join(repo_root, "agents")):
            adir = os.path.join(repo_root, "agents", agent_id)
            if not os.path.isdir(adir):
                continue
            try:
                projs = engine.ProjectManager.list_projects(agent_id, user_id=user_id)
            except Exception:
                continue
            for p in projs or []:
                for f in (p.get("input_folders") or []):
                    pth = f.get("path") if isinstance(f, dict) else None
                    if pth and os.path.isdir(pth):
                        roots.add(os.path.realpath(pth))
    except Exception:
        pass
    return sorted(roots)


def _validate_scan_path(path: str, user_id: str) -> tuple[str | None, str | None]:
    """Returns (real_path, error). error=None on success.

    Hard-denylist + must-be-under-an-allowed-root. Skeleton lives in
    server_lib.pathsafe; the allowed roots are this site's policy.
    """
    return pathsafe.validate_path(
        path,
        allowed_roots=_safe_root_allowlist(user_id),
        must_exist=True,
    )


def _extract_text(src: str) -> tuple[str, list[str], str | None]:
    """Best-effort text + per-page list extraction.

    Returns (full_text, page_texts, error).

    Uses doc_convert.convert_one() for supported types so we get the same
    markdown the rest of Brain sees. For pure-text inputs (.md, .txt, .html)
    we read directly. Page splitting falls back to form-feed / known
    page-break markers when present.
    """
    if not src or not os.path.isfile(src):
        return "", [], f"not a file: {src}"
    ext = os.path.splitext(src)[1].lower()
    try:
        if ext in (".md", ".markdown", ".txt", ".html", ".htm"):
            with open(src, "r", encoding="utf-8", errors="replace") as f:
                text = f.read(_MAX_TEXT_BYTES + 1)
            pages = _split_pages(text)
            return text[:_MAX_TEXT_BYTES], pages, None
        # Binary/tabular types via doc_convert (csv now flows through
        # _extract_csv for one consistent table rendering).
        from engine.doc_convert import convert_one, SUPPORTED_EXTS
        if ext not in SUPPORTED_EXTS:
            return "", [], f"unsupported extension: {ext}"
        md_path, err = convert_one(src)
        if err or not md_path or not os.path.isfile(md_path):
            return "", [], err or "conversion failed"
        with open(md_path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read(_MAX_TEXT_BYTES + 1)
        # Skip the brain-source frontmatter comment if present
        if text.startswith("<!--"):
            end = text.find("-->")
            if end > 0:
                text = text[end + 3:].lstrip()
        pages = _split_pages(text)
        return text[:_MAX_TEXT_BYTES], pages, None
    except Exception as e:
        return "", [], f"extract failed: {e}"


def _split_pages(text: str) -> list[str]:
    """Split markdown text into pages using common markers."""
    if not text:
        return []
    # Form-feed (PDF extractors often use this)
    if "\f" in text:
        return [p for p in text.split("\f") if p.strip()]
    # `--- page N ---` style — markitdown sometimes emits this
    import re as _re
    parts = _re.split(r"\n-{3,}\s*page\s+\d+\s*-{3,}\n", text, flags=_re.IGNORECASE)
    if len(parts) > 1:
        return [p for p in parts if p.strip()]
    # Fallback — single "page"
    return [text] if text.strip() else []


def _scan_one_file(path: str, *, original_name: str = "") -> dict:
    """Run detector on a single file. Returns per-file result dict."""
    name = original_name or os.path.basename(path)
    text, pages, err = _extract_text(path)
    if err:
        return {
            "filename": name,
            "error": err,
            "final_level": "unmarked",
            "marker_level": None,
            "marker_meta": {},
            "marker_evidence": [],
            "filename_hint": None,
            "mismatch": None,
            "heuristic_level": "public",
            "keyword_hits": {},
            "pii_count": 0,
        }
    cfg = _config()
    pdf_path = path if path.lower().endswith(".pdf") else ""
    result = cls.detect_with_pii(text, filename=name, page_texts=pages,
                                  pdf_path=pdf_path, cfg=cfg)
    return {
        "filename": name,
        "error": None,
        "final_level": result["final_level"],
        "marker_level": result["marker_level"],
        "marker_meta": result["marker_meta"],
        "marker_evidence": result["marker_evidence"],
        "filename_hint": result["filename_hint"],
        "mismatch": result["mismatch"],
        "heuristic_level": result["content_signals"]["heuristic_level"],
        "keyword_hits": result["content_signals"]["keyword_hits"],
        "pii_count": len(result["content_signals"].get("pii_findings") or []),
    }


def _summarise(results: list[dict]) -> dict:
    by_level = {"public": 0, "internal": 0, "confidential": 0, "strict": 0, "unmarked": 0}
    mismatch_count = 0
    error_count = 0
    for r in results:
        lvl = r.get("final_level") or "unmarked"
        by_level[lvl] = by_level.get(lvl, 0) + 1
        if r.get("mismatch"):
            mismatch_count += 1
        if r.get("error"):
            error_count += 1
    return {
        "total": len(results),
        "by_level": by_level,
        "mismatch_count": mismatch_count,
        "error_count": error_count,
    }


def _trim_evidence(results: list[dict]) -> str:
    """JSON-encode results, drop heaviest fields if over the cap."""
    payload = json.dumps(results, ensure_ascii=False)
    if len(payload.encode("utf-8")) <= _MAX_EVIDENCE_BYTES:
        return payload
    # Drop marker_evidence excerpts beyond the first, and keyword_hits beyond 3 per level
    trimmed = []
    for r in results:
        rr = dict(r)
        ev = rr.get("marker_evidence") or []
        rr["marker_evidence"] = ev[:1]
        kh = rr.get("keyword_hits") or {}
        rr["keyword_hits"] = {k: v[:3] for k, v in kh.items()}
        trimmed.append(rr)
    payload = json.dumps(trimmed, ensure_ascii=False)
    if len(payload.encode("utf-8")) <= _MAX_EVIDENCE_BYTES:
        return payload
    # Last resort: keep only summary fields per file
    minimal = [
        {"filename": r.get("filename"),
         "final_level": r.get("final_level"),
         "marker_level": r.get("marker_level"),
         "mismatch_severity": (r.get("mismatch") or {}).get("severity") if r.get("mismatch") else None,
         "error": r.get("error")}
        for r in results
    ]
    return json.dumps(minimal, ensure_ascii=False)


# ── Multipart parser (copies from handlers/projects.py pattern, multi-file) ──

def _parse_multipart_files(content_type: str, body: bytes) -> tuple[list[dict], dict, str | None]:
    """Parse a browser multipart upload into a list of {name, bytes} +
    plain-text form fields. Returns (files, fields, error)."""
    boundary = None
    for part in content_type.split(";"):
        part = part.strip()
        if part.startswith("boundary="):
            boundary = part[len("boundary="):]
            break
    if not boundary:
        return [], {}, "no boundary in Content-Type"
    delimiter = f"--{boundary}".encode()
    parts = body.split(delimiter)
    files: list[dict] = []
    fields: dict[str, str] = {}
    for part in parts:
        if not part or part in (b"--\r\n", b"--"):
            continue
        if b"\r\n\r\n" in part:
            header_block, part_body = part.split(b"\r\n\r\n", 1)
        elif b"\n\n" in part:
            header_block, part_body = part.split(b"\n\n", 1)
        else:
            continue
        if part_body.endswith(b"\r\n"):
            part_body = part_body[:-2]
        header_text = header_block.decode("utf-8", errors="replace")
        field_name = None
        field_filename = None
        for line in header_text.split("\r\n"):
            line = line.strip()
            if line.lower().startswith("content-disposition:"):
                for item in line.split(";"):
                    item = item.strip()
                    if item.startswith("name="):
                        field_name = item[5:].strip('"').strip("'")
                    elif item.startswith("filename="):
                        field_filename = item[9:].strip('"').strip("'")
        if field_filename:
            files.append({"name": field_filename, "bytes": part_body})
        elif field_name:
            try:
                fields[field_name] = part_body.decode("utf-8", errors="replace")
            except Exception:
                fields[field_name] = ""
    return files, fields, None


class ClassificationHandlerMixin:
    """Add to BrainAgentHandler. Routes are dispatched from server.py."""

    # ── POST /v1/classification/scan-files ──────────────────────────────
    def _handle_classification_scan_files(self):
        user = self._require_auth()
        if not user:
            return
        ctype = self.headers.get("Content-Type", "")
        if not ctype.startswith("multipart/form-data"):
            self._send_json({"error": "multipart/form-data required"}, 400)
            return
        clen = int(self.headers.get("Content-Length", "0") or 0)
        if not clen:
            self._send_json({"error": "empty body"}, 400)
            return
        body = self.rfile.read(clen)
        files, fields, err = _parse_multipart_files(ctype, body)
        if err:
            self._send_json({"error": err}, 400)
            return
        if not files:
            self._send_json({"error": "no files in upload"}, 400)
            return
        if len(files) > _MAX_FILES_PER_SCAN:
            self._send_json({"error": f"too many files (max {_MAX_FILES_PER_SCAN})"}, 400)
            return
        persist = fields.get("persist", "1") in ("1", "true", "yes")

        # Drop each file to a tmp dir and scan
        tmp_dir = tempfile.mkdtemp(prefix="classification-")
        results: list[dict] = []
        try:
            for f in files:
                name = f["name"] or "upload"
                # Strip path components for safety
                safe_name = os.path.basename(name) or "upload"
                tmp_path = os.path.join(tmp_dir, safe_name)
                # If two uploads share a basename, suffix-disambiguate
                if os.path.exists(tmp_path):
                    stem, ext = os.path.splitext(safe_name)
                    i = 1
                    while os.path.exists(tmp_path):
                        tmp_path = os.path.join(tmp_dir, f"{stem} ({i}){ext}")
                        i += 1
                with open(tmp_path, "wb") as fh:
                    fh.write(f["bytes"])
                results.append(_scan_one_file(tmp_path, original_name=name))
        finally:
            # Best-effort cleanup
            try:
                for nm in os.listdir(tmp_dir):
                    try:
                        os.unlink(os.path.join(tmp_dir, nm))
                    except OSError:
                        pass
                os.rmdir(tmp_dir)
            except OSError:
                pass

        summary = _summarise(results)
        scan_id = ""
        if persist:
            scan_id = uuid.uuid4().hex[:12]
            ClassificationDB.insert(
                scan_id=scan_id,
                user_id=user.get("user_id") or user.get("username") or "",
                source_kind="upload",
                source_label=f"{len(results)} file(s)",
                file_count=len(results),
                summary_json=json.dumps(summary),
                evidence_json=_trim_evidence(results),
            )
        self._send_json({
            "scan_id": scan_id,
            "summary": summary,
            "results": results,
        })

    # ── POST /v1/classification/scan-folder ─────────────────────────────
    def _handle_classification_scan_folder(self):
        user = self._require_auth()
        if not user:
            return
        body = self._read_json() or {}
        path = body.get("path") or ""
        recursive = bool(body.get("recursive", True))
        persist = bool(body.get("persist", True))
        rp, err = _validate_scan_path(path, user.get("user_id") or "")
        if err:
            self._send_json({"error": err}, 400)
            return
        if not os.path.isdir(rp):
            self._send_json({"error": "path is not a directory"}, 400)
            return

        # Walk + collect supported files
        from engine.doc_convert import SUPPORTED_EXTS
        accept_exts = set(SUPPORTED_EXTS) | {".md", ".markdown", ".txt",
                                              ".html", ".htm", ".csv"}
        candidates: list[str] = []
        if recursive:
            for root, dirs, names in os.walk(rp):
                # Skip dot dirs + .brain-extracted
                dirs[:] = [d for d in dirs if not d.startswith(".")]
                for name in names:
                    if name.startswith("."):
                        continue
                    ext = os.path.splitext(name)[1].lower()
                    if ext in accept_exts:
                        candidates.append(os.path.join(root, name))
                    if len(candidates) >= _MAX_FILES_PER_SCAN:
                        break
                if len(candidates) >= _MAX_FILES_PER_SCAN:
                    break
        else:
            for name in os.listdir(rp):
                if name.startswith("."):
                    continue
                full = os.path.join(rp, name)
                if not os.path.isfile(full):
                    continue
                ext = os.path.splitext(name)[1].lower()
                if ext in accept_exts:
                    candidates.append(full)
                if len(candidates) >= _MAX_FILES_PER_SCAN:
                    break

        results = [_scan_one_file(p, original_name=os.path.relpath(p, rp))
                   for p in candidates]
        summary = _summarise(results)
        scan_id = ""
        if persist:
            scan_id = uuid.uuid4().hex[:12]
            ClassificationDB.insert(
                scan_id=scan_id,
                user_id=user.get("user_id") or user.get("username") or "",
                source_kind="folder",
                source_label=rp,
                file_count=len(results),
                summary_json=json.dumps(summary),
                evidence_json=_trim_evidence(results),
            )
        self._send_json({
            "scan_id": scan_id,
            "folder": rp,
            "summary": summary,
            "results": results,
        })

    # ── POST /v1/classification/scan-project ────────────────────────────
    def _handle_classification_scan_project(self):
        user = self._require_auth()
        if not user:
            return
        body = self._read_json() or {}
        agent_id = body.get("agent_id") or "main"
        project_name = body.get("project_name") or body.get("project") or ""
        if not project_name:
            self._send_json({"error": "project_name required"}, 400)
            return
        try:
            project = engine.ProjectManager.get_project(agent_id, project_name)
        except Exception as e:
            self._send_json({"error": f"project lookup failed: {e}"}, 400)
            return
        if not project:
            self._send_json({"error": "project not found"}, 404)
            return

        # Collect input_folders[] + ingested attachments
        from engine.doc_convert import SUPPORTED_EXTS
        accept_exts = set(SUPPORTED_EXTS) | {".md", ".markdown", ".txt",
                                              ".html", ".htm", ".csv"}
        candidates: list[tuple[str, str]] = []  # (abs_path, label)

        for f in (project.get("input_folders") or []):
            pth = f.get("path") if isinstance(f, dict) else None
            if not pth or not os.path.isdir(pth):
                continue
            recursive = bool(f.get("recursive", True))
            walker = os.walk(pth) if recursive else [(pth, [], os.listdir(pth))]
            for root, dirs, names in walker:
                if recursive:
                    dirs[:] = [d for d in dirs if not d.startswith(".")]
                for name in names:
                    if name.startswith("."):
                        continue
                    ext = os.path.splitext(name)[1].lower()
                    if ext in accept_exts:
                        full = os.path.join(root, name)
                        candidates.append((full, os.path.relpath(full, pth)))
                if len(candidates) >= _MAX_FILES_PER_SCAN:
                    break
            if len(candidates) >= _MAX_FILES_PER_SCAN:
                break

        # ingested/ folder
        ing_dir = project.get("ingested_dir") or ""
        if ing_dir and os.path.isdir(ing_dir):
            for name in os.listdir(ing_dir):
                if name.startswith("."):
                    continue
                full = os.path.join(ing_dir, name)
                if not os.path.isfile(full):
                    continue
                ext = os.path.splitext(name)[1].lower()
                if ext in accept_exts:
                    candidates.append((full, f"ingested/{name}"))
                if len(candidates) >= _MAX_FILES_PER_SCAN:
                    break

        results = [_scan_one_file(p, original_name=label)
                   for p, label in candidates]
        summary = _summarise(results)
        scan_id = ""
        if body.get("persist", True):
            scan_id = uuid.uuid4().hex[:12]
            ClassificationDB.insert(
                scan_id=scan_id,
                user_id=user.get("user_id") or user.get("username") or "",
                source_kind="project",
                source_label=f"{agent_id}/{project_name}",
                file_count=len(results),
                summary_json=json.dumps(summary),
                evidence_json=_trim_evidence(results),
            )
        self._send_json({
            "scan_id": scan_id,
            "project": project_name,
            "agent_id": agent_id,
            "summary": summary,
            "results": results,
        })

    # ── GET /v1/classification/scans ────────────────────────────────────
    def _handle_classification_scans_list(self):
        user = self._require_auth()
        if not user:
            return
        admin = user.get("role") == "admin"
        rows = ClassificationDB.list_for_user(
            user.get("user_id") or "", admin=admin, limit=100
        )
        # Hydrate summary_json into a dict
        for r in rows:
            try:
                r["summary"] = json.loads(r.pop("summary_json", "{}"))
            except Exception:
                r["summary"] = {}
        self._send_json({"scans": rows})

    # ── GET /v1/classification/scans/<id> (and .csv) ────────────────────
    def _handle_classification_scan_detail(self, path: str):
        user = self._require_auth()
        if not user:
            return
        # path = /v1/classification/scans/<id>[.csv]
        tail = path.split("/v1/classification/scans/", 1)[1]
        as_csv = tail.endswith(".csv")
        scan_id = tail[:-4] if as_csv else tail
        admin = user.get("role") == "admin"
        row = ClassificationDB.get(
            scan_id, user.get("user_id") or "", admin=admin
        )
        if not row:
            self._send_json({"error": "scan not found"}, 404)
            return
        try:
            summary = json.loads(row.get("summary_json") or "{}")
        except Exception:
            summary = {}
        try:
            results = json.loads(row.get("evidence_json") or "[]")
        except Exception:
            results = []

        if as_csv:
            buf = io.StringIO()
            w = csv.writer(buf)
            w.writerow(["filename", "marker_level", "final_level",
                        "mismatch_severity", "heuristic_level",
                        "pii_count", "error"])
            for r in results:
                mm = r.get("mismatch") or {}
                w.writerow([
                    r.get("filename") or "",
                    r.get("marker_level") or "",
                    r.get("final_level") or "",
                    mm.get("severity") or "",
                    r.get("heuristic_level") or "",
                    r.get("pii_count") or 0,
                    r.get("error") or "",
                ])
            csv_bytes = buf.getvalue().encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Disposition",
                             f'attachment; filename="classification-scan-{scan_id}.csv"')
            self.send_header("Content-Length", str(len(csv_bytes)))
            self.end_headers()
            self.wfile.write(csv_bytes)
            return

        self._send_json({
            "scan_id": scan_id,
            "user_id": row.get("user_id"),
            "created_at": row.get("created_at"),
            "source_kind": row.get("source_kind"),
            "source_label": row.get("source_label"),
            "file_count": row.get("file_count"),
            "summary": summary,
            "results": results,
        })

    # ── DELETE /v1/classification/scans/<id> ────────────────────────────
    def _handle_classification_scan_delete(self, path: str):
        user = self._require_auth()
        if not user:
            return
        scan_id = path.split("/v1/classification/scans/", 1)[1]
        admin = user.get("role") == "admin"
        ClassificationDB.delete(
            scan_id, user.get("user_id") or "", admin=admin
        )
        self._send_json({"status": "deleted", "scan_id": scan_id})

    # ── GET /v1/classification/config ───────────────────────────────────
    def _handle_classification_config_get(self):
        user = self._require_role("admin")
        if not user:
            return
        cfg = _config().get("classification") or {}
        keywords = cls.get_keywords({"classification": cfg})
        extras = []
        for item in (cfg.get("extra_patterns") or []):
            if isinstance(item, dict):
                extras.append({
                    "level": item.get("level"),
                    "pattern": item.get("pattern", ""),
                })
        # Policy block (Phase B) — merged with engine defaults
        scanner_cfg = engine._get_classification_config()
        self._send_json({
            "keywords": keywords,
            "extra_patterns": extras,
            "defaults": {
                "keywords": cls.DEFAULT_KEYWORDS,
                "policy": engine._CLASSIFICATION_DEFAULTS,
            },
            "levels": list(cls.LEVELS),
            "policy": {
                "enabled": scanner_cfg.get("enabled", True),
                "server_block": scanner_cfg.get("server_block", True),
                "server_log": scanner_cfg.get("server_log", True),
                "default_local_fallback_model":
                    scanner_cfg.get("default_local_fallback_model", ""),
                "per_level_action": scanner_cfg.get("per_level_action", {}),
            },
        })

    # ── POST /v1/classification/config ──────────────────────────────────
    def _handle_classification_config_save(self):
        user = self._require_role("admin")
        if not user:
            return
        body = self._read_json() or {}

        keywords = body.get("keywords") or {}
        if not isinstance(keywords, dict):
            self._send_json({"error": "keywords must be an object"}, 400)
            return
        clean_keywords: dict[str, list[str]] = {}
        for lvl in ("internal", "confidential", "strict"):
            words = keywords.get(lvl) or []
            if not isinstance(words, list):
                self._send_json({"error": f"keywords.{lvl} must be a list"}, 400)
                return
            clean_keywords[lvl] = [str(w).strip() for w in words
                                    if isinstance(w, str) and w.strip()]

        extra_patterns = body.get("extra_patterns") or []
        if not isinstance(extra_patterns, list):
            self._send_json({"error": "extra_patterns must be a list"}, 400)
            return
        clean_patterns: list[dict] = []
        import re as _re
        for i, item in enumerate(extra_patterns):
            if not isinstance(item, dict):
                self._send_json({"error": f"extra_patterns[{i}] must be an object"}, 400)
                return
            level = item.get("level")
            pattern = item.get("pattern", "")
            if level not in cls.LEVELS:
                self._send_json({"error": f"extra_patterns[{i}].level invalid"}, 400)
                return
            if not isinstance(pattern, str) or not pattern.strip():
                continue  # skip empty
            try:
                _re.compile(pattern)
            except _re.error as e:
                self._send_json({"error": f"extra_patterns[{i}].pattern invalid regex: {e}"}, 400)
                return
            clean_patterns.append({"level": level, "pattern": pattern.strip()})

        # ── Policy block (Phase B) — optional in request body ──
        policy_in = body.get("policy")
        clean_policy: dict | None = None
        if policy_in is not None:
            if not isinstance(policy_in, dict):
                self._send_json({"error": "policy must be an object"}, 400)
                return
            _VALID_ACTIONS = ("ignore", "warn", "force_local", "block")
            per_level = policy_in.get("per_level_action") or {}
            if not isinstance(per_level, dict):
                self._send_json({"error": "policy.per_level_action must be an object"}, 400)
                return
            clean_per_level: dict[str, str] = {}
            for lvl in ("public", "internal", "confidential", "strict", "unmarked"):
                v = per_level.get(lvl)
                if v is None:
                    continue
                if v not in _VALID_ACTIONS:
                    self._send_json({"error": f"policy.per_level_action.{lvl}={v!r} invalid"}, 400)
                    return
                clean_per_level[lvl] = v
            # Strict-always-block invariant: silently coerce policy.per_level_action.strict
            # to 'block' if server_block is on. (ARL §1.11.)
            clean_policy = {
                "enabled": bool(policy_in.get("enabled", True)),
                "server_block": bool(policy_in.get("server_block", True)),
                "server_log": bool(policy_in.get("server_log", True)),
                "default_local_fallback_model":
                    str(policy_in.get("default_local_fallback_model", "") or ""),
                "per_level_action": clean_per_level,
            }

        # Persist to config.json (atomic via temp + replace)
        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "config.json",
        )
        try:
            cfg = {}
            if os.path.exists(config_path):
                with open(config_path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
            cfg["classification"] = {
                "keywords": clean_keywords,
                "extra_patterns": clean_patterns,
            }
            if clean_policy is not None:
                cfg["classification_scanner"] = clean_policy
            tmp_path = config_path + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(cfg, f, indent=2, ensure_ascii=False)
            os.replace(tmp_path, config_path)
        except Exception as e:
            self._send_json({"error": f"persist failed: {e}"}, 500)
            return

        # Refresh in-memory server_config
        try:
            from server import server_config
            server_config["classification"] = cfg["classification"]
            if "classification_scanner" in cfg:
                server_config["classification_scanner"] = cfg["classification_scanner"]
        except Exception:
            pass

        # Audit
        try:
            if getattr(engine, "_audit_log", None):
                engine._audit_log.log_action(
                    agent="main",
                    action_type="classification_config_save",
                    tool_name="-",
                    args_summary=(
                        f"by={user.get('username','')} "
                        f"kw_internal={len(clean_keywords.get('internal',[]))} "
                        f"kw_confidential={len(clean_keywords.get('confidential',[]))} "
                        f"kw_strict={len(clean_keywords.get('strict',[]))} "
                        f"extra_patterns={len(clean_patterns)} "
                        f"policy_saved={clean_policy is not None}"
                    ),
                    result_status="ok",
                )
        except Exception:
            pass

        self._send_json({"status": "saved",
                         "keywords": clean_keywords,
                         "extra_patterns": clean_patterns,
                         "policy": clean_policy})
