---
name: feedback_commit_to_main
description: "For the cctest/brain-agent repo, commit and push directly to main — do not create feature branches."
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 9ece1edb-fc23-4a61-92a5-90f59f5018a8
---

For this repo (cctest / brain-agent), always commit and push directly to `main`. Do NOT create a feature branch or open a PR unless explicitly asked.

**Why:** The user is the sole developer here and works trunk-based; a branch+PR per change is friction they don't want. Stated explicitly 2026-05-28 after I branched `feat/memdash-dashboard` instead of committing to main.

**How to apply:** Skip the "branch before committing on the default branch" default for this repo. When asked to commit/push, do it on `main` and `git push origin main`. Still keep unrelated pre-existing dirty files out of the commit, and keep the `Co-Authored-By: Claude Opus 4.7` trailer.
