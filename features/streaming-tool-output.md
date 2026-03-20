# Feature Proposal: Streaming Tool Output

**Status:** Proposed
**Effort:** ~5 days
**Priority:** High
**Author:** Brain Agent Team
**Date:** 2026-03-20

---

## Problem Statement

When an agent runs a long-running command via `execute_command` (build, test suite,
deployment, data processing), the user sees nothing until it completes. The entire
tool result arrives as a single block after the process exits.

For commands taking 30+ seconds, this creates real problems:

- **Anxiety**: Is it stuck? Did it crash? Should I cancel?
- **No feedback loop**: User cannot spot errors early and abort
- **Wasted time**: A failing build runs to completion before the agent sees the error
- **Poor UX**: Competing tools (Claude Code, Cursor) stream command output in real-time

Currently, SSE keepalive comments (every 5s) prevent browser timeout, but they carry
no payload. The user stares at a spinner with zero information about what is happening.

---

## Proposed Solution

Stream command stdout/stderr in real-time to all connected frontends (Web UI, TUI,
Telegram) while the command runs. Show live output in an expandable terminal-like
panel in the UI.

### Core Changes

1. **subprocess.Popen with PIPE** instead of subprocess.run in `execute_command`
2. **Line-by-line read loop** emitting SSE events as output arrives
3. **New SSE event type** `tool_output` carrying incremental chunks
4. **UI rendering** of live terminal output in all frontends
5. **Buffered result** still passed to the LLM as the final tool result

---

## SSE Protocol Extension

New event type alongside existing `content`, `tool_use`, `tool_result`:

```
event: tool_output
data: {
  "tool_use_id": "toolu_abc123",
  "name": "execute_command",
  "stream": "stdout",
  "chunk": "PASS src/auth.test.ts (2.1s)\n",
  "seq": 42
}

event: tool_output
data: {
  "tool_use_id": "toolu_abc123",
  "name": "execute_command",
  "stream": "stderr",
  "chunk": "Warning: deprecated API usage in line 55\n",
  "seq": 43
}
```

Fields:
- `tool_use_id` — correlates output to the specific tool call
- `name` — tool name (always "execute_command" for now, extensible)
- `stream` — "stdout" or "stderr" for color coding
- `chunk` — one or more lines of output text
- `seq` — monotonic sequence number for ordering

---

## Architecture

```
execute_command("npm test")
       |
       v
  subprocess.Popen(cmd, stdout=PIPE, stderr=PIPE)
       |
       v
  Thread 1: read stdout line-by-line ──> queue
  Thread 2: read stderr line-by-line ──> queue
       |
       v
  Main loop: drain queue ──> emit SSE tool_output events
       |                      (batched: max 10 lines or 100ms)
       v
  Process exits ──> final tool_result with full output
       |
       v
  LLM sees complete output (unchanged behavior)
```

### Buffering Strategy

- Read threads use `readline()` for line-buffered output
- Queue drains every 100ms or when 10 lines accumulate (whichever first)
- Chunks are batched into a single SSE event to reduce overhead
- If output exceeds 50KB total, streaming continues but final tool_result
  is truncated to last 20KB (same as current truncation behavior)
- Binary/non-UTF8 output: replace invalid bytes, do not stream raw binary

### Megabyte Output Protection

Commands like `find /` or verbose builds can produce megabytes of output.

```
Output < 50KB     Full streaming, full tool_result
50KB - 200KB      Stream last 50KB only (ring buffer), tool_result truncated
> 200KB           Stop streaming, show "[output truncated, 1.2MB total]"
                  Tool_result gets last 20KB only
```

---

## Web UI Mockups

### Collapsed View (Default)

```
+----------------------------------------------------------------------+
| [>] execute_command                                          [12.4s] |
|                                                                      |
|   Running: npm test                              [...] (in progress) |
|                                                                      |
+----------------------------------------------------------------------+
```

The `[>]` chevron indicates expandable content. The `[...]` is an animated
spinner. Elapsed time updates every second.

### Expanded View (Click to Expand)

```
+----------------------------------------------------------------------+
| [v] execute_command                                          [12.4s] |
|                                                                      |
|   Running: npm test                              [...] (in progress) |
|                                                                      |
|   +----------------------------------------------------------------+ |
|   | $ npm test                                                     | |
|   |                                                                | |
|   | > brain-agent@1.5.1 test                                      | |
|   | > jest --verbose                                               | |
|   |                                                                | |
|   | PASS src/utils.test.ts (0.8s)                                  | |
|   |   + sanitize input                                             | |
|   |   + handle empty string                                        | |
|   |   + escape special chars                                       | |
|   |                                                                | |
|   | PASS src/auth.test.ts (2.1s)                                   | |
|   |   + login with valid creds                                     | |
|   |   + reject invalid token                                       | |
|   |                                                                | |
|   | FAIL src/api.test.ts (3.4s)                                    | |
|   |   x should return 200 for /health          <-- red highlight   | |
|   |     Expected: 200                                              | |
|   |     Received: 503                                              | |
|   |                                                                | |
|   | Tests: 1 failed, 14 passed, 15 total        <-- auto-scrolls  | |
|   +----------------------------------------------------------------+ |
|                                                                      |
+----------------------------------------------------------------------+
```

Terminal panel features:
- Dark background (#1e1e1e) with monospace font
- Auto-scrolls to bottom as new output arrives
- stderr lines shown in orange/red
- Max height: 400px with scroll, expandable to full viewport
- User can scroll up without losing auto-scroll (re-engages on new output
  only if user is within 2 lines of bottom)

### Completion State

```
+----------------------------------------------------------------------+
| [v] execute_command                                   [Exit: 1] 34s  |
|                                                                      |
|   Completed: npm test                                    [Copy] [x]  |
|                                                                      |
|   +----------------------------------------------------------------+ |
|   | $ npm test                                                     | |
|   |                                                                | |
|   | ... (scrollable output) ...                                    | |
|   |                                                                | |
|   | Tests: 1 failed, 14 passed, 15 total                          | |
|   | Time:  34.2s                                                   | |
|   +----------------------------------------------------------------+ |
|                                                                      |
+----------------------------------------------------------------------+
```

- Exit code shown: green for 0, red for non-zero
- [Copy] button copies full output to clipboard
- [x] collapses the panel
- Output remains scrollable after completion

---

## TUI Mockup

```
  Assistant: Let me run the tests to check for regressions.

  [tool] execute_command: npm test
  +---------------------------------------------------------+
  | > brain-agent@1.5.1 test                                |
  | > jest --verbose                                        |
  |                                                         |
  | PASS src/utils.test.ts (0.8s)                           |
  | PASS src/auth.test.ts (2.1s)                            |
  | FAIL src/api.test.ts (3.4s)                             |
  |   x should return 200 for /health                      |
  |                                                         |
  | Tests: 1 failed, 14 passed, 15 total                   |
  +---------------------------------------------------------+
  [exit: 1, 34s]

  Assistant: The test suite found one failure in api.test.ts...
```

TUI uses Rich Live display to update output in place. Panel has a border
and respects terminal width. Output is rendered below the tool call
indicator and scrolls within the panel if it exceeds 20 lines.

---

## Telegram Behavior

Telegram cannot render live-updating content. Instead:

- Show "Running: npm test..." as a status message
- On completion, send the output as a code block (truncated to 4096 chars)
- For long output, attach as a text file

---

## Workflows

### 1. Real-Time Test Feedback

```
User: "Run the test suite and fix any failures"

Agent calls: execute_command("npm test")
  -> User sees tests passing one by one in real-time
  -> FAIL appears for api.test.ts
  -> User can already see the error while agent waits for full result
  -> Agent receives full output, analyzes failure, fixes code
```

### 2. Live Build Progress

```
User: "Build the Docker image"

Agent calls: execute_command("docker build .")
  -> User sees "Step 1/12: FROM node:20-alpine"
  -> Each layer builds visually
  -> Step 8/12 fails with dependency error
  -> User sees the error immediately
  -> Agent responds with fix
```

### 3. Long Command With No Output

```
Agent calls: execute_command("find / -name '*.log' -mtime +30 -delete")
  -> Collapsed view: "Running: find / ..."  [23.4s] [...]
  -> No output streaming (command produces nothing until done)
  -> Elapsed timer reassures user it is still running
  -> User can click Cancel to abort
```

### 4. Command Fails Mid-Stream

```
Agent calls: execute_command("pip install -r requirements.txt")
  -> Streaming: "Collecting numpy==1.24.0..."
  -> Streaming: "Downloading numpy-1.24.0.tar.gz"
  -> Streaming (stderr, red): "ERROR: Could not build wheels for numpy"
  -> stderr lines highlighted in red/orange
  -> Process exits with code 1
  -> Agent sees error, suggests fix
```

---

## Implementation Details

### Changes to execute_command (claude_cli.py)

Current implementation uses `subprocess.run()` which blocks until completion.
Replace with `subprocess.Popen()` and threaded readers:

```python
def execute_command(cmd, timeout=120, emit_fn=None):
    proc = subprocess.Popen(
        cmd, shell=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env={**os.environ, "TERM": "dumb"},
        text=True, bufsize=1  # line-buffered
    )

    output_queue = queue.Queue()

    # Reader threads for stdout and stderr
    def reader(stream, name):
        for line in iter(stream.readline, ''):
            output_queue.put((name, line))
        stream.close()

    t1 = threading.Thread(target=reader, args=(proc.stdout, "stdout"))
    t2 = threading.Thread(target=reader, args=(proc.stderr, "stderr"))
    t1.start()
    t2.start()

    # Drain queue and emit SSE events
    full_output = []
    while proc.poll() is None or not output_queue.empty():
        batch = drain_queue(output_queue, max_lines=10, timeout_ms=100)
        for stream, line in batch:
            full_output.append(line)
            if emit_fn:
                emit_fn(stream=stream, chunk=line)

    t1.join()
    t2.join()
    return proc.returncode, ''.join(full_output)
```

### Changes to server.py

The SSE streaming loop in `/v1/chat` needs to handle `tool_output` events
from the engine. The `emit_fn` callback is passed down through the tool
execution chain.

### Changes to web/index.html

- New `handleToolOutput(event)` function in the SSE handler
- Creates/updates a terminal panel DOM element keyed by `tool_use_id`
- Auto-scroll logic with user-scroll detection
- Collapse/expand toggle
- Copy button on completion

### Interaction with Tool Call Dedup

The tool call dedup tracker currently compares tool name + args. Streaming
does not affect dedup because the dedup check happens before execution.
If a command is deduped (blocked), no streaming occurs — the block message
is returned immediately.

### Interaction with Scheduled Tasks

Scheduled tasks run without a connected frontend. Streaming output is
discarded (no `emit_fn` provided). The full result is still captured
and stored as before.

---

## Benefits

- **Immediate feedback**: Users see what is happening, reducing anxiety
- **Early error detection**: Spot failures without waiting for completion
- **Cancelability**: Users can make informed cancel decisions
- **Parity with competitors**: Claude Code, Cursor, Windsurf all stream output
- **Debugging aid**: Agents can reference specific output lines

## Risks and Mitigations

| Risk | Mitigation |
|------|-----------|
| High-volume output floods SSE | Ring buffer + truncation at 200KB |
| Binary output corrupts stream | UTF-8 replace mode, skip non-text |
| Buffered programs (no line output) | Elapsed timer still visible; document limitation |
| Multiple concurrent tool calls | Each stream tagged with tool_use_id |
| Network interruption mid-stream | Client reconnects; final tool_result still sent |

## Effort Breakdown

| Task | Days |
|------|------|
| Popen + threaded reader in claude_cli.py | 1 |
| SSE event type + server.py plumbing | 0.5 |
| Web UI terminal panel + auto-scroll | 1.5 |
| TUI Rich Live rendering | 0.5 |
| Truncation / ring buffer / edge cases | 0.5 |
| Testing + integration | 1 |
| **Total** | **5** |

---

## Open Questions

1. Should streaming be opt-in per command (e.g., only for commands expected
   to take > 5s) or always-on?
2. Should we allow users to type input into the streaming terminal for
   semi-interactive commands? (Probably not — execute_command is non-interactive
   by design, TERM=dumb.)
3. Should Telegram get a "progress update" message every N seconds for long
   commands, or just wait for completion?

---

## Related

- Current keepalive: SSE comments every 5s (`server.py`)
- execute_command: `claude_cli.py`, non-interactive, TERM=dumb
- AbortController: `web/index.html`, fetch cleanup
- Tool call dedup: `claude_cli.py`, 2 identical calls = hard abort
