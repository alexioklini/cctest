---
name: project_project_composer_attachment_drop
description: Bug+fix — files attached on the PROJECT-LANDING composer were silently dropped on send because newChat() wipes _pendingFiles before filesToSend is captured
metadata: 
  node_type: memory
  type: project
  originSessionId: 5d571558-3f48-4dab-a29a-10b41e032c14
---

v9.157.2 (2026-06-17): a file attached in the **project-landing composer** (`#project-input`, "Ihre Nachricht an <Projekt>") was silently dropped on send — the turn ran without it and the model answered from the project wing only. Reported via chat `7e636f00` (an Excel never reached the LLM).

**Root cause:** `sendMessage()` (web/js/chat_send.js) in its `project-detail` branch calls `newChat()` to start a fresh session for the project turn. `newChat()` (web/js/sessions.js ~line 561) resets composer state including `state._pendingFiles = []`. That wipe runs BEFORE `sendMessage` captures `filesToSend = state._pendingFiles…` (~line 302) → `body.files` empty. The composer TEXT survives (already read into local `text` earlier); only files/images are lost.

**Why only this composer:** three composers share ONE global `#file-input` → `state._pendingFiles` (attach works everywhere), but only the project-detail send path calls `newChat()`. welcome-input / chat-input were never affected.

**Fix:** snapshot `_pendingFiles`/`_pendingImages` before `newChat()` and restore after (+ `renderFilePreviews()`). Frontend-only, no Brain restart.

**Verified LIVE in Chrome** (project "KO Kunden"): marker file → `newChat()` wipes to 0 (bug); snapshot/restore keeps it (fix). Server-side bug signature: NO `/tmp/brain-attachments/<sid>/`, no `read_document`, no `/v1/attachments/scan`, no attachment notice in the stored user message. Related extraction work: [[project_pdf_extraction_backends_eval]].
