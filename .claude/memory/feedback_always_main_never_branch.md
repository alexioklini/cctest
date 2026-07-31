---
name: feedback_always_main_never_branch
description: "Git-Workflow — immer direkt auf main committen/pushen, niemals ungefragt einen Branch anlegen"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 78498eb4-5ff3-4a7e-ae09-234032c1a087
---

Immer direkt auf `main` arbeiten (committen + pushen). NIEMALS ungefragt einen Feature-Branch/PR anlegen — nur wenn der User es ausdrücklich verlangt.

**Why:** Solo-Repo, der User pflegt einen Trunk-Workflow direkt auf main. Die Standard-Harness-Empfehlung ("auf Default-Branch zuerst branchen") gilt hier explizit NICHT.

**How to apply:** Bei "commit and push" o.ä. nicht nach Branch fragen, nicht ExitPlanMode/AskUserQuestion für die Branch-Entscheidung nutzen — direkt `git add` → `commit` → `git push origin main`. Commit-Messages weiter mit Co-Authored-By-Trailer. Versionsbump in zwei Stellen beachten ([[feedback_version_two_places]]).
