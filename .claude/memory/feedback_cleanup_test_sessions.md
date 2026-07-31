---
name: feedback_cleanup_test_sessions
description: Synthetische Test-Chats aus autonomen Verifikationsläufen sofort löschen/archivieren — sie landen sonst in der Nutzer-Sidebar und werden als Bug gemeldet
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 37b39c9e-3a43-49aa-8d4d-5a79598fa891
---

Autonome Verifikationsläufe (per [[reference_brain_admin_login]]) erzeugen echte Sessions in der Live-DB ("Sag Hallo.", "Rufe JETZT git_worktree…", Ein-Wort-Proben). Am 13.07.2026 meldete der User sie als Bug ("Terminal-Chats erscheinen als projektlose main-Chats") — es waren ~19 ungelöschte Test-Sessions von mir (07.+11.07., v9.308-311-Tests); die echte code_chat-Ausschlusslogik war korrekt (end-to-end verifiziert, 9.316.0).

**Why:** Test-Sessions sind vom User nicht von echten Chats unterscheidbar; Coding-Prompts wie "Nutze JETZT ast_grep…" lesen sich wie Terminal-Chats und erzeugen Phantom-Bug-Reports.

**How to apply:** Jede per API erzeugte Test-Session am Ende des Laufs mit `DELETE /v1/sessions/<id>` löschen (im selben Skript/Turn, nicht "später"). Bei Bug-Reports über mysteriöse Chat-Einträge zuerst prüfen, ob es eigene Test-Reste sind (Titel-Muster: uid-Suffixe, "JETZT", Ein-Wort-Proben).
