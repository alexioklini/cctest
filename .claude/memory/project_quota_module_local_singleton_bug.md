---
name: project_quota_module_local_singleton_bug
description: User-Kontingent stand immer auf 0 — get_user_state las quotas._cost_tracker (nie gesetzt) statt brain._cost_tracker; Modul-Extraction-Falle
metadata: 
  node_type: memory
  type: project
  originSessionId: d029b009-0fdb-4d15-8bbf-65b608fe93cd
  modified: 2026-07-18T14:26:49.485Z
---

Commit 18eb61cb (2026-07-18). Das persönliche Tages-/Monats-Kostenkontingent (QuotaManager.get_user_state, Balken im Plan-Popover) zeigte für JEDEN User dauerhaft used_usd=0 — unabhängig von Modell/User/Zeitfenster, seit dem Modul-Extraction-Refactor (2026-05-23).

**Ursache (Modul-Identitäts-Bug):** server.py:4056 setzt `engine._cost_tracker = engine.CostTracker()` — das landet auf dem brain/engine-Namespace (Re-Export-Schicht). Aber `get_user_state` in engine/quotas.py las das MODUL-LOKALE `_cost_tracker` (quotas.py: `_cost_tracker = None`), das nie gesetzt wird. Guard `if _cost_tracker and user_id:` schlug immer fehl → daily_used/cycle_used hart 0.0, `sum_user_window` NIE gerufen.

**Warum andere Kosten-Anzeigen funktionierten:** /v1/costs/breakdown + Coding-Plan-Nutzung gehen über `engine._cost_tracker` (die gesetzte Instanz) bzw. direkt `_cost_conn`. Nur get_user_state griff auf das falsche Global. DB, user_id, since — alles korrekt; nur das Tracker-Handle war None.

**Fix:** get_user_state löst die Live-Instanz über `brain._cost_tracker` auf (lazy `import brain`, Zyklus-frei per engine/CLAUDE.md), Fallback auf lokales Global.

**MUSTER (allgemein, wiederkehrende Refactor-Falle):** wenn ein aus brain nach engine/ extrahiertes Modul ein modul-lokales Singleton-Global deklariert (`_x = None`), server.py aber `engine._x = ...` (= brain-Namespace) setzt, ist das Modul-Global NIE gesetzt → still None/0, keine Exception. Symptom: eine einzelne Feature-Anzeige liefert 0/leer, während verwandte Anzeigen funktionieren. Diagnose-Weg der funktioniert hat: temporäre stderr-Debug-Zeile an der if-Bedingung → server.error.log zeigte `_cost_tracker=False`. Prüfen: `grep bare-global-Zugriffe` (ohne engine./brain./self.-Präfix) im extrahierten Modul.

Debug-Weg: /v1/quotas/me + /v1/quotas/admin/breakdown zeigen get_user_state live. Admin-Login (admin/admin) IST hier user_id=17368b7961d3 (role user). Verwandt: [[feedback_never_probe_server_config_via_import]] (Prozess proben statt Datei), [[project_inprocess_openai_loop]] (engine re-export Muster).
