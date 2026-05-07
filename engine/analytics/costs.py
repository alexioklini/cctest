# Extracted from claude_cli.py — cost tracking

import json
import logging
import os
import sqlite3
import threading

from engine.agents import AGENTS_DIR  # noqa: F401 — needed at module level

# --- Cost Tracking ---

COST_DB = os.path.join(AGENTS_DIR, "main", "costs.db")

_cost_db_pool = threading.local()


def _cost_conn():
    """Thread-local SQLite connection for the cost DB."""
    conn = getattr(_cost_db_pool, "conn", None)
    if conn is None:
        conn = sqlite3.connect(COST_DB, timeout=10, check_same_thread=False)
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA journal_mode = WAL")
        _cost_db_pool.conn = conn
    return conn


# Default cost rates per 1M tokens — 0 for free providers
_cost_rates: dict[str, dict[str, float]] = {
    # Anthropic — per-million-token rates (USD)
    "claude-opus-4-6":            {"input": 15.0,  "output": 75.0},
    "claude-opus-4-5-20251101":   {"input": 15.0,  "output": 75.0},
    "claude-opus-4-20250514":     {"input": 15.0,  "output": 75.0},
    "claude-sonnet-4-6":          {"input": 3.0,   "output": 15.0},
    "claude-sonnet-4-5-20241022": {"input": 3.0,   "output": 15.0},
    "claude-sonnet-4-20250514":   {"input": 3.0,   "output": 15.0},
    "claude-haiku-4-5-20251001":  {"input": 0.80,  "output": 4.0},
    "claude-haiku-3.5":           {"input": 0.80,  "output": 4.0},
    "claude-3-5-haiku-20241022":  {"input": 0.80,  "output": 4.0},
    "claude-3-7-sonnet-20250219": {"input": 3.0,   "output": 15.0},
    # OpenAI
    "gpt-4o":                     {"input": 2.50,  "output": 10.0},
    "gpt-4o-2024-11-20":          {"input": 2.50,  "output": 10.0},
    "gpt-4o-2024-08-06":          {"input": 2.50,  "output": 10.0},
    "gpt-4o-mini":                {"input": 0.15,  "output": 0.60},
    "gpt-4o-mini-2024-07-18":     {"input": 0.15,  "output": 0.60},
    "gpt-4.1":                    {"input": 2.0,   "output": 8.0},
    "gpt-4.1-mini":               {"input": 0.40,  "output": 1.60},
    "gpt-4.1-nano":               {"input": 0.10,  "output": 0.40},
    "o1":                         {"input": 15.0,  "output": 60.0},
    "o1-mini":                    {"input": 3.0,   "output": 12.0},
    "o3":                         {"input": 2.0,   "output": 8.0},
    "o3-mini":                    {"input": 1.10,  "output": 4.40},
    "o4-mini":                    {"input": 1.10,  "output": 4.40},
    # Mistral
    "mistral-large-latest":       {"input": 2.0,   "output": 6.0},
    "mistral-large-2411":         {"input": 2.0,   "output": 6.0},
    "mistral-medium-latest":      {"input": 0.40,  "output": 2.0},
    "mistral-small-latest":       {"input": 0.20,  "output": 0.60},
    "mistral-small-2603":         {"input": 0.20,  "output": 0.60},
    "mistralai/mistral-small-2603": {"input": 0.20, "output": 0.60},
    "codestral-latest":           {"input": 0.30,  "output": 0.90},
    "codestral-2508":             {"input": 0.30,  "output": 0.90},
    "devstral-medium-latest":     {"input": 0.40,  "output": 2.0},
    "devstral-small-2507":        {"input": 0.10,  "output": 0.30},
    "magistral-medium-latest":    {"input": 2.0,   "output": 5.0},
    "magistral-small-2509":       {"input": 0.50,  "output": 1.50},
    "ministral-8b-latest":        {"input": 0.10,  "output": 0.10},
    "ministral-3b-latest":        {"input": 0.04,  "output": 0.04},
    "pixtral-large-latest":       {"input": 2.0,   "output": 6.0},
    # Google Gemini
    "gemini-2.5-pro":             {"input": 1.25,  "output": 10.0},
    "gemini-2.5-flash":           {"input": 0.30,  "output": 2.50},
    "gemini-2.5-flash-lite":      {"input": 0.10,  "output": 0.40},
    "gemini-2.0-flash":           {"input": 0.10,  "output": 0.40},
    "gemini-2.0-flash-lite":      {"input": 0.075, "output": 0.30},
    "gemini-1.5-pro":             {"input": 1.25,  "output": 5.0},
    "gemini-1.5-flash":           {"input": 0.075, "output": 0.30},
    # xAI Grok
    "grok-2":                     {"input": 2.0,   "output": 10.0},
    "grok-2-latest":              {"input": 2.0,   "output": 10.0},
    "grok-3":                     {"input": 3.0,   "output": 15.0},
    "grok-3-mini":                {"input": 0.30,  "output": 0.50},
    # DeepSeek
    "deepseek-chat":              {"input": 0.27,  "output": 1.10},
    "deepseek-reasoner":          {"input": 0.55,  "output": 2.19},
    # Local / free (explicit zero so they're not reported as "unknown")
    "OMLX/":                      {"input": 0.0,   "output": 0.0},
    "Bifrost/local":              {"input": 0.0,   "output": 0.0},
    # Prefix patterns for future / aliased model IDs (checked after exact matches)
    "claude-opus":                {"input": 15.0,  "output": 75.0},
    "claude-sonnet":              {"input": 3.0,   "output": 15.0},
    "claude-haiku":               {"input": 0.80,  "output": 4.0},
    "gpt-4o-mini":                {"input": 0.15,  "output": 0.60},
    "gpt-4o":                     {"input": 2.50,  "output": 10.0},
    "gpt-4.1-nano":               {"input": 0.10,  "output": 0.40},
    "gpt-4.1-mini":               {"input": 0.40,  "output": 1.60},
    "gpt-4.1":                    {"input": 2.0,   "output": 8.0},
    "mistral-large":              {"input": 2.0,   "output": 6.0},
    "mistral-medium":             {"input": 0.40,  "output": 2.0},
    "mistral-small":              {"input": 0.20,  "output": 0.60},
    "codestral":                  {"input": 0.30,  "output": 0.90},
    "devstral-medium":            {"input": 0.40,  "output": 2.0},
    "devstral-small":             {"input": 0.10,  "output": 0.30},
    "magistral-medium":           {"input": 2.0,   "output": 5.0},
    "magistral-small":            {"input": 0.50,  "output": 1.50},
    "gemini-2.5-pro":             {"input": 1.25,  "output": 10.0},
    "gemini-2.5-flash-lite":      {"input": 0.10,  "output": 0.40},
    "gemini-2.5-flash":           {"input": 0.30,  "output": 2.50},
    "gemini-2.0-flash-lite":      {"input": 0.075, "output": 0.30},
    "gemini-2.0-flash":           {"input": 0.10,  "output": 0.40},
    "gemini-1.5-pro":             {"input": 1.25,  "output": 5.0},
    "gemini-1.5-flash":           {"input": 0.075, "output": 0.30},
}


def _get_cost_rate(model: str) -> dict[str, float]:
    """Look up cost rate for a model. Checks _models_config first, then defaults.
    Config values of 0 are treated as unset — auto-discovery writes 0 for all models."""
    cfg = _models_config.get(model, {})
    ci = cfg.get("cost_input")
    co = cfg.get("cost_output")
    if ci is not None and co is not None and (float(ci) > 0 or float(co) > 0):
        return {"input": float(ci), "output": float(co)}
    # Check built-in rates
    if model in _cost_rates:
        return _cost_rates[model]
    # Try prefix matching (e.g. "claude-opus-4-6" matches "claude-opus-4-6-20260101")
    ml = model.lower()
    for pattern, rate in _cost_rates.items():
        if ml.startswith(pattern.lower()) or pattern.lower() in ml:
            return rate
    return {"input": 0.0, "output": 0.0}


def _compute_cost(model: str, tokens_in: int, tokens_out: int) -> float:
    """Compute estimated cost in USD."""
    rate = _get_cost_rate(model)
    return (tokens_in * rate["input"] + tokens_out * rate["output"]) / 1_000_000


class CostTracker:
    """Thread-safe cost tracking with SQLite persistence."""

    def __init__(self):
        os.makedirs(os.path.dirname(COST_DB), exist_ok=True)
        self._init_db()

    def _init_db(self):
        with _cost_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS cost_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    agent TEXT NOT NULL,
                    session_id TEXT,
                    user_id TEXT NOT NULL DEFAULT '',
                    model TEXT NOT NULL,
                    provider TEXT NOT NULL DEFAULT '',
                    key_name TEXT NOT NULL DEFAULT '',
                    tokens_in INTEGER NOT NULL DEFAULT 0,
                    tokens_out INTEGER NOT NULL DEFAULT 0,
                    cost_usd REAL NOT NULL DEFAULT 0.0,
                    tool_round INTEGER DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT (datetime('now'))
                )
            """)
            # Migrations
            try:
                cols = {r[1] for r in conn.execute("PRAGMA table_info(cost_log)").fetchall()}
                if "user_id" not in cols:
                    conn.execute("ALTER TABLE cost_log ADD COLUMN user_id TEXT NOT NULL DEFAULT ''")
                if "key_name" not in cols:
                    conn.execute("ALTER TABLE cost_log ADD COLUMN key_name TEXT NOT NULL DEFAULT ''")
            except sqlite3.Error as e:
                logging.warning(f"cost_log migration: {e}")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_cost_agent ON cost_log(agent)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_cost_session ON cost_log(session_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_cost_created ON cost_log(created_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_cost_user ON cost_log(user_id, created_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_cost_provider ON cost_log(provider, key_name, created_at)")
            conn.commit()

    def log_call(self, agent: str, session_id: str, model: str, provider: str,
                 tokens_in: int, tokens_out: int, tool_round: int = 0,
                 user_id: str = "", key_name: str = ""):
        """Log an LLM call with cost estimation."""
        cost = _compute_cost(model, tokens_in, tokens_out)
        try:
            with _cost_conn() as conn:
                conn.execute("""
                    INSERT INTO cost_log (agent, session_id, user_id, model, provider, key_name, tokens_in, tokens_out, cost_usd, tool_round)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (agent, session_id or "", user_id or "", model, provider, key_name or "", tokens_in, tokens_out, cost, tool_round))
                conn.commit()
        except (sqlite3.Error, OSError) as e:
            logging.warning(f"Cost tracking error: {e}")

    def log_ocr(self, agent: str, session_id: str, model: str, provider: str,
                pages: int, cost_usd: float, user_id: str = "",
                key_name: str = ""):
        """Log an OCR call as a synthetic cost row. OCR is billed per-page,
        not per-token, so we stash the page count in tokens_in (output stays
        0) and write the explicit USD cost — bypassing _compute_cost which
        only knows about tokens. Aggregates (per_provider_key_stats /
        get_stats) sum cost_usd correctly without changes; per-token reports
        will see 'tokens_in' weighted by pages, which is acceptable since
        OCR is a small share of overall traffic and pages are interpretable
        on their own."""
        try:
            with _cost_conn() as conn:
                conn.execute("""
                    INSERT INTO cost_log (agent, session_id, user_id, model, provider, key_name, tokens_in, tokens_out, cost_usd, tool_round)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (agent, session_id or "", user_id or "", model, provider, key_name or "", int(pages), 0, float(cost_usd), 0))
                conn.commit()
        except (sqlite3.Error, OSError) as e:
            logging.warning(f"OCR cost tracking error: {e}")

    def per_provider_key_stats(self, days: int = 30) -> list[dict]:
        """Return per-provider + per-key call/token/cost aggregates."""
        try:
            with _cost_conn() as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute("""
                    SELECT provider, key_name,
                           COUNT(*) AS calls,
                           SUM(tokens_in) AS tokens_in,
                           SUM(tokens_out) AS tokens_out,
                           SUM(cost_usd) AS cost_usd,
                           MAX(created_at) AS last_used
                    FROM cost_log
                    WHERE created_at >= datetime('now', ?)
                      AND provider != ''
                    GROUP BY provider, key_name
                    ORDER BY provider, key_name
                """, (f"-{days} days",)).fetchall()
                return [dict(r) for r in rows]
        except (sqlite3.Error, OSError) as e:
            logging.warning(f"per_provider_key_stats error: {e}")
            return []

    def get_stats(self, agent: str | None = None, hours: int = 24,
                  user_id: str | None = None) -> dict:
        """Get aggregate stats for the last N hours."""
        try:
            with _cost_conn() as conn:
                conn.row_factory = sqlite3.Row
                where = "WHERE created_at >= datetime('now', ?)"
                params: list = [f"-{hours} hours"]
                if agent:
                    where += " AND agent = ?"
                    params.append(agent)
                if user_id is not None:
                    where += " AND user_id = ?"
                    params.append(user_id)
                row = conn.execute(f"""
                    SELECT COUNT(*) as total_calls,
                           COALESCE(SUM(tokens_in), 0) as total_tokens_in,
                           COALESCE(SUM(tokens_out), 0) as total_tokens_out,
                           COALESCE(SUM(cost_usd), 0.0) as total_cost
                    FROM cost_log {where}
                """, params).fetchone()
                # Per-agent breakdown
                agents_rows = conn.execute(f"""
                    SELECT agent,
                           COUNT(*) as calls,
                           COALESCE(SUM(tokens_in), 0) as tokens_in,
                           COALESCE(SUM(tokens_out), 0) as tokens_out,
                           COALESCE(SUM(cost_usd), 0.0) as cost
                    FROM cost_log {where}
                    GROUP BY agent ORDER BY cost DESC
                """, params).fetchall()
                # Per-model breakdown
                models_rows = conn.execute(f"""
                    SELECT model,
                           COUNT(*) as calls,
                           COALESCE(SUM(cost_usd), 0.0) as cost
                    FROM cost_log {where}
                    GROUP BY model ORDER BY cost DESC
                """, params).fetchall()
                return {
                    "total_calls": row["total_calls"],
                    "total_tokens_in": row["total_tokens_in"],
                    "total_tokens_out": row["total_tokens_out"],
                    "total_cost": round(row["total_cost"], 4),
                    "hours": hours,
                    "agent_filter": agent,
                    "by_agent": [dict(r) for r in agents_rows],
                    "by_model": [dict(r) for r in models_rows],
                }
        except (sqlite3.Error, OSError) as e:
            logging.warning(f"Cost stats error: {e}")
            return {"total_calls": 0, "total_tokens_in": 0, "total_tokens_out": 0,
                    "total_cost": 0.0, "hours": hours, "agent_filter": agent,
                    "by_agent": [], "by_model": []}

    def get_daily(self, agent: str | None = None, days: int = 7,
                  user_id: str | None = None) -> list[dict]:
        """Get daily breakdown for the last N days."""
        try:
            with _cost_conn() as conn:
                conn.row_factory = sqlite3.Row
                where = "WHERE created_at >= datetime('now', ?)"
                params: list = [f"-{days} days"]
                if agent:
                    where += " AND agent = ?"
                    params.append(agent)
                if user_id is not None:
                    where += " AND user_id = ?"
                    params.append(user_id)
                rows = conn.execute(f"""
                    SELECT date(created_at) as day,
                           COUNT(*) as calls,
                           COALESCE(SUM(tokens_in), 0) as tokens_in,
                           COALESCE(SUM(tokens_out), 0) as tokens_out,
                           COALESCE(SUM(cost_usd), 0.0) as cost
                    FROM cost_log {where}
                    GROUP BY date(created_at) ORDER BY day DESC
                """, params).fetchall()
                return [dict(r) for r in rows]
        except (sqlite3.Error, OSError) as e:
            logging.warning(f"Cost daily error: {e}")
            return []

    def sum_user_window(self, user_id: str, since_iso: str) -> float:
        """Sum cost_usd for a user since a given ISO timestamp (UTC)."""
        try:
            with _cost_conn() as conn:
                row = conn.execute("""
                    SELECT COALESCE(SUM(cost_usd), 0.0) AS c
                    FROM cost_log
                    WHERE user_id = ? AND created_at >= ?
                """, (user_id, since_iso)).fetchone()
                return float(row[0] or 0.0)
        except (sqlite3.Error, OSError):
            return 0.0

    def per_model_user_window(self, user_id: str, since_iso: str) -> list[dict]:
        """Per-model breakdown for a user since ISO timestamp."""
        try:
            with _cost_conn() as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute("""
                    SELECT model,
                           COUNT(*) AS calls,
                           COALESCE(SUM(tokens_in), 0) AS tokens_in,
                           COALESCE(SUM(tokens_out), 0) AS tokens_out,
                           COALESCE(SUM(cost_usd), 0.0) AS cost
                    FROM cost_log
                    WHERE user_id = ? AND created_at >= ?
                    GROUP BY model ORDER BY cost DESC
                """, (user_id, since_iso)).fetchall()
                return [dict(r) for r in rows]
        except (sqlite3.Error, OSError):
            return []

    def get_session_cost(self, session_id: str) -> dict:
        """Get cost for a specific session."""
        try:
            with _cost_conn() as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute("""
                    SELECT COUNT(*) as calls,
                           COALESCE(SUM(tokens_in), 0) as tokens_in,
                           COALESCE(SUM(tokens_out), 0) as tokens_out,
                           COALESCE(SUM(cost_usd), 0.0) as cost
                    FROM cost_log WHERE session_id = ?
                """, (session_id,)).fetchone()
                return dict(row) if row else {"calls": 0, "tokens_in": 0, "tokens_out": 0, "cost": 0.0}
        except (sqlite3.Error, OSError):
            return {"calls": 0, "tokens_in": 0, "tokens_out": 0, "cost": 0.0}


_cost_tracker: CostTracker | None = None
