---
name: project_cost_rates_and_provider_plan_default
description: v9.313.0 — Preistabelle GUI-editierbar (config.json → cost_rates) + Provider-Vorgabe für Coding-Pläne; dabei echter Abrechnungsbug (Präfix-Match) gefixt
metadata: 
  node_type: memory
  type: project
  originSessionId: c9eb024c-901b-4625-93fd-bddec25162df
---

v9.313.0 (2026-07-12): die zwei Lücken geschlossen, die das 9.283.0-Plan-Dashboard offen ließ.

**1. Preistabelle GUI-editierbar.** Neue Stufe `config.json → cost_rates` schiebt sich in `engine/quotas._get_cost_rate` ZWISCHEN das Modell-Feld und die hartkodierte `_cost_rates`-Tabelle. Auflösung: `models.<id>.cost_input` → `cost_rates` → `_cost_rates` (Code-Seed, bleibt) → `{0,0,0}`. `get_config_cost_rates()` mtime-gecacht auf config.json (Muster von `get_coding_plans`) → wirkt ohne Restart. Endpunkte `GET/POST /v1/costs/rates` (admin), UI im Kosten-Tab. Schlüssel = exakte ID ODER Präfix.

**Das stille $0 war der eigentliche Schaden**: 122/140 Modelle hatten nirgends einen Preis → buchten $0 → fehlten in Statistik UND Kontingent, ohne Hinweis. `engine.unpriced_models()` listet sie jetzt (76 Cloud-Modelle; lokale + unit-billed OCR/TTS/STT ausgenommen, dort ist $0/Seitenpreis korrekt). Darunter `gemma-4-*-cloud`, die am Kilo-GUTHABEN hängen und real Geld kosten.

**ECHTER BUG mitgefixt (Bestandscode):** die Präfixsuche nahm den ERSTEN Dict-Treffer. Da `gpt-4.1` vor `gpt-4.1-mini` im Dict steht, wurde ein gpt-4.1-mini mit dem 5× teureren gpt-4.1-Tarif abgerechnet ($2/$8 statt $0.40/$1.60). `_match_rate_table` nimmt jetzt den LÄNGSTEN Treffer. Lehre: bei Präfix-Tabellen ist Dict-Reihenfolge nie die Match-Priorität.

**2. Provider-Vorgabe für Abrechnungskonten.** `providers.<name>.coding_plan` als Fallback — die Plan-Zugehörigkeit ist eine Eigenschaft des KONTOS, nicht des Modells (Mistral hatte 63 identische Modell-Links, jetzt eine Provider-Zeile). `brain.resolve_model_plan_id()` ist DER eine Seam: sowohl die Abrechnung (`model_is_flat_plan`) als auch die Dashboard-Zuordnung (`handlers/admin_costs._plan_models`) gehen durch ihn — sonst könnte ein Modell gegen Plan A buchen und unter Plan B angezeigt werden.

Feld **abwesend** = erbt Provider-Vorgabe; Sentinel `coding_plan: "none"` = explizit KEIN Plan (Abwesenheit kann die Ausnahme nicht ausdrücken). Plan-Löschen löst jetzt auch Provider-Verweise (hängender Default würde still weiterbuchen).

**Gotchas dieser Session:**
- Es gibt **kein `server_config` in brain.py** — selbst `resolve_provider_for_model` liest die Provider mit einem mtime-Cache direkt von der Platte. Neuer `brain.get_provider_configs()` folgt dem Muster. Nicht auf ein In-Memory-Global setzen.
- `POST /v1/models/config {action:"update"}` **ersetzt** die Modell-Config vollständig (kein Merge) — erst lesen, ergänzen, ganz zurückschreiben.
- Playwright-Race: `openGeneralSettings()` lädt den Server-Tab asynchron nach und überschreibt einen zu früh gesetzten Tab-Wechsel. Nach dem Öffnen ~2,5 s warten, dann Tab klicken.

Siehe [[project_openrouter_credit_plan]] (das Konto-Objekt) und [[project_coding_plan_quota_calibration]] (Kalibrier-Methodik).
