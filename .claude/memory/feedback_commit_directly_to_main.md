---
name: feedback_commit_directly_to_main
description: Commit directly to main in this repo; never create a feature branch
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 454afaf1-3a95-4e92-883a-dcd7ef5b54c8
---

In this repo, commit and push directly to `main`. Do NOT create a feature branch, even though the default Claude Code behavior branches when on the default branch.

**Why:** User explicitly said "never branch commit directly" after I branched for a version-bump commit. This is a solo/personal repo where the branch+PR ceremony adds friction with no review benefit.

**How to apply:** When asked to commit/push, stage + commit + push on `main`. Skip branch creation and skip offering to open a PR.
