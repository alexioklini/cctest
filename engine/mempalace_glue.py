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

import json
import logging
import os
import re
import sys
import threading
import time

from engine.context import _thread_local
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
    current_user_id = getattr(_thread_local, "current_user_id", "") or ""
    current_team_ids = list(getattr(_thread_local, "current_team_ids", []) or [])
    current_project = getattr(_thread_local, "project", None) or ""
    _ag = getattr(_thread_local, "current_agent", None)
    # _thread_local.current_agent is an AgentConfig instance (not a string).
    current_agent_id = getattr(_ag, "agent_id", None) or (
        _ag if isinstance(_ag, str) else "main") or "main"
    project_pinned = False
    # Optional: when project-pinned, the model can ask explicitly for
    # past chat memory in this project by setting include_chat_history=true.
    # Default behaviour pins to the project KNOWLEDGE wing only, so wrong
    # answers in past chats can't outrank the underlying source documents.
    include_chat_history = bool(args.get("include_chat_history") or False)
    if current_project:
        # Resolve project name → id (uuid hex). Without an id we refuse to
        # search rather than leak across projects.
        proj_cfg = _brain.ProjectManager.get_project(current_agent_id, current_project)
        proj_id = (proj_cfg or {}).get("id") or ""
        if proj_id:
            safe_pid = re.sub(r"[^A-Za-z0-9_.-]", "_", proj_id)
            wing = (f"project_chat__{safe_pid}" if include_chat_history
                    else f"project__{safe_pid}")
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
        original_binary = md_to_original.get(full_path, "")
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
            "text": (r.get("text") or "")[:2000],
        })

    return _ok({
        "query": query,
        "wing": wing,
        "room": room,
        "count": len(drawers),
        "total_before_filter": (results or {}).get("total_before_filter"),
        "drawers": drawers,
        # Hint to the model: every drawer has a `read_path` field that's a
        # ready-to-use absolute path for read_document — no string-joining
        # required.
        "read_hint": (
            "To follow up on a drawer, call "
            "`read_document(path=<drawer.read_path>)` — or "
            "`read_document(path=<drawer.read_path_original>)` for the "
            "original PDF/DOCX/etc. if you need formula/table fidelity. "
            "Both paths are absolute and ready to use as-is; do NOT join "
            "with input-folder paths."),
    })


def tool_save_chat_to_memory(args: dict) -> str:
    """Enable save_to_memory on the current session and trigger immediate sync."""
    session_id = getattr(_thread_local, "current_session_id", None)
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
