# Feature: Git Worktree Isolation for Agent Tasks

**Status**: Proposal
**Effort**: ~8 days
**Priority**: High
**Affects**: claude_cli.py, server.py, web/index.html, tui.py

---

## Problem

When multiple agents work on code simultaneously via `delegate_task`, they all share
the same working directory. Agent A editing `server.py` while Agent B refactors
`claude_cli.py` creates race conditions: partial writes, conflicting imports, broken
intermediate states. There is no isolation boundary between concurrent coding tasks.

Claude Code solves this with git worktrees -- each task gets its own branch and
directory. Brain Agent's delegation system needs the same capability.

### Current Behavior

```
agents/Coder/  (task: refactor auth)     ──┐
agents/Coder/  (task: add API endpoint)  ──┤──► same working directory
agents/Coder/  (task: fix CSS bug)       ──┘    files collide
```

### Desired Behavior

```
.brain/worktrees/task-a1b2c3/  (refactor auth)     ──► isolated branch
.brain/worktrees/task-d4e5f6/  (add API endpoint)  ──► isolated branch
.brain/worktrees/task-g7h8i9/  (fix CSS bug)       ──► isolated branch
```

---

## Proposed Solution

### 1. delegate_task Gets a `worktree` Option

When the orchestrating agent delegates a coding task, it can request worktree
isolation. The system handles all git plumbing automatically.

```
Tool call: delegate_task
┌─────────────────────────────────────────────────────┐
│  agent:     "Coder"                                 │
│  task:      "Refactor auth module to use JWT"       │
│  worktree:  true                                    │
│  base:      "main"          (optional, default HEAD)│
│  branch:    "refactor-auth" (optional, auto-named)  │
└─────────────────────────────────────────────────────┘
```

If `worktree` is omitted or false, behavior is unchanged (backward compatible).

### 2. Directory Structure

```
project-root/
├── .brain/
│   └── worktrees/
│       ├── task-a1b2c3/            ← git worktree checkout
│       │   ├── server.py           ← independent copy
│       │   ├── claude_cli.py
│       │   ├── web/
│       │   └── ...
│       ├── task-d4e5f6/
│       │   └── ...
│       └── .gitkeep
├── server.py                       ← main branch (untouched)
├── claude_cli.py
└── ...
```

Each worktree is a full checkout on its own branch. The main working directory
stays clean while agents work in isolation.

### 3. Sequence Diagram

```
User           main agent       System            Coder agent
  │                │                │                  │
  │  "refactor     │                │                  │
  │   auth module" │                │                  │
  │───────────────>│                │                  │
  │                │                │                  │
  │                │  delegate_task │                  │
  │                │  worktree=true │                  │
  │                │───────────────>│                  │
  │                │                │                  │
  │                │                │  git worktree    │
  │                │                │  add .brain/     │
  │                │                │  worktrees/      │
  │                │                │  task-a1b2c3     │
  │                │                │  -b task/a1b2c3  │
  │                │                │                  │
  │                │                │  set cwd for     │
  │                │                │  delegate thread  │
  │                │                │─────────────────>│
  │                │                │                  │
  │                │                │                  │  read_file
  │                │                │                  │  edit_file
  │                │                │                  │  execute_cmd
  │                │                │                  │  (all scoped to
  │                │                │                  │   worktree dir)
  │                │                │                  │
  │                │                │    task complete  │
  │                │                │<─────────────────│
  │                │                │                  │
  │                │                │  git add + commit│
  │                │                │  in worktree     │
  │                │                │                  │
  │                │  task_status:  │                  │
  │                │  completed     │                  │
  │                │  branch:       │                  │
  │                │  task/a1b2c3   │                  │
  │                │  diff: +142    │                  │
  │                │       -37 lines│                  │
  │                │<───────────────│                  │
  │                │                │                  │
  │  "looks good,  │                │                  │
  │   merge it"    │                │                  │
  │───────────────>│                │                  │
  │                │  merge_worktree│                  │
  │                │───────────────>│                  │
  │                │                │  git merge       │
  │                │                │  task/a1b2c3     │
  │                │                │  into main       │
  │                │                │                  │
  │                │                │  git worktree    │
  │                │                │  remove ...      │
  │                │                │                  │
  │                │                │  git branch -d   │
  │                │                │  task/a1b2c3     │
  │                │                │                  │
  │                │  merged + clean│                  │
  │                │<───────────────│                  │
  │  "merged       │                │                  │
  │   successfully"│                │                  │
  │<───────────────│                │                  │
```

### 4. Web UI: Task Status with Worktree Info

```
┌─────────────────────────────────────────────────────────────────┐
│  Active Tasks                                            [view] │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  Task a1b2c3 — Refactor auth module           [running]  │  │
│  │  Agent: Coder                                             │  │
│  │  Branch: task/a1b2c3                                      │  │
│  │  Worktree: .brain/worktrees/task-a1b2c3/                  │  │
│  │  Files changed: 4  (+142 / -37)                           │  │
│  │  Started: 2 min ago                                       │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                 │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  Task d4e5f6 — Add /v1/export endpoint      [completed]  │  │
│  │  Agent: Coder                                             │  │
│  │  Branch: task/d4e5f6                                      │  │
│  │  Worktree: .brain/worktrees/task-d4e5f6/                  │  │
│  │  Files changed: 2  (+58 / -3)                             │  │
│  │                                                           │  │
│  │  ┌──────────────┐  ┌──────────────┐  ┌────────────────┐  │  │
│  │  │  View Diff   │  │    Merge     │  │    Discard     │  │  │
│  │  └──────────────┘  └──────────────┘  └────────────────┘  │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                 │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  Task g7h8i9 — Fix sidebar CSS              [no worktree] │  │
│  │  Agent: Coder                                             │  │
│  │  (standard delegation, no isolation)                      │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### 5. Web UI: Merge Worktree Dialog

When user clicks "Merge" on a completed worktree task:

```
┌─────────────────────────────────────────────────────────────────┐
│  Merge Worktree: task/d4e5f6                              [x]  │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  Branch: task/d4e5f6 → main                                    │
│  Commits: 3                                                     │
│  Files changed: 2                                               │
│                                                                 │
│  ┌─────────────────────────────────────────────────────────┐    │
│  │  server.py                                 +45 / -2     │    │
│  │  ─────────────────────────────────────────────────────  │    │
│  │  @@ -340,6 +340,51 @@                                  │    │
│  │  +@app.route('/v1/export', methods=['POST'])            │    │
│  │  +def handle_export():                                  │    │
│  │  +    """Export chat history in various formats."""      │    │
│  │  +    data = request.json                               │    │
│  │  +    format = data.get('format', 'json')               │    │
│  │  +    ...                                               │    │
│  │                                                         │    │
│  │  web/index.html                            +13 / -1     │    │
│  │  ─────────────────────────────────────────────────────  │    │
│  │  @@ -1200,6 +1200,18 @@                                │    │
│  │  +<button onclick="exportChat()">Export</button>        │    │
│  │  +...                                                   │    │
│  └─────────────────────────────────────────────────────────┘    │
│                                                                 │
│  ┌──────────────────────┐  ┌───────────────────────────────┐    │
│  │   Merge & Cleanup    │  │         Cancel                │    │
│  └──────────────────────┘  └───────────────────────────────┘    │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

### 6. TUI: /tasks with Worktree Info

```
┌─ Active Tasks ──────────────────────────────────────────────────┐
│                                                                 │
│  ID       Agent    Status     Branch           Files   Age      │
│  ──────── ──────── ────────── ──────────────── ─────── ──────── │
│  a1b2c3   Coder    running    task/a1b2c3      4 files 2m       │
│  d4e5f6   Coder    completed  task/d4e5f6      2 files 5m       │
│  g7h8i9   Coder    running    (no worktree)    --      1m       │
│                                                                 │
│  Commands:                                                      │
│    /tasks merge d4e5f6     Merge completed worktree             │
│    /tasks diff a1b2c3      Show current diff                    │
│    /tasks discard d4e5f6   Delete worktree and branch           │
│    /tasks cancel a1b2c3    Cancel task + cleanup worktree       │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## Detailed Workflow

### Step 1: User Requests Work

User tells the main agent:

> "Refactor the auth module to use JWT tokens instead of session cookies"

### Step 2: Main Agent Delegates with Worktree

Main agent recognizes this as a code change that benefits from isolation and calls:

```json
{
  "tool": "delegate_task",
  "input": {
    "agent": "Coder",
    "task": "Refactor auth module to use JWT tokens. Replace session cookie auth in server.py with JWT. Update web UI to send Bearer token.",
    "worktree": true
  }
}
```

### Step 3: System Creates Worktree

Before spawning the delegate thread, the system:

```bash
# Generate task ID
TASK_ID="a1b2c3"

# Create worktree with task branch
git worktree add .brain/worktrees/task-$TASK_ID -b task/$TASK_ID

# Set the delegate thread's working directory
os.chdir(".brain/worktrees/task-$TASK_ID")
```

### Step 4: Agent Works in Isolation

The Coder agent's `execute_command`, `read_file`, `write_file`, and `edit_file`
tools all operate relative to the worktree directory. The agent cannot see or
modify files on the main branch.

### Step 5: Task Completes

When the delegate finishes, the system auto-commits any uncommitted changes in
the worktree:

```bash
cd .brain/worktrees/task-a1b2c3
git add -A
git commit -m "task/a1b2c3: Refactor auth module to JWT"
```

### Step 6: User Reviews and Merges

The user sees the completed task in the UI, reviews the diff, and clicks Merge.
The system runs:

```bash
git checkout main
git merge task/a1b2c3
git worktree remove .brain/worktrees/task-a1b2c3
git branch -d task/a1b2c3
```

If the user clicks Discard instead:

```bash
git worktree remove .brain/worktrees/task-a1b2c3 --force
git branch -D task/a1b2c3
```

---

## Implementation Details

### New Tools

| Tool | Description |
|------|-------------|
| `merge_worktree` | Merge a completed worktree branch into target branch |
| `discard_worktree` | Delete worktree and branch without merging |

### New API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/v1/worktrees` | GET | List active worktrees with status and diff stats |
| `/v1/worktrees/<task_id>/diff` | GET | Full diff for a worktree |
| `/v1/worktrees/<task_id>/merge` | POST | Merge worktree into target branch |
| `/v1/worktrees/<task_id>/discard` | POST | Remove worktree and delete branch |

### Changes to Existing Code

**claude_cli.py**:
- `_run_delegate()`: if `worktree=true`, create worktree before spawning thread,
  set thread-local `cwd` to worktree path
- `execute_command()`: respect thread-local `cwd` override
- `read_file()`, `write_file()`, `edit_file()`: resolve paths relative to
  thread-local `cwd` when in worktree mode
- New `_create_worktree(task_id, base_branch)` helper
- New `_cleanup_worktree(task_id)` helper

**server.py**:
- Add `/v1/worktrees` endpoints
- Extend `/v1/chat` SSE events to include worktree metadata in task updates

**web/index.html**:
- Task panel: show branch name, worktree path, diff stats
- Merge dialog with diff preview
- Discard confirmation dialog

**tui.py**:
- `/tasks` command: show worktree column
- `/tasks merge <id>`, `/tasks diff <id>`, `/tasks discard <id>` subcommands

---

## Benefits

- **No conflicts**: Agents cannot interfere with each other's work or the main branch
- **Safe parallel work**: Three agents can edit the same file on different branches
- **Easy rollback**: Discard a worktree and it is as if the work never happened
- **Familiar model**: Developers already understand git branches and merges
- **Incremental adoption**: `worktree: true` is opt-in per task, no breaking changes

## Trade-offs

- **Disk space**: Each worktree is a full checkout (~size of repo, minus .git).
  For a 100MB repo with 5 concurrent worktrees, that is 500MB extra
- **Merge conflicts**: If two agents edit the same file on different branches,
  merging the second one may conflict. The system should detect this and present
  the conflict to the user rather than silently failing
- **File creation outside worktree**: If an agent calls `execute_command` with an
  absolute path outside the worktree, isolation is broken. Mitigation: the system
  should validate and warn (but not hard-block, since some tools need /tmp etc.)
- **Large repos**: Cloning worktrees for very large repos is slow. Could use
  `git worktree add --no-checkout` and sparse checkout for large repos
- **Memory files**: Agent memory (agents/<name>/*.md) should NOT be in the worktree.
  Memory writes must always go to the real agent directory, not the worktree copy

## Effort Estimate

| Component | Days |
|-----------|------|
| Worktree creation/cleanup in claude_cli.py | 2 |
| Thread-local CWD scoping for tools | 1 |
| merge_worktree / discard_worktree tools | 1 |
| Server API endpoints | 1 |
| Web UI task panel + merge dialog | 2 |
| TUI /tasks subcommands | 0.5 |
| Testing + edge cases (conflicts, cleanup) | 0.5 |
| **Total** | **8** |

## Open Questions

1. Should the main agent auto-detect when worktree isolation is beneficial, or
   should the user/agent always explicitly request it?
2. How to handle worktrees that outlive a server restart? On startup, scan
   `.brain/worktrees/` and reconcile with task state.
3. Should there be a maximum number of concurrent worktrees? Disk space could
   balloon with many parallel tasks.
4. When a merge conflict occurs, should the system attempt auto-resolution or
   always punt to the user?
