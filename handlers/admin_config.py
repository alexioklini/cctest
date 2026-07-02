"""Server-config, tool-settings, research-mode, NER, hooks, and command-expansion handlers.

Sub-mixin of AdminHandlerMixin (handlers/admin.py module-split refactor). Holds
ONLY this area's `_handle_*` methods (+ area-only private helpers).
AdminHandlerMixin inherits this class, so the combined BrainAgentHandler MRO is
unchanged.

Like admin.py, this module references `engine`, `brain`, `client`, `_db_conn`,
`sqlite3`, `subprocess`, etc. as BARE MODULE GLOBALS injected at runtime by
server._inject_server_globals(). This module's name is in that function's
injection list. All other helpers (`_send_json`, `_read_json`,
`_parse_agent_from_path`, …) resolve via `self.` against the combined MRO.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import time
import threading
import urllib.request
import urllib.error
import uuid
from urllib.parse import unquote, urlencode


class AdminConfigHandlers:
    """Server-config, tool-settings, research-mode, NER, hooks, and command-expansion handlers."""

    def _handle_tool_settings_get(self):
        """GET /v1/tools/settings — admin-only. List all tools (full TOOL_DISPATCH
        keyset, sorted) merged with their per-tool prompt-prose settings, group
        membership, and global enabled / deferred flags.

        Tools without a settings record get the empty defaults (enabled=true,
        deferred=false, all prose blank) so the UI can render a single 'add
        description' / toggle affordance per tool.
        """
        user = self._require_role("admin")
        if not user:
            return
        try:
            all_tools = sorted(engine.TOOL_DISPATCH.keys())
        except Exception:
            all_tools = []
        # Reverse-index TOOL_GROUPS so each tool knows its group.
        tool_to_group: dict[str, str] = {}
        for grp_name, members in (engine.TOOL_GROUPS or {}).items():
            for m in members:
                tool_to_group.setdefault(m, grp_name)
        ts = engine._tool_settings or {}
        # Live in-memory schema index (engine._TOOL_DEF_INDEX, re-exported from
        # engine/tool_schemas.TOOL_DEFINITIONS). This is the EXACT description +
        # input_schema the sidecar serialises onto the wire for the LLM — not the
        # admin prose overlay above. Surfaced read-only so an operator can verify
        # what the model actually receives, independent of whether anyone wrote
        # an override. Empty for MCP/integration-only entries with no schema.
        try:
            def_index = engine._TOOL_DEF_INDEX or {}
        except Exception:
            def_index = {}
        tools = []
        for name in all_tools:
            rec = ts.get(name) or {}
            sdef = def_index.get(name) or {}
            # Canonical status: prefer `state`, fall back to legacy booleans for
            # any un-migrated record (boot migration normalises these). enabled /
            # deferred are derived from state and kept in the payload only so old
            # clients don't break; the new UI reads `state`.
            state = engine._rec_tool_state(rec, default="active")
            flags = engine._tool_state_to_flags(state)
            tools.append({
                "name": name,
                "group": tool_to_group.get(name, ""),
                "state": state,
                # Per-use-case status map (purpose -> state). Only purposes the
                # admin set explicitly are present; everything else inherits the
                # scalar `state` above. Empty when no per-purpose cell was set.
                "states": dict(rec.get("states") or {}),
                "enabled": flags["enabled"],
                "deferred": flags["deferred"],
                "purposes": list(rec.get("purposes") or []),
                "description": rec.get("description", "") or "",
                "when_to_use": rec.get("when_to_use", "") or "",
                "warnings": rec.get("warnings", "") or "",
                "examples": rec.get("examples", "") or "",
                "applies_with": list(rec.get("applies_with") or []),
                # Wire schema. `wire_description_code` = the verbatim code default
                # (TOOL_DEFINITIONS). `wire_description_override` = the admin edit
                # (empty = none). `wire_description` = the EFFECTIVE description the
                # model receives (override if set, else code). input_schema stays a
                # read-only code contract.
                "wire_description_code": sdef.get("description", "") or "",
                "wire_description_override": str(rec.get("wire_description") or ""),
                "wire_description": (str(rec.get("wire_description") or "").strip()
                                     or (sdef.get("description", "") or "")),
                "wire_input_schema": sdef.get("input_schema") or None,
            })
        # Surface integration-only pseudo-tools (entries in tool_config that
        # have no matching TOOL_DISPATCH function — e.g. refinement, translation,
        # text_to_speech, gmail, code_graph). They need the same per-row UI for
        # integration knobs (model, API key, …) but have no prompt prose,
        # purposes, or applies_with. Flagged with integration_only=True so the
        # client can hide the prose section.
        try:
            tool_cfg_keys = set((engine.get_tool_config() or {}).keys())
        except Exception:
            tool_cfg_keys = set()
        dispatch_set = set(all_tools)
        for name in sorted(tool_cfg_keys - dispatch_set):
            tools.append({
                "name": name,
                "group": "integrations",
                "enabled": True,
                "deferred": False,
                "purposes": [],
                "description": "",
                "when_to_use": "",
                "warnings": "",
                "examples": "",
                "applies_with": [],
                "integration_only": True,
            })
        # Per-use-case status matrix (purpose × tool → {state, tokens} + a
        # per-purpose summary with active/inactive/deferred counts and realized
        # token total). `?agent=<id>` folds that agent's tool_overrides in so the
        # per-agent UI shows effective states + sizing; omitted = global matrix.
        from urllib.parse import urlparse, parse_qs
        qs = parse_qs(urlparse(self.path).query)
        agent_id = (qs.get("agent", [None])[0] or None)
        try:
            matrix = engine.tool_purpose_matrix(agent_id)
        except Exception as e:
            matrix = {"error": str(e)[:200]}
        # Also surface the canonical purpose list so the UI doesn't have to
        # hardcode it.
        self._send_json({
            "tools": tools,
            "purposes": list(engine._VALID_PURPOSES),
            "matrix": matrix,
        })

    def _handle_tool_settings_save(self):
        """POST /v1/tools/settings — admin-only. Save one tool's settings record.

        Body: {name, state?, description?, when_to_use?, warnings?,
               examples?, applies_with?}.
        `state` ∈ {active, inactive, deferred} is the canonical status (one
        field, never two flags). Old clients may still POST enabled/deferred
        booleans — they're collapsed to a state for back-compat — but the
        persisted record carries ONLY `state`. Default = active. Empty strings
        clear a prose section. applies_with is the list of OTHER tool names that
        must also be present for this tool's prose to render (all-of gate).
        """
        user = self._require_role("admin")
        if not user:
            return
        body = self._read_json() or {}
        name = (body.get("name") or "").strip()
        if not name:
            self._send_json({"error": "Missing tool name"}, 400)
            return
        if name not in engine.TOOL_DISPATCH:
            self._send_json({"error": f"Unknown tool: {name}"}, 400)
            return
        applies_with_raw = body.get("applies_with") or []
        if not isinstance(applies_with_raw, list):
            self._send_json({"error": "applies_with must be a list"}, 400)
            return
        applies_with = [str(x).strip() for x in applies_with_raw if str(x).strip()]
        # Reject self-references and unknown tools in applies_with
        for req in applies_with:
            if req == name:
                self._send_json({"error": f"applies_with cannot reference self: {req}"}, 400)
                return
            if req not in engine.TOOL_DISPATCH:
                self._send_json({"error": f"applies_with references unknown tool: {req}"}, 400)
                return
        # Canonical status field: `state` ∈ {active, inactive, deferred}.
        # Back-compat: an old client may still POST enabled/deferred booleans
        # instead — collapse them to a state via the same engine helper. The
        # persisted record carries ONLY `state` (the legacy pair is gone on disk).
        state = body.get("state")
        if state is None:
            # Legacy client path: derive from enabled/deferred if present,
            # else default to active.
            legacy = {}
            if isinstance(body.get("enabled"), bool):
                legacy["enabled"] = body["enabled"]
            if isinstance(body.get("deferred"), bool):
                legacy["deferred"] = body["deferred"]
            state = engine._rec_tool_state(legacy or None, default="active")
        if state not in engine.TOOL_STATES:
            self._send_json({"error": f"state must be one of {list(engine.TOOL_STATES)}"}, 400)
            return
        # purposes: list of canonical purpose names. Empty = all purposes.
        purposes_raw = body.get("purposes") or []
        if not isinstance(purposes_raw, list):
            self._send_json({"error": "purposes must be a list"}, 400)
            return
        purposes = [str(p).strip() for p in purposes_raw if str(p).strip()]
        for p in purposes:
            if p not in engine._VALID_PURPOSES:
                self._send_json({"error": f"unknown purpose: {p} (valid: {list(engine._VALID_PURPOSES)})"}, 400)
                return
        # Per-use-case status map: {purpose: state}. Optional + additive — keys
        # must be canonical purposes, values canonical states. The scalar `state`
        # above stays the catch-all default for any purpose NOT listed here. An
        # empty/absent map keeps the record scalar-clean (KV-prefix stable).
        states_raw = body.get("states") or {}
        if not isinstance(states_raw, dict):
            self._send_json({"error": "states must be an object"}, 400)
            return
        states: dict[str, str] = {}
        for k, v in states_raw.items():
            kp = str(k).strip()
            if kp not in engine._VALID_PURPOSES:
                self._send_json({"error": f"states: unknown purpose: {kp} (valid: {list(engine._VALID_PURPOSES)})"}, 400)
                return
            if v not in engine.TOOL_STATES:
                self._send_json({"error": f"states[{kp}] must be one of {list(engine.TOOL_STATES)}"}, 400)
                return
            states[kp] = v
        # MERGE the posted per-purpose states into the EXISTING map rather than
        # replacing it — a single-cell edit (saveToolPurposeCell posts only the
        # changed purposes) must not wipe the other purposes' states. The table
        # is the source of truth for membership, so losing a cell silently drops
        # a tool from / adds it to a channel. The full map is preserved; only the
        # posted keys are overwritten.
        _existing = engine._tool_settings or {}
        _prev_states = dict(((_existing.get(name) or {}).get("states")) or {})
        _prev_states.update(states)
        # Wire-description override (the editable schema description the model
        # receives). Present in body → use it (empty string clears the override
        # → fall back to the code default); absent from body → preserve the
        # existing override (a prose-only save must not wipe a schema edit).
        if "wire_description" in body:
            _wire_desc = str(body.get("wire_description") or "").strip()
        else:
            _wire_desc = str((_existing.get(name) or {}).get("wire_description") or "").strip()
        rec = {
            "description": str(body.get("description", "") or ""),
            "when_to_use": str(body.get("when_to_use", "") or ""),
            "warnings": str(body.get("warnings", "") or ""),
            "examples": str(body.get("examples", "") or ""),
            "applies_with": applies_with,
            "state": state,
            "purposes": purposes,
        }
        if _prev_states:
            rec["states"] = _prev_states
        if _wire_desc:
            rec["wire_description"] = _wire_desc
        # Mutate in place so the dict referenced by both server_config and
        # engine._tool_settings stays in sync without re-pointing.
        ts = engine._tool_settings if engine._tool_settings is not None else {}
        ts[name] = rec
        engine._tool_settings = ts
        try:
            server_config["tool_settings"] = ts
        except Exception:
            pass
        # Persist to config.json
        try:
            config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json")
            cfg = {}
            if os.path.exists(config_path):
                with open(config_path) as f:
                    cfg = json.load(f)
            cfg["tool_settings"] = ts
            with open(config_path, "w") as f:
                json.dump(cfg, f, indent=2)
        except Exception as e:
            self._send_json({"error": f"Persist failed: {e}"}, 500)
            return
        # Audit
        try:
            if engine._audit_log:
                _all_empty = not any(rec.get(f) for f in ("description", "when_to_use", "warnings", "examples"))
                engine._audit_log.log_action(
                    agent="main",
                    action_type="tool_settings_save",
                    tool_name=name,
                    args_summary=(f"by={user.get('username','')} state={state} "
                                  f"cleared={_all_empty} "
                                  f"applies_with={applies_with}"),
                    result_status="ok",
                )
        except Exception:
            pass
        self._send_json({"status": "saved", "name": name, "tool": rec})

    def _handle_research_mode_disciplines_get(self):
        """GET /v1/research-mode/disciplines — admin-only. Returns the three
        admin-editable discipline sections (refusal/precision/citation) that
        get injected into the system prompt when research_mode is on.

        Response shape:
          { sections: {refusal, precision, citation},
            defaults: {refusal, precision, citation} }
        `sections` is the current effective value (merged with defaults for
        any missing section). `defaults` lets the UI's reset-per-section
        button restore the factory text.
        """
        user = self._require_role("admin")
        if not user:
            return
        current = engine.get_research_mode_disciplines()
        defaults = dict(engine.RESEARCH_MODE_DISCIPLINE_DEFAULTS)
        self._send_json({
            "sections": current,
            "defaults": defaults,
            "section_order": list(engine.RESEARCH_MODE_DISCIPLINE_SECTIONS),
        })

    def _handle_research_backend_status(self):
        """GET /v1/research/backend — any logged-in user. Reports whether a
        search backend (searxng/exa) is active, so the composer's Deep Research
        toggle can gray itself out when none is configured.
        Response: {"backend": "searxng"|"exa"|"", "available": bool}."""
        if self._require_auth() is None:
            return
        from engine import deep_research
        backend = deep_research.active_backend()
        self._send_json({"backend": backend, "available": bool(backend)})

    def _handle_research_mode_disciplines_save(self):
        """POST /v1/research-mode/disciplines — admin-only. Saves the three
        sections atomically.

        Body: {refusal?: str, precision?: str, citation?: str}
        Empty strings = section omitted from the prompt. Missing keys leave
        the existing value untouched.
        """
        user = self._require_role("admin")
        if not user:
            return
        body = self._read_json() or {}
        valid = set(engine.RESEARCH_MODE_DISCIPLINE_SECTIONS)
        # Validate types
        for k, v in body.items():
            if k not in valid:
                continue  # ignore unknown keys silently — forward-compat
            if not isinstance(v, str):
                self._send_json({"error": f"section {k!r} must be a string"}, 400)
                return
        # Merge: start from current persisted value, overwrite only sections
        # the body provides.
        current = dict(engine._research_mode_disciplines or {})
        cleared_sections = []
        for k in engine.RESEARCH_MODE_DISCIPLINE_SECTIONS:
            if k in body:
                v = body[k]
                current[k] = v
                if not v.strip():
                    cleared_sections.append(k)
        # Persist
        engine._research_mode_disciplines = current
        try:
            server_config["research_mode_disciplines"] = current
        except Exception:
            pass
        try:
            config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json")
            cfg = {}
            if os.path.exists(config_path):
                with open(config_path) as f:
                    cfg = json.load(f)
            cfg["research_mode_disciplines"] = current
            with open(config_path, "w") as f:
                json.dump(cfg, f, indent=2)
        except Exception as e:
            self._send_json({"error": f"Persist failed: {e}"}, 500)
            return
        # Audit
        try:
            if engine._audit_log:
                _changed = [k for k in engine.RESEARCH_MODE_DISCIPLINE_SECTIONS if k in body]
                engine._audit_log.log_action(
                    agent="main",
                    action_type="research_mode_disciplines_save",
                    tool_name="-",
                    args_summary=(f"by={user.get('username','')} "
                                  f"sections={_changed} cleared={cleared_sections}"),
                    result_status="ok",
                )
        except Exception:
            pass
        self._send_json({
            "status": "saved",
            "sections": engine.get_research_mode_disciplines(),
        })

    def _handle_code_mode_extension_get(self):
        """GET /v1/code-mode/extension — admin-only. Returns the GENERAL,
        language-agnostic code-mode prompt extension injected into every
        code-mode project's system prompt.
        Response: { text: <current>, default: <factory> }."""
        user = self._require_role("admin")
        if not user:
            return
        self._send_json({
            "text": engine.get_code_mode_extension(),
            "default": engine._CODE_MODE_EXTENSION_DEFAULT,
        })

    def _handle_code_mode_extension_save(self):
        """POST /v1/code-mode/extension — admin-only. Body: { text: str }.
        Empty string disables the extension; the key materialises in config.json
        on first save."""
        user = self._require_role("admin")
        if not user:
            return
        body = self._read_json() or {}
        text = body.get("text", "")
        if not isinstance(text, str):
            self._send_json({"error": "text must be a string"}, 400)
            return
        engine._code_mode_extension = text
        try:
            server_config["code_mode_extension"] = text
        except Exception:
            pass
        try:
            config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json")
            cfg = {}
            if os.path.exists(config_path):
                with open(config_path) as f:
                    cfg = json.load(f)
            cfg["code_mode_extension"] = text
            with open(config_path, "w") as f:
                json.dump(cfg, f, indent=2)
        except Exception as e:
            self._send_json({"error": f"Persist failed: {e}"}, 500)
            return
        try:
            if engine._audit_log:
                engine._audit_log.log_action(
                    agent="main", action_type="code_mode_extension_save",
                    tool_name="-",
                    args_summary=(f"by={user.get('username','')} "
                                  f"len={len(text)} cleared={not text.strip()}"),
                    result_status="ok")
        except Exception:
            pass
        self._send_json({"status": "saved", "text": engine.get_code_mode_extension()})

    def _handle_gdpr_ner_models_get(self):
        """GET /v1/gdpr/ner-models — admin-only. List every spaCy NER
        language Brain knows about plus its load state.

        Response:
          {languages: [{lang, display, model, loaded, failed}, ...]}
        """
        user = self._require_role("admin")
        if not user:
            return
        from engine import pii_ner
        self._send_json({"languages": pii_ner.list_loaded()})

    def _handle_gdpr_ner_models_post(self):
        """POST /v1/gdpr/ner-models — admin-only. Load or unload one
        language's NER model synchronously.

        Body: {action: 'load'|'unload', lang: str}
        Returns the same shape as GET so the client can re-render without a
        follow-up request.
        """
        user = self._require_role("admin")
        if not user:
            return
        body = self._read_json() or {}
        action = (body.get("action") or "").strip()
        lang = (body.get("lang") or "").strip()
        if action not in ("load", "unload"):
            self._send_json({"error": "action must be 'load' or 'unload'"}, 400)
            return
        from engine import pii_ner
        if lang not in pii_ner.KNOWN_LANGUAGES:
            known = ", ".join(sorted(pii_ner.KNOWN_LANGUAGES.keys())) or "(none)"
            self._send_json({
                "error": f"unknown lang {lang!r}; known: {known}",
            }, 400)
            return
        if action == "load":
            pii_ner.load_models((lang,))
            ok = pii_ner.is_available(lang)
            status = "loaded" if ok else "load_failed"
        else:
            existed = pii_ner.unload_model(lang)
            ok = True
            status = "unloaded" if existed else "not_loaded"
        try:
            if engine._audit_log:
                engine._audit_log.log_action(
                    agent="main",
                    action_type="gdpr_ner_models_change",
                    tool_name="-",
                    args_summary=(f"by={user.get('username','')} "
                                  f"action={action} lang={lang} result={status}"),
                    result_status="ok" if ok else "error",
                )
        except Exception:
            pass
        self._send_json({
            "status": status,
            "languages": pii_ner.list_loaded(),
        })

    def _handle_server_config(self):
        """POST /v1/services/server — update server defaults (default_model, attachment_image_model)."""
        body = self._read_json()
        config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json")
        result = {}

        # --- Default model ---
        model = body.get("default_model")
        if model:
            providers = server_config.get("providers", {})
            provider_name = None
            mcfg = engine._models_config or {}
            if model in mcfg and mcfg[model].get("provider"):
                provider_name = mcfg[model]["provider"]
            else:
                for pname, p in providers.items():
                    if p.get("default_model") == model:
                        provider_name = pname
                        break
            server_config["default_model"] = model
            if provider_name:
                server_config["api_key"] = providers[provider_name].get("api_key", "")
                server_config["base_url"] = providers[provider_name].get("base_url", "")
            try:
                config = {}
                if os.path.exists(config_path):
                    with open(config_path) as f:
                        config = json.load(f)
                # Persist the chosen default model itself — previously only
                # default_provider was written, so the model never survived a
                # restart (server.default_model stayed empty → background calls
                # like Brainy had no fallback model).
                config["default_model"] = model
                if provider_name:
                    config["default_provider"] = provider_name
                with open(config_path, "w") as f:
                    json.dump(config, f, indent=2)
            except Exception as e:
                self._send_json({"error": str(e)}, 500)
                return
            result["default_model"] = model
            result["default_provider"] = provider_name or ""

        # --- Attachment image model ---
        if "attachment_image_model" in body:
            aim = body["attachment_image_model"] or ""
            server_config["attachment_image_model"] = aim
            try:
                config = {}
                if os.path.exists(config_path):
                    with open(config_path) as f:
                        config = json.load(f)
                att_cfg = config.setdefault("attachments", {})
                att_cfg["image_model"] = aim
                with open(config_path, "w") as f:
                    json.dump(config, f, indent=2)
            except Exception as e:
                self._send_json({"error": str(e)}, 500)
                return
            result["attachment_image_model"] = aim

        # --- Chat summary model ---
        # Background LLM that generates the per-chat synopsis surfaced as
        # hover tooltip + collapsible block. Empty = auto-pick (cheapest
        # Haiku, else cheapest enabled model).
        if "chat_summary_model" in body:
            csm = str(body["chat_summary_model"] or "").strip()
            if csm:
                mcfg = (engine._models_config or {}).get(csm) or {}
                if not mcfg.get("enabled"):
                    self._send_json({"error": f"chat_summary_model: unknown or disabled model '{csm}'"}, 400)
                    return
            server_config["chat_summary_model"] = csm
            try:
                config = {}
                if os.path.exists(config_path):
                    with open(config_path) as f:
                        config = json.load(f)
                if csm:
                    config["chat_summary_model"] = csm
                else:
                    config.pop("chat_summary_model", None)
                with open(config_path, "w") as f:
                    json.dump(config, f, indent=2)
            except Exception as e:
                self._send_json({"error": str(e)}, 500)
                return
            result["chat_summary_model"] = csm

        # --- Classifier model ---
        # The auto-route PROMPT classifier (forced-tool routing JSON). Split
        # from chat_summary_model so the fast/accurate small model that wins the
        # classify bench (cloud mistral-small) can differ from the summary model
        # (local M4 7B). Empty = fall back to chat_summary_model, then auto-pick.
        if "classifier_model" in body:
            clm = str(body["classifier_model"] or "").strip()
            if clm:
                mcfg = (engine._models_config or {}).get(clm) or {}
                if not mcfg.get("enabled"):
                    self._send_json({"error": f"classifier_model: unknown or disabled model '{clm}'"}, 400)
                    return
            server_config["classifier_model"] = clm
            try:
                config = {}
                if os.path.exists(config_path):
                    with open(config_path) as f:
                        config = json.load(f)
                if clm:
                    config["classifier_model"] = clm
                else:
                    config.pop("classifier_model", None)
                with open(config_path, "w") as f:
                    json.dump(config, f, indent=2)
            except Exception as e:
                self._send_json({"error": str(e)}, 500)
                return
            result["classifier_model"] = clm

        # --- Auto-route classifier mode ---
        # How the composer's "Auto" model picker (and fan-out's
        # background_task_model="auto") classifies intent: keyword heuristics
        # (default, zero cost), an LLM classify on the cheapest/local model, or
        # hybrid (keywords first, LLM only on a miss).
        if "auto_route_classifier_mode" in body:
            mode = str(body["auto_route_classifier_mode"] or "keywords").strip()
            if mode not in ("keywords", "llm", "hybrid"):
                self._send_json({"error": "auto_route_classifier_mode must be one of: keywords, llm, hybrid"}, 400)
                return
            ar = server_config.setdefault("auto_route", {})
            ar["classifier_mode"] = mode
            try:
                config = {}
                if os.path.exists(config_path):
                    with open(config_path) as f:
                        config = json.load(f)
                config.setdefault("auto_route", {})["classifier_mode"] = mode
                with open(config_path, "w") as f:
                    json.dump(config, f, indent=2)
            except Exception as e:
                self._send_json({"error": str(e)}, 500)
                return
            result["auto_route_classifier_mode"] = mode

        # --- GDPR/PII scanner settings ---
        if "gdpr_scanner" in body:
            gs_in = body["gdpr_scanner"]
            if not isinstance(gs_in, dict):
                self._send_json({"error": "gdpr_scanner must be an object"}, 400)
                return
            gs = server_config.setdefault("gdpr_scanner", {})
            for key in ("enabled", "server_log", "name_precision_gate",
                        "block_unscannable_on_cloud"):
                if key in gs_in:
                    gs[key] = bool(gs_in[key])
            # server_block removed 9.195.0 — drop it from any saved config so the
            # stale flag doesn't linger.
            gs.pop("server_block", None)
            # Confidence band thresholds (global lower/upper).
            for key in ("confidence_lower", "confidence_upper"):
                if key in gs_in:
                    try:
                        gs[key] = min(max(float(gs_in[key]), 0.0), 1.0)
                    except (TypeError, ValueError):
                        self._send_json({"error": f"{key} must be a number 0..1"}, 400)
                        return
            if gs.get("confidence_upper", 0.85) <= gs.get("confidence_lower", 0.50):
                self._send_json({"error": "confidence_upper must be > confidence_lower"}, 400)
                return
            if "background_ask_action" in gs_in:
                v = gs_in["background_ask_action"]
                if v not in ("anonymise", "swap_to_local", "ignore"):
                    self._send_json({"error": "background_ask_action must be one of: anonymise, swap_to_local, ignore"}, 400)
                    return
                gs["background_ask_action"] = v
            if "default_local_fallback_model" in gs_in:
                mid = str(gs_in["default_local_fallback_model"] or "")
                # Validate: must be a known, enabled, local model (empty = disabled)
                if mid:
                    mcfg = (engine._models_config or {}).get(mid) or {}
                    if not mcfg.get("enabled"):
                        self._send_json({"error": f"default_local_fallback_model: unknown or disabled model '{mid}'"}, 400)
                        return
                    if not engine.is_model_local(mid):
                        self._send_json({"error": f"default_local_fallback_model: '{mid}' is not local"}, 400)
                        return
                gs["default_local_fallback_model"] = mid

            if "background_pii_action" in gs_in:
                v = gs_in["background_pii_action"]
                if v not in ("anonymise", "swap_to_local", "skip", "abort"):
                    self._send_json({"error": "background_pii_action must be one of: anonymise, swap_to_local, skip, abort"}, 400)
                    return
                gs["background_pii_action"] = v
            if "background_anonymise_fail_action" in gs_in:
                v = gs_in["background_anonymise_fail_action"]
                if v not in ("swap_to_local", "abort"):
                    self._send_json({"error": "background_anonymise_fail_action must be one of: swap_to_local, abort"}, 400)
                    return
                gs["background_anonymise_fail_action"] = v

            # Category actions — only accept known categories + valid actions.
            if "categories" in gs_in:
                cats_in = gs_in["categories"] or {}
                if not isinstance(cats_in, dict):
                    self._send_json({"error": "gdpr_scanner.categories must be an object"}, 400)
                    return
                valid_cats = set(engine.PII_DEFAULT_CATEGORY_ACTIONS.keys())
                out_cats = {}
                for cat, entry in cats_in.items():
                    if cat not in valid_cats:
                        continue
                    action = entry.get("action") if isinstance(entry, dict) else entry
                    if action not in ("ignore", "warn", "block"):
                        self._send_json({"error": f"categories.{cat}.action must be ignore|warn|block"}, 400)
                        return
                    out_cats[cat] = {"action": action}
                # Merge with defaults for any unset categories so save is complete
                for cat, act in engine.PII_DEFAULT_CATEGORY_ACTIONS.items():
                    out_cats.setdefault(cat, {"action": act})
                gs["categories"] = out_cats

            # Rule overrides — reject unknown rule_ids so typos surface.
            if "rule_overrides" in gs_in:
                ovr_in = gs_in["rule_overrides"] or {}
                if not isinstance(ovr_in, dict):
                    self._send_json({"error": "gdpr_scanner.rule_overrides must be an object"}, 400)
                    return
                out_ovr = {}
                valid_rules = set(engine.PII_RULE_CATEGORIES.keys())
                for rid, act in ovr_in.items():
                    if not act:
                        continue
                    if rid not in valid_rules:
                        self._send_json({"error": f"rule_overrides: unknown rule_id '{rid}'"}, 400)
                        return
                    if act not in ("ignore", "warn", "block"):
                        self._send_json({"error": f"rule_overrides[{rid}] must be ignore|warn|block"}, 400)
                        return
                    out_ovr[rid] = act
                gs["rule_overrides"] = out_ovr

            # Per-rule min_occurrences — legacy seed for count_points (no longer a
            # gate). Reject unknown rule_ids; clamp to >=1.
            if "min_occurrences" in gs_in:
                mo_in = gs_in["min_occurrences"] or {}
                if not isinstance(mo_in, dict):
                    self._send_json({"error": "gdpr_scanner.min_occurrences must be an object"}, 400)
                    return
                out_mo = {}
                valid_rules = set(engine.PII_RULE_CATEGORIES.keys())
                for rid, n in mo_in.items():
                    if rid not in valid_rules:
                        self._send_json({"error": f"min_occurrences: unknown rule_id '{rid}'"}, 400)
                        return
                    try:
                        out_mo[rid] = max(1, int(n))
                    except (TypeError, ValueError):
                        self._send_json({"error": f"min_occurrences[{rid}] must be an integer >= 1"}, 400)
                        return
                gs["min_occurrences"] = out_mo

            # Per-rule count_points [lo, hi] — count→score calibration (9.195.0).
            if "count_points" in gs_in:
                cp_in = gs_in["count_points"] or {}
                if not isinstance(cp_in, dict):
                    self._send_json({"error": "gdpr_scanner.count_points must be an object"}, 400)
                    return
                out_cp = {}
                valid_rules = set(engine.PII_RULE_CATEGORIES.keys())
                for rid, pair in cp_in.items():
                    if rid not in valid_rules:
                        self._send_json({"error": f"count_points: unknown rule_id '{rid}'"}, 400)
                        return
                    if not (isinstance(pair, (list, tuple)) and len(pair) == 2):
                        self._send_json({"error": f"count_points[{rid}] must be [lo, hi]"}, 400)
                        return
                    try:
                        lo, hi = int(pair[0]), int(pair[1])
                    except (TypeError, ValueError):
                        self._send_json({"error": f"count_points[{rid}] must be integers"}, 400)
                        return
                    lo = max(1, lo)
                    if hi <= lo:
                        self._send_json({"error": f"count_points[{rid}]: hi must be > lo"}, 400)
                        return
                    out_cp[rid] = [lo, hi]
                gs["count_points"] = out_cp

            # Email allowlist — strip/lowercase/dedupe. Accept "x@y.com" and
            # "@y.com" patterns; reject anything with internal whitespace.
            if "email_allowlist" in gs_in:
                al_in = gs_in["email_allowlist"] or []
                if not isinstance(al_in, list):
                    self._send_json({"error": "gdpr_scanner.email_allowlist must be a list"}, 400)
                    return
                cleaned: list[str] = []
                seen = set()
                for e in al_in:
                    if not isinstance(e, str):
                        continue
                    s = e.strip().lower()
                    if not s or " " in s or "\t" in s:
                        continue
                    if "@" not in s:
                        self._send_json({"error": f"email_allowlist: '{e}' must contain '@'"}, 400)
                        return
                    if s in seen:
                        continue
                    seen.add(s)
                    cleaned.append(s)
                gs["email_allowlist"] = cleaned

            engine._invalidate_gdpr_cache()
            try:
                config = {}
                if os.path.exists(config_path):
                    with open(config_path) as f:
                        config = json.load(f)
                config["gdpr_scanner"] = gs
                with open(config_path, "w") as f:
                    json.dump(config, f, indent=2)
            except Exception as e:
                self._send_json({"error": str(e)}, 500)
                return
            result["gdpr_scanner"] = gs

        # --- MoA (Mixture of Agents) virtual model ---
        # The composer's 🧬 MoA directive: reference pool + gate. Validation is
        # strict where a silent typo would disable the feature unnoticed:
        # gate_task_types entries MUST be classifier vocabulary, pool entries
        # MUST be known+enabled models.
        if "moa" in body:
            moa_in = body["moa"]
            if not isinstance(moa_in, dict):
                self._send_json({"error": "moa must be an object"}, 400)
                return
            mo = server_config.setdefault("moa", {})
            if "enabled" in moa_in:
                mo["enabled"] = bool(moa_in["enabled"])
            if "reference_pool" in moa_in:
                pool_in = moa_in["reference_pool"] or []
                if not isinstance(pool_in, list):
                    self._send_json({"error": "moa.reference_pool must be a list"}, 400)
                    return
                cleaned = []
                for mid in pool_in:
                    if not isinstance(mid, str) or not mid.strip():
                        continue
                    mid = mid.strip()
                    mcfg = (engine._models_config or {}).get(mid) or {}
                    if not mcfg.get("enabled"):
                        self._send_json({"error": f"moa.reference_pool: unknown or disabled model '{mid}'"}, 400)
                        return
                    if mid not in cleaned:
                        cleaned.append(mid)
                mo["reference_pool"] = cleaned
            if "gate_task_types" in moa_in:
                gate_in = moa_in["gate_task_types"] or []
                if not isinstance(gate_in, list):
                    self._send_json({"error": "moa.gate_task_types must be a list"}, 400)
                    return
                valid_tt = set(engine._TASK_TYPES)
                out_gate = []
                for tt in gate_in:
                    if tt not in valid_tt:
                        self._send_json({"error": f"moa.gate_task_types: unknown task_type '{tt}' "
                                                  f"(valid: {', '.join(sorted(valid_tt))})"}, 400)
                        return
                    if tt not in out_gate:
                        out_gate.append(tt)
                mo["gate_task_types"] = out_gate
            # Per-task pools (the Settings matrix). Same strictness as the
            # legacy fields: unknown task_type or unknown/disabled model = 400.
            # Empty lists are dropped (an empty column = gated out anyway).
            if "task_pools" in moa_in:
                tp_in = moa_in["task_pools"] or {}
                if not isinstance(tp_in, dict):
                    self._send_json({"error": "moa.task_pools must be an object {task_type: [models]}"}, 400)
                    return
                valid_tt = set(engine._TASK_TYPES)
                out_tp = {}
                for tt, mids in tp_in.items():
                    if tt not in valid_tt:
                        self._send_json({"error": f"moa.task_pools: unknown task_type '{tt}' "
                                                  f"(valid: {', '.join(sorted(valid_tt))})"}, 400)
                        return
                    if not isinstance(mids, list):
                        self._send_json({"error": f"moa.task_pools.{tt} must be a list of model ids"}, 400)
                        return
                    cleaned = []
                    for mid in mids:
                        if not isinstance(mid, str) or not mid.strip():
                            continue
                        mid = mid.strip()
                        mcfg = (engine._models_config or {}).get(mid) or {}
                        if not mcfg.get("enabled"):
                            self._send_json({"error": f"moa.task_pools.{tt}: unknown or disabled model '{mid}'"}, 400)
                            return
                        if mid not in cleaned:
                            cleaned.append(mid)
                    if cleaned:
                        out_tp[tt] = cleaned
                mo["task_pools"] = out_tp
            for key, lo, hi in (("max_references", 1, 5),
                                ("reference_max_tokens", 64, 4000),
                                ("reference_timeout_s", 5, 600),
                                ("reference_input_max_chars", 1000, 200000)):
                if key in moa_in:
                    try:
                        mo[key] = min(max(int(moa_in[key]), lo), hi)
                    except (TypeError, ValueError):
                        self._send_json({"error": f"moa.{key} must be an integer"}, 400)
                        return
            try:
                config = {}
                if os.path.exists(config_path):
                    with open(config_path) as f:
                        config = json.load(f)
                config["moa"] = mo
                with open(config_path, "w") as f:
                    json.dump(config, f, indent=2)
            except Exception as e:
                self._send_json({"error": str(e)}, 500)
                return
            result["moa"] = engine.get_moa_config()

        if not result:
            self._send_json({"error": "No valid fields to update"}, 400)
            return
        result["status"] = "saved"
        self._send_json(result)

    # --- Wiki config handlers ---

    def _handle_wiki_config_get(self):
        """GET /v1/wiki/config — current wiki-related settings + read-only context
        (which model serves the wiki's text LLM calls, whether TTS is configured)."""
        if self._require_auth() is None:
            return
        mcfg = engine._load_mempalace_config()
        kg = mcfg.get("kg") or {}
        cfg_full = server_config or {}
        tts = (engine.get_tool_config() or {}).get("text_to_speech", {}) or {}
        self._send_json({
            # Editable
            "kg_wiki": bool(kg.get("wiki", False)),         # KG for project-tagged pages
            "tts_model": (tts.get("default_model") or "").strip(),
            # Read-only context (configured elsewhere; shown so the Wiki tab is
            # the one place that explains where the wiki's models come from).
            "kg_enabled": bool(kg.get("enabled", True)),
            "summary_model": (cfg_full.get("chat_summary_model") or "").strip(),
            "default_model": (cfg_full.get("default_model") or "").strip(),
            "available_models": sorted((engine._models_config or {}).keys()),
        })

    def _handle_wiki_config_save(self):
        """POST /v1/wiki/config — save wiki settings (admin). Persists to the
        REPOSITORY-ROOT config.json (mempalace.kg.wiki + text_to_speech.default_model)."""
        if self._require_role("admin") is None:
            return
        body = self._read_json() or {}
        config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json")
        try:
            cfg = {}
            if os.path.exists(config_path):
                with open(config_path) as f:
                    cfg = json.load(f)
            if "kg_wiki" in body:
                cfg.setdefault("mempalace", {}).setdefault("kg", {})["wiki"] = bool(body["kg_wiki"])
                with open(config_path, "w") as f:
                    json.dump(cfg, f, indent=2, ensure_ascii=False)
                engine._mempalace_config_cache = None     # bust the 10s cache
            # TTS model lives in tools_config.json (NOT root config.json) — write
            # it via the tool-config seam so get_tool_config() picks it up.
            if "tts_model" in body:
                m = str(body["tts_model"] or "").strip()
                if m:
                    models = cfg.get("models") or {}
                    if m not in models:
                        self._send_json({"error": f"unknown model id: {m}"}, 400)
                        return
                    engine.save_tool_config({"text_to_speech": {"default_model": m}})
            self._send_json({"status": "saved"})
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    # --- Chat cleanup (auto archive + delete) config handlers ---

    _CLEANUP_DEFAULTS = {
        "enabled": False,
        "archive_after_days": 30,
        "delete_after_days": 90,
        "run_interval_seconds": 3600,
    }

    def _handle_cleanup_config_get(self):
        """GET /v1/cleanup/config — auto archive/delete settings. 0 days = that
        stage disabled; enabled=false = whole feature off."""
        if self._require_auth() is None:
            return
        cfg = dict(self._CLEANUP_DEFAULTS)
        cfg.update((server_config or {}).get("chat_cleanup") or {})
        self._send_json(cfg)

    def _handle_cleanup_config_save(self):
        """POST /v1/cleanup/config — persist auto archive/delete settings to the
        repo-root config.json AND the live server_config (so the chat-cleanup
        daemon picks them up without a restart). Admin-only."""
        if self._require_role("admin") is None:
            return
        body = self._read_json() or {}
        cur = dict(self._CLEANUP_DEFAULTS)
        cur.update((server_config or {}).get("chat_cleanup") or {})
        try:
            if "enabled" in body:
                cur["enabled"] = bool(body["enabled"])
            for k in ("archive_after_days", "delete_after_days"):
                if k in body:
                    v = int(body[k])
                    if v < 0:
                        self._send_json({"error": f"{k} must be >= 0 (0 disables that stage)"}, 400)
                        return
                    cur[k] = v
            if "run_interval_seconds" in body:
                cur["run_interval_seconds"] = max(300, int(body["run_interval_seconds"]))
        except (TypeError, ValueError):
            self._send_json({"error": "archive_after_days / delete_after_days must be integers"}, 400)
            return
        config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json")
        try:
            cfg = {}
            if os.path.exists(config_path):
                with open(config_path) as f:
                    cfg = json.load(f)
            cfg["chat_cleanup"] = cur
            with open(config_path, "w") as f:
                json.dump(cfg, f, indent=2, ensure_ascii=False)
        except Exception as e:
            self._send_json({"error": str(e)}, 500)
            return
        # Live update so the running daemon reads the new values next cycle.
        server_config["chat_cleanup"] = cur
        self._send_json({"status": "saved", "config": cur})

    # --- Tools config handlers ---

    def _handle_tools_config_get(self):
        """GET /v1/tools/config — return tool config with fallback values merged and sensitive fields masked."""
        cfg = engine.get_tool_config()
        # Merge fallback values so UI shows what's actually in use
        exa_cfg = cfg.get("exa_search", {})
        if not exa_cfg.get("api_key"):
            env_key = os.environ.get("EXA_API_KEY", "")
            if env_key:
                exa_cfg["api_key"] = env_key
                exa_cfg["_source"] = "environment variable"
            # No hardcoded built-in default anymore — an unset key stays unset
            # (the Exa backend then returns a 401 the model sees).
        gmail_cfg = cfg.get("gmail", {})
        if not gmail_cfg.get("email") or not gmail_cfg.get("app_password"):
            fb = engine._gmail_config()
            if fb:
                if not gmail_cfg.get("email") and fb.get("email"):
                    gmail_cfg["email"] = fb["email"]
                if not gmail_cfg.get("app_password") and fb.get("app_password"):
                    gmail_cfg["app_password"] = fb["app_password"]
                gmail_cfg["_source"] = "gmail.json"
        # Mask sensitive values
        masked = {}
        for tool_name, tool_cfg in cfg.items():
            masked[tool_name] = dict(tool_cfg)
            for key in ("api_key", "app_password"):
                val = masked[tool_name].get(key, "")
                if val and len(val) > 4:
                    masked[tool_name][key] = "*" * (len(val) - 4) + val[-4:]
        self._send_json(masked)

    def _handle_tools_status(self):
        """GET /v1/tools/status — return tool availability and status."""
        self._send_json(engine.get_tool_status())

    def _handle_tools_breakdown(self):
        """GET /v1/tools/breakdown?agent=<id> — per-group token cost of tool definitions."""
        qs = self.path.split("?", 1)[1] if "?" in self.path else ""
        params = dict(p.split("=", 1) for p in qs.split("&") if "=" in p)
        agent_id = params.get("agent", "main")
        try:
            agent = engine.AgentConfig(agent_id)
        except Exception as e:
            self._send_json({"error": f"Agent not found: {agent_id} ({e})"}, 404)
            return
        with engine.request_context():
            engine.get_request_context().current_agent = agent
            # Use the live MCP manager so connected MCP servers are measured.
            engine.get_request_context().mcp_manager = engine._mcp_manager
            breakdown = engine.get_tool_breakdown(agent_id)
        self._send_json(breakdown)

    def _handle_tools_config_save(self):
        """POST /v1/tools/config — save tool configuration."""
        body = self._read_json()
        if not body:
            self._send_json({"error": "No configuration provided"}, 400)
            return
        # Don't overwrite sensitive fields if masked value is sent
        existing = engine.get_tool_config()
        for tool_name, tool_cfg in body.items():
            for key in ("api_key", "app_password"):
                val = tool_cfg.get(key, "")
                if val and val.startswith("*"):
                    # Masked value — keep existing
                    tool_cfg[key] = existing.get(tool_name, {}).get(key, "")
        result = engine.save_tool_config(body)
        if "error" in result:
            self._send_json(result, 500)
        else:
            self._send_json({"status": "saved", "config": result})

    # --- Hooks handlers ---

    def _handle_hooks_get(self, path: str):
        """GET /v1/agents/{id}/hooks — list hooks for an agent."""
        agent_id = self._parse_agent_from_path(path)
        if not agent_id:
            self._send_json({"error": "Missing agent ID"}, 400)
            return
        try:
            cfg = engine.AgentConfig(agent_id)
            hooks_cfg = cfg.config.get("hooks", {"enabled": False, "timeout": 5000, "scripts": []})
            self._send_json(hooks_cfg)
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    def _handle_hooks_save(self, path: str):
        """POST /v1/agents/{id}/hooks — save hooks config for an agent."""
        agent_id = self._parse_agent_from_path(path)
        if not agent_id:
            self._send_json({"error": "Missing agent ID"}, 400)
            return
        body = self._read_json()
        try:
            agent_json_path = os.path.join(engine.AGENTS_DIR, agent_id, "agent.json")
            config = {}
            if os.path.exists(agent_json_path):
                with open(agent_json_path) as f:
                    config = json.load(f)
            config["hooks"] = body
            with open(agent_json_path, "w") as f:
                json.dump(config, f, indent=2)
            # Reload hook runner cache
            with engine._hook_runners_lock:
                engine._hook_runners.pop(agent_id, None)
            self._send_json({"status": "saved", "hooks": body})
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    # --- MemPalace handlers ---

    def _handle_expand_command(self):
        """POST /v1/commands/expand — expand a custom command template.
        Body: {agent, command, args}
        Returns: {text: "expanded prompt"}
        """
        body = self._read_json()
        agent_id = body.get("agent", "main")
        cmd_name = body.get("command", "")
        cmd_args = body.get("args", "")
        if not cmd_name:
            self._send_json({"error": "command name required"}, 400)
            return
        agent_cfg = engine.AgentConfig(agent_id)
        for cmd in agent_cfg.load_commands():
            if (cmd.get("name", "").lower() == cmd_name.lower() or
                    cmd.get("slug", "").lower() == cmd_name.lower()):
                expanded = engine.AgentConfig.expand_command(cmd, cmd_args)
                self._send_json({"text": expanded, "format": cmd.get("_format", "brain")})
                return
        self._send_json({"error": f"Command '{cmd_name}' not found"}, 404)

    def _handle_settings_commands(self):
        """POST /v1/settings/commands — enable/disable a built-in slash command."""
        body = self._read_json()
        name = body.get("name", "")
        enabled = body.get("enabled", True)
        if not name:
            self._send_json({"error": "name required"}, 400)
            return
        disabled = server_config.get("disabled_commands", [])
        if enabled and name in disabled:
            disabled.remove(name)
        elif not enabled and name not in disabled:
            disabled.append(name)
        server_config["disabled_commands"] = disabled
        # Persist to config.json
        try:
            config_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json")
            config = {}
            if os.path.exists(config_path):
                with open(config_path) as f:
                    config = json.load(f)
            config["disabled_commands"] = disabled
            with open(config_path, "w") as f:
                json.dump(config, f, indent=2)
        except Exception:
            pass
        self._send_json({"status": "ok", "disabled_commands": disabled})
