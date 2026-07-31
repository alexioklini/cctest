---
name: feedback_never_probe_server_config_via_import
description: "never diagnose live server_config (auto_route, classifier_mode, chat_summary_model, etc.) via a standalone `python3 -c \"import brain\"` — it has no server_config and returns defaults; probe the running process"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: d08a80f9-bb28-4076-be0f-75421d0537d0
---

To check anything that reads `brain._server_config()` (e.g. `classifier_is_llm()`, classifier_mode, chat_summary_model, default_model, auto_route.*), do NOT test via a fresh `python3 -c "import brain; ..."`. That is a SEPARATE process where `sys.modules['__main__']` is the `-c` script and `server` was never imported, so `_server_config()` resolves to `{}` and every reader falls to its default ('keywords', etc.) — looking like a bug that isn't there. This is the v9.60.1 "dual-module footgun" surfacing in a CLI probe.

**Why:** the live config only exists on the SINGLETON server module (`sys.modules['__main__']` under launchd), populated by `server.main()`. A standalone import never runs main(), so it never has it.

**How to apply:** probe the RUNNING process instead — a server endpoint (e.g. `/v1/services` for classifier_mode) or the server log (`server.error.log`). v9.73.2 shipped a false "classifier_is_llm() returns False server-side" note from exactly this mistake; retracted in v9.73.3 after the live log showed `mode='llm' sc_has_auto_route=True`. Related: [[feedback_compile_check_brain_py]] (also about verifying against the live server, not assumptions).
