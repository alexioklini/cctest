# Brain Agent — Tool Usage Guide

Each block below applies only when its anchor tool(s) are in the active tool
set. Blocks are emitted by `_render_tool_rules(active_tool_names)` (brain.py).
Anchor markers below are load-bearing — do not rename.

<!-- @anchor:execute_command -->
## Shell command execution

### Rules
- Commands run with **no TTY, no stdin, TERM=dumb**. Interactive programs will hang and timeout.
- Default timeout is 15 seconds. Long-running commands: pipe through `head` or use flags to limit output.

### Banned Commands (interactive, will timeout)
`top`, `htop`, `less`, `more`, `vim`, `nvim`, `nano`, `emacs`, `watch`, `dialog`,
`python`/`node` (without `-c`/`-e`), `ssh` (interactive), `ftp`, `telnet`, `man` (use `--help` instead)

### Non-Interactive Alternatives
| Want | Use |
|---|---|
| CPU/process info | `top -l 1 -n 10` (macOS) or `top -bn1` (Linux) |
| Process list | `ps aux --sort=-%cpu \| head -20` |
| Memory | `vm_stat` (macOS), `free -h` (Linux) |
| File viewing | `cat`, `head -n 50`, `tail -n 50` |
| Git | Use `git --no-pager` for log/diff |
| Manual pages | `<cmd> --help 2>&1 \| head -40` |

### Output Management
- Pipe through `head -N` for large output
- Use `2>/dev/null` to suppress noisy stderr
- Use `|| true` to prevent non-zero exit codes when failure is expected

<!-- @anchor:exa_search,web_fetch -->
## Web research protocol

Always prefer `exa_search` over any server-side search tools (e.g., duckduckgo).
`exa_search` returns URLs and titles only — no page content.

After `exa_search`, you MUST fetch ALL returned URLs with `web_fetch` before answering. Never answer from titles or URLs alone.

**Rules — no exceptions:**
- Fetch every URL returned by `exa_search`, up to 5 at a time in parallel
- Only after all URLs are fetched: synthesise and answer
- If more than 5 URLs were returned: fetch the first 5, then the next 5, and so on until all are done

<!-- @anchor:python_exec -->
## Python execution

Runs Python in a subprocess. The working directory is your artifacts folder — files you write there become user-visible artifacts.

### When to use instead of multiple tool calls
Each tool round re-sends the full conversation to the LLM. One `python_exec` replacing 3+ tool calls saves significant tokens.

**Prefer python_exec for:**
- Multi-file reads: `open()` several files, extract what you need, print a summary
- Search + aggregate: `os.walk` + regex across files, count/filter/group results
- Data processing: parse CSV/JSON, compute stats, transform data
- Bulk file operations: rename, copy, filter files by pattern
- Document processing (see below)

**Keep using native tools for:**
- Single file read/write (tool is simpler, no overhead)
- Git operations (`git_command`)
- Web/API calls (`web_fetch`, `exa_search`)
- Memory, delegation, scheduling (Brain-internal)

### Document processing
These packages are available — use them directly in python_exec instead of chaining read_document/write_document/edit_document:

| Package | Use for |
|---|---|
| `docx` (python-docx) | Read/write/edit DOCX — paragraphs, tables, styles, headers |
| `openpyxl` | Read/write/edit XLSX — cells, sheets, formulas, charts |
| `pptx` (python-pptx) | Read/write/edit PPTX — slides, shapes, text, images |
| `reportlab` | Generate PDFs from scratch (layouts, tables, graphics) |
| `PIL` (Pillow) | Image processing — resize, crop, convert, annotate |
| `csv` | CSV read/write (stdlib) |

Example — read a DOCX table and create a summary CSV:
```python
from docx import Document
import csv
doc = Document('/path/to/report.docx')
with open('summary.csv', 'w', newline='') as f:
    w = csv.writer(f)
    for table in doc.tables:
        for row in table.rows:
            w.writerow([cell.text for cell in row.cells])
print(f"Extracted {len(doc.tables)} tables to summary.csv")
```

### Output rules
- **Large results**: write to a file (becomes an artifact), print only a short summary
- **Small results** (<20 lines): print directly to stdout
- The system auto-saves stdout >1K chars as an artifact, but writing files yourself gives you control over filename and format
