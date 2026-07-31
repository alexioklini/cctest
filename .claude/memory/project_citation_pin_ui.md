---
name: Citation pin UI — kompakte Inline-Symbole mit Click-Popover
description: 2026-05-01 — Citation-Badges in web/index.html ersetzt durch kleine Pin-Buttons (16×16 Open-Book-Icon) die nicht den Textfluss stören; Click öffnet Popover mit File/Locator/Quote; Bracket-Detection auf raw text vor marked.parse gemoved damit *"..."* (Markdown-Italic-Quotes) nicht mehr durchfallen
type: project
originSessionId: b72f1f43-2339-41d1-a5ee-4e57d6582617
---
## Was geändert wurde

`web/index.html`:

**1. Bracket-Detection ZUVOR auf raw text** (statt nach marked.parse):
- Neue Funktion `extractCitationsFromRaw(text)` läuft VOR `marked.parse`
- Findet alle `[Quelle: ...]` brackets, parst File/Locator/Quote raus, ersetzt durch unsichtbares Sentinel-Token (`U+2063CIT⁣<id>⁣/CIT` mit U+2063 Invisible Separator)
- Marked.parse() lässt die Sentinels in Ruhe (Markdown ignoriert U+2063)
- Nach marked: `restoreCitationPins(html, citations)` ersetzt Sentinels durch Pin-Buttons
- **Fix für den `*"..."*` Bug**: Brain emittiert Quotes oft mit Markdown-Italic-Markern, marked rendert das vorher zu `<em>"..."</em>` und brach den outer `[Quelle:...]` Regex. Jetzt egal weil wir auf raw text matchen.

**2. Visual: Pin statt Pille**:
- `.citation-pin` ist jetzt 16×16px Button mit kleinem Open-Book-Icon (Lucide-Style SVG)
- Brand-color Background, hover effect
- Stört den Textfluss nicht mehr (vorher war's eine breite Pille mit File+Locator+Quote alles inline)
- Pin trägt das Citation-Data als URL-encoded JSON in `data-citation` attribute

**3. Click-Popover**:
- Click oder Focus öffnet `.citation-popover`: file (mit File-Icon), Locator (falls vorhanden), und Quote in styled Quote-Block (italic, brand-tinted background, left border-accent)
- Auto-Position: über/unter dem Pin je nach Platz, mit Pfeil zum Pin
- Schließt bei Outside-Click oder ESC
- `_activeCitationPopover` global, max 1 gleichzeitig

**4. Inline-collapse**: Brain stellt Brackets oft auf eine eigene Zeile (`Text:\n[Quelle:...]\nNächster Text`). Mit `breaks: true` würde marked das als Soft-Break rendern und der Pin landet in der Folgezeile. **Fix**: regex `replace(/([^\n])[ \t]*\n[ \t]*(\[(?:Quelle|...):...\])/g, '$1 $2')` zieht den Bracket auf das Ende der vorigen Zeile.

**5. Locator-Erweiterung**: zusätzlich zu `Page N`/`Slide N`/`Sheet "X"`/`§N` jetzt auch `Zeile[n] N-M` erkannt (Brain emittiert das manchmal).

## Verified in Chrome

User-Browser zeigt 35 Pins korrekt inline gerendert auf einer real geladenen Session (8b7904395e20), 0 raw `[Quelle:` Text-Knoten in den Chat-Messages, Click-Popover öffnet sauber.

## Kompatibilität

- Alte Funktionen (`renderCitationBadgesInHtml`, `replaceCitationMarkersInText`, `parseCitationBody`, `renderCitationBadge`) bleiben im Code aber werden NICHT MEHR AUFGERUFEN. Können später entfernt werden.
- Alte CSS-Klassen `.citation-badge*` wurden DURCH `.citation-pin` und `.citation-popover` ERSETZT — alte Klassen sind weg.

## Files modified

- `web/index.html`:
  - CSS-Block `.citation-pin` + `.citation-popover` + `.citation-popover-arrow` (~85 Zeilen, ersetzte alte `.citation-badge*` Block)
  - JS-Block `extractCitationsFromRaw` / `parseCitationBodyRaw` / `restoreCitationPins` / `renderCitationPin` / `openCitationPopover` / `closeCitationPopover` / `_citationPopoverOutsideHandler` / `_citationPopoverEscHandler` (~120 Zeilen, neu)
  - `renderMarkdown()` umgebaut: extract VOR marked.parse → marked.parse → restore zu Pins
