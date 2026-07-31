---
name: Prompt cache reuse — reality vs documentation
description: Findings from investigating forked-agent prompt cache reuse (item 2 of Claude Code analysis roadmap)
type: project
originSessionId: 420945d9-094d-4db9-a3c0-9e8fa2afa788
---
**Finding:** Prompt caching is effectively dead in Brain after v7.2.0 (Anthropic/Mistral wire-format purge).

## What's actually happening

1. **Summariser path** (`execution.py:_summarise_tool_result`, ~line 524): sends a completely fresh `[system=_SUMMARISER_SYSTEM, user=tool_output]` conversation. Zero overlap with parent prefix — no cache reuse possible or intended. Summarising raw tool output doesn't need conversation context, so this is fine.

2. **Next-prompt suggestion path** (`claude_cli.py:generate_next_prompt_suggestion`, ~line 7414): passes `tools=False`. In `send_message` (~line 16510), system prompt is only injected `if tools`. **So next-prompt sends conversation WITH NO system prompt** — different prefix from the parent, which sent `[system, ...messages]`. Cache cannot hit.

3. **Wire format**: Brain is now OpenAI-compatible only (Bifrost, Kilo). v7.2.0 changelog explicitly says `cache_control` markers were removed as "no-op outside Anthropic wire format". Neither Bifrost nor Kilo have documented OpenAI-compatible caching we rely on.

## CLAUDE.md is wrong

CLAUDE.md:121 claims the next-prompt call "hits the same prompt cache as the main chat (near-free)". This is outdated — was probably true when Brain spoke Anthropic wire format with `cache_control` breakpoints. Today the claim is false.

## What to do

- If we ever want cache reuse back: inject the parent's system prompt into next-prompt calls, AND pick a provider that honors caching (Anthropic direct, or Bifrost configured to forward cache markers).
- Otherwise: next-prompt is a small-but-nonzero direct LLM call. Roughly `conversation_tokens + ~50 instruction tokens`. Not egregious.
- Update CLAUDE.md:121 to remove the "near-free" claim.

**Why:** prevents future-us from over-estimating the cost-efficiency of forked-agent patterns, and flags the stale doc.

**How to apply:** when planning features that assume cache reuse (e.g. item 8 of the analysis roadmap — "prompt suggestion via forked agent"), account for the fact that current Brain setup won't deliver it without provider changes.
