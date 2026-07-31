---
name: feedback_update_skill_before_push
description: "Before any commit&push that touches user-facing features/endpoints/tools/schemas/UI, update the brain-agent-guide skill in the SAME commit — Claude Code does this itself, no external automation"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 56686b1d-0566-4d1d-9d3e-f1fad04f638f
---

The user wants the `brain-agent-guide` skill (Brainy's knowledge base) kept current **automatically as part of pushing** — but the right "automatism" is **me (Claude Code) doing it before I push**, NOT a git-hook LLM call or a brain-agent sidecar `background_call`. The push happens in Claude Code, so I am the LLM that writes the skill update. There is no auto-deriver: the skill is curated prose (German UI walkthroughs, routing, "never guess" discipline) and `CLAUDE.md` states it "is NOT auto-derived from code — it drifts unless maintained."

**Why:** the user asked for "einen Automatismus beim Push." After ruling out a pre-push LLM hook (push-time LLM calls are slow/non-deterministic and can silently degrade the curated docs) and clarifying that the push runs in Claude Code (not the brain-agent), the agreed mechanism is a behavioral rule for me, with the existing git `pre-push` hook left as a *warning-only* safety net (override `SKILL_DOC_OK=1`), NOT escalated to a hard block.

**How to apply:** whenever a change adds/alters a user-facing feature, an HTTP endpoint, an agent tool, a DB schema, or a UI control, update the matching skill file in the SAME commit BEFORE pushing — `01-api.md` (endpoints), `02-tools.md` (tools), `03-storage.md` (schemas), `04-recipes.md` (how-tos), `05-internals.md` (architecture), `06-user-manual.md` (German web-UI walkthrough), `SKILL.md` (routing). Then bump the version in BOTH places per [[feedback_version_two_places]]. The server's `load_skill` reads `SKILL.md` fresh from disk per call, so the local server is current the moment the files change (no restart for skill text); the `brain_code` source wing is a SEPARATE path mined from GitHub by the miner daemon (~30 min), not affected by this rule. Related: [[feedback_commit_directly_to_main]], [[feedback_german_ui_everywhere]], [[project_helpdesk_brainy]].
