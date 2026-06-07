"""Configuration doctor — detects misconfigurations that fail silently.

Motivated by the 2026-06 incident where the KG extraction_model (and the TTS /
OCR defaults) pointed at a provider — `mistral-experimental` — that had been
removed weeks earlier. Nothing surfaced it: model→provider resolution falls back
to defaults, per-chunk KG errors are treated as benign noise, and the evals don't
exercise the KG. The result was a silently-dead extraction model for ~3.5 days.

This module runs structured health checks and returns a list of findings, each:
    {check, status: ok|warn|fail, title, detail, fix}

`run_static_checks()` is read-only (config + DB inspection, no network) — safe to
call anytime. `run_live_checks()` adds on-demand network/compute probes (provider
ping, test embedding, 1-chunk KG extract) and may be slow.

All checks reach Brain runtime lazily via `import brain as _brain` (engine modules
must not top-level import brain — cycle).
"""

import os
import time

_OK, _WARN, _FAIL = "ok", "warn", "fail"


def _finding(check, status, title, detail="", fix=""):
    return {"check": check, "status": status, "title": title,
            "detail": detail, "fix": fix}


def _cfg():
    """Return a config dict for the checks. IMPORTANT: the live models dict is
    `brain._models_config`, NOT `server_config['models']` (server_config holds
    providers + scalar settings; models are a separate module global). Merge the
    real models in so model-ref checks see the actual enabled set — reading
    server_config['models'] (absent) made every model-ref look 'not found'."""
    import brain as _brain
    cfg = dict(_brain._server_config() or {})
    live_models = getattr(_brain, "_models_config", None)
    if isinstance(live_models, dict) and live_models:
        cfg["models"] = live_models
    return cfg


# ── Check 1: model → provider integrity ──────────────────────────────────────
# Every model's `provider` field, and every config model-REFERENCE (KG / TTS /
# OCR / summary / fallbacks), must point at a provider that exists in providers{}
# and (for model-refs) a model id that exists + is enabled. This is the exact
# class of bug that broke today.

def _provider_scoped_model(ref):
    """Split 'provider/model_id' → (provider, model_id) or (None, ref)."""
    if ref and "/" in ref:
        prov, _, mid = ref.partition("/")
        return prov, mid
    return None, ref


def check_model_provider_integrity(cfg):
    findings = []
    providers = cfg.get("providers", {}) or {}
    models = cfg.get("models", {}) or {}
    prov_names = set(providers.keys())

    # 1a. Every model's declared provider exists.
    dead = []
    for mid, mc in models.items():
        if not isinstance(mc, dict):
            continue
        p = mc.get("provider")
        if p and p not in prov_names:
            dead.append((mid, p))
    if dead:
        lines = ", ".join(f"{m} → {p!r}" for m, p in dead[:12])
        findings.append(_finding(
            "model_provider", _FAIL,
            f"{len(dead)} model(s) point at a non-existent provider",
            f"These models reference a provider not in providers{{}}: {lines}"
            + (f" (+{len(dead)-12} more)" if len(dead) > 12 else ""),
            "Edit each model's provider (Settings → Models) to an existing "
            f"provider, or re-add the missing provider. Existing: {sorted(prov_names)}"))
    else:
        findings.append(_finding(
            "model_provider", _OK,
            "All models reference existing providers"))

    # 1b. Config MODEL-REFERENCES resolve to an existing + enabled model.
    # (model-ref string, human label) pulled from the well-known config slots.
    refs = []
    mp = cfg.get("mempalace", {}) or {}
    refs.append((mp.get("kg", {}).get("extraction_model"), "MemPalace KG extraction_model"))
    tts = (cfg.get("tool_config", {}) or {}).get("text_to_speech") or cfg.get("text_to_speech") or {}
    refs.append((tts.get("default_model"), "Text-to-speech default_model"))
    ta = (cfg.get("tool_config", {}) or {}).get("transcribe_audio") or cfg.get("transcribe_audio") or {}
    refs.append((ta.get("default_model"), "Transcribe-audio default_model"))
    ocr = cfg.get("ocr", {}) or {}
    if ocr.get("provider"):
        refs.append((f"{ocr.get('provider')}/{ocr.get('model','')}", "OCR provider/model"))
    for key, label in (("default_model", "Server default_model"),
                       ("chat_summary_model", "Chat summary model"),
                       ("background_task_model", "Fan-out background model")):
        if cfg.get(key):
            refs.append((cfg.get(key), label))

    # A ref resolves if SOME model entry matches it, by any of the forms the
    # runtime accepts: the full key (scoped 'prov/id' or bare 'id'), or a
    # model whose base_model_id equals the base part. We treat the ref as OK
    # if at least one MATCHING entry is enabled (scoped variants are commonly
    # enabled while the bare base id is kept disabled). This mirrors how
    # resolve_provider_for_model tolerates scoped/unscoped forms — checking a
    # single exact key produced false "disabled"/"not found" verdicts.
    def _resolve_ref(ref):
        """Return ('ok'|'disabled'|'missing', why)."""
        prov, mid = _provider_scoped_model(ref)
        if prov and prov not in prov_names:
            return "missing", f"provider {prov!r} does not exist"
        candidates = []
        for k, mc in models.items():
            if not isinstance(mc, dict):
                continue
            if k == ref or k == mid or mc.get("base_model_id") == mid:
                candidates.append(mc)
        if not candidates:
            return "missing", "model id not found in models{}"
        if any(mc.get("enabled") is not False for mc in candidates):
            return "ok", ""
        return "disabled", "model is disabled"

    bad_refs = []
    for ref, label in refs:
        if not ref:
            continue
        status, why = _resolve_ref(ref)
        if status != "ok":
            bad_refs.append((label, ref, why))
    if bad_refs:
        for label, ref, why in bad_refs:
            findings.append(_finding(
                "config_model_ref", _FAIL,
                f"{label} is misconfigured",
                f"{label} = {ref!r} — {why}.",
                "Set it to an existing, enabled model in the matching settings "
                "section, or re-add the provider/model."))
    else:
        findings.append(_finding(
            "config_model_ref", _OK,
            "All config model-references resolve to enabled models"))
    return findings


# ── Check 2: provider reachability (static) ──────────────────────────────────
def check_provider_config(cfg):
    findings = []
    import brain as _brain
    providers = cfg.get("providers", {}) or {}
    if not providers:
        return [_finding("provider_config", _WARN, "No providers configured")]
    for name, pc in providers.items():
        if not isinstance(pc, dict):
            continue
        base = (pc.get("base_url") or "").strip()
        is_local = bool(pc.get("is_local"))
        ptype = (pc.get("type") or "").strip()
        # api_key may live in a pool / env; treat presence loosely
        has_key = bool(pc.get("api_key") or pc.get("api_keys"))
        # In-process / local providers (e.g. local-mlx-whisper, type=in_process)
        # legitimately have no base_url — they don't make HTTP calls. Only flag
        # a missing base_url for providers that actually need a URL.
        if not base and not (is_local or ptype == "in_process"):
            findings.append(_finding(
                "provider_config", _FAIL, f"Provider {name!r} has no base_url",
                "A provider without a base_url cannot serve any request.",
                f"Set base_url for {name} in Settings → Providers."))
            continue
        if not base:
            continue  # local/in-process — no URL needed, nothing else to check
        if not is_local and not has_key:
            findings.append(_finding(
                "provider_config", _WARN,
                f"Provider {name!r} (cloud) has no api_key set in config",
                "Non-local provider with no api_key/api_keys — calls will fail "
                "with an auth error unless the key comes from a pool/env.",
                f"Add an api_key for {name}, or confirm it's supplied elsewhere."))
    if not any(f["status"] != _OK for f in findings):
        findings.append(_finding("provider_config", _OK,
                                 f"All {len(providers)} providers have base_url + credentials"))
    return findings


# ── Check 3: MemPalace health (static) ───────────────────────────────────────
def check_mempalace_health(cfg):
    findings = []
    mp = cfg.get("mempalace", {}) or {}
    if not mp.get("enabled", True):
        return [_finding("mempalace", _WARN, "MemPalace is disabled")]
    palace_path = os.path.expanduser(
        os.environ.get("MEMPALACE_PALACE_PATH") or mp.get("palace_path", ""))
    if not palace_path or not os.path.isdir(palace_path):
        return [_finding("mempalace", _FAIL, "Palace directory missing",
                         f"palace_path={palace_path!r} does not exist.",
                         "Fix mempalace.palace_path / MEMPALACE_PALACE_PATH.")]

    # 3a. embedding device must not be the CoreML NaN-trap on this host.
    dev = (os.environ.get("MEMPALACE_EMBEDDING_DEVICE")
           or mp.get("embedding_device") or "").lower()
    if dev in ("", "auto", "coreml"):
        findings.append(_finding(
            "mempalace_embed", _WARN,
            f"Embedding device is {dev or 'unset(auto)'} — CoreML NaN risk",
            "On this Mac the CoreML execution provider produces 100% NaN "
            "embeddings (silent corruption). Device should be 'mlx' or 'cpu'.",
            "Set MEMPALACE_EMBEDDING_DEVICE=mlx (or cpu)."))
    else:
        findings.append(_finding("mempalace_embed", _OK,
                                 f"Embedding device = {dev}"))

    # 3b. backend resolves + dir holds matching artifacts (no mismatch).
    try:
        from mempalace.palace import resolve_backend_name
        backend = resolve_backend_name(palace_path)
        findings.append(_finding("mempalace_backend", _OK,
                                 f"Vector backend = {backend}",
                                 f"palace_path={palace_path}"))
    except Exception as e:
        findings.append(_finding(
            "mempalace_backend", _FAIL, "Backend resolution failed",
            f"{type(e).__name__}: {e}",
            "Check MEMPALACE_BACKEND + that the palace dir holds exactly one "
            "backend's artifacts (BackendMismatchError otherwise)."))

    # 3c. drawer count > 0.
    try:
        from mempalace.palace import get_collection
        col = get_collection(palace_path, create=False)
        n = col.count() if col else 0
        if n <= 0:
            findings.append(_finding(
                "mempalace_drawers", _FAIL, "Palace has zero drawers",
                "The vector store is empty — retrieval will return nothing.",
                "Re-mine (clear chat-sync cursors + restart) or check the miner."))
        else:
            findings.append(_finding("mempalace_drawers", _OK,
                                     f"{n:,} drawers indexed"))
    except Exception as e:
        findings.append(_finding(
            "mempalace_drawers", _WARN, "Could not count drawers",
            f"{type(e).__name__}: {e}"))

    # 3d. stray .corrupt-* / leftover repair temp collections.
    try:
        corrupt = [d for d in os.listdir(palace_path) if ".corrupt-" in d]
        if corrupt:
            findings.append(_finding(
                "mempalace_corrupt", _WARN,
                f"{len(corrupt)} quarantined HNSW segment(s) in palace dir",
                "Corrupt-segment dirs left in place. Harmless if quarantined "
                "elsewhere; clutter otherwise.",
                "Move *.corrupt-* out of the palace dir once stable."))
    except OSError:
        pass
    return findings


# ── Check 4: KG extraction health (static) ───────────────────────────────────
def check_kg_health(cfg):
    findings = []
    mp = cfg.get("mempalace", {}) or {}
    if not (mp.get("kg", {}) or {}).get("enabled", True):
        return [_finding("kg", _WARN, "KG extraction disabled")]
    import brain as _brain
    chats_db = None
    try:
        from server_lib.db import ChatDB
        chats_db = ChatDB._db_path() if hasattr(ChatDB, "_db_path") else None
    except Exception:
        pass
    # Fall back to the known location.
    if not chats_db:
        chats_db = os.path.join("agents", "main", "chats.db")
    if not os.path.isfile(chats_db):
        return [_finding("kg", _WARN, "No KG run history yet",
                         "chats.db not found — cannot assess KG run health.")]
    import sqlite3
    try:
        c = sqlite3.connect(chats_db, timeout=5)
        if not c.execute("SELECT name FROM sqlite_master WHERE name='kg_extraction_log'").fetchone():
            return [_finding("kg", _OK, "KG never run (no log) — nothing to flag")]
        # Judge each wing by its MOST RECENT run, not a 24h sum — a wing that
        # was failing pre-fix but whose latest run succeeded must not stay
        # flagged for old failures. (The 24h sum mixed pre/post-fix runs and
        # flagged already-recovered wings.)
        wings = [r[0] for r in c.execute(
            "SELECT DISTINCT palace_wing FROM kg_extraction_log").fetchall()]
        broken = []
        for wing in wings:
            row = c.execute(
                "SELECT errors, triples_extracted, drawers_processed, error_msg, started_at "
                "FROM kg_extraction_log WHERE palace_wing=? "
                "ORDER BY started_at DESC LIMIT 1", (wing,)).fetchone()
            if not row:
                continue
            errs, tri, proc, emsg, st = row
            errs, tri, proc = int(errs or 0), int(tri or 0), int(proc or 0)
            if st < time.time() - 24 * 3600:
                continue  # stale wing, no recent activity — don't nag
            em = (emsg or "").lower()
            transport = any(s in em for s in (
                "no reply", "could not resolve", "connection", "auth",
                "timeout", "unauthorized", "not found", "no provider"))
            # Broken = latest run had errors AND produced no triples (a working
            # run that emits a few parse-miss errors but still extracts is fine).
            if errs and tri == 0 and (transport or errs >= 3):
                broken.append((wing, errs, tri, emsg))
        if broken:
            for wing, errs, tri, emsg in broken:
                findings.append(_finding(
                    "kg", _FAIL,
                    f"KG extraction failing for wing {wing}",
                    f"Last 24h: {errs} errors, {tri} triples extracted. "
                    f"Last error: {emsg!r}",
                    "The extraction model/provider is likely broken — check "
                    "Settings → KG extraction_model points at a reachable "
                    "provider. Failed chunks now retry automatically."))
        else:
            findings.append(_finding("kg", _OK,
                                     "KG extraction healthy (no failing wings in last 24h)"))
        c.close()
    except Exception as e:
        findings.append(_finding("kg", _WARN, "Could not read KG run log",
                                 f"{type(e).__name__}: {e}"))
    return findings


# ── Orchestration ────────────────────────────────────────────────────────────
def run_static_checks():
    cfg = _cfg()
    out = []
    for fn in (check_model_provider_integrity, check_provider_config,
               check_mempalace_health, check_kg_health):
        try:
            out.extend(fn(cfg))
        except Exception as e:
            out.append(_finding(fn.__name__, _WARN,
                                f"Check {fn.__name__} crashed",
                                f"{type(e).__name__}: {e}"))
    return out


def run_live_checks():
    """On-demand probes (network/compute). Slower; called from POST /v1/doctor/live."""
    cfg = _cfg()
    out = []
    import brain as _brain

    # Live probe A: a test embedding (catches the CoreML-NaN trap for real).
    try:
        import math
        from mempalace.embedding import get_embedding_function
        ef = get_embedding_function()
        v = ef(["doctor connectivity probe"])[0]
        has_nan = any(x != x for x in v)  # NaN != NaN
        if has_nan:
            out.append(_finding("live_embed", _FAIL,
                                "Embedding function returns NaN vectors",
                                "Every vector is NaN — semantic search is silently broken.",
                                "Set embedding_device to mlx/cpu (NOT auto/coreml)."))
        else:
            out.append(_finding("live_embed", _OK,
                                f"Embedding OK ({len(v)}-dim, no NaN)"))
    except Exception as e:
        out.append(_finding("live_embed", _WARN, "Embedding probe failed",
                            f"{type(e).__name__}: {e}"))

    # Live probe B: resolve + sanity each provider's credentials for its default model.
    providers = cfg.get("providers", {}) or {}
    for name, pc in providers.items():
        if not isinstance(pc, dict):
            continue
        dm = pc.get("default_model")
        if not dm:
            continue
        try:
            r = _brain.resolve_provider_for_model(dm)
            if not (r.get("base_url") and (r.get("api_key") or pc.get("is_local"))):
                out.append(_finding(
                    "live_provider", _WARN,
                    f"Provider {name!r} resolves incompletely",
                    f"default_model {dm!r} → base_url={bool(r.get('base_url'))}, "
                    f"api_key={bool(r.get('api_key'))}"))
        except Exception as e:
            out.append(_finding("live_provider", _WARN,
                                f"Resolve failed for {name!r}",
                                f"{type(e).__name__}: {e}"))
    if not any(f["check"] == "live_provider" for f in out):
        out.append(_finding("live_provider", _OK,
                            "All providers with a default_model resolve credentials"))
    return out


def summarize(findings):
    """Roll findings up into {ok, warn, fail} counts + overall status."""
    counts = {"ok": 0, "warn": 0, "fail": 0}
    for f in findings:
        counts[f.get("status", "ok")] = counts.get(f.get("status", "ok"), 0) + 1
    overall = "fail" if counts["fail"] else ("warn" if counts["warn"] else "ok")
    return {"overall": overall, "counts": counts}
