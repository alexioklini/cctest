"""Favourites: per-user/team/global shortcuts to chats, projects, workflows,
schedules, and artifacts.

Storage lives in `chats.db` alongside sessions/artifacts; image files live
under `agents/main/favourite_images/`. RBAC scope (`user|team|general`)
mirrors the underlying item's visibility — a favourite never broadens
visibility, only references it.

Hydration (resolving an item_id to its current title / updated_at /
availability) is the caller's responsibility — this module only stores
favourite rows.
"""
import os
import sqlite3
import time

from server_lib.db import _db_conn, _db_safe, CHAT_DB


ITEM_TYPES = (
    "chat",
    "project_chat",
    "project",
    "workflow",
    "schedule",
    "artifact",
    "translation",
)

SCOPES = ("user", "team", "general")

# Image storage — outside any agent's artifact tree because favourites are
# user/team/global-scoped, not agent-scoped.
IMAGE_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..",
    "agents", "main", "favourite_images",
)
MAX_IMAGE_BYTES = 2 * 1024 * 1024  # 2 MB
ALLOWED_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".svg"}


class FavouritesDB:
    """SQLite persistence for favourite rows."""

    @staticmethod
    def init():
        os.makedirs(IMAGE_DIR, exist_ok=True)
        with _db_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS favourites (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scope TEXT NOT NULL,
                    scope_id TEXT NOT NULL DEFAULT '',
                    item_type TEXT NOT NULL,
                    item_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL DEFAULT 'main',
                    icon TEXT NOT NULL DEFAULT '',
                    image_path TEXT NOT NULL DEFAULT '',
                    color TEXT NOT NULL DEFAULT '',
                    added_at REAL NOT NULL,
                    added_by TEXT NOT NULL DEFAULT '',
                    UNIQUE(scope, scope_id, item_type, item_id, agent_id)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_fav_scope ON favourites(scope, scope_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_fav_item ON favourites(item_type, item_id)")
            conn.commit()

    # ── Mutations ──

    @staticmethod
    @_db_safe(default=None)
    def add(scope: str, scope_id: str, item_type: str, item_id: str,
            agent_id: str, added_by: str,
            icon: str = "", color: str = "") -> dict | None:
        """Insert a favourite. Returns the row dict, or the existing row if
        a duplicate (UNIQUE conflict). Validation of scope authority and item
        visibility happens in the handler — this only enforces shape."""
        if scope not in SCOPES:
            return {"error": f"invalid scope '{scope}'"}
        if item_type not in ITEM_TYPES:
            return {"error": f"invalid item_type '{item_type}'"}
        if not item_id:
            return {"error": "item_id required"}
        if scope == "general":
            scope_id = ""
        elif not scope_id:
            return {"error": f"scope_id required for scope '{scope}'"}
        agent_id = agent_id or "main"
        now = time.time()
        with _db_conn() as conn:
            try:
                cur = conn.execute("""
                    INSERT INTO favourites
                        (scope, scope_id, item_type, item_id, agent_id,
                         icon, image_path, color, added_at, added_by)
                    VALUES (?, ?, ?, ?, ?, ?, '', ?, ?, ?)
                """, (scope, scope_id, item_type, item_id, agent_id,
                      icon or "", color or "", now, added_by or ""))
                conn.commit()
                fav_id = cur.lastrowid
            except sqlite3.IntegrityError:
                row = conn.execute("""
                    SELECT id FROM favourites
                    WHERE scope=? AND scope_id=? AND item_type=? AND item_id=? AND agent_id=?
                """, (scope, scope_id, item_type, item_id, agent_id)).fetchone()
                if not row:
                    return {"error": "duplicate but not found"}
                fav_id = row[0]
        return FavouritesDB.get(fav_id)

    @staticmethod
    @_db_safe(default=None)
    def get(fav_id: int) -> dict | None:
        with _db_conn() as conn:
            row = conn.execute("""
                SELECT id, scope, scope_id, item_type, item_id, agent_id,
                       icon, image_path, color, added_at, added_by
                FROM favourites WHERE id = ?
            """, (fav_id,)).fetchone()
        if not row:
            return None
        keys = ("id", "scope", "scope_id", "item_type", "item_id", "agent_id",
                "icon", "image_path", "color", "added_at", "added_by")
        return dict(zip(keys, row))

    @staticmethod
    @_db_safe(default=False)
    def remove(fav_id: int) -> bool:
        """Delete a single favourite + its image file. Returns True if a row
        was actually removed."""
        row = FavouritesDB.get(fav_id)
        if not row:
            return False
        with _db_conn() as conn:
            conn.execute("DELETE FROM favourites WHERE id = ?", (fav_id,))
            conn.commit()
        # Best-effort image cleanup — never block on FS errors.
        img = row.get("image_path") or ""
        if img:
            _safe_delete_image(img)
        return True

    @staticmethod
    @_db_safe(default=0)
    def remove_bulk(scope: str, scope_id: str) -> int:
        """Delete every favourite in (scope, scope_id). Returns count."""
        if scope not in SCOPES:
            return 0
        if scope == "general":
            scope_id = ""
        with _db_conn() as conn:
            rows = conn.execute("""
                SELECT id, image_path FROM favourites
                WHERE scope = ? AND scope_id = ?
            """, (scope, scope_id)).fetchall()
            n = len(rows)
            if n:
                conn.execute("DELETE FROM favourites WHERE scope = ? AND scope_id = ?",
                             (scope, scope_id))
                conn.commit()
        for _, img in rows:
            if img:
                _safe_delete_image(img)
        return n

    @staticmethod
    @_db_safe(default=None)
    def update_visual(fav_id: int, *, icon: str | None = None,
                      color: str | None = None,
                      image_path: str | None = None) -> dict | None:
        """Patch the visual fields. Pass `None` to leave a field unchanged.
        For image_path, pass empty string to clear (caller deletes the file)."""
        row = FavouritesDB.get(fav_id)
        if not row:
            return None
        sets, vals = [], []
        if icon is not None:
            sets.append("icon = ?"); vals.append(icon)
        if color is not None:
            sets.append("color = ?"); vals.append(color)
        if image_path is not None:
            sets.append("image_path = ?"); vals.append(image_path)
        if not sets:
            return row
        vals.append(fav_id)
        with _db_conn() as conn:
            conn.execute(f"UPDATE favourites SET {', '.join(sets)} WHERE id = ?", vals)
            conn.commit()
        return FavouritesDB.get(fav_id)

    # ── Reads ──

    @staticmethod
    @_db_safe(default=list)
    def list_visible(user_id: str, team_ids: list[str], is_admin: bool = False) -> list[dict]:
        """Return every favourite row visible to the caller — UNION of:
          - user-scope rows where scope_id == user_id
          - team-scope rows where scope_id ∈ team_ids
          - all general-scope rows

        Admins additionally see every user/team row (for audit / management).
        Hydration (item title, updated_at, availability) happens upstream."""
        with _db_conn() as conn:
            if is_admin:
                rows = conn.execute("""
                    SELECT id, scope, scope_id, item_type, item_id, agent_id,
                           icon, image_path, color, added_at, added_by
                    FROM favourites
                    ORDER BY added_at DESC
                """).fetchall()
            else:
                # Build placeholder list for team_ids
                teams = team_ids or []
                placeholders = ",".join(["?"] * len(teams)) if teams else "''"
                params: list = [user_id]
                if teams:
                    params.extend(teams)
                team_clause = f"(scope = 'team' AND scope_id IN ({placeholders}))" if teams else "0"
                rows = conn.execute(f"""
                    SELECT id, scope, scope_id, item_type, item_id, agent_id,
                           icon, image_path, color, added_at, added_by
                    FROM favourites
                    WHERE
                        (scope = 'user'    AND scope_id = ?)
                     OR {team_clause}
                     OR (scope = 'general')
                    ORDER BY added_at DESC
                """, params).fetchall()
        keys = ("id", "scope", "scope_id", "item_type", "item_id", "agent_id",
                "icon", "image_path", "color", "added_at", "added_by")
        return [dict(zip(keys, r)) for r in rows]

    @staticmethod
    @_db_safe(default=list)
    def find_for_item(item_type: str, item_id: str, agent_id: str = "main") -> list[dict]:
        """Every favourite row pointing at one item (across all scopes).
        Used by the star-button to determine current state per scope."""
        with _db_conn() as conn:
            rows = conn.execute("""
                SELECT id, scope, scope_id, item_type, item_id, agent_id,
                       icon, image_path, color, added_at, added_by
                FROM favourites
                WHERE item_type = ? AND item_id = ? AND agent_id = ?
            """, (item_type, item_id, agent_id or "main")).fetchall()
        keys = ("id", "scope", "scope_id", "item_type", "item_id", "agent_id",
                "icon", "image_path", "color", "added_at", "added_by")
        return [dict(zip(keys, r)) for r in rows]


def _safe_delete_image(image_path: str):
    """Delete an image file, but only if it's inside IMAGE_DIR (defence in
    depth — the column should never hold a path outside it)."""
    if not image_path:
        return
    full = os.path.join(IMAGE_DIR, os.path.basename(image_path))
    try:
        real_dir = os.path.realpath(IMAGE_DIR)
        real_full = os.path.realpath(full)
        if not real_full.startswith(real_dir + os.sep):
            return
        if os.path.isfile(real_full):
            os.unlink(real_full)
    except OSError:
        pass
