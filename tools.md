# Brain Agent — Tool Usage Guide

This file is loaded into the system prompt and guides tool behavior.
Update this file to teach the agent new patterns and fix recurring mistakes.

---

## execute_command

### Rules
- Commands run with **no TTY, no stdin, TERM=dumb**. Interactive programs will hang and timeout.
- Default timeout is 15 seconds. Long-running commands should be piped through `head` or use flags to limit output.
- Output is captured as text. ANSI escape codes are stripped automatically.

### Banned Commands (interactive, will timeout)
- `top` (without `-l` or `-bn1`), `htop`, `btop`, `glances`
- `less`, `more`, `most`
- `vim`, `nvim`, `nano`, `emacs`, `vi`
- `watch`, `dialog`, `whiptail`
- `python` / `python3` (without `-c`), `node` (without `-e`), `irb`, `ghci`
- `ssh` (interactive), `ftp`, `telnet`
- `man` (use `man <cmd> | head -50` or `<cmd> --help` instead)

### Non-Interactive Alternatives

| Want | Use instead |
|---|---|
| CPU/process info | `top -l 1 -n 10` (macOS) or `top -bn1 -n 10` (Linux) |
| Process list | `ps aux`, `ps aux --sort=-%cpu \| head -20` |
| Memory info | `vm_stat` (macOS), `free -h` (Linux), `sysctl hw.memsize` (macOS) |
| Disk usage | `df -h`, `du -sh *` |
| System info | `uname -a`, `sw_vers` (macOS), `cat /etc/os-release` (Linux) |
| Network | `ifconfig` or `ip addr`, `netstat -an \| head -30`, `lsof -i -P \| head -30` |
| File viewing | `cat`, `head -n 50`, `tail -n 50` (not less/more) |
| File searching | Prefer `search_files` tool over `grep` command |
| Monitoring | `iostat 1 2`, `uptime`, `nettop -l 1 -n` (macOS) |
| Package info | `brew list` (macOS), `dpkg -l` (Linux), `pip list`, `npm ls` |
| Git | All git commands work. Use `git --no-pager` for log/diff to avoid pager. |
| Docker | `docker ps`, `docker logs <id> --tail 50` |
| Manual pages | `<cmd> --help 2>&1 \| head -40` or `man <cmd> \| col -b \| head -60` |
| Python one-liners | `python3 -c "print('hello')"` (not interactive python) |

### Output Management
- Pipe through `head -N` for commands with potentially large output
- Use `wc -l` to count lines before reading large files
- Use `| tail -20` for log files
- Use `2>/dev/null` to suppress noisy stderr when not relevant
- Use `|| true` to prevent non-zero exit codes from commands where failure is expected

### Platform Detection
- macOS: `sw_vers`, `sysctl`, `vm_stat`, `diskutil`, `launchctl`, `top -l`, `pbcopy/pbpaste`
- Linux: `cat /etc/os-release`, `free`, `systemctl`, `journalctl`, `top -bn1`, `xclip`
- Check `uname -s` if unsure — returns "Darwin" (macOS) or "Linux"

---

## read_file

- Use `offset` and `limit` for large files instead of reading everything
- Check file size first with `execute_command` + `wc -l` if unsure
- Binary files will return garbled output — check extension first

---

## write_file

- Creates parent directories automatically
- Overwrites without confirmation — be sure this is intended
- For small changes to existing files, prefer `edit_file` over `write_file`

---

## edit_file

- `old_string` must match exactly (whitespace, indentation, newlines)
- If match is ambiguous (multiple occurrences), provide more context or use `replace_all=true`
- Read the file first to see exact content before editing

---

## search_files

- Skips `.git`, `node_modules`, `__pycache__`, and hidden files automatically
- Use `glob` parameter to narrow file types (e.g., `*.py`, `*.js`)
- Pattern is regex — escape special chars like `.` `(` `)` `[` `]`
- For simple filename searches, use `list_directory` with a pattern instead

---

## list_directory

- Default is non-recursive. Use `recursive=true` or `**` glob patterns for deep listing
- Returns max 500 entries — use patterns to narrow if needed

---

## web_fetch

- Respects redirects automatically
- Max response length is 50000 chars by default
- For APIs, set `Content-Type: application/json` in headers
- Some sites block non-browser user agents — this sends a Chrome UA by default

---

## exa_search

- Always prefer over any server-side search tools (e.g., duckduckgo)
- Use `category` for specialized searches: "news", "research paper", "tweet", "company", "people"
- Default 5 results — increase `num_results` for broader research
- Returns highlights/snippets, not full page content — use `web_fetch` to read specific URLs
