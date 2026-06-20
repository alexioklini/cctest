# LLM / Model Call Catalog — every use-case, consolidated

Generated 2026-06-20 from live code + `config.json`. Consolidated so you can see **what shares a knob and what shares a model**.

---

## TL;DR — the 5 knobs that actually decide a chat-LLM model

| # | Config knob | GUI location | Current model | Drives these use-cases |
|---|---|---|---|---|
| **A** | `default_model` | Service-Modelle → *Server-Standardmodell* | **mistral-medium-3.5** | Fallback chat model; Brainy/background floor |
| **B** | `classifier_model` | Service-Modelle → *Prompt-Klassifikation* | **mistral-small** | Auto-route prompt classifier (+ per-turn tool-gating) |
| **C** | `chat_summary_model` | Service-Modelle → *Chat-Zusammenfassung* | **Lokal-M4 / Qwen2.5-7B** | Chat summary · ALL 5 wiki ops · user-profile daemon · Studio fallback |
| **D** | `background_task_model` | Service-Modelle → *Fan-out-Hintergrundmodell* | **mistral-small** | Fan-out / detached background sub-tasks |
| **E** | `mempalace.kg.extraction_model` | Service-Modelle → *KG-Extraktion* | **mistral-small** | Knowledge-graph triple extraction |

Everything else either: pins **the session model** (interactive chat, next-prompt, delegate), falls back to **`_background_model_default()`** (= cheapest enabled model, when its own knob is unset), or uses a **specialized non-chat model** (OCR/TTS/STT/embeddings/reranker/NER — see §4).

> **Note on B/C:** these were ONE knob until v9.164.0 (today). `_resolve_classifier_model()` now reads **B first**, falls back to **C**, then to cheapest/local. So if B is ever unset, the classifier silently rides on C again.

---

## §1 — Interactive & user-facing chat LLM calls

| Use-case | Call site | Model = which knob | Fallback chain | cost_purpose |
|---|---|---|---|---|
| **Interactive chat turn** | `handlers/chat.py` worker → `sidecar_proxy.run_turn` | `session.model` (agent-pinned, OR auto-routed at turn 0 / per-turn) | sidecar ACL/quota → `_fallback_model_used`; quota `force_local` → knob (F); GDPR → knob (G) | `chat` |
| **Auto-route model pick** (the "Auto" picker) | `resolve_auto_model_for_task` → `_resolve_auto_model_tiered` | benchmark-rank by task-type tier (reasoning/default/fast) × complexity | never-empty: top-priority enabled model | (part of `chat`) |
| **Auto-route classifier** (the JSON routing call) | `brain.classify_task_structured` :10282 | **B** `classifier_model` | B → **C** → cheapest/local; **fail-open to keyword classifier** on error | `auto_route_classify` |
| **Per-turn tool-gating** (concrete-model sessions) | same `classify_task_structured` | **B** (same call) | same | `auto_route_classify` |
| **next-prompt suggestion** | `brain.generate_next_prompt_suggestion` :5433 | **session.model** (or agent override) | GDPR → abort returns None | `next_prompt` |
| **/v1/refine — Polish & Engineer** | `handlers/admin_artifacts._handle_refine` | request arg → `tools_config.refinement.model` → `_background_model_default()` | GDPR swap-to-local | `refine` |
| **Soul/Persona editor chat** | same handler (purpose=soul) | same as refine | same | `soul_chat` |
| **Brainy helpdesk chat** | `sidecar_proxy.helpdesk_call` :1076 | `helpdesk.model` = **mistral-small** | → `default_model` (A) | `helpdesk` |
| **Brainy search-term extraction** | `handlers/helpdesk.py` :193 | `helpdesk.model` | same | `helpdesk` |

**Auto-route classifier mode** = `auto_route.classifier_mode` (GUI: Server → Auto-Routing). Currently **`llm`**. (`keywords` = no LLM; `hybrid` = keywords first, LLM on miss.)

---

## §2 — Background / daemon chat LLM calls (the `background_call` seam)

> All route through `sidecar_proxy.background_call` → **one `cost_log` row each** (even $0). GDPR-gated where noted.

### Shares knob **C** (`chat_summary_model` = Lokal-M4 7B) — the "small background model"
| Use-case | Call site | Fallback | cost_purpose | GDPR |
|---|---|---|---|---|
| Chat summary (sidebar synopsis) | `handlers/chat.py` :1147 | → `_background_model_default()` | `chat_summary` | yes |
| Wiki auto-tagging | `engine/wiki_store.py` :112 | → bg-default → `[]` | `wiki` | no |
| Wiki page summarization | `engine/wiki_gen.py` :83 | → bg-default | `wiki` | no |
| Wiki podcast script | `engine/wiki_gen.py` :134 | → bg-default | `wiki` | no |
| Wiki diff-merge re-wikify | `engine/wiki_store.py` :640 | → bg-default | `wiki` | no |
| Wiki chat→page organize | `engine/wiki_store.py` :764 | → bg-default | `wiki` | no |
| User-profile daemon | `server.py` :3305 | → bg-default → prior profile | `user_profile` | yes |
| Studio outputs (study guide/briefing/FAQ/timeline) | `engine/output_gen.py` :212 | bg-default OR C | `studio` | — |

### Shares knob **E** (`kg.extraction_model` = mistral-small)
| KG triple extraction | `engine/kg_extract.py` :687 | GDPR-swap → passed model | `kg_extract` | yes |

### Uses `_background_model_default()` (cheapest enabled — no own knob)
| Use-case | Call site | cost_purpose |
|---|---|---|
| Audio Overview dialogue script | `engine/audio_overview.py` :354 | `audio_overview` |
| Code-graph summaries | `engine/code_graph.py` :1003 | `code_graph_summary` |
| Code-mode init | `engine/code_init.py` :160 | `code_init` |
| LCM summarize / condense / recall | `brain.py` :7867 / :7965 / :8319 | `lcm_summarize` / `lcm_condense` / `lcm_recall` |
| Deep Research loop | `engine/deep_research.py` :193 | `deep_research` |

### Pins the session/task/subagent model (not a global knob)
| Use-case | Call site | cost_purpose |
|---|---|---|
| Detached background task | `engine/background_tasks.py` :288 | `background_task` (tool-purpose `interactive`) |
| Fan-out sub-tasks | (fan-out join) | uses **D** `background_task_model` |
| Delegate to subagent | `brain.py` :6703 | `delegate_task` (subagent's own model) |
| `ask_llm` tool | `engine/tools/ask_tools.py` :118 | `ask_llm` |
| Citation re-round | (chat.py, direct `run_turn_blocking`) | `citation_reround` |

### ⚠️ Orphaned knob
| `mempalace.chat_sync.classifier.model` = **Lokal-M4 7B** | The `_MEMORY_CLASSIFIER_PROMPT` call (brain.py:9503, `cost_purpose=memory_classifier`) still EXISTS, but its **driving daemon is retired** (`server_daemons.py:591` — "wiki is the sole feeder"). So this knob currently drives nothing live. **Candidate to remove/ignore.** |

---

## §3 — Two cross-cutting model-SWAP mechanisms (apply on top of any of the above)

| # | Mechanism | Knob | GUI | Current target | Effect |
|---|---|---|---|---|---|
| **F** | Quota `force_local` | `quotas.enforce_red` + `quotas.default_local_fallback_model` | Settings → Quotas | mode `warn_only`; target **EMPTY** | If mode were `force_local`, swaps over-quota user's model → target. Empty target ⇒ no-op. |
| **G** | GDPR / classification swap-to-local | `gdpr_scanner.default_local_fallback_model` · `classification_scanner.default_local_fallback_model` | Settings → GDPR / Classification | both **gemma-4-26B** | On PII/classification hit (policy=swap), background/chat model → gemma-4-26B (local). Single seam `gdpr_pick_model_for_background`. |

> GDPR/classification detection itself is **pure code** (regex + spaCy NER), NOT an LLM — see §4.

---

## §4 — Specialized (non-chat-LLM) model calls

| Use-case | Type | Knob | Current model | Billing |
|---|---|---|---|---|
| **Embeddings** (MemPalace) | MLX embedding, in-process | mempalace venv (not in config.json) | `embeddinggemma-300m` (MLX) | none ($0, local) |
| **Reranker** (MemPalace query) | cross-encoder, in-process | `mempalace.reranker.model` | `BAAI/bge-reranker-v2-m3` | none ($0, local) |
| **OCR — cloud** | specialized OCR | `ocr.model` (engine=`mistral_ocr`) | `mistral-ocr-latest` | `log_ocr`, per-page |
| **OCR — local** | vision chat model | `ocr.local_vision_model` (engine=`local_vision`) | `gemma-4-26B` | `log_ocr`, $0 |
| **Image-describe** (non-vision attachments) | vision chat model | `attachments.image_model` | `mistral-medium-3.5` | inline chat cost |
| **TTS / Audio voices / read-aloud** | Voxtral TTS | `tool_settings.text_to_speech.default_model` | Voxtral (Mistral `/audio/speech`) | `log_tts` / Mistral |
| **STT / transcription** | Whisper (local) OR Voxtral (cloud) | `tool_settings.transcribe_audio.default_model` | whisper-* (local, $0) or voxtral (cloud) | $0 or Mistral |
| **Language detection** | lingua (offline) + LLM fallback | lingua primary; LLM = `_background_model_default()` | lingua; mistral-small fallback | `lang_detect` |
| **GDPR PII detection** | **pure code** (regex + spaCy NER) | `gdpr_scanner` rules | spaCy `de_core_news_md` | none (no LLM) |
| **Document classification (ARL)** | **pure code** (regex/keywords) | `classification_scanner` | — | none (no LLM) |

---

## §5 — Translation (its own knob group)

All via `background_call`, GDPR-gated (pseudonymizes source). Knob: `tool_settings.translation.default_model`; tone-rewrite falls back to `tool_settings.refinement.model`.

| Use-case | Call site | cost_purpose |
|---|---|---|
| Text translate | `server_lib/translate/text.py` :143 | `translate_text` |
| Text tone rewrite | `text.py` :181 | `translate_text_rewrite` |
| Document translate (chunked) | `server_lib/translate/document.py` :240 | `translate_document` |
| Document tone rewrite | `document.py` :199 | `translate_document_rewrite` |
| Language detect (fallback) | `server_lib/translate/detect.py` :95 | `lang_detect` |

---

## §6 — Misc

| Use-case | Knob | Current model |
|---|---|---|
| **Telegram frontend** chat | `telegram.model` | **claude-opus-4-6** |

---

## What's the SAME (consolidation summary)

- **mistral-small** is the single model behind: classifier (B), fan-out (D), KG extraction (E), helpdesk (Brainy), lang-detect fallback. → 5 jobs, but **3 separate knobs** (B, D, E) + 2 derived.
- **Lokal-M4 7B** (knob C) is behind: chat summary + **all 5 wiki ops** + user-profile + Studio fallback. → one knob, ~8 jobs. *Plus* the orphaned chat-sync classifier knob (dead).
- **mistral-medium-3.5** is: `default_model` (A) AND `attachments.image_model`. → 2 distinct knobs, same model today.
- **gemma-4-26B** is: GDPR swap target (G) AND classification swap target (G) AND local OCR. → 3 jobs, all local-fallback flavored.
- **`_background_model_default()` = cheapest enabled model** is the silent fallback for ~8 background jobs whose own knob is unset (audio, code-graph, code-init, LCM ×3, deep-research) — so they all move together if the cheapest model changes.
