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

import json
import re
import threading
import uuid

import brain as _brain
from engine import output_gen
from server_lib.db import ChatDB

# Budget defaults (server-side, generous per spec §8.1: ~60 fetches / ~80k tok /
# ~4 min). Surfaced live in the UI — never silent.
DEFAULT_BUDGET = {"fetches": 60, "tokens": 80000, "rounds": 8}
_MAX_SUBQUERIES = 8
_RESULTS_PER_QUERY = 8
_FETCH_MAX_LEN = 12000          # per-source markdown cap fed to synthesis
_MIN_USABLE_CONTENT = 200       # chars; below this a fetch is treated as failed

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
    try:
        if backend == "searxng":
            raw = _brain.tool_searxng_search({"query": query, "num_results": _RESULTS_PER_QUERY})
        else:  # exa
            raw = _brain.exa_search(query, num_results=_RESULTS_PER_QUERY)
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
    sys = ("You are a research planner. Break the user's topic into focused, "
           "distinct sub-questions that together cover it. Output ONLY a JSON "
           "array of strings (the sub-questions), nothing else. "
           f"Return between 3 and {_MAX_SUBQUERIES} sub-questions.")
    reply, err = _bg_text(f"Topic: {topic}", sys, model, project_name, agent_id, user_id,
                          purpose="transform", max_tokens=600)
    if err:
        return [topic]  # degrade: search the raw topic
    subs = _extract_json_list(reply)
    subs = [s.strip() for s in subs if isinstance(s, str) and s.strip()]
    return subs[:_MAX_SUBQUERIES] or [topic]


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

        # 2. Search (bounded by rounds = number of sub-queries, capped)
        candidates = {}  # norm_url → {title, link, snippet, score}
        for q in subqueries[:budget.get("rounds", 8)]:
            if _cancelled():
                return ChatDB.update_research_run(run_id, status="cancelled")
            for r in _run_search(q):
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

        # 3. Fetch + read top candidates within the fetch budget
        fetch_cap = min(len(cand_list), int(budget.get("fetches", 60)))
        fetched = []
        for c in cand_list[:fetch_cap]:
            if _cancelled():
                return ChatDB.update_research_run(run_id, status="cancelled")
            try:
                raw = _brain.tool_web_fetch({"url": c["link"], "max_length": _FETCH_MAX_LEN})
                fetches_used += 1
                fr = json.loads(raw)
                content = (fr.get("content") or "").strip()
                if not fr.get("error") and len(content) >= _MIN_USABLE_CONTENT:
                    fetched.append({"title": c["title"] or c["link"], "link": c["link"],
                                    "snippet": c.get("snippet", ""), "content": content})
            except Exception:
                pass
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

        # 5. Coverage note (W8 — bounded coverage stated, never silent)
        coverage_note = (
            f"Coverage: planned {len(subqueries)} sub-questions, found "
            f"{len(cand_list)} candidate sources, fetched {fetches_used} within "
            f"budget ({budget.get('fetches')} max), synthesized from {len(selected)}.")

        # 6. Synthesize (judgment) → cited report
        _progress("writing", subqueries=len(subqueries), candidates=len(cand_list),
                  fetched=fetches_used, kept=len(selected))
        report_md, err = _synthesize(topic, selected, coverage_note, model,
                                     project_name, agent_id, user_id)
        if err or not report_md:
            ChatDB.update_research_run(run_id, status="error",
                                       error=("Synthesis failed: " + err) if err else "Empty report.")
            return

        # 7. Save as a project_outputs row (kind=research_report) — reuses the
        #    SHARED save path so Studio browses it with zero new code.
        output_id = uuid.uuid4().hex
        title = f"Research — {topic[:80]}"
        ChatDB.create_project_output(output_id, agent_id, project_id, "research_report",
                                     title, json.dumps({"topic": topic}), user_id)
        body = f"{report_md}\n\n---\n*{coverage_note}*\n"
        output_gen.save_report_output(output_id, agent_id, project_dir, "research_report", title, body)

        # 8. Propose the selected sources for approval (dedup'd vs project already).
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
    eff_budget = dict(DEFAULT_BUDGET)
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
