# Deep Research — the bounded agentic loop (marquee feature).
#
# topic → decompose into sub-questions (LLM) → multi-search (searxng/exa) →
# fetch+read top candidates (web_fetch/crawl4ai) → rank+select (LLM) →
# grounded cited synthesis (LLM, research-mode discipline) → save the report as a
# project_outputs row (kind=research_report, via output_gen.save_report_output so
# Studio browses it identically) → propose the curated source set for approval.
#
# DESIGN (CLAUDE.md rule 5 — model only for judgment calls): the loop is
# DETERMINISTIC orchestration in code. The LLM is used at exactly three judgment
# points (decompose, select-from-candidates, synthesize). Search/fetch/dedup/
# budget accounting are plain code. Bounded + visible budget (W8 — stop + note,
# never silent). All LLM calls route through gdpr_pick_model_for_background (E5).
# Cooperative cancel via the research_runs.cancel flag (E3).

import concurrent.futures
import contextvars
import json
import re
import threading
import uuid

import brain as _brain
from engine import output_gen
from server_lib.db import ChatDB

# Budget defaults (server-side, generous per spec §8.1: ~60 fetches / ~80k tok /
# ~4 min). Surfaced live in the UI — never silent. Every breadth/cost knob below
# is overridable from config.json → research.* (read via _research_int at the use
# site); these remain the DEFAULTS, so behavior is unchanged unless configured.
#   research.{fetches,tokens,rounds}   — per-run budget (DEFAULT_BUDGET)
#   research.max_subqueries            — sub-question ceiling (_MAX_SUBQUERIES)
#   research.results_per_query         — candidates per sub-question search
DEFAULT_BUDGET = {"fetches": 60, "tokens": 80000, "rounds": 8}
_MAX_SUBQUERIES = 8        # default; override via research.max_subqueries
_RESULTS_PER_QUERY = 8     # default; override via research.results_per_query
_FETCH_MAX_LEN = 12000          # per-source markdown cap fed to synthesis
_MIN_USABLE_CONTENT = 200       # chars; below this a fetch is treated as failed
_CHARS_PER_TOKEN = 3.5          # rough corpus-size → token estimate (conservative)
_SYNTH_OVERHEAD_TOKENS = 8000   # reserve for discipline/prompt/reply headroom


def _fit_corpus(sources, token_budget):
    """Pack RANK-ORDERED sources into the synthesis corpus until the token
    budget is spent, so the prompt never overflows the model's context (an
    overflowed prompt silently returns an EMPTY completion → 'Empty report.').

    `sources` is already best-first (from _select_sources). Returns
    (kept_sources, n_dropped). At least the top source is always kept (trimmed
    to fit if it alone exceeds the budget) so a report is still produced."""
    char_budget = int(max(1, token_budget - _SYNTH_OVERHEAD_TOKENS) * _CHARS_PER_TOKEN)
    kept, used = [], 0
    for i, s in enumerate(sources):
        clen = len(s.get("content") or "")
        if i == 0 and clen > char_budget:
            # Top source alone is too big — trim it rather than drop everything.
            s = {**s, "content": s["content"][:char_budget]}
            kept.append(s)
            used = char_budget
            continue
        if used + clen > char_budget:
            break
        kept.append(s)
        used += clen
    return kept, len(sources) - len(kept)

# I/O concurrency for the search + fetch phases. The fetch cap is the main
# protection for the crawl4ai render service (an uncapped ThreadingHTTPServer) —
# keep it modest on a single Mac box; raise on the Spark move. Searches hit the
# one search backend, so a low cap respects its rate limits. config.json →
# research.{fetch_workers,search_workers} overrides these; see _io_workers().
_DEFAULT_FETCH_WORKERS = 4
_DEFAULT_SEARCH_WORKERS = 4


def _io_workers(key: str, default: int) -> int:
    """Read research.<key> from server config, clamped to [1, 16]. Falls back to
    the default on any missing/garbage value (never lets config widen past 16)."""
    return _research_int(key, default, 1, 16)


def _research_int(key: str, default: int, lo: int, hi: int) -> int:
    """Read an integer research.<key> from server config, clamped to [lo, hi].
    Falls back to `default` on any missing/garbage value. The single config
    reader for every Deep Research breadth/cost knob — keeps all the limits
    overridable from config.json without changing the (unchanged) defaults."""
    try:
        v = int((_brain._server_config().get("research") or {}).get(key, default))
    except (TypeError, ValueError, AttributeError):
        return default
    return max(lo, min(hi, v))

_LOW_TRUST = ("reddit.com", "quora.com", "medium.com", "facebook.com",
              "twitter.com", "x.com", "pinterest.com")


def _norm_url(u: str) -> str:
    """Normalise for dedup vs web_urls: drop scheme, www, trailing slash, query."""
    u = (u or "").strip().lower()
    u = re.sub(r"^https?://", "", u)
    u = re.sub(r"^www\.", "", u)
    u = u.split("?")[0].split("#")[0].rstrip("/")
    return u


def _trust_hint(url: str) -> str:
    host = _norm_url(url).split("/")[0]
    return "blog/forum" if any(host.endswith(d) for d in _LOW_TRUST) else ""


def _exa_available() -> bool:
    # Same gate as the chat agent: the admin's tool_settings.exa_search.enabled
    # toggle wins, AND a key must be configured. A disabled tool is never a
    # Research backend even if a key exists.
    if not _brain._global_tool_enabled("exa_search"):
        return False
    try:
        import os
        cfg = _brain.get_tool_config().get("exa_search", {})
        return bool((cfg.get("api_key") or os.environ.get("EXA_API_KEY", "")).strip())
    except Exception:
        return False


def _searxng_available() -> bool:
    if not _brain._global_tool_enabled("searxng_search"):
        return False
    try:
        return bool(_brain._searxng_base_url())
    except Exception:
        return False


def active_backend() -> str:
    """THE single search backend Research uses, or "" if none is available.

    The admin enables exactly one search tool (exa_search OR searxng_search) in
    Settings → Tools — exactly like web search in chat. Research calls whichever
    is enabled (+ configured). There is no merge and no per-run choice: enabling
    both is a config problem the admin owns (same as the chat agent, which would
    then carry both tools). searxng wins the tiebreak if somehow both are on."""
    if _searxng_available():
        return "searxng"
    if _exa_available():
        return "exa"
    return ""


def _run_search(query):
    """Run THE active search backend for one query. Returns
    [{title, link, snippet, score}], deduped by normalised URL. Empty on failure
    or when no backend is active (E2 degrades to no results for that query)."""
    backend = active_backend()
    if not backend:
        return []
    n = _research_int("results_per_query", _RESULTS_PER_QUERY, 1, 50)
    try:
        if backend == "searxng":
            raw = _brain.tool_searxng_search({"query": query, "num_results": n})
        else:  # exa
            raw = _brain.exa_search(query, num_results=n)
        data = json.loads(raw)
    except Exception:
        return []
    merged = {}
    for r in (data.get("results") or []):
        link = r.get("link") or r.get("url") or ""
        if not link:
            continue
        key = _norm_url(link)
        if key and key not in merged:
            merged[key] = {"title": r.get("title", ""), "link": link,
                           "snippet": r.get("snippet", ""), "score": r.get("score", 0)}
    return list(merged.values())


def _bg_text(messages, system_prompt, model, project_name, agent_id, user_id, purpose, max_tokens=None):
    """One grounded background LLM call, GDPR-gated (E5). Returns (reply, err)."""
    safe_model, [sys_safe, msg_safe], deanon = _brain.gdpr_pick_model_for_background(
        model, [system_prompt, messages], purpose=purpose)
    from handlers import sidecar_proxy
    res = sidecar_proxy.background_call(
        messages=[{"role": "user", "content": msg_safe}],
        model=safe_model, system_prompt=sys_safe, purpose=purpose,
        agent_id=agent_id, project=project_name, user_id=user_id,
        max_rounds=1, max_tokens=max_tokens)
    if res.get("error"):
        return "", str(res["error"])
    return deanon((res.get("reply") or "").strip()), ""


# ─── Judgment point 1: decompose the topic into sub-questions ──────────────

def _decompose(topic, model, project_name, agent_id, user_id):
    max_subq = _research_int("max_subqueries", _MAX_SUBQUERIES, 1, 32)
    sys = ("You are a research planner. Break the user's topic into focused, "
           "distinct sub-questions that together cover it. Output ONLY a JSON "
           "array of strings (the sub-questions), nothing else. "
           f"Return between 3 and {max_subq} sub-questions.")
    reply, err = _bg_text(f"Topic: {topic}", sys, model, project_name, agent_id, user_id,
                          purpose="transform", max_tokens=600)
    if err:
        return [topic]  # degrade: search the raw topic
    subs = _extract_json_list(reply)
    subs = [s.strip() for s in subs if isinstance(s, str) and s.strip()]
    return subs[:max_subq] or [topic]


def _extract_json_list(text):
    """Pull the first JSON array out of an LLM reply (tolerates prose around it)."""
    m = re.search(r"\[.*\]", text or "", re.DOTALL)
    if not m:
        return []
    try:
        v = json.loads(m.group(0))
        return v if isinstance(v, list) else []
    except (ValueError, TypeError):
        return []


# ─── Judgment point 2: rank+select which fetched sources to keep ───────────

def _select_sources(topic, fetched, model, project_name, agent_id, user_id):
    """fetched = [{title, link, content}]. Returns the indices to keep, ranked.
    Falls back to keeping all (in fetch order) if the LLM call fails."""
    if len(fetched) <= 3:
        return list(range(len(fetched)))
    catalog = "\n".join(
        f"[{i}] {s['title']} — {s['link']}\n    {(s['content'][:300] or '').strip()}"
        for i, s in enumerate(fetched))
    sys = ("You are a research source selector. Given a topic and a numbered list "
           "of fetched sources (title + URL + excerpt), choose the indices most "
           "relevant and trustworthy for a grounded report, best first. Drop "
           "off-topic, duplicate, or low-quality sources. Output ONLY a JSON array "
           "of the integer indices to keep.")
    reply, err = _bg_text(f"Topic: {topic}\n\nSources:\n{catalog}", sys, model,
                          project_name, agent_id, user_id, purpose="transform", max_tokens=400)
    if err:
        return list(range(len(fetched)))
    idxs = [i for i in _extract_json_list(reply) if isinstance(i, int) and 0 <= i < len(fetched)]
    return idxs or list(range(len(fetched)))


# ─── Judgment point 3: grounded cited synthesis ────────────────────────────

def _synthesize(topic, sources, coverage_note, model, project_name, agent_id, user_id):
    """sources = [{title, link, content}] (selected). Returns (report_md, err)."""
    discipline = _brain.render_research_mode_disciplines()
    corpus = "\n\n".join(
        f"--- Source: {s['title']} ({s['link']}) ---\n{s['content']}" for s in sources)
    sys = (
        "You are a research synthesizer producing a long-form, STRUCTURED, CITED "
        "report grounded strictly in the provided sources.\n\n" + discipline +
        "\n\nAdditional rules for this report:\n"
        "- Cite every non-trivial claim verbatim as [Quelle: <source title or domain> "
        "— \"<exact quoted snippet>\"].\n"
        "- Structure: ## Summary, then thematic sections with headings, then "
        "## Sources (a bulleted list of the sources you used).\n"
        "- Use ONLY the provided sources. Do not add outside knowledge. If the "
        "sources don't cover part of the topic, say so plainly.\n"
        "- Write in the language of the topic.")
    prompt = (f"Research topic: {topic}\n\n"
              f"{coverage_note}\n\n=== FETCHED SOURCES ===\n{corpus}")
    return _bg_text(prompt, sys, model, project_name, agent_id, user_id,
                    purpose="transform", max_tokens=4096)


# ─── The bounded loop (worker thread body) ─────────────────────────────────

def _run_research(*, run_id, agent_id, project_name, project_id, project_dir,
                  topic, budget, existing_norm_urls, user_id):

    def _cancelled():
        return ChatDB.research_run_cancelled(run_id)

    def _progress(phase, **counts):
        ChatDB.update_research_run(run_id, phase=phase, progress=json.dumps(counts))

    fetches_used = 0
    try:
        model = _brain._background_model_default()
        if not model:
            ChatDB.update_research_run(run_id, status="error", error="No model available (set a server default model).")
            return
        if not active_backend():
            ChatDB.update_research_run(run_id, status="error", error="No search backend configured.")
            return

        # 1. Plan
        _progress("planning", subqueries=0)
        if _cancelled():
            return ChatDB.update_research_run(run_id, status="cancelled")
        subqueries = _decompose(topic, model, project_name, agent_id, user_id)
        _progress("searching", subqueries=len(subqueries), candidates=0)

        # 2. Search — fan out the sub-queries concurrently (bounded by rounds =
        #    number of sub-queries, capped). The per-query searches run in a
        #    thread pool; dedup/merge into `candidates` happens single-threaded
        #    in this parent thread as each result lands, so the dict + the
        #    existing-URL exclusion stay race-free without a lock. contextvars
        #    propagate via copy_context().run (fresh pool threads start empty).
        if _cancelled():
            return ChatDB.update_research_run(run_id, status="cancelled")
        queries = subqueries[:budget.get("rounds", 8)]
        candidates = {}  # norm_url → {title, link, snippet, score}
        parent_ctx = contextvars.copy_context()
        with concurrent.futures.ThreadPoolExecutor(
                max_workers=_io_workers("search_workers", _DEFAULT_SEARCH_WORKERS)) as ex:
            futs = {ex.submit(parent_ctx.copy().run, _run_search, q): q for q in queries}
            for fut in concurrent.futures.as_completed(futs):
                try:
                    results = fut.result()
                except Exception:
                    results = []
                for r in results:
                    key = _norm_url(r["link"])
                    if key and key not in candidates and key not in existing_norm_urls:
                        candidates[key] = r
                _progress("searching", subqueries=len(subqueries), candidates=len(candidates))
        cand_list = sorted(candidates.values(), key=lambda x: x.get("score", 0), reverse=True)

        if not cand_list:
            # W9 — nothing usable
            ChatDB.update_research_run(
                run_id, status="done", phase="done", proposed="[]",
                coverage_note="No new sources found for this topic. Try broadening it or use Fast Research.")
            return

        # 3. Fetch + read top candidates within the fetch budget — fan out over
        #    a BOUNDED pool (the cap is the main protection for the crawl4ai
        #    render service, an uncapped ThreadingHTTPServer). Each worker runs
        #    tool_web_fetch inside the parent's copied contextvars context (fresh
        #    pool threads start empty — tool_web_fetch reads request scope), and
        #    one fetch = one candidate. fetches_used + the `fetched` list are
        #    mutated ONLY here in the parent as futures complete, so no lock.
        if _cancelled():
            return ChatDB.update_research_run(run_id, status="cancelled")
        fetch_cap = min(len(cand_list), int(budget.get("fetches", 60)))
        fetched = []
        parent_ctx = contextvars.copy_context()

        def _fetch_one(c):
            raw = _brain.tool_web_fetch({"url": c["link"], "max_length": _FETCH_MAX_LEN})
            fr = json.loads(raw)
            content = (fr.get("content") or "").strip()
            if not fr.get("error") and len(content) >= _MIN_USABLE_CONTENT:
                return {"title": c["title"] or c["link"], "link": c["link"],
                        "snippet": c.get("snippet", ""), "content": content}
            return None

        with concurrent.futures.ThreadPoolExecutor(
                max_workers=_io_workers("fetch_workers", _DEFAULT_FETCH_WORKERS)) as ex:
            futs = {ex.submit(parent_ctx.copy().run, _fetch_one, c): c
                    for c in cand_list[:fetch_cap]}
            for fut in concurrent.futures.as_completed(futs):
                fetches_used += 1
                try:
                    item = fut.result()
                except Exception:
                    item = None
                if item:
                    fetched.append(item)
                _progress("reading", subqueries=len(subqueries),
                          candidates=len(cand_list), fetched=fetches_used, kept=len(fetched))

        if not fetched:
            ChatDB.update_research_run(
                run_id, status="done", phase="done", proposed="[]",
                coverage_note=f"Searched {len(cand_list)} candidates but none could be fetched/read.")
            return

        # 4. Select (judgment) → ranked subset
        if _cancelled():
            return ChatDB.update_research_run(run_id, status="cancelled")
        keep_idx = _select_sources(topic, fetched, model, project_name, agent_id, user_id)
        selected = [fetched[i] for i in keep_idx]

        # 5. Fit the ranked corpus to the token budget so synthesis never
        #    overflows the model context (overflow → empty completion → 'Empty
        #    report'). budget["tokens"] (default 80k) is enforced HERE — it was
        #    declared but never wired before; with parallel fetch now keeping all
        #    selected sources, a large corpus (e.g. 37×12k chars) overflowed.
        synth_sources, n_dropped = _fit_corpus(selected, int(budget.get("tokens", 80000)))

        # 6. Coverage note (W8 — bounded coverage stated, never silent)
        drop_note = (f" Dropped {n_dropped} lower-ranked source(s) to fit the "
                     f"{budget.get('tokens', 80000)}-token synthesis budget."
                     if n_dropped else "")
        coverage_note = (
            f"Coverage: planned {len(subqueries)} sub-questions, found "
            f"{len(cand_list)} candidate sources, fetched {fetches_used} within "
            f"budget ({budget.get('fetches')} max), synthesized from "
            f"{len(synth_sources)}.{drop_note}")

        # 7. Synthesize (judgment) → cited report
        _progress("writing", subqueries=len(subqueries), candidates=len(cand_list),
                  fetched=fetches_used, kept=len(synth_sources))
        report_md, err = _synthesize(topic, synth_sources, coverage_note, model,
                                     project_name, agent_id, user_id)
        if err or not report_md:
            detail = ("Synthesis failed: " + err) if err else (
                f"Synthesis returned no text (model produced an empty completion "
                f"from {len(synth_sources)} source(s) — likely a context/length "
                f"limit; lower research.tokens or the fetch count).")
            ChatDB.update_research_run(run_id, status="error", error=detail)
            return

        # 8. Save as a project_outputs row (kind=research_report) — reuses the
        #    SHARED save path so Studio browses it with zero new code.
        output_id = uuid.uuid4().hex
        title = f"Research — {topic[:80]}"
        ChatDB.create_project_output(output_id, agent_id, project_id, "research_report",
                                     title, json.dumps({"topic": topic}), user_id)
        body = f"{report_md}\n\n---\n*{coverage_note}*\n"
        output_gen.save_report_output(output_id, agent_id, project_dir, "research_report", title, body)

        # 9. Propose the selected sources for approval (dedup'd vs project already).
        proposed = [{"title": s["title"], "url": s["link"], "snippet": s.get("snippet", ""),
                     "trust_hint": _trust_hint(s["link"]), "in_project": False}
                    for s in selected]
        ChatDB.update_research_run(
            run_id, status="done", phase="done",
            report_output_id=output_id, proposed=json.dumps(proposed),
            coverage_note=coverage_note,
            progress=json.dumps({"subqueries": len(subqueries), "candidates": len(cand_list),
                                 "fetched": fetches_used, "kept": len(selected)}))
    except Exception as e:
        import traceback
        traceback.print_exc()
        try:
            ChatDB.update_research_run(run_id, status="error", error=f"{type(e).__name__}: {e}"[:500])
        except Exception:
            pass


def start_research(*, agent_id, project, topic, budget, user_id):
    """Insert a running research_runs row + spawn the worker. Returns run_id.
    Caller validated topic + project membership + an active search backend."""
    run_id = uuid.uuid4().hex
    project_id = project.get("id") or ""
    project_name = project.get("folder_name") or project.get("name") or ""
    project_dir = project.get("dir") or ""
    # Budget precedence: config.json default (research.<k>) → per-run override.
    # Config absent ⇒ DEFAULT_BUDGET, so behavior is unchanged unless configured.
    eff_budget = {
        "fetches": _research_int("fetches", DEFAULT_BUDGET["fetches"], 1, 500),
        "tokens": _research_int("tokens", DEFAULT_BUDGET["tokens"], 4000, 1_000_000),
        "rounds": _research_int("rounds", DEFAULT_BUDGET["rounds"], 1, 32),
    }
    if isinstance(budget, dict):
        for k in ("fetches", "tokens", "rounds"):
            if isinstance(budget.get(k), int) and budget[k] > 0:
                eff_budget[k] = budget[k]
    # Dedup set: normalised existing project web_urls (W6 — never re-propose).
    existing = {_norm_url(u.get("url", "")) for u in (project.get("web_urls") or [])}
    ChatDB.create_research_run(run_id, agent_id, project_id, topic, json.dumps(eff_budget), user_id)
    threading.Thread(
        target=_run_research,
        kwargs={"run_id": run_id, "agent_id": agent_id, "project_name": project_name,
                "project_id": project_id, "project_dir": project_dir, "topic": topic,
                "budget": eff_budget,
                "existing_norm_urls": existing, "user_id": user_id},
        daemon=True, name=f"deep_research_{run_id[:8]}").start()
    return run_id, eff_budget
