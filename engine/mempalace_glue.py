"""engine/mempalace_glue.py — MemPalace integration glue (refactor C3, last Tier C).

Extracted from brain.py. Owns the agent-facing MemPalace SEARCH path and its
supporting cluster — the config/import helpers, the cross-encoder reranker
loader, and the explicit "remember this chat" tool.

Owns:
  - `tool_mempalace_query` — THE search tool. Resolves the caller's wing
    (project force-scope + refuse-on-missing-id leak guard), runs the direct
    Chroma query, applies the filename-token boost, optional cross-encoder
    rerank, fingerprint dedup, optional chunk-substitution, and path
    resolution back to absolute `read_path` / `read_path_original`.
  - `tool_save_chat_to_memory` — flips save_to_memory on the active session and
    triggers an immediate chat-sync via the server-installed callback.
  - `_load_mempalace_config` (10s-cached config block reader) +
    `_ensure_mempalace_importable` (lazy venv-site-packages path insert) — the
    config + import helpers the query tool depends on.
  - `_get_reranker_model` + its `_reranker_lock` / `_reranker_cache` globals —
    cross-encoder loader used only by `tool_mempalace_query`.

THE C3 SECURITY GATE — `_wing_visible(wing, own_user, own_teams)`:
  Module-level PURE predicate (was a closure `_visible` inside
  `tool_mempalace_query`). It is the cross-wing visibility filter that stops a
  broad (unspecified-wing) search from surfacing another project's or another
  user's drawers. Behaviour byte-identical to the old closure:
    - project__* AND project_chat__* -> always False (private)
    - user__*  -> True only if wing == own_user
    - team__*  -> True only if wing in own_teams
    - else (bare/untyped) -> True (shared)
  Pinned by tests/test_mempalace_wing_isolation.py — do NOT change semantics.

STAYS in brain.py / server_lib (NOT moved):
  - `_resolve_session_wing`, `_project_id_for_name`, `_memorize_mempalace_turns`
    live in server_lib/db.py (entangled with ChatDB).
  - `ProjectManager` (brain class) — reached lazily via `_brain.ProjectManager`.
  - the 4 tool-registration sites (TOOL_DEFINITIONS / TOOL_GROUPS /
    TOOL_DISPATCH entries) stay in brain.py; only the `tool_*` functions move.
  - `_MempalaceActivity` / `mempalace_activity` (UI telemetry) — reached lazily.

Seams:
  - `_thread_local` from engine.context (low-level base, no cycle).
  - `_ok` / `_err` from engine.tool_exec (pure JSON envelopes, no cycle).
  - every brain-runtime symbol (`ProjectManager`, `mempalace_activity`,
    `_save_chat_to_memory_callback`) is reached lazily via the `_LazyBrain`
    proxy. NO top-level `import brain` (brain imports this module for
    TOOL_DISPATCH — a top-level import would cycle). The heavy `mempalace`
    pip package + `sentence_transformers` / `torch` are imported LAZILY inside
    functions.

brain.py re-exports every symbol defined here via
`from engine.mempalace_glue import (...)` so existing callers (`brain.tool_*`,
`brain._load_mempalace_config`, the KG tools' in-brain calls, the tests'
`mock.patch.object(brain, ...)`, `brain._wing_visible`) resolve unchanged. The
mutable globals (`_reranker_cache`, the config-cache pair, the locks) live HERE
as the single instance — brain's re-export binds the same objects, so
`brain._reranker_cache is engine.mempalace_glue._reranker_cache`.
"""

from __future__ import annotations

import glob
import hashlib
import json
import logging
import os
import re
import sys
import threading
import time

from engine.context import get_request_context
from engine.tool_exec import _ok, _err


class _LazyBrain:
    """Lazy proxy to the live `brain` module (avoids the import cycle —
    brain imports this module). Every brain-runtime symbol this module
    touches is reached through this proxy as `_brain.<name>`."""
    __slots__ = ()

    def __getattr__(self, name):
        import brain as _b
        return getattr(_b, name)


_brain = _LazyBrain()


# --- MemPalace (direct, in-process) ---
#
# MemPalace ships as a Python package in its own venv. We import it lazily
# (only on first call) so Brain startup stays fast and missing installs are
# soft failures. No MCP, no subprocess — `mempalace.searcher.search` runs
# in-process and goes straight to Chroma.

_mempalace_import_lock = threading.Lock()
_mempalace_imported = False
_mempalace_config_cache = None
_mempalace_config_cache_time = 0.0


def _load_mempalace_config() -> dict:
    """Read the 'mempalace' block from config.json. 10s cache."""
    global _mempalace_config_cache, _mempalace_config_cache_time
    now = time.time()
    if _mempalace_config_cache is not None and (now - _mempalace_config_cache_time) < 10:
        return _mempalace_config_cache
    cfg_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.json")
    block = {}
    try:
        with open(cfg_path) as f:
            block = json.load(f).get("mempalace", {}) or {}
    except (OSError, json.JSONDecodeError):
        block = {}
    _mempalace_config_cache = block
    _mempalace_config_cache_time = now
    return block


def _ensure_mempalace_importable() -> tuple[bool, str]:
    """Add the mempalace venv site-packages to sys.path if configured. Idempotent."""
    global _mempalace_imported
    if _mempalace_imported:
        return True, ""
    with _mempalace_import_lock:
        if _mempalace_imported:
            return True, ""
        cfg = _load_mempalace_config()
        site_packages = cfg.get("venv_site_packages", "")
        if site_packages and os.path.isdir(site_packages) and site_packages not in sys.path:
            sys.path.insert(0, site_packages)
        try:
            import mempalace.searcher  # noqa: F401  — probe import
        except ImportError as e:
            return False, f"mempalace package not importable: {e} (venv_site_packages={site_packages!r})"
        _mempalace_imported = True
        return True, ""


def _wing_visible(wing: str, own_user: str, own_teams: set[str]) -> bool:
    """Cross-wing visibility predicate — THE C3 security gate.

    Project wings are always private (never returned in cross-wing searches);
    only the caller's own user__ wing, their team__ wings, and bare/untyped
    (shared) wings are visible. Promoted to module-level from the
    `tool_mempalace_query` `_visible` closure — semantics byte-identical."""
    w = wing or ""
    # Project wings are always private — never returned in cross-wing
    # searches. Both knowledge (`project__`) and chat (`project_chat__`)
    # are scoped to the project's own context.
    if w.startswith("project__") or w.startswith("project_chat__"):
        return False
    if w.startswith("user__"):
        return w == own_user
    if w.startswith("team__"):
        return w in own_teams
    # Anything without a typed prefix is treated as shared.
    return True


def tool_mempalace_query(args: dict) -> str:
    """Query MemPalace. Returns ranked drawers as a JSON list."""
    cfg = _load_mempalace_config()
    if not cfg.get("enabled", True):
        return _err("mempalace: disabled in config.json (mempalace.enabled = false)")
    palace_path = cfg.get("palace_path", "")
    if not palace_path:
        return _err("mempalace: no palace_path configured in config.json")
    if not os.path.isdir(palace_path):
        return _err(f"mempalace: palace_path does not exist: {palace_path}")

    ok, err = _ensure_mempalace_importable()
    if not ok:
        return _err(err)

    query = (args.get("query") or "").strip()
    if not query:
        return _err("mempalace_query: 'query' is required")
    wing = args.get("wing") or None
    # Wing scheme is ID-only (no agent suffix):
    #   project__<project_id>  ← project chats force-scope to this
    #   team__<team_id>        ← team-visible chats
    #   user__<user_id>        ← per-user
    # Plus shared wings (no "__" prefix, e.g. "brain_code") that anyone can read.
    current_user_id = get_request_context().current_user_id or ""
    current_team_ids = list(get_request_context().current_team_ids or [])
    current_project = get_request_context().project or ""
    _ag = get_request_context().current_agent
    # _thread_local.current_agent is an AgentConfig instance (not a string).
    current_agent_id = getattr(_ag, "agent_id", None) or (
        _ag if isinstance(_ag, str) else "main") or "main"
    project_pinned = False
    # Optional: when project-pinned, the model can ask explicitly for
    # past chat memory in this project by setting include_chat_history=true.
    # Default behaviour pins to the project KNOWLEDGE wing only, so wrong
    # answers in past chats can't outrank the underlying source documents.
    include_chat_history = bool(args.get("include_chat_history") or False)
    # A scheduled run has no chat history — `sched-<run_id>` is a fresh,
    # isolated synthetic session, so the project_chat__ wing is always empty.
    # If the model sets include_chat_history=true on a scheduled run it would
    # search that empty wing and get 0 hits, then fall back to free web access
    # (e.g. curl via execute_command) — exactly the v9.31.x webnews symptom.
    # Force it off here so the query always hits the project KNOWLEDGE wing.
    _sid = get_request_context().current_session_id or ""
    if include_chat_history and isinstance(_sid, str) and _sid.startswith("sched-"):
        include_chat_history = False
    # When the caller wants chat history, we search the chat wing AND the
    # knowledge wing (not chat-only). The project chat wing is often empty
    # (chat-sync may never have run, or this is a fresh project) — searching
    # it alone returns 0 hits and the model falls back to free web access
    # (the v9.31.x webnews curl symptom). Including the knowledge wing means
    # a thin/empty chat history can never starve retrieval of the underlying
    # curated source documents. `_extra_wings` is searched alongside `wing`.
    _extra_wings: list[str] = []
    if current_project:
        # Resolve project name → id (uuid hex). Without an id we refuse to
        # search rather than leak across projects.
        proj_cfg = _brain.ProjectManager.get_project(current_agent_id, current_project)
        proj_id = (proj_cfg or {}).get("id") or ""
        if proj_id:
            safe_pid = re.sub(r"[^A-Za-z0-9_.-]", "_", proj_id)
            wing = f"project__{safe_pid}"  # knowledge wing is always searched
            if include_chat_history:
                # Add the chat-history wing on top of the knowledge wing.
                _extra_wings.append(f"project_chat__{safe_pid}")
            project_pinned = True
        else:
            return _err("mempalace_query: project has no id (run a sync first)")
    elif current_user_id and not wing:
        # Default to the user's own wing when nothing else is specified.
        wing = f"user__{current_user_id}"
    room = args.get("room") or None
    n_results = args.get("n_results") or 5
    try:
        n_results = max(1, min(25, int(n_results)))
    except (TypeError, ValueError):
        n_results = 5

    # When no explicit wing and we want to see across the user's own wings +
    # any team wings they're in + shared wings, over-fetch then filter.
    # With the new ID-only scheme this only triggers when something deliberately
    # passes wing=None and we couldn't auto-set a user wing (anonymous caller).
    _needs_user_filter = bool(current_user_id and not wing and not project_pinned)
    fetch_n = n_results * 4 if _needs_user_filter else n_results

    # Use direct Chroma query (mirrors `mempalace search` CLI's `search()`
    # function) instead of the higher-level `search_memories()`. The latter
    # runs a closet-boost + drawer-grep-enrichment pass that produces the
    # "all 19 hits are the document's frontmatter" pathology on closet-
    # boosted multi-chunk sources (see CLAUDE.md v8.21.2 for the deep dive).
    # Vanilla MemPalace CLI's `search` skips that pass and returns the raw
    # Chroma vector hits — which actually diversify across the document
    # because Chroma's distances are per-chunk. That's why `mempalace search
    # "IT-Risk Score Berechnung"` from a vanilla install returns 5 distinct
    # chunks (TOC, frontmatter, section 2.13 body, ...) while Brain's
    # `search_memories()` call returned 19 byte-identical frontmatter blobs
    # plus 1 unrelated chunk.
    mempalace_activity = _brain.mempalace_activity
    mempalace_activity.retrieve_begin()
    try:
        try:
            from mempalace.palace import get_collection as _gc_query
            from mempalace.searcher import build_where_filter as _build_where
            col = _gc_query(palace_path, create=False)
            if col is None:
                return _err(f"mempalace_query: palace collection not found at {palace_path}")
            where_filter = _build_where(wing, room)
            # Multi-wing search (knowledge + chat history): replace the single
            # wing equality with a `wing $in [...]` clause so a thin chat wing
            # can't starve retrieval of the knowledge wing. Built directly
            # (not via _build_where, which takes one wing) and combined with
            # the room clause the same way _build_where would.
            if _extra_wings:
                _all_wings = [wing] + _extra_wings
                _wing_clause = {"wing": {"$in": _all_wings}}
                if room:
                    where_filter = {"$and": [_wing_clause, {"room": room}]}
                else:
                    where_filter = _wing_clause
            kwargs = {
                "query_texts": [query],
                "n_results": fetch_n,
                "include": ["documents", "metadatas", "distances"],
            }
            if where_filter:
                kwargs["where"] = where_filter
            chroma_res = col.query(**kwargs)
            # Chroma returns lists-of-lists keyed by query; we ran one query.
            docs = (chroma_res.get("documents") or [[]])[0]
            metas = (chroma_res.get("metadatas") or [[]])[0]
            dists = (chroma_res.get("distances") or [[]])[0]
            raw = []
            for doc, meta, dist in zip(docs, metas, dists):
                meta = meta or {}
                similarity = max(0.0, 1.0 - float(dist or 0.0))
                raw.append({
                    "wing": meta.get("wing", ""),
                    "room": meta.get("room", ""),
                    "source_file": meta.get("source_file", ""),
                    "similarity": round(similarity, 3),
                    "matched_via": "chroma-vector",
                    "text": doc or "",
                })
            results = {"results": raw, "total_before_filter": len(raw)}
        except Exception as e:
            return _err(f"mempalace_query: {type(e).__name__}: {e}")
    finally:
        mempalace_activity.retrieve_end()

    if isinstance(results, dict) and results.get("error"):
        return _err(f"mempalace_query: {results.get('error')}")

    raw_results = (results or {}).get("results", [])
    if _needs_user_filter:
        own_user = f"user__{current_user_id}"
        own_teams = {f"team__{tid}" for tid in current_team_ids}
        raw_results = [r for r in raw_results
                       if isinstance(r, dict)
                       and _wing_visible(r.get("wing", ""), own_user, own_teams)]

    # Helpdesk (Brainy) source-code reach: in helpdesk_mode, ADDITIVELY search
    # the shared `brain_code` wing (the mined brain-agent source) so Brainy can
    # answer code-level questions the skill docs don't cover — semantic search
    # over the source replaces the missing GitHub code-search. This is a
    # SEPARATE Chroma query pinned to wing="brain_code"; it does NOT touch the
    # force-scope above (project__/user__ filter stays byte-identical), so the
    # project-isolation guarantee can't weaken — brain_code is a shared wing,
    # never a project's private knowledge. Only runs for Brainy, and only when
    # the main scope wasn't already brain_code (avoid double-search).
    if get_request_context().helpdesk_mode and wing != "brain_code":
        try:
            _src_where = _build_where("brain_code", None)
            _src_res = col.query(
                query_texts=[query], n_results=n_results,
                include=["documents", "metadatas", "distances"],
                where=_src_where,
            )
            _sdocs = (_src_res.get("documents") or [[]])[0]
            _smetas = (_src_res.get("metadatas") or [[]])[0]
            _sdists = (_src_res.get("distances") or [[]])[0]
            for doc, meta, dist in zip(_sdocs, _smetas, _sdists):
                meta = meta or {}
                # source_file is the on-disk clone path
                # (<palace>/.brain-source-clone/<wing>/<repo-relative>).
                # Strip the clone prefix so it's the repo-relative path Brainy
                # can drop straight into a GitHub raw URL.
                _sf = meta.get("source_file", "") or ""
                _m = re.search(r"\.brain-source-clone/[^/]+/(.+)$", _sf)
                if _m:
                    _sf = _m.group(1)
                raw_results.append({
                    "wing": meta.get("wing", "brain_code"),
                    "room": meta.get("room", ""),
                    "source_file": _sf,
                    "similarity": round(max(0.0, 1.0 - float(dist or 0.0)), 3),
                    "matched_via": "chroma-vector+source",
                    "text": doc or "",
                })
        except Exception:
            # Source reach is best-effort — never fail the user's query
            # because the brain_code wing is empty or unbuilt.
            pass

    # Filename-token boost: lexical re-rank that promotes drawers whose source
    # filename literally contains query tokens. Pure-vector retrieval scores
    # filename-matching files surprisingly low when the query is verbose
    # ("Archivierung Datensicherung Regelung bank IT-Policy" pushes
    # `ARL_4_4_Archivierung und Datensicherung.pdf` from rank 1 to outside
    # top 8 even though it's a perfect filename match — the generic Filler
    # tokens drag the embedding toward IKT-Strategie / DOR-Strategie chunks).
    # We award +0.10 per query token (≥3 chars) that appears as a word in
    # the basename, capped at +0.30. Word-boundary match avoids the German-
    # compound trap (`risk` ⊂ `Risikomanagement` would otherwise count).
    # Also bumps `matched_via` to `chroma-vector+filename` for traceability.
    try:
        # Tokenise the query the same way as a filename so a query token like
        # "Morgencheck" matches a filename containing "MorgenCheck" — and so
        # "IT-Morgencheck" splits into ["it", "morgencheck"] AND ["morgen",
        # "check"] (we keep both forms so either form on the filename side
        # matches). Also adds the original \w{3,}-tokens so multi-letter
        # German compounds aren't accidentally split.
        def _tokenise_for_match(text: str) -> set[str]:
            base = set(re.findall(r"\w{3,}", text.lower(), flags=re.UNICODE))
            cs_split = re.sub(r"(?<=[a-zäöü])(?=[A-ZÄÖÜ])", " ", text)
            sep_split = re.sub(r"[^A-Za-zÄÖÜäöüß]+", " ", cs_split).lower()
            base |= set(t for t in sep_split.split() if len(t) >= 3)
            return base

        _qtoks_boost = _tokenise_for_match(query)
        if _qtoks_boost:
            def _normalise_filename(name: str) -> set[str]:
                """Filename → set of lowercase tokens (single + adjacent-pair forms).

                We emit BOTH split tokens AND adjacent concatenations:
                  - `MorgenCheck.pdf` → {"morgen", "check", "morgencheck"}
                  - `IT_Morgen_Check_Prozessbeschreibung.pdf` → adds the pair
                    "morgencheck" so a query token "morgencheck" matches.
                Without the concat-pair the CamelCase split would dilute the
                signal — query "Morgencheck" would never match a filename that
                spelled it as `Morgen_Check`.
                """
                name = re.sub(r"\.(pdf|docx|pptx|xlsx|xlsm|eml|msg)\.md$", r".\1",
                              name, flags=re.IGNORECASE)
                name = re.sub(r"(?<=[a-zäöü])(?=[A-ZÄÖÜ])", " ", name)
                name = name.lower()
                name = re.sub(r"[^a-zäöüß]+", " ", name)
                parts = [p for p in name.split() if len(p) >= 2]
                tokens = set(p for p in parts if len(p) >= 3)
                # Adjacent-pair concatenations for compound matching
                for i in range(len(parts) - 1):
                    pair = parts[i] + parts[i + 1]
                    if len(pair) >= 3:
                        tokens.add(pair)
                # Triplet too — helps match e.g. "informationssicherheit"
                # against "Informations Sicherheit Management"-style splits.
                for i in range(len(parts) - 2):
                    tri = parts[i] + parts[i + 1] + parts[i + 2]
                    if len(tri) >= 3:
                        tokens.add(tri)
                return tokens

            for r in raw_results:
                if not isinstance(r, dict):
                    continue
                sf = (r.get("source_file") or "")
                bn = sf.split("/")[-1] if sf else ""
                if not bn:
                    continue
                fn_tokens = _normalise_filename(bn)
                if not fn_tokens:
                    continue
                # Count distinct query tokens that appear in the filename's
                # token set. Set intersection naturally dedups.
                matched = _qtoks_boost & fn_tokens
                hits = len(matched)
                if hits == 0:
                    continue
                bonus = min(0.30, hits * 0.10)
                old_sim = float(r.get("similarity") or 0.0)
                new_sim = min(1.0, old_sim + bonus)
                r["similarity"] = round(new_sim, 3)
                r["filename_boost"] = round(bonus, 3)
                r["filename_match_tokens"] = hits
                # Append to matched_via without breaking downstream "chroma-vector"
                # exact-match expectations.
                mv = r.get("matched_via") or "chroma-vector"
                if "filename" not in mv:
                    r["matched_via"] = f"{mv}+filename"
            # Re-sort by boosted similarity so dedup + downstream see the new order.
            raw_results.sort(key=lambda x: float((x or {}).get("similarity") or 0.0), reverse=True)
    except Exception:
        # Boost failures must not break the query — fall back to unboosted order.
        pass

    # Cross-encoder reranking: when enabled in config, run a multilingual
    # cross-encoder over the top-N drawer snippets. Cross-encoders read
    # (query, passage) pairs jointly and score relevance directly — much
    # more accurate than the bi-encoder vector retrieval, but only as a
    # re-ordering pass (it can't pull in drawers that vector missed).
    #
    # Skip-gate: when the top hit got a strong filename-boost (≥0.20, =
    # 2+ filename token matches), trust the lexical signal and skip the
    # reranker. Empirically the reranker scores low-content snippets
    # (frontmatter / TOC / cover page) lower than content-rich snippets
    # from less-relevant files, which can demote correct files when the
    # filename was the only reliable signal.
    try:
        _rr_cfg = (cfg.get("reranker") or {}) if isinstance(cfg, dict) else {}
        _rr_enabled = bool(_rr_cfg.get("enabled", False))
        if _rr_enabled and raw_results:
            top_boost = float((raw_results[0] or {}).get("filename_boost") or 0)
            if top_boost < 0.20:
                _rr_model = _get_reranker_model(
                    _rr_cfg.get("model", "BAAI/bge-reranker-v2-m3"),
                    _rr_cfg.get("device", "auto"),
                )
                if _rr_model is not None:
                    _rr_in = max(8, min(80, int(_rr_cfg.get("top_k_in", 40))))
                    _rr_max_chars = max(500, min(4000, int(_rr_cfg.get("max_chars_per_passage", 1500))))
                    pool = raw_results[:_rr_in]
                    pairs = [(query, (r.get("text") or "")[:_rr_max_chars]) for r in pool]
                    if pairs:
                        try:
                            scores = _rr_model.predict(
                                pairs,
                                batch_size=int(_rr_cfg.get("batch_size", 16)),
                                show_progress_bar=False,
                            )
                            for r, s in zip(pool, scores):
                                r["rerank_score"] = round(float(s), 4)
                                mv = r.get("matched_via") or "chroma-vector"
                                if "rerank" not in mv:
                                    r["matched_via"] = f"{mv}+rerank"
                            # Re-order pool by rerank_score; tail (drawers
                            # not reranked) keeps original order. Dedup
                            # downstream still sees similarity for tie-breaks.
                            pool.sort(key=lambda x: float((x or {}).get("rerank_score") or 0.0), reverse=True)
                            raw_results = pool + raw_results[_rr_in:]
                        except Exception:
                            pass
    except Exception:
        pass

    # Dedupe by (source_file, chunk_text_hash): MemPalace's searcher hydration
    # step can return the same chunk text for every hit on a closet-boosted
    # source (the keyword-best chunk is computed identically each time, since
    # query + source_file are the same). Earlier versions deduplicated by
    # source_file alone, which collapsed e.g. 19 identical-text hits down to
    # 1 — but ALSO threw away genuinely different chunks of the same file
    # that happened to rank lower. On the kg-real-policies "IT-Risk Score
    # Berechnung" query, vanilla MemPalace returns 5 distinct hits with 3 of
    # them showing 3 different chunks of the ISMS Handbuch (TOC, frontmatter,
    # and the actual section 2.13 text). Brain's old per-source dedupe kept
    # only the title-frontmatter hit and dropped the section-2.13 hit
    # entirely, since the latter had a lower closet-boosted similarity.
    #
    # Now: dedupe by (source, content-fingerprint) so we keep multiple hits
    # per file as long as they're showing DIFFERENT text. Cap per-source at
    # `max_per_source` so a doc that genuinely has many distinct relevant
    # chunks doesn't crowd out other sources entirely.
    max_per_source = 4
    seen_fingerprints: set[tuple[str, str]] = set()
    per_source_count: dict[str, int] = {}
    deduped: list[dict] = []
    # raw_results is already sorted by similarity desc by the searcher;
    # iterate in that order so the highest-sim variant wins on a fingerprint
    # collision.
    for r in raw_results:
        if not isinstance(r, dict):
            continue
        sf = (r.get("source_file") or "").strip()
        text = (r.get("text") or "").strip()
        # Fingerprint: first 200 chars of text, normalised. Cheap and stable
        # for the "identical hydration" symptom (which produces byte-identical
        # text across ranks) without false-collapsing genuinely-different
        # chunks that happen to start with the same heading.
        fp = (sf, " ".join(text[:200].split()))
        if fp in seen_fingerprints:
            continue
        if per_source_count.get(sf, 0) >= max_per_source:
            continue
        seen_fingerprints.add(fp)
        per_source_count[sf] = per_source_count.get(sf, 0) + 1
        deduped.append(r)
    # Order is already similarity-desc; nothing else to sort.

    # Substitute chunks: when the hit's text is dominated by document-title
    # repetition (frontmatter + first-page noise common in `.brain-extracted/
    # *.pdf.md` companions) but the source has other chunks with rarer query
    # terms, pull one of those instead. We look for query tokens NOT present
    # in the source filename — those are the user's real subject keywords.
    #
    # Gated via `mempalace.chunk_substitute.enabled` (default off). The pass
    # can create cite-vs-content drift: the model sees text from chunk B but
    # the drawer carries chunk A's metadata. Eval 2026-05-14 showed Brain
    # mean improves with substitution off (matches the SDK-harness baseline).
    _cs_enabled = bool(((cfg.get("chunk_substitute") or {})
                        if isinstance(cfg, dict) else {}).get("enabled", False))
    try:
        if _cs_enabled and deduped and palace_path:
            from mempalace.palace import get_collection as _gc
            _qtokens = re.findall(r"\w{3,}", query.lower(), flags=re.UNICODE)
            # German-compound-aware "rare" detection: a query token is "rare"
            # if it doesn't appear as a WORD in the filename (substring match
            # is too lax for German — `risk` ⊂ `Risikomanagement` would
            # falsely classify "risk" as already-in-filename for an "IT-Risk
            # Score" query about a `Risikomanagement_Handbuch.pdf` source).
            # Use \b word-boundary matching so compounds don't swallow short
            # tokens.
            def _word_in(token: str, text: str) -> bool:
                return re.search(r"\b" + re.escape(token) + r"\b",
                                 text, flags=re.IGNORECASE | re.UNICODE) is not None
            for hit in deduped:
                full_sf = hit.get("source_file") or ""
                hit_text = hit.get("text") or ""
                fname_lower = full_sf.lower()
                rare = [t for t in set(_qtokens)
                        if not _word_in(t, fname_lower)]
                # If no token is structurally "rare" (every query token is in
                # the filename), fall back to ALL query tokens so the
                # substitute scan still has a signal to score chunks by — the
                # query terms are ALWAYS what the user wants chunks about,
                # filename match or not.
                if not rare:
                    rare = list(set(_qtokens))
                # If the current hit text already contains a rare term as a
                # word, leave it alone — the searcher picked well.
                if any(_word_in(t, hit_text) for t in rare):
                    continue
                # Otherwise scan all chunks for this source and pick the
                # one with the highest count of rare terms.
                try:
                    _col = _gc(palace_path, create=False)
                    if _col is None:
                        continue
                    # The searcher returns basename in source_file; the
                    # actual stored value in Chroma is the absolute path.
                    # Try the most likely candidates first (project input
                    # folders + their `.brain-extracted` companions when
                    # we're in a project), avoiding the full-palace
                    # metadata scan unless we have to.
                    bn = full_sf.split("/")[-1]
                    candidate_paths = [full_sf]
                    if current_project:
                        proj_cfg2 = _brain.ProjectManager.get_project(
                            current_agent_id, current_project) or {}
                        for entry in (proj_cfg2.get("input_folders") or []):
                            root = (entry or {}).get("path", "").strip()
                            if not root:
                                continue
                            # `.brain-extracted` is where doc_convert puts
                            # the markdown companions; original folder
                            # layout is mirrored under it.
                            candidate_paths.append(
                                f"{root}/.brain-extracted/{bn}")
                            candidate_paths.append(f"{root}/{bn}")
                    src_drawers = None
                    for cand in candidate_paths:
                        try:
                            res = _col.get(
                                where={"source_file": cand},
                                include=["documents", "metadatas"])
                            if (res.get("documents") or []):
                                src_drawers = res
                                break
                        except Exception:
                            continue
                    # Last-resort wildcard: scan metadata to find the row.
                    # Only runs when input-folder candidates miss (e.g.
                    # nested subdirectory).
                    if src_drawers is None:
                        _all_meta = _col.get(include=["metadatas"])
                        candidate_full = None
                        for m in _all_meta.get("metadatas") or []:
                            sf2 = (m or {}).get("source_file") or ""
                            if sf2.endswith("/" + bn) or sf2 == bn:
                                candidate_full = sf2
                                break
                        if not candidate_full:
                            continue
                        src_drawers = _col.get(
                            where={"source_file": candidate_full},
                            include=["documents", "metadatas"])
                    docs = src_drawers.get("documents") or []
                    metas = src_drawers.get("metadatas") or []
                    if not docs:
                        continue
                    # Score each chunk by COUNT (not set membership) of
                    # rare-term occurrences — this breaks the title-frequency
                    # tie that lands every hit on chunk 0.
                    best_doc, best_score = None, 0
                    for d in docs:
                        if not isinstance(d, str):
                            continue
                        dl = d.lower()
                        s = sum(dl.count(t) for t in rare)
                        if s > best_score:
                            best_score, best_doc = s, d
                    if best_doc and best_score > 0:
                        hit["text"] = best_doc
                        hit["matched_via"] = "drawer+keyword-substitute"
                except Exception:
                    pass
    except Exception:
        pass

    # Resolve each drawer's basename `source_file` back to the absolute
    # on-disk path the miner stored in Chroma metadata. The MemPalace searcher
    # strips paths to basename (`Path(source_file).name`), so without this
    # the model only sees `policy.pdf.md` and has to guess the subfolder
    # when calling `read_document` — guesswork that has caused live
    # hallucinations on this corpus (session ba3b33b8, 2026-04-29). Build
    # one basename→full-path map per query via Chroma `where={wing: ...}`
    # so the lookup costs O(1) per drawer (one hash lookup, not one round
    # trip per drawer).
    basename_to_full: dict[str, str] = {}
    md_to_original: dict[str, str] = {}
    if drawers_to_serialize := deduped[:n_results]:
        try:
            from mempalace.palace import get_collection as _gc2
            _col2 = _gc2(palace_path, create=False)
            if _col2 is not None and wing:
                _meta = _col2.get(where={"wing": wing}, include=["metadatas"])
                for m in (_meta.get("metadatas") or []):
                    sf = ((m or {}).get("source_file") or "").strip()
                    if not sf or "/" not in sf:
                        continue
                    bn = sf.rsplit("/", 1)[-1]
                    # Keep first-wins; if multiple subdirs share a basename
                    # the caller can disambiguate from drawer text.
                    basename_to_full.setdefault(bn, sf)
                    # Map the .md companion to its original binary if the
                    # binary lives next to the .brain-extracted/ folder.
                    if "/.brain-extracted/" in sf and sf.endswith(".md"):
                        # /a/b/.brain-extracted/sub/foo.pdf.md → /a/b/sub/foo.pdf
                        without_ext = sf[:-3]  # drop .md
                        original = without_ext.replace(
                            "/.brain-extracted/", "/", 1)
                        md_to_original[sf] = original
        except Exception:
            pass

    drawers = []
    for r in deduped[:n_results]:
        if not isinstance(r, dict):
            continue
        sf_in = r.get("source_file", "") or ""
        # If the searcher already gave us a path, prefer it; else look up by
        # basename.
        if "/" in sf_in:
            full_path = sf_in
        else:
            full_path = basename_to_full.get(sf_in, sf_in)
        # Artifact drawers carry a synthetic marker `session/<sid>#artifact/
        # <name>`, not a real path — but the file DOES exist on disk under
        # agents/<agent>/artifacts/<date>_<sid>/<name>. Resolve it so the
        # snippet rule below treats it like any other readable document
        # (read_document, not a possibly-huge inline snippet). Pick the newest
        # matching folder when a session ran on several days.
        if not os.path.isfile(full_path) and "#artifact/" in sf_in:
            try:
                _sid_part, _name = sf_in.split("#artifact/", 1)
                _sid = _sid_part.split("/", 1)[-1]  # 'session/sched-76' → 'sched-76'
                _art_base = os.path.join(_brain.AGENTS_DIR, current_agent_id, "artifacts")
                _cands = sorted(
                    glob.glob(os.path.join(_art_base, f"*_{_sid}", _name)),
                    reverse=True)  # newest date folder first
                if _cands:
                    full_path = _cands[0]
            except Exception:
                pass
        original_binary = md_to_original.get(full_path, "")
        # Per-drawer snippet rule (universal — applies to EVERY caller, no
        # use-case branching): if the drawer points at a readable original
        # document on disk (read_path or read_path_original is a real file),
        # OMIT the snippet — the model MUST call read_document to get the
        # content, so it can never answer from a partial ~800-char snippet
        # (the documented hallucination cause). This now includes artifacts
        # (their synthetic marker was resolved to the real file above). If
        # there's NO readable original (chat-turn / summary / user-profile
        # drawers — source_file is a synthetic 'session/...#...' marker with
        # no file behind it), the drawer text IS the verbatim content and the
        # only source, so we KEEP the snippet (in full). The distinction is
        # purely structural (is there a file to read?), independent of who
        # called mempalace_query.
        _has_readable = bool(
            (full_path and "/" in full_path and os.path.isfile(full_path))
            or (original_binary and os.path.isfile(original_binary)))
        if _has_readable:
            _text = ""  # force read_document — no snippet to shortcut from
        else:
            # No readable original (chat / profile / artifact drawer): the
            # drawer text is the ONLY copy of the content and read_document
            # can't fetch it, so deliver it in FULL — never truncate, or the
            # model could never see the rest. Drawers are atomic ~800-char
            # chunks, but some (large artifacts, profile sections) exceed
            # that; they must still come through whole.
            _text = r.get("text") or ""
        drawers.append({
            "wing": r.get("wing", ""),
            "room": r.get("room", ""),
            "source_file": sf_in,
            # Absolute path to pass directly to `read_document(path=...)`.
            # Always populated when we could resolve; equal to source_file
            # when the path was already absolute. For .brain-extracted/.md
            # companions we ALSO surface `read_path_original` pointing at
            # the underlying PDF/DOCX/etc — read_document handles those
            # natively and gives higher-fidelity output (tables, layout).
            "read_path": full_path,
            "read_path_original": original_binary,
            "similarity": r.get("similarity"),
            "matched_via": r.get("matched_via", "drawer"),
            # Empty when a readable original exists (read_document required);
            # the verbatim snippet otherwise.
            "text": _text,
            "content_via": "read_document" if _has_readable else "snippet",
        })

    # Distinct readable sources (drawers whose text was omitted because a real
    # document exists → read_document required). These are the files the model
    # MUST load before answering; list them so it reads each, not just the top
    # one. Drawers that kept their snippet (chat/profile/artifact — no file)
    # need no read.
    _read_paths = []
    for _d in drawers:
        if _d.get("content_via") != "read_document":
            continue
        _p = _d.get("read_path") or _d.get("source_file")
        if _p and _p not in _read_paths:
            _read_paths.append(_p)
    _read_hint = (
        "Drawers with an empty `text` have `content_via:\"read_document\"` — "
        "their full content lives in a real document on disk; you MUST call "
        "`read_document(path=<drawer.read_path>)` (or `read_path_original` for "
        "the original PDF/DOCX) to read it. NEVER answer about such a drawer "
        "without reading it — the snippet was deliberately withheld so you "
        "can't answer from a partial chunk. Drawers WITH a `text` value "
        "(`content_via:\"snippet\"` — chat history, profile, artifacts) carry "
        "their verbatim content inline; no read needed. Paths are absolute, "
        "use as-is; do NOT join with input-folder paths.")
    if len(_read_paths) > 1:
        _list = "\n".join(f"  - {p}" for p in _read_paths)
        _read_hint += (
            f"\n\nThese hits span {len(_read_paths)} DISTINCT documents — "
            f"read_document EACH before summarising so the answer covers all "
            f"of them, not just the top hit:\n{_list}")

    return _ok({
        "query": query,
        "wing": wing,
        "room": room,
        "count": len(drawers),
        "total_before_filter": (results or {}).get("total_before_filter"),
        "drawers": drawers,
        # Hint to the model: every drawer has a `read_path` field that's a
        # ready-to-use absolute path for read_document — no string-joining
        # required. When >1 distinct source, also nudges to read each.
        "read_hint": _read_hint,
    })


def tool_save_chat_to_memory(args: dict) -> str:
    """Enable save_to_memory on the current session and trigger immediate sync."""
    session_id = get_request_context().current_session_id
    if not session_id:
        return _err("save_chat_to_memory: no active session")
    # The callback is installed by server.py as `brain._save_chat_to_memory_callback`
    # (rebound, not mutated in place) — read it through the brain namespace so the
    # live value is seen, not this module's stale `None` default.
    callback = getattr(_brain, "_save_chat_to_memory_callback", None)
    if callback:
        try:
            result = callback(session_id)
            return _ok(result)
        except Exception as e:
            return _err(f"save_chat_to_memory: {e}")
    return _err("save_chat_to_memory: sync callback not configured")


# Callback set by server.py to trigger immediate chat sync for a session.
# server.py assigns `brain._save_chat_to_memory_callback = ...`; the tool reads
# it via `_brain._save_chat_to_memory_callback` (not this module global) so the
# rebind is seen. Kept here for the re-export so `brain._save_chat_to_memory_callback`
# resolves to a defined name at import time (before server.py overwrites it).
_save_chat_to_memory_callback = None


# Cross-encoder reranker. Loaded on first mempalace_query when reranker.enabled
# is true; held in memory afterwards. Default model BAAI/bge-reranker-v2-m3 is
# multilingual (100+ languages incl. German), 560M params, MIT license, fits
# comfortably in a few hundred MB on Apple Silicon MPS. Loading takes ~5-8s the
# first time (incl. HF hub download on cold start), <1s on subsequent process
# starts (HF cache hit).
_reranker_lock = threading.Lock()
_reranker_cache: dict[tuple[str, str], object] = {}


def _get_reranker_model(model_id: str, device_pref: str = "auto"):
    """Return a CrossEncoder instance, loading lazily and caching by
    (model_id, resolved_device). Returns None if sentence-transformers
    isn't installed or device resolution fails — caller falls back to
    unreranked order."""
    if not model_id:
        return None
    # Resolve device preference. "auto" → mps on Apple Silicon, cuda on
    # NVIDIA, cpu otherwise. Caller can pin via config.
    if device_pref == "auto":
        try:
            import torch
            if torch.backends.mps.is_available():
                device = "mps"
            elif torch.cuda.is_available():
                device = "cuda"
            else:
                device = "cpu"
        except Exception:
            device = "cpu"
    else:
        device = device_pref
    key = (model_id, device)
    with _reranker_lock:
        m = _reranker_cache.get(key)
        if m is not None:
            return m
        # Make sure the mempalace venv site-packages is on sys.path so we
        # find sentence_transformers / torch (we install it there to keep
        # all heavy ML deps in one place).
        ok, _err_msg = _ensure_mempalace_importable()
        if not ok:
            return None
        try:
            from sentence_transformers import CrossEncoder
        except Exception:
            return None
        try:
            t0 = time.time()
            m = CrossEncoder(model_id, device=device, max_length=512)
            try:
                logging.info(f"[reranker] loaded {model_id} on {device} in {time.time()-t0:.1f}s")
            except Exception:
                pass
            _reranker_cache[key] = m
            return m
        except Exception as e:
            try:
                logging.warning(f"[reranker] failed to load {model_id} on {device}: {e}")
            except Exception:
                pass
            return None


# ─────────────────────────────────────────────────────────────────────────────
# E4 additions — memory tools + project-KG tools (folded in here per the
# refactor plan: the memory + KG tools belong with the MemPalace glue).
#
#   - tool_memory_store / tool_memory_recall / tool_memory_delete /
#     tool_memory_shared — the legacy file-backed MemoryStore tools, plus the
#     private `_graph_expand_results` helper (follows `related` frontmatter
#     links one hop). The MemoryStore class + `_get_memory_store` /
#     `_parse_frontmatter` / `_record_recall_cooccurrence` /
#     `_get_agent_team_info` / `trigger_memory_summary_refresh` / `AgentConfig`
#     / `AGENTS_DIR` STAY in brain — reached via `_brain.`.
#   - tool_mempalace_kg_query / tool_mempalace_kg_search /
#     tool_mempalace_kg_neighbors — the project-scoped KG tools, plus their
#     private helper cluster (`_kg_resolve_project_scope`, `_kg_open`,
#     `_kg_source_in_scope`, `_kg_has_adapter_column`, `_kg_has_span_column`).
#     These reach `_load_mempalace_config` / `_ensure_mempalace_importable`
#     (defined above in this module) and `_brain.ProjectManager` lazily.
#
# Pure relocation — JSON envelopes + error strings byte-identical to pre-E4.
# brain.py re-exports all 7 tools via `from engine.mempalace_glue import (...)`.
# ─────────────────────────────────────────────────────────────────────────────


def _graph_expand_results(results: list[dict], base_dir: str, ingest_dir: str,
                          max_hops: int = 1) -> list[dict]:
    """Follow 'related' frontmatter links from matched results for context expansion."""
    seen_files = {r.get("file_path", "") for r in results}
    expanded = list(results)
    frontier = list(results)
    for _hop in range(max_hops):
        next_frontier = []
        for r in frontier:
            fpath = r.get("file_path", "")
            if not fpath or not os.path.exists(fpath):
                continue
            try:
                with open(fpath, "r") as f:
                    raw = f.read(2000)
                fm, _ = _brain._parse_frontmatter(raw)
            except Exception:
                continue
            # Parse related field (simple YAML list parsing)
            related_raw = fm.get("related", "")
            if not related_raw:
                continue
            # related is stored as multi-line YAML in frontmatter, parse linked files
            related_files = re.findall(r'file:\s*(\S+\.md)', raw)
            for rel_file in related_files:
                # Try ingest_dir first, then base_dir
                for search_dir in (ingest_dir, base_dir):
                    rel_path = os.path.join(search_dir, rel_file)
                    if rel_path in seen_files or not os.path.exists(rel_path):
                        continue
                    seen_files.add(rel_path)
                    try:
                        with open(rel_path, "r") as f:
                            rel_raw = f.read()
                        rel_fm, rel_body = _brain._parse_frontmatter(rel_raw)
                        mem = {
                            "id": hashlib.sha256(rel_fm.get("name", rel_file).encode()).hexdigest()[:12],
                            "name": rel_fm.get("name", rel_fm.get("title", rel_file.replace(".md", ""))),
                            "description": rel_fm.get("description", ""),
                            "type": rel_fm.get("type", "general"),
                            "content": rel_body,
                            "file_path": rel_path,
                            "score": max(0, (r.get("score", 0.5) - 0.2)),
                            "source_scope": "related",
                        }
                        expanded.append(mem)
                        next_frontier.append(mem)
                    except Exception:
                        continue
                    break  # found in one dir, skip the other
        frontier = next_frontier
    return expanded


def tool_memory_store(args: dict) -> str:
    """Store a memory. When a project is active, writes to project directory."""
    ms = _brain._get_memory_store()
    if not ms:
        return _err("Memory store not initialized")
    name = args.get("name", "")
    content = args.get("content", "")
    description = args.get("description", "")
    mem_type = args.get("type", "general")
    if not name or not content:
        return _err("memory_store: name and content are required")
    # If project is active, store in project directory
    project = get_request_context().project
    if project:
        agent_id = ms.agent_id
        proj_dir = os.path.join(_brain.AGENTS_DIR, agent_id, "projects", project)
        if os.path.isdir(proj_dir):
            proj_store = _brain.MemoryStore(agent_id=f"{agent_id}/{project}", base_dir=proj_dir)
            result = proj_store.store(name, content, description, mem_type)
            result["project"] = project
            return _ok(result)
    result = ms.store(name, content, description, mem_type)
    # Trigger near-term memory summary refresh when user-facing memories are stored
    # (skip if this IS the memory summary being written)
    if name != "Memory Summary" and mem_type in ("user", "feedback", "project"):
        try:
            agent_id = ms.agent_id if hasattr(ms, 'agent_id') else "main"
            _brain.trigger_memory_summary_refresh(agent_id)
        except Exception:
            pass
    return _ok(result)


def tool_memory_recall(args: dict) -> str:
    """Recall memories by searching. When a project is active, searches project first."""
    ms = _brain._get_memory_store()
    if not ms:
        return _err("Memory store not initialized")
    query = args.get("query", "")
    limit = args.get("limit", 10)
    mem_type = args.get("type")
    mode = args.get("mode", "")

    # Project-scoped search: search project collection first, then agent
    project = get_request_context().project
    if project and query:
        agent_id = ms.agent_id
        proj_dir = os.path.join(_brain.AGENTS_DIR, agent_id, "projects", project)
        if os.path.isdir(proj_dir):
            proj_store = _brain.MemoryStore(agent_id=f"{agent_id}/{project}", base_dir=proj_dir)
            # Also search ingested subdir
            ingest_dir = os.path.join(proj_dir, "ingested")
            proj_results = proj_store.recall(query, limit, mem_type)
            # Tag project results
            for r in proj_results:
                r["source_scope"] = "project"
            # Then agent-level results
            agent_results = ms.recall(query, max(2, limit - len(proj_results)), mem_type)
            for r in agent_results:
                r["source_scope"] = "agent"
            results = proj_results + agent_results
            # Always expand via graph relationships (follow related links 1 hop)
            if results:
                results = _graph_expand_results(results, proj_dir, ingest_dir,
                                                max_hops=2 if mode == "graph" else 1)
            for r in results:
                if r.get("content") and len(r["content"]) > 4000:
                    r["content"] = r["content"][:4000] + "..."
            return _ok({"query": query, "project": project, "results": results[:limit], "count": len(results[:limit])})

    if not query:
        results = ms.list_all(mem_type)
        return _ok({"query": "", "results": results, "count": len(results)})
    results = ms.recall(query, limit, mem_type)

    # Always expand via graph relationships (1 hop default, 2 hops for explicit graph mode)
    if results:
        agent_id = ms.agent_id
        agent_dir = os.path.join(_brain.AGENTS_DIR, agent_id)
        ingest_dir = os.path.join(agent_dir, "ingested")
        results = _graph_expand_results(results, agent_dir, ingest_dir,
                                        max_hops=2 if mode == "graph" else 1)

    for r in results:
        if r.get("content") and len(r["content"]) > 4000:
            r["content"] = r["content"][:4000] + "..."

    # --- Co-recall tracking (Mechanism 3) ---
    if query and len(results) >= 2:
        try:
            result_files = [os.path.basename(r.get("file_path", "")) for r in results if r.get("file_path")]
            agent_id = ms.agent_id
            agent_dir = os.path.join(_brain.AGENTS_DIR, agent_id)
            threading.Thread(
                target=_brain._record_recall_cooccurrence,
                args=(result_files, agent_id, agent_dir),
                daemon=True,
            ).start()
        except Exception:
            pass  # Co-recall tracking is best-effort

    return _ok({"query": query, "results": results, "count": len(results)})


def tool_memory_delete(args: dict) -> str:
    """Delete a memory."""
    ms = _brain._get_memory_store()
    if not ms:
        return _err("Memory store not initialized")
    name = args.get("name", "")
    if not name:
        return _err("memory_delete: name is required")
    result = ms.delete(name)
    return _ok(result)


def tool_memory_shared(args: dict) -> str:
    """Access shared memory — global (main) or team (team head) scope."""
    action = args.get("action", "recall")
    scope = args.get("scope", "global")

    # Determine which agent's memory to use
    if scope == "team":
        # Find the team head for the calling agent
        caller_id = get_request_context().delegate_agent_id
        if not caller_id:
            agent = get_request_context().current_agent or _brain._current_agent
            caller_id = agent.agent_id if agent else "main"
        team_info = _brain._get_agent_team_info(caller_id)
        if not team_info:
            return _err("memory_shared: agent is not in any team — use scope='global' instead")
        team_head_id = team_info["head"]
        target_agent = _brain.AgentConfig(team_head_id)
        source_label = f"{team_info['name']} (team)"
    else:
        target_agent = _brain.AgentConfig("main")
        source_label = "main (shared)"

    shared_store = _brain.MemoryStore(agent_id=target_agent.agent_id, base_dir=target_agent.memory_dir)

    if action == "store":
        name = args.get("name", "")
        content = args.get("content", "")
        description = args.get("description", "")
        mem_type = args.get("type", "general")
        if not name or not content:
            return _err("memory_shared store: name and content are required")
        result = shared_store.store(name, content, description, mem_type)
        result["source"] = source_label
        return _ok(result)
    else:  # recall
        query = args.get("query", "")
        limit = args.get("limit", 10)
        mem_type = args.get("type")
        if not query:
            results = shared_store.list_all(mem_type)
        else:
            results = shared_store.recall(query, limit, mem_type)
            # Graph expansion on shared memory too
            if results:
                shared_dir = os.path.join(_brain.AGENTS_DIR, target_agent.agent_id)
                shared_ingest = os.path.join(shared_dir, "ingested")
                results = _graph_expand_results(results, shared_dir, shared_ingest, max_hops=1)
            for r in results:
                if r.get("content") and len(r["content"]) > 4000:
                    r["content"] = r["content"][:4000] + "..."
        return _ok({"query": query, "source": source_label, "results": results[:limit], "count": len(results[:limit])})


# ─── Project Knowledge-Graph tools + helper cluster ──────────────────────────

def _kg_resolve_project_scope() -> tuple[str, list[str], str | None]:
    """Return (palace_path, source_prefixes, error_msg) for the current
    project, or ("",[], "<reason>") if scoping fails. source_prefixes is
    the union of (project_dir, every input_folder.path) — drawers carrying
    any of these prefixes belong to this project.
    """
    cfg = _load_mempalace_config()
    if not cfg.get("enabled", True):
        return "", [], "mempalace: disabled in config.json"
    if not (cfg.get("kg") or {}).get("enabled", True):
        return "", [], (
            "mempalace_kg: knowledge-graph extraction is disabled in "
            "config.json (mempalace.kg.enabled=false). Use mempalace_query "
            "to retrieve drawers and read_document on the source files for "
            "verbatim policy text instead.")
    palace_path = cfg.get("palace_path", "")
    if not palace_path or not os.path.isdir(palace_path):
        return "", [], f"mempalace: palace_path missing: {palace_path}"

    current_project = get_request_context().project or ""
    if not current_project:
        return "", [], (
            "mempalace_kg: this tool requires a project context. "
            "Step 1 supports only project-scoped queries.")

    _ag = get_request_context().current_agent
    current_agent_id = getattr(_ag, "agent_id", None) or (
        _ag if isinstance(_ag, str) else "main") or "main"
    proj_cfg = _brain.ProjectManager.get_project(current_agent_id, current_project)
    if not proj_cfg:
        return "", [], f"project not found: {current_project}"
    pid = proj_cfg.get("id") or ""
    if not pid:
        return "", [], "project has no id (run a sync first)"

    pdir = proj_cfg.get("dir") or ""
    prefixes: list[str] = []
    def _norm(p: str) -> str:
        # Resolve symlinks so the prefix filter matches what the miner stored
        # in drawer source_file (macOS /tmp → /private/tmp, etc.).
        try:
            r = os.path.realpath(p)
        except OSError:
            r = p
        if r and not r.endswith(os.sep):
            r += os.sep
        return r
    if pdir:
        prefixes.append(_norm(pdir))
    for entry in (proj_cfg.get("input_folders") or []):
        fp = (entry.get("path") or "").strip()
        if fp:
            prefixes.append(_norm(fp))
    if not prefixes:
        return "", [], "project has no input folders or attachments to scope by"
    return palace_path, prefixes, None


def _kg_open(palace_path: str):
    """Lazy-open the KG. Returns (kg, error_msg)."""
    ok, err = _ensure_mempalace_importable()
    if not ok:
        return None, err
    try:
        from mempalace.knowledge_graph import KnowledgeGraph
    except Exception as e:
        return None, f"import KnowledgeGraph: {type(e).__name__}: {e}"
    kg_path = os.path.join(palace_path, "knowledge_graph.sqlite3")
    if not os.path.isfile(kg_path):
        return None, "knowledge_graph.sqlite3 not yet created (no extractions run)"
    try:
        return KnowledgeGraph(db_path=kg_path), None
    except Exception as e:
        return None, f"open KG: {type(e).__name__}: {e}"


def _kg_source_in_scope(source_file: str, prefixes: list[str]) -> bool:
    if not source_file:
        return False
    return any(source_file.startswith(p) for p in prefixes)


def _kg_has_adapter_column(palace_path: str) -> bool:
    """Cheap one-time PRAGMA check for adapter_name. Cached on the module."""
    cache_key = f"_kg_adapter_col_{palace_path}"
    cached = globals().get(cache_key)
    if cached is not None:
        return cached
    import sqlite3 as _sql
    kg_path = os.path.join(palace_path, "knowledge_graph.sqlite3")
    if not os.path.isfile(kg_path):
        return False
    try:
        c = _sql.connect(kg_path, timeout=5, check_same_thread=False)
        try:
            cols = {r[1] for r in c.execute("PRAGMA table_info(triples)")}
        finally:
            c.close()
        out = "adapter_name" in cols
    except Exception:
        out = False
    globals()[cache_key] = out
    return out


def _kg_has_span_column(palace_path: str) -> bool:
    """Cheap one-time PRAGMA check for span column. Cached on the module."""
    cache_key = f"_kg_span_col_{palace_path}"
    cached = globals().get(cache_key)
    if cached is not None:
        return cached
    import sqlite3 as _sql
    kg_path = os.path.join(palace_path, "knowledge_graph.sqlite3")
    if not os.path.isfile(kg_path):
        return False
    try:
        c = _sql.connect(kg_path, timeout=5, check_same_thread=False)
        try:
            cols = {r[1] for r in c.execute("PRAGMA table_info(triples)")}
        finally:
            c.close()
        out = "span" in cols
    except Exception:
        out = False
    globals()[cache_key] = out
    return out


# The KG `span` is a ≤200-char verbatim quote stored per triple. It reads like
# an answer-ready citation, which lured the model into answering straight from
# the KG tool result without ever calling read_document — yielding weak/absent
# citations and, worse, answers built on the WRONG document (eval P2=0.45,
# C2=0.38 used ONLY kg_search, never read_document). So the KG tools now return
# the triple (subject/predicate/object/source_file/confidence) WITHOUT span:
# the triple points at a fact + its source document; to quote or answer
# precisely the model MUST read_document the source_file. Same structural rule
# as mempalace_query dropping the snippet for on-disk drawers.
def _kg_strip_span(t: dict) -> dict:
    if isinstance(t, dict) and "span" in t:
        t = {k: v for k, v in t.items() if k != "span"}
    return t

_KG_READ_HINT = (
    "Triples state a fact + its `source_file` — they are pointers, NOT quotable "
    "text (the verbatim span is intentionally omitted). To quote, give exact "
    "figures, or answer precisely, you MUST `read_document(path=<source_file>)` "
    "the underlying document. Never answer a content question from triples "
    "alone; they only tell you WHICH document to read.")


def tool_mempalace_kg_query(args: dict) -> str:
    """Entity-first KG lookup, scoped to the caller's current project."""
    palace_path, prefixes, err = _kg_resolve_project_scope()
    if err:
        return _err(err)
    entity = (args.get("entity") or "").strip()
    if not entity:
        return _err("mempalace_kg_query: 'entity' is required")
    direction = (args.get("direction") or "outgoing").strip().lower()
    if direction not in {"outgoing", "incoming", "both"}:
        direction = "outgoing"
    as_of = (args.get("as_of") or "").strip() or None

    kg, err = _kg_open(palace_path)
    if err or kg is None:
        return _err(err or "kg unavailable")
    try:
        triples = kg.query_entity(entity, as_of=as_of, direction=direction) or []
    except Exception as e:
        return _err(f"kg.query_entity: {type(e).__name__}: {e}")
    finally:
        try: kg.close()
        except Exception: pass

    # Post-filter by project source prefix. Only triples whose source_file
    # falls under one of the project's known prefixes are returned.
    in_scope = []
    for t in triples:
        if not isinstance(t, dict):
            continue
        sf = t.get("source_file", "") or ""
        if _kg_source_in_scope(sf, prefixes):
            in_scope.append(t)
    return _ok({
        "entity": entity,
        "direction": direction,
        "as_of": as_of,
        "count": len(in_scope),
        "total_before_scope_filter": len(triples),
        "triples": [_kg_strip_span(t) for t in in_scope[:200]],
        "read_hint": _KG_READ_HINT,
    })


def tool_mempalace_kg_search(args: dict) -> str:
    """Find triples in the project KG.

    Two modes — pick by intent:

    1) **Structured mode** (`predicate` set): exact predicate match, optionally
       narrowed by `subject_contains` / `object_contains`. Use this for
       contradiction- and coverage-detection: 'every requires triple about
       retention', 'every cites triple referencing GDPR'.

    2) **Free-text mode** (`query` set, no `predicate`): substring match across
       subject OR predicate OR object. Use this when you don't know which
       predicate to ask for and just want any triple mentioning a topic.

    Either `predicate` or `query` must be set. Both can be combined: when
    `predicate` is set, `query` is ignored.
    """
    palace_path, prefixes, err = _kg_resolve_project_scope()
    if err:
        return _err(err)
    predicate = (args.get("predicate") or "").strip().lower().replace(" ", "_")
    free_query = (args.get("query") or "").strip()
    if not predicate and not free_query:
        return _err(
            "mempalace_kg_search: one of 'predicate' (structured mode) or "
            "'query' (free-text substring mode) is required")
    subj_q = (args.get("subject_contains") or "").strip().lower()
    obj_q = (args.get("object_contains") or "").strip().lower()
    try:
        limit = max(1, min(200, int(args.get("limit") or 25)))
    except (TypeError, ValueError):
        limit = 25

    ok, err_imp = _ensure_mempalace_importable()
    if not ok:
        return _err(err_imp)

    kg_path = os.path.join(palace_path, "knowledge_graph.sqlite3")
    if not os.path.isfile(kg_path):
        return _err("knowledge_graph.sqlite3 not yet created")

    import sqlite3 as _sql
    has_adapter = _kg_has_adapter_column(palace_path)
    has_span = _kg_has_span_column(palace_path)
    conn = _sql.connect(kg_path, timeout=5, check_same_thread=False)
    conn.row_factory = _sql.Row
    try:
        # Build source_file scope filter from the project's prefixes.
        scope_clause = " OR ".join(["source_file LIKE ? || '%'"] * len(prefixes))
        sql_head = (
            "SELECT t.subject AS sub_id, e1.name AS sub_name, "
            "       t.predicate, "
            "       t.object AS obj_id, e2.name AS obj_name, "
            "       t.confidence, t.source_file, "
            f"       {'t.source_drawer_id' if has_adapter else 'NULL'} AS source_drawer_id, "
            f"       {'t.adapter_name' if has_adapter else 'NULL'} AS adapter_name, "
            f"       {'t.span' if has_span else 'NULL'} AS span, "
            "       t.valid_from, t.valid_to "
            "FROM triples t "
            "LEFT JOIN entities e1 ON t.subject = e1.id "
            "LEFT JOIN entities e2 ON t.object = e2.id "
        )
        if predicate:
            # Structured mode — exact predicate match.
            sql = sql_head + (
                f"WHERE t.predicate = ? AND ({scope_clause}) "
                "AND t.valid_to IS NULL "
            )
            params: list = [predicate] + list(prefixes)
            if subj_q:
                sql += " AND LOWER(e1.name) LIKE ? "
                params.append(f"%{subj_q}%")
            if obj_q:
                sql += " AND LOWER(e2.name) LIKE ? "
                params.append(f"%{obj_q}%")
        else:
            # Free-text mode — substring across subject_name / predicate / object_name.
            # COALESCE so entity-table absence (rows where t.subject is the literal
            # string itself rather than an entity id) still scans.
            like = f"%{free_query.lower()}%"
            sql = sql_head + (
                f"WHERE ({scope_clause}) AND t.valid_to IS NULL "
                "AND (LOWER(COALESCE(e1.name, t.subject)) LIKE ? "
                "     OR LOWER(t.predicate) LIKE ? "
                "     OR LOWER(COALESCE(e2.name, t.object)) LIKE ?) "
            )
            params = list(prefixes) + [like, like, like]
        sql += " ORDER BY t.confidence DESC, t.extracted_at DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()

    triples = []
    for r in rows:
        triples.append({
            "subject": r["sub_name"] or r["sub_id"],
            "predicate": r["predicate"],
            "object": r["obj_name"] or r["obj_id"],
            "confidence": r["confidence"],
            "source_file": r["source_file"] or "",
            "source_drawer_id": r["source_drawer_id"] or "",
            # span intentionally omitted — see _KG_READ_HINT.
            "valid_from": r["valid_from"] or "",
        })
    return _ok({
        "mode": "structured" if predicate else "free_text",
        "predicate": predicate or None,
        "query": free_query or None,
        "subject_contains": subj_q or None,
        "object_contains": obj_q or None,
        "count": len(triples),
        "triples": triples,
        "read_hint": _KG_READ_HINT,
    })


def tool_mempalace_kg_neighbors(args: dict) -> str:
    """BFS in the project's KG. Returns reachable entities + connecting triples."""
    palace_path, prefixes, err = _kg_resolve_project_scope()
    if err:
        return _err(err)
    entity = (args.get("entity") or "").strip()
    if not entity:
        return _err("mempalace_kg_neighbors: 'entity' is required")
    try:
        depth = max(1, min(3, int(args.get("depth") or 1)))
    except (TypeError, ValueError):
        depth = 1
    pred_filter = (args.get("predicate") or "").strip().lower().replace(" ", "_") or None

    kg, err = _kg_open(palace_path)
    if err or kg is None:
        return _err(err or "kg unavailable")

    visited: set[str] = set()
    frontier: list[str] = [entity]
    edges: list[dict] = []
    try:
        for hop in range(depth):
            next_frontier: list[str] = []
            for ent in frontier:
                if ent in visited:
                    continue
                visited.add(ent)
                try:
                    triples = kg.query_entity(ent, direction="both") or []
                except Exception:
                    triples = []
                for t in triples:
                    if not isinstance(t, dict):
                        continue
                    if not _kg_source_in_scope(t.get("source_file", "") or "",
                                                prefixes):
                        continue
                    if pred_filter and t.get("predicate") != pred_filter:
                        continue
                    edges.append({
                        "subject": t.get("subject", ""),
                        "predicate": t.get("predicate", ""),
                        "object": t.get("object", ""),
                        "confidence": t.get("confidence"),
                        "source_file": t.get("source_file", "") or "",
                        # span intentionally omitted — see _KG_READ_HINT.
                        "hop": hop + 1,
                    })
                    other = t.get("object") if t.get("subject") == ent \
                            else t.get("subject")
                    if other and other not in visited:
                        next_frontier.append(other)
            frontier = next_frontier
            if not frontier:
                break
    finally:
        try: kg.close()
        except Exception: pass

    return _ok({
        "entity": entity,
        "depth": depth,
        "predicate_filter": pred_filter,
        "entities_reached": sorted(visited),
        "edge_count": len(edges),
        "edges": edges[:300],
        "read_hint": _KG_READ_HINT,
    })
