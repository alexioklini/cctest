"""Multi-user authentication, RBAC, and user team management.

Provides:
- AuthDB: SQLite-backed user & team CRUD
- JWT token generation/verification
- Authorization helpers for session/project/resource access control
"""

import hashlib
import json
import os
import sqlite3
import threading
import time
import uuid

import bcrypt
import jwt as pyjwt

# ── Database ─────────────────────────────────────────────────────────

AUTH_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agents", "main", "auth.db")

_auth_db_lock = threading.Lock()
_auth_db_pool: dict[int, sqlite3.Connection] = {}


def _auth_conn() -> sqlite3.Connection:
    tid = threading.current_thread().ident
    with _auth_db_lock:
        conn = _auth_db_pool.get(tid)
        if conn is None:
            os.makedirs(os.path.dirname(AUTH_DB), exist_ok=True)
            conn = sqlite3.connect(AUTH_DB, timeout=10, check_same_thread=False)
            conn.execute("PRAGMA busy_timeout = 5000")
            conn.execute("PRAGMA journal_mode = WAL")
            conn.row_factory = sqlite3.Row
            _auth_db_pool[tid] = conn
    return conn


# ── Config ───────────────────────────────────────────────────────────

_auth_config: dict = {}
_jwt_secret: str = ""

ROLES = ("admin", "poweruser", "user")


def init_auth(config: dict):
    """Initialize auth module from server config. Call once at startup."""
    global _auth_config, _jwt_secret
    _auth_config = config.get("auth", {})

    # JWT secret: load from config or generate
    _jwt_secret = _auth_config.get("jwt_secret", "")
    if not _jwt_secret:
        _jwt_secret = uuid.uuid4().hex + uuid.uuid4().hex
        _auth_config["jwt_secret"] = _jwt_secret
        config.setdefault("auth", {})["jwt_secret"] = _jwt_secret
        # Persist to config.json
        config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
        try:
            with open(config_path, "w") as f:
                json.dump(config, f, indent=2)
        except Exception:
            pass

    AuthDB.init()


def auth_enabled() -> bool:
    return _auth_config.get("enabled", False)


def registration_enabled() -> bool:
    return _auth_config.get("registration_enabled", True)


def default_role() -> str:
    r = _auth_config.get("default_role", "user")
    return r if r in ROLES else "user"


def token_expiry() -> int:
    return _auth_config.get("token_expiry_seconds", 86400)


# ── AuthDB ───────────────────────────────────────────────────────────

class AuthDB:

    @staticmethod
    def init():
        conn = _auth_conn()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                display_name TEXT DEFAULT '',
                email TEXT DEFAULT '',
                role TEXT NOT NULL DEFAULT 'user',
                created_at REAL,
                last_login REAL,
                disabled INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS user_teams (
                id TEXT PRIMARY KEY,
                name TEXT UNIQUE NOT NULL,
                description TEXT DEFAULT '',
                head_user_id TEXT NOT NULL,
                created_at REAL,
                FOREIGN KEY (head_user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS user_team_members (
                user_id TEXT NOT NULL,
                team_id TEXT NOT NULL,
                joined_at REAL DEFAULT (strftime('%s','now')),
                PRIMARY KEY (user_id, team_id),
                FOREIGN KEY (user_id) REFERENCES users(id),
                FOREIGN KEY (team_id) REFERENCES user_teams(id)
            );

            CREATE INDEX IF NOT EXISTS idx_utm_user ON user_team_members(user_id);
            CREATE INDEX IF NOT EXISTS idx_utm_team ON user_team_members(team_id);
        """)

        # Create default admin if no users exist
        row = conn.execute("SELECT COUNT(*) FROM users").fetchone()
        if row[0] == 0:
            pw = bcrypt.hashpw(b"admin", bcrypt.gensalt()).decode()
            uid = uuid.uuid4().hex[:12]
            conn.execute(
                "INSERT INTO users (id, username, password_hash, display_name, role, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (uid, "admin", pw, "Administrator", "admin", time.time()),
            )
            conn.commit()
            print(f"  ⚠  Default admin user created (username: admin, password: admin) — change immediately!")

    # ── User CRUD ────────────────────────────────────────────────────

    @staticmethod
    def create_user(username: str, password: str, role: str = "",
                    display_name: str = "", email: str = "") -> dict:
        if not username or not password:
            return {"error": "Username and password required"}
        if len(password) < 6:
            return {"error": "Password must be at least 6 characters"}
        if not role:
            role = default_role()
        if role not in ROLES:
            return {"error": f"Invalid role: {role}"}

        uid = uuid.uuid4().hex[:12]
        pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        try:
            conn = _auth_conn()
            conn.execute(
                "INSERT INTO users (id, username, password_hash, display_name, email, role, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (uid, username, pw_hash, display_name or username, email, role, time.time()),
            )
            conn.commit()
            return _user_dict(conn.execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone())
        except sqlite3.IntegrityError:
            return {"error": "Username already exists"}

    @staticmethod
    def authenticate(username: str, password: str) -> dict | None:
        conn = _auth_conn()
        row = conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        if not row:
            return None
        if row["disabled"]:
            return None
        if bcrypt.checkpw(password.encode(), row["password_hash"].encode()):
            return _user_dict(row)
        return None

    @staticmethod
    def get_user(user_id: str) -> dict | None:
        row = _auth_conn().execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return _user_dict(row) if row else None

    @staticmethod
    def get_user_by_username(username: str) -> dict | None:
        row = _auth_conn().execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        return _user_dict(row) if row else None

    @staticmethod
    def list_users() -> list[dict]:
        rows = _auth_conn().execute("SELECT * FROM users ORDER BY created_at").fetchall()
        return [_user_dict(r) for r in rows]

    @staticmethod
    def update_user(user_id: str, updates: dict) -> dict:
        allowed = {"display_name", "email", "role", "disabled"}
        fields = {k: v for k, v in updates.items() if k in allowed}
        if "role" in fields and fields["role"] not in ROLES:
            return {"error": f"Invalid role: {fields['role']}"}
        if not fields:
            return {"error": "No valid fields to update"}
        conn = _auth_conn()
        sets = ", ".join(f"{k} = ?" for k in fields)
        vals = list(fields.values()) + [user_id]
        conn.execute(f"UPDATE users SET {sets} WHERE id = ?", vals)
        conn.commit()
        return AuthDB.get_user(user_id) or {"error": "User not found"}

    @staticmethod
    def delete_user(user_id: str) -> bool:
        conn = _auth_conn()
        # Remove from teams first
        conn.execute("DELETE FROM user_team_members WHERE user_id = ?", (user_id,))
        # Remove teams where user is head (dissolve)
        head_teams = conn.execute("SELECT id FROM user_teams WHERE head_user_id = ?", (user_id,)).fetchall()
        for t in head_teams:
            conn.execute("DELETE FROM user_team_members WHERE team_id = ?", (t["id"],))
            conn.execute("DELETE FROM user_teams WHERE id = ?", (t["id"],))
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        conn.commit()
        return True

    @staticmethod
    def change_password(user_id: str, old_password: str, new_password: str) -> dict:
        if len(new_password) < 6:
            return {"error": "Password must be at least 6 characters"}
        conn = _auth_conn()
        row = conn.execute("SELECT password_hash FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row:
            return {"error": "User not found"}
        if not bcrypt.checkpw(old_password.encode(), row["password_hash"].encode()):
            return {"error": "Current password is incorrect"}
        new_hash = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt()).decode()
        conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (new_hash, user_id))
        conn.commit()
        return {"ok": True}

    @staticmethod
    def update_last_login(user_id: str):
        _auth_conn().execute("UPDATE users SET last_login = ? WHERE id = ?", (time.time(), user_id))
        _auth_conn().commit()

    # ── User Team CRUD ───────────────────────────────────────────────

    @staticmethod
    def create_team(name: str, head_user_id: str, description: str = "") -> dict:
        head = AuthDB.get_user(head_user_id)
        if not head:
            return {"error": "Head user not found"}
        if head["role"] not in ("poweruser", "admin"):
            return {"error": "Team head must be poweruser or admin"}
        tid = uuid.uuid4().hex[:12]
        try:
            conn = _auth_conn()
            conn.execute(
                "INSERT INTO user_teams (id, name, description, head_user_id, created_at) VALUES (?, ?, ?, ?, ?)",
                (tid, name, description, head_user_id, time.time()),
            )
            # Head is automatically a member
            conn.execute(
                "INSERT INTO user_team_members (user_id, team_id) VALUES (?, ?)",
                (head_user_id, tid),
            )
            conn.commit()
            return AuthDB.get_team(tid)
        except sqlite3.IntegrityError:
            return {"error": "Team name already exists"}

    @staticmethod
    def get_team(team_id: str) -> dict | None:
        conn = _auth_conn()
        row = conn.execute("SELECT * FROM user_teams WHERE id = ?", (team_id,)).fetchone()
        if not row:
            return None
        members = conn.execute(
            "SELECT u.id, u.username, u.display_name, u.role FROM users u "
            "JOIN user_team_members m ON u.id = m.user_id WHERE m.team_id = ?",
            (team_id,),
        ).fetchall()
        return {
            "id": row["id"], "name": row["name"], "description": row["description"],
            "head_user_id": row["head_user_id"], "created_at": row["created_at"],
            "members": [dict(m) for m in members],
        }

    @staticmethod
    def add_team_member(team_id: str, user_id: str) -> dict:
        conn = _auth_conn()
        team = conn.execute("SELECT * FROM user_teams WHERE id = ?", (team_id,)).fetchone()
        if not team:
            return {"error": "Team not found"}
        user = AuthDB.get_user(user_id)
        if not user:
            return {"error": "User not found"}
        try:
            conn.execute("INSERT INTO user_team_members (user_id, team_id) VALUES (?, ?)", (user_id, team_id))
            conn.commit()
        except sqlite3.IntegrityError:
            pass  # already a member
        return AuthDB.get_team(team_id)

    @staticmethod
    def remove_team_member(team_id: str, user_id: str) -> dict:
        conn = _auth_conn()
        team = conn.execute("SELECT * FROM user_teams WHERE id = ?", (team_id,)).fetchone()
        if not team:
            return {"error": "Team not found"}
        if team["head_user_id"] == user_id:
            return {"error": "Cannot remove team head from team"}
        conn.execute("DELETE FROM user_team_members WHERE user_id = ? AND team_id = ?", (user_id, team_id))
        conn.commit()
        return AuthDB.get_team(team_id)

    @staticmethod
    def update_team(team_id: str, updates: dict) -> dict:
        allowed = {"name", "description", "head_user_id"}
        fields = {k: v for k, v in updates.items() if k in allowed}
        if not fields:
            return {"error": "No valid fields to update"}
        if "head_user_id" in fields:
            head = AuthDB.get_user(fields["head_user_id"])
            if not head or head["role"] not in ("poweruser", "admin"):
                return {"error": "New head must be poweruser or admin"}
        conn = _auth_conn()
        sets = ", ".join(f"{k} = ?" for k in fields)
        vals = list(fields.values()) + [team_id]
        try:
            conn.execute(f"UPDATE user_teams SET {sets} WHERE id = ?", vals)
            conn.commit()
        except sqlite3.IntegrityError:
            return {"error": "Team name already exists"}
        return AuthDB.get_team(team_id) or {"error": "Team not found"}

    @staticmethod
    def delete_team(team_id: str) -> bool:
        conn = _auth_conn()
        conn.execute("DELETE FROM user_team_members WHERE team_id = ?", (team_id,))
        conn.execute("DELETE FROM user_teams WHERE id = ?", (team_id,))
        conn.commit()
        return True

    @staticmethod
    def get_user_teams(user_id: str) -> list[dict]:
        conn = _auth_conn()
        rows = conn.execute(
            "SELECT t.* FROM user_teams t JOIN user_team_members m ON t.id = m.team_id WHERE m.user_id = ?",
            (user_id,),
        ).fetchall()
        result = []
        for r in rows:
            members = conn.execute(
                "SELECT u.id, u.username, u.display_name, u.role FROM users u "
                "JOIN user_team_members m ON u.id = m.user_id WHERE m.team_id = ?",
                (r["id"],),
            ).fetchall()
            result.append({
                "id": r["id"], "name": r["name"], "description": r["description"],
                "head_user_id": r["head_user_id"], "created_at": r["created_at"],
                "members": [dict(m) for m in members],
            })
        return result

    @staticmethod
    def get_team_members(team_id: str) -> list[dict]:
        rows = _auth_conn().execute(
            "SELECT u.id, u.username, u.display_name, u.role, u.email FROM users u "
            "JOIN user_team_members m ON u.id = m.user_id WHERE m.team_id = ?",
            (team_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    @staticmethod
    def list_teams() -> list[dict]:
        conn = _auth_conn()
        rows = conn.execute("SELECT * FROM user_teams ORDER BY created_at").fetchall()
        result = []
        for r in rows:
            members = conn.execute(
                "SELECT u.id, u.username, u.display_name, u.role FROM users u "
                "JOIN user_team_members m ON u.id = m.user_id WHERE m.team_id = ?",
                (r["id"],),
            ).fetchall()
            result.append({
                "id": r["id"], "name": r["name"], "description": r["description"],
                "head_user_id": r["head_user_id"], "created_at": r["created_at"],
                "members": [dict(m) for m in members],
            })
        return result


# ── JWT ──────────────────────────────────────────────────────────────

def generate_token(user: dict) -> str:
    payload = {
        "user_id": user["id"],
        "username": user["username"],
        "role": user["role"],
        "exp": time.time() + token_expiry(),
        "iat": time.time(),
    }
    return pyjwt.encode(payload, _jwt_secret, algorithm="HS256")


def verify_token(token: str) -> dict | None:
    try:
        payload = pyjwt.decode(token, _jwt_secret, algorithms=["HS256"],
                                options={"verify_exp": False})
        if payload.get("exp", 0) < time.time():
            return None
        return payload
    except pyjwt.InvalidTokenError:
        return None


def refresh_token(token: str) -> str | None:
    payload = verify_token(token)
    if not payload:
        return None
    user = AuthDB.get_user(payload["user_id"])
    if not user or user.get("disabled"):
        return None
    return generate_token(user)


# ── Authorization Helpers ────────────────────────────────────────────

# Synthetic admin user returned when auth is disabled
SYNTHETIC_ADMIN = {
    "id": "__system__",
    "username": "system",
    "display_name": "System",
    "email": "",
    "role": "admin",
    "disabled": False,
}


def get_visible_user_ids(user: dict) -> list[str] | None:
    """Get list of user IDs whose data this user can see.
    Returns None for admin (meaning: all data visible).
    """
    if user["role"] == "admin" or user["id"] == "__system__":
        return None  # admin sees everything
    visible = [user["id"]]
    teams = AuthDB.get_user_teams(user["id"])
    for team in teams:
        if team["head_user_id"] == user["id"]:
            # Team head sees all member data
            for m in team.get("members", []):
                if m["id"] not in visible:
                    visible.append(m["id"])
    return visible


def can_access_session(user: dict, session_user_id: str) -> bool:
    if not session_user_id:
        return user["role"] == "admin" or user["id"] == "__system__"
    if user["role"] == "admin" or user["id"] == "__system__":
        return True
    if session_user_id == user["id"]:
        return True
    visible = get_visible_user_ids(user)
    return visible is None or session_user_id in visible


def can_access_project(user: dict, project_config: dict) -> bool:
    visibility = project_config.get("visibility", "global")
    if visibility == "global" or user["role"] == "admin" or user["id"] == "__system__":
        return True
    if visibility == "user":
        return project_config.get("owner_user_id") == user["id"]
    if visibility == "team":
        team_id = project_config.get("owner_team_id", "")
        if not team_id:
            return False
        teams = AuthDB.get_user_teams(user["id"])
        return any(t["id"] == team_id for t in teams)
    return False


def can_delete_resource(user: dict, owner_user_id: str, owner_team_id: str = "") -> bool:
    if user["role"] == "admin" or user["id"] == "__system__":
        return True
    if owner_user_id == user["id"]:
        return True
    if owner_team_id:
        teams = AuthDB.get_user_teams(user["id"])
        return any(t["id"] == owner_team_id and t["head_user_id"] == user["id"] for t in teams)
    return False


def can_manage_team(user: dict, team_id: str) -> bool:
    """Check if user can manage a specific team (is head or admin)."""
    if user["role"] == "admin" or user["id"] == "__system__":
        return True
    team = AuthDB.get_team(team_id)
    if not team:
        return False
    return team["head_user_id"] == user["id"]


# ── Internal ─────────────────────────────────────────────────────────

def _user_dict(row) -> dict:
    """Convert a DB row to a user dict (excludes password_hash)."""
    if row is None:
        return {}
    return {
        "id": row["id"],
        "username": row["username"],
        "display_name": row["display_name"],
        "email": row["email"],
        "role": row["role"],
        "created_at": row["created_at"],
        "last_login": row["last_login"],
        "disabled": bool(row["disabled"]),
    }
