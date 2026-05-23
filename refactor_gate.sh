#!/usr/bin/env bash
# refactor_gate.sh — completeness + regression gate for the module-extraction refactor.
# Run after EVERY extraction. A refactor is "done" only when this passes AND the
# per-extraction grep proof (gate 2: old symbol gone from live code) is clean.
#
# Usage:  ./refactor_gate.sh                  # full gate
#         ./refactor_gate.sh grep <SYMBOL>    # gate-2 helper: prove old copy is gone
#
# Baseline recorded 2026-05-22 at clean HEAD (before any extraction):
#   - unittest: 80 pass / 3 fail. The 3 failures are ALL in test_pii_ner.py and
#     are environmental (spaCy NER not loaded in the bare test process, not a
#     code defect). Gate rule: NO new failures beyond these 3 named tests.
#   - imports: 16/16 core+handler modules import clean.
set -u
PY=/opt/homebrew/bin/python3          # the daemon interpreter (see launchd plist)
cd "$(dirname "$0")"

KNOWN_FAILS="test_contact_warn_promotes_ner_findings test_name_roundtrip test_ner_findings_merge_with_regex"

if [ "${1:-}" = "grep" ]; then
  sym="$2"
  # Gate 2 enforces governing principle #3: the ORIGINAL DEFINITION must be gone
  # from brain.py. A surviving `def <sym>(` / `class <sym>(` in brain.py = NOT DONE
  # (worse-than-before state) -> revert. A `from ... import <sym>` alias is fine.
  echo "=== Gate 2: definition of '$sym' must NOT remain in brain.py ==="
  defhits=$(grep -nE "^(def|class| +def| +class) +$sym\b" brain.py 2>/dev/null)
  if [ -n "$defhits" ]; then
    echo "  FAIL — original definition still in brain.py (principle #3 violated):"
    echo "$defhits" | sed 's/^/    /'
    echo "  -> finish the move (delete this) or revert. NOT a completed extraction."
  else
    echo "  OK — no definition of '$sym' in brain.py."
  fi
  echo ""
  echo "  --- all live references (alias import is expected; eyeball the rest) ---"
  grep -rnE "\b$sym\b" brain.py engine/ handlers/ server_lib/ server.py 2>/dev/null \
    | grep -v -E '^\S+:[0-9]+:\s*#' \
    | grep -vE '^\S+:8:|^\S+:[0-9]+:.*"[0-9]+\.[0-9]+\.[0-9]+", "20' \
    || echo "  (no references at all — fully removed)"
  [ -n "$defhits" ] && exit 1 || exit 0
fi

fail=0

echo "=== Gate 4: import sanity (catches missed re-exports) ==="
$PY - <<'EOF'
import importlib, sys
mods = ["brain","server",
        "handlers.chat","handlers.admin","handlers.projects","handlers.sessions_handler",
        "handlers.providers","handlers.translate","handlers.auth","handlers.favourites",
        "handlers.share","handlers.sidecar_proxy","handlers.classification",
        "server_lib.db","server_lib.auth","engine.doc_convert","engine.classification",
        "engine.kg_extract"]
bad=0
for m in mods:
    try: importlib.import_module(m)
    except Exception as e:
        print("  FAIL", m, "->", repr(e)[:140]); bad+=1
print(f"  {len(mods)-bad}/{len(mods)} import clean")
sys.exit(1 if bad else 0)
EOF
[ $? -ne 0 ] && fail=1

echo ""
echo "=== Gate 5a: test suite (stdlib unittest, no pytest needed) ==="
out=$($PY -m unittest discover -s tests -p "test_*.py" 2>&1)
newfails=$(echo "$out" | grep -E "^(FAIL|ERROR):" | sed -E 's/^(FAIL|ERROR): ([a-zA-Z0-9_]+).*/\2/' \
           | grep -vF -w -f <(printf '%s\n' $KNOWN_FAILS) )
echo "$out" | tail -1
if [ -n "$newfails" ]; then
  echo "  NEW failures beyond the 3 known NER-env ones:"; echo "$newfails" | sed 's/^/    /'; fail=1
else
  echo "  OK — no new failures beyond the 3 known NER-env baseline."
fi

echo ""
if [ $fail -eq 0 ]; then echo "GATE PASS ✓"; else echo "GATE FAIL ✗ — do not commit."; fi
exit $fail
