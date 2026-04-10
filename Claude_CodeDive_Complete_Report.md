# Claude & Claude Code: Deep Dive on New Features That Target OpenClaw Territory
## March 2026 — Versions 2.1.68 → 2.1.80

---

## Executive Summary

In roughly three weeks spanning early-to-mid March 2026, Anthropic shipped the most concentrated feature push in Claude Code's history — 12+ meaningful updates across versions 2.1.68 through 2.1.80. The pattern is unmistakable: virtually every high-impact feature maps directly onto OpenClaw's core value propositions. The culmination was Claude Code Channels (v2.1.80, March 19–20), which lets developers message their running Claude Code session via Telegram or Discord from any device. The developer community's reaction was immediate and blunt — *"They've BUILT OpenClaw"* (Matthew Berman) and *"Anthropic just shipped an OpenClaw killer"* (VentureBeat).

This analysis breaks down each major feature, the technical architecture behind it, how it compares to OpenClaw, and what the full picture means strategically.

---

## Version Changelog Timeline

| Version | Date | Headline Feature |
|---|---|---|
| **2.1.80** | Mar 19–20 | 🔴 Claude Code Channels (Telegram + Discord) |
| **2.1.76** | Mar 14 | MCP Elicitation, `/effort`, PostCompact hook |
| **2.1.75** | Mar 13 | 1M context window, Opus 4.6, memory timestamps |
| **2.1.74** | Mar 12 | `autoMemoryDirectory`, improved `/context` |
| **2.1.73** | Mar 11 | `modelOverrides` config, OAuth/SSL guidance |
| **2.1.72** | Mar 10 | ExitWorktree, simplified effort, `/plan` w/ description |
| **2.1.71** | Mar 7 | 🔴 `/loop` cron scheduler, rebindable voice push-to-talk |
| **2.1.70** | Mar 6 | VSCode spark icon, markdown plan view |
| **2.1.69** | Mar 5 | `/claude-api` skill, 10 new voice languages, 100+ fixes |
| **2.1.68** | Mar 4 | Opus 4.6 as default model, `ultrathink`, removed Opus 4/4.1 |

Two features (Channels and `/loop`) are the headliners. Everything else compounds them.

---

## Feature 1 — Claude Code Channels: Native Remote Control via Telegram & Discord
**Released:** March 19–20, 2026 | **Version:** 2.1.80+

### What It Is

Channels is a plugin-based system that connects a live, running Claude Code session to a messaging platform. The developer sends a message from their phone on Telegram or Discord → an MCP server forwards it into the active session → Claude processes it with full local filesystem, git, and MCP access → replies back in the same chat thread.

This is not a cloud-hosted coding environment. The session runs locally on the developer's machine. Telegram/Discord is purely the interface — a remote window into local execution.

### Technical Architecture

```
Phone (Telegram/Discord)
       ↓
  MCP server (Bun runtime — fast JS execution)
       ↓
  Polling service (--channels flag)
       ↓
  Active Claude Code session (local machine)
  [full filesystem / git / MCP tools access]
       ↓
  reply tool → sends response back to messaging app
```

**Setup (Telegram):**
```bash
# Requires v2.1.80+ and claude.ai Pro/Max (API key not supported)
claude --channels plugin:telegram@claude-plugins-official

# In-session configuration
/telegram:configure   # prompts for BotFather credentials
# Pair with security code
```

**Discord equivalent:**
```bash
claude --channels plugin:discord@claude-plugins-official
```

**Fakechat** — A local-only demo chat UI ships alongside for testing push/reply logic before connecting to any external platform. No bot setup needed.

**Open-source plugins:** Both Telegram and Discord plugin repos are hosted on official Anthropic GitHub, meaning the community can (and already is) building connectors for Slack, WhatsApp, iMessage. All three have been publicly requested within hours of launch.

### OpenClaw Comparison

| Dimension | OpenClaw | Claude Code Channels |
|---|---|---|
| Telegram access | ✅ Core feature | ✅ Now native |
| Discord access | ❌ Not officially | ✅ Shipped |
| Architecture | 3rd-party bridge (unofficial) | Native MCP plugin (official) |
| Session location | Cloud (persistent) | Local machine |
| Security | Community-maintained | Anthropic-secured |
| Subscription | OpenClaw license | claude.ai Pro/Max required |
| Extensibility | Community forks | Open plugin architecture |
| Always-on | ✅ 24/7 cloud | ❌ Machine must be running |

### Community Verdict

> *"This is exactly why Anthropic cracked down on OpenClaw users — to roll out their own version."* — r/ClaudeAI

> *"Well fuck... I guess I have to sign up for Pro again and retire my OpenClaw."* — r/ClaudeAI

> *"They've BUILT OpenClaw."* — Matthew Berman (AI commentator)

The timing is not subtle. The feature is functionally what OpenClaw users were paying for, now native, officially supported, and integrated directly into the Claude Code runtime.

---

## Feature 2 — `/loop`: Session-Level Cron Scheduler
**Released:** March 7, 2026 | **Version:** 2.1.71

### What It Is

`/loop` transforms Claude Code from a reactive tool into a proactive background worker. It runs any prompt on a recurring schedule, inside an active session, automatically — no external cron setup required.

### Syntax

```bash
# Fixed interval
/loop 15m check build status

# Natural language → parsed to cron
/loop audit deps every 2 hours

# One-shot reminder
remind me at 3 PM to push the release branch
```

- Supports both cron expressions and natural language time parsing
- Up to **50 scheduled tasks** per session
- Terminal can be closed — runs as a persistent background process

### Constraints and Guardrails

| Constraint | Detail |
|---|---|
| **Maximum lifetime** | 72 hours (3 days), hard limit |
| **Catch-up runs** | None — missed runs during sleep are dropped |
| **Session scope** | Dies with the session that created it |
| **Cost** | API credits consumed per cycle |
| **Underlying tools** | `CronCreate`, `CronList`, `CronDelete` (with jitter + safety expiry) |

### Use Cases (from Anthropic developers)

- Auto-scan error logs every few hours → create fix PRs automatically
- Monitor open PRs for new comments
- Generate morning Slack/Telegram summaries
- Dependency vulnerability checks
- Build status polling during deployments

**The key ergonomic shift:** You define the watch pattern once and context-switch away. Claude runs the loop in the background while you work on something else.

---

## Feature 3 — Desktop Scheduled Tasks: Persistent Durable Scheduler
**Released:** Early March 2026 | **Requires:** Claude Desktop app

### What It Is

The persistent, durable sibling to `/loop`. Where `/loop` is session-scoped and ephemeral, Desktop Scheduled Tasks are configured in the Claude Desktop app and survive indefinitely — as long as the app is running.

### `/loop` vs Desktop Scheduled Tasks

| Dimension | `/loop` | Desktop Scheduled Tasks |
|---|---|---|
| **Scope** | Session-level | Desktop app persistent |
| **Lifetime** | 72 hours max | Indefinite |
| **Context** | Continues in same session | Fresh instance per run |
| **Catch-up runs** | ❌ Dropped on sleep | ✅ Processed on reopen |
| **Environment** | CLI terminal | Desktop app |
| **Output routing** | Terminal/session | Configurable (incl. Telegram) |

### The Killer Combo: Scheduled Tasks + Telegram

This is where the two features compound into OpenClaw territory:

```
Desktop Scheduled Task (daily/weekly cadence)
       ↓
  Fresh Claude Code instance runs
       ↓
  Result pushed via Telegram bot (BotFather + ENV credentials)
       ↓
  Notification delivered to phone
```

The pipeline becomes: **configure once → Claude runs unattended → you get notified on your phone**. This is the OpenClaw workflow, natively.

**Practical automations:**
- Daily morning briefings from inbox + calendar + recent commits
- Weekly dependency update reports → pushed to team Telegram group
- Automated PR summaries → delivered to a channel
- Build alerts on failure

---

## Feature 4 — Auto-Memory with Structured Format
**Shipped in stages:** v2.1.74 (autoMemoryDirectory), v2.1.75 (timestamps)

### What It Is

Claude Code now writes memories automatically — no `/remember` command needed. When something noteworthy happens in a session (you correct output, establish a preference, explain an architectural decision), Claude stores it locally and recalls it next session.

Storage path: `.claude/` directory (project-local).

### The Structured Memory Format (System Prompt Enforced)

Every auto-memory entry follows a 3-part template:

```
1. The Rule or Fact
   "This project uses Pydantic v2 for data validation"

2. The Reason
   "v2 has breaking changes from v1, so always use model_validate() not .parse()"

3. The Application
   "Apply whenever writing serialization code or validating incoming API payloads"
```

This is a significant improvement over OpenClaw's long-term memory, which stored context as loosely structured notes. The 3-part format makes memories **actionable** — Claude doesn't just remember what you told it, it knows when and why to apply it.

### Management

- `/memory` — view, edit, delete stored memories
- `autoMemoryDirectory` config — skips unnecessary filesystem checks on writes (v2.1.74)
- Memory timestamps added (v2.1.75) — entries now track when they were written

---

## Feature 5 — Skills 2.0: From Static Instructions to a Programmable Agent Platform
**Originally launched:** January 2026 | **Enhanced March 2026**

### The Evolution

| Generation | When | Model |
|---|---|---|
| **Skills 1.0** | Oct 2025 | Drop a SKILL.md, write instructions, done. Static, no evaluation. |
| **Skills 2.0** | Jan 2026 | Structured evals, A/B testing, hot reload, forked contexts, lifecycle hooks. |
| **Skills 2.0 Enhanced** | Mar 2026 | Multi-agent parallel testing, model override per skill, 4 bundled skills. |

### New Capabilities in 2.0

**Structured Evaluations:** Skill Creator auto-generates test cases. Each eval defines explicit success criteria, runs against test cases, scores output, and produces a report. No more guessing whether a skill works.

**A/B Testing & Benchmarks:** Compare two skill versions head-to-head with a blind AI judge. A demo ran 5 parallel agents generating outputs → HTML report with pass/fail breakdown and concrete examples.

**Hot Reload:** Edit SKILL.md → takes effect immediately without restarting Claude Code.

**Forked Context Mode:** Skills can run in isolated subagent context windows — preventing skill execution from polluting the main session context.

**Lifecycle Hooks:** `before`/`after` shell command injection. Inject live data into prompts before Claude sees them (e.g., pull latest API schema, inject recent git log).

**Per-skill configuration:**
- Tool restriction (limit which Claude tools a skill can use)
- Model override (e.g., use Haiku for fast/cheap skill runs)
- Context budget (scales with context window — 2% reserved for skill descriptions)

### Real-World Benchmark Data

| Skill | Before | After | Improvement |
|---|---|---|---|
| SEO Audit | 34% | 87% | +156% |
| Insurance Triage | 67% | 94% | +40% |
| PDF Forms | 23% | 89% | +287% |

**4 bundled skills ship out-of-the-box**, including one that decomposes large codebase changes and spawns parallel agents in separate git worktrees.

---

## Feature 6 — Multi-Agent Code Review
**Released:** March 10, 2026 | **Status:** Research preview | **Tier:** Team & Enterprise only

### What It Is

Automated PR review using a fleet of 5 parallel agents, each reviewing from a different angle. This is Anthropic's own internal review system, now available externally.

### How It Works

```
PR opened or updated
       ↓
5 parallel agents dispatched:
  [1] CLAUDE.md compliance check
  [2] Bug detection scan
  [3] Git history context analysis
  [4] Previous PR comment review
  [5] Code comment verification
       ↓
Issues ranked by severity
Only >80% confidence issues posted (false positive reduction)
       ↓
Inline GitHub comments on specific lines
       ↓
~20 minute average turnaround
```

**Hard constraint:** Does NOT approve PRs. Human reviewers retain final authority.

**Cost:** ~$15–25 per run. Team/Enterprise only.

**Tunable:** Modify CLAUDE.md to configure what categories of issues Claude flags.

**Setup:** Team admin installs Claude GitHub app → selects repos → runs automatically on every PR.

---

## Feature 7 — Supporting Features (March 2026 Wave)

### Model & Context Upgrades

| Update | Version | Detail |
|---|---|---|
| **Opus 4.6 default** | 2.1.68 | Replaces Opus 4/4.1 entirely |
| **1M token context** | 2.1.75 | Full codebase fits in one context window |
| **`ultrathink` mode** | 2.1.68 | Extended reasoning budget for complex problems |

### New Commands

| Command | Function |
|---|---|
| `/btw [question]` | Ask an off-topic question without disrupting current task context |
| `/plan [description]` | Create plan inline — no need to enter plan mode first |
| `/simplify` | Auto code review post-write: checks reuse, quality, efficiency |
| `/copy` | Interactive picker for selecting specific code blocks from output |
| `/effort [low\|medium\|high\|max\|ultra]` | Set reasoning depth/cost at session start |
| `/v` | Push-to-talk voice — now universal, rebindable, 10 new languages |
| `/color` | UI color customization |
| `/context` | Improved context window management |

### Remote Control (Pre-Channels Option)

Before Channels shipped, `claude remote-control` was the mobile access path:
- Single command setup (vs Channels' bot creation flow)
- Uses claude.ai web interface as the session window
- Works with Claude iOS/Android mobile app

This remains a valid, simpler alternative for users who don't want to set up a Telegram/Discord bot.

### Infrastructure & MCP Improvements

- **MCP Elicitation (v2.1.76):** MCP servers can now request additional context or permissions from users mid-session — servers become interactive, not just passive data sources
- **PostCompact hook (v2.1.76):** Lifecycle hook that fires after context compaction — useful for skills that need to re-inject state after a long session is compressed
- **`modelOverrides` config (v2.1.73):** Route different task types to different models (e.g., heavy reasoning to Opus, simple tasks to Haiku)
- **ExitWorktree (v2.1.72):** Clean exit handling for git worktree-based workflows

### Google Workspace Integration (Beta)

A CLI exposes Google Drive, Docs, Sheets, and Slides directly to Claude Code. Previously Claude Code could only interact with email and calendar. Now it can produce and manipulate fully formatted Docs, Sheets, and Slides — not just spit out markdown that needs manual conversion.

### Interactive Visualizations (Beta)

Build interactive charts and diagrams directly in the desktop app conversation. Available on all plans, including free tier.

---

## Strategic Analysis: OpenClaw vs Claude Code's New Feature Stack

### OpenClaw's Four Core Value Propositions

| OpenClaw Feature | Claude Code Equivalent | Match Quality |
|---|---|---|
| Telegram/phone remote control | Channels (Telegram + Discord) + Dispatch (mobile→desktop) | ✅ Full match — native, officially supported, MCP-based |
| 24/7 always-on background tasks | /loop (session, 72h) + Desktop Scheduled Tasks (persistent) | ⚠️ Partial — machine must stay on; no cloud execution |
| Long-term cross-session memory | Auto-Memory (structured 3-part format, local .claude/) | ✅ Full match — arguably better structure than OpenClaw's notes |
| Multi-platform messaging integrations | Channels plugin architecture (open GitHub repos) | ⚠️ Partial — Telegram+Discord shipped; Slack/WhatsApp community-driven |

### What Claude Code Still Cannot Match (OpenClaw's Remaining Moat):

1.  **True 24/7 cloud persistence** — OpenClaw runs on a server; Claude's scheduler dies when the machine sleeps. This is a fundamental architecture difference, not a feature gap.
2.  **Model agnosticism** — OpenClaw works with Kimi K2.5, Llama, GPT-4o, local models. Claude Code is Claude-only (Anthropic lock-in).
3.  **Permissionless skill marketplace** — OpenClaw's ClawHub has thousands of community skills with no review gate. Claude's Skill directory is curated and slow-growing.
4.  **Platform breadth** — OpenClaw natively supports 15+ messaging platforms (WhatsApp, iMessage, LINE, Slack, etc). Claude Channels launched with 2 (Telegram, Discord).
5.  **Autonomous loop architecture** — OpenClaw runs without human oversight by design. Claude Code always routes confirmations back to the user — by design (safety-first).

### Where Claude Code Wins Decisively:

1.  **Security** — CVE-2026-25253 (CVSS 8.8) exposed OpenClaw's WebSocket origin bypass, granting full RCE via a single malicious link. 40,000+ instances were exposed publicly. Anthropic's architecture is closed, audited, enterprise-grade.
2.  **Coding intelligence** — Opus 4.6 with context compaction, 1M token context, and IDE integration beats any OpenClaw coding task hands-down.
3.  **Setup friction** — Claude Code installs in minutes; OpenClaw requires Docker, gateway config, SSL setup. The security-competent setup is a "legit side project" per one founder.
4.  **Cost efficiency** — OpenClaw's autonomous loop burns tokens continuously even at idle. Claude Code only consumes tokens during active task runs.
5.  **Enterprise compliance** — Sandboxed Cowork, audit trails, RBAC (Team/Enterprise), no data leaving Anthropic's approved infrastructure.

### Strategic Context — Why Anthropic Did This Now:

-   OpenClaw creator Peter Steinberger originally named the project "Clawd" → Anthropic sent a cease-and-desist for trademark violation → Steinberger renamed it OpenClaw → was subsequently hired by Anthropic's rival OpenAI.
-   OpenClaw reached 200,000–325,000 GitHub stars, validating massive demand for always-on personal AI agents.
-   Anthropic observed high token consumption from OpenClaw users on their subscriptions → implemented subscription restrictions → then shipped native alternatives.
-   The February 2026 security storm (CVE-2026-25253, malicious ClawHub skills) gave Anthropic a clear narrative: "we do this safely."
-   The pattern is classic platform playbook: observe breakout open-source project → validate demand → productize for mass market → own the distribution.

### The Bottom Line — "Founder Scorecard" Data Point (from LinkedIn, Craig Hewitt):

Scored OpenClaw vs Claude Code across 8 categories (1–10 each):
-   OpenClaw total: 52 | Claude Code total: 62
-   OpenClaw wins: Accessibility (9 vs 5), Reach/24-7 (9 vs 5), Future-fit concept (9 vs 8)
-   Claude Code wins: Setup (9 vs 4), Security (9 vs 3), ROI/receipts (9 vs 5)
-   Verdict: "OpenClaw is a glimpse into the future. Claude Code has receipts."

### Expert Community Consensus (March 2026):

-   Most developers running both: Claude Code for active development, OpenClaw-style patterns for background automation
-   Honest Reddit review after 1 month: "Claude Code is way smarter, cost efficient... it's heading in the same direction anyway. Save yourself the headache."
-   Epsilla (enterprise AI): "Features can be copied. Ecosystems cannot." — Claude cannot replicate OpenClaw's permissionless community innovation model
-   DataCamp: "OpenClaw takes the day-to-day automation crown. Claude Code wins complex refactoring — it's not close."
-   Alex P. (Medium): "The wrong question is which one wins. One is a scalpel. One is a Swiss Army knife."

---

## Conclusion

Anthropic's concentrated feature release in March 2026 for Claude Code and Claude Desktop represents a direct, strategic counter to the success and disruptive potential of OpenClaw. By shipping native equivalents for remote control (Channels, Dispatch), scheduled tasks (/loop, Desktop Scheduled Tasks), and enhanced memory, Anthropic has largely neutralized OpenClaw's primary value propositions.

While OpenClaw still maintains advantages in true cloud persistence, model agnosticism, and breadth of messaging platform integrations, Claude Code decisively wins on security, coding intelligence, ease of setup, cost efficiency, and enterprise compliance. The CVE-2026-25253 vulnerability in OpenClaw provided Anthropic a crucial opportunity to highlight the risks of community-maintained, self-hosted autonomous agents and position Claude Code as the secure, reliable alternative.

The strategic takeaway is clear: Anthropic has productized validated demand for always-on, remotely controlled AI agents. The consensus among experts suggests that while OpenClaw offered a glimpse into the future of autonomous AI, Claude Code offers the robust, secure, and performant tools necessary for present-day professional development and enterprise use. Developers are increasingly finding a complementary role for both, using Claude Code for core development and adopting OpenClaw-style patterns within Claude Code's ecosystem for specific background automation needs.

---

## Appendix: Feature Quick Reference

| Feature Category | Claude Code/Desktop Feature | Version/Date | OpenClaw Analogue |
|---|---|---|---|
| **Remote Control/Messaging** | Claude Code Channels (Telegram/Discord) | v2.1.80 (Mar 19–20) | OpenClaw messaging integrations |
| | Claude Dispatch (mobile app to desktop) | Early Mar | OpenClaw mobile access |
| | Remote Control (pre-Channels CLI) | Pre-Mar | OpenClaw remote control |
| **Scheduling/Automation** | `/loop` (session-level cron) | v2.1.71 (Mar 7) | OpenClaw heartbeat scheduler |
| | Desktop Scheduled Tasks (persistent) | Early Mar | OpenClaw persistent daemon |
| **Memory** | Auto-Memory w/ Structured Format | v2.1.74/75 | OpenClaw long-term memory (notes) |
| **Core Capabilities** | Opus 4.6 as default, 1M context, `ultrathink` | v2.1.68/75 | Underlying Claude API (used by OpenClaw) |
| **Agent Development** | Skills 2.0 (Structured Evals, Hot Reload, Forked Context) | Mar | OpenClaw Skill.md / community skills |
| **Collaboration** | Multi-Agent Code Review | Mar 10 | N/A (unique to Claude) |
| **Integrations** | Google Workspace (Drive, Docs, Sheets, Slides) | Beta (Mar) | Partial (OpenClaw email/calendar) |
| | Interactive Visualizations | Beta (Mar) | N/A (unique to Claude) |
| **Infrastructure** | MCP Elicitation, PostCompact hook, `modelOverrides` | v2.1.76/73 | N/A (Claude-specific protocol) |
| | ExitWorktree | v2.1.72 | N/A (Claude-specific git workflow) |
