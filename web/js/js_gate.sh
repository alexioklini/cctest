#!/usr/bin/env bash
# js_gate.sh — the JS analog of refactor_gate.sh for the Tier-F web/js refactor.
#
# Steps (plan §0):
#   1. gen-globals.sh                 regenerate .globals.json from current source
#   2. eslint (flat config)           no-undef / no-redeclare must be clean
#                                     (modulo a documented pre-existing baseline)
#   3. globals-count diff vs baseline net globals unchanged (the split invariant)
#   4. (if server up) playwright smoke core flows + zero console errors
#
# Prints `JS GATE PASS ✓` / `JS GATE FAIL ✗`. Component 4 skips with a loud
# notice if the dev server (127.0.0.1:8420) isn't running — pure-split steps can
# gate fast on 1-3, but a server-up run is REQUIRED before declaring a step done.
set -uo pipefail
cd "$(dirname "$0")"

GREEN=$'\033[32m'; RED=$'\033[31m'; YEL=$'\033[33m'; NC=$'\033[0m'
fail() { echo "${RED}JS GATE FAIL ✗${NC} — $1"; exit 1; }

BASELINE_GLOBALS=.globals-count.baseline   # net-globals invariant anchor
ESLINT_BASELINE=.eslint-baseline.txt       # documented pre-existing no-undef findings

# --- Step 1: regenerate globals ----------------------------------------------
./gen-globals.sh || fail "gen-globals.sh errored"

# --- Step 3a: net-globals count (compute now; compared after lint) -----------
count=$(grep -c '"writable"' .globals.json)

# --- Step 2: eslint no-undef / no-redeclare ----------------------------------
# We capture only no-undef/no-redeclare findings as "<file>: <rule>: <msg>"
# (line-number-free, so a finding stays "the same" when code above it shifts),
# and diff against a committed baseline of KNOWN pre-existing findings. Any
# finding NOT in the baseline fails the gate — that is the "a split lost/
# duplicated a global" signal. The baseline can only shrink during the refactor,
# never grow (a grown baseline = a regression being hidden; refuse it).
# Lint the directory (not a *.js glob) so the config's `ignores` for the gate's
# own Node tooling (eslint.config / playwright.config / smoke.spec) is honoured.
findings=$(npx eslint . --rule '{"no-unused-vars":"off"}' --format json 2>/dev/null \
  | node -e '
    const r = JSON.parse(require("fs").readFileSync(0,"utf8"));
    const out = new Set();
    for (const f of r) {
      const base = f.filePath.split("/").pop();
      for (const m of f.messages)
        if (m.ruleId==="no-undef" || m.ruleId==="no-redeclare")
          out.add(`${base}: ${m.ruleId}: ${m.message}`);
    }
    process.stdout.write([...out].sort().join("\n"));
  ')

if [ -f "$ESLINT_BASELINE" ]; then
  baseline=$(grep -vE '^[[:space:]]*#' "$ESLINT_BASELINE" | grep -v '^[[:space:]]*$' | sort -u)
else
  baseline=""
fi

# New findings = in current but not in baseline.
new=$(comm -23 <(printf '%s\n' "$findings" | grep -v '^$') <(printf '%s\n' "$baseline" | grep -v '^$'))
if [ -n "$new" ]; then
  echo "${RED}New ESLint no-undef/no-redeclare findings (not in baseline):${NC}"
  printf '%s\n' "$new"
  fail "ESLint surfaced a new undefined/duplicate global — a split likely lost or copied a definition"
fi

# Findings that LEFT the baseline are fine (refactor can fix latent issues), but
# the baseline file should then be trimmed. Warn loudly so it gets cleaned up.
gone=$(comm -13 <(printf '%s\n' "$findings" | grep -v '^$') <(printf '%s\n' "$baseline" | grep -v '^$'))
if [ -n "$gone" ]; then
  echo "${YEL}NOTE: these baseline findings no longer occur — trim them from $ESLINT_BASELINE:${NC}"
  printf '%s\n' "$gone"
fi
echo "${GREEN}eslint: clean modulo $(printf '%s\n' "$baseline" | grep -vc '^$') documented baseline finding(s)${NC}"

# --- Step 3b: net-globals invariant ------------------------------------------
if [ -f "$BASELINE_GLOBALS" ]; then
  base=$(cat "$BASELINE_GLOBALS")
  if [ "$count" != "$base" ]; then
    fail "net-globals changed: baseline=$base now=$count (a split must RELOCATE globals, never add/drop). If this change is intentional+verified, update $BASELINE_GLOBALS in the same commit."
  fi
  echo "${GREEN}net-globals: $count (unchanged vs baseline)${NC}"
else
  echo "$count" > "$BASELINE_GLOBALS"
  echo "${YEL}net-globals baseline established: $count (wrote $BASELINE_GLOBALS)${NC}"
fi

# --- Step 4: playwright smoke (only if server up) ----------------------------
if curl -sf -o /dev/null --max-time 3 http://127.0.0.1:8420/ 2>/dev/null; then
  if [ -f smoke.spec.js ] && [ -d node_modules/@playwright ]; then
    echo "Server up — running Playwright smoke…"
    if npx playwright test smoke.spec.js 2>&1 | tail -20; then
      echo "${GREEN}smoke: passed${NC}"
    else
      fail "Playwright smoke failed (core flow broke or console error)"
    fi
  else
    echo "${YEL}smoke: skipped (smoke.spec.js or playwright not installed)${NC}"
  fi
else
  echo "${YEL}smoke: SKIPPED — dev server not running at 127.0.0.1:8420.${NC}"
  echo "${YEL}        Components 1-3 passed. A server-up run is REQUIRED before declaring a step done.${NC}"
fi

echo "${GREEN}JS GATE PASS ✓${NC}"
