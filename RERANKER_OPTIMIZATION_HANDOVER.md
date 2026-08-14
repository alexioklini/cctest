# Handover: Reranker-Optimierung (frische Session)

Stand 14.08.2026 abends, Brain v9.432.0. Kontext: Der MemPalace-Reranker
(Infinity + BAAI/bge-reranker-v2-m3, M4 :8002) hat den AFM-Ersetzungs-A/B
klar gewonnen (bleibt!), dabei sind aber DREI Betriebs-/Nutzenprobleme
messbar geworden. Diese Session soll den Reranker-Betrieb optimieren —
NICHT das Retrieval-Design ändern (dafür gilt weiter
[[backlog_retrieval_eval_harness]] als Vorbedingung).

## Messdaten (14.08., eval/rerank_ab_bge_vs_afm.py — reproduzierbar)

50 echte Drawer aus 10 Wings, Gold = Quell-Drawer der lokal generierten
Query, Produktions-Suchpfad exakt (Präfix `task: sentence similarity |
query: `, Matryoshka-384d, top_k_in=40, max 1500 Zeichen/Passage).
Vektor-Recall@40: 44/50.

| Variante | Hit@1 | Hit@5 | MRR@10 | Ø s/Query |
|---|---|---|---|---|
| ohne Rerank | 0.568 | 0.864 | 0.690 | ~0 |
| bge (Prod.) | 0.568 | 0.886 | 0.707 | **4.68** |
| AFM-Pointwise | 0.273 | 0.659 | 0.427 | 15.3 |

AFM-Rerank ist ENDGÜLTIG verworfen (schlechter als kein Rerank). Embed
Ø0.05s, Vektorsuche Ø0.019s — der Rerank ist der mit Abstand teuerste
Suchschritt.

## Die drei Probleme

1. **Kaltstart→Latch**: Erste echte Rerank-Anfrage nach Idle/Restart braucht
   ~16s (live gemessen; /health antwortet in 20ms — es ist die
   Modell-Kaltladung), läuft in Brains Timeout, und nach
   `_REMOTE_RERANK_MAX_FAILS=2` Fehlern schaltet Brain den Rerank für den
   GESAMTEN Prozesslauf ab (Latch; historisch oft passiert — Log:
   "LATCHING remote rerank OFF").
2. **Warm zu teuer**: ~4.7s/Query bei Produktionsgröße (40 Passagen × 1500
   Zeichen). Kleine Batches sind schnell (8 kurze Docs: 0.31s) — die Kosten
   skalieren mit Passagen-Anzahl×Länge.
3. **Nutzen auf leichten Queries unter Noise**: +0.017 MRR vs. ohne Rerank
   (Schwelle 0.05, [[feedback_eval_single_run_noise]]). ABER: die
   Harness-Queries sind aus den Golddokumenten generiert (leicht für die
   Vektorsuche). VOR jeder Abschalt-Diskussion: Harness um
   PARAPHRASE-Queries erweitern (Query-Gen-Prompt: "formuliere mit ANDEREN
   Worten als der Text") und neu messen.

## Code-/Betriebsanker

- **Brain-Seite**: `engine/mempalace_glue.py` ~Z.1255–1300 —
  `RemoteReranker.predict`: httpx POST `{base_url}/rerank`, Timeout
  `httpx.Timeout(self.timeout_s, connect=2.0)` (timeout_s aus Config —
  Herkunft/Default beim Einstieg prüfen), Latch-Zähler unter
  `_reranker_lock` (prozessweit, `_remote_rerank_latched_off`). Kein
  Retry, kein Un-Latch.
- **Config**: `config.json → mempalace.reranker`: enabled, model
  BAAI/bge-reranker-v2-m3, device remote, url http://192.168.1.214:8002,
  top_k_in 40, max_chars_per_passage 1500, batch_size 16.
- **M4-Dienst**: launchd `com.brain-agent.infinity-reranker`,
  `~/.venv_infinity/bin/infinity_emb v2 --model-id BAAI/bge-reranker-v2-m3
  --port 8002` — **OHNE --device-Flag!** torch.backends.mps.is_available()
  = True im venv, aber ob Infinity wirklich MPS nutzt, ist UNGEPRÜFT
  (Startup-Log lesen / `/models`-backend-Feld; 4.7s für 40×1500 riecht
  nach CPU). Restart: `launchctl kill SIGTERM gui/501/…` (NIE kickstart -k).
- **API-Form**: POST /rerank `{model, query, documents[], return_documents:false}`
  → `results[{index, relevance_score}]`. /health 20ms, /metrics Prometheus.
- **Harness**: `eval/rerank_ab_bge_vs_afm.py` (python3 -u; Varianten
  einfach erweiterbar — `variants`-Dict). Ergebnisse
  `eval/_rerank_ab_results.json` (gitignored, enthält Bank-Inhalte).
- **Monitoring**: SparkDash-Karte "Reranker (Infinity)" (:8013,
  ~/sparkdash.py — Patches nur mit exakten Text-Ankern + assert, siehe
  Memory-Lektion 14.08.) + Aktivitätszeilen "Reranker (Infinity)".

## Optimierungs-Kandidaten (Priorität)

1. **Device-Check zuerst** (größter möglicher Hebel, 15 min): Läuft
   Infinity auf CPU statt MPS? Wenn ja: `--device mps` (bzw. Infinity-
   Engine-Option) und warm neu messen. Erwartung bei echter GPU-Nutzung:
   deutlich unter 1s für 40×1500.
2. **Kaltstart/Latch entschärfen** (das akute Produktionsproblem):
   Kombination aus (a) `timeout_s` beim ERSTEN Call großzügig (oder
   Warmup-Ping: Brain schickt nach Boot/`recover` einen Mini-Rerank an
   :8002, bevor echte Queries kommen — analog Warm-Pool), (b) launchd-
   seitig: Infinity mit Preload starten (macht es beim Boot ohnehin —
   das Problem ist eher TTL/Idle-Entladung? PRÜFEN ob Infinity überhaupt
   entlädt oder ob die 16s NUR nach Dienst-Restart auftreten), (c) Latch
   mit Selbstheilung (z. B. nach 10 min wieder versuchen) statt
   prozess-permanent — kleiner Eingriff in mempalace_glue, Review N2
   (Lock!) beachten.
3. **Kostenschraube top_k_in/max_chars** (NUR mit Harness-Beleg): 40→20
   Kandidaten bzw. 1500→800 Zeichen halbieren die Warm-Kosten grob;
   Qualitätsdelta mit dem Harness messen (≥3 Seeds, Noise-Regel).
   Negativ-Historie beachten: `rerank_score_floor` war ein Fehlschlag —
   NICHT wiederholen.
4. **Paraphrase-Harness** (entscheidet die Nutzenfrage sauber): zweiten
   Query-Gen-Modus einbauen, dann bge vs. ohne auf schweren Queries. Erst
   DANACH ggf. über Abschalten/kleineres Modell reden.
5. **Modell-Alternativen** (nachrangig, nur falls 1-3 nicht reichen):
   kleinere Cross-Encoder (bge-reranker-base, Qwen3-Reranker-0.6B o. ä.)
   via Harness; Achtung Mehrsprachigkeit (Deutsch!) und 8bit-vs-4bit-Lehre
   aus mlx_ocr.py (Halluzination schlägt Speed).

## Fallen & Regeln

- M4-Toolchain: Nach CLT-Updates blockiert die Xcode-Lizenz auch
  git/python3-Shims (`sudo xcodebuild -license accept`, User machen lassen).
- Qdrant läuft LOKAL auf dem Brain-Mac (localhost:6333), Collection
  `mempalace_db0eee7a22b04148_mempalace_drawers` (53k Punkte, 384d).
- Router-Key für lokale Modelle: `config.json → providers.llm-router.api_key`.
- Retrieval-Inhalte sind Bank-Daten: ALLES lokal halten (Query-Gen über
  lokales gemma-12B via Router, keine Cloud).
- Eval-Konventionen: `python3 -u`, ≥3 Reps bzw. Multi-Seed, Delta <0.05 =
  Noise, Commits direkt auf main, VERSION+CHANGELOG in brain.py (beide!).
- Rollbacks: Config-Werte sind einzeln rückstellbar; Infinity-Plist-Änderung
  = Datei editieren + bootout/bootstrap (Env-Änderungen brauchen Re-Bootstrap).

## Verwandte offene Punkte (NICHT diese Session)

- Voller Retrieval-Eval-Harness ([[backlog_retrieval_eval_harness]]) —
  Vorbedingung für kg_extract-A/B und jede Embedding-/Closet-Änderung.
- wiki-A/B mit `apple-fm-contenttagging` (Endpoint steht bereit).
- fm-agent/AFM-Themen: siehe AFM_MIGRATION_HANDOVER.md (Stand-Blöcke) +
  Memory `project_afm_migration_status`.
