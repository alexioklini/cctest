# Feature Proposal: Code Sandbox

**Status:** Proposed
**Effort:** ~8 days
**Priority:** Medium
**Author:** Brain Agent Team
**Date:** 2026-03-20

---

## Problem Statement

`execute_command` runs directly on the host macOS system with full filesystem and
network access. There is no isolated environment for:

- Running untrusted code snippets from the web or user input
- Data analysis scripts that should not touch the host filesystem
- Exploratory code generation where mistakes could have side effects
- Generated Python/JS code that might import dangerous modules

The current tool has a banned commands list (in `tools.md`), but this is a soft
guard enforced by prompt instructions — not a technical sandbox. An agent in a
loop or following adversarial instructions could still `rm -rf`, exfiltrate data,
or install unwanted software.

---

## Proposed Solution

A new `run_code` tool that executes code in a sandboxed environment with:

- **Restricted filesystem**: temporary directory only, no access to host files
- **Network isolation**: disabled by default, opt-in per execution
- **Resource limits**: max memory, CPU time, output size
- **Automatic cleanup**: temp directory destroyed after execution
- **Multi-language**: Python, JavaScript (Node.js), Shell

This complements `execute_command` rather than replacing it. `execute_command`
remains for legitimate host operations (git, build tools, file management).
`run_code` is for generated/untrusted code that should run in isolation.

---

## Tool Definition

```json
{
  "name": "run_code",
  "description": "Execute code in an isolated sandbox. Use for data analysis, code generation, testing snippets. Cannot access host filesystem or network.",
  "parameters": {
    "language": {
      "type": "string",
      "enum": ["python", "javascript", "shell"],
      "description": "Programming language to execute"
    },
    "code": {
      "type": "string",
      "description": "Source code to execute"
    },
    "timeout": {
      "type": "integer",
      "description": "Max execution time in seconds (default 30, max 300)",
      "default": 30
    },
    "network": {
      "type": "boolean",
      "description": "Allow network access (default false)",
      "default": false
    },
    "files": {
      "type": "object",
      "description": "Files to provision in sandbox: {filename: content}",
      "default": {}
    }
  }
}
```

---

## Comparison: execute_command vs run_code

```
+---------------------+------------------------+------------------------+
|                     | execute_command         | run_code               |
+---------------------+------------------------+------------------------+
| Filesystem          | Full host access        | Temp directory only    |
| Network             | Full access             | Blocked by default     |
| Persistence         | Changes persist         | Ephemeral (auto-clean) |
| Languages           | Any (shell)             | Python, JS, Shell      |
| Use case            | Git, build, file ops    | Analysis, snippets     |
| Dependencies        | Host-installed          | Pre-approved list      |
| Risk level          | High (host mutation)    | Low (sandboxed)        |
| Output artifacts    | On host filesystem      | Returned as base64     |
| Max runtime         | 120s (configurable)     | 30s default, 300s max  |
| Agent trust needed  | Yes                     | No                     |
+---------------------+------------------------+------------------------+
```

---

## Architecture

### Lightweight Sandbox (No Docker)

For macOS, use subprocess isolation without Docker:

```
run_code(language="python", code="import pandas as pd\n...")
       |
       v
  1. Create temp directory: /tmp/sandbox-<uuid>/
       |
       v
  2. Write code to /tmp/sandbox-<uuid>/main.py
     Write provisioned files to /tmp/sandbox-<uuid>/
       |
       v
  3. Execute with restrictions:
     - subprocess.Popen with cwd=/tmp/sandbox-<uuid>/
     - Environment: minimal PATH (only language runtime)
     - ulimit: max memory (512MB), max CPU time (timeout)
     - No HOME, no host env vars leaked
     - stdin closed immediately
       |
       v
  4. Capture stdout, stderr, exit code
     Scan /tmp/sandbox-<uuid>/ for generated files
       |
       v
  5. Return: {stdout, stderr, exit_code, files: {name: base64}, elapsed}
       |
       v
  6. Cleanup: shutil.rmtree(/tmp/sandbox-<uuid>/)
```

### Enhanced Sandbox (macOS sandbox-exec)

For stronger isolation, use macOS `sandbox-exec` with a profile:

```
(version 1)
(deny default)
(allow file-read* (subpath "/tmp/sandbox-<uuid>"))
(allow file-write* (subpath "/tmp/sandbox-<uuid>"))
(allow file-read* (subpath "/usr/lib"))
(allow file-read* (subpath "/usr/local/lib"))
(allow file-read* (subpath "/opt/homebrew"))
(allow process-exec)
(allow process-fork)
(deny network*)           ; no network unless opted in
(deny file-write* (subpath "/Users"))
(deny file-read* (subpath "/Users"))
```

### Execution Per Language

```
Python:     /usr/bin/python3 /tmp/sandbox-<uuid>/main.py
JavaScript: /usr/local/bin/node /tmp/sandbox-<uuid>/main.js
Shell:      /bin/bash /tmp/sandbox-<uuid>/main.sh
```

---

## Web UI Mockups

### Code Execution Result

```
+----------------------------------------------------------------------+
| [v] run_code (python)                                [Exit: 0] 2.3s  |
|                                                                      |
|   +-----------------------------+  +-------------------------------+ |
|   | Code                 [Copy] |  | Output                [Copy] | |
|   +-----------------------------+  +-------------------------------+ |
|   | import pandas as pd         |  | Shape: (1000, 5)             | |
|   | import numpy as np          |  | Columns: [id, name, value,   | |
|   |                             |  |   date, category]            | |
|   | df = pd.read_csv('data.csv')|  |                              | |
|   | print(f"Shape: {df.shape}") |  | Summary:                     | |
|   | print(f"Columns: {list(     |  |   value: mean=42.3, std=12.1 | |
|   |   df.columns)}")            |  |   category: 5 unique         | |
|   | print()                     |  |                              | |
|   | print("Summary:")           |  |                              | |
|   | print(f"  value: mean=      |  |                              | |
|   |   {df.value.mean():.1f}")   |  |                              | |
|   +-----------------------------+  +-------------------------------+ |
|                                                                      |
+----------------------------------------------------------------------+
```

### With Generated Files (e.g., Chart)

```
+----------------------------------------------------------------------+
| [v] run_code (python)                                [Exit: 0] 4.1s  |
|                                                                      |
|   Output:                                                            |
|   Chart saved to output.png                                          |
|                                                                      |
|   Generated Files:                                                   |
|   +-------------------------------+                                  |
|   | output.png          [Download]|                                  |
|   |  +-------------------------+ |                                  |
|   |  |                         | |                                  |
|   |  |   [matplotlib chart     | |                                  |
|   |  |    rendered inline]     | |                                  |
|   |  |                         | |                                  |
|   |  +-------------------------+ |                                  |
|   +-------------------------------+                                  |
|                                                                      |
+----------------------------------------------------------------------+
```

### Sandbox Settings (Config Modal)

```
+----------------------------------------------------------------------+
|  Code Sandbox Settings                                               |
|                                                                      |
|  Allowed Languages:                                                  |
|    [x] Python     [x] JavaScript     [x] Shell                      |
|                                                                      |
|  Resource Limits:                                                    |
|    Max Memory:      [  512  ] MB                                     |
|    Max CPU Time:    [   30  ] seconds                                |
|    Max Output:      [    1  ] MB                                     |
|                                                                      |
|  Network Access:                                                     |
|    ( ) Always blocked                                                |
|    (*) Agent can request (shown in UI)                               |
|    ( ) Always allowed                                                |
|                                                                      |
|  Sandbox Backend:                                                    |
|    (*) subprocess (lightweight, basic isolation)                      |
|    ( ) sandbox-exec (macOS sandbox, stronger isolation)              |
|    ( ) Docker (full container, requires Docker Desktop)              |
|                                                                      |
|                                              [Cancel]  [Save]        |
+----------------------------------------------------------------------+
```

### Error State

```
+----------------------------------------------------------------------+
| [v] run_code (python)                            [Exit: 1] 0.4s     |
|                                                                      |
|   +-----------------------------+  +-------------------------------+ |
|   | Code                 [Copy] |  | Error                  [Copy] | |
|   +-----------------------------+  +-------------------------------+ |
|   | import os                   |  | PermissionError:              | |
|   | os.listdir('/Users')        |  |   [Errno 1] Operation not    | |
|   |                             |  |   permitted: '/Users'         | |
|   +-----------------------------+  +-------------------------------+ |
|                                                                      |
+----------------------------------------------------------------------+
```

---

## TUI Mockup

```
  Assistant: Let me analyze that data for you.

  [tool] run_code (python):
  +--[ code ]-------------------------------------------+
  | import pandas as pd                                  |
  | df = pd.read_csv('data.csv')                         |
  | print(df.describe())                                 |
  +------------------------------------------------------+
  +--[ output, exit: 0, 1.8s ]-------------------------+
  |              value    count                          |
  | mean         42.3   1000.0                          |
  | std          12.1    0.0                             |
  | min           1.0   1000.0                          |
  | max          99.0   1000.0                          |
  +------------------------------------------------------+

  Assistant: The dataset has 1000 rows with values ranging...
```

---

## Workflows

### 1. CSV Data Analysis

```
User: "Analyze this CSV file" (attaches data.csv)

Agent: I'll analyze this data in a sandbox.

Agent calls: run_code(
  language="python",
  code="import pandas as pd\ndf = pd.read_csv('data.csv')\nprint(df.describe())\n...",
  files={"data.csv": "<csv content>"}
)

Sandbox: creates /tmp/sandbox-abc123/, writes data.csv, runs script
Result: stdout with statistics, exit code 0
Agent: "The dataset contains 1000 rows..."
```

### 2. Chart Generation

```
User: "Plot the sales trend from this data"

Agent calls: run_code(
  language="python",
  code="""
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import pandas as pd

df = pd.read_csv('sales.csv')
plt.figure(figsize=(10, 6))
plt.plot(df['month'], df['revenue'])
plt.title('Monthly Revenue')
plt.savefig('chart.png')
print('Chart saved')
""",
  files={"sales.csv": "<data>"}
)

Result: stdout="Chart saved", files={"chart.png": "<base64 PNG>"}
Web UI: renders chart inline
Agent: "Here's the sales trend chart..."
```

### 3. Malicious Code Blocked

```
Agent (compromised or adversarial) calls: run_code(
  language="python",
  code="import os; print(open('/etc/passwd').read())"
)

Sandbox (sandbox-exec mode):
  PermissionError: [Errno 1] Operation not permitted: '/etc/passwd'
  Exit code: 1

Agent receives error, cannot access host files.
```

### 4. Timeout Protection

```
Agent calls: run_code(
  language="python",
  code="while True: pass",
  timeout=10
)

Sandbox: process killed after 10 seconds
Result: stderr="Execution timed out after 10 seconds", exit_code=-9
Agent: "The code entered an infinite loop and was terminated."
```

---

## Dependency Management

### Pre-installed Packages

The sandbox inherits the host Python/Node installation but only allows
importing from a curated allowlist of packages:

**Python (common data/analysis):**
- Standard library (all)
- pandas, numpy, matplotlib, seaborn
- scipy, scikit-learn
- requests (only when network=true)
- json, csv, re, math, datetime

**JavaScript (Node.js):**
- Standard library (fs limited to sandbox dir)
- lodash, moment (if globally installed)

### On-Demand Install

For packages not pre-installed, the sandbox can run pip/npm as part of
the code execution:

```python
# Agent generates:
import subprocess
subprocess.run(["pip", "install", "beautifulsoup4"], check=True)
from bs4 import BeautifulSoup
# ... use it
```

This installs into the temp directory and is cleaned up after execution.
Network must be enabled for installs.

---

## Output Artifacts

When code generates files (images, CSVs, HTML), the sandbox:

1. Scans the temp directory for new/modified files after execution
2. Reads files up to 5MB each, 20MB total
3. Returns as base64-encoded entries in the result
4. Web UI renders images inline, offers download for other types
5. Files are ephemeral — not persisted after the response

```json
{
  "stdout": "Chart saved to output.png\n",
  "stderr": "",
  "exit_code": 0,
  "elapsed": 4.1,
  "files": {
    "output.png": {
      "content": "iVBORw0KGgo...",
      "encoding": "base64",
      "size": 45230,
      "mime": "image/png"
    }
  }
}
```

---

## Implementation Approach

### Phase 1: Basic Subprocess Sandbox (3 days)

- New `run_code` tool in `claude_cli.py`
- Temp directory creation and cleanup
- subprocess.Popen with restricted environment
- Timeout enforcement via `proc.kill()` after deadline
- Capture stdout/stderr/exit code
- File artifact scanning and base64 encoding
- Web UI rendering of code + output side by side

### Phase 2: macOS sandbox-exec Integration (2 days)

- Generate sandbox profile dynamically per execution
- Block filesystem access outside temp dir
- Block network access unless opted in
- Test with Python, Node, Bash
- Fallback to basic subprocess if sandbox-exec unavailable

### Phase 3: UI and Polish (3 days)

- Sandbox settings in config modal
- Generated file rendering (inline images, download buttons)
- TUI support with Rich panels
- Language syntax highlighting in code display
- Error state formatting
- Integration tests

---

## Benefits

- **Safety**: Untrusted code cannot damage the host system
- **Data analysis**: Agents can run pandas/numpy code without risk
- **Visualization**: Charts and plots rendered inline in the UI
- **Reproducibility**: Ephemeral environment = no state leakage between runs
- **User confidence**: Users can ask agents to "just try it" without fear

## Risks and Mitigations

| Risk | Mitigation |
|------|-----------|
| sandbox-exec deprecated by Apple | Fallback to basic subprocess; Docker as option |
| Python packages not available | Pre-install common ones; allow pip in sandbox |
| Base64 output too large | 5MB per file, 20MB total limit |
| Agent prefers run_code over execute_command | Tool descriptions guide correct usage |
| Sandbox escape via kernel exploit | Defense in depth; not a security boundary for nation-state |

## Effort Breakdown

| Task | Days |
|------|------|
| run_code tool + subprocess sandbox | 2 |
| sandbox-exec profile + integration | 2 |
| Web UI: code/output rendering | 1.5 |
| File artifact handling + inline images | 1 |
| TUI support | 0.5 |
| Testing + edge cases | 1 |
| **Total** | **8** |

---

## Open Questions

1. Should `run_code` support stateful sessions (run multiple snippets in the
   same sandbox, preserving variables)? This adds complexity but enables
   iterative data exploration.
2. Should we support R language for statistical analysis?
3. Docker option: worth including for users who have Docker Desktop, or
   over-engineering for a local dev tool?
4. Should generated images be auto-stored in agent memory for future reference?

---

## Related

- execute_command: `claude_cli.py`, full host access, non-interactive
- tools.md: banned commands list (soft guard)
- Tool call dedup: prevents infinite sandbox loops
- Scheduled tasks: sandbox could be useful for safer scheduled code execution
