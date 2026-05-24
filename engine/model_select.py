"""engine/model_select.py — model-profile overlays + provider resolution.

Extracted from brain.py (refactor C1). Owns two tightly-coupled clusters:

  1. **Model optimization profiles** — `MODEL_PROFILES` + the four resolvers
     (`get_model_profile`, `resolve_model_settings`, `resolve_profile_token_config`,
     `resolve_profile_limits`). Sparse request-style overlays; explicit per-model
     fields always win.

  2. **Provider resolution + multi-key pools** — `resolve_provider_for_model`
     is the SINGLE source of truth for `{api_key, base_url, provider_name}`,
     used by chat / delegate / scheduler / warmup / background. It depends on
     the per-provider `ProviderKeyPool` (round-robin + exhaustion tracking) and
     the 60s `_provider_cache`. `clear_provider_cache` resets both.

INVARIANTS preserved from the brain.py original:
  - **Single instance**: `_provider_cache`, `_provider_cache_lock`,
    `_key_pools`, `_key_pools_lock` are module globals here and re-exported by
    brain.py by reference (alias import), so `brain._provider_cache is
    engine.model_select._provider_cache`. Splitting them into two copies would
    be a silent concurrency bug. The cost-logger in brain.py reads
    `_key_pools`/`_key_pools_lock` via that same alias.
  - The provider CONCURRENCY QUEUE (`LocalProviderQueue`, `_ProviderTicket`,
    `_ProviderQueueSlot`) is a DIFFERENT subsystem and stays in brain.py — it
    is not part of model→provider resolution.

Seams:
  - brain-runtime state reached lazily via `import brain as _b` inside the
    `_LazyBrain` proxy (one-way DAG; brain imports this module). The proxied
    symbols are: `_models_config`, `_delegate_api_key`, `_delegate_base_url`,
    `_invalidate_providers_cache`. They live in brain because the launcher /
    server / handlers mutate them at runtime (`engine._delegate_api_key = …`).
"""

from __future__ import annotations

import datetime
import json
import os
import threading
import time


class _LazyBrain:
    """Lazy proxy to the live `brain` module (avoids the import cycle —
    brain imports this module). Every brain-runtime symbol this module
    touches is reached through this proxy as `_brain.<name>`."""
    __slots__ = ()

    def __getattr__(self, name):
        import brain as _b
        return getattr(_b, name)


_brain = _LazyBrain()


# ============================================================
# Model optimization profiles
# ============================================================

MODEL_PROFILES = {
    "speed": {
        "model": {
            "warmup_mode": "full",
            "parallel_tool_calls": True,
            "caveman_system": 0,
        },
        "token_config": {
            "include_tools_guide": True,
            "deferred_tool_groups": [],      # no deferral → stable KV prefix
            "compact_threshold": 0.85,       # delay compaction → keep cache warm
        },
        "limits": {
            "max_tool_rounds": 15,
            "tool_results_total_tokens": 80000,
            "context_safety_ratio": 0.95,
        },
    },
    "balanced": {
        "model": {
            "parallel_tool_calls": True,
            "caveman_system": 0,
        },
        "token_config": {
            "include_tools_guide": True,
            "deferred_tool_groups": ["email", "documents", "code_graph", "scheduler", "delegation"],
            "compact_threshold": 0.70,
        },
        "limits": {
            "max_tool_rounds": 15,
            "tool_results_total_tokens": 50000,
            "context_safety_ratio": 0.95,
        },
    },
    "frugal": {
        "model": {
            "warmup": False,
            "warmup_allow_cloud": False,
            "parallel_tool_calls": True,
            "caveman_system": 2,
        },
        "token_config": {
            "include_tools_guide": False,
            "deferred_tool_groups": ["email", "documents", "code_graph",
                                     "scheduler", "nodes", "git", "delegation"],
            "compact_threshold": 0.50,
        },
        "limits": {
            "max_tool_rounds": 8,
            "tool_results_total_tokens": 25000,
            "context_safety_ratio": 0.90,
        },
    },
}


def get_model_profile(mid: str) -> str:
    """Return the profile name for a model ('speed', 'balanced', 'frugal', 'custom').

    Models without an explicit `profile` field default to 'custom' (no overlay)
    for backward compat with hand-tuned configs.
    """
    cfg = (_brain._models_config or {}).get(mid, {}) or {}
    p = cfg.get("profile")
    if p in MODEL_PROFILES or p == "custom":
        return p
    return "custom"


def resolve_model_settings(mid: str) -> dict:
    """Return the model config with profile overlay applied.

    Profile overlay sets *defaults* — explicit per-model fields win. This lets
    users flip a model onto a profile and still override individual knobs.
    Returns a fresh dict; safe to mutate.
    """
    raw = dict((_brain._models_config or {}).get(mid, {}) or {})
    profile = raw.get("profile")
    if profile not in MODEL_PROFILES:
        return raw
    overlay = MODEL_PROFILES[profile].get("model", {})
    for k, v in overlay.items():
        if k not in raw or raw[k] is None:
            raw[k] = v
    return raw


def resolve_profile_token_config(mid: str) -> dict:
    """Return the profile's token_config fragment, or empty dict."""
    profile = get_model_profile(mid)
    if profile not in MODEL_PROFILES:
        return {}
    return dict(MODEL_PROFILES[profile].get("token_config", {}))


def resolve_profile_limits(mid: str) -> dict:
    """Return the profile's limits fragment, or empty dict."""
    profile = get_model_profile(mid)
    if profile not in MODEL_PROFILES:
        return {}
    return dict(MODEL_PROFILES[profile].get("limits", {}))


# ============================================================
# Provider resolution + multi-key pools
# ============================================================

_provider_cache: dict[str, dict] = {}
_provider_cache_lock = threading.Lock()
_provider_cache_time: float = 0

# --- Multi-key pool per provider ---
#
# Each provider may have a list of api_keys with a usage tier:
#   preferred   — prio 1: used round-robin among all preferred keys first
#   round_robin — prio 2: used when no preferred keys are available
#   fallback    — prio 3: only when prio 1+2 are all exhausted
#
# A key is "exhausted" when the last HTTP call returned 429 or a token-limit
# error. Exhaustion resets after _KEY_EXHAUST_TTL seconds.
#
# Backward compat: a plain string `api_key` field is treated as a single
# preferred key with name "default".

_KEY_EXHAUST_TTL = 60.0  # seconds before an exhausted key is retried


class ProviderKeyPool:
    """Thread-safe round-robin key selector with exhaustion tracking."""

    def __init__(self, keys: list):
        self._lock = threading.Lock()
        self._keys = keys
        self._rr: dict = {"preferred": 0, "round_robin": 0, "fallback": 0}
        self._exhausted: dict = {}

    def _available(self, tier: str) -> list:
        now = time.time()
        out = []
        for k in self._keys:
            if k.get("usage", "preferred") != tier:
                continue
            dl = k.get("deadline")
            if dl:
                try:
                    exp = datetime.datetime.fromisoformat(dl)
                    if exp.tzinfo is None:
                        exp = exp.replace(tzinfo=datetime.timezone.utc)
                    if datetime.datetime.now(datetime.timezone.utc) > exp:
                        continue
                except Exception:
                    pass
            if self._exhausted.get(k["name"], 0) > now:
                continue
            out.append(k)
        return out

    def pick(self):
        with self._lock:
            for tier in ("preferred", "round_robin", "fallback"):
                avail = self._available(tier)
                if not avail:
                    continue
                idx = self._rr[tier] % len(avail)
                self._rr[tier] = (idx + 1) % len(avail)
                return avail[idx]["key"]
        return None

    def mark_exhausted(self, key_value: str, ttl: float = _KEY_EXHAUST_TTL):
        with self._lock:
            for k in self._keys:
                if k["key"] == key_value:
                    self._exhausted[k["name"]] = time.time() + ttl
                    break

    def reset_exhausted(self, key_value: str):
        with self._lock:
            for k in self._keys:
                if k["key"] == key_value:
                    self._exhausted.pop(k["name"], None)
                    break


# Per-provider key pools (keyed by provider_name)
_key_pools: dict = {}
_key_pools_lock = threading.Lock()


def _get_key_pool(provider_name: str, prov_cfg: dict) -> ProviderKeyPool:
    with _key_pools_lock:
        pool = _key_pools.get(provider_name)
        if pool is not None:
            return pool
        keys = _normalize_api_keys(prov_cfg)
        pool = ProviderKeyPool(keys)
        _key_pools[provider_name] = pool
        return pool


def _normalize_api_keys(prov_cfg: dict) -> list:
    """Return normalized key list from provider config.

    Handles both legacy `api_key: str` and new `api_keys: [{name,key,usage,...}]`.
    """
    if prov_cfg.get("api_keys"):
        raw = prov_cfg["api_keys"]
        result = []
        for i, k in enumerate(raw):
            if isinstance(k, str):
                result.append({"name": f"key-{i}", "key": k, "usage": "preferred"})
            else:
                entry = dict(k)
                entry.setdefault("name", f"key-{i}")
                entry.setdefault("usage", "preferred")
                result.append(entry)
        return result
    legacy = prov_cfg.get("api_key", "")
    if legacy:
        return [{"name": "default", "key": legacy, "usage": "preferred"}]
    return []


def invalidate_key_pool(provider_name: str):
    """Drop the cached pool so it is rebuilt on next pick."""
    with _key_pools_lock:
        _key_pools.pop(provider_name, None)


def mark_api_key_exhausted(provider_name: str, api_key: str, ttl: float = _KEY_EXHAUST_TTL):
    """Mark a specific key as rate-limited / token-exhausted."""
    with _key_pools_lock:
        pool = _key_pools.get(provider_name)
    if pool:
        pool.mark_exhausted(api_key, ttl)


def resolve_provider_for_model(model: str) -> dict:
    """Resolve provider credentials for a model. Returns {api_key, base_url, provider_name}.

    Single source of truth for model→provider resolution. Uses model config's provider
    field, falls back to delegate defaults. Thread-safe with 60s cache.

    When a provider has multiple api_keys, picks one according to the priority/
    round-robin rules defined in ProviderKeyPool.pick().
    """
    global _provider_cache_time

    now = time.time()
    with _provider_cache_lock:
        if model in _provider_cache and now - _provider_cache_time < 60:
            cached = _provider_cache[model].copy()
            pname = cached.get("provider_name", "")
            if pname and pname != "default":
                with _key_pools_lock:
                    pool = _key_pools.get(pname)
                if pool:
                    picked = pool.pick()
                    if picked is not None:
                        cached["api_key"] = picked
            return cached

    # Default: delegate globals
    result = {
        "api_key": _brain._delegate_api_key,
        "base_url": _brain._delegate_base_url,
        "provider_name": "default",
    }

    # Look up model's configured provider
    model_cfg = _brain._models_config.get(model, {})
    provider_name = model_cfg.get("provider", "")
    if provider_name:
        try:
            cfg_path = os.path.join(os.path.dirname(os.path.abspath(_brain.__file__)), "config.json")
            with open(cfg_path) as f:
                prov = json.load(f).get("providers", {}).get(provider_name, {})
            if prov:
                pool = _get_key_pool(provider_name, prov)
                picked = pool.pick()
                result = {
                    "api_key": picked if picked is not None else "",
                    "base_url": prov.get("base_url", ""),
                    "provider_name": provider_name,
                }
        except Exception:
            pass

    with _provider_cache_lock:
        _provider_cache[model] = result.copy()
        _provider_cache_time = now
    return result


def clear_provider_cache():
    """Clear the provider resolution cache and key pools (call after config changes)."""
    global _provider_cache_time
    with _provider_cache_lock:
        _provider_cache.clear()
        _provider_cache_time = 0
    with _key_pools_lock:
        _key_pools.clear()
    _brain._invalidate_providers_cache()
