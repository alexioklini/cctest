---
name: project_crawl4ai_render_service
description: "crawl4ai headless-render service for JS-rendered web pages — venv, supervisor, why web_fetch returns raw HTML"
metadata: 
  node_type: memory
  type: project
  originSessionId: 87cdf066-0218-4d0f-b29b-d5f238839a87
---

2026-05-24: Added a **crawl4ai headless-browser render service** so JS-rendered pages (SvelteKit/React apps) yield real content. Root problem: `brain._html_to_markdown` (brain.py ~1810) converts HTML→md via the `markitdown` CLI but does `_html_to_markdown(text) or text` — on a JS-shell page markitdown returns ~empty, so `web_fetch` **falls back to raw HTML**. The chat view renders the tool result as `<pre><code>` (NOT markdown-rendered), so the user sees raw-looking text and can't tell what transform happened. For client-rendered pages (e.g. wien.wetterheute.at) the server HTML has NO content at all — only a JS shell — so no static converter can help; needs a real browser.

**Architecture** (mirrors sidecar/SearXNG ProcessSupervisor pattern):
- `.venv_crawl4ai` (Python 3.13 — NOT system 3.14, PEP-668 + heavy deps; gitignored) + crawl4ai + `playwright install chromium`.
- `crawl4ai/render_service.py` — stdlib HTTP server, port 8422, `GET /health` + `POST /render {url}` → `{success,markdown,length,url,error}`. One warm shared `AsyncWebCrawler` on a background event loop.
- **CRITICAL render config**: default `arun` returns NOTHING on JS pages. Must use `CrawlerRunConfig(wait_until='networkidle', delay_before_return_html=~2.5s)` — then weather URL returns 3715 chars of real forecast. Validated live.
- `Crawl4aiSupervisor(ProcessSupervisor)` in server_lib/sidecar_supervisor.py (singleton `crawl4ai_supervisor`), wired into server.py `main()` + `server_config['crawl4ai']` copy. Admin GET /v1/crawl4ai/status + POST /v1/crawl4ai/restart (handlers/admin_artifacts.py).

**GOTCHA that cost debugging time**: `ProcessSupervisor.start()` early-returns unless `config.json → <key>.auto_start == true`. A missing/empty config block = silent no-start (prints "auto_start disabled"). Had to add `config.json → crawl4ai {enabled,auto_start:true,url,venv_python}` (config.json is gitignored — set per-machine). Same requirement for sidecar/searxng.

Design decisions (user): used for BOTH live web_fetch + project-URL mining; FALLBACK trigger (cheap HTTP first, render only on empty/JS-shell); graceful degradation if service down → today's behavior. Related: [[project_manual_web_search_websuche]].

**Wiring shipped:**
- `tool_web_fetch` (engine/tools/misc_tools.py): tracks `fetch_method` (raw/markitdown/crawl4ai) on the result; markitdown first, then crawl4ai fallback when `usable` (converted) text < 30 chars on an HTML GET. `brain._crawl4ai_render(url)` + `_crawl4ai_base_url()` client helpers (graceful: service down → keep HTTP result). Gate is on USABLE (converted) length, NOT raw HTML length — early bug: raw HTML is 1681 chars so a `<200` raw gate never fired; and a `<200` usable gate wrongly re-rendered example.com (166 chars of real md). Final: `<30` usable = markitdown produced nothing = JS shell.
- **Chat-view badge** (chat_tools.js `_extractFetchMethod` + `.tool-result-fetch-badge` CSS): color-coded crawl4ai(blue)/markitdown(green)/raw(amber) badge on web_fetch results w/ tooltip — so the user can SEE what transform the LLM's content went through (fixes the "raw-looking text, can't tell" complaint).

**Project-URL mining (the ORIGINAL feature, now distinct from the chat Websuche basket):** project.json → `web_urls` [{url,title}] (whitelisted in ProjectManager.update_project; normalized in handlers/projects.py PUT; UI editor in panels_projects.js "Web URLs" section). `_sync_project_web_urls(pdir, web_urls)` in server_daemons.py fetches each fresh via tool_web_fetch (→ crawl4ai for JS pages) into `pdir/web-urls/weburl-<urlhash>.md` (hash-gated: rewrite→remine only when content changed). The project-sync loop mines that folder (1b, after ingested) + runs KG, same convert→mine→KG pass. Removed-URL cleanup: `_is_stale_src` in the stale-path purge also drops drawers whose web-urls/ .md no longer exists (prefix check alone misses them since web-urls/ is under pdir). NO turn-time injection, NO merge with chat basket — reached via mempalace_query/KG like any project knowledge.
