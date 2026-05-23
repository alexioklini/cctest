#!/usr/bin/env bash
# refactor_publish.sh — regenerate the HTML view of the report and push to remote.
# Call this after each report update so the remote HTML stays current and viewable.
#   - HTML is GENERATED from REFACTOR_REPORT.md (the source of truth); never hand-edited.
#   - Pushes to origin/main so REFACTOR_REPORT.html is viewable remotely on GitHub.
# Usage:  ./refactor_publish.sh "commit message" [REPORT.md]
#   REPORT.md defaults to REFACTOR_REPORT.md; pass JS_REFACTOR_REPORT.md for Tier F.
set -eu
PY=/opt/homebrew/bin/python3
cd "$(dirname "$0")"
msg="${1:-docs(refactor): update progress report}"
report="${2:-REFACTOR_REPORT.md}"
html="${report%.md}.html"

$PY refactor_report_html.py "$report"             # regenerate the HTML view
git add "$report" "$html"
git commit -q -m "$msg

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>" || echo "(nothing to commit)"
git push -q origin main && echo "pushed -> https://github.com/alexioklini/cctest/blob/main/$html"
