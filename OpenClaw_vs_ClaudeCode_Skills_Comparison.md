# OpenClaw Skills vs Claude Code Skills — Architecture Comparison

## Executive Summary

OpenClaw and Claude Code both use a **skills** abstraction to extend AI agent capabilities, but they serve fundamentally different purposes and embody opposing architectural philosophies. OpenClaw is a **general-purpose autonomous agent framework** (open-source, self-hosted, multi-platform), while Claude Code is a **purpose-built coding agent** (Anthropic-managed, terminal/IDE-native). Their skill systems reflect these differences at every level.

---

## 1. Architecture Overview

### OpenClaw Skills Architecture

OpenClaw uses a **9-layer system prompt architecture** where skills sit at Layer 3 (Skills Registry):

```
Layer 1: Core Instructions (agent DNA — immutable)
Layer 2: Tool Definitions (JSON Schema for all tools)
Layer 3: Skills Registry (auto-discovered, on-demand loading)
Layer 4: Model Aliases
Layer 5: Protocol Specs
Layer 6: Runtime Info
Layer 7: Workspace Files (MEMORY.md, AGENTS.md, IDENTITY.md)
Layer 8: Bootstrap Hooks (dynamic injection)
Layer 9: Inbound Context
```

**Key architectural elements:**
- **Skill = a folder** containing a `SKILL.md` file with YAML frontmatter + Markdown instructions
- **Two-phase loading**: At startup, only skill names/descriptions are injected as a compact list. The full `SKILL.md` is read **on-demand** when the model decides a skill is relevant
- **Declarative capability surface**: `TOOLS.md` (primitives) + `SKILLS.md` (compositions) define everything the agent can do — if it's not listed, the agent can't do it
- **Gateway → Brain → Skills** pipeline: Messages route through a WebSocket gateway, then the Brain (agent runtime) assembles context and enters a ReAct loop
- **ClawHub marketplace**: 5,700+ community skills, installable like apps (but with significant security concerns — 400+ malicious skills found)

```
~/.openclaw/skills/
  └── github-pr-reviewer/
      └── SKILL.md       # Natural language instructions + YAML config
```

### Claude Code Skills Architecture

Claude Code uses a **layered, scoped skill system** integrated directly into the development workflow:

```
Skill Scopes (priority order):
  Enterprise  → Managed org-wide settings
  Personal    → ~/.claude/skills/<name>/SKILL.md
  Project     → .claude/skills/<name>/SKILL.md
  Plugin      → <plugin>/skills/<name>/SKILL.md
```

**Key architectural elements:**
- **Skill = a directory** with a required `SKILL.md` + optional supporting files (scripts, templates, references, examples)
- **Three-phase context loading**:
  1. **Startup**: Only frontmatter metadata (~100 tokens per skill) loaded
  2. **Conversation**: Claude reads descriptions to determine relevance
  3. **Activation**: Full skill content loads when Claude deems it relevant
- **Dual invocation model**: Model-invoked (automatic based on context) OR user-invoked (`/skill-name`)
- **Subagent execution**: Skills can run in isolated forked contexts via `context: fork`
- **Agent Skills open standard**: Interoperable across multiple AI tools
- **Dynamic context injection**: Shell commands can inject live data into prompts before Claude processes them (`!` syntax)

```
.claude/skills/
  └── code-review/
      ├── SKILL.md           # Instructions + YAML frontmatter (required)
      ├── template.md        # Template for output format
      ├── examples/
      │   └── sample.md      # Example output
      └── scripts/
          └── validate.sh    # Executable script
```

---

## 2. Key Differences

| Dimension | OpenClaw | Claude Code |
|---|---|---|
| **Primary purpose** | General-purpose life automation agent | Purpose-built coding agent |
| **Skill definition** | Single `SKILL.md` file per skill | `SKILL.md` + optional supporting files directory |
| **Skill discovery** | Auto-discovered from `~/development/openclaw/skills/` at startup | Auto-discovered from multiple scoped directories with priority hierarchy |
| **Invocation** | Model decides when to read full skill content | Dual: model-invoked (auto) + user-invoked (`/slash-command`) |
| **Context strategy** | Compact skill list injected → full skill read on-demand | Frontmatter metadata at startup → full content on relevance |
| **Skill ecosystem** | ClawHub marketplace (5,700+ skills, community-driven, security risks) | Bundled skills + custom skills + plugins (curated, Anthropic-managed) |
| **Tool restriction** | Skills describe capabilities in natural language | `allowed-tools` frontmatter restricts which tools a skill can use |
| **Execution isolation** | No built-in isolation; runs in agent's main context | `context: fork` runs skill in isolated subagent with separate context window |
| **Model flexibility** | Any LLM provider (OpenAI, Anthropic, Google, Ollama, local) | Claude models only; `model` frontmatter can override per-skill |
| **Supporting files** | Not natively supported — instructions only in SKILL.md | Full directory structure: scripts, templates, references, assets |
| **Lifecycle hooks** | Via Heartbeat (cron-based proactive execution) | `hooks` frontmatter for skill-scoped lifecycle events |
| **Security model** | Self-hosted, user-managed; 512 CVEs found; sandboxing optional | Anthropic-managed sandbox, granular tool permissions, explicit allow/deny |
| **Hosting** | Entirely self-hosted on user hardware | Anthropic infrastructure + local CLI |
| **Multi-platform** | 40+ messaging integrations (WhatsApp, Telegram, Slack, Discord, etc.) | Terminal + VS Code + JetBrains + Xcode only |
| **Memory** | Persistent MEMORY.md + SQLite FTS5 keyword search + semantic search | Fresh per session; CLAUDE.md files for project-level persistence |
| **Cost** | Free (MIT license) + BYO API keys (~$5-150/month) | Claude Pro $20/mo or Max $100-200/mo |

---

## 3. Detailed Comparison

### 3.1 Skill Definition & Structure

**OpenClaw** keeps it minimal:
```yaml
---
name: github-pr-reviewer
description: Review GitHub pull requests and post feedback
---
# GitHub PR Reviewer

When asked to review a pull request:
1. Fetch the PR diff using the GitHub API
2. Analyze each file for code quality issues
3. Post inline comments on specific lines
4. Summarize findings in a PR comment
```

**Claude Code** offers richer structure:
```yaml
---
name: code-review
description: >
  Perform thorough code review following team checklist.
  Use when reviewing code changes, pull requests, or
  when asked to review/audit/check code.
allowed-tools:
  - Read
  - Grep
  - Glob
  - Bash
context: fork
user-invocable: true
model: claude-sonnet-4-6
---
# Code Review Skill

1. **Read the diff** — Understand what changed and why
2. **Check each category** — Security, performance, readability
3. **Reference** — See `references/checklist.md` for full standards
4. **Run validation** — Execute `scripts/validate.sh` for automated checks
```

### 3.2 Context Management

**OpenClaw** uses a **two-tier approach**:
- At the start of each cycle, a compact list of eligible skills (name + description + file path) is injected into the system prompt
- The model reads the full `SKILL.md` on-demand when it decides a skill is relevant
- Total compiled system prompt can exceed **150KB** across all 9 layers
- Context Window Guard monitors token count and triggers summarization before overflow

**Claude Code** uses a **three-tier approach**:
- **Startup**: Only frontmatter metadata loaded (~100 tokens per skill)
- **Conversation**: Claude evaluates descriptions using LLM reasoning (not keyword matching) to determine relevance
- **Activation**: Full SKILL.md body loads when relevant (<5,000 tokens recommended)
- Supporting files (references, scripts) loaded only during execution — no context bloat
- Automatic context compaction when token usage exceeds threshold

### 3.3 Execution Model

**OpenClaw** — ReAct Loop:
```
Receive message → Assemble context (9 layers) → Send to LLM →
Parse for tool calls → Execute tool → Feed result back → Loop until done →
Stream response to user channel
```
- Default serial execution (Lane Queue system) to prevent race conditions
- Skills are consumed as context/instructions, not isolated execution units
- Heartbeat system enables cron-triggered proactive skill execution (e.g., daily briefings)
- MCP server integration for standardized external tool connections

**Claude Code** — Agentic Coding Loop:
```
User prompt → Load relevant skill metadata → Claude reasons about relevance →
Load full skill if needed → Execute with allowed tools →
Optional: fork to subagent → Return result with diffs
```
- Skills can run **inline** (main conversation) or **forked** (isolated subagent context)
- Forked skills don't see main conversation history — prevents hallucination from irrelevant context
- Bundled skills like `/batch` spawn **parallel agents in isolated git worktrees**
- Extended thinking for complex multi-step reasoning within skills

### 3.4 Extensibility

**OpenClaw**:
- Drop a new `SKILL.md` into the skills directory → available on next cycle (zero config)
- ClawHub marketplace for community skills (install like apps)
- MCP server integration for external service connections
- Bootstrap hooks for runtime prompt injection
- Model-agnostic: swap underlying LLM without changing skills

**Claude Code**:
- Create directory in `.claude/skills/` with `SKILL.md` → auto-discovered
- Git-distributed: push skills to repo, team gets them on pull
- Four scope levels (enterprise → personal → project → plugin) with priority
- `--add-dir` for loading skills from additional directories
- Live change detection: edit skills during a session without restarting
- Agent Skills open standard for cross-tool interoperability
- MCP servers for external tool integration

---

## 4. Pros & Cons

### OpenClaw Skills

| Pros | Cons |
|---|---|
| ✅ Model-agnostic — use any LLM provider | ❌ Major security concerns (512 CVEs, 400+ malicious ClawHub skills) |
| ✅ Free & self-hosted — full data sovereignty | ❌ Complex setup (Docker, Python, sandboxing required) |
| ✅ 40+ messaging platform integrations | ❌ No built-in skill isolation or sandboxing |
| ✅ Persistent memory across sessions (automatic) | ❌ No supporting files structure — instructions only |
| ✅ Heartbeat for proactive/scheduled execution | ❌ No tool restriction per skill |
| ✅ Massive community ecosystem (5,700+ skills) | ❌ Quality varies wildly in marketplace |
| ✅ Simple skill format (single Markdown file) | ❌ Limited code-specific capabilities |
| ✅ Cross-platform life automation | ❌ User responsible for all security |

### Claude Code Skills

| Pros | Cons |
|---|---|
| ✅ Rich skill structure (scripts, templates, references) | ❌ Claude models only — no model flexibility |
| ✅ Granular tool permissions (`allowed-tools`) | ❌ Paid service ($20-200/month) |
| ✅ Subagent isolation (`context: fork`) | ❌ Fresh context each session (no automatic memory) |
| ✅ Dual invocation (auto + manual `/command`) | ❌ Terminal/IDE only — no messaging platform support |
| ✅ Sandboxed, Anthropic-managed security | ❌ Smaller skill ecosystem (no marketplace) |
| ✅ Deep codebase understanding and context | ❌ Coding-focused — not for general automation |
| ✅ Git-native distribution for teams | ❌ Depends on Anthropic infrastructure |
| ✅ Open standard (Agent Skills) for interoperability | ❌ Some features still maturing (fork bugs reported) |
| ✅ Lifecycle hooks scoped to skills | |
| ✅ Four-level scoping hierarchy | |

---

## 5. Philosophical Differences

| Aspect | OpenClaw | Claude Code |
|---|---|---|
| **Agent philosophy** | AI as autonomous operator — plans, decides, acts independently | AI as guided collaborator — assists within human-controlled workflow |
| **Skill philosophy** | Skills = modular units of competence replacing monolithic prompts | Skills = specialized knowledge packages that enhance domain expertise |
| **Architecture metaphor** | Swiss Army knife — breadth of integration | Surgical scalpel — depth of capability |
| **Trust model** | Open marketplace, user-curated, community-driven | Curated, Anthropic-managed, security-first |
| **Transparency** | Full transparency — plain text files, auditable, version-controlled | Similar transparency but within Anthropic's managed environment |
| **Target user** | Power users wanting AI-powered life automation | Developers wanting AI-powered coding assistance |

---

## 6. When to Use Which

**Choose OpenClaw Skills when:**
- You need cross-platform automation (messaging, email, calendars, smart home)
- You want full control over hosting and data sovereignty
- You need proactive/scheduled agent execution (Heartbeat)
- You want to use different LLM providers for different tasks
- You're building autonomous workflows with minimal human involvement

**Choose Claude Code Skills when:**
- You're doing software development (writing, refactoring, reviewing code)
- You need deep codebase understanding and multi-file editing
- Security and sandboxing are critical
- You want team-wide standardized coding practices via Git
- You need subagent isolation for complex workflows
- You want rich skill packages with scripts, templates, and references

**Use both when:**
- You want Claude Code for all development work AND OpenClaw for non-coding life automation
- They are complementary tools, not competitors

---

*Research compiled: July 2025. Sources: Official documentation, architecture deep-dives, community analysis, and comparison guides.*
