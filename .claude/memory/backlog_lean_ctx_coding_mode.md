---
name: lean-ctx coding mode backlog
description: lean-ctx concepts to apply when Brain gets a coding mode — AST signatures as compression layer, 10 read modes, session cache
type: project
originSessionId: 70381c9e-811e-4c21-9e96-0d82f9481762
---
lean-ctx (github.com/yvgude/lean-ctx) is a Rust-based context runtime for AI coding agents that compresses file reads 60–95% before they hit the LLM.

Key concepts worth adopting when Brain gets a coding mode:

**Read Modes** (10 modes): `full` / `map` / `signatures` / `diff` / `aggressive` / `entropy` / `task` / `reference` / `lines` / `auto`
- `signatures` sends only class/method/type stubs via Tree-sitter (Brain already has Tree-sitter code_graph — repurpose as compression layer, not just navigation)
- `map` sends structure overview; only switch to `full` when editing

**Session Cache**: hash-based re-read detection → ~13 tokens for unchanged files (Brain already has per-session read_doc cache — similar idea, can extend)

**Entropy Filtering**: Shannon entropy analysis removes redundant/boilerplate lines before send

**90+ CLI Output Patterns**: compress git/cargo/npm/docker output before it reaches the LLM

**Project Dependency Graph**: cross-file import resolution (Brain's code_graph already does this)

**KG + lean-ctx synergy idea (2026-05-03)**: When KG is re-enabled, use KG triples as a "signatures layer" for policy docs — instead of always doing full read_document after mempalace_query, check if the KG triple already contains the answer. Only fall back to read_document if the triple is insufficient. This would reduce token cost on the post-retrieval read pass significantly.

**Why:** Our current code_graph is navigation-only. lean-ctx shows that AST = compression layer is the higher-value use case for coding agents.

**How to apply:** When implementing coding mode, wire `read_file` with a `mode` parameter. Use existing Tree-sitter code_graph data to serve `signatures` mode without re-parsing.
