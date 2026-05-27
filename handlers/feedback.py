"""HTTP handlers for /v1/feedback.

Per-user 👍/👎 feedback on assistant responses/results. Any authenticated user
may submit feedback on a response they see; the admin Feedback tab lists all
rows. Feedback is self-authored, so no item-hydration or scope-authority checks
are needed (unlike favourites) — only the admin-list/delete are gated.
"""
from urllib.parse import urlparse, parse_qs

from server_lib import auth as _auth_mod
from server_lib.feedback import FeedbackDB, SURFACES, RATINGS

COMMENT_CAP = 4000
SNAPSHOT_CAP = 500


class FeedbackHandlerMixin:
    """HTTP handlers for /v1/feedback."""

    def _feedback_caller(self):
        """Return (user, is_admin) for the current request."""
        user = getattr(self, "_auth_user", _auth_mod.SYNTHETIC_ADMIN)
        is_admin = user.get("role") == "admin" or user.get("id") == "__system__"
        return user, is_admin

    # ── POST /v1/feedback ──

    def _handle_feedback_submit(self):
        body = self._read_json()
        surface = (body.get("surface") or "").strip()
        target_id = str(body.get("target_id") or "").strip()
        session_id = (body.get("session_id") or "").strip()
        rating = (body.get("rating") or "").strip()
        comment = str(body.get("comment") or "")[:COMMENT_CAP]
        context_snapshot = str(body.get("context_snapshot") or "")[:SNAPSHOT_CAP]

        if surface not in SURFACES:
            self._send_json({"error": f"invalid surface '{surface}'"}, 400)
            return
        if rating not in RATINGS:
            self._send_json({"error": f"invalid rating '{rating}'"}, 400)
            return
        if not target_id:
            self._send_json({"error": "target_id required"}, 400)
            return

        user, _ = self._feedback_caller()
        result = FeedbackDB.upsert(
            surface=surface, target_id=target_id, session_id=session_id,
            user_id=user.get("id") or "", rating=rating,
            comment=comment, context_snapshot=context_snapshot,
        )
        if result is None:
            self._send_json({"error": "save failed"}, 500)
            return
        if "error" in result:
            self._send_json(result, 400)
            return
        self._send_json(result)

    # ── GET /v1/feedback?surface=&rating= (admin) ──

    def _handle_feedback_list(self):
        _, is_admin = self._feedback_caller()
        if not is_admin:
            self._send_json({"error": "admin only"}, 403)
            return
        qs = parse_qs(urlparse(self.path).query)
        surface = (qs.get("surface", [""])[0] or "").strip() or None
        rating = (qs.get("rating", [""])[0] or "").strip() or None
        rows = FeedbackDB.list(surface=surface, rating=rating)
        # Resolve user_id -> display name (display_name → username → id) so the
        # admin view shows a human name, not an opaque id. Cache per id.
        name_cache: dict[str, str] = {}
        for r in rows:
            uid = r.get("user_id") or ""
            if uid not in name_cache:
                u = _auth_mod.AuthDB.get_user(uid) if uid else None
                name_cache[uid] = (u.get("display_name") or u.get("username")) if u else (uid or "—")
            r["user_name"] = name_cache[uid]
        self._send_json({"feedback": rows})

    # ── GET /v1/feedback/mine?surface=&session_id= ──

    def _handle_feedback_mine(self):
        user, _ = self._feedback_caller()
        qs = parse_qs(urlparse(self.path).query)
        surface = (qs.get("surface", [""])[0] or "").strip() or None
        session_id = (qs.get("session_id", [""])[0] or "").strip() or None
        rows = FeedbackDB.find_mine(user.get("id") or "", surface=surface,
                                    session_id=session_id)
        self._send_json({"feedback": rows})

    # ── DELETE /v1/feedback/<id> (admin) ──

    def _handle_feedback_remove(self, path: str):
        _, is_admin = self._feedback_caller()
        if not is_admin:
            self._send_json({"error": "admin only"}, 403)
            return
        try:
            fb_id = int(path.split("/")[-1])
        except (ValueError, IndexError):
            self._send_json({"error": "invalid feedback id"}, 400)
            return
        if not FeedbackDB.remove(fb_id):
            self._send_json({"error": "not found"}, 404)
            return
        self._send_json({"removed": fb_id})
