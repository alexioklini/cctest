# Claude & Claude Code: New Features Summary
## March 2026 — Targeting OpenClaw Territory

---

## Overview

Between March 4–20, 2026, Anthropic shipped 12+ major updates to Claude Code (v2.1.68 → v2.1.80). The pattern is clear: nearly every high-impact feature maps directly onto OpenClaw's core value propositions — remote messaging control, background scheduling, persistent memory, and agent extensibility. The community reaction was instant: *"They've built OpenClaw."*

---

## 7 Key Features at a Glance

### 1. Claude Code Channels (v2.1.80 — Mar 19–20) 🔴
Remote control of a live Claude Code session via **Telegram or Discord** from any device. Built on MCP, runs locally, open plugin architecture. Directly replicates OpenClaw's flagship Telegram integration — but officially, securely, and with Discord added.

### 2. `/loop` — Session Cron Scheduler (v2.1.71 — Mar 7) 🔴
Run any Claude prompt on a repeating schedule inside an active session. Natural language time parsing, up to 50 tasks, 72-hour max lifetime. Turns Claude Code into a proactive background agent — the OpenClaw heartbeat loop, natively.

### 3. Desktop Scheduled Tasks (Early March)
The persistent sibling to `/loop`. Configured in the Claude Desktop app, survives indefinitely, processes catch-up runs on reopen. Combined with Telegram delivery: **configure once → Claude runs unattended → get notified on your phone**. This is the full OpenClaw workflow.

### 4. Auto-Memory with Structured Format (v2.1.74–75)
Claude Code automatically stores memories without a `/remember` command. Each entry uses a 3-part template: **Rule → Reason → Application**. More actionable than OpenClaw's loosely structured notes. Stored locally in `.claude/`, with timestamps added in v2.1.75.

### 5. Skills 2.0 Enhanced (March)
Built on January's Skills 2.0 launch: now includes structured evals, A/B benchmarking, hot reload, forked context, lifecycle hooks, and per-skill model overrides. Real-world benchmarks show 40–287% task accuracy improvements. 4 bundled skills ship out-of-the-box.

### 6. Multi-Agent Code Review (Mar 10 — Team/Enterprise)
5 parallel agents review every PR from distinct angles (compliance, bugs, git history, prior comments, code comments). Only >80% confidence issues get posted. ~20 min turnaround, ~$15–25/run. Does not auto-approve — human remains final authority.

### 7. Supporting Upgrades
- **Opus 4.6** as default model (replaces Opus 4/4.1)
- **1M token context window** — entire codebases in one window
- **`ultrathink` mode** — extended reasoning budget
- **MCP Elicitation** — MCP servers can request context mid-session
- **`modelOverrides`** — route task types to different models
- **New commands:** `/btw`, `/plan`, `/simplify`, `/copy`, `/effort`, `/v` (voice, 10 languages)
- **Google Workspace** (Beta) — Drive, Docs, Sheets, Slides via CLI
- **Interactive Visualizations** (Beta) — charts/diagrams in desktop app

---

## OpenClaw vs Claude Code: Where Things Stand

| OpenClaw Strength | Claude Code Status |
|---|---|
| Telegram remote control | ✅ Fully matched (Channels) |
| Cross-session memory | ✅ Fully matched (Auto-Memory, structured) |
| 24/7 cloud persistence | ⚠️ Partial — machine must stay on |
| 15+ messaging platforms | ⚠️ Partial — Telegram + Discord only at launch |
| Model agnosticism | ❌ Claude-only (Anthropic lock-in) |
| Permissionless skill marketplace | ❌ Claude's skill directory is curated |

**Claude Code decisive wins:** Security (CVE-2026-25253 exposed OpenClaw to RCE), coding intelligence, setup simplicity, cost efficiency, enterprise compliance.

**Founder Scorecard (Craig Hewitt, LinkedIn):** OpenClaw 52/80 vs Claude Code 62/80.
> *"OpenClaw is a glimpse into the future. Claude Code has receipts."*

---

## Strategic Takeaway

Anthropic executed the classic platform playbook: observe breakout open-source project (OpenClaw, 200k–325k GitHub stars) → validate demand → productize natively → own distribution. The February 2026 OpenClaw security vulnerability (CVE-2026-25253, CVSS 8.8, 40k+ exposed instances) handed Anthropic a clear narrative advantage.

OpenClaw retains a moat in true 24/7 cloud persistence, model flexibility, and community breadth. Claude Code wins on safety, polish, and raw coding capability. The expert consensus: **use both** — Claude Code for active development, OpenClaw-style patterns within Claude Code's ecosystem for background automation.

---

*Full deep dive: `Claude_CodeDive_Complete_Report.md`*
*Generated: March 21, 2026*
