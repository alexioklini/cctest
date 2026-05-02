# Extracted from claude_cli.py — per-user cost quotas

import datetime
import json
import os
import threading
import time

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
            cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
            with open(cfg_path) as f:
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
        cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
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
            if is_model_local(model):
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
