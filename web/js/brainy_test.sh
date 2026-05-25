#!/usr/bin/env bash
# Brainy test runner — view-context (deterministic) + E2E (real LLM).
#
#   ./brainy_test.sh            # both specs
#   ./brainy_test.sh context    # only the deterministic view-context checks
#   ./brainy_test.sh e2e        # only the real-LLM end-to-end checks
#
# Requires: dev server on 127.0.0.1:8420, admin/admin login, and (for e2e) a
# resolvable Brainy model (config.json → helpdesk.model or a server default_model).
set -euo pipefail
cd "$(dirname "$0")"

CFG=playwright.brainy.config.js
which=${1:-all}

if ! curl -s -m 3 -o /dev/null http://127.0.0.1:8420/ ; then
  echo "✗ Dev server not reachable at 127.0.0.1:8420 — start it first." >&2
  exit 1
fi

case "$which" in
  context) npx playwright test -c "$CFG" brainy_context.spec.js ;;
  e2e)     npx playwright test -c "$CFG" brainy_e2e.spec.js ;;
  all)     npx playwright test -c "$CFG" ;;
  *) echo "usage: $0 [context|e2e|all]" >&2; exit 2 ;;
esac
