#!/usr/bin/env bash
# refactor_gate.sh — completeness + regression gate for the module-extraction refactor.
# Run after EVERY extraction. A refactor is "done" only when this passes AND the
# per-extraction grep proof (gate 2: old symbol gone from live code) is clean.
#
# Usage:  ./refactor_gate.sh                  # full gate
#         ./refactor_gate.sh grep <SYMBOL>    # gate-2 helper: prove old copy is gone
#         ./refactor_gate.sh tlgrep <ATTR>    # Tier-G: prove raw _thread_local.<attr> is gone
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

if [ "${1:-}" = "tlgrep" ]; then
  attr="$2"
  # Tier-G gate component 2: a MIGRATED request-context attribute must have NO
  # remaining raw `_thread_local.<attr>` read/write in live code. The only
  # allowed mention is the shim/accessor definition in engine/context.py (the
  # ContextVar-backed storage) — every other reference must go through a typed
  # accessor. A surviving raw access = NOT DONE for that attr -> finish or revert.
  echo "=== Tier-G: raw _thread_local.$attr must be gone from live code (engine/context.py exempt) ==="
  # Match every prefix form the codebase uses for the shim name: bare
  # `_thread_local`, `brain._thread_local`, `_brain._thread_local`,
  # `engine._thread_local` (the `import brain as engine` alias). The optional
  # `(\w+\.)?` prefix catches all of them in the .X / getattr / setattr shapes.
  P='(\w+\.)?_thread_local'
  hits=$(grep -rnE "${P}\.$attr\b|getattr\(${P}, ['\"]$attr['\"]|setattr\(${P}, ['\"]$attr['\"]" \
           brain.py execution.py engine/ handlers/ server_lib/ server.py server_daemons.py 2>/dev/null \
           | grep -v '^engine/context\.py:' \
           | grep -vE '^\S+:[0-9]+:\s*#')
  if [ -n "$hits" ]; then
    echo "  FAIL — raw access of '$attr' still in live code:"
    echo "$hits" | sed 's/^/    /'
    echo "  -> route through the typed accessor or revert. NOT migrated."
    exit 1
  else
    echo "  OK — no raw _thread_local.$attr access outside engine/context.py."
    exit 0
  fi
fi

fail=0

echo "=== Gate 4: import sanity (catches missed re-exports) ==="
$PY - <<'EOF'
import importlib, sys
mods = ["brain","server","execution",
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
echo "=== Gate 4b: PII scanner Python<->JS parity (drift checker, U5) ==="
$PY tools/check_pii_js_parity.py | sed 's/^/  /'
[ ${PIPESTATUS[0]} -ne 0 ] && fail=1

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
echo "=== Gate 5b: request-context no-bleed property (Tier-G headline criterion) ==="
out2=$($PY -m unittest tests.test_request_context_isolation 2>&1)
if echo "$out2" | tail -1 | grep -q '^OK'; then
  echo "  OK — concurrency/bleed + teardown-residue + negative-control all pass."
else
  echo "$out2" | tail -6 | sed 's/^/  /'; fail=1
fi

echo ""
if [ $fail -eq 0 ]; then echo "GATE PASS ✓"; else echo "GATE FAIL ✗ — do not commit."; fi
exit $fail
