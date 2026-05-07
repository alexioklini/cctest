# Extracted from server.py — provider/model config and warmup handlers
import hashlib
import json
import os
import threading
import time

import brain as engine


class ProvidersHandlerMixin:

    @staticmethod
    def _mask_key(key: str) -> str:
        return key[:4] + "***" if key and len(key) > 4 else "***"

    def _handle_list_providers(self):
        providers = server_config.get("providers", {})
        models_cfg = engine._models_config or {}
        result = []
        for name, p in providers.items():
            all_models = [mid for mid, mcfg in models_cfg.items()
                          if mcfg.get("provider") == name]
            enabled_models = [mid for mid, mcfg in models_cfg.items()
                              if mcfg.get("provider") == name and mcfg.get("enabled", True)]
            # Expose masked key list; also expose legacy single-key in masked form
            raw_keys = engine._normalize_api_keys(p)
            masked_keys = [
                {
                    "name": k.get("name", ""),
                    "usage": k.get("usage", "preferred"),
                    "deadline": k.get("deadline", ""),
                    "key_hint": self._mask_key(k.get("key", "")),
                }
                for k in raw_keys
            ]
            result.append({
                "name": name,
                "base_url": p.get("base_url", ""),
                "api_key": self._mask_key(p.get("api_key", "")) if p.get("api_key") else "",
                "api_keys": masked_keys,
                "type": p.get("type", "openai"),
                "default_model": p.get("default_model", ""),
                "use_sdk": p.get("use_sdk", True),
                "models": all_models,
                "model_count": len(all_models),
                "enabled_count": len(enabled_models),
                "status": "connected" if all_models else "no models",
            })
        self._send_json({"providers": result})

    def _handle_save_providers(self):
        """POST /v1/providers — save provider config."""
        body = self._read_json()
        action = body.get("action", "save")

        if action == "save":
            # Save all providers to config.json
            providers = body.get("providers", {})
            config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json")
            try:
                config = {}
                if os.path.exists(config_path):
                    with open(config_path) as f:
                        config = json.load(f)
                config["providers"] = providers
                if body.get("default_provider"):
                    config["default_provider"] = body["default_provider"]
                with open(config_path, "w") as f:
                    json.dump(config, f, indent=2)
                # Update server config in memory
                server_config["providers"] = providers
                self._send_json({"status": "saved"})
            except Exception as e:
                self._send_json({"error": str(e)}, 500)

        elif action == "add":
            name = body.get("name", "")
            provider = body.get("provider", {})
            if not name:
                self._send_json({"error": "Provider name required"}, 400)
                return
            config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json")
            try:
                config = {}
                if os.path.exists(config_path):
                    with open(config_path) as f:
                        config = json.load(f)
                existing = config.get("providers", {}).get(name, {})
                # Preserve existing api_key when _keep_key is set
                if provider.pop("_keep_key", False):
                    provider["api_key"] = existing.get("api_key", "")
                # Preserve existing api_keys list when not supplied
                if "api_keys" not in provider and existing.get("api_keys"):
                    provider["api_keys"] = existing["api_keys"]
                config.setdefault("providers", {})[name] = provider
                with open(config_path, "w") as f:
                    json.dump(config, f, indent=2)
                server_config.setdefault("providers", {})[name] = provider
                engine.invalidate_key_pool(name)
                self._send_json({"status": "added", "name": name})
            except Exception as e:
                self._send_json({"error": str(e)}, 500)

        elif action == "save_key":
            # Add or update a single api_key entry for a provider.
            # Body: {name, key_entry: {name, key|_keep_key_value, usage, deadline?}, old_name?}
            # If old_name is provided, the key with that name is replaced; otherwise appended.
            # _keep_key_value: preserve the existing key value when editing (name/usage/deadline may change).
            prov_name = body.get("name", "")
            key_entry = body.get("key_entry", {})
            old_key_name = body.get("old_name", "")
            keep_key_value = key_entry.pop("_keep_key_value", False)
            if not prov_name or not key_entry.get("name"):
                self._send_json({"error": "name and key_entry.name required"}, 400)
                return
            if not key_entry.get("key") and not keep_key_value:
                self._send_json({"error": "key_entry.key required for new keys"}, 400)
                return
            config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json")
            try:
                config = {}
                if os.path.exists(config_path):
                    with open(config_path) as f:
                        config = json.load(f)
                prov = config.get("providers", {}).get(prov_name)
                if prov is None:
                    self._send_json({"error": f"Unknown provider: {prov_name}"}, 404)
                    return
                # Migrate legacy single key to api_keys list if needed
                if "api_keys" not in prov:
                    legacy = prov.pop("api_key", "")
                    prov["api_keys"] = [{"name": "default", "key": legacy, "usage": "preferred"}] if legacy else []
                keys = prov["api_keys"]
                # Find existing key to preserve value if _keep_key_value
                existing_key_value = ""
                lookup_name = old_key_name or key_entry["name"]
                for k in keys:
                    if k.get("name") == lookup_name:
                        existing_key_value = k.get("key", "")
                        break
                entry = {
                    "name": key_entry["name"],
                    "key": key_entry["key"] if key_entry.get("key") else existing_key_value,
                    "usage": key_entry.get("usage", "preferred"),
                }
                if key_entry.get("deadline"):
                    entry["deadline"] = key_entry["deadline"]
                if old_key_name:
                    for i, k in enumerate(keys):
                        if k.get("name") == old_key_name:
                            keys[i] = entry
                            break
                    else:
                        keys.append(entry)
                else:
                    for i, k in enumerate(keys):
                        if k.get("name") == entry["name"]:
                            keys[i] = entry
                            break
                    else:
                        keys.append(entry)
                with open(config_path, "w") as f:
                    json.dump(config, f, indent=2)
                server_config.setdefault("providers", {})[prov_name] = prov
                engine.invalidate_key_pool(prov_name)
                self._send_json({"status": "saved", "name": prov_name})
            except Exception as e:
                self._send_json({"error": str(e)}, 500)

        elif action == "delete_key":
            # Remove a single api_key entry. Body: {name, key_name}
            prov_name = body.get("name", "")
            key_name = body.get("key_name", "")
            if not prov_name or not key_name:
                self._send_json({"error": "name and key_name required"}, 400)
                return
            config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json")
            try:
                config = {}
                if os.path.exists(config_path):
                    with open(config_path) as f:
                        config = json.load(f)
                prov = config.get("providers", {}).get(prov_name)
                if prov is None:
                    self._send_json({"error": f"Unknown provider: {prov_name}"}, 404)
                    return
                if "api_keys" not in prov:
                    legacy = prov.pop("api_key", "")
                    prov["api_keys"] = [{"name": "default", "key": legacy, "usage": "preferred"}] if legacy else []
                prov["api_keys"] = [k for k in prov["api_keys"] if k.get("name") != key_name]
                with open(config_path, "w") as f:
                    json.dump(config, f, indent=2)
                server_config.setdefault("providers", {})[prov_name] = prov
                engine.invalidate_key_pool(prov_name)
                self._send_json({"status": "deleted", "name": prov_name, "key_name": key_name})
            except Exception as e:
                self._send_json({"error": str(e)}, 500)

        elif action == "delete":
            name = body.get("name", "")
            config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json")
            try:
                config = {}
                if os.path.exists(config_path):
                    with open(config_path) as f:
                        config = json.load(f)
                config.get("providers", {}).pop(name, None)
                with open(config_path, "w") as f:
                    json.dump(config, f, indent=2)
                server_config.get("providers", {}).pop(name, None)
                engine.invalidate_key_pool(name)
                self._send_json({"status": "deleted", "name": name})
            except Exception as e:
                self._send_json({"error": str(e)}, 500)

        elif action == "rename":
            # Atomic rename: provider key + every reference that pins by name.
            # Touched: providers.<name>, default_provider, models[*].provider,
            # provider-scoped model keys "<name>/<id>", deleted_models tombstones
            # of either form. Caches (key pool, provider→model resolution) are
            # invalidated for both old and new names.
            old_name = (body.get("name", "") or "").strip()
            new_name = (body.get("new_name", "") or "").strip()
            if not old_name or not new_name:
                self._send_json({"error": "name and new_name required"}, 400)
                return
            if old_name == new_name:
                self._send_json({"status": "unchanged", "name": new_name})
                return
            # Reject characters that would break provider-scoped model ids.
            if "/" in new_name or any(c.isspace() for c in new_name):
                self._send_json({"error": "new_name must not contain '/' or whitespace"}, 400)
                return
            config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json")
            try:
                config = {}
                if os.path.exists(config_path):
                    with open(config_path) as f:
                        config = json.load(f)
                providers = config.get("providers", {}) or {}
                if old_name not in providers:
                    self._send_json({"error": f"Unknown provider: {old_name}"}, 404)
                    return
                if new_name in providers:
                    self._send_json({"error": f"Provider already exists: {new_name}"}, 409)
                    return
                # 1) provider entry
                providers[new_name] = providers.pop(old_name)
                # Preserve insertion order by rebuilding (Python dict is insertion-ordered;
                # popping then inserting moves the entry to the end — acceptable).
                config["providers"] = providers
                # 2) default_provider pointer
                if config.get("default_provider") == old_name:
                    config["default_provider"] = new_name
                # 3) models[*].provider field + provider-scoped model keys
                models_dict = config.get("models", {}) or {}
                renamed_models: dict[str, dict] = {}
                old_prefix = f"{old_name}/"
                new_prefix = f"{new_name}/"
                for mid, mcfg in models_dict.items():
                    new_mid = (new_prefix + mid[len(old_prefix):]) if mid.startswith(old_prefix) else mid
                    if (mcfg or {}).get("provider") == old_name:
                        mcfg = dict(mcfg)
                        mcfg["provider"] = new_name
                    renamed_models[new_mid] = mcfg
                config["models"] = renamed_models
                # 4) tombstones — rewrite scoped form, leave bare ids alone
                tombstones = config.get("deleted_models", []) or []
                config["deleted_models"] = [
                    (new_prefix + t[len(old_prefix):]) if t.startswith(old_prefix) else t
                    for t in tombstones
                ]
                with open(config_path, "w") as f:
                    json.dump(config, f, indent=2)
                # 5) in-memory mirrors
                server_config["providers"] = providers
                if server_config.get("default_provider") == old_name:
                    server_config["default_provider"] = new_name
                engine._models_config = dict(renamed_models)
                # 6) invalidate caches for both old and new names
                engine.invalidate_key_pool(old_name)
                engine.invalidate_key_pool(new_name)
                engine.clear_provider_cache()
                self._send_json({"status": "renamed", "old_name": old_name, "new_name": new_name})
            except Exception as e:
                self._send_json({"error": str(e)}, 500)
        else:
            self._send_json({"error": f"Unknown action: {action}"}, 400)

    def _handle_test_provider(self):
        """POST /v1/providers/test — test provider connection."""
        body = self._read_json()
        name = body.get("name")
        if name and not body.get("base_url"):
            providers = server_config.get("providers", {})
            p = providers.get(name, {})
            base_url = p.get("base_url", "")
            # Pick the first available key using the pool
            pool = engine._get_key_pool(name, p)
            api_key = pool.pick() or p.get("api_key", "")
        else:
            base_url = body.get("base_url", "")
            api_key = body.get("api_key", "")
        try:
            models = engine.get_available_models(api_key, base_url)
            self._send_json({
                "status": "ok",
                "models": len(models),
                "model_count": len(models),
                "model_list": models,
            })
        except Exception as e:
            self._send_json({
                "status": "error",
                "error": str(e),
                "models": [],
            })

    def _handle_provider_stats(self):
        """GET /v1/providers/stats?days=30 — per-provider+key call/token/cost aggregates."""
        days = int((self.path.split("days=")[-1].split("&")[0]) if "days=" in self.path else 30)
        try:
            rows = engine._cost_tracker.per_provider_key_stats(days=days)
        except Exception as e:
            self._send_json({"error": str(e)}, 500)
            return
        # Group by provider
        by_provider = {}
        for r in rows:
            pname = r["provider"]
            if pname not in by_provider:
                by_provider[pname] = {"provider": pname, "calls": 0, "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0, "keys": []}
            by_provider[pname]["calls"] += r["calls"]
            by_provider[pname]["tokens_in"] += r["tokens_in"] or 0
            by_provider[pname]["tokens_out"] += r["tokens_out"] or 0
            by_provider[pname]["cost_usd"] += r["cost_usd"] or 0.0
            by_provider[pname]["keys"].append({
                "key_name": r["key_name"] or "(unknown)",
                "calls": r["calls"],
                "tokens_in": r["tokens_in"] or 0,
                "tokens_out": r["tokens_out"] or 0,
                "cost_usd": round(r["cost_usd"] or 0.0, 6),
                "last_used": r["last_used"] or "",
            })
        result = sorted(by_provider.values(), key=lambda x: x["calls"], reverse=True)
        for p in result:
            p["cost_usd"] = round(p["cost_usd"], 6)
        self._send_json({"stats": result, "days": days})

    def _handle_models_config_get(self):
        """GET /v1/models/config — return models configuration.

        Each model entry is annotated with `is_local` (derived from the resolved
        provider's base_url) so the web UI can filter without re-implementing
        the local-URL matcher.

        Non-admin callers receive only the models in their allowed_models grant
        (mirrors /v1/models scoping), and sensitive fields are stripped — the
        chat dropdown only needs naming + locality + enable/priority. Admins
        get the full payload for the Models tab.
        """
        user = getattr(self, '_auth_user', _auth_mod.SYNTHETIC_ADMIN)
        is_admin = bool(user) and (user["role"] == "admin" or user["id"] == "__system__")
        allowed = None
        if not is_admin:
            allowed = _auth_mod.AuthDB.get_user_allowed_models(user["id"])

        # Whitelist of fields safe to expose to non-admin chat UI. Anything
        # else (api keys are already in providers, but cost/inference/warmup
        # internals, raw_formats, profile knobs, base_url, etc.) is stripped.
        _SAFE_FIELDS = (
            "display_name", "description", "shortname", "enabled", "priority",
            "provider", "base_model_id", "is_local", "thinking_format",
            "max_context", "max_output", "capabilities", "caveman_system",
        )

        models = {}
        for mid, cfg in (engine._models_config or {}).items():
            if allowed is not None and mid not in allowed:
                continue
            entry = dict(cfg)
            try:
                entry["is_local"] = engine.is_model_local(mid)
            except Exception:
                entry["is_local"] = False
            if not is_admin:
                entry = {k: entry[k] for k in _SAFE_FIELDS if k in entry}
            models[mid] = entry
        self._send_json({
            "models": models,
            "capabilities": list(engine.CAPABILITY_VALUES),
        })

    def _handle_models_config_save(self):
        """POST /v1/models/config — save/update/sync models configuration."""
        body = self._read_json()
        action = body.get("action", "save")
        config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json")

        try:
            config = {}
            if os.path.exists(config_path):
                with open(config_path) as f:
                    config = json.load(f)

            # Snapshot pre-save warmup flags + KV-prefix-relevant fields so we
            # can invalidate pool entries for models whose warmup was turned
            # off, or whose system-prompt-shaping config changed enough that
            # the pooled KV prefix is suspect.
            _prefix_fields = ("warmup", "warmup_mode", "enabled", "max_context",
                              "warmup_allow_cloud", "parallel_tool_calls",
                              "caveman_system", "provider", "base_model_id",
                              "profile")
            prev_model_snapshot = {
                mid: {k: cfg.get(k) for k in _prefix_fields}
                for mid, cfg in (engine._models_config or {}).items()
            }

            tombstones = list(config.get("deleted_models", []) or [])

            if action == "save":
                models = body.get("models", {})
                config["models"] = models
                engine._models_config = dict(models)
                # Any model id present in the new dict is, by definition, no
                # longer deleted — strip from tombstones (handles manual re-add).
                if tombstones:
                    tombstones = [mid for mid in tombstones if mid not in models]
                    config["deleted_models"] = tombstones

            elif action == "update":
                model_id = body.get("model_id", "")
                model_cfg = body.get("config", {})
                if not model_id:
                    self._send_json({"error": "model_id required"}, 400)
                    return
                config.setdefault("models", {})
                config["models"][model_id] = model_cfg
                engine._models_config[model_id] = model_cfg
                # Re-adding/updating an id revives it from the tombstone list.
                if model_id in tombstones:
                    tombstones.remove(model_id)
                    config["deleted_models"] = tombstones

            elif action == "delete":
                # User-initiated single-model delete. Removes from active config
                # AND tombstones the id so init_models_config doesn't auto-rediscover
                # it on next startup or sync.
                model_id = body.get("model_id", "")
                if not model_id:
                    self._send_json({"error": "model_id required"}, 400)
                    return
                config.setdefault("models", {}).pop(model_id, None)
                engine._models_config.pop(model_id, None)
                if model_id not in tombstones:
                    tombstones.append(model_id)
                config["deleted_models"] = tombstones

            elif action == "resync_provider":
                # Full user-initiated resync of one provider:
                #   1) drop ALL models attributed to that provider
                #   2) clear tombstones for those ids (incl. provider-scoped form)
                #   3) re-discover from /models endpoint
                # Never runs automatically — UI button only.
                prov_name = body.get("provider", "")
                if not prov_name:
                    self._send_json({"error": "provider required"}, 400)
                    return
                all_providers = server_config.get("providers", {})
                if prov_name not in all_providers:
                    self._send_json({"error": f"unknown provider: {prov_name}"}, 400)
                    return
                models_dict = config.setdefault("models", {})
                # Identify everything tied to this provider, in either bare or
                # provider-scoped form.
                cleared_ids: set[str] = set()
                for mid, mcfg in list(models_dict.items()):
                    if (mcfg or {}).get("provider") == prov_name:
                        cleared_ids.add(mid)
                        # Also collect the bare id behind a scoped key, since
                        # tombstones can appear in either form.
                        base = (mcfg or {}).get("base_model_id")
                        if base:
                            cleared_ids.add(base)
                        del models_dict[mid]
                        engine._models_config.pop(mid, None)
                # Clear tombstones for those ids + any "<provider>/..." scoped tombstones.
                tombstones = [
                    mid for mid in tombstones
                    if mid not in cleared_ids and not mid.startswith(f"{prov_name}/")
                ]
                config["deleted_models"] = tombstones
                # Re-discover this provider's models (synchronously — user clicked
                # a button and is waiting). Persist + clear caches.
                providers_subset = {prov_name: all_providers[prov_name]}
                synced = engine.init_models_config(
                    providers_subset, models_dict,
                    all_providers=all_providers,
                    deleted_models=tombstones,
                )
                config["models"] = synced
                engine._models_config = dict(synced)
                engine.clear_provider_cache()

            elif action == "sync":
                # Run sync in background thread — return immediately
                sync_provider = body.get("provider")  # optional: sync single provider
                def _bg_sync(provider_filter=None):
                    try:
                        all_providers = server_config.get("providers", {})
                        if provider_filter:
                            providers = {k: v for k, v in all_providers.items() if k == provider_filter}
                        else:
                            providers = all_providers
                        cfg_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json")
                        with open(cfg_path) as f:
                            cfg = json.load(f)
                        existing = cfg.get("models", {})
                        deleted = cfg.get("deleted_models", [])
                        synced = engine.init_models_config(
                            providers, existing,
                            all_providers=all_providers,
                            deleted_models=deleted,
                        )
                        cfg["models"] = synced
                        with open(cfg_path, "w") as f:
                            json.dump(cfg, f, indent=2)
                        engine.clear_provider_cache()
                    except Exception as e:
                        import traceback
                        print(f"[sync] error: {e}")
                        traceback.print_exc()
                threading.Thread(target=_bg_sync, args=(sync_provider,), daemon=True).start()
                self._send_json({"status": "syncing"})
                return

            else:
                self._send_json({"error": f"Unknown action: {action}"}, 400)
                return

            with open(config_path, "w") as f:
                json.dump(config, f, indent=2)

            # Clear provider cache since model config changed
            engine.clear_provider_cache()

            # Invalidate warm-pool slots for models whose warmup flag flipped
            # off OR whose KV-prefix-relevant config changed (system prompt,
            # tool set, context size, provider all invalidate the primed prefix).
            # Also drop the cached _warmup_state so the keeper re-primes.
            new_warmup_models: set[str] = set()
            for mid, prev in prev_model_snapshot.items():
                now_cfg = engine._models_config.get(mid, {}) or {}
                now = {k: now_cfg.get(k) for k in _prefix_fields}
                was_on = bool(prev.get("warmup"))
                now_on = bool(now.get("warmup"))
                if was_on and not now_on:
                    warm_pool.invalidate_model(mid, reason="warmup flag off")
                    engine.set_warmup_state(mid, state="idle", last_error="")
                elif now_on and prev != now:
                    warm_pool.invalidate_model(mid, reason="config changed")
                    engine.set_warmup_state(mid, state="idle",
                                             last_warmup_ts=0, last_error="")
            # Newly-enabled warmup models (weren't in prev snapshot)
            for mid, cfg in (engine._models_config or {}).items():
                if cfg.get("warmup") and mid not in prev_model_snapshot:
                    new_warmup_models.add(mid)

            # Poke keeper so it re-evaluates immediately instead of waiting up
            # to interval_seconds (default 30s) — the set of models to prime
            # may have just changed.
            _wake_warmup_keeper()

            self._send_json({"status": "saved", "models": dict(engine._models_config)})
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    def _handle_warmup_status(self):
        """GET /v1/warmup/status — per-model warmup state snapshot for UI indicators.

        Hold-forever semantics: a model is 'warm' from the moment it's primed
        (or first used) and stays warm until something external evicts it.
        We don't age warm states back to cold on a TTL timer.
        """
        states = engine.all_warmup_states()
        pool_states = warm_pool.all_states()
        wcfg = server_config.get("warmup", {}) or {}
        now = time.time()
        out = {}
        any_warming = False
        any_pool_building = False
        for mid, _raw_cfg in engine._models_config.items():
            cfg = engine.resolve_model_settings(mid)
            if not cfg.get("warmup"):
                continue
            st = states.get(mid, {
                "state": "idle", "last_warmup_ts": 0, "last_used_ts": 0,
                "last_error": "", "next_due_ts": 0,
            })
            last = max(st.get("last_warmup_ts", 0), st.get("last_used_ts", 0))
            age = (now - last) if last else None
            effective = st.get("state", "idle")
            if effective == "warming":
                any_warming = True
            pool = pool_states.get(mid, {
                "state": "empty", "ready": 0, "building": 0,
                "target": WarmSessionPool.target_depth(), "built_at": 0,
            })
            if pool.get("state") == "building":
                any_pool_building = True
            desired_mode = (cfg.get("warmup_mode") or "full").lower()
            if desired_mode not in ("full", "minimal"):
                desired_mode = "full"
            out[mid] = {
                "state": effective,
                "last_warmup_ts": st.get("last_warmup_ts", 0),
                "last_used_ts": st.get("last_used_ts", 0),
                "last_error": st.get("last_error", ""),
                "age_seconds": age,
                "enabled": True,
                "display_name": cfg.get("display_name", mid),
                "provider": cfg.get("provider", ""),
                "mode": st.get("mode", ""),
                "desired_mode": desired_mode,
                "pool_state": pool.get("state", "empty"),
                "pool_built_at": pool.get("built_at", 0),
                "ready": pool.get("ready", 0),
                "building": pool.get("building", 0),
                "target": pool.get("target", WarmSessionPool.target_depth()),
            }
        self._send_json({
            "models": out,
            "any_warming": any_warming,
            "any_pool_building": any_pool_building,
            "enabled": wcfg.get("enabled", True),
            "interval_seconds": int(wcfg.get("interval_seconds", 30)),
        })

    def _handle_queue_status(self):
        """GET /v1/queue/status — snapshot of per-provider concurrency queue.

        Returns active + waiting tickets per provider for the UI modal. Only
        providers with max_concurrent > 0 in config.json get a queue slot; others
        are omitted from the output (they don't gate concurrency).
        """
        try:
            snap = engine.get_provider_queue().snapshot_all()
        except Exception as e:
            self._send_json({"error": str(e), "providers": {}}, 200)
            return
        providers = snap.get("providers", {})
        # Augment with configured max_concurrent for every provider (even idle)
        # so the UI can display capacity even when no tickets are in flight.
        try:
            cfg_providers = (server_config.get("providers") or {})
        except Exception:
            cfg_providers = {}
        for pname, pcfg in cfg_providers.items():
            mc = int(pcfg.get("max_concurrent", 0) or 0)
            if mc <= 0:
                continue
            if pname not in providers:
                providers[pname] = {
                    "provider": pname,
                    "max_concurrent": mc,
                    "active_count": 0,
                    "waiting_count": 0,
                    "active": [],
                    "waiting": [],
                }
        any_waiting = any(p.get("waiting_count", 0) > 0 for p in providers.values())
        any_active = any(p.get("active_count", 0) > 0 for p in providers.values())
        self._send_json({
            "providers": providers,
            "any_waiting": any_waiting,
            "any_active": any_active,
        })

    def _handle_queue_cancel(self):
        """POST /v1/queue/cancel — admin-only. Cancel a queued or running ticket.

        Body: {ticket_id: str, reason?: str}
        Waiting tickets are dropped from the waitlist (~instant).
        Running tickets: fires the ticket's cancel_token, which the SSE stream
        loop in _handle_openai_response checks every incoming chunk — aborts
        at the next byte or keepalive.
        """
        user = self._require_role("admin")
        if not user:
            return
        body = self._read_json() or {}
        ticket_id = (body.get("ticket_id") or "").strip()
        reason = (body.get("reason") or "").strip()
        if not ticket_id:
            self._send_json({"error": "ticket_id required"}, 400)
            return
        result = engine.get_provider_queue().cancel_ticket(
            ticket_id, reason=reason or f"by admin {user.get('username','?')}"
        )
        # Audit log the action for accountability
        try:
            if _audit_log:
                _audit_log.log_action(
                    agent=None,
                    action_type="queue_cancel",
                    tool_name="queue",
                    args_summary=f"ticket={ticket_id} state={result.get('state','?')}",
                    result_summary=f"provider={result.get('provider','?')} session={result.get('session_id','')}",
                    result_status="ok" if result.get("ok") else "error",
                    session_id=result.get("session_id") or None,
                    source=f"admin:{user.get('username','?')}",
                )
        except Exception:
            pass
        if not result.get("ok"):
            self._send_json(result, 404)
            return
        self._send_json(result)

    def _handle_warmup_trigger(self):
        """POST /v1/warmup/trigger — manually warm a specific model. Body: {model}."""
        body = self._read_json()
        mid = body.get("model", "")
        if not mid:
            self._send_json({"error": "model required"}, 400)
            return
        if not engine._models_config.get(mid):
            self._send_json({"error": "unknown model"}, 404)
            return
        cfg = engine.resolve_model_settings(mid)
        wcfg = server_config.get("warmup", {}) or {}
        allow_cloud = bool(cfg.get("warmup_allow_cloud",
                                   wcfg.get("allow_cloud", False)))

        def _run():
            try:
                engine.run_model_warmup(
                    mid, allow_cloud=allow_cloud, agent_id="main",
                    timeout=int(wcfg.get("timeout_seconds", 30)),
                )
            except Exception as e:
                print(f"[warmup-trigger] {mid}: {e}")

        threading.Thread(target=_run, daemon=True, name=f"warmup-trigger-{mid[:12]}").start()
        self._send_json({"status": "triggered", "model": mid})

    def _handle_running_tasks(self):
        """GET /v1/schedule/running — list currently executing scheduled tasks."""
        if engine._scheduler:
            running = engine._scheduler.get_running_tasks()
            # Remove cancel_token from response (not serializable)
            for r in running:
                r.pop("cancel_token", None)
            self._send_json({"running": running})
        else:
            self._send_json({"running": []})

    def _handle_cancel_scheduled(self):
        """POST /v1/schedule/cancel — cancel a running scheduled task."""
        body = self._read_json()
        name = body.get("name", "")
        if not name:
            self._send_json({"error": "Task name required"}, 400)
            return
        if engine._scheduler and engine._scheduler.cancel_running_task(name):
            self._send_json({"status": "cancelling", "name": name})
        else:
            self._send_json({"error": f"Task '{name}' not running"}, 404)

    def _handle_list_tasks(self):
        if engine._task_runner:
            tasks = engine._task_runner.list_tasks()
            for t in tasks:
                if t.get("result") and len(t["result"]) > 500:
                    t["result"] = t["result"][:500] + "..."
            self._send_json({"tasks": tasks})
        else:
            self._send_json({"tasks": []})
