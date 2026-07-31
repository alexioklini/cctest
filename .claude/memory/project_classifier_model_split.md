---
name: project_classifier_model_split
description: "v9.164.0 split the auto-route prompt classifier onto its own classifier_model knob, separate from chat_summary_model (they shared one before). Classifier→cloud mistral-small, summary→local M4 7B."
metadata: 
  node_type: memory
  type: project
  originSessionId: 4ba4b9bc-7f50-4609-a70f-dac96a6d1484
---

v9.164.0 (2026-06-19): the auto-route **prompt classifier** and **chat summary** used to share ONE config knob — `chat_summary_model`. `_resolve_classifier_model()` (brain.py ~10170) read it directly ("one setting for all tiny background calls"), so flipping the model for the classifier also moved the summary (+ wiki tagging + profile daemon). This burned a whole session: user asked to revert ONLY prompt-classification to mistral; flipping chat_summary_model dragged the summary off the M4 7B too.

**FIX:** new optional top-level `classifier_model` knob. `_resolve_classifier_model()` now checks `classifier_model` first (known+enabled), falls back to `chat_summary_model`, then cheapest/local — unset = old behavior. Wired at 4 sites: boot-loader (server.py seeds `server_config['classifier_model']`), setter `/v1/services/server` (handlers/admin_config.py), AND the unified Service-Modelle panel (handlers/admin_observability.py `_SERVICE_MODEL_SLOTS` + read + write → new GUI slot "Prompt-Klassifikation (Auto-Routing)"). Skill 05-internals + 01-api updated; version bumped both places (1.80.0 / 9.164.0).

**LIVE state set:** `classifier_model=CLIProxyAPI/mistral-small-latest`, `chat_summary_model=Lokal-M4/Qwen2.5-7B-Instruct-4bit`. Bench ([[project_m4_vllm_metal_deployed]] follow-up) showed cloud mistral-small classifies 100%@0.55s vs local Qwen-7B 100%@1.82s vs Qwen-3B 85%@1.10s → classifier belongs on cloud, summary can stay local.

**The 3 eval use-cases** (eval/bg_tasks_local_eval.py): (1) auto-route prompt classifier + (2) chat summary BOTH on `chat_summary_model`; (3) memory classifier on `mempalace.chat_sync.classifier.model` (still M4 7B). The 3B (Qwen2.5-3B) was benched on M4 :8013 then SHUT DOWN — rejected (drops implicit-internal-source routing cases). M4 :8012 serves only the 7B.

⚠️ DURING this work I corrupted config.json (models dict wiped 93→0) via the setter attempts — recovered from `config.json.bak-chatsummary-20260619-212300`. Damaged copy kept as `config.json.bak-DAMAGED-models-wiped-20260619-2227`. The exact wipe path was never found (failing setter returns before write, yet file truncated) — worth investigating if it recurs.
