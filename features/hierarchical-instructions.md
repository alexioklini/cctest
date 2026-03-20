# Feature: Hierarchical Project Instructions

**Status**: Proposal
**Effort**: ~4 days
**Priority**: High
**Affects**: claude_cli.py, server.py, web/index.html, tui.py

---

## Problem

Brain Agent loads `soul.md` (personality/role) and `tools.md` (tool guidance) per
agent, but these are agent-global. When an agent works across multiple projects --
a React frontend, a Python backend, an infrastructure repo -- it has no way to
pick up project-specific conventions automatically.

Claude Code solves this by loading `.claude/CLAUDE.md` files from the working
directory. Brain Agent needs an equivalent: when an agent operates in a directory,
project-specific instructions should be injected into the context automatically.

### Current Behavior

```
Agent reads/writes files in ~/projects/my-react-app/
  └── No project context. Agent uses generic soul.md only.
      Does not know: "Use TypeScript", "Tests go in __tests__/",
      "Use Tailwind, not inline styles"
```

### Desired Behavior

```
Agent reads/writes files in ~/projects/my-react-app/
  └── .brain/instructions.md auto-loaded into context
      Agent now knows: "Use TypeScript", "Tests go in __tests__/",
      "Use Tailwind, not inline styles"
```

---

## Proposed Solution

### 1. The `.brain/instructions.md` File

Any directory can contain a `.brain/instructions.md` file with project-specific
guidance. When an agent's tools (read_file, write_file, edit_file, execute_command,
list_directory, search_files) operate in that directory, the instructions are
discovered and loaded into the system prompt.

Example file at `~/projects/my-react-app/.brain/instructions.md`:

```markdown
# Project: My React App

## Stack
- React 19 with TypeScript (strict mode)
- Tailwind CSS for styling (no inline styles, no CSS modules)
- Vite for bundling
- Vitest for testing

## Conventions
- Components in src/components/ as PascalCase.tsx
- Hooks in src/hooks/ as useCamelCase.ts
- Tests colocated: src/components/__tests__/ComponentName.test.tsx
- Use `interface` for props, not `type`
- All API calls go through src/api/client.ts

## File Structure
src/
  components/     UI components
  hooks/          Custom hooks
  api/            API client and types
  pages/          Route pages
  utils/          Pure utility functions

## Do NOT
- Use `any` type
- Import from relative paths more than 2 levels up (use @/ alias)
- Add new npm dependencies without noting why
```

### 2. Directory Tree Discovery

Instructions are discovered by walking up from the file being accessed:

```
/Users/alex/projects/my-react-app/src/components/Button.tsx
                                                          │
  Walk up:                                                │
    src/components/.brain/instructions.md  ← not found    │
    src/.brain/instructions.md             ← not found    │
    .brain/instructions.md                 ← FOUND        │
    /Users/alex/projects/.brain/instructions.md ← also    │
    /Users/alex/.brain/instructions.md     ← also         │
                                                          │
  Stop at: filesystem root or home directory               │
```

All found instruction files are loaded, from most general (highest directory)
to most specific (closest to the file). This allows layered instructions:

```
~/.brain/instructions.md                     Global personal preferences
  └── ~/projects/.brain/instructions.md      All projects conventions
        └── ~/projects/my-app/.brain/        This specific project
              instructions.md
```

### 3. How Instructions Stack in the System Prompt

```
┌─────────────────────────────────────────────────────────────┐
│  System Prompt Assembly                                     │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  1. soul.md (agent personality and role)                    │
│     ─────────────────────────────────────                   │
│     "You are the Coder agent. You write clean,              │
│      well-tested code..."                                   │
│                                                             │
│  2. Project instructions (auto-discovered, stacked)         │
│     ─────────────────────────────────────                   │
│     [~/.brain/instructions.md]                              │
│     "I prefer concise code with minimal comments..."        │
│                                                             │
│     [~/projects/my-app/.brain/instructions.md]              │
│     "This is a React 19 + TypeScript app. Use Tailwind..."  │
│                                                             │
│  3. tools.md (tool usage guide)                             │
│     ─────────────────────────────────────                   │
│     "When using execute_command, never run interactive..."   │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

**Precedence order** (highest priority first):

1. `soul.md` -- agent identity is non-negotiable
2. Project `instructions.md` -- specific > general (inner overrides outer)
3. `tools.md` -- tool constraints apply universally

If a project instruction conflicts with soul.md (e.g., "ignore your role"),
soul.md wins. If two instruction files at different levels conflict, the more
specific (deeper) one wins.

### 4. Web UI: Project Instructions Indicator

When instructions are loaded, the Web UI shows an indicator in the chat header:

```
┌─────────────────────────────────────────────────────────────────┐
│  Coder                                             claude-sonnet│
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  /projects/my-react-app/.brain/instructions.md loaded     │  │
│  │  (React 19 + TypeScript project)                     [x]  │  │
│  └───────────────────────────────────────────────────────────┘  │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  User: Add a dark mode toggle to the navbar                     │
│                                                                 │
│  Coder: I'll create a DarkModeToggle component following        │
│  the project conventions (TypeScript, Tailwind, colocated       │
│  tests).                                                        │
│                                                                 │
│  [read_file] src/components/Navbar.tsx                          │
│  [write_file] src/components/DarkModeToggle.tsx                 │
│  [write_file] src/components/__tests__/DarkModeToggle.test.tsx  │
│  [edit_file] src/components/Navbar.tsx                          │
│                                                                 │
│  Created DarkModeToggle.tsx using Tailwind classes and          │
│  added a Vitest test file in __tests__/ as per project          │
│  conventions.                                                   │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### 5. Web UI: Instructions Editor

Clicking the instructions indicator opens an editor:

```
┌─────────────────────────────────────────────────────────────────┐
│  Project Instructions                                     [x]  │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  Path: /Users/alex/projects/my-react-app/.brain/instructions.md │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │  # Project: My React App                                │    │
│  │                                                         │    │
│  │  ## Stack                                               │    │
│  │  - React 19 with TypeScript (strict mode)               │    │
│  │  - Tailwind CSS for styling                             │    │
│  │  - Vite for bundling                                    │    │
│  │  - Vitest for testing                                   │    │
│  │                                                         │    │
│  │  ## Conventions                                         │    │
│  │  - Components in src/components/ as PascalCase.tsx      │    │
│  │  - Tests colocated in __tests__/                        │    │
│  │  |                                                      │    │
│  └─────────────────────────────────────────────────────────┘    │
│                                                                 │
│  Also loaded (read-only):                                       │
│    ~/.brain/instructions.md (global)                            │
│                                                                 │
│  ┌──────────────┐  ┌──────────────┐                             │
│  │     Save     │  │    Cancel    │                             │
│  └──────────────┘  └──────────────┘                             │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### 6. TUI: /instructions Command

```
┌─ /instructions ─────────────────────────────────────────────────┐
│                                                                 │
│  Loaded instruction files (in precedence order):                │
│                                                                 │
│  1. [global]  ~/.brain/instructions.md                          │
│     "Personal coding preferences: concise style, minimal..."   │
│     (12 lines)                                                  │
│                                                                 │
│  2. [project] ~/projects/my-react-app/.brain/instructions.md   │
│     "React 19 + TypeScript project. Tailwind CSS, Vitest..."   │
│     (34 lines)                                                  │
│                                                                 │
│  Working directory: ~/projects/my-react-app/                    │
│                                                                 │
│  Commands:                                                      │
│    /instructions edit          Open instructions.md in $EDITOR  │
│    /instructions create        Create .brain/instructions.md    │
│    /instructions show          Print full contents              │
│    /instructions clear         Remove from current context      │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## Detailed Workflow

### Step 1: User Creates Instructions

User creates `.brain/instructions.md` in their project directory. This can be
done manually, via the Web UI editor, or by asking the agent:

> "Create a .brain/instructions.md for this project based on what you see"

The agent reads the project structure and generates appropriate instructions.

### Step 2: Agent Discovers Instructions

When the agent's tools access files in that directory, the instruction discovery
runs. This happens on the first tool call that touches the filesystem in a given
directory -- results are cached for the rest of the conversation.

```python
# Pseudocode for discovery
def discover_instructions(file_path: str) -> list[str]:
    instructions = []
    current = Path(file_path).parent
    home = Path.home()

    while current != current.parent and current >= home:
        candidate = current / ".brain" / "instructions.md"
        if candidate.exists():
            instructions.append(candidate.read_text())
        current = current.parent

    # Reverse: general first, specific last
    return list(reversed(instructions))
```

### Step 3: Instructions Injected into Context

The discovered instructions are inserted into the system prompt between `soul.md`
and `tools.md`. They are wrapped with clear delimiters so the agent knows their
source:

```
<project-instructions path="/Users/alex/projects/my-react-app/.brain/instructions.md">
# Project: My React App
## Stack
- React 19 with TypeScript...
</project-instructions>
```

### Step 4: Agent Follows Project Conventions

With instructions in context, the agent naturally follows project conventions.
When asked to "add a dark mode toggle," it knows to use TypeScript, Tailwind,
and put tests in `__tests__/` without being told each time.

### Step 5: Different Projects, Different Instructions

When the agent switches to working in a different directory (e.g., via a new
task or explicit directory change), the instructions update automatically:

```
Working in ~/projects/my-react-app/  → React/TS instructions loaded
Working in ~/projects/api-backend/   → Python/FastAPI instructions loaded
Working in ~/projects/infra/         → Terraform instructions loaded
```

---

## Surviving Context Compaction

Brain Agent compacts context at 75% of the context window. Instructions must
survive compaction because they are part of the system prompt, not the
conversation history.

**Strategy**: Instructions are stored in the system prompt assembly, not as
user/assistant messages. The system prompt is never compacted -- it is always
included in full. This means instructions persist across compaction events
automatically.

**Size limit**: To prevent instructions from consuming too much of the context
window, enforce a maximum size:

- Single instructions.md: 2000 tokens max (roughly 100 lines of markdown)
- Total stacked instructions: 4000 tokens max
- If exceeded: truncate with a note: "[instructions truncated, {N} lines omitted]"

---

## Implementation Details

### Changes to Existing Code

**claude_cli.py**:
- New `_discover_instructions(file_path)` function: walks up directory tree,
  finds `.brain/instructions.md` files, returns list of (path, content) tuples
- New `_instructions_cache`: dict mapping directory paths to discovered
  instructions. Cleared on new conversation. Thread-local to avoid conflicts
  between concurrent agent tasks.
- Modify `_build_system_prompt()`: insert discovered instructions between
  soul.md and tools.md sections
- Modify file tool functions (`_tool_read_file`, `_tool_write_file`, etc.):
  call `_discover_instructions()` on first file access, trigger system prompt
  update if new instructions found
- New `_instructions_for_cwd()`: resolve instructions for the current working
  directory (used by execute_command)

**server.py**:
- New endpoint `GET /v1/instructions?path=<dir>`: returns discovered instructions
  for a given directory path
- New endpoint `PUT /v1/instructions`: save instructions.md content to a path
- Extend `/v1/chat` SSE events: emit `instructions_loaded` event when new
  project instructions are discovered mid-conversation

**web/index.html**:
- Instructions indicator bar below chat header (shows when instructions active)
- Click indicator to open instructions editor modal
- Listen for `instructions_loaded` SSE events to update indicator
- Instructions editor with save/cancel buttons

**tui.py**:
- `/instructions` command: list, show, edit, create, clear
- Status bar update when instructions are loaded

### New Files

| File | Purpose |
|------|---------|
| `.brain/instructions.md` | Per-directory project instructions (user-created) |

No new Python files needed -- all logic fits in existing modules.

### Discovery Caching

To avoid re-scanning the filesystem on every tool call:

```
_instructions_cache = {}  # thread-local

def _discover_instructions(file_path):
    dir_path = os.path.dirname(os.path.abspath(file_path))

    if dir_path in _instructions_cache:
        return _instructions_cache[dir_path]

    instructions = _walk_up_for_instructions(dir_path)
    _instructions_cache[dir_path] = instructions

    # Also cache all parent directories we checked
    current = dir_path
    while current != os.path.dirname(current):
        if current not in _instructions_cache:
            _instructions_cache[current] = _walk_up_for_instructions(current)
        current = os.path.dirname(current)

    return instructions
```

Cache is invalidated when:
- A new conversation starts
- User explicitly runs `/instructions clear`
- An instructions.md file is written or deleted via agent tools

---

## Benefits

- **Per-project context**: Each project gets its own conventions without manual
  prompting every conversation
- **Scales across repos**: Work in 10 different projects and each one has its
  own instructions
- **No manual switching**: Instructions load automatically based on where the
  agent is working
- **Familiar pattern**: Same concept as `.claude/CLAUDE.md`, `.editorconfig`,
  `.eslintrc` -- config that lives with the project
- **Team sharing**: Instructions checked into git are shared with the whole team.
  Every developer's Brain Agent follows the same project conventions
- **Layered overrides**: Global preferences + project-specific rules + optional
  subdirectory overrides

## Trade-offs

- **Context window cost**: Instructions consume tokens from the system prompt.
  With a 2000-token limit per file and 4000 total, this is manageable but not
  free. Agents with large soul.md + tools.md + project instructions may hit
  context pressure sooner.
- **Discovery overhead**: Walking up the directory tree on every new directory
  adds latency. Mitigated by caching, but first access to a new directory
  takes an extra few milliseconds of filesystem scanning.
- **Stale instructions**: If a user edits instructions.md outside of Brain Agent
  (e.g., in their editor), the cached version may be stale until a new
  conversation starts. Could add file mtime checking to the cache.
- **Security**: Instructions in a cloned repo could contain adversarial prompts
  (prompt injection). Mitigation: wrap instructions with clear delimiters so
  the agent knows they are project instructions, not system instructions. The
  soul.md always takes precedence.
- **Subdirectory instructions**: Supporting instructions at every directory level
  (monorepo packages, nested subprojects) adds complexity. Start with one level
  of discovery (walk up to find the first `.brain/instructions.md`), expand to
  multi-level stacking later if needed.

## Effort Estimate

| Component | Days |
|-----------|------|
| Discovery logic + caching in claude_cli.py | 1 |
| System prompt assembly changes | 0.5 |
| Server API endpoints | 0.5 |
| Web UI indicator + editor modal | 1 |
| TUI /instructions command | 0.5 |
| Testing + edge cases | 0.5 |
| **Total** | **4** |

## Open Questions

1. Should the discovery walk up to the filesystem root or stop at the home
   directory? Stopping at home prevents loading instructions from `/etc/.brain/`
   or similar system directories.
2. Should instructions be agent-specific? E.g., `.brain/instructions.coder.md`
   for the Coder agent only. This adds flexibility but also complexity.
3. How to handle monorepos where different packages have different conventions?
   The stacking approach (multiple instructions.md at different levels) handles
   this, but is it intuitive enough?
4. Should the agent be able to modify project instructions autonomously, or
   should writes require user confirmation?
5. What about `.brain/ignore` -- a file listing patterns the agent should not
   read or modify? Similar to `.gitignore` but for agent access control. This
   could be a separate feature but pairs naturally with instructions.
