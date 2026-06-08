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

AUTH_DB = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "agents", "main", "auth.db")

_auth_db_pool = threading.local()


def _auth_conn() -> sqlite3.Connection:
    """Thread-local SQLite connection for the auth DB."""
    conn = getattr(_auth_db_pool, "conn", None)
    if conn is None:
        os.makedirs(os.path.dirname(AUTH_DB), exist_ok=True)
        conn = sqlite3.connect(AUTH_DB, timeout=10, check_same_thread=False)
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.row_factory = sqlite3.Row
        _auth_db_pool.conn = conn
    return conn


# ── Config ───────────────────────────────────────────────────────────

_auth_config: dict = {}
_jwt_secret: str = ""

ROLES = ("admin", "poweruser", "user")

# Default capability flags by role. Admins bypass in all checks regardless,
# so this is mainly the starting point when an admin edits a user's caps.
CAPABILITY_DEFAULTS = {
    "admin":     {"allow_projects": True,  "allow_artifacts": True, "allow_workflows": True,  "allow_skills_install": True},
    "poweruser": {"allow_projects": True,  "allow_artifacts": True, "allow_workflows": False, "allow_skills_install": False},
    "user":      {"allow_projects": False, "allow_artifacts": True, "allow_workflows": False, "allow_skills_install": False},
}
CAPABILITY_KEYS = tuple(CAPABILITY_DEFAULTS["admin"].keys())

# User-controlled preferences. Anything in here is editable by the user
# themselves via /v1/auth/me/preferences — admin-only fields belong on the
# capabilities map, not here. Schema:
#   greeting_name             — what the agent should call the user (free text;
#                               falls back to display_name → username)
#   memory_chats_default      — default for new chat sessions: 0=off, 1=on, 2=auto
#                               (null = use server `mempalace.chat_sync.classifier
#                               .default_mode`)
#   memory_sched_default      — same shape, applied to scheduler-created sessions
#                               (null = use chat default)
#   daily_summary_enabled     — once-per-day digest of activity → memory drawer
#   daily_summary_hour_local  — 0–23, local hour to fire (default 6)
PREFERENCE_DEFAULTS = {
    "greeting_name": "",
    # Free-text "About me" fields, surfaced to the agent on the first turn of
    # each session (same preamble as greeting_name) so it has context without
    # a memory lookup. Long enough for a sentence or two each, capped to keep
    # the preamble token cost bounded.
    "job_description": "",
    "communication_preferences": "",
    "memory_chats_default": None,
    "memory_sched_default": None,
    "daily_summary_enabled": False,
    "daily_summary_hour_local": 6,
    # Cosmetic ASCII companion shown in the chat spinner + welcome screen.
    # Purely client-side — the server only stores/validates the choice.
    #   ""           — default-on: client auto-picks a species (deterministic
    #                  from the user id, so each user reliably gets "theirs")
    #   "off"        — disabled, classic wave-bar spinner only
    #   <species id> — a specific species from BUDDY_SPECIES
    "buddy_species": "",
}
PREFERENCE_KEYS = tuple(PREFERENCE_DEFAULTS.keys())

# Allowed buddy species ids. Mirrors BUDDY_SPECIES in web/js/buddy.js — keep
# the two in sync. Validated server-side so a bad client can't store junk that
# the renderer would then have to defend against.
BUDDY_SPECIES = ("cat", "fox", "dog", "bear", "panda", "frog", "owl", "penguin", "dragon", "crab")


def _coerce_pref(key: str, value):
    """Validate + coerce a single preference value to its allowed shape.
    Returns the coerced value, or raises ValueError on bad input."""
    if key not in PREFERENCE_DEFAULTS:
        raise ValueError(f"Unknown preference: {key}")
    if key == "greeting_name":
        s = (value or "").strip() if isinstance(value, str) else ""
        if len(s) > 64:
            raise ValueError("greeting_name too long (max 64)")
        return s
    if key == "job_description":
        s = (value or "").strip() if isinstance(value, str) else ""
        # Tight cap — this is "what's your role" not "tell me about yourself".
        if len(s) > 500:
            raise ValueError("job_description too long (max 500)")
        return s
    if key == "communication_preferences":
        s = (value or "").strip() if isinstance(value, str) else ""
        # Roomy cap — this is the per-user equivalent of soul.md, intended
        # to carry tone/voice/style guidance. 4000 chars ≈ ~1k tokens, which
        # is the upper end of what fits in a per-turn preamble without
        # crowding out the actual conversation.
        if len(s) > 4000:
            raise ValueError("communication_preferences too long (max 4000)")
        return s
    if key in ("memory_chats_default", "memory_sched_default"):
        if value is None or value == "":
            return None
        try:
            iv = int(value)
        except (TypeError, ValueError):
            raise ValueError(f"{key} must be 0|1|2|null")
        if iv not in (0, 1, 2):
            raise ValueError(f"{key} must be 0|1|2|null")
        return iv
    if key == "buddy_species":
        s = (value or "").strip().lower() if isinstance(value, str) else ""
        if s and s != "off" and s not in BUDDY_SPECIES:
            raise ValueError(f"buddy_species must be ''|'off'|{'|'.join(BUDDY_SPECIES)}")
        return s
    if key == "daily_summary_enabled":
        return bool(value)
    if key == "daily_summary_hour_local":
        try:
            iv = int(value)
        except (TypeError, ValueError):
            raise ValueError("daily_summary_hour_local must be 0..23")
        if not (0 <= iv <= 23):
            raise ValueError("daily_summary_hour_local must be 0..23")
        return iv
    return value


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
        config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json")
        try:
            with open(config_path, "w") as f:
                json.dump(config, f, indent=2)
        except Exception:
            pass

    AuthDB.init()


def auth_enabled() -> bool:
    return _auth_config.get("enabled", False)


def registration_enabled() -> bool:
    # Default-off: public self-registration is disabled unless explicitly enabled
    # in config. Users are provisioned via admin through /v1/auth/users.
    return _auth_config.get("registration_enabled", False)


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
                disabled INTEGER DEFAULT 0,
                -- capabilities: JSON object of feature flags (null = role defaults)
                capabilities TEXT DEFAULT NULL,
                -- preferences: JSON object of user-controlled prefs (greeting_name,
                -- memory_chats_default, memory_sched_default, daily_summary_enabled, …)
                preferences TEXT DEFAULT NULL
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

            -- Agent/model grants: row present => allowed. Admin always bypasses.
            -- Agents are global singletons; ACL is user→agent or team→agent.
            CREATE TABLE IF NOT EXISTS user_agent_permissions (
                user_id TEXT NOT NULL,
                agent_id TEXT NOT NULL,
                granted_at REAL DEFAULT (strftime('%s','now')),
                PRIMARY KEY (user_id, agent_id)
            );
            CREATE INDEX IF NOT EXISTS idx_uap_user ON user_agent_permissions(user_id);

            CREATE TABLE IF NOT EXISTS team_agent_permissions (
                team_id TEXT NOT NULL,
                agent_id TEXT NOT NULL,
                granted_at REAL DEFAULT (strftime('%s','now')),
                PRIMARY KEY (team_id, agent_id)
            );
            CREATE INDEX IF NOT EXISTS idx_tap_team ON team_agent_permissions(team_id);

            CREATE TABLE IF NOT EXISTS user_model_permissions (
                user_id TEXT NOT NULL,
                model_id TEXT NOT NULL,
                granted_at REAL DEFAULT (strftime('%s','now')),
                PRIMARY KEY (user_id, model_id)
            );
            CREATE INDEX IF NOT EXISTS idx_ump_user ON user_model_permissions(user_id);

            CREATE TABLE IF NOT EXISTS team_model_permissions (
                team_id TEXT NOT NULL,
                model_id TEXT NOT NULL,
                granted_at REAL DEFAULT (strftime('%s','now')),
                PRIMARY KEY (team_id, model_id)
            );
            CREATE INDEX IF NOT EXISTS idx_tmp_team ON team_model_permissions(team_id);

            -- Append-only audit log for admin-scope events.
            CREATE TABLE IF NOT EXISTS audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL DEFAULT (strftime('%s','now')),
                actor_user_id TEXT DEFAULT '',
                actor_username TEXT DEFAULT '',
                action TEXT NOT NULL,
                target TEXT DEFAULT '',
                details TEXT DEFAULT '',
                ip TEXT DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit_log(ts);
            CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit_log(actor_user_id);
            CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log(action);

            -- Per-user daily-summary cursor. The daemon stamps `last_run_ts`
            -- after a successful summary so it doesn't re-summarise the same
            -- window; `last_status` carries the most recent outcome string
            -- (skipped/no_activity/filed/error:<reason>) for debugging.
            CREATE TABLE IF NOT EXISTS user_daily_summary (
                user_id TEXT PRIMARY KEY,
                last_run_ts REAL DEFAULT 0,
                last_status TEXT DEFAULT '',
                last_drawer_id TEXT DEFAULT ''
            );
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

        # Migrate: add capabilities + preferences columns to existing DBs
        try:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()]
            if "capabilities" not in cols:
                conn.execute("ALTER TABLE users ADD COLUMN capabilities TEXT DEFAULT NULL")
            if "preferences" not in cols:
                conn.execute("ALTER TABLE users ADD COLUMN preferences TEXT DEFAULT NULL")
            conn.commit()
        except Exception:
            pass

        # Backfill default agent grants for non-admin users that predate the ACL.
        # Admins always bypass the ACL check, so they don't need rows. This keeps
        # pre-existing users functional after Step 5 rolls out.
        for agent_id in _default_allowed_agents():
            conn.execute(
                "INSERT OR IGNORE INTO user_agent_permissions (user_id, agent_id) "
                "SELECT id, ? FROM users WHERE role != 'admin'",
                (agent_id,),
            )
        conn.commit()

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
            # Default-allow: new users get access to the configured default
            # agent (typically "main"). Admins see everything regardless.
            for agent_id in _default_allowed_agents():
                conn.execute(
                    "INSERT OR IGNORE INTO user_agent_permissions (user_id, agent_id) VALUES (?, ?)",
                    (uid, agent_id),
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
        # Capabilities: only persist known keys, serialize as JSON
        if "capabilities" in updates and isinstance(updates["capabilities"], dict):
            override = {k: bool(v) for k, v in updates["capabilities"].items() if k in CAPABILITY_KEYS}
            fields["capabilities"] = json.dumps(override) if override else None
        if not fields:
            return {"error": "No valid fields to update"}
        conn = _auth_conn()
        sets = ", ".join(f"{k} = ?" for k in fields)
        vals = list(fields.values()) + [user_id]
        conn.execute(f"UPDATE users SET {sets} WHERE id = ?", vals)
        conn.commit()
        return AuthDB.get_user(user_id) or {"error": "User not found"}

    @staticmethod
    def update_self_profile(user_id: str, updates: dict) -> dict:
        """Self-service profile edit: display_name + email only. Role,
        disabled, capabilities — admin-only via update_user()."""
        allowed = {"display_name", "email"}
        fields = {k: (v or "").strip() if isinstance(v, str) else v
                  for k, v in updates.items() if k in allowed}
        if "display_name" in fields and len(fields["display_name"]) > 64:
            return {"error": "display_name too long (max 64)"}
        if "email" in fields and len(fields["email"]) > 128:
            return {"error": "email too long (max 128)"}
        if not fields:
            return {"error": "No valid fields to update"}
        conn = _auth_conn()
        sets = ", ".join(f"{k} = ?" for k in fields)
        vals = list(fields.values()) + [user_id]
        conn.execute(f"UPDATE users SET {sets} WHERE id = ?", vals)
        conn.commit()
        return AuthDB.get_user(user_id) or {"error": "User not found"}

    @staticmethod
    def update_preferences(user_id: str, updates: dict) -> dict:
        """Merge-update preferences. Unknown keys are silently dropped;
        invalid values return an error and persist nothing (atomic)."""
        if not isinstance(updates, dict):
            return {"error": "preferences must be a dict"}
        # Load current row
        row = _auth_conn().execute("SELECT preferences FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row:
            return {"error": "User not found"}
        current: dict = {}
        if row["preferences"]:
            try:
                stored = json.loads(row["preferences"])
                if isinstance(stored, dict):
                    current = stored
            except Exception:
                current = {}
        # Validate everything before writing anything
        coerced: dict = {}
        for k, v in updates.items():
            if k not in PREFERENCE_KEYS:
                continue
            try:
                coerced[k] = _coerce_pref(k, v)
            except ValueError as e:
                return {"error": str(e)}
        merged = {**current, **coerced}
        # Drop keys whose value matches the default — keeps the JSON small
        cleaned = {k: v for k, v in merged.items() if v != PREFERENCE_DEFAULTS.get(k)}
        payload = json.dumps(cleaned) if cleaned else None
        conn = _auth_conn()
        conn.execute("UPDATE users SET preferences = ? WHERE id = ?", (payload, user_id))
        conn.commit()
        return AuthDB.get_user(user_id) or {"error": "User not found"}

    @staticmethod
    def get_daily_summary_cursor(user_id: str) -> dict:
        row = _auth_conn().execute(
            "SELECT last_run_ts, last_status, last_drawer_id FROM user_daily_summary WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        if not row:
            return {"last_run_ts": 0.0, "last_status": "", "last_drawer_id": ""}
        return {
            "last_run_ts": float(row["last_run_ts"] or 0),
            "last_status": row["last_status"] or "",
            "last_drawer_id": row["last_drawer_id"] or "",
        }

    @staticmethod
    def set_daily_summary_cursor(user_id: str, ts: float, status: str, drawer_id: str = ""):
        conn = _auth_conn()
        conn.execute(
            "INSERT INTO user_daily_summary (user_id, last_run_ts, last_status, last_drawer_id) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET last_run_ts = excluded.last_run_ts, "
            "  last_status = excluded.last_status, last_drawer_id = excluded.last_drawer_id",
            (user_id, ts, status, drawer_id),
        )
        conn.commit()

    @staticmethod
    def list_users_with_preferences() -> list[dict]:
        """For the daily-summary daemon: only fields it actually needs."""
        rows = _auth_conn().execute(
            "SELECT id, username, display_name, email, role, disabled, preferences FROM users"
        ).fetchall()
        out: list[dict] = []
        for r in rows:
            if r["disabled"]:
                continue
            prefs = dict(PREFERENCE_DEFAULTS)
            if r["preferences"]:
                try:
                    stored = json.loads(r["preferences"])
                    if isinstance(stored, dict):
                        for k in PREFERENCE_KEYS:
                            if k in stored:
                                try:
                                    prefs[k] = _coerce_pref(k, stored[k])
                                except ValueError:
                                    pass
                except Exception:
                    pass
            out.append({
                "id": r["id"], "username": r["username"],
                "display_name": r["display_name"], "email": r["email"],
                "role": r["role"], "preferences": prefs,
            })
        return out

    @staticmethod
    def delete_user(user_id: str) -> bool:
        conn = _auth_conn()
        # Remove from teams first
        conn.execute("DELETE FROM user_team_members WHERE user_id = ?", (user_id,))
        # Remove teams where user is head (dissolve)
        head_teams = conn.execute("SELECT id FROM user_teams WHERE head_user_id = ?", (user_id,)).fetchall()
        for t in head_teams:
            conn.execute("DELETE FROM user_team_members WHERE team_id = ?", (t["id"],))
            conn.execute("DELETE FROM team_agent_permissions WHERE team_id = ?", (t["id"],))
            conn.execute("DELETE FROM team_model_permissions WHERE team_id = ?", (t["id"],))
            conn.execute("DELETE FROM user_teams WHERE id = ?", (t["id"],))
        # Purge the user's own grants
        conn.execute("DELETE FROM user_agent_permissions WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM user_model_permissions WHERE user_id = ?", (user_id,))
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
    def admin_reset_password(user_id: str, new_password: str) -> dict:
        """Admin-initiated password reset — no old_password required.

        Caller must already be authorized as admin (enforced at the endpoint).
        """
        if len(new_password) < 6:
            return {"error": "Password must be at least 6 characters"}
        conn = _auth_conn()
        row = conn.execute("SELECT id FROM users WHERE id = ?", (user_id,)).fetchone()
        if not row:
            return {"error": "User not found"}
        new_hash = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt()).decode()
        conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (new_hash, user_id))
        conn.commit()
        return {"ok": True}

    @staticmethod
    def update_last_login(user_id: str):
        conn = _auth_conn()
        conn.execute("UPDATE users SET last_login = ? WHERE id = ?", (time.time(), user_id))
        conn.commit()

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
        conn.execute("DELETE FROM team_agent_permissions WHERE team_id = ?", (team_id,))
        conn.execute("DELETE FROM team_model_permissions WHERE team_id = ?", (team_id,))
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

    # ── Agent / Model ACL ────────────────────────────────────────────

    @staticmethod
    def grant_agent(user_id: str = "", team_id: str = "", agent_id: str = "") -> dict:
        if not agent_id or (not user_id and not team_id):
            return {"error": "agent_id + (user_id or team_id) required"}
        conn = _auth_conn()
        if user_id:
            conn.execute("INSERT OR IGNORE INTO user_agent_permissions (user_id, agent_id) VALUES (?, ?)",
                         (user_id, agent_id))
        if team_id:
            conn.execute("INSERT OR IGNORE INTO team_agent_permissions (team_id, agent_id) VALUES (?, ?)",
                         (team_id, agent_id))
        conn.commit()
        return {"ok": True}

    @staticmethod
    def revoke_agent(user_id: str = "", team_id: str = "", agent_id: str = "") -> dict:
        if not agent_id or (not user_id and not team_id):
            return {"error": "agent_id + (user_id or team_id) required"}
        conn = _auth_conn()
        if user_id:
            conn.execute("DELETE FROM user_agent_permissions WHERE user_id = ? AND agent_id = ?",
                         (user_id, agent_id))
        if team_id:
            conn.execute("DELETE FROM team_agent_permissions WHERE team_id = ? AND agent_id = ?",
                         (team_id, agent_id))
        conn.commit()
        return {"ok": True}

    @staticmethod
    def grant_model(user_id: str = "", team_id: str = "", model_id: str = "") -> dict:
        if not model_id or (not user_id and not team_id):
            return {"error": "model_id + (user_id or team_id) required"}
        conn = _auth_conn()
        if user_id:
            conn.execute("INSERT OR IGNORE INTO user_model_permissions (user_id, model_id) VALUES (?, ?)",
                         (user_id, model_id))
        if team_id:
            conn.execute("INSERT OR IGNORE INTO team_model_permissions (team_id, model_id) VALUES (?, ?)",
                         (team_id, model_id))
        conn.commit()
        return {"ok": True}

    @staticmethod
    def revoke_model(user_id: str = "", team_id: str = "", model_id: str = "") -> dict:
        if not model_id or (not user_id and not team_id):
            return {"error": "model_id + (user_id or team_id) required"}
        conn = _auth_conn()
        if user_id:
            conn.execute("DELETE FROM user_model_permissions WHERE user_id = ? AND model_id = ?",
                         (user_id, model_id))
        if team_id:
            conn.execute("DELETE FROM team_model_permissions WHERE team_id = ? AND model_id = ?",
                         (team_id, model_id))
        conn.commit()
        return {"ok": True}

    @staticmethod
    def get_user_allowed_agents(user_id: str) -> set[str]:
        conn = _auth_conn()
        direct = {r[0] for r in conn.execute(
            "SELECT agent_id FROM user_agent_permissions WHERE user_id = ?", (user_id,)).fetchall()}
        via_team = {r[0] for r in conn.execute(
            "SELECT tap.agent_id FROM team_agent_permissions tap "
            "JOIN user_team_members m ON m.team_id = tap.team_id WHERE m.user_id = ?",
            (user_id,)).fetchall()}
        return direct | via_team

    @staticmethod
    def get_user_allowed_models(user_id: str) -> set[str]:
        conn = _auth_conn()
        direct = {r[0] for r in conn.execute(
            "SELECT model_id FROM user_model_permissions WHERE user_id = ?", (user_id,)).fetchall()}
        via_team = {r[0] for r in conn.execute(
            "SELECT tmp.model_id FROM team_model_permissions tmp "
            "JOIN user_team_members m ON m.team_id = tmp.team_id WHERE m.user_id = ?",
            (user_id,)).fetchall()}
        return direct | via_team

    @staticmethod
    def list_user_grants(user_id: str) -> dict:
        """Return {agents: [...], models: [...], teams_granting_agents: {...}, teams_granting_models: {...}}
        split into direct and via-team for admin UI display.
        """
        conn = _auth_conn()
        direct_agents = [r[0] for r in conn.execute(
            "SELECT agent_id FROM user_agent_permissions WHERE user_id = ? ORDER BY agent_id",
            (user_id,)).fetchall()]
        direct_models = [r[0] for r in conn.execute(
            "SELECT model_id FROM user_model_permissions WHERE user_id = ? ORDER BY model_id",
            (user_id,)).fetchall()]
        team_agents = [dict(r) for r in conn.execute(
            "SELECT t.id as team_id, t.name as team_name, tap.agent_id "
            "FROM team_agent_permissions tap "
            "JOIN user_team_members m ON m.team_id = tap.team_id "
            "JOIN user_teams t ON t.id = tap.team_id "
            "WHERE m.user_id = ? ORDER BY t.name, tap.agent_id",
            (user_id,)).fetchall()]
        team_models = [dict(r) for r in conn.execute(
            "SELECT t.id as team_id, t.name as team_name, tmp.model_id "
            "FROM team_model_permissions tmp "
            "JOIN user_team_members m ON m.team_id = tmp.team_id "
            "JOIN user_teams t ON t.id = tmp.team_id "
            "WHERE m.user_id = ? ORDER BY t.name, tmp.model_id",
            (user_id,)).fetchall()]
        return {
            "agents_direct": direct_agents,
            "models_direct": direct_models,
            "agents_via_team": team_agents,
            "models_via_team": team_models,
        }

    @staticmethod
    def list_team_grants(team_id: str) -> dict:
        conn = _auth_conn()
        agents = [r[0] for r in conn.execute(
            "SELECT agent_id FROM team_agent_permissions WHERE team_id = ? ORDER BY agent_id",
            (team_id,)).fetchall()]
        models = [r[0] for r in conn.execute(
            "SELECT model_id FROM team_model_permissions WHERE team_id = ? ORDER BY model_id",
            (team_id,)).fetchall()]
        return {"agents": agents, "models": models}

    # ── Audit Log ────────────────────────────────────────────────────

    @staticmethod
    def audit_write(actor: dict | None, action: str, target: str = "",
                    details: dict | None = None, ip: str = "") -> None:
        """Append a row to the audit log. Fail-silently — audit must never
        block the primary action."""
        try:
            actor_uid = (actor or {}).get("id", "") if actor else ""
            actor_uname = (actor or {}).get("username", "") if actor else ""
            details_str = json.dumps(details, default=str) if details else ""
            conn = _auth_conn()
            conn.execute(
                "INSERT INTO audit_log (actor_user_id, actor_username, action, target, details, ip) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (actor_uid, actor_uname, action, target or "", details_str, ip or ""),
            )
            conn.commit()
        except Exception:
            pass

    @staticmethod
    def audit_read(limit: int = 100, actor: str = "", action: str = "",
                   since_ts: float = 0.0) -> list[dict]:
        conn = _auth_conn()
        q = "SELECT * FROM audit_log WHERE 1=1"
        params: list = []
        if actor:
            q += " AND (actor_user_id = ? OR actor_username = ?)"
            params += [actor, actor]
        if action:
            q += " AND action LIKE ?"
            params.append(f"%{action}%")
        if since_ts:
            q += " AND ts >= ?"
            params.append(float(since_ts))
        q += " ORDER BY id DESC LIMIT ?"
        params.append(int(limit))
        rows = conn.execute(q, params).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            if d.get("details"):
                try:
                    d["details"] = json.loads(d["details"])
                except Exception:
                    pass
            out.append(d)
        return out


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
        # A validly-signed token whose payload lacks the identity claim (an
        # older/foreign token shape signed with the same secret) is unusable —
        # reject it as unauthenticated rather than letting callers KeyError on
        # payload["user_id"].
        if not payload.get("user_id"):
            return None
        return payload
    except pyjwt.InvalidTokenError:
        return None


def refresh_token(token: str) -> str | None:
    payload = verify_token(token)
    if not payload or not payload.get("user_id"):
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


# ── Generic sharing / visibility model ───────────────────────────────
# One mechanism — private · users · team · global — applied to chats,
# projects, scheduled tasks, workflows, and artifacts. The "block" is a
# dict with keys: owner_user_id, visibility, owner_team_id,
# extra_member_user_ids, excluded_user_ids.

VISIBILITY_VALUES = ("private", "users", "team", "global")
_LEGACY_VIS_ALIAS = {"user": "private"}  # legacy project.json value

# Total order for narrow-only comparisons (artifacts). "users" and "team"
# are not directly comparable; callers handle that case explicitly.
_VIS_RANK = {"private": 0, "users": 1, "team": 1, "global": 2}


def normalize_visibility(v) -> str:
    v = (v or "private")
    return _LEGACY_VIS_ALIAS.get(v, v)


def can_access(user: dict, block: dict, legacy_open: bool = False) -> bool:
    """Generic read-access check over a five-field visibility block.

    block = {owner_user_id, visibility, owner_team_id,
             extra_member_user_ids, excluded_user_ids}

    legacy_open: when True and the block has no owner_user_id, grant access
    to any authenticated user (the pre-ownership behaviour for chats /
    workflows). When False, an owner-less block is admin-only.
    """
    if not user:
        return False
    if user["role"] == "admin" or user["id"] == "__system__":
        return True
    owner = block.get("owner_user_id") or ""
    if owner and owner == user["id"]:
        return True
    if not owner:
        return bool(legacy_open)
    extras = block.get("extra_member_user_ids") or []
    if user["id"] in extras:
        return True
    vis = normalize_visibility(block.get("visibility"))
    if vis == "global":
        return user["id"] not in (block.get("excluded_user_ids") or [])
    if vis in ("private", "users"):
        return False  # owner + extras only (handled above)
    if vis == "team":
        tid = block.get("owner_team_id") or ""
        if not tid:
            return False
        return any(t["id"] == tid for t in AuthDB.get_user_teams(user["id"]))
    return False


def can_manage(user: dict, block: dict) -> bool:
    """Edit metadata, change visibility, edit ACL, transfer, delete.
    Strictly owner-or-admin — no team-head shortcut for items."""
    if not user:
        return False
    if user["role"] == "admin" or user["id"] == "__system__":
        return True
    owner = block.get("owner_user_id") or ""
    return bool(owner) and owner == user["id"]


def normalize_share_block(block: dict) -> dict:
    """Normalise a block on save: drop grants/excludes that the chosen
    visibility makes meaningless, and the owner is never excludable."""
    out = dict(block)
    vis = normalize_visibility(out.get("visibility"))
    out["visibility"] = vis
    owner = out.get("owner_user_id") or ""
    extras = [u for u in (out.get("extra_member_user_ids") or []) if u and u != owner]
    excluded = [u for u in (out.get("excluded_user_ids") or []) if u and u != owner]
    if vis == "global":
        extras = []  # everyone already has access
    else:
        excluded = []  # no allow-list to subtract from
    out["extra_member_user_ids"] = extras
    out["excluded_user_ids"] = excluded
    if vis != "team":
        out["owner_team_id"] = ""
    return out


def can_access_project(user: dict, project_config: dict) -> bool:
    if user["role"] == "admin" or user["id"] == "__system__":
        return True
    owner_uid = project_config.get("owner_user_id", "")
    extras = project_config.get("extra_member_user_ids") or []
    excluded = project_config.get("excluded_user_ids") or []
    if owner_uid and owner_uid == user["id"]:
        return True
    if user["id"] in extras:
        return True
    # Visibility arms apply regardless of whether owner is set (legacy
    # projects may lack owner_user_id but still carry team/global visibility).
    vis = normalize_visibility(project_config.get("visibility", "global"))
    if vis == "global":
        return user["id"] not in excluded
    if vis in ("private", "users"):
        return False  # owner + extras only (handled above)
    if vis == "team":
        team_id = project_config.get("owner_team_id", "")
        if not team_id:
            return False
        return any(t["id"] == team_id for t in AuthDB.get_user_teams(user["id"]))
    return False


def can_manage_project(user: dict, project_config: dict) -> bool:
    """Project management (edit, add/remove members, archive, delete).
    Owner-centric: only the named owner_user_id or admin."""
    return can_manage(user, {"owner_user_id": project_config.get("owner_user_id", "")})


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


def _default_allowed_agents() -> list[str]:
    """Agents every new user is auto-granted. Configurable via
    config.auth.default_allowed_agents (defaults to ["main"])."""
    cfg = _auth_config.get("default_allowed_agents", ["main"])
    if not isinstance(cfg, list):
        return ["main"]
    return [str(a) for a in cfg if a]


def can_access_agent(user: dict, agent_id: str) -> bool:
    """Admin + system bypass. Regular users need a direct or via-team grant.
    When auth is disabled the SYNTHETIC_ADMIN is used, so this returns True."""
    if not user:
        return False
    if user["role"] == "admin" or user["id"] == "__system__":
        return True
    allowed = AuthDB.get_user_allowed_agents(user["id"])
    return agent_id in allowed


def can_access_model(user: dict, model_id: str) -> bool:
    if not user:
        return False
    if user["role"] == "admin" or user["id"] == "__system__":
        return True
    allowed = AuthDB.get_user_allowed_models(user["id"])
    return model_id in allowed


# ── Internal ─────────────────────────────────────────────────────────

def _user_dict(row) -> dict:
    """Convert a DB row to a user dict (excludes password_hash)."""
    if row is None:
        return {}
    # Merge per-user capability overrides with role defaults
    role = row["role"]
    caps = dict(CAPABILITY_DEFAULTS.get(role, CAPABILITY_DEFAULTS["user"]))
    raw = None
    try:
        raw = row["capabilities"]
    except (IndexError, KeyError):
        raw = None
    if raw:
        try:
            override = json.loads(raw)
            if isinstance(override, dict):
                for k in CAPABILITY_KEYS:
                    if k in override:
                        caps[k] = bool(override[k])
        except Exception:
            pass
    prefs = dict(PREFERENCE_DEFAULTS)
    pref_raw = None
    try:
        pref_raw = row["preferences"]
    except (IndexError, KeyError):
        pref_raw = None
    if pref_raw:
        try:
            stored = json.loads(pref_raw)
            if isinstance(stored, dict):
                for k in PREFERENCE_KEYS:
                    if k in stored:
                        try:
                            prefs[k] = _coerce_pref(k, stored[k])
                        except ValueError:
                            pass
        except Exception:
            pass
    return {
        "id": row["id"],
        "username": row["username"],
        "display_name": row["display_name"],
        "email": row["email"],
        "role": role,
        "created_at": row["created_at"],
        "last_login": row["last_login"],
        "disabled": bool(row["disabled"]),
        "capabilities": caps,
        "preferences": prefs,
    }


def has_capability(user: dict, cap: str) -> bool:
    """Check a capability flag. Admins always pass."""
    if not user:
        return False
    if user.get("role") == "admin" or user.get("id") == "__system__":
        return True
    caps = user.get("capabilities") or {}
    return bool(caps.get(cap, False))
