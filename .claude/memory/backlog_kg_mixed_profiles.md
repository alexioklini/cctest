---
name: KG mixed profiles per document
description: Auto-detect normative vs generic KG profile per source file within a project
type: project
originSessionId: fb969ddf-cff5-4879-a081-7ba8dd1e96df
---
Per-file KG profile routing — currently one profile per project, can't mix normative + generic.

**Why:** Some projects contain both policy PDFs (normative) and narrative docs (generic). Forcing one profile misses structure or adds noise.

**How to apply:** When building this:
1. Add `profile` column to KG triples table (migration, same pattern as `span`)
2. Per-file detection: filename keywords (`Richtlinie`, `Policy`, `ARL`, `Konzept`, `Verordnung`, `gemäß`, `verpflichtet`) → normative; else generic. Cheap regex, no LLM needed.
3. Pass detected profile into `_process_source` instead of global profile
4. KG query tools can optionally filter by profile
