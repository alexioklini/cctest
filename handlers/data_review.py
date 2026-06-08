"""HTTP handlers for the per-document GDPR + classification reviewer.

One unified review surface shared by the Data view, the project ingest tree,
and right-panel attachments. Detection + anonymisation live in
`engine.doc_review`; persistence in `server_lib.db.DataReviewDB`; the
de-anonymisation index in the encrypted `pseudonym_maps` store.

INVARIANT (see CLAUDE.md / handover): this feature NEVER changes what any LLM
or non-LLM path sees today. The disk file is always the original. A saved
anonymisation only gets reused at the two seams that ALREADY anonymise on the
way to the LLM (`_gdpr_anon_tool_text`, KG extraction), guarded by
"a saved review with matching content hash exists". No saved review →
byte-identical to today.

Routes (all authenticated):
  POST   /v1/data-review/analyze        upload | {agent_id,project,path|source_hash}
  POST   /v1/data-review/overrule       {review_id, violation_id, explanation}
  POST   /v1/data-review/anonymise      {review_id}  → build+store anon_text + index
  POST   /v1/data-review/revert         {review_id, drop_overrules?}
  GET    /v1/data-review/<id>           full review (text + violations + overrules)
  GET    /v1/data-review/list           user's reviews
  DELETE /v1/data-review/<id>           remove a review record
  GET    /v1/data-review/<id>/export    download a self-contained anonymised copy
  POST   /v1/data-review/state          {refs:[{kind,ref}]} → badge states (batch)
"""

from __future__ import annotations

import io
import json
import os
import tempfile
import time
import uuid
from urllib.parse import urlparse

import brain as engine
from engine import doc_review
from engine import review_metadata
from server_lib.db import DataReviewDB
from server_lib import pathsafe


_MAX_UPLOAD_BYTES = 50 * 1024 * 1024


def _uid(user: dict) -> str:
    return user.get("id") or user.get("user_id") or user.get("username") or ""


def _now() -> float:
    return time.time()


def _project_file_allowed(agent_id: str, project_name: str, path: str,
                          user_id: str) -> tuple[str | None, str | None]:
    """Validate `path` is a real file under one of the project's input folders
    (or its ingested/ dir). Returns (real_path, error)."""
    try:
        project = engine.ProjectManager.get_project(agent_id, project_name)
    except Exception as e:
        return None, f"project lookup failed: {e}"
    if not project:
        return None, "project not found"
    roots = []
    for f in (project.get("input_folders") or []):
        p = f.get("path") if isinstance(f, dict) else None
        if p:
            roots.append(p)
    ing = project.get("ingested_dir") or ""
    if ing:
        roots.append(ing)
    if not roots:
        return None, "project has no input folders"
    return pathsafe.validate_path(path, allowed_roots=roots, must_exist=True)


def _resolve_source_hash(agent_id: str, project_name: str,
                         source_hash: str) -> str | None:
    """Map an ingested doc's source_hash → its original on-disk path, if that
    source is a local file (not a URL / removed temp)."""
    try:
        docs = engine.IngestManager.list_ingested(agent_id,
                                                  project_name=project_name)
    except Exception:
        return None
    for d in docs or []:
        if d.get("source_hash") == source_hash:
            src = d.get("source") or ""
            if src and os.path.isfile(src):
                return src
            return None
    return None


class DataReviewHandlerMixin:
    """Add to BrainAgentHandler. Routes dispatched from server.py."""

    # ── POST /v1/data-review/analyze ────────────────────────────────────────
    def _handle_data_review_analyze(self):
        user = self._require_auth()
        if not user:
            return
        uid = _uid(user)
        ctype = self.headers.get("Content-Type", "")

        text = ""
        filename = ""
        source_kind = ""
        source_ref = ""

        if ctype.startswith("multipart/form-data"):
            # Upload path — extract text from the posted bytes.
            clen = int(self.headers.get("Content-Length", "0") or 0)
            if not clen or clen > _MAX_UPLOAD_BYTES:
                self._send_json({"error": "empty or oversize upload"}, 400)
                return
            body = self.rfile.read(clen)
            from handlers.classification import _parse_multipart_files
            files, _fields, err = _parse_multipart_files(ctype, body)
            if err or not files:
                self._send_json({"error": err or "no file"}, 400)
                return
            f = files[0]
            filename = os.path.basename(f["name"] or "upload") or "upload"
            tmp_dir = tempfile.mkdtemp(prefix="datareview-")
            tmp_path = os.path.join(tmp_dir, filename)
            try:
                with open(tmp_path, "wb") as fh:
                    fh.write(f["bytes"])
                text, kind = engine.extract_attachment_text(tmp_path)
                # Reuse any review embedded in the uploaded file itself.
                embedded = review_metadata.extract(tmp_path)
            finally:
                try:
                    os.unlink(tmp_path)
                    os.rmdir(tmp_dir)
                except OSError:
                    pass
            if kind != "text" or not (text or "").strip():
                self._send_json({"error": "could not extract text"}, 400)
                return
            source_kind = "upload"
            source_ref = doc_review.content_hash(text)
            if embedded:
                self._maybe_restore_embedded(embedded)
        else:
            body = self._read_json() or {}
            agent_id = body.get("agent_id") or "main"
            project_name = body.get("project") or body.get("project_name") or ""
            path = body.get("path") or ""
            source_hash = body.get("source_hash") or ""
            if source_hash and not path:
                path = _resolve_source_hash(agent_id, project_name, source_hash)
                if not path:
                    self._send_json(
                        {"error": "source has no readable local file",
                         "read_only": True}, 422)
                    return
                source_kind = "project_doc"
            else:
                source_kind = "project_path"
            if not path:
                self._send_json({"error": "path or source_hash required"}, 400)
                return
            rp, err = _project_file_allowed(agent_id, project_name, path, uid)
            if err:
                self._send_json({"error": err}, 403)
                return
            text, kind = engine.extract_attachment_text(rp)
            if kind != "text" or not (text or "").strip():
                self._send_json({"error": "could not extract text"}, 400)
                return
            filename = os.path.basename(rp)
            source_ref = rp

        # Reuse an existing review (instant for already-checked files).
        review_id = doc_review._stable_review_id(source_kind, source_ref, uid)
        chash = doc_review.content_hash(text)
        prior = DataReviewDB.get(review_id, uid, admin=True)
        if prior and prior.get("content_hash") == chash:
            self._send_json(self._review_payload(prior, reused=True))
            return

        result = doc_review.analyze(text, filename=filename)
        DataReviewDB.upsert(
            review_id=review_id, user_id=uid, content_hash=chash,
            source_kind=source_kind, source_ref=source_ref, filename=filename,
            status="reviewed", text=text,
            violations_json=json.dumps(result["violations"]),
            overrules_json="[]", anon_mapping_id="", anon_text="",
        )
        row = DataReviewDB.get(review_id, uid, admin=True)
        self._send_json(self._review_payload(row, analysis=result))

    def _maybe_restore_embedded(self, embedded: dict):
        """An uploaded file may carry its own review (overrules + de-anon
        index). Restore the de-anon mapping to the registry so subsequent
        reads of this content can be de-anonymised."""
        try:
            anon_map = embedded.get("anon_map")
            if anon_map:
                m = review_metadata.decode_mapping(anon_map)
                if m:
                    import pseudonymizer as _ps
                    _ps.restore_mapping_to_registry(m)
        except Exception:
            pass

    # ── POST /v1/data-review/overrule ───────────────────────────────────────
    def _handle_data_review_overrule(self):
        user = self._require_auth()
        if not user:
            return
        uid = _uid(user)
        body = self._read_json() or {}
        review_id = body.get("review_id") or ""
        vid = body.get("violation_id") or ""
        explanation = (body.get("explanation") or "").strip()
        remove = bool(body.get("remove"))
        if not review_id or not vid:
            self._send_json({"error": "review_id and violation_id required"}, 400)
            return
        if not remove and not explanation:
            self._send_json({"error": "explanation required to overrule"}, 400)
            return
        row = DataReviewDB.get(review_id, uid)
        if not row:
            self._send_json({"error": "review not found"}, 404)
            return
        try:
            violations = json.loads(row.get("violations_json") or "[]")
            overrules = json.loads(row.get("overrules_json") or "[]")
        except Exception:
            violations, overrules = [], []
        v = next((x for x in violations if x.get("id") == vid), None)
        if v is None:
            self._send_json({"error": "violation not found"}, 404)
            return
        overrules = [o for o in overrules if o.get("id") != vid]
        if not remove:
            overrules.append({
                "id": vid,
                "kind": v.get("kind"),
                "label": v.get("label"),
                "excerpt": v.get("excerpt"),
                "explanation": explanation,
                "by": uid,
                "at": _now(),
            })
        DataReviewDB.upsert(
            review_id=review_id, user_id=row.get("user_id") or uid,
            content_hash=row.get("content_hash") or "",
            source_kind=row.get("source_kind") or "",
            source_ref=row.get("source_ref") or "",
            filename=row.get("filename") or "",
            status=row.get("status") or "reviewed",
            text=row.get("text") or "",
            anon_text=row.get("anon_text") or "",
            violations_json=json.dumps(violations),
            overrules_json=json.dumps(overrules),
            anon_mapping_id=row.get("anon_mapping_id") or "",
        )
        self._send_json({"status": "ok", "overrule_count": len(overrules)})

    # ── POST /v1/data-review/anonymise ──────────────────────────────────────
    def _handle_data_review_anonymise(self):
        user = self._require_auth()
        if not user:
            return
        uid = _uid(user)
        body = self._read_json() or {}
        review_id = body.get("review_id") or ""
        row = DataReviewDB.get(review_id, uid)
        if not row:
            self._send_json({"error": "review not found"}, 404)
            return
        text = row.get("text") or ""
        try:
            violations = json.loads(row.get("violations_json") or "[]")
            overrules = json.loads(row.get("overrules_json") or "[]")
        except Exception:
            violations, overrules = [], []
        overruled_ids = {o.get("id") for o in overrules}

        anon_text, mapping, replaced = doc_review.anonymise(
            text, violations, overruled_ids=overruled_ids,
            source=f"data_review:{review_id}")
        if replaced == 0:
            self._send_json({"status": "noop", "replaced": 0,
                             "message": "Keine anonymisierbaren Treffer "
                                        "(alle übersteuert oder keine PII)."})
            return
        # Persist the encrypted de-anon index (keyed off review_id so it's
        # discoverable later; session_id field reused as the review scope).
        try:
            import pseudonymizer as _ps
            _ps.save_mapping(mapping, session_id=f"review:{review_id}")
        except Exception as e:
            print(f"[data_review] save_mapping failed: {e}", flush=True)
        DataReviewDB.upsert(
            review_id=review_id, user_id=row.get("user_id") or uid,
            content_hash=row.get("content_hash") or "",
            source_kind=row.get("source_kind") or "",
            source_ref=row.get("source_ref") or "",
            filename=row.get("filename") or "",
            status="anonymised", text=text, anon_text=anon_text,
            violations_json=json.dumps(violations),
            overrules_json=json.dumps(overrules),
            anon_mapping_id=mapping.mapping_id,
        )
        self._send_json({"status": "anonymised", "replaced": replaced,
                         "mapping_id": mapping.mapping_id})

    # ── POST /v1/data-review/revert ─────────────────────────────────────────
    def _handle_data_review_revert(self):
        """Restore original + clear anonymisation state. The disk file was
        never modified, so this only clears the saved anonymisation (the
        LLM-bound seams then fall back to today's fresh-scan behavior). Overrule
        history is kept unless drop_overrules is set."""
        user = self._require_auth()
        if not user:
            return
        uid = _uid(user)
        body = self._read_json() or {}
        review_id = body.get("review_id") or ""
        drop_overrules = bool(body.get("drop_overrules"))
        row = DataReviewDB.get(review_id, uid)
        if not row:
            self._send_json({"error": "review not found"}, 404)
            return
        # Drop the persisted de-anon index (no longer applied).
        mid = row.get("anon_mapping_id") or ""
        if mid:
            try:
                import pseudonymizer as _ps
                _ps.delete_persisted_mapping(mid)
                _ps.close_mapping(mid)
            except Exception:
                pass
        overrules = "[]" if drop_overrules else (row.get("overrules_json") or "[]")
        try:
            has_ov = bool(json.loads(overrules))
        except Exception:
            has_ov = False
        DataReviewDB.upsert(
            review_id=review_id, user_id=row.get("user_id") or uid,
            content_hash=row.get("content_hash") or "",
            source_kind=row.get("source_kind") or "",
            source_ref=row.get("source_ref") or "",
            filename=row.get("filename") or "",
            status="reviewed" if has_ov else "reviewed",
            text=row.get("text") or "", anon_text="",
            violations_json=row.get("violations_json") or "[]",
            overrules_json=overrules, anon_mapping_id="",
        )
        self._send_json({"status": "reverted"})

    # ── GET /v1/data-review/<id> ────────────────────────────────────────────
    def _handle_data_review_get(self, path: str):
        user = self._require_auth()
        if not user:
            return
        uid = _uid(user)
        review_id = path.rsplit("/", 1)[-1]
        admin = user.get("role") == "admin"
        row = DataReviewDB.get(review_id, uid, admin=admin)
        if not row:
            self._send_json({"error": "review not found"}, 404)
            return
        self._send_json(self._review_payload(row))

    # ── GET /v1/data-review/list ────────────────────────────────────────────
    def _handle_data_review_list(self):
        user = self._require_auth()
        if not user:
            return
        admin = user.get("role") == "admin"
        rows = DataReviewDB.list_for_user(_uid(user), admin=admin, limit=200)
        self._send_json({"reviews": rows})

    # ── DELETE /v1/data-review/<id> ─────────────────────────────────────────
    def _handle_data_review_delete(self, path: str):
        user = self._require_auth()
        if not user:
            return
        uid = _uid(user)
        review_id = path.rsplit("/", 1)[-1]
        admin = user.get("role") == "admin"
        row = DataReviewDB.get(review_id, uid, admin=admin)
        if row and row.get("anon_mapping_id"):
            try:
                import pseudonymizer as _ps
                _ps.delete_persisted_mapping(row["anon_mapping_id"])
            except Exception:
                pass
        DataReviewDB.delete(review_id, uid, admin=admin)
        self._send_json({"status": "deleted"})

    # ── GET /v1/data-review/<id>/export ─────────────────────────────────────
    def _handle_data_review_export(self, path: str):
        """Download a self-contained ANONYMISED copy of the document, with the
        review metadata (overrules + encrypted de-anon index) embedded so it
        round-trips back into a chat/project."""
        user = self._require_auth()
        if not user:
            return
        uid = _uid(user)
        review_id = path.split("/v1/data-review/", 1)[1].rsplit("/export", 1)[0]
        admin = user.get("role") == "admin"
        row = DataReviewDB.get(review_id, uid, admin=admin)
        if not row:
            self._send_json({"error": "review not found"}, 404)
            return
        if row.get("status") != "anonymised" or not row.get("anon_text"):
            self._send_json({"error": "review is not anonymised"}, 409)
            return
        filename = row.get("filename") or "document.txt"
        stem, ext = os.path.splitext(filename)
        out_name = f"{stem}.anon{ext or '.txt'}"
        # Write the anonymised text + embed metadata. Plain-text export keeps
        # it simple + portable; the embedded JSON carries the de-anon index.
        anon_map = None
        mid = row.get("anon_mapping_id") or ""
        if mid:
            try:
                import pseudonymizer as _ps
                m = _ps.load_mapping(mid)
                anon_map = review_metadata.encode_mapping(m) if m else None
            except Exception:
                anon_map = None
        payload = {
            "v": 1, "review_id": review_id,
            "content_hash": row.get("content_hash") or "",
            "status": "anonymised", "anonymised": True,
            "overrules": json.loads(row.get("overrules_json") or "[]"),
            "anon_map": anon_map,
        }
        tmp_dir = tempfile.mkdtemp(prefix="datareview-exp-")
        out_path = os.path.join(tmp_dir, out_name)
        try:
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(row.get("anon_text") or "")
            review_metadata.embed(out_path, payload)
            with open(out_path, "rb") as f:
                data = f.read()
        finally:
            try:
                for nm in os.listdir(tmp_dir):
                    os.unlink(os.path.join(tmp_dir, nm))
                os.rmdir(tmp_dir)
            except OSError:
                pass
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Disposition",
                         f'attachment; filename="{out_name}"')
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # ── POST /v1/data-review/state ──────────────────────────────────────────
    def _handle_data_review_state(self):
        """Batch badge-state lookup. Body: {refs:[{kind,ref}]}."""
        user = self._require_auth()
        if not user:
            return
        uid = _uid(user)
        body = self._read_json() or {}
        refs = body.get("refs") or []
        from engine import review_state
        items = [(r.get("kind") or "project_path", r.get("ref") or "")
                 for r in refs if isinstance(r, dict) and r.get("ref")]
        states = review_state.review_states(items, uid)
        self._send_json({"states": states})

    # ── auto-review on add (called from project add handlers) ────────────────
    # Synchronous so badges are correct on the add response. Bounded: a folder
    # add reviews up to _AUTO_REVIEW_MAX files with a per-file budget; the rest
    # are left to the project-sync daemon's re-mine refresh. Re-running on an
    # unchanged file is a cheap hash-compare no-op (review_file_to_db skips).
    _AUTO_REVIEW_MAX = 200

    def _auto_review_paths(self, *, user: dict, source_kind: str,
                           paths: list[str]) -> int:
        """Review each path → data_reviews. Returns count reviewed. Best-effort:
        a single file's failure never breaks the add."""
        uid = _uid(user)
        if not uid:
            return 0
        n = 0
        for p in (paths or [])[:self._AUTO_REVIEW_MAX]:
            try:
                row = doc_review.review_file_to_db(
                    p, user_id=uid, source_kind=source_kind, source_ref=p,
                    filename=os.path.basename(p))
                if row:
                    n += 1
            except Exception as e:
                print(f"[data_review] auto-review {p}: {e}", flush=True)
        return n

    def _auto_review_folder(self, *, user: dict, folder: str,
                            recursive: bool = True) -> int:
        """Enumerate supported files under `folder` and review them."""
        from engine.doc_convert import SUPPORTED_EXTS
        accept = set(SUPPORTED_EXTS) | {".md", ".markdown", ".txt",
                                        ".html", ".htm", ".csv"}
        paths: list[str] = []
        try:
            walker = os.walk(folder) if recursive else [
                (folder, [], os.listdir(folder))]
            for root, dirs, names in walker:
                if recursive:
                    dirs[:] = [d for d in dirs if not d.startswith(".")]
                for nm in names:
                    if nm.startswith("."):
                        continue
                    if os.path.splitext(nm)[1].lower() in accept:
                        paths.append(os.path.join(root, nm))
                    if len(paths) >= self._AUTO_REVIEW_MAX:
                        break
                if len(paths) >= self._AUTO_REVIEW_MAX:
                    break
        except Exception as e:
            print(f"[data_review] folder enumerate {folder}: {e}", flush=True)
        return self._auto_review_paths(user=user, source_kind="project_path",
                                       paths=paths)

    # ── helpers ─────────────────────────────────────────────────────────────
    def _review_payload(self, row: dict, *, analysis: dict | None = None,
                        reused: bool = False) -> dict:
        try:
            violations = json.loads(row.get("violations_json") or "[]")
        except Exception:
            violations = []
        try:
            overrules = json.loads(row.get("overrules_json") or "[]")
        except Exception:
            overrules = []
        out = {
            "review_id": row.get("review_id"),
            "filename": row.get("filename"),
            "source_kind": row.get("source_kind"),
            "source_ref": row.get("source_ref"),
            "status": row.get("status"),
            "content_hash": row.get("content_hash"),
            "anonymised": bool(row.get("anon_mapping_id")),
            "text": row.get("text") or "",
            "violations": violations,
            "overrules": overrules,
            "reused": reused,
        }
        if analysis:
            out["classification"] = analysis.get("classification")
            out["counts"] = analysis.get("counts")
        return out
