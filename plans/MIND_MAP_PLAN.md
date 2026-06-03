# Mind Map (KG Visualization) — Implementation Plan

**Status:** SCOPED, not built. Greenlit in the 2026-06-03 NotebookLM-gap session.
**Parent context:** `NOTEBOOKLM_GAP_HANDOVER.md` (Tier 1 #4). brain-agent VERSION
when scoped: 9.62.0.

---

## What this is

An interactive map of a project's knowledge. NotebookLM's mind map is
**embedding-only** (proximity blobs). Ours visualizes the **real KG triples** we
already extract per project — typed edges (`requires`, `cites`, `defines`, …), so
you can see *why* two concepts connect, not just that they're "near." Clicking a
node seeds a grounded chat on that subtopic. **This is a feature where we can beat
NotebookLM, not just match it.**

---

## What already exists (strong foundation)

- Per-project KG at `<palace_path>/knowledge_graph.sqlite3` — `(subject,
  predicate, object)` triples, **12-predicate normative profile**, each with a
  `source_file` drawer ref.
- **Entity-first tools** (`engine/mempalace_glue.py`): `tool_mempalace_kg_query`
  (1690 — `query_entity(entity, direction=outgoing/incoming/both)`),
  `tool_mempalace_kg_search` (1734 — structured by predicate OR free-text), and
  `tool_mempalace_kg_neighbors` (1854). Directional traversal = the expand-a-node
  interaction already exists.
- **Project scoping** via source-prefix filter (`_kg_source_in_scope`) — a mind
  map is per-project by construction, no leak risk.
- `_kg_resolve_project_scope()` / `_kg_open(palace_path)` — the open + scope helpers
  to reuse for the new whole-graph read.

## The one real gap

Every existing tool is **entity-first** (query ONE entity → its triples). A mind
map needs a **whole-graph read** (all nodes + typed edges for the project, or a
top-N-by-degree subgraph) to render initially. That read path does NOT exist yet —
but it's a straightforward `SELECT` over the same table, reusing the existing
prefix scoping. Not a new data model, just a new read shape.

---

## Locked decisions

| Decision | Choice | Notes |
|---|---|---|
| **Surface** | **New full-view tab/page** | Room to pan/zoom large graphs. Needs a nav entry + a project-scoped view. |
| **Renderer** | **Lightweight graph lib (Cytoscape or vis)** | ⚠️ See the no-bundler constraint below — must vendor a UMD/global build. |
| **Node click** | **Seed a grounded chat** scoped to that entity | Full NotebookLM "click a branch → chat" parity (`mempalace_query` + KG on that node). |

### ⚠️ Renderer constraint — read before adding the lib

This repo has **NO ES modules, NO bundler** (CLAUDE.md: every fn/var is a browser
global, fixed load order via `<script>` tags). You **cannot** `npm install
cytoscape` and `import` it. Required approach:
1. Vendor the library's **UMD / global-scope build** (a single
   `cytoscape.min.js` that attaches `window.cytoscape`) into `web/js/` (or a
   `web/vendor/`).
2. Add it to the **fixed load order** BEFORE the mind-map file (it must be defined
   before `init.js`).
3. `./web/js/js_gate.sh` enforces a **net-globals-count invariant** — adding the
   library global + the mind-map module's functions changes the count; update the
   expected count deliberately and document why.
4. Cytoscape DOES publish a UMD build (so does vis-network) — confirm at build,
   pick whichever has the cleaner single-file global drop.

---

## Build steps

### 1. Whole-graph read endpoint (engine + handler)

- `GET /v1/projects/<id>/kg/graph` → `{nodes:[{id,label,degree}],
  edges:[{source,target,predicate,source_file}]}`.
- Reuse `_kg_resolve_project_scope()` + `_kg_open()`; `SELECT` all in-scope triples
  (prefix-filtered), aggregate into node + edge lists, compute degree.
- **Pruning for renderability**: cap to top-N nodes by degree (param `?limit=`)
  so a 300-triple policy graph doesn't ship 300 nodes to the browser at once. Note
  in the response when pruning happened (don't silently truncate — repo rule).
- Auth + project-membership check like other project endpoints.

### 2. Frontend graph view (the bulk of the work)

- New full-view page/tab + nav entry (follow web/js global-scope + load-order
  conventions; gate with `./web/js/js_gate.sh`).
- Fetch the graph, hand nodes/edges to the vendored lib, render with a force/
  layered layout, pan/zoom.
- Edge labels = predicates (the differentiator — show the typed relationship).
- Loading/empty states: "no KG yet — run extraction" when the db is absent (the
  endpoint should signal this distinctly from an empty project).

### 3. Click-a-node → grounded chat

- On node click, open/seed a project chat scoped to that entity: a turn primed to
  call `mempalace_kg_query` (that entity) + `mempalace_query` (its source_file
  drawers). Mirrors NotebookLM's branch→chat.
- DECIDE AT BUILD: open a fresh session vs. inject into the current one; how to
  pre-seed the first user message.

---

## Open items to resolve AT BUILD

1. **Lib choice** — Cytoscape vs vis-network: pick by cleanest UMD single-file
   global + click/expand API. (Renderer constraint above.)
2. **Pruning policy** — top-N by degree? by predicate importance? Let the user
   raise the cap? Start simple (degree, with a "show more" control).
3. **Layout** — force-directed (organic) vs hierarchical (good for normative
   `requires`/`cites` chains). Maybe expose a toggle.
4. **Node-click target session** — new vs current session; first-message seeding.
5. **Multiple maps per project** (NotebookLM allows several) — out for v1, note
   as a later add (ties to the Tier 3 "Studio: multiple outputs" item).

## Explicitly OUT of scope

- In-graph neighbor-expand (we chose chat-seed; expand-in-place is a possible add).
- Multiple saved maps per project (v2).
- Editing the graph from the UI (read-only visualization).

---

## Repo-convention obligations (same change)

- brain-agent-guide skill: new endpoint → `01-api.md`; new UI view →
  `06-user-manual.md` (German). VERSION bump in two places. python-compile
  brain.py. Graceful restart (SIGTERM, never SIGKILL). Commit to main.
- **js_gate net-globals-count** must be updated for the vendored lib + new module.

## Success criteria

- On a project with an extracted KG, the view renders nodes + **typed** edges,
  pans/zooms, and prunes large graphs gracefully (with a visible "pruned" signal).
- Clicking a node opens a grounded chat scoped to that entity.
- Absent-KG state shows a clear "run extraction" message, not an error.
- `./web/js/js_gate.sh` passes (count invariant updated); brain.py compiles;
  version check after restart.
