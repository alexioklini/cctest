---
name: project-anonymise-skip-attachment-notice
description: "2026-05-19 — chat anonymise/history pass now skips the Brain-generated attachment notice ([User attached files saved to disk. IMPORTANT: ...]\\n  - /tmp/.../file.docx). NER was misclassifying \"IMPORTANT\" as organisation and filenames as addresses, breaking read_document."
metadata: 
  node_type: memory
  type: project
  originSessionId: 22620a72-629b-4be0-9f54-e2f83900b63c
---

Symptom (chat `2255354c76c5`): user attached `Protokoll_Entwurf_11.06.2025_CD_KL_clean.docx` and typed "was ist das". The anonymise chat_text pass scanned the full user message including the system-appended attachment notice. spaCy German NER returned two false positives:

- `"IMPORTANT"` → org → replaced with `"Hooli Corp"`.
- `"Protokoll_Entwurf_11.06.2025_CD_KL_clean.docx"` → LOC/address → replaced with `"Oakstraße 52, Fairview"`.

The LLM received the fake path and `read_document` failed with "file not found".

**Fix** (handlers/chat.py, 2026-05-19):

- New module-level `_split_attachment_notice(text)` returns `(typed_part, notice_part)`. The notice is matched by stable prefix (`\n\n[User attached files saved to disk` or `\n\n[User attached image(s)`) using `rfind`.
- Worker anonymise branch (~L1985) now scans only `typed`, pseudonymises only `typed`, and glues `notice` back onto the result before assigning to `nonlocal_message` / user_content blocks.
- `_pseudonymize_history_for_wire` (string + list-of-blocks branches) applies the same split — the persisted prior-turn user message in `session.messages` still carries the notice, so without this the same FPs would re-fire on follow-up turns and could mint new tokens diverging from the original mapping.

**Why:** The notice is deterministic Brain-generated boilerplate plus literal `/tmp/brain-attachments/<sid>/<filename>` paths the model needs verbatim. It carries no privacy risk: the path is server-internal, the filename is a string the user already chose locally, and pseudonymising any of it strictly degrades behavior. Browser-side scanner already skips it (different code path — it only sees the typed input box content), so the server-side scanner being broader was the asymmetry causing the bug.

**How to apply:** Any future place that scans the persisted user message for PII (history walk, eval harness pre-flight, audit re-scan) should call `_split_attachment_notice` and scan only the typed half. Never pseudonymise the disk path the model needs to call `read_document`/`read_file` on.
