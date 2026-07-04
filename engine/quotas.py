"""engine/quotas.py — Cost tracking + per-user quotas + per-agent rate limiting.

Extracted from brain.py (refactor B4). Owns:
  - the cost DB pool (`COST_DB`, `_cost_db_pool`, `_cost_conn`)
  - default cost rates (`_cost_rates`) + `_get_cost_rate` / `_compute_cost`
  - `class CostTracker` (logs every LLM call to costs.db)
  - `QUOTA_DEFAULTS`, `_quota_default_role_limits`
  - `class QuotaExceededError` (raised by the hard_block / force_local gate)
  - `class QuotaManager` (per-user daily/cycle quotas, 30s config cache)
  - `class RateLimiter` (sliding-window per agent from rate_limits in agent.json)

Single module (not split cost.py/quotas.py): QuotaManager reads CostTracker's
data, QuotaExceededError is the quota contract, and the rate helpers are tiny —
a split would only add a cross-module dependency with no independent reuse.

Seams:
  - `is_model_local` (used by QuotaManager.check_request) is a SHARED utility
    that stays in brain — reached lazily via `import brain as _brain`.
  - `_get_cost_rate` reads brain's live `_models_config` (rebound at runtime)
    — also reached lazily.
  - `_log_call_cost` stays in brain.py: it's coupled to brain runtime globals
    (`_key_pools`, `_current_agent`, `_thread_local`, `_rate_limiter`,
    `_cost_tracker`) and is the single write path. It calls `_compute_cost`
    and `CostTracker.log_call` via the brain re-export aliases.
  - The singletons (`_cost_tracker`, `_quota_manager`, `_rate_limiter`) are
    instantiated by server.py (`engine._cost_tracker = engine.CostTracker()`).
    Only the class defs + the `None` placeholders move here; the instantiation
    site stays in server.py and resolves via the brain/engine alias.

The cost DB path is computed from this file's location (engine/ is a subdir of
the repo root that also holds agents/) so it equals brain.AGENTS_DIR/main/
costs.db without an import-time dependency on brain. RateLimiter._get_limits
uses the same repo-root-relative agents dir.

brain.py re-exports every public symbol defined here so existing callers
(`engine.CostTracker`, `engine._quota_manager`, `brain.QuotaExceededError`,
the singleton instantiation in server.py, the handlers) resolve unchanged.
"""
from __future__ import annotations

import collections
import datetime
import json
import logging
import os
import sqlite3
import threading
import time

# Repo-root agents dir (engine/ is one level below the repo root).
_AGENTS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "agents")
_CONFIG_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json")


# --- Cost Tracking ---

COST_DB = os.path.join(_AGENTS_DIR, "main", "costs.db")

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
    Config values of 0 are treated as unset — auto-discovery writes 0 for all models.

    Returns `{input, output, cache_read}` per 1M tokens. `cache_read` is the price
    of a prompt-cache HIT (provider-served prefix). It's distinct from `input`
    because cached tokens bill at a steep discount (Mistral/Anthropic ≈ 0.1×). The
    per-model `cost_cache_read` field wins when set/non-zero; otherwise cache_read
    defaults to 0.1× the input rate (the common provider discount) so a freshly
    auto-discovered model still prices cache hits sensibly. A `cost_cache_read` of
    0 (or unset) ALSO means "this model is not cache-priced" for the routing-freeze
    decision (see brain.model_is_cache_priced) — there the explicit config field is
    read directly, NOT this derived default."""
    import brain as _brain
    cfg = _brain._models_config.get(model, {})
    ci = cfg.get("cost_input")
    co = cfg.get("cost_output")
    ccr = cfg.get("cost_cache_read")
    if ci is not None and co is not None and (float(ci) > 0 or float(co) > 0):
        inp = float(ci)
        return {"input": inp, "output": float(co),
                "cache_read": float(ccr) if (ccr is not None and float(ccr) > 0) else inp * 0.1}
    # Check built-in rates
    if model in _cost_rates:
        r = _cost_rates[model]
        return {"input": r["input"], "output": r["output"],
                "cache_read": r.get("cache_read", r["input"] * 0.1)}
    # Try prefix matching (e.g. "claude-opus-4-6" matches "claude-opus-4-6-20260101")
    ml = model.lower()
    for pattern, rate in _cost_rates.items():
        if ml.startswith(pattern.lower()) or pattern.lower() in ml:
            return {"input": rate["input"], "output": rate["output"],
                    "cache_read": rate.get("cache_read", rate["input"] * 0.1)}
    return {"input": 0.0, "output": 0.0, "cache_read": 0.0}


def _compute_cost(model: str, tokens_in: int, tokens_out: int,
                  cache_read_tokens: int = 0) -> float:
    """Compute estimated cost in USD.

    `tokens_in` is the FULL-PRICE input count (fresh prompt + cache_creation —
    oMLX reports the whole prompt under cache_creation, so that stays full-price).
    `cache_read_tokens` is the cache-HIT portion, billed at the discounted
    `cache_read` rate. Callers pass cache_read SEPARATELY from tokens_in (it is
    NOT a subset of tokens_in) — see the collapse-site fix in sidecar_proxy.

    Flat-plan models (config `flat_plan: true`) always cost $0 here — their
    cost_* fields keep the API list price for the breakdown's hypothetical
    'ohne Flatrate' estimate, but nothing real is billed per call."""
    import brain as _brain
    if _brain.model_is_flat_plan(model):
        return 0.0
    rate = _get_cost_rate(model)
    return (tokens_in * rate["input"]
            + tokens_out * rate["output"]
            + cache_read_tokens * rate["cache_read"]) / 1_000_000


def _unit_list_cost(purpose: str, units: int) -> float | None:
    """API list price for SYNTHETIC unit-billed cost rows of flat-plan models —
    OCR rows carry pages in tokens_in (rate ocr.cost_per_page_usd), TTS rows
    carry chars in tokens_in (rate text_to_speech.cost_per_1k_chars_usd). The
    token-based flat-plan reconstruction yields 0 for these rows, so the read-
    time aggregators call this first. None = not a unit-billed purpose."""
    try:
        if purpose == "ocr":
            from engine.doc_convert import _ocr_config
            return units * float(_ocr_config().get("cost_per_page_usd") or 0.0)
        if purpose in ("read_aloud", "audio_overview"):
            import brain as _brain
            cfg = _brain.get_tool_config().get("text_to_speech", {}) or {}
            return units / 1000.0 * float(cfg.get("cost_per_1k_chars_usd", 0) or 0.0)
    except Exception:
        return None
    return None


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
                    cache_read_tokens INTEGER NOT NULL DEFAULT 0,
                    cost_usd REAL NOT NULL DEFAULT 0.0,
                    tool_round INTEGER DEFAULT 0,
                    purpose TEXT NOT NULL DEFAULT '',
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
                # `purpose` = use-case tag (chat, chat_summary, scheduled, translate,
                # ...) for the per-use-case cost breakdown. Pre-migration rows keep
                # '' → surfaced as "unknown (legacy)" in the breakdown.
                if "purpose" not in cols:
                    conn.execute("ALTER TABLE cost_log ADD COLUMN purpose TEXT NOT NULL DEFAULT ''")
                # `cache_read_tokens` = prompt-cache HIT tokens (billed at the
                # discounted cache_read rate, NOT folded into tokens_in). Pre-
                # migration rows keep 0 → counted as no cache activity, which is
                # correct for the period before cache-aware costing existed.
                if "cache_read_tokens" not in cols:
                    conn.execute("ALTER TABLE cost_log ADD COLUMN cache_read_tokens INTEGER NOT NULL DEFAULT 0")
            except sqlite3.Error as e:
                logging.warning(f"cost_log migration: {e}")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_cost_agent ON cost_log(agent)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_cost_session ON cost_log(session_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_cost_created ON cost_log(created_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_cost_user ON cost_log(user_id, created_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_cost_provider ON cost_log(provider, key_name, created_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_cost_purpose ON cost_log(purpose, created_at)")
            conn.commit()

    def log_call(self, agent: str, session_id: str, model: str, provider: str,
                 tokens_in: int, tokens_out: int, tool_round: int = 0,
                 user_id: str = "", key_name: str = "", purpose: str = "",
                 cache_read_tokens: int = 0):
        """Log an LLM call with cost estimation.

        `cache_read_tokens` is the prompt-cache HIT portion (billed at the
        discounted cache_read rate). It is passed SEPARATELY from `tokens_in`
        (full-price input) and stored in its own column so the breakdown can show
        cache hit-rate + realized savings. 0 when the provider reported no hit."""
        cost = _compute_cost(model, tokens_in, tokens_out, cache_read_tokens)
        try:
            with _cost_conn() as conn:
                conn.execute("""
                    INSERT INTO cost_log (agent, session_id, user_id, model, provider, key_name, tokens_in, tokens_out, cache_read_tokens, cost_usd, tool_round, purpose)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (agent, session_id or "", user_id or "", model, provider, key_name or "", tokens_in, tokens_out, int(cache_read_tokens or 0), cost, tool_round, purpose or ""))
                conn.commit()
        except (sqlite3.Error, OSError) as e:
            logging.warning(f"Cost tracking error: {e}")

    def log_ocr(self, agent: str, session_id: str, model: str, provider: str,
                pages: int, cost_usd: float, user_id: str = "",
                key_name: str = "", purpose: str = "ocr"):
        """Log an OCR call as a synthetic cost row. Pages stashed in tokens_in
        (output stays 0); explicit USD cost bypasses _compute_cost which only
        knows tokens. Aggregates sum cost_usd correctly without changes.

        Flat-plan models (config `flat_plan: true`) log $0 real cost — the
        caller-computed amount is the LIST price, reconstructed at read time
        from pages × ocr.cost_per_page_usd (see _unit_list_cost)."""
        import brain as _brain
        if _brain.model_is_flat_plan(model):
            cost_usd = 0.0
        try:
            with _cost_conn() as conn:
                conn.execute("""
                    INSERT INTO cost_log (agent, session_id, user_id, model, provider, key_name, tokens_in, tokens_out, cost_usd, tool_round, purpose)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (agent, session_id or "", user_id or "", model, provider, key_name or "", int(pages), 0, float(cost_usd), 0, purpose or "ocr"))
                conn.commit()
        except (sqlite3.Error, OSError) as e:
            logging.warning(f"OCR cost tracking error: {e}")

    def log_tts(self, agent: str, session_id: str, model: str, provider: str,
                chars: int, cost_usd: float, user_id: str = "",
                key_name: str = "", purpose: str = "read_aloud"):
        """Log a text-to-speech render as a synthetic cost row. Chars synthesized
        stashed in tokens_in (output stays 0); explicit USD cost bypasses
        _compute_cost (TTS is char-billed, not token-billed). Aggregates sum
        cost_usd correctly without changes — mirrors log_ocr (incl. the
        flat-plan $0 rule; list price reconstructed via _unit_list_cost)."""
        import brain as _brain
        if _brain.model_is_flat_plan(model):
            cost_usd = 0.0
        try:
            with _cost_conn() as conn:
                conn.execute("""
                    INSERT INTO cost_log (agent, session_id, user_id, model, provider, key_name, tokens_in, tokens_out, cost_usd, tool_round, purpose)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (agent, session_id or "", user_id or "", model, provider, key_name or "", int(chars), 0, float(cost_usd), 0, purpose or "read_aloud"))
                conn.commit()
        except (sqlite3.Error, OSError) as e:
            logging.warning(f"TTS cost tracking error: {e}")

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

    def breakdown(self, since_iso: str | None = None, until_iso: str | None = None,
                  user_id: str | None = None, agent: str | None = None) -> list[dict]:
        """Per-(purpose, model) aggregate for the [since, until) window. Either
        bound may be None (None since = all-time start; None until = now).
        Rows: {purpose, model, calls, tokens_in, tokens_out, cache_read_tokens, cost}.
        The caller maps raw `purpose` → display use-case buckets and nests by model."""
        try:
            with _cost_conn() as conn:
                conn.row_factory = sqlite3.Row
                where = "WHERE 1=1"
                params: list = []
                if since_iso:
                    where += " AND created_at >= ?"
                    params.append(since_iso)
                if until_iso:
                    where += " AND created_at < ?"
                    params.append(until_iso)
                if agent:
                    where += " AND agent = ?"
                    params.append(agent)
                if user_id is not None:
                    where += " AND user_id = ?"
                    params.append(user_id)
                rows = conn.execute(f"""
                    SELECT purpose, model,
                           COUNT(*) as calls,
                           COALESCE(SUM(tokens_in), 0) as tokens_in,
                           COALESCE(SUM(tokens_out), 0) as tokens_out,
                           COALESCE(SUM(cache_read_tokens), 0) as cache_read_tokens,
                           COALESCE(SUM(cost_usd), 0.0) as cost
                    FROM cost_log {where}
                    GROUP BY purpose, model
                    ORDER BY cost DESC
                """, params).fetchall()
                return [dict(r) for r in rows]
        except (sqlite3.Error, OSError) as e:
            logging.warning(f"Cost breakdown error: {e}")
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
        """Get cost for a specific session.

        `cost` = real/charged (flat-plan models log $0). `cost_list` = the
        API list price of the same usage: for flat-plan models computed from
        the session's tokens × the model's regular cost_* fields (which keep
        the list price), for everything else identical to `cost`."""
        _empty = {"calls": 0, "tokens_in": 0, "tokens_out": 0, "cost": 0.0, "cost_list": 0.0}
        try:
            with _cost_conn() as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute("""
                    SELECT model, purpose,
                           COUNT(*) as calls,
                           COALESCE(SUM(tokens_in), 0) as tokens_in,
                           COALESCE(SUM(tokens_out), 0) as tokens_out,
                           COALESCE(SUM(cache_read_tokens), 0) as cache_read_tokens,
                           COALESCE(SUM(cost_usd), 0.0) as cost
                    FROM cost_log WHERE session_id = ? GROUP BY model, purpose
                """, (session_id,)).fetchall()
                out = dict(_empty)
                import brain as _brain
                for r in rows:
                    out["calls"] += r["calls"]
                    out["tokens_in"] += r["tokens_in"]
                    out["tokens_out"] += r["tokens_out"]
                    out["cost"] += r["cost"]
                    if _brain.model_is_flat_plan(r["model"]):
                        _ul = _unit_list_cost(r["purpose"] or "", r["tokens_in"])
                        if _ul is not None:
                            out["cost_list"] += _ul
                        else:
                            rate = _get_cost_rate(r["model"])
                            out["cost_list"] += (r["tokens_in"] * rate["input"]
                                                 + r["tokens_out"] * rate["output"]
                                                 + r["cache_read_tokens"] * rate["cache_read"]) / 1e6
                    else:
                        out["cost_list"] += r["cost"]
                return out
        except (sqlite3.Error, OSError):
            return dict(_empty)


_cost_tracker: 'CostTracker | None' = None


# --- Per-User Cost Quotas ---
#
# Two-axis quota: daily (rolling 24h reset at local midnight UTC) and
# billing-cycle (monthly/weekly/yearly with admin-configured start day).
# Limits are per-role with optional per-user overrides. Local models always
# cost $0 and never count against the quota.
#
# Enforcement modes (config.json → quotas.enforce_red):
#   - "warn_only" (default): UI shows red badge; no server-side refusal
#   - "force_local": auto-swap to default_local_fallback_model on red, like GDPR
#   - "hard_block": refuse outright on red
#
# Local models bypass the gate (is_model_local).

QUOTA_DEFAULTS = {
    "enabled": True,
    "billing_cycle": "monthly",     # monthly | weekly | yearly
    "cycle_start_day": 1,           # 1-31 monthly, 0-6 weekly (0=Mon), 1-12 yearly (month-of-year)
    "warn_pct": 70,
    "block_pct": 100,
    "enforce_red": "warn_only",     # warn_only | force_local | hard_block
    "default_local_fallback_model": "",
    "limits": {
        "admin":     {"daily_usd": 0.0, "cycle_usd": 0.0},
        "poweruser": {"daily_usd": 0.0, "cycle_usd": 0.0},
        "user":      {"daily_usd": 0.0, "cycle_usd": 0.0},
    },
    "user_overrides": {},
}


def _quota_default_role_limits(role: str) -> dict:
    return {"daily_usd": 0.0, "cycle_usd": 0.0}


class QuotaExceededError(RuntimeError):
    """Raised when hard_block mode rejects a request, or when force_local
    cannot find a usable local fallback."""
    def __init__(self, message: str, level: str = "red", reason: str = ""):
        super().__init__(message)
        self.level = level
        self.reason = reason


class QuotaManager:
    """Per-user cost quotas. Reads `quotas` block from config.json.

    Stateless beyond a 30s cache of the config and a per-user state cache
    so the status-bar pill can poll cheaply. Cost numbers come from
    cost_log via CostTracker; this class only adds the policy + windowing.
    """

    def __init__(self):
        self._cfg_cache: tuple[float, dict] | None = None
        self._cfg_cache_ttl = 30.0
        self._lock = threading.Lock()

    # --- config ---

    def _load_config(self) -> dict:
        now = time.time()
        with self._lock:
            if self._cfg_cache and (now - self._cfg_cache[0]) < self._cfg_cache_ttl:
                return self._cfg_cache[1]
        try:
            with open(_CONFIG_PATH) as f:
                raw = json.load(f).get("quotas") or {}
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            raw = {}
        merged = dict(QUOTA_DEFAULTS)
        for k, v in raw.items():
            if k == "limits" and isinstance(v, dict):
                limits = {role: dict(QUOTA_DEFAULTS["limits"].get(role, _quota_default_role_limits(role)))
                          for role in ("admin", "poweruser", "user")}
                for role, lim in v.items():
                    if role in limits and isinstance(lim, dict):
                        for fld in ("daily_usd", "cycle_usd"):
                            if fld in lim:
                                try:
                                    limits[role][fld] = max(0.0, float(lim[fld] or 0))
                                except (TypeError, ValueError):
                                    pass
                merged["limits"] = limits
            elif k == "user_overrides" and isinstance(v, dict):
                ov = {}
                for uid, lim in v.items():
                    if not isinstance(lim, dict):
                        continue
                    entry = {}
                    for fld in ("daily_usd", "cycle_usd"):
                        if fld in lim:
                            try:
                                entry[fld] = max(0.0, float(lim[fld] or 0))
                            except (TypeError, ValueError):
                                pass
                    if entry:
                        ov[str(uid)] = entry
                merged["user_overrides"] = ov
            else:
                merged[k] = v
        # Normalise enforce_red
        if merged.get("enforce_red") not in ("warn_only", "force_local", "hard_block"):
            merged["enforce_red"] = "warn_only"
        if merged.get("billing_cycle") not in ("monthly", "weekly", "yearly"):
            merged["billing_cycle"] = "monthly"
        with self._lock:
            self._cfg_cache = (now, merged)
        return merged

    def invalidate_cache(self):
        with self._lock:
            self._cfg_cache = None

    def get_config(self) -> dict:
        """Return the full quotas config for admin UI."""
        return self._load_config()

    def save_config(self, new_cfg: dict) -> dict:
        """Validate + persist quotas block to config.json."""
        cfg_path = _CONFIG_PATH
        try:
            with open(cfg_path) as f:
                full = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            full = {}
        existing = dict(QUOTA_DEFAULTS)
        existing.update(full.get("quotas") or {})
        # Whitelist + coerce
        out = dict(existing)
        if "enabled" in new_cfg:
            out["enabled"] = bool(new_cfg["enabled"])
        if "billing_cycle" in new_cfg and new_cfg["billing_cycle"] in ("monthly", "weekly", "yearly"):
            out["billing_cycle"] = new_cfg["billing_cycle"]
        if "cycle_start_day" in new_cfg:
            try:
                out["cycle_start_day"] = int(new_cfg["cycle_start_day"])
            except (TypeError, ValueError):
                pass
        if "warn_pct" in new_cfg:
            try:
                out["warn_pct"] = max(0, min(100, int(new_cfg["warn_pct"])))
            except (TypeError, ValueError):
                pass
        if "block_pct" in new_cfg:
            try:
                out["block_pct"] = max(0, min(200, int(new_cfg["block_pct"])))
            except (TypeError, ValueError):
                pass
        if "enforce_red" in new_cfg and new_cfg["enforce_red"] in ("warn_only", "force_local", "hard_block"):
            out["enforce_red"] = new_cfg["enforce_red"]
        if "default_local_fallback_model" in new_cfg:
            out["default_local_fallback_model"] = str(new_cfg["default_local_fallback_model"] or "")
        if "limits" in new_cfg and isinstance(new_cfg["limits"], dict):
            limits = {role: dict(out["limits"].get(role, _quota_default_role_limits(role)))
                      for role in ("admin", "poweruser", "user")}
            for role, lim in new_cfg["limits"].items():
                if role in limits and isinstance(lim, dict):
                    for fld in ("daily_usd", "cycle_usd"):
                        if fld in lim:
                            try:
                                limits[role][fld] = max(0.0, float(lim[fld] or 0))
                            except (TypeError, ValueError):
                                pass
            out["limits"] = limits
        if "user_overrides" in new_cfg and isinstance(new_cfg["user_overrides"], dict):
            ov = {}
            for uid, lim in new_cfg["user_overrides"].items():
                if not isinstance(lim, dict):
                    continue
                entry = {}
                for fld in ("daily_usd", "cycle_usd"):
                    if fld in lim:
                        try:
                            entry[fld] = max(0.0, float(lim[fld] or 0))
                        except (TypeError, ValueError):
                            pass
                if entry:
                    ov[str(uid)] = entry
            out["user_overrides"] = ov
        full["quotas"] = out
        try:
            with open(cfg_path, "w") as f:
                json.dump(full, f, indent=2)
        except OSError as e:
            raise RuntimeError(f"Failed to save quotas config: {e}") from e
        self.invalidate_cache()
        return out

    # --- cycle math ---

    @staticmethod
    def _last_day_of_month(year: int, month: int) -> int:
        import calendar
        return calendar.monthrange(year, month)[1]

    def cycle_window(self, cfg: dict | None = None, now: datetime.datetime | None = None) -> tuple[datetime.datetime, datetime.datetime]:
        """Return (start_inclusive, end_exclusive) of the current billing cycle in UTC."""
        cfg = cfg or self._load_config()
        now = now or datetime.datetime.now(datetime.timezone.utc)
        cycle = cfg.get("billing_cycle", "monthly")
        start_day = int(cfg.get("cycle_start_day") or 1)
        if cycle == "weekly":
            # 0=Mon..6=Sun
            anchor_dow = max(0, min(6, start_day))
            today_dow = now.weekday()
            delta = (today_dow - anchor_dow) % 7
            start = (now - datetime.timedelta(days=delta)).replace(hour=0, minute=0, second=0, microsecond=0)
            end = start + datetime.timedelta(days=7)
            return start, end
        if cycle == "yearly":
            anchor_month = max(1, min(12, start_day))
            year = now.year if (now.month >= anchor_month) else (now.year - 1)
            start = datetime.datetime(year, anchor_month, 1, tzinfo=datetime.timezone.utc)
            end = datetime.datetime(year + 1, anchor_month, 1, tzinfo=datetime.timezone.utc)
            return start, end
        # monthly (default)
        anchor = max(1, min(31, start_day))
        # Clamp to last day of (month-1) if anchor > last day of that month
        cur_year, cur_month = now.year, now.month
        last_this = self._last_day_of_month(cur_year, cur_month)
        # Determine current cycle start: if today >= clamped anchor of this month, start = this month, else previous month
        anchor_this = min(anchor, last_this)
        cycle_start_this_month = datetime.datetime(cur_year, cur_month, anchor_this, tzinfo=datetime.timezone.utc)
        if now >= cycle_start_this_month:
            start = cycle_start_this_month
            ny, nm = (cur_year + (cur_month // 12)), ((cur_month % 12) + 1)
            anchor_next = min(anchor, self._last_day_of_month(ny, nm))
            end = datetime.datetime(ny, nm, anchor_next, tzinfo=datetime.timezone.utc)
        else:
            py, pm = (cur_year - 1, 12) if cur_month == 1 else (cur_year, cur_month - 1)
            anchor_prev = min(anchor, self._last_day_of_month(py, pm))
            start = datetime.datetime(py, pm, anchor_prev, tzinfo=datetime.timezone.utc)
            end = cycle_start_this_month
        return start, end

    @staticmethod
    def _today_window(now: datetime.datetime | None = None) -> tuple[datetime.datetime, datetime.datetime]:
        now = now or datetime.datetime.now(datetime.timezone.utc)
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return start, start + datetime.timedelta(days=1)

    # --- per-user state ---

    def get_user_role(self, user_id: str) -> str:
        if not user_id:
            return "user"
        try:
            from auth import AuthDB
            u = AuthDB.get_user(user_id)
            return (u or {}).get("role") or "user"
        except Exception:
            return "user"

    def get_user_limits(self, user_id: str, role: str | None = None, cfg: dict | None = None) -> dict:
        cfg = cfg or self._load_config()
        role = role or self.get_user_role(user_id)
        base = dict(cfg["limits"].get(role) or _quota_default_role_limits(role))
        ov = (cfg.get("user_overrides") or {}).get(user_id) or {}
        for fld in ("daily_usd", "cycle_usd"):
            if fld in ov:
                base[fld] = float(ov[fld] or 0)
        return {"daily_usd": float(base.get("daily_usd") or 0.0),
                "cycle_usd": float(base.get("cycle_usd") or 0.0),
                "role": role,
                "has_override": bool(ov)}

    def get_user_state(self, user_id: str, cfg: dict | None = None) -> dict:
        """Return per-user quota state suitable for the UI pill + modal."""
        cfg = cfg or self._load_config()
        role = self.get_user_role(user_id)
        limits = self.get_user_limits(user_id, role=role, cfg=cfg)
        now = datetime.datetime.now(datetime.timezone.utc)
        d_start, d_end = self._today_window(now)
        c_start, c_end = self.cycle_window(cfg, now)
        # Aggregate windows
        if _cost_tracker and user_id:
            daily_used = _cost_tracker.sum_user_window(user_id, d_start.strftime("%Y-%m-%d %H:%M:%S"))
            cycle_used = _cost_tracker.sum_user_window(user_id, c_start.strftime("%Y-%m-%d %H:%M:%S"))
        else:
            daily_used, cycle_used = 0.0, 0.0
        warn_pct = float(cfg.get("warn_pct", 70))
        block_pct = float(cfg.get("block_pct", 100))

        def _level(used: float, limit: float) -> tuple[str, float]:
            if limit <= 0:
                return ("green", 0.0)
            pct = (used / limit) * 100.0
            if pct >= block_pct:
                return ("red", pct)
            if pct >= warn_pct:
                return ("yellow", pct)
            return ("green", pct)

        d_level, d_pct = _level(daily_used, limits["daily_usd"])
        c_level, c_pct = _level(cycle_used, limits["cycle_usd"])
        worst = "red" if "red" in (d_level, c_level) else ("yellow" if "yellow" in (d_level, c_level) else "green")
        return {
            "user_id": user_id,
            "role": role,
            "enabled": bool(cfg.get("enabled", True)),
            "enforce_red": cfg.get("enforce_red", "warn_only"),
            "default_local_fallback_model": cfg.get("default_local_fallback_model") or "",
            "warn_pct": warn_pct,
            "block_pct": block_pct,
            "billing_cycle": cfg.get("billing_cycle", "monthly"),
            "cycle_start_day": cfg.get("cycle_start_day", 1),
            "daily": {
                "used_usd": round(daily_used, 4),
                "limit_usd": limits["daily_usd"],
                "pct": round(d_pct, 2),
                "level": d_level,
                "resets_at": d_end.isoformat(),
            },
            "cycle": {
                "used_usd": round(cycle_used, 4),
                "limit_usd": limits["cycle_usd"],
                "pct": round(c_pct, 2),
                "level": c_level,
                "starts_at": c_start.isoformat(),
                "resets_at": c_end.isoformat(),
            },
            "level": worst,
            "has_override": limits["has_override"],
        }

    # --- enforcement ---

    def check_request(self, user_id: str, model: str) -> tuple[str, str]:
        """Return (decision, reason). Decisions:
          - "allow": proceed unchanged
          - "force_local": caller should swap to fallback model
          - "block": caller must raise QuotaExceededError

        Local models always allowed. Disabled feature → allow.
        """
        cfg = self._load_config()
        if not cfg.get("enabled", True):
            return "allow", ""
        if not user_id:
            return "allow", ""
        try:
            import brain as _brain
            if _brain.is_model_local(model):
                return "allow", "local-model"
        except Exception:
            pass
        st = self.get_user_state(user_id, cfg=cfg)
        if st["level"] != "red":
            return "allow", st["level"]
        mode = cfg.get("enforce_red", "warn_only")
        worst_axis = "daily" if st["daily"]["level"] == "red" else "cycle"
        reason = f"{worst_axis} quota exhausted ({st[worst_axis]['pct']:.0f}%)"
        if mode == "warn_only":
            return "allow", reason
        if mode == "force_local":
            return "force_local", reason
        return "block", reason


_quota_manager: 'QuotaManager | None' = None


# --- Rate Limiting ---

class RateLimiter:
    """Sliding-window rate limiter per agent. In-memory only (resets on restart)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._requests: dict[str, collections.deque] = collections.defaultdict(collections.deque)
        self._tokens: dict[str, collections.deque] = collections.defaultdict(collections.deque)
        self._cost: dict[str, collections.deque] = collections.defaultdict(collections.deque)

    def _prune(self, dq: collections.deque, cutoff: float):
        """Remove entries older than cutoff timestamp."""
        while dq and (dq[0] if isinstance(dq[0], (int, float)) else dq[0][0]) < cutoff:
            dq.popleft()

    def check(self, agent_id: str) -> tuple[bool, str, dict]:
        """Check if a request is allowed for this agent.

        Returns (allowed, reason, usage_info).
        Loads limits from the agent's agent.json rate_limits field.
        """
        limits = self._get_limits(agent_id)
        if not limits:
            return True, "", {}

        now = time.time()
        with self._lock:
            # Check requests/minute
            rpm_limit = limits.get("max_requests_per_minute")
            if rpm_limit:
                dq = self._requests[agent_id]
                self._prune(dq, now - 60)
                if len(dq) >= rpm_limit:
                    oldest = dq[0]
                    retry = 60 - (now - oldest)
                    return False, f"Rate limit: {rpm_limit} requests/minute exceeded. Retry in {int(retry)}s.", {
                        "dimension": "max_requests_per_minute", "current": len(dq), "limit": rpm_limit}

            # Check tokens/hour
            tph_limit = limits.get("max_tokens_per_hour")
            if tph_limit:
                dq = self._tokens[agent_id]
                self._prune(dq, now - 3600)
                total = sum(t[1] for t in dq)
                if total >= tph_limit:
                    return False, f"Rate limit: {tph_limit} tokens/hour exceeded.", {
                        "dimension": "max_tokens_per_hour", "current": total, "limit": tph_limit}

            # Check cost/day
            cpd_limit = limits.get("max_cost_per_day")
            if cpd_limit:
                dq = self._cost[agent_id]
                self._prune(dq, now - 86400)
                total = sum(t[1] for t in dq)
                if total >= cpd_limit:
                    return False, f"Rate limit: ${cpd_limit}/day cost limit exceeded.", {
                        "dimension": "max_cost_per_day", "current": total, "limit": cpd_limit}

            # Record the request timestamp
            self._requests[agent_id].append(now)

        return True, "", {}

    def record_usage(self, agent_id: str, tokens: int, cost: float):
        """Record token and cost usage after a successful response."""
        now = time.time()
        with self._lock:
            self._tokens[agent_id].append((now, tokens))
            self._cost[agent_id].append((now, cost))

    def get_status(self, agent_id: str | None = None) -> dict:
        """Get current usage vs limits for display."""
        result = {}
        agents_to_check = [agent_id] if agent_id else list(set(
            list(self._requests.keys()) + list(self._tokens.keys())))

        now = time.time()
        with self._lock:
            for aid in agents_to_check:
                limits = self._get_limits(aid)
                if not limits:
                    continue
                # Requests/minute
                dq_r = self._requests.get(aid, collections.deque())
                self._prune(dq_r, now - 60)
                rpm_limit = limits.get("max_requests_per_minute", 0)
                # Tokens/hour
                dq_t = self._tokens.get(aid, collections.deque())
                self._prune(dq_t, now - 3600)
                tph_total = sum(t[1] for t in dq_t)
                tph_limit = limits.get("max_tokens_per_hour", 0)
                # Cost/day
                dq_c = self._cost.get(aid, collections.deque())
                self._prune(dq_c, now - 86400)
                cpd_total = sum(t[1] for t in dq_c)
                cpd_limit = limits.get("max_cost_per_day", 0)

                result[aid] = {
                    "requests_per_minute": {"current": len(dq_r), "limit": rpm_limit},
                    "tokens_per_hour": {"current": tph_total, "limit": tph_limit},
                    "cost_per_day": {"current": round(cpd_total, 4), "limit": cpd_limit},
                }
        return result

    def _get_limits(self, agent_id: str) -> dict:
        """Load rate limits from agent.json."""
        try:
            agent_json = os.path.join(_AGENTS_DIR, agent_id, "agent.json")
            if os.path.isfile(agent_json):
                with open(agent_json) as f:
                    cfg = json.load(f)
                return cfg.get("rate_limits", {})
        except (OSError, json.JSONDecodeError):
            pass
        return {}


_rate_limiter: 'RateLimiter | None' = None
