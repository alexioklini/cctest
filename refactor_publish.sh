#!/usr/bin/env bash
# refactor_publish.sh — regenerate the HTML view of the report and push to remote.
# Call this after each report update so the remote HTML stays current and viewable.
#   - HTML is GENERATED from REFACTOR_REPORT.md (the source of truth); never hand-edited.
#   - Pushes to origin/main so REFACTOR_REPORT.html is viewable remotely on GitHub.
# Usage:  ./refactor_publish.sh "commit message"
set -eu
PY=/opt/homebrew/bin/python3
cd "$(dirname "$0")"
msg="${1:-docs(refactor): update progress report}"

$PY refactor_report_html.py                       # regenerate the HTML view
git add REFACTOR_REPORT.md REFACTOR_REPORT.html
git commit -q -m "$msg

Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>" || echo "(nothing to commit)"
git push -q origin main && echo "pushed -> https://github.com/alexioklini/cctest/blob/main/REFACTOR_REPORT.html"
