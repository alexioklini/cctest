#!/usr/bin/env bash
# PreToolUse(Bash) deny hook: block any SIGKILL aimed at the brain-agent server
# / MemPalace process. SIGKILL bypasses brain-agent's v9.60.3 graceful-shutdown
# handler, so chromadb never flushes its HNSW index → the MemPalace vector
# segment is quarantined + rebuilt on next boot (recurring corruption the user
# explicitly forbade). The ONLY approved restart is a graceful SIGTERM:
#     launchctl kill SIGTERM gui/$(id -u)/com.brain-agent.server
# (NB: `launchctl kickstart` WITHOUT a kill flag is a no-op on an already-running
#  service — it does NOT restart it; use `kill SIGTERM` to trigger a clean cycle.)
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

ALT='Use a GRACEFUL restart instead: launchctl kill SIGTERM gui/$(id -u)/com.brain-agent.server  (SIGTERM → clean shutdown so chromadb flushes the MemPalace HNSW index; note: launchctl kickstart WITHOUT a kill flag is a no-op on an already-running service). SIGKILL is forbidden without explicit user approval.'

# A command only counts as DANGEROUS if it references the brain-agent service —
# this is the key tightening: descriptive text / echoes / commit messages that
# merely MENTION the kill flag (but don't target brain-agent) no longer trip the
# guard, while every genuinely dangerous command (which must name the service to
# act on it) still does. A real SIGKILL to brain-agent always names it.
refs_brain() { printf '%s' "$cmd" | grep -Eiq 'brain-agent|brain_agent|mempalace|server\.py|com\.brain-agent'; }

if refs_brain; then
  # (1) launchctl kickstart with a -k (SIGKILL/kill) flag, as an actual
  #     invocation: `launchctl ... kickstart ... -<…>k …`. The launchctl+kickstart
  #     pairing (not the bare word "kickstart") plus a -k flag = the real call.
  if printf '%s' "$cmd" | grep -Eq 'launchctl[^;|&]*\bkickstart\b[^;|&]*(^|[[:space:]])-[A-Za-z]*k'; then
    deny "Blocked: 'launchctl kickstart -k' SIGKILLs the brain-agent server and corrupts the MemPalace HNSW index. $ALT"
  fi

  # (2) kill / pkill with a KILL signal targeting the brain-agent process.
  if printf '%s' "$cmd" | grep -Eq '(^|[[:space:]])(p?kill)([[:space:]]|$)' \
     && printf '%s' "$cmd" | grep -Eq -- '-(9|KILL)([[:space:]]|$)|-s[[:space:]]+(KILL|9)|-SIGKILL'; then
    deny "Blocked: SIGKILL (kill -9 / -KILL) of a brain-agent/MemPalace process bypasses graceful shutdown and corrupts the HNSW index. $ALT"
  fi
fi

# Default: allow.
jq -n '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"allow"}}'
