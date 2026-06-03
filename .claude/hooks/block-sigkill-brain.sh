#!/usr/bin/env bash
# PreToolUse(Bash) deny hook: block any SIGKILL aimed at the brain-agent server
# / MemPalace process. SIGKILL bypasses brain-agent's v9.60.3 graceful-shutdown
# handler, so chromadb never flushes its HNSW index → the MemPalace vector
# segment is quarantined + rebuilt on next boot (recurring corruption the user
# explicitly forbade). The ONLY approved restart is a graceful SIGTERM:
#     launchctl kickstart gui/$(id -u)/com.brain-agent.server   # no -k
#
# Reads the PreToolUse JSON on stdin, emits a permissionDecision of "deny" with
# a reason when the command matches, else "allow". Deterministic — enforced by
# the harness, not by the model remembering.
set -euo pipefail

cmd="$(jq -r '.tool_input.command // ""' 2>/dev/null)"

# Safety bias: a FALSE POSITIVE (blocking a harmless command) is annoying; a
# FALSE NEGATIVE (letting a real SIGKILL through) is the corruption we're
# preventing — so the matcher stays strict. The ONE narrow exclusion is a
# `git commit`: it cannot signal a process, and its message body routinely
# quotes the very strings we match (this rule, changelogs). Excluding it removes
# the common false positive with zero added risk. Everything else is checked.
# (We only skip when the command STARTS with git commit, so a compound like
#  `git commit … && launchctl kickstart -k …` is still inspected.)
if printf '%s' "$cmd" | grep -Eq '^[[:space:]]*git[[:space:]]+commit([[:space:]]|$)' \
   && ! printf '%s' "$cmd" | grep -Eq '&&|\|\||;'; then
  jq -n '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"allow"}}'
  exit 0
fi

deny() {
  # PreToolUse deny: the harness blocks the call and shows the reason.
  jq -n --arg r "$1" '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"deny",permissionDecisionReason:$r}}'
  exit 0
}

ALT='Use a GRACEFUL restart instead: launchctl kickstart gui/$(id -u)/com.brain-agent.server  (no -k → SIGTERM → clean shutdown so chromadb flushes the MemPalace HNSW index). SIGKILL is forbidden without explicit user approval.'

# (1) launchctl kickstart with a -k (SIGKILL) flag — matches -k, -kp, -pk, -k -p, etc.
if printf '%s' "$cmd" | grep -Eq 'kickstart'; then
  if printf '%s' "$cmd" | grep -Eq '(^|[[:space:]])-[A-Za-z]*k'; then
    deny "Blocked: 'launchctl kickstart -k' SIGKILLs the brain-agent server and corrupts the MemPalace HNSW index. $ALT"
  fi
fi

# (2) kill / pkill with a KILL signal that references the brain-agent process.
refs_brain() { printf '%s' "$cmd" | grep -Eiq 'brain-agent|brain_agent|mempalace|server\.py|com\.brain-agent'; }
is_kill9() { printf '%s' "$cmd" | grep -Eq '(^|[[:space:]])(p?kill)([[:space:]]|$)' \
             && printf '%s' "$cmd" | grep -Eq -- '-(9|KILL)([[:space:]]|$)|-s[[:space:]]+(KILL|9)|-SIGKILL'; }
if is_kill9 && refs_brain; then
  deny "Blocked: SIGKILL (kill -9 / -KILL) of a brain-agent/MemPalace process bypasses graceful shutdown and corrupts the HNSW index. $ALT"
fi

# Default: allow.
jq -n '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"allow"}}'
