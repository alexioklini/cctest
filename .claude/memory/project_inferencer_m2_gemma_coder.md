---
name: project_inferencer_m2_gemma_coder
description: Inferencer.app-Anbindung (gemma-4-12b-coder) am 28.07.26 wieder ENTFERNT aus Router + Brain; App selbst läuft noch auf :8081. Router-Admin-Token im Klartext in config.db
metadata: 
  node_type: memory
  type: project
  originSessionId: 57976744-d43d-4116-b5d6-729a4622dbaa
  modified: 2026-07-28T19:12:02.231Z
---

**ENTFERNT 28.07.2026** (User-Anweisung "remove the local inferencer"): Die am 27.07. eingerichtete Anbindung der **Inferencer.app** (MLX-GUI-App, `/Applications/Inferencer.app`, OpenAI-kompatibel auf Port 8081, servierte `mlx-community/gemma-4-12b-coder-fable5-composer2.5-4bit`) ist komplett zurückgebaut:

- **llm-router**: Provider `Lokal-M2` + Modell `gemma-4-12b-coder` per Admin-API gelöscht (`DELETE /admin/models/<id>` dann `/admin/providers/<pid>`).
- **brain-agent**: Modell `gemma-4-12b-coder` per `POST /v1/models/config {action:'delete'}` entfernt → Tombstone `gemma-4-12b-coder` gesetzt; der ältere Scoped-Tombstone `llm-router/gemma-4-12b-coder` blieb ebenfalls stehen. Provider `llm-router-local` selbst blieb unangetastet (M4-gemma + whisper laufen weiter darüber).
- Die einzige gepinnte Session (Smoke-Test "hi" vom 27.07.) wurde gelöscht ([[feedback_cleanup_test_sessions]]).
- **Die Inferencer.app läuft weiter** auf 127.0.0.1:8081 (nur die Anbindung wurde entfernt, nicht die App) — bei Bedarf manuell beenden oder neu anbinden.

Nützlich fürs nächste Mal (gilt weiterhin): llm-router-Admin-Session-Tokens liegen im Klartext in `~/.llm-router/config.db → admin_sessions` (gültige wiederverwendbar); Admin-API-Prefix ist `/admin/*` (NICHT `/admin/api/*`); `rewrite_response_model` im Router ist ein totes/reserviertes Feld. Bei Wieder-Anbindung: beide Tombstones entfernen bzw. Modell manuell anlegen (Tombstone-Revive passiert bei `action:'update'` automatisch). Siehe [[project_llm_router_service]].
