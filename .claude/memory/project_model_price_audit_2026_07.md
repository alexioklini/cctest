---
name: project_model_price_audit_2026_07
description: "2026-07-13 Preis-Audit — 27 Token-Preise eingetragen; ZWEI falsche Bestandspreise gefunden (kimi -18%, mistral-OCR Faktor 4 zu niedrig)"
metadata: 
  node_type: memory
  type: project
  originSessionId: c9eb024c-901b-4625-93fd-bddec25162df
---

2026-07-13, nach v9.313.0 (Preistabelle, siehe [[project_cost_rates_and_provider_plan_default]]): die 76 ungepreisten Cloud-Modelle abgearbeitet → **27 offen** (11 davon `:free` = korrekt gratis, 16 nicht belegt).

**Der eigentliche Fund waren nicht die fehlenden, sondern zwei FALSCHE Bestandspreise:**

1. **`mistral-ocr-latest`: $0.001 → $0.004/Seite (Faktor 4).** Mistral hat den OCR-Preis über die Generationen erhöht: OCR-1 $1/1000 Seiten → OCR-3 $2 → **OCR-4 $4 (seit 23.06.2026)**. Der Alias `-latest` zeigt seitdem auf OCR-4, unser Preis stammte noch von OCR-1. 1109 verarbeitete Seiten waren mit $1,11 verbucht statt real $4,44. Batch-Tarif ist −50%. `mistral-ocr-3*` = $0.002 hinterlegt.
   → **LEHRE: `*-latest`-Aliase wandern auf neue Generationen MIT NEUEM PREIS.** Ein einmal gesetzter Preis auf einem `-latest`-Alias veraltet still.
2. **`kimi-k2.6`: $0.80/$3.40 → $0.95/$4.00** (war 18% zu billig; offiziell platform.kimi.ai/docs/pricing/chat-k26). Bucht $0 real (Flatrate), steuert aber die "ohne Abo"-Ersparnisanzeige.

**Eingetragen als PRÄFIXE** in `config.json → cost_rates` (greifen so auch für künftige Varianten): komplette GLM-Familie (5.2/5.1 1.40/4.40 · 5 1.00/3.20 · 5-turbo+5v-turbo 1.20/4.00 · 4.7/4.6/4.5 0.60/2.20 · 4.5-air 0.20/1.10), gemma-4-26b 0.06/0.33, gemma-4-31b 0.12/0.35, voxtral-small 0.10/0.40, devstral 0.40/2.00, ministral-14b/8b/3b, grok-4.5 2/6, hy3, aion-3.0, qwen3-coder, nex-n2-mini, fugu-ultra. glm-5.2 gegen die Quelle VERIFIZIERT (stimmte).

**Token vs. Unit — die Trennung ist eine Invariante** (User-Hinweis): STT rechnet pro MINUTE (`cost_per_minute_usd`), TTS pro ZEICHEN (`cost_per_1k_chars_usd`), OCR pro SEITE (`cost_per_page_usd`). Diese Felder stehen AM MODELL und werden von `_unit_rate`/`_unit_list_cost` gelesen — NICHT von der Token-Tabelle. `unpriced_models()` blendet Modelle mit einem Unit-Feld korrekt aus. Nie einen Unit-Dienst in `cost_rates` eintragen.
Sonderfall `voxtral-small`: hat BEIDE Modi — Token ($0.10/$0.40) im Chat, $0.004/Min bei reiner Transkription. Unser Audio-Pfad nutzt nur `voxtral-mini-*` (Minutenpreis), daher Token-Preis für `small` korrekt.

**NICHT eingetragen (nicht belegt — nicht raten):** `kimi-for-coding`/`-highspeed` — diese Namen existieren in der offiziellen Moonshot-Doku NICHT; dort heißen sie `kimi-k2.7-code` ($0.95/$4.00) und `-highspeed` ($1.90/$8.00). Vermutlich Aliase, aber unbelegt → am Endpunkt verifizieren, bevor man sie zuordnet. Ebenso ungeklärt: `mistral-tiny`, `mistral-code-*`, `mistral-vibe-cli-*`, `labs-leanstral`, `dolphin-*`, `~x-ai/grok-latest` (nicht mehr gelistet/abgekündigt).
