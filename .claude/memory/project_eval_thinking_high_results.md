---
name: eval thinking=high results
description: Mistral Medium 3.5 + thinking=high eval results 2026-05-03, judge Sonnet 4.6, rubric fixed for .pdf.md
type: project
originSessionId: 70381c9e-811e-4c21-9e96-0d82f9481762
---
Run: `20260503T075614_disc-none_medium-3.5-thinking-high` + nachgejudgte Korrekturen.

**Brain mean: ~0.73** (13 Fragen vollständig gejudgt, Sonnet 4.6 judge)

## Korrekturen die in diesem Run gemacht wurden

1. **`engine/models.py` Fix**: `_detect_thinking_format` hatte `mistral-medium-3*` nicht — Commit 98ae039 hat nur `brain.py` gepatcht, nicht `engine/models.py`. Nach Fix + Restart upgradet `init_models_config` automatisch auf `mistral_blocks`.

2. **Rubric Fix**: `.pdf.md` Suffix nicht mehr als Fehler werten — Brain liest `.brain-extracted/*.md` Companions, UI zeigt Original-PDF. Rubric jetzt: "Accept `<name>.pdf.md` as equivalent to `<name>.pdf`".

3. **Judge max-turns**: von 1 auf 3 erhöht — Sonnet braucht manchmal 2 Turns für langen Judge-Prompt.

## Ergebnisse nach Bucket

| Bucket | Brain mean | Lücke zu Gold |
|--------|-----------|---------------|
| retrieval (R1-R3) | ~0.74 | R3 Brain gewinnt (Gold war leer); R1/R2 Citation-Lücke |
| precision (P1-P3) | ~0.90 | Sehr gut; P3 Löschfristen noch -0.125 |
| multi_doc (M1-M3) | ~0.88 | M3 tie; M1/M2 Composition-Lücke |
| **refusal (F1-F3)** | **~0.29** | **Hauptproblem — F1/F2 halluzinieren, F3 korrekt aber dünn** |
| citation (C1-C3) | ~0.86 | C1 tie; C2/C3 leichte Lücke |

## Ohne F-Bucket: brain mean ~0.84 — sehr kompetitiv gegen Gold

## Vergleichbarkeit
- Sonnet-Judge ≠ Mistral-Judge — nicht direkt mit früheren Runs vergleichbar
- citation-reround-phase2 (Mistral-Judge): brain mean 0.743

## Nächste Baustellen
1. Refusal-Discipline: F1/F2 halluzinieren aus Trainingsdaten — server-side post-retrieval gate oder stärkere Prompt-Discipline
2. Refusal-Composition: F3 korrekt aber ein Satz statt strukturierter Erklärung (welche Suchen, warum leer)
