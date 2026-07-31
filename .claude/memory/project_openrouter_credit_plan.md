---
name: project_openrouter_credit_plan
description: "OpenRouter als Credit-Konto im Coding-Plan-Dashboard (2026-07-12) — Plan-System konnte das schon, nur Objekt+Verknüpfung fehlten"
metadata: 
  node_type: memory
  type: project
  originSessionId: c9eb024c-901b-4625-93fd-bddec25162df
---

2026-07-12: OpenRouter als 5. Konto im Plan-Popover angelegt — **ohne Codeänderung**. Das Coding-Plan-System (v9.283.0) deckte den Fall bereits ab; es fehlten nur das Plan-Objekt und die Modell-Verknüpfung.

**Angelegt:** `coding_plans` → `openrouter-credit`, `type: credit`, `balance_usd 18.98`, `anchor 2026-07-12`. Verlinkt sind **nur die 6 GPT-5.6-Modelle** (luna/terra/sol × standard/pro) — die einzigen mit hinterlegtem Listenpreis. Der Provider hat 30 Modelle, aber 24 davon sind `:free`/inaktiv (Grok, Qwen, Llama, Nemotron…) und gehören NICHT ans Guthaben.

**Drei Fallen, die ich getroffen habe:**
1. **Ein Plan erscheint erst im Popover, wenn ≥1 Modell darauf verlinkt ist** (`_plan_models` in `handlers/admin_costs.py` liest die Verknüpfung VOM MODELL, nicht vom Plan). Plan anlegen allein reicht nicht.
2. **`anchor` = Zeitpunkt, ab dem gegen `balance_usd` gezählt wird.** Wenn der User den AKTUELLEN Kontostand nennt (nicht den Stand direkt nach der Aufladung), muss der Anker auf HEUTE, sonst werden schon-verbrauchte Calls doppelt abgezogen. Nachträglich korrigierbar via `POST /v1/plans/calibrate {plan_id, balance_usd, anchor}`.
3. **Nicht stumpf über `provider == X` verlinken.** Ich hatte erst alle 30 openrouter-Modelle angehängt; der User hat es gemerkt ("openrouter hat 30 gpt 5.6 modelle?"). Richtiges Kriterium für ein Credit-Konto: hat das Modell einen Listenpreis (`cost_input`/`cost_output`)? Free-Modelle verbrauchen kein Guthaben.

Bei `type: credit` NIE `flat_plan` setzen — Credit-Konten müssen ECHTE Kosten buchen, sonst bucht `_compute_cost` $0 und der Guthabenzähler steht still.

**Offene Lücken im Kosten-GUI** (nicht angefasst, User hat nur OpenRouter beauftragt):
- `engine/quotas.py:76 _cost_rates` ist eine hardcodierte Preistabelle (~70 Modelle); 122 von 140 Modellen haben keine `cost_input`/`cost_output` in config.json und hängen daran oder fallen auf stillen Nulltarif. Andockpunkt für GUI-Fähigkeit: neue Prioritätsstufe `config.json → cost_rates` in `_get_cost_rate` + Route in `handlers/admin_costs.py`.
- Kein Provider-Default für die Plan-Verknüpfung (`providers.<name>.coding_plan`) — die Zuordnung hängt einzeln an jedem Modell (Mistral: ~63 Einträge auf `mistral-vibe`).

Siehe [[project_coding_plan_quota_calibration]] (Kalibrier-Methodik) und [[project_openrouter_gpt56_provider]] (die Modelle selbst).
