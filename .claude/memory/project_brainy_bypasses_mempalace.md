---
name: project_brainy_bypasses_mempalace
description: "Warum Brainy bessere Antworten gibt als Projekt-Chats — er umgeht MemPalace komplett (liest Skill-Dateien direkt, kein Chunking/Retrieval)"
metadata: 
  node_type: memory
  type: project
  originSessionId: 9ca0dd2a-02c9-494f-99df-841efee16f3b
---

2026-05-27: Verifiziert im Code, warum Brainy (helpdesk-Bot) durchgängig bessere, besser strukturierte Antworten liefert als Projekt-Chats — der Grund ist DOPPELT:

**1. Brainy umgeht MemPalace vollständig.** Sein Systemprompt (`handlers/helpdesk.py` `_HELPDESK_DEFAULT_PROMPT`) befiehlt: "Lade ZUERST den Skill `brain-agent-guide` mit use_skill." `tool_use_skill` (`engine/tools/misc_tools.py`) liest die SKILL.md direkt vom Dateisystem (Volltext als `instructions`) UND gibt die absoluten Pfade aller Companion-Seiten (01-api.md … 06-user-manual.md) zurück; Brainy liest die relevante Seite dann mit `read_document` ganz von der Platte. KEIN mempalace_query, KEIN Embedding, KEIN ~800-Zeichen-Chunking, KEIN Reranker.

**2. Brainys Quelle ist handgepflegt** (brain-agent-guide = redaktionelles, LLM-freundliches Markdown, ein Thema pro Sektion). Siehe [[project_doc_refinement_before_mining]].

⇒ Brainy bekommt ganze, saubere Dateien am Stück in den Kontext. Das Schwierige bei Projekt-Chats — richtiges Dokument FINDEN (mempalace_query) + richtiges Chunk TREFFEN (Reranker) — entfällt per Konstruktion. Das erklärt vollständig, warum Projekt-Chats mehr "Babysitting" brauchen.

**Trade-off (warum Projekt-Chats NICHT auch ganze Dateien lesen):** Token-Kosten. Chunk-Retrieval ist der Umweg, der genau diese Kosten spart. Ganze-Datei-Lesen skaliert nicht auf hunderte Projektdokumente. Für KLEINE kuratierte Korpora wäre Brainys Pfad (ganze Datei statt chunk-retrieven) aber der direktere Qualitätshebel — bisher nicht umgesetzt.

Verbundene Messung warum Veredelung das Chunk-Problem NICHT löst: [[project_doc_refinement_eval]].
