# Extracted from server.py — provider/model config and warmup handlers
import hashlib
import json
import os
import threading
import time

import brain as engine


class ProvidersHandlerMixin:

    def _handle_list_providers(self):
        providers = server_config.get("providers", {})
        models_cfg = engine._models_config or {}
        result = []
        for name, p in providers.items():
            # Use already-known models from config instead of fetching from provider
            all_models = [mid for mid, mcfg in models_cfg.items()
                          if mcfg.get("provider") == name]
            enabled_models = [mid for mid, mcfg in models_cfg.items()
                              if mcfg.get("provider") == name and mcfg.get("enabled", True)]
            result.append({
                "name": name,
                "base_url": p.get("base_url", ""),
                "api_key": p.get("api_key", "")[:4] + "***" if p.get("api_key") else "",
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
                # If _keep_key is set, preserve existing api_key
                if provider.pop("_keep_key", False):
                    existing = config.get("providers", {}).get(name, {})
                    provider["api_key"] = existing.get("api_key", "")
                config.setdefault("providers", {})[name] = provider
                with open(config_path, "w") as f:
                    json.dump(config, f, indent=2)
                server_config.setdefault("providers", {})[name] = provider
                self._send_json({"status": "added", "name": name})
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
                self._send_json({"status": "deleted", "name": name})
            except Exception as e:
                self._send_json({"error": str(e)}, 500)
        else:
            self._send_json({"error": f"Unknown action: {action}"}, 400)

    # --- Client-hosted local model manifest ---
    # Server declares GGUF weights that Electron clients may download and run
    # locally. Family string is the routing key (server oMLX + client GGUF with
    # matching family are treated as one model for session routing).

    def _handle_client_models_manifest(self):
        """GET /v1/client/models/manifest — return the list of client-eligible
        models. Any authenticated user can read this. Strips absolute paths;
        clients never learn where weights live on the server, only that they
        exist and what their sha256/size are. """
        entries = engine._load_client_models()
        out = []
        for e in entries:
            out.append({
                "id": e.get("id"),
                "family": e.get("family"),
                "sha256": e.get("sha256", ""),
                "size_bytes": int(e.get("size_bytes") or 0),
                "auto_download": bool(e.get("auto_download", False)),
                "download_path": f"/v1/client/models/{e.get('id')}/weights",
            })
        self._send_json({"models": out})

    def _handle_client_engines_manifest(self):
        """GET /v1/client/engines — per-platform llama.cpp binary URLs.

        Admin publishes entries in config.json → client_engines:
        {
          "darwin-arm64": {"url": "...", "sha256": "..."},
          "win32-x64":    {"url": "...", "sha256": "..."},
          "linux-x64":    {"url": "...", "sha256": "..."}
        }

        URL may point to an internal mirror for air-gapped deployments.
        No server-hardcoded URLs — we refuse to invent defaults so a
        misconfigured server never silently fetches from the public
        internet when the admin assumed air-gap."""
        try:
            cfg_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json")
            cfg = {}
            if os.path.exists(cfg_path):
                with open(cfg_path) as f:
                    cfg = json.load(f)
            engines = cfg.get("client_engines", {}) or {}
            # Strip any fields beyond what the Electron client needs.
            out = {}
            for key, entry in engines.items():
                if not isinstance(entry, dict):
                    continue
                u = entry.get("url", "")
                sha = entry.get("sha256", "")
                if u and sha:
                    out[key] = {"url": u, "sha256": sha}
            self._send_json({"engines": out})
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    def _handle_client_model_weights(self, path: str):
        """GET /v1/client/models/<id>/weights — stream GGUF bytes. Supports
        HTTP Range for resumable downloads. Any authenticated user may pull
        weights the admin has listed."""
        # Parse id from path
        rest = path[len("/v1/client/models/"):]
        model_id = rest.rsplit("/weights", 1)[0]
        if not model_id or "/" in model_id or ".." in model_id:
            self._send_json({"error": "invalid model id"}, 400)
            return
        entry = engine.get_client_model(model_id)
        if not entry:
            self._send_json({"error": "model not found"}, 404)
            return
        gguf_path = entry.get("gguf_path", "")
        if not gguf_path or not os.path.isfile(gguf_path):
            self._send_json({"error": "weights file missing on server"}, 404)
            return

        try:
            total = os.path.getsize(gguf_path)
        except OSError as e:
            self._send_json({"error": f"stat failed: {e}"}, 500)
            return

        # Parse Range: bytes=start-end
        range_header = self.headers.get("Range", "") or self.headers.get("range", "")
        start, end = 0, total - 1
        is_partial = False
        if range_header.startswith("bytes="):
            try:
                spec = range_header[len("bytes="):].split("-", 1)
                if spec[0].strip():
                    start = int(spec[0])
                if len(spec) > 1 and spec[1].strip():
                    end = int(spec[1])
                if start < 0 or end >= total or start > end:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{total}")
                    self.end_headers()
                    return
                is_partial = True
            except ValueError:
                self._send_json({"error": "invalid Range header"}, 400)
                return

        length = end - start + 1
        status = 206 if is_partial else 200
        try:
            self.send_response(status)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(length))
            self.send_header("Accept-Ranges", "bytes")
            if is_partial:
                self.send_header("Content-Range", f"bytes {start}-{end}/{total}")
            # sha256 header lets the client verify without a separate call
            if entry.get("sha256"):
                self.send_header("X-Model-SHA256", entry["sha256"])
            self.end_headers()
        except (BrokenPipeError, ConnectionResetError):
            return

        # Audit log the download (once per request, before streaming).
        # Only log the first chunk of a ranged download so large files don't
        # flood audit with one row per MiB — 'start == 0' is our "fresh download
        # or full fetch" heuristic.
        if start == 0:
            try:
                user = getattr(self, "_auth_user", None) or {}
                engine._audit_log.log_action(
                    agent=user.get("id", "anonymous"),
                    action_type="client_model_download",
                    tool_name="client_models",
                    source="weight_stream",
                    args_summary=f"model={model_id} bytes={total}",
                )
            except Exception:
                pass

        # Stream in 1 MiB chunks
        chunk = 1024 * 1024
        try:
            with open(gguf_path, "rb") as f:
                f.seek(start)
                remaining = length
                while remaining > 0:
                    buf = f.read(min(chunk, remaining))
                    if not buf:
                        break
                    try:
                        self.wfile.write(buf)
                    except (BrokenPipeError, ConnectionResetError):
                        return
                    remaining -= len(buf)
        except OSError as e:
            try:
                print(f"[client-models] weight stream error: {e}", file=sys.stderr, flush=True)
            except Exception:
                pass

    def _handle_client_models_admin(self):
        """POST /v1/client/models — admin-only CRUD for the manifest.

        Actions:
          - save: replace full list
          - add: insert/update one entry by id
          - delete: remove one entry by id
        """
        body = self._read_json()
        action = body.get("action", "save")
        config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json")

        def _hash_and_size(p: str) -> tuple[str, int] | None:
            if not p or not os.path.isfile(p):
                return None
            try:
                h = hashlib.sha256()
                size = 0
                with open(p, "rb") as f:
                    while True:
                        b = f.read(1024 * 1024)
                        if not b:
                            break
                        h.update(b)
                        size += len(b)
                return h.hexdigest(), size
            except OSError:
                return None

        def _validate(entry: dict) -> tuple[bool, str]:
            for k in ("id", "family", "gguf_path"):
                if not entry.get(k):
                    return False, f"missing field: {k}"
            if "/" in entry["id"] or ".." in entry["id"]:
                return False, "id must be a simple slug (no '/' or '..')"
            if not os.path.isfile(entry["gguf_path"]):
                return False, f"gguf_path does not exist: {entry['gguf_path']}"
            return True, ""

        try:
            cfg = {}
            if os.path.exists(config_path):
                with open(config_path) as f:
                    cfg = json.load(f)
            current = list(cfg.get("client_models", []) or [])

            if action == "save":
                entries = body.get("client_models", [])
                if not isinstance(entries, list):
                    self._send_json({"error": "client_models must be a list"}, 400)
                    return
                cleaned = []
                for e in entries:
                    ok, msg = _validate(e)
                    if not ok:
                        self._send_json({"error": f"invalid entry: {msg}"}, 400)
                        return
                    # Recompute hash/size unless the caller supplied them AND
                    # the file size hasn't changed.
                    hs = _hash_and_size(e["gguf_path"])
                    if hs is None:
                        self._send_json({"error": f"cannot hash {e['gguf_path']}"}, 500)
                        return
                    e["sha256"], e["size_bytes"] = hs
                    e["auto_download"] = bool(e.get("auto_download", False))
                    cleaned.append(e)
                cfg["client_models"] = cleaned

            elif action == "add":
                entry = body.get("model", {}) or {}
                ok, msg = _validate(entry)
                if not ok:
                    self._send_json({"error": msg}, 400)
                    return
                hs = _hash_and_size(entry["gguf_path"])
                if hs is None:
                    self._send_json({"error": f"cannot hash {entry['gguf_path']}"}, 500)
                    return
                entry["sha256"], entry["size_bytes"] = hs
                entry["auto_download"] = bool(entry.get("auto_download", False))
                # Replace by id if exists, else append
                current = [x for x in current if x.get("id") != entry["id"]]
                current.append(entry)
                cfg["client_models"] = current

            elif action == "delete":
                model_id = body.get("id", "")
                if not model_id:
                    self._send_json({"error": "id required"}, 400)
                    return
                cfg["client_models"] = [x for x in current if x.get("id") != model_id]

            else:
                self._send_json({"error": f"unknown action: {action}"}, 400)
                return

            with open(config_path, "w") as f:
                json.dump(cfg, f, indent=2)

            server_config["client_models"] = cfg.get("client_models", [])
            engine._invalidate_client_models_cache()

            self._send_json({
                "status": "ok",
                "action": action,
                "client_models": cfg.get("client_models", []),
            })
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    def _handle_test_provider(self):
        """POST /v1/providers/test — test provider connection."""
        body = self._read_json()
        # If only name is provided, look up provider config
        name = body.get("name")
        if name and not body.get("base_url"):
            providers = server_config.get("providers", {})
            p = providers.get(name, {})
            base_url = p.get("base_url", "")
            api_key = p.get("api_key", "")
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
            "display_name", "shortname", "enabled", "priority", "provider",
            "base_model_id", "is_local", "thinking_format", "max_context",
            "max_output", "capabilities", "caveman_system",
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
