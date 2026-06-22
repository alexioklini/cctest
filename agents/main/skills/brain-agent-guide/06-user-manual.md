# Brain-Agent Benutzerhandbuch

Verständliche Anleitung zur Web-Oberfläche. Lies dies, wenn der Nutzer
fragt „wie mache ich…" zu etwas, das er auf dem Bildschirm sieht, oder
eine Schritt-für-Schritt-Anleitung für eine echte Aufgabe will
(„ein docx übersetzen", „zwei Excel-Dateien vergleichen", „einen
wiederkehrenden Bericht einrichten"). Zitiere dies nicht wörtlich —
extrahiere die Antwort auf die konkrete Frage und lass den Rest weg.

Die gesamte Oberfläche ist **auf Deutsch** (seit v9.20). Technische
Begriffe bleiben englisch (Agent, Workflow, Token, Provider, Cache, MCP,
KG/Knowledge Graph, Caveman, Warmup, Tool, Hook, Skill, Soul). Wenn der
Nutzer dich bittet, die Aufgabe **auszuführen** statt sie zu erklären,
wechsle zu `04-recipes.md` und führe sie aus.

---

## Die Oberfläche auf einen Blick

**Linke Seitenleiste** — Hauptnavigation:
- **Neuer Chat** — öffnet die Willkommens-/Eingabeansicht
- **Suche** — unscharfe Suche über alle Chats
- **Chats** — alle Chats, neueste zuerst; archivieren/markieren/umbenennen
  über das Zeilenmenü
- **Projekte** — Wissensbasen mit eigenem Gedächtnis
- **Favoriten** — angeheftete Chats, Artifacts, Prompts, Bilder
- **Artifacts** — alle erzeugten oder hochgeladenen Dateien sitzungsübergreifend
- **Geplante Aufgaben** — wiederkehrende Tasks (cron-artig)
- **Workflows** — mehrstufige Automatisierungen mit Freigabe-Gates
- **Übersetzung** — Text / Dokument / Audio / Live-Mikrofon
- **Daten** — Platzhalter (Funktion in Entwicklung)
- **Wiki** — editierbares Wissens-Wiki (= Langzeit-Gedächtnis; durchsuchbar)
- **Einstellungen (Zahnrad)** — Agent- + allgemeine Einstellungen
  (meist nur für Admins)

**Hauptbereich** — die jeweils gewählte Ansicht. Die Willkommensansicht
zeigt Begrüßung + Eingabefeld + Prompt-Karten.

**Rechtes Panel** (oben rechts ein-/ausblenden) — Tabs mit Inhalt. Der
**Panel**-Knopf erscheint nur in einem offenen Chat (dort gibt es Anhänge/
Referenzen/Dateien/Websuche); in Listen- und Übersichts-Ansichten
(Chats, Projekte, Artefakte, Geplant …) ist er ausgeblendet und ein offenes
Panel wird beim Verlassen geschlossen. Tabs mit Inhalt:
- **Anhänge** — in diesen Chat hochgeladene Dateien
- **Referenzen** — Quellen, die das Modell in diesem Turn gelesen hat
  (Web-Abrufe, Dokument-Lesungen)
- **Dateien** — Artifacts, die das Modell in diesem Turn erzeugt hat
- **Websuche** — kuratierte Web-Quellen für den nächsten Turn (siehe unten);
  jede markierte Quelle wird beim Senden frisch + vollständig geladen
- **Aktivität** — alle Tool-Aufrufe dieses Chats (synchrone + Hintergrund­aufgaben),
  chronologisch (neueste oben) in „Laufend" / „Abgeschlossen" (siehe unten)

Neben dem **Panel**-Knopf erscheint zusätzlich eine kleine **Uhren-Pille mit
Zähler**, sobald es Hintergrundaufgaben gibt — ein Klick öffnet direkt den
Hintergrundaufgaben-Tab.

Das Panel öffnet sich automatisch nur bei einem neuen **Output**-Artifact;
sonst glüht das Symbol kurz auf. Es öffnet beim ersten Tab mit Inhalt,
bis der Nutzer selbst einen Tab wählt. Schließt der Nutzer das Panel,
bleibt es bis zum Neuladen geschlossen.

**Statusleiste** (unten) — Verbindungspunkt, Agent, Modell, In/Out-Tokens,
Geschwindigkeit, Kontextfenster-Füllstand, Sitzungskosten, Plan-Nutzung,
Warmup-Status, Provider-Queue. Auf jedes Element klicken für Details. In/Out,
Kosten und Kontext-Füllstand **aktualisieren live während der Antwort** (pro
Runde, nicht erst am Ende). Wird eine Antwort **mitten im Stream abgebrochen**,
bleiben die bis dahin verbrauchten Tokens **und Kosten** erhalten (sie werden
pro Runde sofort verbucht, gehen also nicht verloren).

Ein Klick auf **Plan-Nutzung** öffnet ein Fenster mit den Kontingent-Balken
(täglich + Abrechnungszeitraum) und darunter der **Kostenaufschlüsselung**: oben
die Gesamtkosten, dann die Kosten **pro Anwendungsfall** (Chat, Chat-Zusammenfassung,
Übersetzung, Geplante Aufgaben, Studio, Deep Research, Audio Overview/Podcast,
Vorlesen, …) mit Balken, Prozentanteil und Aufrufzahl. Jede Zeile lässt sich
aufklappen, um die **Aufteilung nach Modell** (Tokens, Kosten, Anteil) zu zeigen.
Über das Auswahlmenü oben rechts wählt man den **Zeitraum** (Heute, Diese Woche,
letzte 7/30/180/365 Tage, seit Jahresbeginn, aktueller/letzter Abrechnungszeitraum,
Gesamt). Hinweis: lokale Modelle sind kostenlos und erscheinen mit 0 $ (aber echten
Tokens); Aufrufe von vor der Einführung dieser Funktion erscheinen als
*Unbekannt (Altdaten)*.

**Handy & Tablet (Responsive)** — die Oberfläche passt sich drei Größen an:
- **Desktop** (breit) — volle Drei-Spalten-Ansicht (Seitenleiste · Inhalt ·
  rechtes Panel).
- **Tablet/iPad** — die Seitenleiste bleibt fest sichtbar, das rechte Panel
  legt sich als Overlay über den Inhalt (statt ihn zu verengen).
- **Handy/iPhone** — die Seitenleiste ist ein **ausklappbares Menü**: oben
  links erscheint ein **☰-Knopf**, der sie einblendet; nach Auswahl einer
  Ansicht schließt sie sich wieder. So sind **alle** Funktionen (Projekte,
  Einstellungen, Workflows usw.) auch auf dem Handy erreichbar — nicht nur der
  Chat. Das rechte Panel und Einstellungs-Dialoge füllen auf dem Handy den
  Bildschirm.

**Eingabe-Werkzeugleiste** (über dem Textfeld, links):
- 📎 Dateien anhängen
- 🧠 Thinking-Level (off / low / medium / high — nur bei Modellen, die es können)
- 🔬 Recherche-Modus-Override (nur in Projekt-Chats)
- ✨ Verfeinern — den Entwurf vor dem Senden überarbeiten
- 🔧 Verfeinerungs-Modus (Polish ↔ Engineer) — der Schalter direkt neben ✨. **Polish** (Standard) säubert nur Grammatik/Klarheit und lässt die Absicht unverändert. **Engineer** (akzentfarben, wenn aktiv) strukturiert den Prompt um: präzisiert die Aufgabe, nutzt das aktive Modell + verfügbare Tools + Projekt-Anweisungen als Kontext, fragt bei hoffnungslos vagen Entwürfen nach (statt Details zu erfinden) und lässt bereits gute Entwürfe unangetastet. Pro Oberfläche gemerkt (Chat, geplante Aufgaben, Soul-Editor)
- 🛡️ GDPR-Details (PII-Funde inline aus-/einblenden)
- 💾 In-Gedächtnis-speichern-Zyklus (aus / an / auto)
- 🪨 Caveman-Modus (knappe Antworten)

**Standard vs. gemerkter Zustand:** Ein **neuer Chat** startet immer auf
**Standardwerten** — das Standard-/Agenten-Modell (nie das zuletzt benutzte),
Caveman aus, Gedächtnis-Modus auf der Server-Voreinstellung. Beim **Wieder-
öffnen eines bestehenden Chats** wird dagegen der **letzte Zustand dieses
Chats** wiederhergestellt: das im letzten Zug verwendete Modell sowie der
gespeicherte Caveman- und Gedächtnis-Modus. Ein neuer Chat ist außerdem immer
**allgemein (ohne Projekt)** — projektgebunden nur, wenn er **innerhalb eines
Projekts** gestartet wird. Eine **Übergabe** erbt den Modus des Ursprungs-Chats
(projektgebunden bleibt im Projekt, allgemein bleibt allgemein).

**Brainy** — die schwebende Sprechblase unten rechts (in jeder Ansicht).
Brainy ist ein **schreibgeschützter** Helpdesk-Assistent: er kennt diese
Oberfläche, kann Sitzungs-/Nutzerkontext lesen und Fragen beantworten,
ändert aber nie etwas. Siehe „Brainy" weiter unten.

---

## Eine Nachricht senden

Tippen, `Enter` drücken (Shift+Enter für Zeilenumbruch) oder den
Senden-Knopf klicken. Während das Modell antwortet, wird der Senden-Knopf
zum Stopp-Knopf.

**Prompt-Vorschlag (Tab)**: Nach jeder Antwort schlägt das Modell eine
mögliche nächste Frage vor — sie erscheint als ausgegrauter Platzhaltertext
im Eingabefeld. Mit `Tab` (oder `→` bei leerem Feld) wird der Vorschlag
übernommen, `Esc` verwirft ihn. Ist gerade kein Vorschlag zu sehen (noch
keiner berechnet oder vorher verworfen), erzeugt ein Druck auf `Tab` im
**leeren** Eingabefeld einen Vorschlag auf Anfrage (bereits berechneter wird
wiederverwendet, sonst neu generiert) und füllt ihn ein.

**Anhänge**: 📎 klicken oder Dateien ins Eingabefeld ziehen. Unterstützt:
Bilder, PDF, docx, xlsx, pptx, eml/msg, epub, txt/md, csv/tsv, json,
Quellcode (py/js/ts/go/rs/…), zip. Du kannst auch ganze **Ordner** ins
Eingabefeld ziehen — sie werden rekursiv aufgelöst und alle enthaltenen
Dateien einzeln angehängt (versteckte Dateien und `.git`-Verzeichnisse
werden übersprungen). Das Modell erhält Bilder direkt, wenn es Vision hat;
alles andere wird serverseitig zu Markdown konvertiert und mit
`read_document` gelesen.

**Modelle**: über das Modell-Badge in der Statusleiste oder im Eingabefeld
wechseln. Lokale Modelle bleiben auf dem Gerät; Cloud-Modelle gehen an den
konfigurierten Provider. Ganz oben in der Liste stehen zwei automatische
Modi: **✨ Smart (Cloud)** und **✨ Smart (Lokal)** — sie wählen pro Nachricht
automatisch das am besten passende Modell, wobei **Cloud** nur unter den
Cloud-Modellen und **Lokal** nur unter den lokalen Modellen sucht (die
Klassifizierung der Anfrage ist identisch, nur der Kandidatenpool ist
eingeschränkt). Wie die Absicht erkannt wird, steuert Einstellungen → Server →
Auto-Routing. Wird beim Senden eine DSGVO-Sperre ausgelöst, verschwindet
**Smart (Cloud)** aus der Liste, **Smart (Lokal)** bleibt (es trifft ohnehin
nur lokale Modelle). Der Verfasser zeigt weiter „✨ Smart (…)"; welches Modell
gerade arbeitet, steht in der Status-Zeile am Anfang der laufenden Antwort
(siehe unten) und im Tooltip.

**Klassifikations-Inspektor (Lupe am Turn-Ende)**: Lief für eine Anfrage der
LLM-Classifier (Einstellungen → Server → Auto-Routing = LLM/Hybrid), erscheint
unter der Antwort eine kleine Lupe. Ein Klick öffnet ein Fenster mit der
Klassifikation (Aufgabentypen, Komplexität), bei ✨ Smart zusätzlich der
Modellentscheidung — und vor allem mit den **tatsächlich übergebenen Tools
dieser Anfrage**: exakt, welche Tools im Prompt standen, welche zurückgestellt
wurden (per `tool_search` weiterhin erreichbar) und welche hart ausgeschlossen
waren. Der Inspektor erscheint **pro Anfrage** (der Classifier läuft in jedem
Turn) — auch wenn ein Modell manuell gewählt wurde, nicht nur bei ✨ Smart.
Zusätzlich zeigt er die **Research-Disziplin** dieser Anfrage: ob sie eingefügt
wurde, warum (Retrieval-Tool aktiv bzw. Recherche-Modus an), wie (Wire-Präambel
dynamisch oder System-Prompt) und welche der drei Sektionen (Refusal / Precision
/ Citation) dabei waren. Die drei werden immer zusammen als ein Block eingefügt;
einzelne fehlen nur, wenn ein Admin sie global abgeschaltet hat.

**Abbrechen / Wieder-Anhängen**: Stopp drücken bricht den Turn ab.
Schließt man den Browser-Tab mitten im Turn, einfach den Chat wieder
öffnen — der Worker läuft weiter und der Verlauf holt auf.

**Anfragen auf-/zuklappen**: Jede Anfrage hat oben ein **Anfrage N**-Badge.
Ein Klick klappt diese eine Anfrage auf oder zu. **Lange gedrückt halten**
(≈ ½ Sekunde) klappt **alle** Anfragen auf oder zu — die Richtung richtet
sich nach der gehaltenen Anfrage (eine offene gehalten → alle zu; eine
zugeklappte gehalten → alle auf); danach wird die gehaltene Anfrage wieder
in den Blick gescrollt. Alle Auf-/Zuklapp-Bereiche im Chat — Anfragen, der
**Datenschutz**-Block, verdichteter Kontext, Webquellen, Quellen-Legende,
durchsuchte Quellen — animieren weich.

**Live-Anzeige während der Antwort (Claude-Code-Stil)**: Denken und Tool-Aufrufe
erscheinen jetzt **direkt im Chat-Verlauf, genau in der Reihenfolge, in der sie
passieren** — kein eigener Block, kein Auf-/Zuklappen. **Denken** steht in der
Chat-Schrift, etwas heller und kursiv; **Tool-Aufrufe** stehen als kompakte,
anklickbare Zeile (Klick öffnet die Details im rechten Panel) und zeigen ihre
Ausführungsdauer. Am Anfang der laufenden Antwort steht eine **Status-Zeile**
(Spinner-Balken + arbeitendes Modell + „Denke nach…" + verstrichene Zeit); sie
verschwindet automatisch, sobald der Turn fertig ist oder abgebrochen wird.
Nur **Datenschutz**-Vorgänge (Anonymisierung / De-Anonymisierung) bleiben in
einem eigenen, aufklappbaren **Datenschutz**-Block mit Zählern zusammengefasst.

**Quellenangaben (Zitat-Chips)**: Belegt die Antwort eine Aussage mit einer
Quelle, erscheint an der Stelle eine kleine nummerierte Hochzahl **[1] [2] …**.
Unter der Antwort fasst der **Quellen**-Block (aufklappbar, standardmäßig
zugeklappt — auch nach dem Neuladen) jede Nummer mit Datei + wörtlichem Zitat
zusammen; ein **⚠** markiert ein Zitat, das der Validator nicht in der Quelle
verifizieren konnte. Mit der Maus über einen Chip → Datei + Zitat als Tooltip. **Klick** auf
einen Chip öffnet ein kleines Fenster mit **Im Dokument öffnen →** (öffnet die
Quelldatei rechts und hebt die zitierte Stelle hervor) bzw. **Quelle öffnen ↗**
bei Web-Quellen (öffnet die URL). Die Zitier-Disziplin wird automatisch
angewandt, wenn eine Antwort auf abgerufenen Inhalten beruht (Gedächtnis-,
Web- oder Dokument-Abfrage) — in jedem Chat, ohne manuelle Einstellung.

**Quellentreue-Hinweis (Badge)**: Hat die Antwort bei einer Recherche-Anfrage
Aussagen ohne Beleg, erscheint unter der Antwort ein kleines bernsteinfarbenes
Badge **„⚠ x von y ohne Quellenangabe"**. Mit der Maus darüber zeigt ein
Tooltip den vollständigen Hinweis (inkl. der Möglichkeit, dass zur Frage
schlicht keine passenden Quellen vorlagen — bitte einzelne Aussagen vor
Weiterverwendung gegen die Originalquellen prüfen). Das Badge erscheint **nur**,
wenn in der Anfrage tatsächlich eine Quelle abgerufen wurde (Datei gelesen,
gesucht, Web abgerufen oder Gedächtnis abgefragt) — eine reine Wissensantwort
ohne Abruf zeigt es nicht.

---

## Chats

**Chats** klicken listet alles. Zeilenmenü (⋯):
- Umbenennen, markieren, archivieren, löschen
- Projekt zuweisen (in ein Projekt verschieben)
- Teilen — Sichtbarkeit ändern (privat / bestimmte Nutzer / Team / global)
- Turns merken / verwerfen — auswählen, welche Nachrichten ins Gedächtnis kommen

**Audio zu jeder Antwort** — unter jeder Assistenten-Antwort gibt es zwei
Audio-Knöpfe (neben Kopieren):
- **🔊 Vorlesen** — liest die Antwort laut vor (Text-zu-Sprache). Lange
  Antworten werden automatisch in Stücke geteilt und nacheinander abgespielt;
  während der Wiedergabe **leuchtet der Knopf** (aktive Audioausgabe), erneut
  klicken stoppt. Während Sprache erkannt + Audio erzeugt wird **pulsiert der
  Knopf**, sobald wirklich abgespielt wird **leuchtet er** (aktive Audioausgabe).
  Die Sprache wird **einmal zu Beginn** erkannt und für die gesamte Wiedergabe
  festgehalten — ein fremdsprachiges Zitat in einem späteren Stück lässt die
  Stimme nicht mehr umschalten. (Markdown/Code wird vor dem Vorlesen entfernt.)
- **🎧 Podcast aus diesem Chat** — erzeugt einen **Audio Overview** (zwei Hosts)
  über den **gesamten bisherigen Chatverlauf** — wie der Studio-Podcast eines
  Projekts, nur aus der Unterhaltung. Dauert ~1 Minute; währenddessen **pulsiert
  der Knopf**, und ein erneuter Klick **bricht die Erstellung ab**. Danach öffnet
  sich ein Player und die `.mp3` (plus Skript) liegt — mit sprechendem Namen
  („Podcast — <Chattitel>“) — in den Artefakten der Sitzung; ein Klick darauf
  spielt sie direkt im Panel ab. **Solange sich der Chat nicht ändert, wird der
  vorhandene Podcast wiederverwendet** statt neu (und kostenpflichtig) erzeugt —
  erst bei geändertem Inhalt entsteht eine neue Folge. Die
  **Sprache wird automatisch erkannt** — ein deutscher Chat ergibt einen deutschen
  Podcast (Voxtral spricht 9 Sprachen). Standardmäßig werden englische Stimmen
  verwendet; für muttersprachlichen Klang eine eigene Stimme unter **Einstellungen
  → Tools → Sprachausgabe → „🎙️ Stimmen verwalten“** klonen. Geht in JEDEM Chat,
  auch ohne Projekt. Die **Kosten** (Skript-LLM + Sprachsynthese) werden erfasst
  und zählen wie jeder andere Aufruf ins Nutzungsbudget. *(Im Chat genügt auch
  die Bitte „mach einen Podcast aus diesem Gespräch“.)*

**Suche** (Seitenleiste): semantisch + Stichwort über alle sichtbaren
Nachrichten.

**Archivieren ≠ Löschen**: archivierte Chats sind aus der Liste
ausgeblendet, ihre Gedächtnis-Drawer bleiben aber erhalten. Löschen
entfernt alles inklusive Drawer.

**In-Gedächtnis-speichern** (Eingabe-Knopf oder Chat-Einstellung):
- **aus** — nichts aus diesem Chat geht in MemPalace
- **an** — jeder Turn wird in den privaten Wing eingespeist
- **auto** — der Server-Klassifizierer entscheidet pro Nachricht
  (Fakten / Entscheidungen / Referenzen bleiben; Smalltalk wird übersprungen)

Für punktgenaue Kontrolle das Pro-Turn-🌐-Menü neben jeder Nachricht:
*diese merken / komplett merken / diese verwerfen / oben verwerfen /
unten verwerfen*.

---

## Projekte

Ein Projekt ist eine Wissensbasis mit eigenem privaten Gedächtnis und
eigenen Dateien. Nutze ein Projekt, wenn die KI konsistent aus einem
bestimmten Dokumentkorpus schöpfen soll (Richtlinien, Handbücher,
Codebasen, Forschungspapiere, …).

**Projekt anlegen**:
1. Seitenleiste → **Projekte** → ＋ neu
2. Benennen. Optional: Beschreibung und Bild.
3. **Eingabeordner** — auf Verzeichnisse zeigen, die eingespeist werden
   sollen. Rekursiv- + Auto-Sync-Flags pro Ordner.
4. **Hochladen** — Einzeldateien über das ＋ im Dateien-Zweig, oder einen
   ganzen **Ordner** über das 📁 (bzw. per Drag&Drop auf den Dateien-Zweig).
   Ein Ordner-Import (per Drag&Drop) zeigt zuerst einen Bestätigungsdialog
   (Anzahl Dateien/Ordner/Größe; beim 📁-Picker entfällt der, weil der Browser
   selbst schon fragt), dann einen Fortschrittsdialog mit Balken und einem
   **Abbrechen**-Button (bereits importierte Dateien bleiben erhalten). Am Ende
   erscheint ein **Status** — bei Fehlern bleibt der Dialog offen und listet,
   welche Dateien nicht importiert werden konnten und warum (z. B. „Kein Text
   extrahierbar", „Dateityp nicht unterstützt"). Die Unterordner-Struktur wird
   als Gruppen übernommen.
5. **Web-Adressen** — über das ＋ im Web-Adressen-Zweig öffnet sich ein Dialog
   (Adresse + optionale Bezeichnung). Eine Liste von URLs, die das Projekt frisch abruft
   und ins Projektgedächtnis + KG einspeist (per crawl4ai gerendert, also
   auch JS-Seiten). Anderer Mechanismus als die Websuche im Chat. Zeigt eine
   Web-Adresse auf eine **Datei** (PDF, DOCX, XLSX …) statt auf eine Webseite,
   wird ihr Inhalt wie bei einem hochgeladenen Dokument extrahiert (nicht der
   Rohcode gespeichert). Ein **Klick** auf eine Web-Adresse im Quellen-Baum
   öffnet sie in einem neuen Tab (auch über das ↗-Symbol). Mit dem
   🔗-Symbol in der Web-Adressen-Zeile lassen sich **verlinkte Dokumente**
   finden: Brain durchsucht die hinterlegten Webseiten nach Links auf Dateien
   (PDF/DOCX/XLSX …) derselben Domain und schlägt sie zur Aufnahme vor — du
   wählst aus, was ins Projekt übernommen wird (nichts wird automatisch
   importiert; keine rekursive Suche).
6. **Projektmodus** / **Recherche**-Umschalter — siehe unten.

**Code-Projekt** (eigener Projekttyp): In der Projektübersicht gibt es zwei
Knöpfe — **Neues Projekt** und **Neues Code-Projekt**. Ein Code-Projekt arbeitet
grundlegend anders: statt Dateien ins Projektgedächtnis (MemPalace) einzulesen,
arbeitet es direkt in einem von dir gewählten **Arbeitsverzeichnis** (meist dein
Code-Ordner) — das Modell liest, bearbeitet und erzeugt Dateien dort, und
Befehle/Code laufen in diesem Verzeichnis (nicht im Artifact-Ordner). Es gibt
**kein** Ingest und **kein** Projektgedächtnis; die Quellen-/Ingest-Ansicht
entfällt, Projekt-Anweisungen gelten weiter. **Der Typ ist fest** und kann
nachträglich nicht mehr geändert werden (er wird beim Anlegen gewählt). In der
Übersicht haben Code-Projekte ein eigenes `</>`-Symbol. Vorgehen: **Neues
Code-Projekt** → beim Anlegen (oder später in den Einstellungen) das
**Arbeitsverzeichnis** wählen → **BRAIN.md generieren (init)** klicken (oder im
Chat einfach `init` schreiben). `init` lässt das Modell das Verzeichnis erkunden
und eine **BRAIN.md** im Wurzelverzeichnis schreiben — eine Zusammenfassung
(analog CLAUDE.md), die künftig als Projektgedächtnis dient (reines Markdown,
wird nicht gemined). Existiert eine BRAIN.md, wird ihr Inhalt dem Modell bei
jeder Antwort mitgegeben. Während `init` läuft, zeigt die Code-Mode-Sektion einen
**Fortschrittsanzeiger** (Spinner + verstrichene Zeit) mit einem
**Abbrechen**-Knopf; danach erscheint der Ausgang (✓ generiert / abgebrochen /
Fehler). Der **Datei-Baum des Arbeitsverzeichnisses** darunter aktualisiert sich
**von selbst**, sobald Dateien angelegt, geändert oder gelöscht werden (z. B.
während `init` oder einer Chat-Antwort) — kein manuelles Neuladen nötig (der
⟳-Knopf bleibt für eine sofortige Aktualisierung).

**Projekt-Anweisungen + Begleitdateien**: Über **Anweisungen bearbeiten**
öffnet sich ein Dialog mit dem Freitext-Feld (Hinweise, an die sich das
Projekt in jeder Antwort hält — Tonfall, Sprache, Formatvorgaben; Markdown +
Vorschau). Darunter der Bereich **Begleitdateien**: hier lassen sich
ergänzende Dateien hochladen (Styleguide, Vorlage, Begriffsliste, erläuternde
Unterlagen — beliebige Typen, max. 25 MB/Datei). **Wichtiger Unterschied zu
hochgeladenen Projektdateien (Schritt 4):** Begleitdateien werden **NICHT**
ins Projektgedächtnis aufgenommen/gemined. Sie funktionieren wie ein
Chat-Anhang — der Assistent bekommt ihren Speicherort genannt und liest die
jeweils passende bei Bedarf eigenständig, verlässt sich also direkt auf die
**Datei** statt auf eine Gedächtnis-Suche. Nutze sie für Material, das die
Anweisungen ergänzt und wörtlich vorliegen soll; nutze Schritt 4 / Eingabe-
ordner für einen durchsuchbaren Dokumentkorpus.

Die Hilfe auf der Projekt-Einstellungsseite ist ein aufklappbarer
„Hilfe"-Bereich.

**Quellen-Baum** (rechter Bereich der Projektseite): Anweisungen, Dateien,
Ordner und Web-Adressen erscheinen als ein gemeinsamer, aufklappbarer Baum
— bis auf Dateiebene. Ein **Farbpunkt** pro Element zeigt den Status im
Projektgedächtnis: 🟢 indexiert · 🟠 ausstehend · 🔴 Fehler · ⚪ veraltet
(Legende oben). Eingespeiste Ordner lassen sich bis zu den echten Dateien
aufklappen (schreibgeschützt — die Ordnerstruktur ist fest). Du kannst
**virtuelle Gruppen** anlegen (⊞ in der Bereichszeile, ＋ für Untergruppen,
bis zu 3 Ebenen) und Elemente per **Ziehen & Ablegen** hineinsortieren;
mit Cmd/Strg-Klick mehrere gleichartige Elemente auswählen und gemeinsam
ziehen (Esc hebt die Auswahl auf). Gruppen sind typgebunden (eine Datei-
Gruppe nimmt nur Dateien). Pro Gruppe gibt es zwei Lösch-Aktionen: **✕**
löst NUR die virtuelle Gruppierung auf (die Quellen bleiben, wandern zur
übergeordneten Ebene); **🗑️** löscht die Gruppe samt allen Untergruppen UND
allen darin enthaltenen Dokumenten/Ordnern/Web-Adressen dauerhaft (mit
Bestätigung samt Anzahl). Der Baum startet eingeklappt; den Aufklapp-
Zustand merkt sich der Browser pro Projekt.

**Recherche-Modus** — wie er gesteuert wird, hängt vom Auto-Routing-Modus ab
(Einstellungen → Server → Auto-Routing):
- **Bei LLM-/Hybrid-Routing (automatisch):** Die Zitier-/Refuse-Disziplin wird
  **dynamisch** angewandt, sobald für die Antwort ein Abruf-Tool aktiv ist
  (Gedächtnis, Websuche, Web-Abruf, Dokument-/Datei-Lesen) — in **jedem** Chat,
  Projekt oder nicht. Der manuelle Projekt-Schalter und der 🔬-Knopf werden
  dann **ausgeblendet** (statt deaktiviert) — an ihrer Stelle steht der Hinweis,
  dass der Recherche-Modus automatisch aktiviert wird, wenn die Anfrage ihn
  benötigt. Eine manuelle Umschaltung ist nicht nötig.
- **Bei Schlüsselwort-Routing (Standard):** Der manuelle Schalter steuert es —
  Projekt-Standard, pro Chat per 🔬-Knopf überschreibbar:
- **AN** (Frage-Antwort-Projekt) — für Richtlinien-/Compliance-/Q&A-Projekte.
  Das Modell muss zuerst das Projektgedächtnis konsultieren, verweigert
  bei leerem Treffer, muss pro Aussage mit wörtlichem Zitat belegen. Ein
  serverseitiger Zitat-Validator läuft mit.
- **AUS** — für Codegen/Entwurf/Bauen-mit-Kontext. Kein Zwang zu Zitaten,
  keine Verweigerung bei leerem Treffer; das Modell darf auf sein Training
  zurückgreifen. **Wichtig (entkoppelt):** Hat das Projekt eigene kuratierte
  Quellen (hochgeladene Dateien, Eingabeordner oder Web-URLs), wird das
  Projektgedächtnis trotzdem ZUERST abgefragt und den freien Web-Tools
  vorgezogen — auch ohne Recherche-Modus. Nur die strenge Zitier-/Refuse-
  Disziplin hängt am Recherche-Modus. (So nutzt z. B. ein News-Projekt mit
  hinterlegten URLs wirklich diese Quellen, statt frei zu googeln.)

**Websuche unterbinden** (Projekt-Setting, Checkbox unter dem Projektmodus):
Wenn aktiv, dürfen Chats UND geplante Aufgaben dieses Projekts die Web-Tools
(web_fetch / Websuche / exa) NICHT nutzen — das Modell muss ausschließlich
aus dem Projektgedächtnis antworten. Das ist eine **harte, modell-
unabhängige** Sperre (die Tools werden für den Turn entfernt), nicht nur
eine Bitte im Prompt: manche Modelle (z. B. mistral-medium) ignorieren den
Prompt-Hinweis und googeln trotzdem — diese Sperre verhindert das
zuverlässig. Empfohlen für Projekte mit bewusst kuratierten Quellen. Bei
aktiver Sperre weist der Agent im Prompt explizit darauf hin, dass er aus
einem **geschlossenen Korpus** antwortet (nur die kuratierten, geprüften
Projektquellen — jede Web-Quelle ist EINE gespeicherte Seite, kein Crawl der
ganzen Website) und sagt klar, wenn etwas nicht abgedeckt ist, statt eine
breitere Web-Analyse vorzutäuschen. Mehr Tiefe holt man, indem man VOR dem
Sperren eine Deep-Recherche fährt (Quellen finden → prüfen → übernehmen) —
nicht durch tieferes Mining.

**Wissensgraph (KG)** (Projekt-Setting, eigener Abschnitt im Projekt-Panel):
zwei Dropdowns — **Methode** und **Profil** — jeweils mit „Standard
übernehmen" (= der in den Einstellungen gesetzte Projekt-Standard). Methode
**LLM** extrahiert hochwertigere Triples (kann ein Cloud-Modell sein);
**Regelbasiert** läuft ganz ohne LLM und lokal (spaCy-NER + Beziehungsmuster),
liefert aber nur generische Prädikate und geringere Qualität — gut für
biografische/relationale Inhalte oder wenn nichts das Gerät verlassen soll.
Das Profil (normative/generic) ist nur bei der LLM-Methode wirksam.

**Synchronisierung**: Eingabeordner werden von einem Daemon alle 6 h
eingespeist. Knöpfe auf der Projektseite:
- **Jetzt synchronisieren** — neue/geänderte Dateien sofort einspeisen
- **Vollständig neu synchronisieren** — den Gedächtnis-Wing des Projekts
  leeren und alles neu einspeisen
- **Sync-Verlauf** — vergangene Läufe + Ergebnisse pro Datei
- **Knowledge Graph** — extrahierte Entitäts-Relations-Tripel
  (nur sinnvoll bei normativen Dokumenten wie Richtlinien/Vorschriften)

**Projekt-Chats**: Ein Chat von der Projektseite aus skopiert
Gedächtnis-Abfragen automatisch auf dieses Projekt. Das Modell sieht das
`instructions`-Feld des Projekts plus die passende Recherche-Disziplin.

**Geplante Aufgaben im Projekt**: Auf der Projektseite gibt es neben den
Tabs **Chats** / **Archiviert** den Tab **Geplante Aufgaben**. Dort legst
du wiederkehrende, zeitgesteuerte Aufgaben an, die im Kontext genau dieses
Projekts laufen — sie sehen also dieselben `instructions`, das
Projektgedächtnis (Wing) und den Recherche-Modus wie ein Projekt-Chat.
- **Neue Aufgabe** öffnet dasselbe Formular wie eine normale geplante
  Aufgabe — Name, Prompt (mit „Mit KI verfeinern"), Modell, Häufigkeit
  (Stündlich/Täglich/Wöchentlich/Benutzerdefiniert + Uhrzeit), Timeout,
  Denkstufe und Caveman-Modus. Nur Arbeitsverzeichnis, Tool-Profil und
  Anhänge fehlen (im Projektkontext nicht nötig); der Agent ist fest der
  des Projekts.
- Pro Aufgabe (Drei-Punkte-Menü): **Pausieren/Fortsetzen**, **Jetzt
  ausführen**, **Verlauf**, **Löschen**.
- Im **Verlauf** stehen die vergangenen Läufe mit Zeitpunkt, Status
  (grün = erfolgreich, rot = Fehler) und **Kosten** des Laufs. Ein Klick auf
  einen Lauf öffnet die Detailansicht mit **Dauer · Tools · Tokens ein/aus ·
  Kosten**, dem **Ergebnis-Text**, den erzeugten **Artefakten** und dem
  **Tool-Verlauf** (dieselbe Detailansicht wie im globalen Zeitplan-Tab).
- Die Bindung an das Projekt wird automatisch gesetzt — kein Projekt-Dropdown
  nötig. Der globale **Zeitplan**-Tab in den Agent-Einstellungen bleibt
  davon unberührt (Aufgaben ohne Projektbindung laufen wie bisher
  agent-weit).

**Ausgaben aus den Quellen generieren** (Output-Presets + Studio): Auf der
Projektseite gibt es neben **Chats** / **Archiviert** / **Geplante Aufgaben** den
Tab **Studio**. Dort lassen sich aus den Quellen eines Projekts mit einem Klick
fertige, belegte Dokumente erzeugen — vier Vorlagen:
- **Study Guide** (📖) — Schlüsselkonzepte · Begriffe/Definitionen · Wiederholungsfragen.
- **Briefing** (📋) — Kurzfassung → Kernpunkte → Implikationen.
- **FAQ** (❓) — belegte Frage-/Antwort-Paare.
- **Timeline** (🕒) — chronologische, datierte Ereignisse (lässt sie weg statt
  zu erfinden, wenn die Quellen keine Daten enthalten).
- **Audio Overview** (🎧) — ein **Podcast** im Stil von NotebookLM: zwei KI-Hosts
  (Oliver & Jane) besprechen die Projektinhalte in einem natürlichen Gespräch.
  Ergebnis ist eine **`.mp3`-Audiodatei** (plus das Dialog-Skript als `.md`). Über
  **Öffnen** erscheint ein Audio-Player direkt im Studio; **Herunterladen** lädt
  die MP3. **Die Sprache wird automatisch erkannt** — ein deutschsprachiges Projekt
  ergibt einen deutschen Podcast (Voxtral spricht 9 Sprachen: en/fr/de/es/nl/pt/it/
  hi/ar). Es werden Stimmen passend zur Sprache gewählt, sofern vorhanden; sonst
  englische Standardstimmen (für muttersprachlichen Klang eine Stimme unter
  Einstellungen → Tools → Sprachausgabe klonen). Die Generierung dauert länger als
  ein Textdokument (Skript schreiben → jede Zeile vertonen → zusammenfügen); der
  Fortschritt wird als Phase angezeigt (Sammeln → Skript → Vertonen N/M).

Jede Text-Ausgabe wird **streng aus dem Projektgedächtnis** erzeugt und **verbatim
zitiert** (`[Quelle: … — "…"]`); nichts wird hinzuerfunden. Das Ergebnis ist eine
gespeicherte `.md`-Datei, die im Projekt erhalten bleibt (beim Audio Overview eine
`.mp3`). Optional lassen sich ein **Fokus** (Schwerpunkt-Stichwort) und eine
**Länge** (Kurz/Standard/Lang) angeben.

> Der Audio Overview lässt sich auch **im Chat** erzeugen: in einem geöffneten
> Projekt einfach nach einem „Podcast“ / „Audio-Überblick“ fragen — der Agent nutzt
> dann das Tool `generate_audio_overview` und legt die MP3 in den Artefakten der
> Sitzung ab. Außerhalb eines Projekts geht das nicht (es fehlen die Quellen). Hat ein Projekt noch keine Quellen, ist die Generierung nicht möglich
(erst Dateien/Web-Adressen hinzufügen oder Recherche laufen lassen). Die
Generierung läuft im Hintergrund (~20–40 s) — man kann die Seite verlassen, die
fertige Ausgabe taucht im Studio-Tab auf (er aktualisiert sich von selbst).

Im **Studio**-Tab sind alle erzeugten Ausgaben nach Typ gruppiert aufgelistet
(mit Anzahl der Zitate + Zeitpunkt). Pro Ausgabe: **Öffnen** (zeigt das belegte
Dokument), und über das **⋯**-Menü **Umbenennen**, **Neu generieren** (erzeugt
eine neue Ausgabe, die alte bleibt erhalten), **Herunterladen** und **Löschen**
(entfernt Eintrag + Datei; während eine Ausgabe noch generiert, ist Löschen
gesperrt). *(Hinweis: Inline-Zitat-Chips zum Anklicken folgen in einem späteren
Schritt; aktuell stehen die Belege als `[Quelle: …]` im Text.)*

**Neue Quellen finden** (Research-Tab 🔍): Auf der Projektseite gibt es den Tab
**🔍 Research**, um neue Quellen für das Projekt zu finden. Thema eingeben, Modus
wählen, **Recherche starten**. (Es wird das eine aktivierte Such-Tool verwendet —
wie bei der Websuche im Chat; eingestellt unter Einstellungen → Tools.) Zwei Modi:
- **Fast** — schnelle Suche. Es erscheint eine Trefferliste; die gewünschten
  Treffer ankreuzen und **importieren** → sie werden als Projekt-Quellen
  hinzugefügt und beim nächsten Sync ins Gedächtnis gemined (im Chat durchsuchbar).
  Bereits im Projekt vorhandene Treffer sind markiert und gesperrt.
- **Deep** — die KI plant Unterfragen, sucht breit, liest die besten Quellen und
  schreibt daraus einen **strukturierten, belegten Bericht** (im Studio gespeichert).
  Der Fortschritt (Planen → Suchen → Lesen → Schreiben) und das Budget (max. Anzahl
  Seitenabrufe) werden live angezeigt; der Lauf läuft weiter, auch wenn man den Tab
  verlässt, und lässt sich **abbrechen**. Am Ende: ein Link zum Bericht **und** eine
  Liste **vorgeschlagener Quellen** zum Import (nichts wird automatisch importiert —
  man wählt selbst aus). Das Budget ist begrenzt und der Bericht nennt offen, wenn
  die Abdeckung dadurch beschränkt war.

Research ist nur verfügbar, wenn ein Such-Tool (SearXNG oder Exa) in den
Tool-Einstellungen aktiviert ist (sonst ist der Tab mit Hinweis deaktiviert).

---

## Dokument-Prüfung (DSGVO + Klassifizierung)

Mit dem **Prüfen**-Dialog kannst du ein einzelnes Dokument im Detail auf
DSGVO-Verstöße (personenbezogene Daten) **und** Vertraulichkeits-Klassifizierung
untersuchen — mit Hervorhebungen direkt im Text.

**Wo du ihn öffnest:**
- **Daten-Ansicht** — nach einem Klassifizierungs-Scan: Knopf **Prüfen** in jeder
  Ergebniszeile.
- **Projekt-Dateibaum** — **Rechtsklick** auf eine Datei oder einen Ordner →
  „GDPR/Klassifizierung prüfen". Bei einem Ordner werden alle enthaltenen
  Dateien geprüft.
- **Rechtes Panel → Anhänge** — das ⚖-Symbol bzw. Rechtsklick auf eine auf der
  Festplatte liegende Anhang-Datei.

**Im Dialog:**
- Der Dokumenttext wird angezeigt; jeder Treffer ist **farbig hervorgehoben**
  (rot = DSGVO/PII, orange = Klassifizierungs-Markierung).
- Oben ein **Navigator** (‹ n / N ›, auch mit ←/→) springt von Treffer zu
  Treffer; Filter Alle / GDPR / Klassifizierung.
- Ein **Tooltip** an jedem Treffer erklärt, *warum* es ein Verstoß ist.
- **Übersteuern** — einen Treffer mit kurzer Begründung akzeptieren; die
  Begründung wird gespeichert.
- **Anonymisieren** — ersetzt die (nicht übersteuerten) personenbezogenen Daten
  durch realistische, **umkehrbare** Platzhalter. Wichtig: die Datei auf der
  Festplatte bleibt unverändert — anonymisiert wird nur die Version, die an ein
  **externes LLM** geht. Alles, was nur lokal läuft (deine Ansicht, der
  Download), sieht weiterhin das Original.
- **Zurücksetzen** — hebt die Anonymisierung wieder auf (das Original wird
  verwendet); die Übersteuerungs-Begründungen bleiben erhalten.
- **Anon. Kopie exportieren** — lädt eine eigenständige, anonymisierte Kopie
  herunter, in deren Metadaten die Prüfung + der (verschlüsselte)
  Rück-Index eingebettet sind. Lädst du diese Datei später wieder in einen Chat
  oder ein Projekt, erkennt die Prüfung sie als bereits geprüft.

**Automatisch beim Hinzufügen:** Wenn du Dateien oder Ordner zu einem Projekt
hinzufügst, läuft die Prüfung sofort — die **Symbole** im Dateibaum zeigen den
Status: 🛡️ anonymisiert · ⚠️ offene Verstöße · ✓ geprüft/sauber. Beim erneuten
Einlesen einer **unveränderten** Datei wird die Prüfung nicht wiederholt — deine
Übersteuerungen und die Anonymisierung werden wiederverwendet, du wirst nicht
erneut gefragt.

---

## Übersetzung

Seitenleiste → **Übersetzung**. Vier Tabs:

### Text-Tab
- Text links einfügen, Übersetzung erscheint rechts.
- Quellsprache wird automatisch erkannt; manuell über die **Von**-Pille.
- **Glossar**-Auswahl — eine gespeicherte Begriffsliste anwenden
  (konsistente Terminologie).
- **Ton**-Auswahl — formal / locker / technisch / …
- 🔊-Knöpfe lesen Quelle oder Übersetzung vor (TTS).
- Tausch-Pfeile drehen Quelle↔Ziel.

### Dokument-Tab
- `.docx`, `.pptx` oder `.pdf` per Drag&Drop ablegen.
- **Von** und **Nach** Sprache wählen.
- **Übersetzen** klicken. PDF wird zuerst in docx konvertiert.
- Ergebnis erscheint im **Verlauf** darunter — **Herunterladen** speichert.
- Formatierung (Überschriften, Tabellen, Fußnoten, Folien-Layouts) bleibt
  per chunkweiser OOXML-Bearbeitung in-place erhalten.

### Audio-/Video-Tab
- Audio oder Video ablegen. Voxtral transkribiert + übersetzt.
- **Modus**-Auswahl: Untertitel (SRT/VTT), Transkript (TXT) oder beides.
- Verlaufszeile hat eigene Herunterladen-Knöpfe pro Ausgabeformat.

### Live-Mikrofon-Tab
- Aufnahme klicken. Sprechen. Übersetzung erscheint während des Sprechens.
- **Modus**: Live-Untertitel vs. satzweise Stücke.
- Stopp finalisiert; als SRT/VTT/TXT herunterladbar.

### Glossare
- **Glossare**-Knopf oben rechts öffnet das Modal.
- Pro Sprachpaar Begriffslisten anlegen für konsistente Übersetzung über
  Dokumente hinweg (besonders für Recht/Technik/Marken-Terminologie).

---

## Geplante Aufgaben

Seitenleiste → **Geplante Aufgaben** → ＋ neu. Eine geplante Aufgabe ist ein
Prompt, der nach einem Cron-Zeitplan mit vollem Tool-Zugriff läuft.
Ergebnisse werden als Artifacts gespeichert und im Verlauf der Aufgabe
sichtbar.

**Anlegen**:
- **Name** — Slug (für Abbruch / Jetzt-ausführen)
- **Aufgabe** — der Prompt für den Agenten. Sei konkret zum Liefergegenstand.
- **Zeitplan** — Cron-Ausdruck (`0 8 * * *` = täglich 08:00) oder `@every 30m`.
- **Modell** — wählen. Lokale Modelle kosten nichts; Cloud zählt aufs
  Tageskontingent.
- **Timeout** — Sekunden bis zum Abbruch (Standard 300; für lange Jobs erhöhen).
- **Anhänge** (optional) — Dateien, die die Aufgabe lesen kann (dieselben
  Dateien bei jedem Lauf wiederverwendet, keine Pro-Lauf-Kopien).
- **Arbeitsverzeichnis** — überschreibt das cwd der Aufgabe.
- **Tool-Profil**:
  - leer = research-minimal (weniger Tools, schneller, günstiger)
  - `interactive` = volle Toolbox (für „echte Arbeit"-Aufgaben)
- **Thinking-Level** + **Caveman-Chat** — Pro-Aufgabe-Overrides bei Bedarf.

**Verwalten**: Filter-Tabs oben (Alle / Laufend). Pro-Zeile-Knöpfe:
**Jetzt ausführen**, **Pausieren / Fortsetzen**, **Bearbeiten**,
**Löschen**, **Verlauf leeren** oder Lauf-Detail öffnen (Ausgabe + Kosten
+ Artifacts + Traces).

**Artifacts aus einem geplanten Lauf** erscheinen in der Artifacts-Ansicht
mit der synthetischen Sitzung `sched-<run_id>` und im Lauf-Detail.

---

## Workflows

Seitenleiste → **Workflows**. Mehrstufige Automatisierungen: jeder Schritt
ist ein Prompt oder ein `ask_user`-Gate. Nutze diese, wenn eine
wiederkehrende Aufgabe an bestimmten Punkten menschliche Freigabe braucht
(z. B. „E-Mail entwerfen" → Mensch gibt frei → „via gmail_send senden").

Im Editor anlegen: Name, Beschreibung, geordnete Schritte mit optionalen
Datei-Uploads und `ask_user_for_file` / `ask_llm`-Blöcken. Aus der
Workflows-Ansicht starten; das **Ausführungen**-Panel zeigt den
Live-Status mit Freigeben-/Abbrechen-Knöpfen.

---

## Artifacts

Seitenleiste → **Artifacts**. Zwei Ansichten:
- **Raster**: alle Output-Dateien (md, html, pdf, Bilder) über alle
  Sitzungen. Standardmäßig **nur Outputs** (versteckt Zwischendateien wie
  `.py` / `.csv`, die das Modell als Notiz schrieb).
- **Durchsuchen**: Verzeichnisansicht unter `agents/<id>/artifacts/`.

**Pro-Artifact-Aktionen** (rechtes Panel bei offenem Chat + gewähltem
Artifact): Vorschau, Quelle ansehen, kopieren, herunterladen, teilen.

**Vorschau je Dateityp** im rechten Panel: Bilder/SVG/HTML/Markdown/Code/
Audio rendern direkt; **PDF** öffnet im eingebauten PDF-Viewer (blättern/
zoomen); **Office-Dateien** (docx/xlsx/pptx) zeigen eine Datei-Karte mit
„Öffnen / Herunterladen" (kein Inline-Render möglich). Im **Durchsucht**-
Tab haben Projektquellen aus Text-/Markdown-Dateien einen „Vorschau
anzeigen"-Schalter, der den Inhalt direkt in der Karte rendert.

Jeder Schreib-/Bearbeitungsvorgang erzeugt eine **Artifact-Version**
(5 MB-Limit). Artifact-Panel → Versions-Auswahl zum Vergleichen.

---

## Wiki

Seitenleiste → **Wiki**. Dein durchsuchbares, editierbares Wissens-Wiki — und
zugleich das Langzeit-Gedächtnis des Agenten: jede gespeicherte Seite wird in
MemPalace gespiegelt, sodass der Agent sie bei der Suche findet.

**Aufbau:**
- **Links** der Seitenbaum. Der obere Filter schaltet den Sichtbarkeitsbereich
  (**Alle** / **Meine** / **Team** / **Alle sichtbar**). Das **Gruppieren nach**-
  Menü ordnet die Seiten: **Manuell** (der editierbare Baum mit Unterseiten +
  Drag&Drop), **Thema**, **Projekt**, **Quelle** (Chat / Studio / …), **Erstellt
  von**, **Geändert von**. Jede Zeile zeigt ein Bereichs-Symbol, einen kleinen
  grünen Punkt (in MemPalace durchsuchbar) und ihre Tags.
- **Tags & Filter:** Seiten bekommen beim Erstellen/Aktualisieren automatisch
  Themen-Tags (KI-vorgeschlagen); du kannst eigene Tags ergänzen oder entfernen
  (deine Tags bleiben erhalten). Die Tag-Leiste oben filtert den Baum; ein Klick
  auf ein Tag filtert ebenfalls.
- **Bearbeiten im Baum:** Beim Überfahren einer Zeile erscheinen **Umbenennen**
  und **Löschen**. Im Modus *Manuell* lassen sich Seiten per **Drag&Drop** unter
  eine andere Seite ziehen (verschachteln/umordnen).
- **Zur Quelle springen:** Aus einem Chat/Studio/… erzeugte Seiten zeigen unter
  dem Titel einen Link (z. B. „Zum Chat"), der das Ursprungsobjekt öffnet.
- **Rechts** der Editor mit zwei Ansichten, umschaltbar oben rechts:
  **Ansicht** (gerendertes Markdown) ↔ **Markdown** (Roh-Editor mit
  Zeilennummern). Titel oben ist direkt editierbar.

**Aktionen:** **+ Seite** legt eine neue Seite an (Bereich richtet sich nach dem
aktiven Filter; optional als Unterseite der offenen Seite). **Speichern**
schreibt eine neue Version. **Versionen** zeigt den Verlauf — jede Bearbeitung
ist eine unveränderliche Version; **Ansehen** öffnet eine schreibgeschützte
Vorschau, **Aktivieren** macht eine alte Version wieder zur aktuellen (nur die
aktuelle Version ist editierbar und durchsuchbar). **Löschen** entfernt die
Seite; Unterseiten bleiben erhalten (rücken eine Ebene hoch).

**Bereiche (Scopes):** *Meine* Seiten sehen nur du, *Team*-Seiten dein Team,
*globale* Seiten alle. Aus Chats, Studio-Ergebnissen, geplanten Aufgaben und
Workflows automatisch erzeugte Seiten tragen eine Quell-Markierung (↩) und
werden bei einer Änderung der Quelle als neue Version fortgeschrieben statt
doppelt angelegt.

**Erzeugen & Medien (Seitenkopf):**
- **🔊** liest die Seite vor (Sprache wird automatisch erkannt).
- **Zusammenfassung** erzeugt per KI eine kompakte Zusammenfassung als
  Unterseite (optional inklusive aller Unterseiten).
- **🎧 Podcast** erzeugt eine zweistimmige Audio-Übersicht (MP3) als Unterseite.
- **📎 Medien** lädt Bild / Audio / Video hoch und fügt es in die Seite ein;
  in der Ansicht wird es als `<img>`/`<audio>`/`<video>` dargestellt.

Wie in Studio brauchen Zusammenfassung und Podcast Quellen-Inhalt und ein
konfiguriertes Hintergrund- bzw. TTS-Modell; die Erzeugung läuft synchron
(kurzer Moment Geduld).

---

## Favoriten

Beliebiges für Schnellzugriff anheften: Chats, Artifacts, Prompts, Bilder,
**einzelne Übersetzungen** (★ in der jeweiligen Verlaufs-Zeile im
Übersetzungs-Tab — öffnet beim Anklicken direkt die richtige Übersetzung im
passenden Typ-Tab). Seitenleiste → **Favoriten** listet sie. ★ in einer
Listenzeile klicken oder über das Teilen-Menü.

---

## Feedback geben (👍/👎)

Jede Antwort und jedes Ergebnis kannst du bewerten — das hilft uns, die
Qualität zu verbessern. Du findest zwei Daumen (👍/👎) bei:

- **Chat & Projekt-Chat** — in der Aktionsleiste unter jeder Antwort
  (neben Kopieren/Erneut versuchen).
- **Brainy** (der Helpdesk-Bot) — unter jeder Brainy-Antwort.
- **Workflow-Läufe** — in jeder abgeschlossenen Verlaufs-Zeile.
- **Geplante Aufgaben** — in jeder Lauf-Zeile (Einstellungen → Zeitplan, bzw.
  im Projekt-Tab).
- **Übersetzungen** — in jeder Verlaufs-Zeile.
- **Klassifizierung** — in jeder Scan-Zeile.

So funktioniert's: Daumen klicken → ein kleines Feld öffnet sich, in dem du
**optional** kurz schreiben kannst, was gut bzw. nicht gut war → **Senden**.
Danach erscheint eine kurze „Danke!"-Animation, und dein gewählter Daumen
bleibt markiert (auch nach dem Neuladen). Du kannst deine Bewertung jederzeit
ändern — die neue ersetzt die alte. Feedback ist pro Person: nur du siehst
deine eigene Markierung.

**Weiterschreiben — eine kleine Unterhaltung:** Nach der ersten Bewertung wird
aus dem Kommentarfeld ein **Verlauf**. Klick erneut auf deinen Daumen, und du
siehst die bisherige Konversation und kannst **jederzeit eine weitere Zeile**
hinzufügen (Enter zum Senden, Emoji-Knopf für 🙂🎉👍). Das Team kann hier
direkt antworten — immer nur kurze Einzeiler, keine Romane. Sobald eine neue
Team-Antwort vorliegt, erscheint ein kleiner **Punkt** am Daumen; er
verschwindet, sobald du den Verlauf öffnest.

Administratoren finden alle eingegangenen Bewertungen gebündelt unter
**Einstellungen → Feedback** (siehe unten) — dort sehen sie den ganzen Verlauf
je Eintrag und können mit einer Zeile antworten.

---

## Websuche (kuratierte Web-Quellen)

Im rechten Panel der **Websuche**-Tab. Damit kuratiert der Nutzer selbst
Web-Quellen, und der nächste Turn arbeitet strikt aus dieser Menge.

**Ablauf**:
1. Im Websuche-Tab eine Suchanfrage eingeben → Treffer erscheinen.
2. Treffer ankreuzen, um sie in den **Korb** zu legen. URLs lassen sich
   auch manuell eintippen oder per Drag&Drop ablegen.
3. Der Korb gehört zur jeweiligen **Sitzung** (er wird serverseitig in der
   Sitzung gespeichert und beim Öffnen eines Chats geladen) und bleibt über
   mehrere Suchen innerhalb der Sitzung erhalten — nur der Nutzer leert ihn.
   Einträge einzeln aktivieren/deaktivieren (überspringen, aber behalten) oder
   entfernen; Sammelaktionen vorhanden. Die Kopfzeile zeigt eine grobe
   Token-Schätzung der aktivierten Menge.
4. Die nächste Chat-Nachricht senden. Der Server ruft jede aktivierte URL
   **frisch zum Turn-Zeitpunkt** ab und gibt den Inhalt dem Modell —
   nichts wird im Verlauf eingefroren, jeder Versand ruft neu ab (eine
   Wetterseite ist morgen wieder aktuell).
5. Solange der Korb gefüllt ist, sind die drei Web-Tools für diesen Turn
   **gesperrt** — das Modell darf nicht frei weitersuchen, sondern nur die
   kuratierten Quellen nutzen. Die abgerufenen Quellen erscheinen unter der
   Antwort als „Webquellen dieser Anfrage" (jede aufklappbar).

**Escape-Hatch**: Das Kontrollkästchen in der Websuche-Kopfzeile (sticky
pro Sitzung) hebt die Sperre auf — die kuratierten Quellen werden weiter
vorab abgerufen, das Modell darf zusätzlich selbst suchen/abrufen. Das
Kästchen ist wirkungslos, solange der Korb leer ist.

(Anderer Mechanismus als die **Web-Adressen** eines Projekts, die dauerhaft
ins Projektgedächtnis eingespeist werden.)

---

## Aktivität (Tool-Aufrufe) & Hintergrundaufgaben

Der **Aktivität**-Tab im rechten Panel zeigt **alle Tool-Aufrufe dieses Chats** an
einem Ort — sowohl die synchronen (während eines normalen Turns, z.B. `web_fetch`,
`read_document`) als auch die abgekoppelten **Hintergrundaufgaben**. Chronologisch
sortiert (neueste oben), in zwei Bereichen **Laufend** / **Abgeschlossen**. Jeder
Eintrag trägt seinen Typ (Tool-Name oder „Hintergrundaufgabe") und asynchrone
Einträge zusätzlich ein **Hintergrund**-Badge.

**Vollansicht, Kopieren und Herunterladen** eines Tool-Ergebnisses passieren hier
im Panel. Im Chat selbst steht pro Tool-Aufruf nur noch eine **kompakte Zeile**
(Name + kurze Vorschau) — ein Klick darauf öffnet den passenden Eintrag im Panel.
(Reasoning/Thinking bleibt weiterhin direkt im Chat.)

### Hintergrundaufgaben

Für lange, ausgabe-intensive Recherchen kann der Assistent eine Aufgabe
**abkoppeln**, statt den Chat zu blockieren — ähnlich wie in der
Claude-Desktop-App. Das entscheidet das Modell selbst: erkennt es, dass eine
Aufgabe lange läuft und viel Ausgabe erzeugt, startet es sie im Hintergrund und
antwortet sofort mit „läuft im Hintergrund". Der Chat bleibt frei.

Die Aufgabe läuft als **derselbe Agent** (gleiche Werkzeuge) in einem eigenen
Lauf. Das **ausführende Modell** ist standardmäßig das Chat-Modell, kann aber ein
günstigeres **Fan-out-Modell** sein, wenn das Chat-Modell eines gesetzt hat
(Einstellungen → Modelle → ⚙ → Fan-out-Modell). Im Tab **Hintergrundaufgaben**
(oder über die Uhren-Pille oben rechts) siehst du:

- **Wird ausgeführt** — laufende Aufgaben mit Live-Untertitel (Status · Dauer ·
  Tokens · Tool-Verwendungen · **ausführendes Modell**) und einem rechtsbündigen
  **Stopp**-Knopf. Am Modell-Namen erkennst du, ob eine Aufgabe auf dem
  Fan-out-Modell statt dem Chat-Modell lief. In der Karte steht **sofort** die
  **Anfrage** (der Prompt, der die Aufgabe gestartet hat), und darunter die
  **Tool-Aufrufe als dieselben ausklappbaren Karten wie im Chat** (Argumente +
  Ergebnis): jeder Aufruf erscheint, **sobald er passiert**, zusammen mit dem
  streamenden Antworttext — du musst nicht bis zum Abschluss warten. Diese
  Tool-Karten **bleiben auch nach einem Reload** erhalten (sie werden
  gespeichert, nicht nur live gestreamt). Den vollständigen Lauf gibt es
  zusätzlich über **Transkript anzeigen**. An einer **gerade laufenden**
  Tool-Karte erscheint ein **✕** — damit brichst du **diesen einen Tool-Aufruf**
  ab (die Aufgabe selbst läuft weiter; das Modell bekommt für diesen Aufruf ein
  Abbruch-Ergebnis und macht weiter). Bei **`python_exec`** und
  **`execute_command`** wird der laufende Prozess dabei **wirklich beendet**
  (hartes Kill der Prozessgruppe); bei Netz-/Speicher-Tools wird nur das Warten
  abgebrochen (die laufende Abfrage selbst kann nicht hart gestoppt werden). Den
  ganzen Lauf stoppst du weiterhin mit **Stopp**.
- **Der Status-Punkt** links zeigt per Farbe den Zustand: **grün** = läuft,
  **gelb** = über 80 % der erlaubten Laufzeit, **orange** = 90–100 %,
  **rot** = Timeout/Fehler/abgebrochen, **grau** = fertig. Bei einer Gruppe
  zeigt der Punkt den ungünstigsten Zustand ihrer Teilaufgaben.
- **Fertig** — abgeschlossene/abgebrochene Aufgaben. **Löschen** sitzt oben rechts
  an der „Fertig"-Sektion und entfernt alle fertigen Aufgaben auf einmal.
- **Klick auf eine Zeile** klappt das **Transkript** auf: **Anfrage** (der Prompt,
  mit dem die Aufgabe gestartet wurde) und **Ergebnis** (der live mitlaufende bzw.
  fertige Arbeitsverlauf).

**Parallele Recherchen (Fan-out):** deckt eine Anfrage mehrere unabhängige
Themen ab (z.B. „vergleiche Anbieter A, B und C"), startet das Modell sie als
**Gruppe** parallel. Im Panel erscheinen sie dann unter einer aufklappbaren Karte
„Parallele Recherche (N Aufgaben) · X von N fertig" mit der Zusammenführungs-
Aufgabe darunter; jede Teilaufgabe behält ihre eigenen Knöpfe. Sobald **alle**
Teile fertig sind, liefert der Assistent das **kombinierte** Ergebnis in einer
einzigen Antwort. Hängt eine Teilaufgabe zu lange, wird die Gruppe nach kurzer
Zeit mit den vorhandenen Teilergebnissen ausgeliefert (die hängende als
fehlgeschlagen markiert) — sie wartet nicht ewig.

Wenn eine Aufgabe (bzw. eine ganze Gruppe) fertig ist, **liefert der Assistent das
Ergebnis von selbst** in den Chat: ist der Chat gerade ruhig, erscheint automatisch
eine neue Antwort, die das Ergebnis zusammenfasst/damit weiterarbeitet — du musst
nichts tun. (Schreibst du gerade selbst eine Nachricht, während die Aufgabe fertig
wird, fließt das Ergebnis in genau diese Antwort ein.) Das Ergebnis wird genau
einmal verwendet. **Stopp** behält das bis dahin erzeugte Teilergebnis.

Die Aufgaben einer Sitzung bleiben erhalten: nach einem Browser-Reload zeigt die
Uhren-Pille sie wieder an, und im Panel stehen die fertigen unter „Fertig", bis
du sie per **Löschen** entfernst.

---

## Einstellungen

Zahnrad, unten links. Je nach Rolle zwei Bereiche:

**Hilfe-Symbole („?")**: In allen Konfigurationsdialogen (Agent, Benutzer,
Allgemeine Einstellungen) stehen Erklärungen nicht mehr dauerhaft im Dialog,
sondern hinter einem kleinen runden **„?"** neben dem jeweiligen Feld oder
Abschnitt. Klick (oder Tastatur: Enter/Leertaste) öffnet ein Popover mit der
Erklärung; erneuter Klick, Klick daneben oder Esc schließt es wieder. So bleiben
die Dialoge aufgeräumt und in der gleichen Schrift/Größe wie der Chat.

### Agent-Einstellungen (Admin)
- **Soul** — die Persona des Agenten (Markdown)
- **Agent** — `agent.json`: tool_groups, token_config, rate_limits, team
- **Skills** — Zip installieren / Claude-Code-Plugins durchsuchen /
  pro Agent aktivieren
- **MCP** — MCP-Server verbinden
- **Tokens** (Token-Optimierung) — Pro-Tool-Overrides + Pro-Agent-Compact-Schwelle
  + **Werkzeug-Optimierung pro Anfrage** (Schalter, Standard **an**): klassifiziert
  jede Anfrage und stellt nicht benötigte Werkzeuge zurück (per `tool_search`
  weiter erreichbar) — schlankerer Prompt, bessere Treffsicherheit bei schwächeren
  Modellen. Unabhängig von der Modellwahl. Bei **lokalen Modellen mit aktiviertem
  Warmup** wird die Optimierung übersprungen (der warme KV-Prefix muss stabil
  bleiben — Werkzeuge sind Teil davon); lokale Modelle **ohne** Warmup werden wie
  Cloud-Modelle optimiert.
- **Hooks** — Event-Hooks (pre/post Tool, pre/post Turn)
- **Zeitplan** — der eigene memory_summary-Daemon des Agenten

### Allgemeine Einstellungen (Admin)
- **Server** — **Auto-Routing**, Ports, Monitore für **Sidecar**, **Web Search
  (SearXNG)** und **crawl4ai** (Status/PID/Uptime/Health/Breaker + Neustart;
  SearXNG zusätzlich mit Pro-Engine-Tabelle und „Jetzt testen"). Die
  Modell-Zuordnungen (Standardmodell, Chat-Zusammenfassung, Auto-Routing-
  Klassifikator usw.) liegen nicht mehr hier, sondern zentral unter
  **Service-Modelle** — eine „Modelle"-Karte verlinkt dorthin.
  **Auto-Routing** legt fest, wie die „✨ Smart"-Modi im Verfasser (Cloud/Lokal)
  und `Fan-out-Modell = Auto` die Absicht einer Anfrage erkennen, um das passende
  Modell zu wählen: **Schlüsselwörter** (Standard, ohne Kosten), **LLM** (ein
  Klassifizierungsmodell erkennt die Absicht — welches Modell, legt der Slot
  **„Prompt-Klassifikation (Auto-Routing)"** unter Service-Modelle fest; leer =
  günstigstes/lokales Modell) oder **Hybrid** (erst Schlüsselwörter, LLM nur bei
  Bedarf). LLM/Hybrid fallen bei Fehler oder Timeout still auf Schlüsselwörter
  zurück — eine Anfrage hängt nie daran. Diese Einstellung steuert nur die
  **Modellwahl**; die **Werkzeug-Optimierung** ist davon getrennt und wird pro
  Agent geschaltet (siehe Agent-Einstellungen → Token-Optimierung).
- **Provider** — OpenAI-kompatible Provider hinzufügen/bearbeiten/testen
- **Nodes** — verteilte Compute-Peers
- **Modelle** — Pro-Modell-Konfiguration (warmup, thinking, profile, cost). Pro
  Modell über das ⚙-Icon: u.a. **Fan-out-Modell** — teilt dieses Chat-Modell im
  Chat eine Hintergrundaufgabe per Fan-out auf, laufen die Leaf-Tasks auf dem
  hier gewählten (meist günstigeren) Modell; leer = bleiben auf diesem Modell.
  **✨ Auto** klassifiziert stattdessen die Absicht jedes Leaf-Tasks und wählt
  je Task das passende Modell (gesteuert über Server → Auto-Routing).
- **Service-Modelle** — eine zentrale Stelle für die Modellzuordnung aller
  Hintergrunddienste: Server-Standardmodell, Bildbeschreibung (Anhänge),
  Chat-Zusammenfassung, **Prompt-Klassifikation (Auto-Routing)**,
  Fan-out-Hintergrundmodell, KG-Extraktion, Text-to-Speech, Transkription sowie
  **OCR** (Engine/Provider/Modell). Jeder Slot ist ein geprüftes Dropdown; es
  gibt **keine fest verdrahteten Standardwerte** — ein nicht zugewiesener oder
  unbekannter Slot ist ein Fehler (rote Markierung) und wird auch vom **Doctor**
  gemeldet, statt still auf ein Modell zurückzufallen.
- **Agents** — Liste + anlegen
- **Teams** — Team-CRUD + ACLs
- **Kosten** — globale Kostenansicht pro Nutzer/Modell/Tag
- **Kontingente** — Pro-Nutzer Tages- + Zyklus-Limits + Durchsetzungsmodus
- **Feedback** — alle eingegangenen 👍/👎-Bewertungen (mit Kommentar,
  Kontext-Ausschnitt, **Benutzername**, Zeitpunkt), filterbar nach Bereich und
  Bewertung; einzelne Einträge löschbar. Pro Zeile springt der **↗-Button**
  direkt zum bewerteten Inhalt (Chat → Sitzung öffnen und zur Antwort scrollen;
  Workflow/Aufgabe → Lauf-Detail; Übersetzung/Klassifizierung → die jeweilige
  Ansicht). Brainy hat keinen Sprung-Ziel. Jede Zeile zeigt zudem den
  **Verlauf der Unterhaltung** mit dem Nutzer und ein **Antwortfeld** (eine
  Zeile, Enter zum Senden) — die Antwort erscheint beim Nutzer am Daumen als
  Ungelesen-Punkt.
- **GDPR** — PII-Scanner, Kategorie-Aktionen, NER-Modelle, Regel-Overrides.
  Pro Regel lässt sich eine **Mindestanzahl** unterschiedlicher Treffer setzen
  (eine Regel löst erst aus, wenn ≥N verschiedene Werte im Dokument vorkommen —
  unterdrückt Fehlalarme bei zahlenreichen Texten; Standard 1). Die Richtlinie
  für nicht-interaktive Aufrufe (**„Wenn PII erkannt wird"**) bietet
  Anonymisieren / lokales Modell / **Überspringen** (kein Aufruf, leer fortfahren)
  / Abbrechen. Reine Unternehmens-IDs (Kategorie **Unternehmens-IDs**) und
  bloße Datumsangaben/Orte ohne Personenbezug gelten nicht als personenbezogene
  Daten und lösen standardmäßig nicht aus.
- **Kontext** — LCM-Schwellen (Lossless Context Manager)
- **MemPalace** — Statistik (Drawers/Closets/Wings/Rooms/Halls/DB-Größe),
  Palace-Explorer, Chat-Sync-Klassifizierer, Wing-Regeln, **Daemons**
- **Knowledge Graph** — Extraktionseinstellungen mit getrennten Knöpfen für
  **Projekte (Standard)** und **Wiki**: je **Methode** (LLM = hochwertig, ggf.
  Cloud · Regelbasiert = kein LLM, ganz lokal via spaCy-NER, nur generische
  Prädikate) + **Profil** (normative/generic; bei Regelbasiert auf generic
  fixiert und ausgegraut). Der Projekt-Standard ist pro Projekt überschreibbar
  (Projektansicht → „Wissensgraph (KG)"). Dazu Extraktionsmodell (nur LLM),
  Max-Triples/Konfidenz/Zeichen + Closet-Konfiguration
- **Tools** — Pro-Tool aktivieren/deferren/Purpose + Prosatexte; enthält
  auch den **Brainy**-Tab (siehe unten)
- **Doctor** — Konfig-Diagnose: erkennt Modell-/Konfig-Verweise auf nicht
  existierende Provider oder deaktivierte Modelle, Provider-Lücken,
  MemPalace-Zustand (Backend, Embedding-Gerät, Drawer-Zahl), fehlschlagende
  KG-Extraktion und **deaktivierte DSGVO-/Klassifizierungs-Scanner** (Warnung,
  damit der Aus-Zustand sichtbar bleibt). „Live-Prüfungen" testet zusätzlich
  Embedding + Provider-Zugangsdaten.
- **Bibliotheken** — schreibgeschützte Übersicht der installierten Versionen
  aller externen Bibliotheken (markitdown, MLX, spaCy, MemPalace, anthropic-SDK,
  crawl4ai/playwright …), gruppiert nach Komponente. Pro Eintrag Version und
  lokales Installationsdatum. Die Werte werden über alle vier Python-Umgebungen
  ermittelt (Server-Python, MemPalace-venv, `.venv_sdk`, `.venv_crawl4ai`).
  „Aktualisiert" ist das pip-Installationsdatum, kein Live-Abgleich mit PyPI.
- **Recherche-Modus** — Disziplintexte (Verweigerung / Präzision / Zitat)

Im **Quellbaum eines Projekts** zeigt jede Datei neben dem Mining-Punkt ein
kleines **KG-Abzeichen**: grünes „KG" = Tripel extrahiert, graues „KG·" =
gemined, aber keine extrahierbaren Tripel, gelbes „KG⊘" = KG-Extraktion
übersprungen, weil DSGVO/Klassifizierung das Dokument blockieren oder
anonymisieren würde (Tooltip nennt den Grund). So ist pro Dokument sichtbar, was
in den Knowledge Graph eingeflossen ist und was nicht.

### Konto (jeder Nutzer)
Nutzermenü (Avatar oben rechts):
- **Profil** — Anzeigename, E-Mail
- **Einstellungen** — Begrüßungsname, Tätigkeitsbeschreibung,
  Kommunikationspräferenzen, Gedächtnis-Standards, Tageszusammenfassung
  an/aus + Stunde
- **Passwort** — eigenes ändern
- **Profildokument** — das automatisch gepflegte Nutzerprofil-Markdown;
  Aktualisierung oder Zurücksetzen auslösbar

---

## Brainy (Helpdesk-Bot)

Brainy ist die schwebende Sprechblase unten rechts, in jeder Ansicht
verfügbar (das Symbol ist der Buddy des Nutzers oder 🧠). Brainy ist ein
**schreibgeschützter** Assistent für genau diese Oberfläche — er erklärt
Funktionen, findet Dinge und liest Sitzungs-/Nutzerkontext, aber er ändert,
schreibt oder löscht nie etwas.

- Auf die Blase klicken öffnet einen Mini-Chat. Antworten kommen auf
  Deutsch und sind auf die aktuelle Ansicht zugeschnitten.
- Der Verlauf ist **pro Nutzer** (nicht pro Chat) und bleibt erhalten.
  Nachrichten werden zu Frage-Antwort-Paaren mit Zeitstempel gruppiert;
  bei mehr als ~10 Paaren greift adaptive Altersgruppierung (Heute /
  Gestern / Diese Woche / Diesen Monat / Monat / Jahr, einklappbar) mit
  „Ältere laden". Einzelne Paare oder ganze Gruppen lassen sich löschen.
- Admins konfigurieren Brainy unter **Einstellungen → Tools → Brainy**:
  Aktiviert-Schalter, Modell (Standard „Auto" = Server-Standardmodell),
  Tool-Runden (1–12) und den editierbaren System-Prompt.

---

## FAQ

**F: Warum ist die Modellauswahl plötzlich auf lokale Modelle beschränkt?**
A: Der GDPR-Scanner hat PII im Entwurf oder Verlauf gefunden, und für diese
Kategorie ist „Server-Block" aktiv. Entweder die PII entfernen, auf ein
lokales Modell wechseln (Daten bleiben auf dem Gerät) oder — als Admin —
die Kategorie-Aktion unter Einstellungen → GDPR von `block` auf `warn`
ändern.

**F: Warum stoppte die Antwort mit „Sidecar error…"?**
A: Der Sidecar-Subprozess (der die LLM-Schleife fährt) ist unten. Neu
starten: Einstellungen → Server → Sidecar-Monitor oder
`POST /v1/sidecar/restart`. Bei wiederholtem Fehlschlag
`~/.brain-agent/pi-sidecar.log` prüfen.

**F: Meine geplante Aufgabe steht ewig auf „läuft".**
A: Entweder Timeout erreicht (im Zeitplan erhöhen) oder sie hängt an
`ask_user_for_file` / `ask_user` (geplante Aufgaben können nicht
nachfragen). **Abbrechen** in der Zeile und die Aufgabe ohne interaktive
Tools neu schreiben.

**F: Ich sehe mein Projektgedächtnis nicht im Chat.**
A: Drei Prüfungen:
1. Ist der Chat wirklich im Projekt? Chat öffnen → Projekt-Badge sollte
   den Projektnamen zeigen.
2. Wurde das Projekt synchronisiert? Projekt öffnen → **Sync-Verlauf**.
3. Ist der Recherche-Modus richtig gesetzt? Q&A braucht ihn AN; Codegen AUS.

**F: Der Chat sagt „Kontextfenster wird voll".**
A: **Jetzt verdichten** im Warnbanner klicken oder das Verdichten-Symbol in
der Statusleiste. Der LCM (Lossless Context Manager) fasst ältere Turns
zusammen; nichts geht verloren (Originale bleiben durchsuchbar), aber die
aktive Konversation schrumpft.

**F: Was ist Auto-LCM?**
A: Eine **pro Modell** einstellbare automatische Verdichtung (General
Settings → Service-Modelle → Häkchen „Auto-LCM" je Modell; **Standard: aus**).
Ist sie für das verwendete Modell an, verdichtet Brain den Verlauf **vor
jedem Turn** automatisch (und entfaltet ihn wieder, wenn Platz frei wird), so
dass das Kontextfenster unter der Schwelle bleibt — manuelles Verdichten ist
dann deaktiviert. In der Statusleiste zeigt ein Abzeichen den Verdichtungs-
Grad (−X% · N/M Anfragen); der verdichtete Verlauf erscheint im Chat heller/
kursiv wie ein Denk-Block. Reicht selbst maximale Verdichtung nicht, fragt ein
Dialog, ob du es erneut versuchen oder einen neuen Chat (leer oder **mit
Übergabe**) starten willst.

**F: Was macht der Übergabe-Knopf im Eingabefeld?**
A: Er erstellt aus dem aktuellen Chat ein **Übergabe-Dokument** (Ziel, Stand,
Entscheidungen, offene Punkte) und öffnet einen **neuen Chat** mit zwei
angehängten Dokumenten: der kompakten Übergabe und dem **vollständigen
Verlauf** des Ursprungs-Chats. Das Modell arbeitet aus der Übergabe und öffnet
den vollen Verlauf nur bei Bedarf — du machst nahtlos dort weiter, wo du
aufgehört hast.

**F: Was bedeuten die farbigen Badges an Web-Abrufen (raw / markitdown /
crawl4ai)?**
A: Sie zeigen, **wie** `web_fetch` den Inhalt geholt hat: `raw` (keine
Konvertierung), `markitdown` (HTML→Markdown) oder `crawl4ai` (per
Headless-Browser gerendert — für JS-Seiten, die sonst leer bleiben). Rein
informativ.

**F: Übersetzung hat meine Formatierung verloren.**
A: Bei `.docx` / `.pptx` bleibt die Formatierung in-place erhalten. Bei
PDFs kommt das Ergebnis als `.docx` zurück (PDFs sind nicht direkt
editierbar). War das Quell-PDF gescannt (bildbasiert), lief zuerst OCR und
manche Layouts gehen verloren — dafür gibt es PDF-seitig keine Lösung.

**F: Wie bringe ich das Modell dazu, Quellen zu zitieren?**
A: Ein Projekt mit **Recherche-Modus AN** nutzen. Dann muss das Modell pro
Aussage mit wörtlichem Zitat aus den Projektdokumenten belegen, und der
serverseitige Validator fängt unbelegte Aussagen ab.

**F: Mein Kontingent ist rot.**
A: Tages- oder Monats-Limit überschritten. Auf die Kontingent-Pille in der
Statusleiste klicken für die Aufschlüsselung. Der Durchsetzungsmodus
bestimmt das Verhalten:
- `warn_only` — es geht weiter, nur eine sichtbare Warnung
- `force_local` — Cloud-Modelle wechseln still auf ein lokales Fallback
- `hard_block` — Chat verweigert bis zum Reset

**F: Das Modell hat das falsche Dokument gewählt.**
A: Im rechten Panel den **Referenzen**-Tab öffnen, um zu sehen, welche
Dateien es gelesen hat. Bei der falschen explizit hinweisen: *„Lies
`projects/X/ingested/<dateiname>.pdf` und antworte nur daraus."*

**F: Wie teile ich einen Chat mit meinem Team?**
A: Chat-Menü (⋯) → **Teilen** → Sichtbarkeit auf **Team** setzen. Sie sehen
ihn unter Chats mit deinem Namen als Eigentümer. Für Workflows, Projekte,
geplante Aufgaben, Artifacts — dasselbe Teilen-Menü überall.

**F: Wie erhole ich mich von „PII erkannt — Aktion wählen"?**
A: Das Modal vor dem Senden bietet:
- **Bleiben** (blockieren, selbst bearbeiten)
- **Lokal fortfahren** (an ein lokales Modell; Daten bleiben auf dem Gerät)
- **Pseudonymisiert fortfahren** (PII durch Tokens ersetzt; eine
  Admin-Decrypt-Map wird für späteres Audit gespeichert)
- **Whitelist** — einmaliges Erlauben (z. B. die eigene E-Mail-Adresse)

**F: Kann ich nachträglich prüfen, ob die GDPR-Aktion funktioniert hat?**
A: Ja — im Modal vor dem Senden (anonymisieren / lokales Modell / weiter) gibt
es die Option **„Frag mich nachher wies gelaufen ist"**. Ist sie angehakt,
erscheint nach jeder Anfrage mit Datenschutz-Aktion ein **Rückmeldungs-Dialog**
(„Hat es gepasst?"). Dort kannst du:
- **Passt so** wählen — das Ergebnis bleibt; oder
- dieselbe Anfrage **mit einer anderen Methode erneut senden** (Anonymisieren /
  Lokales Modell / Unverändert senden). Der vorherige Versuch wird dabei
  verworfen, damit er das neue Ergebnis nicht beeinflusst.

Die gewählte Methode wird für die Folge-Anfragen gemerkt. Der Dialog hat eine
Checkbox **„Frag mich weiter wies gelaufen ist"** (standardmäßig an) — hakst du
sie ab, kommt der Rückmeldungs-Dialog nicht mehr.

**F: Kann der Agent sehen, was ich hochgeladen habe?**
A: Ja — Uploads landen im Sitzungsordner. Erreichbar über `read_document`
(reiche Formate) oder `read_file` (Klartext). Der **Anhänge**-Tab im
rechten Panel zeigt alles Verfügbare.

**F: Warum ist derselbe Prompt nach dem ersten Lauf günstiger?**
A: Prompt-Cache + Warmup-KV-Prefix. Der erste Turn einer frischen Sitzung
wärmt den Cache; Folge-Turns sind schneller und günstiger, solange der
System-Prompt stabil bleibt.

---

## Rezept: ein Word-Dokument übersetzen

1. Seitenleiste → **Übersetzung** → **Dokument**-Tab.
2. `.docx` in die Ablagezone ziehen (oder klicken zum Auswählen).
3. **Von**: Auto-Erkennung lassen oder wählen. **Nach**: Zielsprache.
4. Optional: Glossar (konsistente Terminologie) und Ton wählen.
5. **Übersetzen** klicken. Der Fortschrittsbalken zeigt Chunk für Chunk.
6. Fertig → **Herunterladen** erscheint neben der Datei im Verlauf darunter.
   Originalformatierung (Überschriften, Tabellen, Fußnoten) bleibt erhalten.

**Tipp**: Bei langen Dokumenten ist das Chunking automatisch. Bei einem
mehrsprachigen Glossar (Rechtsbegriffe, Markennamen) dieses einmal über das
**Glossare**-Modal anlegen und über Dokumente hinweg wiederverwenden.

**Bei PDF**: das PDF im selben Tab ablegen. Es wird zuerst in `.docx`
konvertiert, dann übersetzt. Die Ausgabe ist `.docx`, nicht `.pdf`.

---

## Rezept: zwei Excel-Dateien vergleichen

Das ist eine Chat-Aufgabe, keine Übersetzungs-Funktion:

1. Neuen Chat öffnen. Ein Modell wählen, das gut mit Code umgeht.
2. 📎 Beide `.xlsx`-Dateien anhängen.
3. Prompt:
   > Vergleiche `datei_a.xlsx` und `datei_b.xlsx`. Beide haben eine Spalte
   > `customer_id`. Liste die Zeilen, in denen sich der Wert von `amount`
   > zwischen den Dateien bei gleicher `customer_id` unterscheidet. Gib eine
   > CSV mit den Spalten `customer_id, amount_a, amount_b, delta` aus.
4. Der Agent liest beide mit `read_document` (oder `python_exec` + pandas),
   erzeugt den Vergleich und speichert die CSV als Artifact, herunterladbar
   im **Dateien**-Tab des rechten Panels.

**Tipp**: Für einen wiederkehrenden Vergleich daraus eine geplante Aufgabe
machen — beide Dateien anhängen, Prompt wie oben, Zeitplan `0 7 * * *`,
Tool-Profil `interactive`.

---

## Rezept: tägliche E-Mail-Zusammenfassung einrichten

1. Seitenleiste → **Geplante Aufgaben** → ＋ neu.
2. Ausfüllen:
   - **Name**: `tägliche_inbox_zusammenfassung`
   - **Aufgabe**:
     > Nutze `gmail_search`, um ungelesene Nachrichten der letzten 24 h zu
     > finden. Für jeden Thread, der eine Antwort zu brauchen scheint,
     > liste Absender, Betreff und einen Ein-Satz-Grund. Überspringe
     > Newsletter und Benachrichtigungen. Gib eine Markdown-Liste aus.
   - **Zeitplan**: `0 8 * * *`
   - **Modell**: ein fähiges Modell (lokal ist gut)
   - **Tool-Profil**: `interactive` (braucht die Gmail-Tools)
3. Speichern. **Jetzt ausführen** klicken zum Testen. Lauf-Detail prüfen.
4. Passt die Ausgabe, stehen lassen — feuert täglich um 08:00.

**Tipp**: Das Ergebnis an etwas Umsetzbares schicken — die Aufgabe so
ändern, dass sie eine Zusammenfassung per `gmail_send` an dich selbst
schickt.

---

## Rezept: ein Projekt bauen, das aus einem PDF-Ordner antwortet

1. Seitenleiste → **Projekte** → ＋ neu. Benennen (z. B. `gdpr_richtlinien`).
2. Projekt öffnen. **Eingabeordner hinzufügen** → auf das Verzeichnis mit
   den PDFs zeigen. **Rekursiv** ankreuzen, falls Unterordner zählen.
3. **Jetzt synchronisieren** klicken. Auf Abschluss warten (Sync-Verlauf
   zeigt Fortschritt; große Ordner dauern Minuten).
4. (Optional) Die KG-Extraktion läuft automatisch, falls aktiviert; das
   dauert länger. Den **Knowledge Graph**-Knopf beobachten.
5. Sicherstellen, dass der **Recherche-Modus** AN ist.
6. **Neuer Chat** aus dem Projekt. Frage stellen.
7. Das Modell muss nun pro Aussage zitieren; in der Antwort erscheinen
   `[Quelle: … — "…"]`-Klammern. Zum Prüfen auf eine klicken.

**Tipp**: Verweigert das Modell mit „kein relevantes Gedächtnis", die
Anfrage breiter fassen oder prüfen, ob der Sync die Dokumente wirklich
eingespeist hat (Projekt → **Dateien**-Tab listet sie).

**Tipp**: Nach Hinzufügen/Entfernen von Dateien neu synchronisieren.
**Vollständig neu synchronisieren** nur, wenn sich Inhalte drastisch ändern
— es ist teuer.

---

## Best Practices

**Für Chat:**
- In-Gedächtnis-speichern auf **auto** für allgemeine Chats — der
  Klassifizierer behält Nützliches, verwirft Smalltalk.
- Für signalstarke Projekte (Forschung, Entscheidungen) auf **an** und pro
  Turn über das 🌐-Menü prüfen.
- **Verfeinern** (✨) bei unsauberem Entwurf — Polish poliert ohne Sinnänderung; Engineer (Schalter daneben) strukturiert um und ergänzt fehlende Klarheit/Schutzregeln, ohne Inhalte zu erfinden.
- Caveman-Modus für „gib mir eine Zeile, sonst nichts."

**Für Projekte:**
- Recherche-Modus AN ist der richtige Standard für Q&A; AUS für Codegen.
- `instructions` kurz halten — geht jeden Turn in den System-Prompt.
- Dem Zitat-Validator vertrauen — unbelegte Aussagen in einem
  Recherche-Modus-Projekt werden serverseitig abgefangen.

**Für geplante Aufgaben:**
- Konkret zum Ausgabeformat sein („gib eine Markdown-Tabelle aus",
  „speichere nach `bericht.md`"). Vage Aufgaben → vage Ausgabe.
- Mit Tool-Profil leer (research-minimal) starten. Nur bei echtem Bedarf
  auf `interactive` hochstufen.
- Mit **Jetzt ausführen** testen, bevor Cron läuft.
- Ein Modell festlegen — nicht auf „Standard" lassen, wenn Kosten zählen.

**Für Anhänge:**
- Bei Binärdateien (PDF, docx, xlsx, …) nutzt das Modell `read_document`
  über markitdown / Mistral OCR. Qualität schwankt; kommt eine Tabelle
  verstümmelt, ein erneutes Lesen mit expliziter Paginierung verlangen.
- Bildbasierte PDFs brauchen OCR (langsam). Wenn möglich vorab in
  Text-PDFs umwandeln.

**Für Gedächtnis:**
- Projektgedächtnis (bei Recherche-Modus an) ist das hochwertigste Signal.
  Projekte nutzen, nicht über lange Chats „beibringen".
- Das **Nutzerprofil** wird automatisch aus der Aktivität gepflegt — bei
  Fehlern `agents/main/user_profiles/<uid>.md` direkt editieren oder in
  Profildokument „zurücksetzen".

**Für Datenschutz:**
- Lokale Modelle = Daten verlassen den Host nie. Die Statusleiste zeigt ein
  Badge lokal vs. Cloud.
- Der GDPR-Scanner läuft vor jedem Cloud-Versand. Ihm vertrauen; die
  Kategorie-Aktionen anpassen, falls zu aggressiv.

---

## Tipps & Tricks

- **`/`-Befehle**: `/` im Eingabefeld öffnet das Slash-Menü — Agent-Befehle,
  Suche, letzte Prompts.
- **`@`-Erwähnungen**: in einem Team-Chat benachrichtigt `@nutzername`.
- **Drag&Drop**: im Willkommens-Eingabefeld, im Chat-Eingabefeld und im
  Projekt-Upload-Bereich.
- **Mehrfachauswahl**: `Cmd/Strg` halten und mehrere Chats anklicken, um
  sie gesammelt zu archivieren / löschen / einem Projekt zuzuweisen.
- **Kontextleiste** füllt sich mit der Konversation. Bei 60 % erscheint die
  LCM-Warnung; bei 80 % droht Abschneiden. Früh verdichten.
- **Kostenvorschau**: über das Modell-Badge im Eingabefeld schweben — zeigt
  Kosten pro 1K Tokens. Nützlich vor langem Kontext an ein teures Modell.
- **Geschwindigkeits-Badge**: Tokens/Sek. — als **Gesamtdurchsatz** gerechnet
  (Eingabe- + Ausgabe-Tokens geteilt durch die Dauer), nicht nur die generierten
  Tokens; das Verarbeiten eines großen Prompts (Prefill) zählt mit. Ist ein
  lokales Modell langsam, den Warmup-Status prüfen — der erste Turn nach
  Kaltstart ist langsamer.
- **Wieder anhängen**: Browser-Tab mitten im Stream schließen bricht nicht
  ab. Chat wieder öffnen, der Live-Stream wird fortgesetzt.
- **Übersetzungs-Glossare** gelten über alle vier Übersetzungs-Tabs.
- **Geplante Aufgabe + Workflow kombinieren**: den Workflow planen, nicht
  einzelne Schritte. Der Workflow handhabt Freigabe-Gates.
- **Inspizieren** (🔍 in der Statusleiste): wenn etwas seltsam aussieht,
  zeigt das Inspect-Modal Modell, System-Prompt-Größe, Nachrichtenzahl,
  Token-Budget — der schnellste Weg, eine Fehlkonfiguration zu finden.
- **Brainy fragen**: für „wo finde ich…"/„wie geht…"-Fragen die schwebende
  Sprechblase nutzen — sie kennt diese Oberfläche.

---

## Wann was nutzen

| Ziel | Nutze |
|---|---|
| Schnelle Frage, kein Gedächtnis nötig | Neuer Chat, beliebiges Modell |
| Q&A aus einem Dokumentkorpus | Projekt + Recherche-Modus AN + Dokumente einspeisen |
| Code bauen, Text mit Kontext entwerfen | Projekt + Recherche-Modus AUS |
| Einmalige Datei-Konvertierung/-Extraktion | Chat mit Anhang |
| Antworten aus kuratierten Web-Quellen | Websuche-Tab → URLs markieren → senden |
| Ein Dokument übersetzen | Übersetzung → Dokument-Tab |
| Audio transkribieren + übersetzen | Übersetzung → Audio/Video |
| Wiederkehrende Aufgabe („jeden Tag…") | Geplante Aufgaben |
| Wiederkehrende Aufgabe mit Freigabe-Gates | Workflow + geplante Aufgabe |
| Chat-übergreifende Suche | Seitenleiste Suche |
| Etwas für Schnellzugriff anheften | Favorit (★) |
| Arbeit mit Team teilen | Teilen-Menü → Team |
| Datenschutzsensibler Inhalt | Lokales Modell + GDPR-Scanner AN |
| „Wie funktioniert X hier?" beantwortet bekommen | Brainy (Sprechblase) |
