# Brain Agent vs Claude Code / Claude Cowork — Gap Analysis

## Feature Comparison

| Feature | Brain Agent | Claude Code | Claude Cowork | Gap |
|---|---|---|---|---|
| **Hooks system** | None — tool behavior via `tools.md` prompt injection only | 8 hook events (PreToolUse, PostToolUse, etc.) — deterministic shell scripts | Inherits from CC | **Critical** |
| **Permissions model** | Banned command list in `tools.md` (advisory), tool call dedup | Per-tool approval prompts, auto-approve mode, OS-level sandboxing | VM isolation | **Critical** |
| **Plan mode** | None | Three modes: normal, auto-accept, plan (read-only analysis) | N/A | **Medium** |
| **Worktrees** | None | `claude --worktree <name>` — isolated branch + directory | N/A | **Medium** |
| **Project instructions** | `soul.md` + `tools.md` per agent | Hierarchical: global + project `.claude/CLAUDE.md` + `.claude/rules/*.md` | N/A | **Medium** |
| **Agent SDK** | HTTP API only | Python + TypeScript embeddable libraries | N/A | **Medium** |
| **IDE integration** | Web UI, TUI, Telegram | VS Code, JetBrains, Desktop app | N/A | **Low** |
| **Shared sessions** | No multi-user | Single-user | Desktop agent + Dispatch (phone remote) | **Low** |
| **Sub-agents** | `delegate_task` sync/async, teams, scoped delegation | Up to 10 subagents, isolated contexts, tool restrictions | Desktop automation | **Low** (comparable) |
| **Context management** | Auto-compact at 75%, token tracking | Auto-compact at ~167K, background summaries, `/compact` instant | 1M context | **Low** |
| **Tool ecosystem** | 20+ built-in, MCP (stdio+SSE), per-agent MCP | Bash/Read/Edit/Web/Glob/Grep, MCP, 3000+ servers | Desktop tools | **Low** (comparable) |
| **Skill system** | SKILL.md, 7000+ ClawHub, install via search/URL/zip | Unified skills + slash commands, SKILL.md with frontmatter | N/A | **Low** (ahead) |

## Critical Gaps

### 1. Hooks System

Brain Agent has no hooks. Claude Code has deterministic shell scripts that execute before/after tool calls and can block operations.

**Implementation plan:**
- Add `hooks` in `agent.json` with events: `pre_tool_use`, `post_tool_use`, `session_start`, `session_end`
- Each hook: shell command receiving JSON on stdin, exit code controls flow (0=proceed, non-zero=block)
- Wrap tool dispatch in `claude_cli.py` with pre/post hook execution
- Synchronous with configurable timeout (5s default)

### 2. Permissions / Sandboxing

Brain Agent relies on prompt-level instructions (advisory). Claude Code enforces permissions at the engine level with per-tool approval.

**Implementation plan:**
- Add `permissions` in `agent.json` with per-tool config:
  ```json
  {"execute_command": {"mode": "ask", "allow_patterns": ["git *"], "deny_patterns": ["rm -rf *"]}}
  ```
- Modes: `"auto"` (no prompt), `"ask"` (frontend approval via SSE), `"deny"` (blocked)
- Enforce in `claude_cli.py` tool dispatch — engine level, not prompt level
- For sandbox: macOS `sandbox-exec` profiles for Bash subprocess filesystem/network restrictions

## Medium Gaps

### 3. Plan Mode
Add `mode` parameter to `/v1/chat` (`"normal"`, `"plan"`). In plan mode, disable write tools, allow only read tools. Toggle via UI button/shortcut.

### 4. Worktrees
Add `worktree` option to `delegate_task` — creates git worktree, sets agent CWD, cleans up on completion. Builds on existing async delegation.

### 5. Embeddable SDK
Extract core engine from `claude_cli.py` into `brain-agent-sdk` Python package. HTTP server becomes thin wrapper.

### 6. Hierarchical Instructions
Support `.brain/instructions.md` per working directory, auto-loaded when agent operates there.

## Brain Agent Advantages (Claude Code Lacks)

| Feature | Description |
|---|---|
| **Always-on server daemon** | launchd-managed, survives reboots, frontends connect/disconnect freely |
| **Multi-frontend** | Web UI + TUI + Telegram from same server, resume any session from any frontend |
| **Task scheduler** | Built-in cron-like scheduler with per-agent tasks, history, timeout watchdog |
| **Gmail integration** | 5 native Gmail tools with per-agent credentials |
| **Hybrid memory search (QMD)** | BM25 + vector + LLM reranking, per-agent collections, auto-indexing |
| **Agent teams with hierarchy** | Formal teams: heads/members, scoped delegation, scoped shared memory |
| **Multi-provider routing** | Automatic routing across Anthropic, OpenAI-compat, MiniMax, local oMLX, OAuth proxy |
| **Local inference (oMLX)** | MLX on Apple Silicon with SSD KV cache, zero API cost |
| **Web management dashboard** | Server status, QMD health, model routing, Telegram, providers, agent activity |
| **Shared memory with scoping** | `memory_shared(scope="global"|"team")` for cross-agent knowledge sharing |
| **Cloudflare tunnel** | Public access via Zero Trust tunnel |

## Top 3 Actions

1. **Implement hooks** — pre/post tool execution for deterministic control
2. **Implement permissions model** — per-tool approval with allow/deny patterns at engine level
3. **Add plan mode** — read-only analysis mode, easy to implement
