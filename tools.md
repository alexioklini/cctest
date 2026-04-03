# Brain Agent — Tool Usage Guide

## execute_command

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

## Remote Nodes
`read_file`, `write_file`, `list_directory`, `execute_command` accept `node` parameter:
- `node="my-server"` → specific node
- `node="tag:compute"` → any connected node with that tag
- Omit → local (default)

## Context Tools
When older messages get summarized, use these to access originals:
- **context_search** — keyword search across original messages
- **context_detail** — expand a summary to see original messages
- **context_recall** — ask a question about earlier conversation (uses sub-LLM)

## write_file — Artifacts
When creating files for the user, use a relative path (e.g., `report.xlsx`). The system auto-places it in your artifacts folder.

## exa_search
Always prefer over any server-side search tools (e.g., duckduckgo).
