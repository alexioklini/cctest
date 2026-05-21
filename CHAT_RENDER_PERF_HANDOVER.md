# Handover — Chat Render Performance (Ebene 3: inkrementelles DOM-Rendering)

**Erstellt:** 2026-05-21 · Version bei Übergabe: `9.10.0` · Branch: `main`

## Warum dieses Dokument

Beim Lesen großer Dokumente (z.B. xlsx → 50KB Tool-Result) **blockiert der
Browser für mehrere Sekunden**, nachdem die Antwort fertig ist. Ursache ist ein
Render-Antipattern in `web/js/chat.js`. Wir wollen es **strukturell** (Ebene 3)
beheben — inkrementelles/append-only DOM-Rendering statt Full-Rebuild — und das
in einer **frischen Session** angehen, weil es den heiklen Resumable-Streaming-
Pfad berührt und sorgfältige Analyse + Tests braucht.

## Die Diagnose (belegt)

`renderMessages()` in `web/js/chat.js` baut das **komplette** Chat-DOM neu
(alle Messages, alle Tool-Blöcke, inkl. Syntax-Highlighting) und wird bei
**jedem SSE-Event** aufgerufen — insbesondere bei **jedem `text_delta`** während
des Streamings (siehe die vielen `renderMessages()`-Aufrufe in den SSE-Callbacks,
chat.js ~Zeile 300–700). Kosten ≈ *(Anzahl Messages × Größe je Message × Events
pro Turn)*. Bei einem 50KB-Tool-Result + hunderten Deltas → mehrsekündige
Haupt-Thread-Blockade.

Konkret gemessen/gesehen:
- Tool-Result von Session `b181898e` (xlsx via markitdown): **50.000 Zeichen**
  (am Render-Cap).
- `buildToolResultBlock` (chat.js:3091) ist schon defensiv: kappt Rendering bei
  `TOOL_RESULT_MAX_RENDER = 200000`, initial `TOOL_RESULT_INITIAL_CHARS = 8000`,
  highlightet aber bei **jedem** Re-Render neu → das ist die teure Operation.
- `renderToolCall` (chat.js:3176) läuft bei jedem Full-Rebuild für jeden
  Tool-Block.

**Wichtig — was schon gefixt ist (NICHT nochmal anfassen):** Das
„markitdown/fallback/OCR"-Badge, das in dieser Session gebaut wurde
(`renderToolCall`), parste anfangs den vollen 50KB-String per `JSON.parse` pro
Render — das verschärfte die Blockade. Bereits behoben in commit `aa69705`:
ersetzt durch eine billige Regex (`/"backend"\s*:\s*"([^"]+)"/`). Das war NUR
die selbstverursachte Regression; die **Wurzel (Full-Rebuild bei jedem Delta)
besteht weiter** und ist das Thema dieses Handovers.

## Lösungsebenen (zur Erinnerung — User hat Ebene 3 gewählt)

- **Ebene 1 — Throttling**: `renderMessages` per `requestAnimationFrame`
  bündeln. Behebt Frequenz, nicht Kosten/Render. Pflaster.
- **Ebene 2 — Memoization**: Tool-Result-HTML pro `tool_use_id` cachen +
  `renderMessages` während `text_delta` weglassen (nur am Turn-Ende). Bester
  Aufwand/Nutzen, berührt aber Streaming-Pfad.
- **Ebene 3 (GEWÄHLT) — inkrementelles/append-only DOM**: Render-Modell von
  „blow away & rebuild" auf inkrementell umstellen. Neue Messages anhängen,
  bestehende DOM-Knoten in Ruhe lassen, nur geänderte/aktive Turn-Knoten
  anfassen. Strukturell endgültig; größte Umschreibung, höchstes
  Regressionsrisiko gegen Resumable-Streaming.

## Pflicht-Analyse VOR dem ersten Edit (war der nächste Schritt)

Den gesamten Render-Pfad lesen und verstehen, mit file:line-Belegen:

1. **`renderMessages()`** (chat.js) — setzt es `container.innerHTML = …`
   (Full-Blow-away) oder appended es? Ziel-Container? Turn-Gruppierung?
2. **`renderStreamingMessage(chat)`** — wie überlebt die `.msg-streaming`-Div
   den `renderMessages`-Aufruf? Kommentare sagen: renderMessages „wipes the
   .msg-streaming div", sie wird „re-appended". **Das ist die kritische
   Kopplung** — exakt verstehen, sonst bricht Streaming.
3. **Alle SSE-Callbacks, die `renderMessages()` rufen** (chat.js ~300–700):
   text_delta, tool_call, tool_result, thinking, done, … — jeweils auch
   `renderStreamingMessage`? Welche sind durch `state.showToolCalls` gated?
4. **Turn-Gruppierung** (`renderTurnBody` / Turn-Bucketing, chat.js ~2230–2300):
   wird jeder Turn EIN DOM-Block? Dann kann man pro-Turn rendern und nur den
   aktiven Turn anfassen — der natürliche Hebel für inkrementelles Rendering.
5. **Stabile Identität pro Message**: `_seq` (chat.js `_nextSeq`),
   `tool_use_id`, DB-`id` — was taugt als stabiler DOM-Key für Reconciliation?
6. **Resumable-Streaming-Constraints** (siehe root CLAUDE.md § „Resumable
   Streaming" + „Client"): Replay via LiveStream; Client „drops trailing
   thinking DB rows" beim Reconnect und pre-seedet `streamingText` NICHT.
   `attachStream` / `buildStreamCallbacks` / `openSession` (web/js/) — welche
   DOM-Annahmen muss ein inkrementeller Renderer bewahren?
7. **Seiteneffekte beim Rendern** (kein reines DOM): `renderToolCall` behandelt
   synthetische GDPR-Zeilen (`renderSyntheticGdprCall`) und das References-Panel
   wird bei `tool_result` aktualisiert (chat.js ~453–480, `openRightPanel`,
   `updateRightPanelBadges`). Ein inkrementeller Renderer darf diese nicht
   verlieren/duplizieren.

> Empfehlung: diese Analyse via Explore-Subagent (read-only) starten — der
> Aufruf war in dieser Session schon formuliert, wurde aber bewusst abgebrochen,
> um stattdessen zu übergeben.

## Bekannte relevante Fixpunkte / Invarianten

- **History-Rekonstruktion existiert bereits**: `web/js/sessions.js:174–184`
  baut beim Session-Laden aus `metadata.tools[]` `tool_call`+`tool_result`-
  Pseudo-Zeilen (`result` wird **voll** durchgereicht). Tool-Runden sind NICHT
  als eigene DB-Zeilen persistiert — nur User-Msg + finale Assistant-Msg + deren
  `metadata.tools[]` (verifiziert an Session `b181898e`: nur 2 DB-Zeilen).
  Ein inkrementeller Renderer muss BEIDE Quellen bedienen: Live-SSE-Zeilen UND
  diese rekonstruierten History-Zeilen.
- **`.msg-streaming`-Div-Kopplung** ist laut Code-Kommentaren der fragilste
  Punkt — mehrere Callbacks rufen `renderMessages()` dann `renderStreamingMessage(chat)`
  in genau dieser Reihenfolge, weil der Rebuild die Streaming-Div wegwirft.
- **`state.showToolCalls`** (default an, `web/js/state.js:28`) gated viele
  Re-Renders. Tool-Blöcke werden „paired inside tool_call" gerendert
  (chat.js:2259 `if (m.role === 'tool_result') continue`).
- Render-Caps: `TOOL_RESULT_INITIAL_CHARS=8000`, `TOOL_RESULT_MAX_RENDER=200000`
  (chat.js:3013–3014). `highlightToolResult` ist die teure Funktion.

## Teststrategie (vor „fertig")

- Browser-Login nötig (User macht das selbst; Reload loggt aus → Sign-in).
  „Tool calls"-Icon muss aktiv sein, damit Tool-Blöcke gerendert werden.
- Repro: neuer Chat, xlsx mit vielen Spalten anhängen, lesen lassen — die
  mehrsekündige Blockade nach der Antwort beobachten (vorher/nachher).
- **Resumable-Streaming-Regression prüfen**: Turn starten, Tab schließen/neu
  öffnen während des Streamings (`GET /v1/chat/stream`-Reattach), prüfen dass
  kein Doppel-Render / verlorene thinking-Zeilen / kaputte Streaming-Div.
- Mehrere Tabs gleichzeitig am selben Chat (concurrent attach).
- Lange History laden (viele Turns) + neuen Turn senden → kein Full-Rebuild-Jank.
- Server-Neustart: `launchctl kickstart -k gui/$(id -u)/com.brain-agent.server`,
  >6s warten, `~/.brain-agent/server.error.log` auf Tracebacks prüfen.
  (Aber: reine JS/CSS-Änderung braucht nur Browser-Reload — `no-store` auf
  Static-Assets seit 9.9.13, kein Server-Neustart nötig für web/js-Edits.)

## Stand bei Übergabe

- Version `9.10.0`. Working tree clean.
- Zwei Commits dieser Session betreffen das Badge-Feature:
  - `0ac433d` feat(chat): backend (markitdown/fallback/OCR) im Tool-Block
  - `aa69705` fix(chat): billige Regex statt 50KB-JSON.parse fürs Badge
  - **Push-Status prüfen** (`git log origin/main..HEAD`) — ggf. noch pushen.
- Das Badge-Feature selbst ist fertig + funktioniert (live + History via
  sessions.js-Rekonstruktion). KEIN offener Punkt dort außer der allgemeinen
  Render-Perf.

## Empfohlener Einstieg in der frischen Session

1. Root `CLAUDE.md` § „Resumable Streaming" + `web/js`-Kommentare lesen.
2. Read-only Render-Pfad-Analyse (Explore-Subagent, Punkte 1–7 oben).
3. Design vorlegen (append-only pro Turn, aktiver Turn = einziger neu
   gerenderter Block; stabile Keys via `_seq`/`tool_use_id`), DANN implementieren.
4. Inkrementell + verifizieren; Resumable-Streaming ist der Haupt-Regressionsfokus.
