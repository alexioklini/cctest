# LLM / Model Call Catalog

Every LLM/model use-case in brain-agent: what the user does, what they get, which setting controls it, the GUI location, and the current model. Reflects **v9.173.0** (live values 2026-06-20).

After this session's decomposition, **every background LLM task has its own dedicated model knob** — no more silent sharing. Most live in **Settings → Service-Modelle** (a slot grid that renders + saves generically).

---

## A. Interactive / user-facing chat

### 1. Send a chat message (main turn)
- **Does:** types + sends a message in a chat / project chat.
- **Gets:** streamed assistant reply with tools.
- **Setting:** per-chat model picker. New chats default to `default_model`.
- **GUI:** chat composer dropdown · default = Service-Modelle → *Server-Standardmodell*.
- **Model:** per-chat; default **mistral-medium-3.5**.
- **Fallbacks:** sidecar ACL/quota swap · quota force_local (off) · GDPR swap → gemma-4-26B.

### 2. "Auto" model selection
- **Does:** sets the chat model to ✨ Auto (Cloud/Lokal).
- **Gets:** best-fit model picked per message by task tier × complexity.
- **Setting:** `auto_route.classifier_mode` (keywords / **llm** / hybrid).
- **GUI:** Settings → Server → *Auto-Routing*.

### 3. Auto-route prompt classifier (the routing call behind #2)
- **Does:** indirect — fires on Auto-routing + per-turn tool-gating.
- **Gets:** structured `{task_types, tools, complexity}` driving model tier + tools.
- **Setting:** `classifier_model` → cheapest/local fallback (no longer rides chat_summary).
- **GUI:** Service-Modelle → *Prompt-Klassifikation (Auto-Routing)*.
- **Model:** **mistral-small** · cost_purpose `auto_route_classify`.

### 4. Next-prompt suggestion
- **Does:** finishes a turn / presses Tab on empty composer.
- **Gets:** a ghosted follow-up prompt suggestion.
- **Setting:** `next_prompt_model` (empty = the chat's current model).
- **GUI:** Service-Modelle → *Nächster-Prompt-Vorschlag (leer = Chat-Modell)*.
- **Model:** **M4 7B** · cost_purpose `next_prompt`.

### 5. Refine prompt (✨ Refine — Polish / Engineer)
- **Does:** clicks Refine on the composer (also Soul editor, profile-field).
- **Gets:** rewritten composer/soul/bio text.
- **Setting:** `refinement.model` (request arg wins; else default).
- **GUI:** Settings → Tools → refinement → model.
- **Model:** **M4 7B** · cost_purpose `refine` / `soul_chat`. GDPR-gated.

### 6. Brainy helpdesk bot (🧠)
- **Does:** asks the floating Brainy buddy about brain-agent itself.
- **Gets:** read-only helpdesk reply (+ a search-term extraction sub-call).
- **Setting:** `helpdesk.model` → default fallback.
- **GUI:** Settings → Tools → Brainy tab (model + max-rounds + system prompt).
- **Model:** **mistral-small** · cost_purpose `helpdesk`.

---

## B. Memory / wiki / knowledge

### 7. Chat summary (sidebar synopsis)
- **Does:** nothing — auto after each turn.
- **Gets:** the one-line summary under each chat in the sidebar.
- **Setting:** `chat_summary_model` (**now exclusive** to this — was shared).
- **GUI:** Service-Modelle → *Chat-Zusammenfassung* (also Server tab).
- **Model:** **M4 7B** · cost_purpose `chat_summary`. GDPR-gated.

### 8. Auto-memory gate (Auto mode: should this chat go to the wiki?)
- **Does:** sets a chat's memory toggle to **Auto**.
- **Gets:** an LLM judges the whole conversation SAVE/SKIP; only memorable chats (fact/preference/decision/reference) get wikified. "On" mode always files; "Off" never.
- **Setting:** `wiki_gate_model` (empty = background default). Fail-open.
- **GUI:** Service-Modelle → *Wiki-Auto-Gate (Auto-Memory: merken?)*.
- **Model:** **M4 7B** · cost_purpose `wiki_gate`. GDPR-gated. *(Successor to the removed per-turn memory classifier — now per-SESSION + wiki-aware.)*

### 9. Wiki operations (tagging · page summary · podcast script · diff-merge · chat→page)
- **Does:** memorizes a chat ("merken"), or auto-fed wiki pages from chats/Studio/profile; generates page summaries/podcasts.
- **Gets:** organized wiki pages, auto-tags, generated summaries/audio.
- **Setting:** `wiki_model` (empty = background default).
- **GUI:** Service-Modelle → *Wiki (Tags/Zusammenfassung/Podcast/Merge)*.
- **Model:** **mistral-small** · cost_purpose `wiki`.

### 10. User-profile daemon
- **Does:** nothing — daily background per user.
- **Gets:** an auto-maintained user-context profile (+ a wiki "Profil & Aktivität" page) from recent chat history.
- **Setting:** `user_profile_model` (empty = background default).
- **GUI:** Service-Modelle → *Nutzerprofil-Daemon*.
- **Model:** **M4 7B** · cost_purpose `user_profile`. GDPR-gated.

### 11. Knowledge-graph triple extraction (+ closet regen)
- **Does:** indirect — project-sync mines input folders.
- **Gets:** `{subject,predicate,object}` triples in the project KG; regenerated closets.
- **Setting:** `mempalace.kg.extraction_model` (drives both extraction + closet regen).
- **GUI:** Settings → Knowledge-Graph · Service-Modelle → *KG-Extraktion*.
- **Model:** **mistral-small** · cost_purpose `kg_extract`. GDPR-gated.

### 12. Lossless Context Manager (compaction: summarize / condense / recall)
- **Does:** clicks ✂️ in the status bar on a long chat.
- **Gets:** older messages hierarchically compacted (still searchable).
- **Setting:** none of its own — uses the **chat's `session.model`**.
- **GUI:** none (just the ✂️ trigger + ≥60% warning banner).
- **Model:** the active chat's model · cost_purpose `lcm_summarize`/`lcm_condense`/`lcm_recall`. GDPR-gated.

---

## C. Project outputs / research

### 13. Studio outputs (study guide / briefing / FAQ / timeline)
- **Does:** clicks a preset in a project's Studio tab.
- **Gets:** a generated cited markdown report.
- **Setting:** `studio_model` (empty = background default).
- **GUI:** Service-Modelle → *Studio (Projekt-Outputs)*.
- **Model:** **mistral-small** · cost_purpose `studio`.

### 14. Deep Research (agentic research loop)
- **Does:** clicks 🔍 Research (Deep/Fast) with a topic + budget.
- **Gets:** multi-step search→fetch→verify→synthesize → cited report into Studio.
- **Setting:** `deep_research_model` (empty = background default).
- **GUI:** Service-Modelle → *Deep Research (Recherche-Loop)*.
- **Model:** **mistral-medium-3.5** (stronger — does verification/synthesis) · cost_purpose `deep_research`. GDPR-gated.

### 15. Audio Overview (podcast) + read-aloud
- **Does:** clicks 🎧 podcast (project/chat) or 🔊 read-aloud.
- **Gets:** a two-host dialogue script → stitched .mp3.
- **Setting (script LLM):** `audio_overview_model` (empty = background default).
  **Setting (TTS voice):** `text_to_speech.default_model` (Voxtral).
- **GUI:** Service-Modelle → *Audio Overview (Podcast-Skript)* + *Text-to-Speech*.
- **Models:** script **mistral-small** (cost_purpose `audio_overview`) · voice **voxtral-mini-tts-latest** (`log_tts`).

### 16. Code-graph summaries
- **Does:** indirect — when the code-structure graph builds/updates.
- **Gets:** one-line NL summaries per function/class for code search.
- **Setting:** `code_graph_model` (empty = background default).
- **GUI:** Service-Modelle → *Code-Graph (Symbol-Zusammenfassungen)*.
- **Model:** **M4 7B** · cost_purpose `code_graph_summary`.

---

## D. Tasks / delegation

### 17. Scheduled tasks (recurring/cron runs)
- **Does:** creates a scheduled task (prompt + cron + optional model).
- **Gets:** a headless agentic turn at fire time → schedule_history run.
- **Setting:** per-task model → agent `preferred_model` → default → opus last-resort.
- **GUI:** Schedule create/edit UI (per-task model dropdown).
- **Model:** per-task; empty → mistral-medium · cost_purpose `scheduled`.

### 18. Delegation (`delegate_task`)
- **Does:** indirect — agent hands a sub-task to another agent.
- **Gets:** the target agent runs it; result returns to the caller.
- **Setting:** tool arg → **target agent's `preferred_model`** → default.
- **GUI:** Settings → Agents (per-agent model).
- **Model:** per target agent · cost_purpose `delegate_task`. GDPR-gated.

### 19. Background tasks / fan-out
- **Does:** indirect — agent decomposes + offloads parallel leaf sub-tasks.
- **Gets:** leaf tasks on a (cheaper) offload model; results joined.
- **Setting:** per-model `models.<id>.background_task_model` (+ top-level `background_task_model`; `auto` = classify each leaf).
- **GUI:** Settings → Models → ⚙ per model (*Fan-out-Modell*) + Service-Modelle → *Fan-out-Hintergrundmodell*.
- **Model:** top-level **mistral-small** · cost_purpose `background_task`.

### 20. `ask_llm` tool
- **Does:** indirect — agent/workflow one-shot LLM call (no agentic loop).
- **Gets:** a single response.
- **Setting:** arg → workflow MODEL header → workflow agent → `refinement.model` → default.
- **GUI:** none standalone (caller/workflow specifies).
- **Model:** caller-dependent · cost_purpose `ask_llm`.

---

## E. Translation

### 21. Translation (text / document / rewrite / language-detect)
- **Does:** translates text or an uploaded document (optional tone-rewrite).
- **Gets:** translated (+ polished) text; language auto-detected first.
- **Settings:** `translation.default_model` · tone-rewrite → `refinement.model` · lang-detect = **lingua offline first**, LLM fallback only on low confidence → `translation.detection_fallback_model`.
- **GUI:** Service-Modelle → *Übersetzung* (also Tools tab).
- **Models:** translate **mistral-small** · rewrite **M4 7B** · detect-fallback **M4 7B** *(was a dead model, fixed v9.170.0)*. cost_purpose `translate_text`/`translate_document`/`…_rewrite`/`lang_detect`. GDPR-gated.

---

## F. Specialized (non-chat) models

### 22. OCR (scanned PDFs / images)
- **Does:** uploads/mines a scanned doc with no text layer.
- **Setting:** `ocr.engine` (mistral_ocr / local_vision / auto) + `ocr.model` + `ocr.local_vision_model`.
- **GUI:** Service-Modelle → OCR block.
- **Models:** **mistral-ocr-latest** (cloud, $0.001/page via `log_ocr`) / **gemma-4-26B** (local vision).

### 23. Image-describe (non-vision-model attachments)
- **Does:** attaches an image to a text-only model.
- **Setting:** `attachments.image_model`.
- **GUI:** Settings → Server → Bildmodell.
- **Model:** **mistral-medium-3.5** (vision chat).

### 24. Speech-to-text / transcription
- **Setting:** `transcribe_audio.default_model`.
- **GUI:** Service-Modelle → *Transkription (STT)*.
- **Model:** **voxtral-mini-latest** (cloud) or whisper-* (local, $0).

### 25. Text-to-speech
- **Setting:** `text_to_speech.default_model`.
- **GUI:** Service-Modelle → *Text-to-Speech*.
- **Model:** **voxtral-mini-tts-latest** (`log_tts`). Drives podcast voices + read-aloud.

### 26. MemPalace reranker (retrieval, local cross-encoder — NOT an LLM)
- **Setting:** `mempalace.reranker.model`.
- **GUI:** Settings → MemPalace.
- **Model:** **BAAI/bge-reranker-v2-m3** ($0, local).

### 27. Embeddings (MemPalace, MLX — NOT an LLM)
- **Setting:** MemPalace venv config (NOT brain config.json — venv-level).
- **GUI:** none (changing it needs a venv patch + re-mine).
- **Model:** **embeddinggemma-300m** (MLX, $0, local).

---

## G. Cross-cutting swaps + misc

### 28. GDPR / classification swap-to-local
- **Does:** on PII / classified-document detection (policy=swap), the active model is swapped to a local one. *(Detection itself is pure regex + spaCy NER — no LLM.)*
- **Setting:** `gdpr_scanner.default_local_fallback_model` · `classification_scanner.default_local_fallback_model`.
- **GUI:** Settings → GDPR / Classification.
- **Model:** **gemma-4-26B** (both).

### 29. Quota force_local
- **Setting:** `quotas.enforce_red` + `quotas.default_local_fallback_model`.
- **GUI:** Settings → Quotas.
- **State:** mode `warn_only`; target **empty** → no-op unless configured.

### 30. Telegram frontend
- **Setting:** `telegram.model`.
- **Model:** **claude-opus-4-6**.

---

## Consolidation — what shares a model

- **M4 7B (local):** next-prompt, chat summary, user-profile, code-graph, wiki-gate, refine, translation-rewrite, lang-detect-fallback. *(The cheap-local workhorse.)*
- **mistral-small:** classifier, wiki, KG, studio, audio-script, fan-out, translation, Brainy.
- **mistral-medium-3.5:** default_model, image-describe, deep-research.
- **gemma-4-26B (local):** GDPR swap, classification swap, local OCR.
- **Voxtral:** TTS + STT.
- **Empty knob → `_background_model_default()` = `default_model` (mistral-medium).**

## Removed this session
- The per-turn **memory classifier** (`classify_chat_for_memory`) + its orphaned `mempalace.chat_sync.classifier.model` knob — dead since the chat-sync daemon was retired (wiki is the sole feeder). Replaced by the **Auto-memory gate** (#8).
