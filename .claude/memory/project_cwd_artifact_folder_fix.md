---
name: project_cwd_artifact_folder_fix
description: "2026-05-20 fix — system prompt advertised repo root as cwd, model wrote generated files (docx) there with invented absolute paths; replaced with truthful artifact-folder statement + warn-on-stray-abs-write guard"
metadata: 
  node_type: memory
  type: project
  originSessionId: cdcf2ade-2ae8-468c-8bbd-25739e0f2021
---

2026-05-20: fixed the recurring "model writes generated files to the repo root, not the artifacts folder" bug (observed in chat `6c8dc5937f2c`, a docx written to `/Users/alexander/Documents/dev/cctest/Sprechvorlage_*.docx`).

**Root cause:** `_build_system_prompt` (brain.py interactive branch, ~line 24990) printed `Current working directory: {os.getcwd()}` = the server's launchd `WorkingDirectory` = the repo root. That line was FALSE for every interactive call type — chat / project chat / scheduled task / workflow ALL run `python_exec`/`execute_command`/relative `write_file` with cwd = the per-session artifact folder (`agents/<agent>/artifacts/<date>_<session_id>/`), keyed on `_thread_local.current_session_id`. Repo root only applies to session-less background jobs. The model latched onto the advertised repo root and built absolute paths there.

**Fix (3 parts):**
1. System prompt: replaced the `Current working directory: <repo root>` line with a session-AGNOSTIC truthful statement ("Working directory: your session's artifact folder … write with a RELATIVE filename … exact path is in the first message"). No `os.getcwd()`, no session id → KV-prefix stays byte-identical warmup↔turn (verified: both 1042 chars, identical). Removed the now-dead `cwd = os.getcwd()` line.
2. Sharpened `_artifact_folder_preamble_text` (the literal-path first-message preamble, lives there since v9.9.9 for KV-safety) to push relative filenames.
3. Tool guard — WARN, don't redirect (user's explicit choice). New `_stray_write_warning(text, artifact_dir)` + `_append_to_tool_result(json, suffix)` helpers in brain.py. `write_file` checks its resolved abs path; `python_exec`/`execute_command` scan the code/command via `_ABS_PATH_RE` for abs-path literals outside the artifact folder. Skips `/tmp /var /usr /etc /System /Library /dev /proc` (no noise on attachment reads / temp files) and paths inside the artifact folder. Appends a ⚠️ warning to the tool result so the model re-saves relative. Both `execute_command` return branches (streaming `_res` JSON + non-streaming `result`) covered.

**Why warn not redirect:** user wanted non-invasive — a deliberate absolute write (e.g. into a project input folder, or a user-given path) must not be hijacked.

Note: `task_working_dir` param of `_build_system_prompt` is DEAD (never consumed in body) — CLAUDE.md's claim that scheduled `working_dir` "overrides the cwd line" is stale. Left untouched (separate concern). See [[feedback_kv_cache_stability]] [[feedback_single_fix_point]].
