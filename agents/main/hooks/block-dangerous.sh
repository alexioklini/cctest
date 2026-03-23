#!/bin/bash
# Pre-hook: Block dangerous commands
# Type: pre | Tools: execute_command

COMMAND=$(echo "$HOOK_TOOL_ARGS" | python3 -c "import sys,json; print(json.load(sys.stdin).get('command',''))" 2>/dev/null)

PATTERNS=(
  "rm -rf /"
  "rm -rf ~"
  "rm -rf \$HOME"
  "mkfs"
  "dd if=/dev/zero"
  "> /dev/sda"
  "chmod -R 777 /"
  ":(){ :|:& };:"
)

for pattern in "${PATTERNS[@]}"; do
  if echo "$COMMAND" | grep -qF "$pattern"; then
    echo "BLOCKED: Command matches dangerous pattern: $pattern"
    exit 1
  fi
done

exit 0
