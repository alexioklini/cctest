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
- **Chats** — alle Chats, sortiert nach der **letzten Nachricht** (zuletzt
  beschriebener zuerst); ein Chat bloß zu ÖFFNEN ändert die Reihenfolge nicht.
  Archivieren/markieren/umbenennen über das Zeilenmenü. Chats, in denen gerade
  eine Antwort erstellt wird, tragen eine grüne **„läuft"-Markierung** — auch in
  den Projekt-Chatlisten — so ist sofort sichtbar, wo eine Antwort im Entstehen
  ist, ohne den Chat zu öffnen.
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
- **Versionsnummer** (oben neben „Brain Agent“) — anklickbar: öffnet die
  **Versionshistorie** „Was ist neu in Brain Agent“. Ein Modal mit der
  kuratierten Liste aller Releases (neueste zuerst, aktuelle Version
  vorausgewählt): links die Versionen, rechts pro Eintrag was die Version
  bringt — aus Nutzersicht beschrieben, mit „Für alle“/„Für Admins“-Markierung.

**„Zuletzt verwendet"** (unten in der Seitenleiste) — passt sich der offenen
Ansicht an: In **Chats** zeigt sie die letzten Chats, in **Projekten** die
Projekt-Chats, in **Favoriten** alle Favoriten, in **Geplant** die letzten
Ausführungen, in **Workflows** die letzten Läufe, in **Übersetzung** den
Übersetzungs-Verlauf, in **Daten** die letzten Klassifizierungs-Scans und im
**Wiki** die zuletzt geänderten Seiten. Der **Filter-Knopf** daneben öffnet je
nach Ansicht das passende Menü — Chats bieten Typ/Status/Letzte Aktivität,
Läufe (Geplant/Workflows) bieten einen Status-Filter, die übrigen Ansichten
bieten „Letzte Aktivität"; überall lässt sich zusätzlich **nach Datum
gruppieren**. Ein Klick auf einen Eintrag öffnet ihn direkt.

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
- **Datenquellen** (v9.372.0) — externe Datenbanken/APIs für diesen Chat
  freischalten (siehe unten); erscheint nur, wenn Quellen konfiguriert und
  für Sie freigegeben sind
- **Aktivität** — alle Tool-Aufrufe dieses Chats (synchrone + Hintergrund­aufgaben)
  sowie Karten für **eingefügte Klarstellungen** und **Goal-Modus-Aktivität**
  (geplante/laufende Ziel-Prüfungen, zusätzliche Iterationen), chronologisch
  (neueste oben) in „Laufend" / „Abgeschlossen" (siehe unten)
- **Zwischenfragen (btw)** — Nebenfragen an den Assistenten mit eigenem
  Eingabefeld und Frage-Antwort-Verlauf; stört die laufende Antwort nicht
  (siehe unten)

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
pro Runde sofort verbucht, gehen also nicht verloren). Läuft das Modell über
eine **Flatrate** (Coding-Plan, siehe unten), zeigt das $-Feld beide Werte:
`0.000 (API 0.412)` — verrechnet plus, in Klammern, was die Sitzung zum
API-Listenpreis gekostet hätte (Tooltip nennt die Ersparnis). Dasselbe gilt
für die Statuszeile des Terminal-Chats in Code-Projekten.

Ein Klick auf **Plan-Nutzung** öffnet ein Fenster mit den Kontingent-Balken
(täglich + Abrechnungszeitraum) und darunter der **Kostenaufschlüsselung**: oben
vier Kennzahlen — **Verrechnete Kosten** (tatsächlich abgebucht; Flatrate-Modelle
buchen 0 $), **API-Kosten (Listenpreis)** (dieselbe Nutzung zu Listenpreisen),
**Flatrate-Ersparnis** (die Differenz) und **Caching-Ersparnis** (was die
⚡-gecachten Tokens ohne Prompt-Cache zusätzlich gekostet hätten) — plus die
Token-Summen (ein · aus · ⚡ gecached). Darunter eine **Tabelle pro
Anwendungsfall** (Chat, Chat-Zusammenfassung, Übersetzung, Geplante Aufgaben,
Studio, Deep Research, Audio Overview/Podcast, Vorlesen, …) mit festen Spalten
in überall gleicher Reihenfolge: Aufrufe · Token ein · Token aus · ⚡ Gecached ·
**API-Kosten** · **Verrechnet** · **⚡-Ersparnis**. Jede Zeile lässt sich
aufklappen und zeigt die **Aufteilung nach Modell** in denselben Spalten.
Dieselben Kennzahlen in derselben Reihenfolge erscheinen auch im
**Sitzungs-Inspektor** (Lupe in der Statusleiste): oben als Kacheln für die
ganze Sitzung, und in jeder Anfrage-Kopfzeile pro Turn („API $… ·
verrechnet $… · ⚡ −$…").

Zwischen Kontingenten und Kostentabelle sitzt die Sektion **„Coding-Pläne &
API-Guthaben (geschätzt)"** (9.283.0): eine Zeile pro Abrechnungskonto, das
mit mindestens einem Modell verknüpft ist — mit Name, Abo-Preis und pro
Zeitfenster (5h / Woche / Monat) einem Balken mit geschätzter Auslastung
— die **Farbe ist eine Hochrechnung**: grün = bei gleichbleibendem Nutzungs-Tempo bleibt das Fenster im Rahmen, gelb = das Tempo müsste sinken (Projektion über 100 % bzw. Füllstand ab 70 %), rot = deutlicher Überlauf-Kurs oder fast leer; der Tooltip nennt den projizierten Stand am Fensterende, bei Guthaben-Konten die Reichweite in Tagen — samt **Countdown bis zum Reset** (↻ „in 39 min" / „in 7
Tagen"; das 5h-Fenster ist ein echtes Session-Fenster ab der ersten Anfrage).
**Flat-Pläne** (Z.ai/Kimi Coding Plan, Mistral Vibe) zeigen
Token-Fenster; **Credit-Konten** (API-Guthaben, z. B. Kilo) zeigen verbraucht/
verfügbar in $ seit der letzten Aufladung. Die Werte sind aus dem eigenen
Nutzungs-Ledger **geschätzt** (die Anbieter haben keine Quota-API) — Admins
kalibrieren per %-Feld gegen das echte Anbieter-Dashboard (das Limit wird
daraus neu berechnet) bzw. setzen bei Credit-Konten nach einer Aufladung das
neue Guthaben. **„+ Plan"** legt ein Konto an (Typ Flat oder Credit, Preis,
Fenster-Limits bzw. Guthaben); ✎/× bearbeiten/löschen.

Die **Verknüpfung Modell ↔ Konto** hat seit 9.313.0 **zwei Ebenen**, weil die
Plan-Zugehörigkeit meist eine Eigenschaft des *Kontos* ist, nicht des einzelnen
Modells:
- **Einstellungen → Provider → Einstellungen → „Coding-Plan / Konto (Vorgabe)"**
  gilt für **alle** Modelle dieses Anbieters. Das ist der Normalfall — Mistral
  hängt so mit einer einzigen Zeile an seinem Abo statt mit 63 Einzeleinträgen.
- **Einstellungen → Modelle → „Coding-Plan / Konto"** sticht die Vorgabe für ein
  einzelnes Modell. „Vorgabe des Providers" = erben (Standard); „— kein Plan
  (Vorgabe ignorieren) —" nimmt ein Modell bewusst aus dem Konto heraus.

Nur Konten mit mindestens einem so verknüpften Modell erscheinen im Dashboard.
Über das Auswahlmenü oben rechts wählt man den **Zeitraum** (Heute, Diese Woche,
letzte 7/30/180/365 Tage, seit Jahresbeginn, aktueller/letzter Abrechnungszeitraum,
Gesamt). Hinweis: lokale Modelle sind kostenlos und erscheinen mit 0 $ (aber echten
Tokens); Aufrufe von vor der Einführung dieser Funktion erscheinen als
*Unbekannt (Altdaten)*.

**Preistabelle (Einstellungen → Kosten, nur Admins, 9.313.0).** Unter der
Kostenübersicht liegen zwei Sektionen:
- **Preistabelle** — hier pflegt man Modellpreise in **$ pro 1 Mio. Token**
  (ein · aus · optional *cached*; leer ⇒ automatisch 0,1× vom Eingangspreis).
  Der Schlüssel darf eine exakte Modell-ID **oder ein Präfix** sein
  (`claude-opus` trifft `claude-opus-4-6-…`); bei mehreren Treffern gewinnt der
  längste. Sie greift für Modelle, die im Modelle-Grid **keinen** eigenen Preis
  gesetzt haben. Änderungen wirken sofort, ohne Neustart.
- **„Ohne hinterlegten Preis"** — alle Cloud-Modelle, für die *nirgends* ein
  Preis existiert. Diese Aufrufe werden mit **0 $ verbucht** und fehlen dadurch
  in Statistik *und* Kontingent, ohne dass es auffällt. Ein Klick auf ein Modell
  übernimmt es oben in die Tabelle. Aktive Modelle sind mit ● hervorgehoben.
  Lokale Modelle stehen bewusst nicht in dieser Liste (0 $ ist dort korrekt),
  ebenso OCR/TTS/Transkription, die pro Seite/Zeichen/Minute abgerechnet werden.

Die vollständige Reihenfolge, in der ein Preis gesucht wird: **Modelle-Grid →
Preistabelle → eingebaute Standardpreise → 0 $**.

Am **rechten Rand der Statusleiste** liegen Aktionsknöpfe für den aktuellen
Chat:
- **Zusammenfassung** — erstellt per KI (über das konfigurierte
  Zusammenfassungs-Modell) eine strukturierte Zusammenfassung des Chats als
  Markdown-Datei und legt sie im Artefakte-Ordner der Sitzung ab.
- **Verlauf exportieren** — speichert den **vollständigen Chat-Verlauf
  wortgetreu** als Markdown-Datei im Artefakte-Ordner (ohne KI).
- **Bundle herunterladen** — erzeugt ein **komplettes ZIP-Paket** der Sitzung
  und lädt es herunter (es wird *nicht* als Artefakt gespeichert). Während des
  Packens zeigt ein Fenster den Fortschritt. Das ZIP enthält alles, was auch
  das rechte Panel zeigt: den Verlauf (`conversation.md`), die Tool-Aufrufe mit
  Ein-/Ausgabe (`tool-calls.md`), Referenzen/Webquellen, eine Statistik
  (Anfragen, Tokens, Kosten, Modelle, Tool-Nutzung), die Roh-Audit-Daten
  (`inspect.json`, `messages.json`), den Ordner `attachments/` (hochgeladene
  Dateien) und den Ordner `artifacts/` (erzeugte Dateien).
- **Verdichten** (Schere) — Kontext verdichten (LCM, siehe FAQ).
- **Untersuchen** (Lupe) — Sitzungs-Inspektor öffnen.

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
- 🔬 **Deep Research** (Mikroskop-Symbol) — wenn aktiviert, führt der **nächste
  Turn eine tiefe Web-Recherche** aus (Unterfragen → Suche → Quellen lesen →
  belegter Bericht) statt einer normalen Chat-Antwort; ist er aus, läuft alles
  wie ein gewöhnlicher Chat. Funktioniert in **jedem** Chat (nicht nur Projekten)
  und ist **unabhängig** von den anderen Schaltern. Das Ergebnis erscheint als
  hochwertiger HTML-Bericht im **Artefakte-Panel** (öffnet sich automatisch) plus
  als Markdown-Quelle; die Chat-Antwort ist eine kurze Karte. Der Schalter ist
  **ausgegraut**, solange kein Suchanbieter aktiv ist (Einstellungen → Tools).
  *(Nicht zu verwechseln mit dem Recherche-Modus-Override darüber — der steuert
  nur die Zitier-/Refuse-Disziplin in Projekt-Chats, startet aber keine Recherche.)*
- ✨ Verfeinern — den Entwurf vor dem Senden überarbeiten
- 🔧 Verfeinerungs-Modus (Polish ↔ Engineer) — der Schalter direkt neben ✨. **Polish** (Standard) säubert nur Grammatik/Klarheit und lässt die Absicht unverändert. **Engineer** (akzentfarben, wenn aktiv) strukturiert den Prompt um: präzisiert die Aufgabe, nutzt das aktive Modell + verfügbare Tools + Projekt-Anweisungen als Kontext, fragt bei hoffnungslos vagen Entwürfen nach (statt Details zu erfinden) und lässt bereits gute Entwürfe unangetastet. Pro Oberfläche gemerkt (Chat, geplante Aufgaben, Soul-Editor)
- 🛡️ GDPR-Details (PII-Funde inline aus-/einblenden)
- 🛡️ Datenschutz-Übersicht (Schild-Symbol, erscheint nur wenn der Chat personenbezogene Daten oder frühere Datenschutz-Entscheidungen enthält) — **Klick** öffnet die große Datenschutz-Übersicht (siehe FAQ unten); kurzes **Überfahren** zeigt eine schnelle Vorschau
- 💾 In-Gedächtnis-speichern-Zyklus (aus / an / auto)
- 🪨 Caveman-Modus (knappe Antworten)

**Standard vs. gemerkter Zustand:** Ein **neuer Chat** startet immer auf
**Standardwerten** — das Standard-/Agenten-Modell (nie das zuletzt benutzte)
sowie **Denk-Stufe, Caveman-Modus und Gedächtnis-Modus** auf der konfigurierten
Voreinstellung (nie vom letzten Chat übernommen). Beim **Wiederöffnen eines
bestehenden Chats** wird dagegen der **letzte Zustand dieses Chats**
wiederhergestellt: das im letzten Zug verwendete Modell (war es „✨ Smart/Auto",
bleibt es Auto) sowie die gespeicherte **Denk-Stufe, Caveman- und Gedächtnis-
Stufe** dieses Chats. Die drei Standardwerte legst du **pro Konto** in
**Benutzereinstellungen → Memory → „Eingabefeld-Standards für neue Chats"**
fest (Denk-Stufe, Caveman; der Gedächtnis-Standard steht direkt darüber unter
„Standard für neue Chats"). „Server-Standard verwenden" erbt jeweils die
globale Admin-Vorgabe (Einstellungen → Allgemein → Server → „Eingabefeld-
Standards (global)"). Auflösung: dein Konto-Wert → globaler Wert → aus. Ein neuer Chat ist außerdem immer **allgemein (ohne Projekt)** —
projektgebunden nur, wenn er **innerhalb eines Projekts** gestartet wird. Eine
**Übergabe** erbt den Modus des Ursprungs-Chats (projektgebunden bleibt im
Projekt, allgemein bleibt allgemein).

Die **Denk-Stufe** ist pro Chat: sie wird beim Wiederöffnen wiederhergestellt
und gilt auch auf „✨ Smart/Auto" — dort wird die gewählte Stufe „best effort"
auf das vom Auto-Router gewählte Modell angewendet (Modelle ohne Reasoning
ignorieren sie, Modelle mit nur An/Aus klemmen auf „Hoch").

**Brainy** — die schwebende Sprechblase unten rechts (in jeder Ansicht).
Brainy ist ein **schreibgeschützter** Helpdesk-Assistent: er kennt diese
Oberfläche, kann Sitzungs-/Nutzerkontext lesen und Fragen beantworten,
ändert aber nie etwas. Siehe „Brainy" weiter unten.

---

## Eine Nachricht senden

Tippen, `Enter` drücken (Shift+Enter für Zeilenumbruch) oder den
Senden-Knopf klicken. Während das Modell antwortet, wird der Senden-Knopf
zum Stopp-Knopf.

**Projekt-Chip**: Links in der Eingabeleiste zeigt ein kleiner Chip, ob die
nächste Nachricht **projektgebunden** läuft (farbig, Projektname — z. B.
„sql-und-showcase") oder als **„Allgemeiner Chat"** (grau, ohne
Projektkontext). So sehen Sie vor dem Senden, ob Wissensbasis, Instruktionen
und Arbeitsverzeichnis des Projekts gelten. Landet eine Projekt-Aufgabe
versehentlich im allgemeinen Chat (z. B. nach „Neuer Chat" außerhalb der
Projektansicht), macht der Chip das sofort sichtbar — für einen Projekt-Chat
starten Sie den Chat aus der Projektansicht heraus.

**Eine laufende Antwort steuern**: Sie müssen eine Antwort nicht abwarten oder
abbrechen, um weiterzuarbeiten. Während gestreamt wird, stehen vier Werkzeuge
bereit (im normalen Chat über Knöpfe in der Eingabeleiste, im Terminal-Chat über
Slash-Befehle und Tastenkürzel):

- **Warteschlange** — Tippen Sie einfach weiter und drücken Sie `Enter`: die
  Nachricht wird *eingereiht* (nicht als zweite Antwort gestartet) und
  automatisch als normaler Turn gesendet, sobald die laufende Antwort fertig
  ist. Unter der Eingabe erscheint die Warteschlange; dort können Sie Einträge
  **bearbeiten, entfernen, umsortieren (↑/↓), sofort senden oder alle leeren**.
  Die Warteschlange gehört zur Sitzung und übersteht ein Neuladen. Im
  Terminal-Chat: `/queue` (bzw. `/queue rm N`, `/queue mv N M`, `/queue edit N …`,
  `/queue clear`).
- **Pause / Fortsetzen** — Der **Pause-Knopf** (bzw. `/pause`, Tastenkürzel
  `Strg-Z`) hält die Antwort am nächsten Schritt an; die aktuelle Runde und ein
  gerade laufendes Werkzeug werden noch fertig, es geht nichts verloren.
  Fortsetzen über denselben Knopf (bzw. `/resume`, `Strg-Q`).
- **btw-Zwischenfrage** — Nebenfragen haben ihren eigenen Tab
  **„Zwischenfragen“ im rechten Panel** (Sprechblasen-Symbol; im
  Terminal-Chat: `/btw <Frage>`): ein kleiner Frage-Antwort-Bereich mit
  eigenem Eingabefeld, in dem die Nebenfrage beantwortet wird, ohne die
  laufende Antwort zu stören und ohne den Chat-Verlauf zu verändern. Er ist
  jederzeit verfügbar. Der btw-Assistent weiß, *was der Assistent gerade
  tut* — Sie können also „Was machst du gerade?“ oder „Wie lange dauert das
  noch?“ fragen und eine sachliche Auskunft zum aktuellen Schritt und zur
  bisher verstrichenen Zeit bekommen (eine Angabe zur Restdauer ist
  ausdrücklich eine grobe Schätzung).
- **Klarstellung einfügen** — Schieben Sie mitten in die laufende Antwort einen
  Hinweis nach, den das Modell im nächsten Schritt berücksichtigt (im
  Terminal-Chat `/inject <Text>`, Alias `/clarify`). Anders als die
  Warteschlange startet dies keinen neuen Turn, sondern ergänzt den laufenden.
  Den Status sehen Sie als **Karte im Aktivität-Tab** des rechten Panels —
  erst „Wartet auf das nächste Rundenende“, dann „In Runde N übernommen“
  (wie ein Tool-Aufruf).

Im Terminal-Chat bricht `Esc` (oder `/cancel`) die Antwort weiterhin ab.

**🎯 Goal-Modus (Ziel setzen)**: Geben Sie dem Chat ein *Ziel*, und der
Assistent arbeitet nach jeder Antwort selbstständig weiter, bis es erreicht
ist. Klicken Sie auf den **Zielscheiben-Knopf** in der Eingabeleiste, tragen
Sie das Ziel ein (z. B. „Der Bericht enthält alle fünf Abschnitte und jede
Zahl ist belegt.“) und optional eine Obergrenze an Durchläufen, dann
**Speichern**. Ab jetzt prüft nach jeder Antwort ein Prüf-Modell, ob das Ziel
erfüllt ist:

- **Nicht erfüllt** → der Assistent erhält automatisch eine konkrete
  Anweisung, was noch fehlt (im Verlauf sichtbar als gedämpfte Notiz
  „🎯 Automatische Fortsetzung (Iteration n)“), und arbeitet weiter — bis zum
  Ziel oder bis zur Obergrenze. Die Statusleiste zeigt dabei
  „Ziel: Iteration 2/5“. Zusätzlich zeigt der **Aktivität-Tab** im rechten
  Panel die Goal-Aktivität als Karten: die **geplante Ziel-Prüfung** (läuft
  nach Abschluss der Antwort), die **laufende Prüfung**, ihr **Ergebnis**
  und jede **zusätzliche Iteration** samt Judge-Anweisung. Die
  Ergebnis-Karte enthält dabei die **Begründung des Prüf-Modells** (warum
  das Ziel (noch) nicht erreicht ist) und — bei einer Fortsetzung — die
  **konkrete Anweisung an den Assistenten für den nächsten Durchlauf**.
- **Erfüllt** → der Durchlauf endet, der Knopf wird grün („Ziel erreicht“),
  und weitere Nachrichten werden nicht mehr geprüft, bis Sie ein neues Ziel
  setzen oder das alte löschen.
- **Unerreichbar / Obergrenze** → der Knopf wird rot; passen Sie das Ziel an
  (erneutes Speichern aktiviert es wieder) oder löschen Sie es. Eine
  berechtigte Ablehnung (z. B. fehlende Daten) wird respektiert und NICHT in
  Wiederholungen gezwungen.

Solange ein Ziel **aktiv** ist, gilt es für *jede* gesendete Nachricht der
Sitzung (der Knopf ist eingefärbt; in der Chat-Liste links erscheint eine
🎯-Markierung). Im **Terminal-Chat** heißt der Befehl `/goal <Text>`
(`/goal status` zeigt das Ziel, `/goal off` löscht es). Auch **geplante
Aufgaben** können ein Ziel bekommen — Feld „🎯 Ziel“ im Aufgaben-Editor; das
Ergebnis vermerkt dann z. B. „Ziel: erreicht nach 2 Iterationen“. Admins
wählen unter Einstellungen → Allgemein → **Service-Modelle** das Prüf-Modell
(„Goal-Modus (Ziel-Prüfung)“) und unter **Eingabefeld-Standards** die
Standard-Obergrenze — dort lässt sich der Goal-Modus auch komplett
abschalten. Hinweis: Jede Iteration ist ein vollwertiger Durchlauf und
kostet entsprechend Tokens; die Prüfungen erscheinen in der
Kosten-Übersicht als eigener Posten.

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

**🧬 Experten-Gremium** (MoA / Mixture of Agents, v9.268.0; umbenannt +
Beitrags-Modi 9.271.0): erscheint in der Liste, wenn ein Admin das Gremium
aktiviert und die Matrix gepflegt hat (Einstellungen → Server →
„Experten-Gremium (MoA)"). Verhält sich wie Smart (Cloud) — plus: bei
geeigneten Aufgabentypen arbeiten mehrere Experten-Modelle **parallel und
ohne Tools** der Antwort zu; das automatisch gewählte Smart-Modell führt die
Beiträge zusammen und formuliert die finale Antwort mit vollem
Werkzeugzugriff. Je Aufgabentyp liefern die Experten entweder eine
**Antwort** (vollständiger Entwurf — stark bei Wissens-/Urteilsfragen; das
Smart-Modell prüft und synthetisiert) oder einen **Ansatz** (nur die
Herangehensweise: Schritte, Quellen, Fallstricke — stark bei Recherche/
Orchestrierung; das Smart-Modell wählt die beste Kombination und führt sie
mit seinen Tools aus). Dritte Option je Aufgabentyp: **Plan-Delegation**
(9.284.0) — der Orchestrator (z. B. das stärkste Modell) fasst die
Experten-Ansätze in EINEN Ausführungsplan zusammen (eigene 🧬-Karte
„Plan-Orchestrator", aufklappbar), und ein **günstigeres Modell**, das
anhand des Plans automatisch gewählt und für den Chat gemerkt wird, führt
den Plan mit den Tools aus — das teure Modell denkt einmal, das günstige
erledigt die Werkzeug-Arbeit. **Plan-Review** (9.285.0): Im Chat und im
Terminal-Chat pausiert die Delegation vor der Ausführung mit einer
Prüf-Karte — Sie können den Plan direkt im Textfeld bearbeiten, dem
Orchestrator eine Rückfrage bzw. einen Änderungswunsch geben („Neu planen
lassen" — er legt einen überarbeiteten Plan zur erneuten Prüfung vor, bis
zu 5 Runden), das **Ausführungs-Modell** per Dropdown wechseln und den Plan
mit „Plan freigeben & ausführen" starten. Im Modell-Dropdown steht das
automatisch empfohlene (geeignetste) Modell oben; Modelle, die laut
Fähigkeits-Benchmark für die Art dieses Plans wenig geeignet sind, sind
ausgegraut und mit „— wenig geeignet" markiert — Sie können sie trotzdem
wählen, sehen dann aber eine Warnung, dass das Ergebnis schwächer ausfallen
kann (9.286.2). Reagieren Sie nicht, wird der
aktuelle Stand nach 15 Minuten automatisch freigegeben; Abbrechen des
Turns bricht auch den Review ab. Jeder geprüfte Planstand (Entwurf,
Überarbeitungen, Ihre freigegebene Fassung) landet zusätzlich als
versioniertes Artefakt „ausfuehrungsplan.md" in der Sitzung — der
Versionsverlauf ist im Artefakte-Panel einsehbar (9.285.1). Geplante Aufgaben und andere automatische
Abläufe halten nicht an — dort beurteilt der Orchestrator selbst, ob sein
Plan trägt (hält er ihn für unzureichend, behält das antwortende Modell
die Arbeit ohne Delegation). **Nachbesserung der Experten-Vorschläge**
(9.286.0): Findet der Orchestrator die Ansätze für einen tragfähigen Plan zu
schwach, benennt er die betroffenen Experten und lässt GENAU diese einmal
gezielt nachbessern — mit einer konkreten Begründung, was gefehlt hat — und
plant anschließend neu (die nachgebesserten Beiträge erscheinen als 🧬-Karte
„· Nachbesserung"). Wichtig: Ihre Plan-Rückfragen im Review gehen immer an
den Orchestrator, nie an die Experten — die haben ihre Arbeit mit ihrem
Vorschlag erledigt. **Ergebnis-Prüfung nach der Ausführung** (9.286.0, nur
im interaktiven Chat): Nachdem das ausführende Modell geantwortet hat,
beurteilt der Orchestrator, ob die Antwort den Plan und Ihre Anfrage wirklich
erfüllt (Vollständigkeit, Korrektheit, keine Behauptungen ohne Beleg — ein
sauberes „nicht gefunden" gilt als richtig). Fehlt etwas, lässt er das Modell
gezielt nachbessern und wiederholt das, bis das Ergebnis passt oder die
eingestellte Obergrenze erreicht ist (Standard: bis zu 2 Nachbesserungen; in
den Server-Einstellungen wählbar, 0 = nur protokollieren). Die Prüfung
erscheint als 🧬-Karte „Ergebnis-Prüfung" („Ergebnis bestätigt" bzw.
„Nachbesserung angefordert", aufklappbar mit der Begründung des Orchestrators
bzw. der konkreten Nachbesserungs-Anweisung). **Nachvollziehbarkeit**
(9.286.1): Jede Delegations-Entscheidung hinterlässt eine eigene 🧬-Karte im
Chat-Verlauf UND im Aktivität-Tab des rechten Panels — die Plan-Review-Karte
„Plan-Review" zeigt das Ergebnis Ihrer Prüfung („Plan freigegeben", „Neu
planen lassen" mit Ihrem Feedback zum Aufklappen, „Executor gewechselt",
„Plan bearbeitet", „Abgebrochen" oder eine Auto-Freigabe bei Zeitablauf), und
die Ergebnis-Prüfung wie oben. So bleibt im Verlauf sichtbar, was geprüft,
entschieden und nachgebessert wurde. Jeder Beitrag erscheint live als eigene 🧬-Karte im
Chat (Modell, Größe, Dauer, ggf. „· Ansatz"; Fehler einzelner Experten
brechen die Anfrage nie ab) — **ein Klick auf die Karte klappt den
vollständigen Beitragstext auf** (seit 9.270.0; enthielt die Anfrage
anonymisierte Daten, zeigt der Beitrag die Ersatzwerte). Dieselben Karten
stehen auch im **Aktivität-Tab des rechten Panels**, ebenfalls mit Text. Bei Aufgaben, wo mehrere Meinungen nichts bringen
(z. B. Programmierung, Mathematik, schnelle Kurzanfragen), wird der Fan-out
automatisch übersprungen — die Anfrage läuft dann exakt wie Smart (Cloud),
ohne Zusatzkosten. **Nach einem Turn, für den das Gremium wirklich gearbeitet
hat, stellt der Composer automatisch auf das Modell um, das die Antwort erstellt
hat** (den Orchestrator bzw. bei Plan-Delegation das Ausführungs-Modell) — so
läuft die nächste Anfrage direkt auf diesem Modell, statt jedes Mal wieder das
ganze Gremium einzubeziehen (Rückfragen brauchen es meist nicht erneut; wählen
Sie „Experten-Gremium" einfach wieder aus, wenn Sie es benötigen). Wurde der
Fan-out für eine Anfrage übersprungen, bleibt „Experten-Gremium" ausgewählt
(9.392.0). Wissenswert: Jeder Entwurf ist ein eigener
kostenpflichtiger Modell-Aufruf (im Kosten-Popover als „moa_reference"
ausgewiesen); die Antwort startet erst, wenn alle Entwürfe da sind (oder ihr
Timeout abläuft); Bild-Anhänge sieht nur das antwortende Modell, nicht die
Referenzen; MoA benötigt den LLM-/Hybrid-Klassifikator und ist unter der
DSGVO-Sperre ausgeblendet (Referenzen sind Cloud-Modelle). In geplanten
Aufgaben ist MoA nicht verfügbar.

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
Wird eine mehrteilige Sitzung geöffnet, sind alle Anfragen **bis auf die
letzte vorab zugeklappt** — Sie landen direkt auf dem jüngsten Austausch
statt durch die ganze Historie zu scrollen. Das gilt nur beim ersten Öffnen
der Sitzung; klappen Sie danach Anfragen auf und wechseln zwischendurch
weg, bleibt Ihr Zustand beim erneuten Öffnen erhalten (kein erneutes
Einklappen). Bei einer noch laufenden Antwort bleibt alles offen.
Ein Klick klappt diese eine Anfrage auf oder zu. **Lange gedrückt halten**
(≈ ½ Sekunde) klappt **alle** Anfragen auf oder zu — die Richtung richtet
sich nach der gehaltenen Anfrage (eine offene gehalten → alle zu; eine
zugeklappte gehalten → alle auf); danach wird die gehaltene Anfrage wieder
in den Blick gescrollt. Alle Auf-/Zuklapp-Bereiche im Chat — Anfragen, der
**Datenschutz**-Block, verdichteter Kontext, Webquellen, Quellen-Legende,
durchsuchte Quellen — animieren weich. Ist der Anfrage-Text neben dem Badge
zu lang für eine Zeile, wird er abgekürzt und ein kleiner Pfeil zeigt die
volle Anfrage. **Sie können dafür direkt auf den Anfrage-Text klicken** (nicht
nur auf den Pfeil), um ihn ein- bzw. auszuklappen; Text markieren zum Kopieren
löst das Aufklappen nicht aus.

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

**Antwort ins Wiki übernehmen** (v9.303.0) — neben Kopieren gibt es unter jeder
Assistenten-Antwort ein **Lesezeichen-Symbol** („Als Wiki-Seite speichern“): Ein
Klick legt genau diese Antwort als eigene, bearbeitbare Wiki-Seite an (Scope
„Meine“; in einem Projekt-Chat wird die Seite automatisch dem Projekt
zugeordnet). Der Seitentitel kommt aus der ersten Zeile der Antwort. Dieselbe
Antwort erneut speichern **aktualisiert die vorhandene Seite als neue Version**
statt ein Duplikat anzulegen. Das ist unabhängig vom Speicher-Menü (das die
MemPalace-Erinnerung steuert) und vom automatischen Chat-Wiki (das den ganzen
Chat zusammengefasst ablegt) — hier landet die Antwort wortgetreu.

**Suche** (Seitenleiste, auch Strg/Cmd+K, v9.306.0): EIN Suchfeld über alles —
**Chats** (Volltext über Titel, Zusammenfassungen und Nachrichteninhalte, mit
Treffer-Vorschau), **Wiki** (sinngemäße/semantische Treffer über alle
zugänglichen Wiki-Seiten) und **Gedächtnis** (MemPalace-Erinnerungen). Die
Ergebnisse sind gruppiert; ein Klick öffnet den Chat bzw. die Wiki-Seite
direkt. Es werden nur Inhalte durchsucht, die das eigene Konto sehen darf.

**Archivieren ≠ Löschen**: archivierte Chats sind aus der Liste
ausgeblendet, ihre Gedächtnis-Drawer bleiben aber erhalten. Löschen
entfernt alles inklusive Drawer **und des zugehörigen Wikis**.

**Automatisches Archivieren & Löschen** (Einstellungen → Allgemein →
**Bereinigung**, admin-konfigurierbar): Ist die Funktion aktiviert, werden
Chats automatisch aufgeräumt — für projektlose **und** projektbasierte Chats:
- **Archivieren nach N Tagen Inaktivität** (Standard 30): Ein Chat, der seit N
  Tagen nicht mehr **aufgerufen** wurde, wird automatisch archiviert — **aber
  nur**, wenn er rein **privat** ist (nicht geteilt), **nicht gemerkt** ist
  (kein Wiki/Memory aus diesem Chat) und **nirgends referenziert** ist (kein
  Favorit, keine laufende Hintergrundaufgabe, kein Workflow). Gemerkte,
  geteilte, favorisierte oder referenzierte Chats bleiben unangetastet.
- **Löschen nach M Tagen im Archiv** (Standard 90): Alles, was seit M Tagen
  archiviert ist, wird endgültig gelöscht (inkl. Drawer **und Wiki**).
- **Jede Frist auf 0 setzen schaltet die jeweilige Stufe ab**; der Haupt-Schalter
  schaltet die ganze Funktion aus (Standard: aus).
- Das **Öffnen** eines archivierten Chats holt ihn **nicht** automatisch zurück
  und stoppt die Lösch-Uhr nicht — zum Behalten den Chat manuell de-archivieren
  (Zeilenmenü ⋯). Ein noch genutzter, **aktiver** Chat wird hingegen schon durch
  das bloße Öffnen als „aufgerufen“ gewertet und damit vor dem Archivieren bewahrt.
- Vor diesem Release bereits archivierte Chats haben keinen Archivierungs-Zeitstempel
  und werden erst nach erneutem Archivieren auto-gelöscht.

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
   selbst schon fragt). Der Import läuft dann in **zwei Phasen**: Erst werden
   die Dateien hochgeladen (mehrere parallel — der Server nimmt sie nur
   entgegen, das dauert Millisekunden pro Datei), danach zeigt derselbe Dialog
   „Inhalte werden extrahiert …" — die eigentliche Text-Extraktion (bei
   gescannten PDFs inkl. **OCR**, das mehrere Minuten pro Datei dauern kann)
   läuft **im Hintergrund auf dem Server**. Der Button **„Im Hintergrund
   fortsetzen"** schließt den Dialog, ohne etwas abzubrechen — der Fortschritt
   bleibt im Dateien-Zweig sichtbar: jede Datei trägt dort ihren
   Extraktions-Status (**offen** → **wird extrahiert …** → normale
   Dateizeile; bei Problemen **Fehler** mit Grund im Tooltip), der Zweigkopf
   zählt „N in Extraktion" mit, und über das **✕** an einer wartenden oder
   laufenden Datei lässt sich deren Extraktion **einzeln abbrechen**.
   Am Ende erscheint ein **Status** — bei Fehlern bleibt der Dialog offen und
   listet, welche Dateien nicht importiert werden konnten und warum (z. B.
   „Kein Text extrahierbar", „Dateityp nicht unterstützt"). Die
   Unterordner-Struktur wird als Gruppen übernommen. Das Einspeisen ins
   Projektgedächtnis wartet automatisch, bis die Extraktion des gesamten
   Stapels fertig ist, und startet dann sofort.

   **Unterstützte Dateitypen:** PDF (gescannte inkl. OCR), docx, xlsx/xls,
   pptx, eml/msg (Outlook-Mails), txt/md, html, csv/tsv, Bilder
   (png/jpg/gif/webp/bmp/svg) sowie **Tonaufnahmen** (mp3, m4a, wav, flac,
   ogg, opus, aac, mp4, mov, webm) — Audio-/Videodateien werden beim Import
   **automatisch transkribiert** und danach wie jedes andere Dokument
   durchsucht (standardmäßig lokal, also ohne Zusatzkosten). Alles andere
   wird beim Hochladen mit „Dateityp nicht unterstützt" abgelehnt.

   **Bilder werden ausgelesen:** Aus einem Bild wird nicht nur Größe/Format
   erfasst, sondern per **OCR der enthaltene Text** (Scan eines Dokuments,
   Ausweis, Screenshot) — damit ist der Inhalt durchsuchbar und landet im
   Wissensgraphen. Dieselbe OCR greift bei **gescannten PDFs** ohne Textebene.
   Eingestellt wird sie unter *Einstellungen → Service-Modelle → OCR*
   (`config.json` → `ocr.engine`):

   - **`mlx_ocr`** (Standard) — ein spezialisiertes OCR-Modell, das **direkt im
     Brain-Prozess** auf der eigenen GPU läuft (mlx-vlm), Standardmodell
     `mlx-community/GLM-OCR-8bit` (0,9 Mrd. Parameter, 1,6 GB).
     Nichts verlässt den Rechner. Läuft **nicht** über oMLX — das bleibt den
     Chat-Modellen vorbehalten. **Remote-Variante:** Ist im OCR-Block eine
     **Remote-URL** (+ optional API-Key) gesetzt (`ocr.mlx_ocr_url`), wird das
     Bild stattdessen an diesen OpenAI-kompatiblen GLM-OCR-Endpoint geschickt
     (z. B. den Mac mini M4) — gleiches Modell, nur per HTTP statt in-process.
     Nötig auf Maschinen ohne MLX (etwa Windows); ein Wechsel greift ohne
     Neustart (die in-process-Gewichte werden beim Umschalten entladen).
   - **`mistral_ocr`** — Cloud-OCR-Endpunkt (schnell, aber die Datei geht zum
     Anbieter).
   - **`local_vision`** — ein allgemeines Chat-Vision-Modell über oMLX.
     Funktioniert, ist aber deutlich langsamer als `mlx_ocr` (~37 s statt ~1 s).
   - **`auto`** — Cloud zuerst, lokal als Rückfall. · **`none`** — OCR aus.

   **Ausweise & sensible Unterlagen werden an ihrer ART erkannt:** Unabhängig
   vom erkannten Text wird bei Bildern der **Dokumenttyp** bestimmt (Reisepass,
   Personalausweis, Kontoauszug, Rechnung …) und daraus automatisch eine
   **Vertraulichkeitsstufe** abgeleitet: Ausweisdokumente und medizinische
   Unterlagen gelten als *Streng Vertraulich*, Kontoauszüge/Verträge als
   *Vertraulich*, Rechnungen/Belege als *Intern*. Das greift **auch dann, wenn
   die Texterkennung nichts lesen konnte** — genau der Fall, in dem eine reine
   Textprüfung blind wäre. Die Stufe kann dadurch nur **steigen**, nie sinken.

   **Wie verlässlich ist der erkannte Text?** Er ist ein **Sucheinstieg, keine
   Datenquelle.** Bei sauberen Vorlagen (Dokumentenscan, Screenshot) stimmt er
   meist; bei schwierigen Bildern (abfotografierter Ausweis, schräg, gespiegelt,
   winziger Ausschnitt) **kann er Namen, Daten und Nummern falsch lesen**. Zwei
   Schutzmechanismen sind eingebaut: (a) Findet eine unbestechliche
   Zweitprüfung (Tesseract) im Bild **gar keinen** lesbaren Text, wird die
   Ausgabe des Modells **verworfen** — denn ohne lesbare Vorlage wäre alles
   Gesagte frei erfunden. (b) Jeder Bild-Text trägt einen **Warnhinweis** im
   Dokument, den auch der Agent und der Wissensgraph mitlesen. Für belastbare
   Angaben gilt: **Originalbild prüfen** — es bleibt unter `originals/`
   erhalten.

   **Originale bleiben erhalten:** Die hochgeladene Datei selbst wird im
   Projekt unter `originals/` aufbewahrt (nicht nur der extrahierte Text).
   Sie wird mitgelöscht, wenn Sie das Dokument aus dem Baum entfernen. Für
   **Eingabeordner** ist das ohnehin nie ein Thema — die liegen auf Ihrer
   Platte und werden nur gelesen, nie verändert oder gelöscht.
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

**Isolierte Arbeitsbereiche (Worktree-Lanes, Code-Projekte):** Für riskante
oder parallele Arbeiten (großes Refactoring, Paket-Upgrade testen) kann der
Assistent auf Wunsch eine **Lane** anlegen — eine isolierte Kopie des Projekts
unter `.worktrees/<name>` auf einem eigenen Branch (`brain/<name>`). Er fragt
vor dem Anlegen und Entfernen nach, arbeitet dann in der Lane statt im
Hauptverzeichnis und zeigt Ihnen zum Review den **Diff gegen den
Ausgangsstand**. Das **Zusammenführen bleibt bewusst bei Ihnen** (im Terminal:
`git merge brain/<name>`) — der Assistent merged nie automatisch. Eine Lane
mit uncommitteten Änderungen wird nicht stillschweigend gelöscht.

Außerdem arbeitet der Assistent in Code-Projekten präziser: **Datei-Änderungen
schlagen seltener fehl** (fast-richtige Änderungsstellen mit typografischen
oder Einrückungs-Abweichungen werden sicher erkannt — bei Mehrdeutigkeit wird
nachgefragt statt geraten), und für **strukturelle Code-Suchen und
-Umbauten** („alle Aufrufe von X auf Y umstellen") nutzt er eine syntaxbewusste
Suche mit **Vorschau vor jeder Änderung**.

**Code-Index nutzen** (Code-Projekte): Der untere Bereich kennt alle Symbole des
Projekts (aus dem automatisch gepflegten Code-Index) und macht sie direkt
nutzbar:
- **Symbole im Datei-Baum** (Code-Projekte): Die Symbole (**Klassen, Methoden,
  Funktionen und Variablen**) stecken direkt **im Datei-Baum** — kein getrenntes
  Panel mehr. Quelldateien mit Symbolen haben einen **Aufklapp-Pfeil**; klappen
  Sie eine Datei auf, erscheinen ihre Symbole darunter (mit Signatur und
  Zeilennummer). **Klick** auf den Dateinamen öffnet die Datei wie gewohnt;
  **Klick** auf ein Symbol öffnet die Datei und springt an die Definition (die
  Zeile blinkt kurz auf). Über das **↗-Symbol** klappt ein Symbol seine
  **Aufrufer** (wer ruft das auf) und **Verwendungen** (alle Fundstellen im
  Projekt) auf — beide ebenfalls anklickbar zum Hinspringen. **Bei SQL- und
  ShowCase-Dateien** (`.sql`/`.dbq`) erscheinen als Symbole die **verwendeten
  Tabellen**, die **definierten Prozeduren und Views**, etwaige **CTEs** und
  **Linked-Server** (OPENQUERY) — so sehen Sie pro Abfrage auf einen Blick, welche
  Datenquellen sie anfasst.
- **Vereinte Suche** (Suchfeld über dem Datei-Baum): filtert **gleichzeitig nach
  Dateinamen und Symbolnamen**. Dateien, deren Symbole passen, werden automatisch
  aufgeklappt und zeigen nur die Treffer.
- **Symbol-Schnellsuche**: **Cmd/Strg+P** öffnet zusätzlich eine schnelle
  Suchleiste über alle Funktionen, Methoden und Klassen (↑/↓ + Enter springt).
- **Rechtsklick auf ein Symbol** im Editor: **Gehe zu Definition** und **Wer
  ruft das auf?** (klickbare Liste der aufrufenden Stellen).
- **Autovervollständigung**: **Strg-Leertaste** schlägt passende
  Projekt-Symbole vor (rein aus dem Index, kein KI-Lauf; auf dem Mac
  Strg-Leertaste — nicht Cmd-Leertaste, das ist Spotlight).
- **Hover**: Mit der Maus über ein Symbol fahren zeigt Signatur, Docstring und
  Aufruf-Häufigkeit.
- **Auswertungen** (Σ-Knopf in der Datei-Baum-Leiste): fertige Analysen über den
  Code-Index als Karten — **komplexeste Funktionen**, **meistgenutzte
  Funktionen**, **aufruf-intensivste Funktionen**, **größte Dateien**,
  **Klassenhierarchie** — mit übersichtlicher Ergebnistabelle (Balken für
  Kennzahlen, Klick auf Pfad/Zeile springt in den Code). Für Power-User gibt es
  ganz unten einen Aufklapper **„Eigene Abfrage (erweitert)“** für rohe
  (nur lesende) Cypher-Abfragen.
  - **SQL-Auswertungen**: Enthält das Projekt SQL-Dateien (`.sql` oder `.dbq`),
    erscheint zusätzlich eine eigene Gruppe **SQL-Auswertungen** mit:
    **Tabellen-Hotspots** (meistreferenzierte Tabellen über alle Abfragen),
    **komplexeste Abfragen** (nach Anzahl der JOINs), **Linked-Server-Zugriffe**
    (OPENQUERY-Abhängigkeiten) und einem **Prozeduren-&-Views-Inventar** (mit
    Sprung zur Definition). Funktioniert auch mit großen, gemischten SQL-Sammlungen
    inkl. IBM-DB2/iSeries-Syntax. **ShowCase-Dateien (`.dbq`)** werden automatisch
    mitberücksichtigt: das darin eingebettete SQL wird ausgelesen und indexiert,
    sodass diese Auswertungen genauso durchsuchbar sind wie reine `.sql`-Dateien.
  - **R-Auswertungen**: Enthält das Projekt R-Skripte (`.R`), erscheint eine
    Gruppe **R-Auswertungen** mit: **Funktionen & Aufrufe** (inkl. Warnung bei
    Funktionen, die mehrfach in verschiedenen Dateien definiert sind),
    **Daten-Fluss & Quellen** (welches Skript welche Dateien liest/schreibt),
    **Skript-Abhängigkeiten** (`source()`-Graph) und **Globaler Zustand &
    Komplexität** (Funktionsgröße + Nutzung globaler Variablen — Hinweis auf
    Refactoring-Risiken). Auf Base-R wie auch tidyverse-Stil ausgelegt.

**Code-Mode-Prompt-Erweiterung** (Admin): Unter **Einstellungen → Tools** lässt
sich eine sprachunabhängige Arbeitsanweisung bearbeiten, die in JEDEN
Code-Mode-Projekt-Chat in den System-Prompt eingefügt wird (Index zuerst nutzen,
vollständigen lauffähigen Code liefern, Abhängigkeiten/globalen Zustand benennen,
Projektstil/Duplikate beachten). Das Feld leeren deaktiviert die Erweiterung.

**Datei-Baum im unteren Bereich** (Code-Projekte): Der Baum des
Arbeitsverzeichnisses liegt als **linke Spalte** des unteren Bereichs, direkt
neben Terminal und Editor (gleicher Look wie der Editor; der gesamte untere
Bereich folgt dem Hell-/Dunkel-Modus der Oberfläche). Er lässt sich
über den Knopf in der Bereichsleiste **ein-/ausblenden** und in der Breite
ziehen; Sichtbarkeit, Breite und Aufklapp-Zustand werden **pro Projekt**
gemerkt (standardmäßig sind alle Ordner zugeklappt — zwei Knöpfe klappen alles
auf bzw. zu). Jede Datei zeigt drei Signale: die **Farbe des Datei-Symbols**
(links vom Namen) spiegelt den **Git-Status** (geändert = amber, neu/
unversioniert = grün, gelöscht = rot, umbenannt = blau), ein **`*`** hinter dem
Namen markiert im Editor geöffnete Dateien mit **ungespeicherten Änderungen**,
und ein **Punkt** rechts zeigt den Index-Status (grün = indexiert,
hohl-grün = indexiert, aber ohne extrahierbare Symbole — z. B. ein reines
SQL-SELECT-Skript, das keine benannte Funktion/Prozedur enthält; grau =
veraltet; rot = sollte indexiert sein, ist es aber nicht; hohl-grau = keine
Quelldatei). Ein Klick öffnet die Datei im
Editor; die gerade bearbeitete Datei ist im Baum hervorgehoben. Der
**Ein-Fenster-Modus** (Umschalter in der Baum-Leiste, seit 9.318 — ersetzt
den früheren Ein-Editor-Modus) hält den Terminal-Bereich bewusst aufgeräumt:
aktiviert gibt es **maximal einen Tab je Typ** — einen Editor, ein Terminal,
einen Terminal-Chat. Öffnen Sie etwas Neues, **ersetzt** es den jeweiligen
Tab seines Typs (eine neue Datei ersetzt die offene Datei, ein neuer Chat den
offenen Chat); die Diff-Ansicht teilt sich den Editor-Platz, der
Subagenten-Hub den Chat-Platz. Die **Fensteraufteilung bleibt frei**: die bis
zu drei Tabs lassen sich weiterhin per Ziehen an Fensterkanten in Splits
anordnen (z. B. Editor oben, Terminal unten). Terminals werden beim Ersetzen
nur ausgeblendet, nicht beendet — die Sitzung läuft serverseitig weiter.
Deaktivieren Sie den Modus, können Sie wieder beliebig viele Tabs je Typ
öffnen.

**Auto-Close-Modus** (⚡-Umschalter in der Baum-Leiste, seit 9.319.0): Wenn
aktiv, wird der Terminal-Bereich beim **Auswählen eines Chats** (in der
Terminal-Chats-Liste) oder beim **Öffnen einer Datei, die zu einem Chat
gehört**, automatisch aufgeräumt — alle Tabs, die nicht zu diesem Chat
gehören (andere Chats, fremde Dateien, Terminals), werden geschlossen. „Zum
Chat gehörend“ sind der Chat selbst, seine Arbeitsdateien (Ordner
`chats/…_<Chat-ID>/`) und der Subagenten-Hub. Terminals werden dabei nur
ausgeblendet (die Sitzung läuft serverseitig weiter). **Der Arbeitsplatz je
Chat wird dabei gemerkt und wiederhergestellt** (seit 9.322.0): Beim Verlassen
eines Chats merkt sich der Modus, welche Tabs gerade offen waren — geöffnete
Dateien, Terminals, deren Fenster-Zuordnung und die Aufteilungs-Größen. Wechseln
Sie später zu diesem Chat zurück, öffnen sich diese Tabs automatisch wieder an
ihren alten Positionen (Beispiel: Chat + eine CSV-Datei offen, Wechsel in einen
anderen Chat und zurück → Chat und CSV erscheinen wieder wie zuvor). Die
gemerkten Tab-Sätze werden pro Projekt gespeichert und überleben ein Neuladen
der Seite. Dieselbe Aufräum-Aktion gibt es
auch einmalig per **Rechtsklick auf einen Chat-Tab → „Alles Chat-Fremde
schließen“** (dort schließen Terminals endgültig, ohne Merken/Wiederherstellen).

**Dateien vergleichen (Diff-Ansicht, seit 9.318.0)**: Ein **Rechtsklick auf
eine geänderte Datei** im Datei-Baum (amber gefärbt) bietet **„Diff gegen
HEAD“** — die Datei erscheint als Vorher/Nachher-Gegenüberstellung
(Seite-an-Seite, Änderungen farbig markiert, unveränderte Passagen
eingeklappt) in einem eigenen Tab (Δ). Zwei beliebige Dateien vergleichen Sie
per Rechtsklick → **„Zum Vergleich markieren“** auf der ersten und
**„Vergleichen mit …“** auf der zweiten Datei. Die Ansicht ist nur-lesend;
bearbeitet wird weiterhin im Editor-Tab. Unabhängig davon kann der Agent im
Chat Datendateien vergleichen: Excel/CSV/JSON/XML per Schlüssel-Vergleich
(auch über Formatgrenzen, z. B. CSV gegen JSON) und Text-/Code-Dateien als
Zeilen-Diff — fragen Sie einfach „vergleiche Datei A mit B“.

**Ansicht vs. Bearbeiten im Editor**: In der **Ansicht** (nur lesen) werden
**darstellbare Dateien gerendert** — HTML/SVG als fertige Seite bzw. Grafik,
Markdown als formatierter Text. Alle anderen Dateien (Code) erscheinen in der
Ansicht als nur-lesbarer Quelltext (gleiche Darstellung wie im Bearbeiten-Modus,
nur ohne Cursor). Im **Bearbeiten**-Modus sehen Sie immer den Quelltext und
können ihn ändern; eine Statuszeile unten zeigt Größe, Zeilenzahl und
Änderungsdatum. **SQL-Dateien** (`.sql`) werden farblich hervorgehoben.

**XML- und JSON-Dateien** (`.xml`/`.svg`/`.json`/`.jsonl`/`.geojson`) lassen
sich wie eine Baumstruktur erkunden: In der **Ansicht** erscheint ein
**aufklappbarer Datenbaum** — Objekte, Listen und Werte per Klick auf-/zuklappen,
mit „Alles aufklappen/zuklappen“. Im **Bearbeiten**-Modus zeigen **Klapp-Pfeile
am linken Rand** (Gutter) jede Verschachtelung, sodass Sie einzelne XML-Elemente
bzw. JSON-Objekte/Arrays einklappen können (Tastenkürzel **Strg+Q** faltet am
Cursor).

**ShowCase-Dateien (`.dbq`)**: Diese Dateien sind XML-Hüllen um eine
SQL-Abfrage. Der Editor bietet dafür **zwei bearbeitbare Ansichten**, die Sie
über dieselben zwei Knöpfe oben umschalten (für `.dbq` beschriftet als
**„SQL (extrahiert)"** und **„XML-Quelle"**): die SQL-Ansicht zeigt allein die
Abfrage mit SQL-Farbhervorhebung, die XML-Ansicht die vollständige Rohdatei.
Sie können in **beiden** Ansichten editieren — beim Speichern wird eine in der
SQL-Ansicht geänderte Abfrage automatisch wieder in die XML-Datei eingesetzt,
sodass immer die vollständige `.dbq` geschrieben wird. Ein dritter Knopf
**„XML-Baum"** zeigt die XML-Struktur als aufklappbaren Baum (nur lesen).

**Geteilter Arbeitsbereich** (Code-Projekte): Sie teilen den unteren Bereich
**dynamisch per Ziehen** auf — es gibt keine feste Layout-Auswahl mehr. Ziehen
Sie einen Tab an den **linken, rechten, oberen oder unteren Rand** eines
Teilbereichs, teilt sich der Bereich in diese Richtung und der Tab landet im neuen
Teilbereich; ziehen Sie ihn in die **Mitte** (oder auf die Tab-Leiste), wandert er
nur dorthin. Innerhalb einer Tab-Leiste lässt sich die **Reihenfolge der Tabs
per Ziehen ändern** (seit 9.322.0): Ziehen Sie einen Tab **auf einen anderen
Tab**, wird er an dieser Position eingefügt — eine farbige Kante am Ziel-Tab
zeigt, ob er links oder rechts davon landet; ein Ablegen auf der **freien
Fläche** der Tab-Leiste hängt ihn ans Ende. So entstehen bis zu vier
Teilbereiche (ein 2×2-Raster: oben links,
oben rechts, unten links, unten rechts). Jeder Teilbereich hat eigene Tabs und
einen eigenen „+“/◈/Neue-Datei-Knopf, und die Teilbereiche sind über die
Trennlinien **größenverstellbar**. **Schließen Sie das letzte Fenster eines
Teilbereichs, wird der Platz automatisch wieder freigegeben** und die übrigen
Bereiche füllen ihn aus. Aufteilung, Größen und geöffnete Dateien werden
**pro Projekt** gemerkt.

**Terminal direkt öffnen**: In der **Projektansicht** eines Code-Projekts öffnet
der **„Terminal“-Knopf** oben (neben Titel und Stern) den Terminal/Editor-Bereich
sofort im **Vollbild** — ohne dass Sie erst einen Chat starten müssen. Beim Öffnen
wird Ihr **gespeicherter Arbeitsbereich wiederhergestellt**; ein neues Terminal
wird nur angelegt, wenn der Bereich vollständig leer ist. Wechseln Sie zu einer
anderen Ansicht (Startseite, anderes Projekt, Chat ohne Bezug), **schließt sich
der Bereich automatisch** — er bleibt an das Projekt/den Chat gebunden, aus dem
Sie ihn geöffnet haben.

**Maximale Arbeitsfläche**: Solange der Terminal/Editor-Bereich offen ist,
zieht sich die **linke Navigationsleiste automatisch auf ihre schmale
Symbol-Ansicht** zurück (alle Funktionen bleiben über die Symbole erreichbar).
Beim Schließen des Bereichs kehrt sie in Ihre gewohnte Breite zurück; haben Sie
die Leiste selbst eingeklappt (Pin-Knopf oben), bleibt sie auch danach
eingeklappt — Ihre manuelle Einstellung gewinnt immer.

**Standard-Platzierung**: Neue Inhalte landen je nach Layout automatisch im
passenden Bereich — **Quelldateien** oben links, **andere Dateien** (HTML,
Markdown, SVG …) oben rechts, **Terminal** und **Terminal-Chat** unten; fehlt
eine Position im aktuellen Layout, wird sinnvoll ausgewichen (z. B. oben rechts
→ oben links). Öffnen Sie gezielt über die **Knöpfe direkt am Bereich**
(„+“ / ◈ / Neue Datei), landet der Inhalt weiterhin genau in diesem Bereich.

**Automatische Aktualisierung**: Ändert sich eine im Editor geöffnete Datei auf
der Platte (durch den Assistenten, das Terminal oder ein externes Programm),
wird sie **automatisch neu geladen**. Haben Sie selbst **ungespeicherte
Änderungen** offen, wird nichts überschrieben — der Tab markiert den Konflikt
(amber), bis Sie speichern. Auch der **Datei-Baum** des Arbeitsverzeichnisses
hält sich von selbst aktuell: kommen Dateien hinzu oder werden gelöscht (etwa
durch einen Terminal-Befehl, den „!“-Shell-Befehl oder ein externes Programm),
erscheinen bzw. verschwinden sie automatisch.

**In externem Programm öffnen**: Dateien, die sich nicht sinnvoll im Editor
bearbeiten lassen (Word, Excel, PowerPoint, PDF, Medien), öffnen sich per Klick
im Datei-Baum direkt im **Standardprogramm** des Rechners. Über das
**Rechtsklick-Menü** einer Datei wählen Sie immer zwischen „Im Editor öffnen"
und „In externem Programm öffnen".

**Dateien verwalten**: Der Datei-Baum lässt sich direkt bearbeiten. **Rechtsklick
auf eine Datei** bietet zusätzlich **Umbenennen** und **Löschen**; **Rechtsklick
auf einen Ordner** bietet **Neue Datei**, **Neuer Ordner**, **Umbenennen** und
**Löschen**. Gelöschte Elemente landen in einem **Papierkorb** (`.brain-trash`
im Projekt) und sind wiederherstellbar — es wird nichts unwiderruflich entfernt.
Dateien und Ordner lassen sich per **Ziehen** in einen anderen Ordner
**verschieben**. Über die Knöpfe oben im Baum legen Sie eine **neue Datei** oder
einen **neuen Ordner** direkt im Arbeitsverzeichnis an.

Im unteren Bereich teilen sich der **Datei-Baum** und die **Terminal-Chats** die
linke Spalte standardmäßig zur Hälfte; das Höhenverhältnis passen Sie über die
**Trennlinie** dazwischen per Ziehen an (pro Projekt gemerkt). Der **Maus-Tooltip**
einer Datei zeigt Pfad, **Größe** und **letzte Änderung**. Über den
**Sortier-Knopf** in der Baum-Leiste sortieren Sie nach **Art** (Ordner zuerst —
Standard), **Name**, **Datum** (neueste zuerst) oder **Größe** (größte zuerst);
die Wahl wird pro Projekt gemerkt.

**Chat-Arbeitsdateien im Baum (Toggle)**: Die von Terminal-Chats erzeugten
Dateien (Skripte, Reports, Zwischenergebnisse) liegen im Ordner `chats/` des
Arbeitsverzeichnisses — je Chat ein eigener Unterordner. Standardmäßig ist
dieser Ordner im Datei-Baum **ausgeblendet**, sodass der Baum nur die echten
**Projektdateien** zeigt (das, was auch ins Code-Mining eingeht). Über den
**Ordner-Toggle-Knopf** in der Baum-Leiste blenden Sie `chats/` bei Bedarf ein;
die Wahl wird pro Projekt gemerkt. Die Chat-Dateien selbst finden Sie
stattdessen in der Sektion **Terminal-Chats**: jeder Chat mit erzeugten Dateien
trägt dort einen **Aufklapp-Pfeil**, der seinen Ausgabe-Ordner direkt unter dem
Chat zeigt (Klick auf eine Datei öffnet sie im Editor). **Auch jede
Ordner-Ebene innerhalb dieser Hierarchie ist auf-/zuklappbar** (seit 9.322.0)
— standardmäßig ist alles zugeklappt. **Erzeugt oder ändert ein Chat eine
Datei, klappt der Pfad dorthin automatisch auf** (Chat-Eintrag plus die
betroffenen Unterordner) und die Datei erhält eine **grüne Markierung**, bis
Sie sie öffnen — Neues ist so sofort sichtbar, ohne dass alles andere offen
stehen muss (nur dort — der Datei-Baum bleibt unberührt). Der
Auf-/Zuklapp-Zustand wird pro Projekt gespeichert und beim nächsten Öffnen
wiederhergestellt.

**Terminal-Chat** (Code-Projekte): Neben Terminal und Editor öffnen Sie im
unteren Bereich einen **Terminal-Chat** — eine schlanke, terminalartige
Chat-Oberfläche („wie ein Coding-Assistent im Terminal"). Sie ist im echten
**CLI-Look** gestaltet: durchgehend Monospace in einheitlicher Größe, ruhige
Terminalfarben und Struktur allein über Farbe statt Fettdruck — liest sich wie
die Kommandozeile. Die Farben folgen dem **App-Theme**: im hellen Modus hell,
im dunklen Modus die gewohnte warm-dunkle CLI-Palette. Sie ist als
vollwertiger Ersatz für die normale Chat-Ansicht beim Arbeiten in einem
Code-Projekt gedacht: Sie öffnen sie über den **◈-Knopf** in der Tab-Leiste eines
Teilbereichs (oder „+ Neuer Terminal-Chat" in der Sektion **Terminal-Chats**).
Ein Terminal-Chat lässt sich wie jeder Tab teilen — z. B. links ein Editor,
rechts der Chat — und maximieren.

Die **Eingabezeile** sitzt — wie in einer Terminal-CLI — direkt **unter der
letzten Antwort** und scrollt mit dem Verlauf mit. Den **Text im Verlauf** können
Sie wie gewohnt markieren und kopieren. Die Antworten **streamen** live mit einem
Lauf-Anzeiger (Spinner); darunter zeigt eine **Statuszeile** das aktive Modell,
die Denkstufe, Token (ein/aus), die **Prompt-Cache-Treffer** (⚡ gecachte Token
samt Trefferquote, grün sobald Treffer anfallen), Kosten und die
Kontext-Auslastung — und rechts einen **⬇ .md-Knopf**, der den **Chatverlauf
als Markdown** direkt herunterlädt (ohne Ablage im Arbeitsverzeichnis).
Werkzeugaufrufe werden als kompakte Zeilen (`● Werkzeug Datei ✓`) eingeblendet
(per `/tools` ein-/ausschaltbar) — zusammen mit den Denkschritten in der
**tatsächlichen Reihenfolge der Ausführung**, live wie beim erneuten Öffnen
eines Verlaufs.

**Aufbau des Verlaufs** (v9.325.0): Ihre eigene Eingabe steht **bündig links**
mit dem `›`-Zeichen davor und ist damit der Ankerpunkt, den Sie beim
Zurückscrollen suchen — alles, was Brain daraufhin tut (Werkzeugaufrufe,
Denkschritte, Antwort), ist darunter **eingerückt**. Eine Eingabe beginnt also
sichtbar einen Abschnitt. Interne Hinweise, die Brain sich selbst gibt (etwa der
Ausgabeordner dieses Chats oder die Zustellung fertiger Hintergrundaufgaben),
erscheinen **nicht** als Ihre Eingabe: der Ordner-Hinweis wird ausgeblendet, die
Zustellung erscheint gedämpft wie ein Denkschritt.

Mit
**↑/↓** blättern Sie wie in einer Shell durch zuletzt gesendete Eingaben, mit
**Tab** auf leerer Zeile holen Sie einen Eingabe-Vorschlag. Über das
**Rechtsklick-Menü** eines Tabs exportieren Sie Terminal-Chat **oder**
Shell-Terminal jederzeit **als Markdown**. Schließen Sie den letzten Tab, bleibt
der untere Bereich **geöffnet** (leerer Teilbereich) — er wird nur über das
✕/den Umschalter geschlossen.

Sobald Sie ein **„/“** eingeben, erscheint eine **Auswahlliste** der Befehle
(mit Beschreibung); bei Befehlen mit Optionen (z. B. `/model`, `/think`) folgt
direkt die Werteliste — mit **↑/↓** auswählen, mit **Enter/Tab** übernehmen,
**Esc** schließt. **Schrägstrich-Befehle** steuern den Chat direkt:
`/model <Name|auto>`
(Modell wechseln), `/think off|low|medium|high` (Denktiefe), `/tools on|off`
(Werkzeuganzeige), `/caveman 0-3` (Antwortstil), `/clear` (neue Sitzung, leerer
Kontext), `/lcm` (Kontext komprimieren), `/sync` (Projekt-Sync anstoßen),
`/init` (BRAIN.md erzeugen), `/suggest` (nächste Eingabe vorschlagen),
`/cancel` (laufende Antwort abbrechen) und `/help` (Übersicht). Mit einem
vorangestellten **Ausrufezeichen** führen Sie eine Zeile direkt als
**Shell-Befehl** im Arbeitsverzeichnis aus — z. B.
`! python wettervorhersage.py --region="München"` oder `! ls *.md`; die Ausgabe
erscheint sofort im Chat (ohne Anfrage an das Sprachmodell).

Eine laufende Antwort brechen Sie jederzeit mit **Esc** oder **`/cancel`** ab —
auch wenn der Cursor gerade nicht im Eingabefeld steht. Diese Chats werden **pro
Projekt** gespeichert und in der Sektion **Terminal-Chats** unter dem Datei-Baum
gelistet — ein Klick öffnet einen früheren Verlauf wieder; über das **✕** an
einer Zeile löschen Sie einen einzelnen Chat, über den Knopf im Sektionskopf
**alle auf einmal**. Sie erscheinen **bewusst nicht**
in der normalen Chat-Liste des Projekts, sondern bleiben dem Code-Bereich
vorbehalten. Offene Terminal-Chats werden — wie geöffnete Editor-Dateien — pro
Projekt gemerkt und beim erneuten Öffnen wiederhergestellt.

**Projekt-Anweisungen + Begleitdateien**: Über **Anweisungen bearbeiten**
öffnet sich ein Dialog mit dem Freitext-Feld (Hinweise, an die sich das
Projekt in jeder Antwort hält — Tonfall, Sprache, Formatvorgaben; Markdown +
Vorschau). Darunter der Bereich **Begleitdateien**: hier lassen sich
ergänzende Dateien hochladen (Styleguide, Vorlage, Begriffsliste, erläuternde
Unterlagen — beliebige Typen, max. 25 MB/Datei).

Dieselben Begleitdateien stehen als **eigener Knoten „Begleitdateien"** direkt
im rechten Projekt-Panel — gleichrangig neben *Anweisungen*, *Dateien*,
*Ordner* und *Web-Adressen*. Der Knoten zeigt die Anzahl im Kopf, aufgeklappt
jede Datei mit Größe; über **＋** lädst du eine hinzu, über **✕** entfernst du
eine (mit Rückfrage). Beide Oberflächen zeigen denselben Stand — was du im
Panel änderst, steht auch im Dialog und umgekehrt. Anders als *Dateien*/
*Ordner* tragen Begleitdateien **keinen Index-Status-Punkt**, weil sie
absichtlich nicht gemined werden (siehe nächster Absatz). **Wichtiger Unterschied zu
hochgeladenen Projektdateien (Schritt 4):** Begleitdateien werden **NICHT**
ins Projektgedächtnis aufgenommen/gemined. Sie funktionieren wie ein
Chat-Anhang — der Assistent bekommt ihren Speicherort genannt und liest die
jeweils passende bei Bedarf eigenständig, verlässt sich also direkt auf die
**Datei** statt auf eine Gedächtnis-Suche. Nutze sie für Material, das die
Anweisungen ergänzt und wörtlich vorliegen soll; nutze Schritt 4 / Eingabe-
ordner für einen durchsuchbaren Dokumentkorpus.

**Anweisung mit KI erstellen** (im selben Dialog, Bereich „✨ Anweisung mit KI
erstellen"): Statt die Projekt-Anweisung von Hand zu schreiben, beschreibst du
kurz Ziel und gewünschtes Ergebnis des Projekts, klickst **Generieren** — und
ein KI-Lauf verfasst die vollständige Anweisung. Er liest dabei die beigelegten
Referenz-/Begleitdateien (Inhalt wird direkt verwendet, eine Vorlagendatei wird
als verbindliche Ergebnis-Struktur übernommen), fragt das Projektwissen
(eingelesene Ordner/Web-Quellen) ab und darf eine Webrecherche machen. Der
Fortschritt (welche Datei gelesen, welche Abfrage läuft) wird live angezeigt;
mit **Abbrechen** lässt sich der Lauf stoppen. Das Ergebnis wird zum **Prüfen**
in den Editor geladen — gespeichert wird erst mit **Speichern** (nichts wird
automatisch überschrieben). Welches Modell die Generierung nutzt, stellt der
Admin unter **Einstellungen → Allgemein → Service-Modelle →
„Projektanweisungen (KI-Generierung)"** ein; welche Werkzeuge der Lauf nutzen
darf, steht in der Tool-Matrix in der Spalte **„Projektanweisung"**.

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
eingespeist. Während des Einlesens zeigt der Status-Chip den Fortschritt nur
über die tatsächlich **geänderten** Dokumente — bereits aktuelle werden
separat ausgewiesen (z. B. „Speicher: Dokumente werden gelesen 4/145
Dokumente · 258 unverändert · noch ca. 2 Min."), die Zahl ist also nicht der
ganze Projektbestand. Knöpfe auf der Projektseite:
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
- **Audio Overview** (🎧) — ein **Podcast** im Stil von NotebookLM über die
  Projektinhalte. Ein Klick auf **Generieren** öffnet einen Optionen-Dialog
  (v9.304.0): **Sprache** (automatisch erkennen oder fest wählen — ein
  deutschsprachiges Projekt ergibt einen deutschen Podcast; Voxtral spricht 9
  Sprachen: en/fr/de/es/nl/pt/it/hi/ar), **Sprecherzahl 1–4** (Monolog,
  Dialog, Gesprächsrunde oder Panel), je Sprecher optional **Name**, **Stimme**
  (aus allen Provider-Stimmen inkl. selbst geklonter) und **Persona** (z. B.
  „skeptische Expertin“ — prägt den Gesprächsstil), sowie ein **Publikum**-Feld
  (richtet die Folge auf die Zielgruppe aus). Ohne Stimmen-Auswahl wird
  automatisch eine zur Sprache passende Stimme gewählt; fehlt eine (z. B. keine
  deutsche Stimme geklont), sprechen die englischen Standardstimmen das
  lokalisierte Skript (für muttersprachlichen Klang eine Stimme unter
  Einstellungen → Tools → Sprachausgabe klonen). Ergebnis ist eine
  **`.mp3`-Audiodatei** (plus das Dialog-Skript als `.md`). Über **Öffnen**
  erscheint ein Audio-Player direkt im Studio; **Herunterladen** lädt die MP3.
  Die Generierung dauert länger als ein Textdokument (Skript schreiben → jede
  Zeile vertonen → zusammenfügen); der Fortschritt wird als Phase angezeigt
  (Sammeln → Skript → Vertonen N/M).

Jede Text-Ausgabe wird **streng aus dem Projektgedächtnis** erzeugt und **verbatim
zitiert** (`[Quelle: … — "…"]`); nichts wird hinzuerfunden. Das Ergebnis wird in
**zwei Formaten** gespeichert: einer kanonischen `.md`-Datei (bleibt die Quelle der
Wahrheit für Wiki, Suche und Audio Overview) **und** einem hochwertig gestalteten,
eigenständigen **HTML-Dokument** im redaktionellen Magazin-Stil (Serif-Typografie,
automatisches Inhaltsverzeichnis, helle/dunkle Darstellung, druck-/PDF-fertig). Die
HTML-Ansicht ist die neue Standarddarstellung beim Öffnen. (Beim Audio Overview
bleibt das Ergebnis eine `.mp3`.) Optional lassen sich ein **Fokus** (Schwerpunkt-
Stichwort) und eine **Länge** (Kurz/Standard/Lang) angeben.

**Eigene Vorlagen** (v9.302.0): Neben den eingebauten Karten steht eine
gestrichelte Karte **„Neue Vorlage“**. Dort definiert man einmal eine eigene
Anweisung (z. B. „Extrahiere Fragestellung, Methodik, Kernergebnisse,
Limitationen“), optional mit Titel-Präfix — die Vorlage erscheint danach als
eigene Karte (Ebenen-Symbol) und lässt sich wie die eingebauten per Klick
anwenden, inklusive Fokus/Länge und der üblichen Beleg-/Zitierpflicht. Über die
Stift-/Papierkorb-Symbole auf der Karte wird eine Vorlage bearbeitet oder
gelöscht (nur durch den Ersteller oder einen Admin; vorhandene Ausgaben bleiben
erhalten). Vorlagen sind systemweit — jeder Nutzer sieht alle Vorlagen in jedem
Projekt-Studio.

Die Checkbox **„Pro Quelle anwenden“** macht aus einer Vorlage eine
Batch-Transformation: Sie läuft dann **einzeln über jedes Dokument** des
Projekts (hochgeladene Dateien, Eingabe-Ordner, Web-Adressen; max. 40 Quellen
pro Lauf, Überzählige werden ausgewiesen) und legt **je Quelle automatisch eine
Wiki-Seite** an (projekt-zugeordnet, Titel „Präfix — Dateiname“). Ein erneuter
Lauf derselben Vorlage aktualisiert diese Wiki-Seiten als neue Version, statt
Duplikate anzulegen. Zusätzlich entsteht im Studio eine kombinierte
Gesamtausgabe mit allen Einzel-Ergebnissen; der Fortschritt wird pro Quelle
angezeigt („Quelle 3/12 verarbeiten“), und ein Lauf lässt sich zwischen zwei
Quellen stoppen.

> Der Audio Overview lässt sich auch **im Chat** erzeugen: in einem geöffneten
> Projekt einfach nach einem „Podcast“ / „Audio-Überblick“ fragen — der Agent nutzt
> dann das Tool `generate_audio_overview` und legt die MP3 in den Artefakten der
> Sitzung ab. Außerhalb eines Projekts geht das nicht (es fehlen die Quellen). Hat ein Projekt noch keine Quellen, ist die Generierung nicht möglich
(erst Dateien/Web-Adressen hinzufügen oder Recherche laufen lassen). Die
Generierung läuft im Hintergrund (~20–40 s) — man kann die Seite verlassen, die
fertige Ausgabe taucht im Studio-Tab auf (er aktualisiert sich von selbst).

Im **Studio**-Tab sind alle erzeugten Ausgaben nach Typ gruppiert aufgelistet
(mit Anzahl der Zitate + Zeitpunkt). Pro Ausgabe: **Öffnen** zeigt das belegte
Dokument in der gestylten **HTML-Ansicht** (das zugehörige Markdown bleibt als
Fallback erhalten), und über das **⋯**-Menü **Umbenennen**, **Neu generieren** (erzeugt
eine neue Ausgabe, die alte bleibt erhalten), **Herunterladen** (lädt das HTML) und **Löschen**
(entfernt Eintrag + Datei; während eine Ausgabe noch generiert, ist Löschen
gesperrt). In der HTML-Ansicht erscheinen Quellenangaben als **kompakte
nummerierte Zitat-Chips** im Text plus ein **Belege-Verzeichnis** am Ende (statt
langer `[Quelle: …]`-Klammern).

> **HTML-Report im Chat:** Verlangt man in **irgendeinem** Chat einen Report als
> HTML (z. B. „erstelle einen HTML-Report", „Due-Diligence-Report als HTML"),
> erzeugt der Agent seit v9.260.0 **automatisch** dasselbe redaktionelle
> Magazin-Layout wie oben (warme Farbwelt, Titelbild, Inhaltsverzeichnis, farbige
> Kennzahl-Kacheln, Zitat-Chips + Belege-Liste, hell/dunkel, druckfertig) — das
> Wort „schön" muss man dafür nicht mehr sagen. Textauszeichnungen (verschachtelte
> Listen, Zitate, Tabellen, durchgestrichener Text, eingebettete Bilder) werden
> dabei vollständig umgesetzt — gleichwertig zur Word- und PDF-Ausgabe. Das
> schlichtere Briefkopf-Layout (Word-artig, mit Logo) kommt nur, wenn man
> ausdrücklich ein On-Brand-Dokument verlangt.

**Neue Quellen finden** (Research-Tab 🔍): Auf der Projektseite gibt es den Tab
**🔍 Research**, um neue Quellen für das Projekt zu finden. Thema eingeben, Modus
wählen, **Recherche starten**. (Es wird das eine aktivierte Such-Tool verwendet —
wie bei der Websuche im Chat; eingestellt unter Einstellungen → Tools.) Zwei Modi:
- **Fast** — schnelle Suche. Es erscheint eine Trefferliste; die gewünschten
  Treffer ankreuzen und **importieren** → sie werden als Projekt-Quellen
  hinzugefügt und beim nächsten Sync ins Gedächtnis gemined (im Chat durchsuchbar).
  Bereits im Projekt vorhandene Treffer sind markiert und gesperrt.
- **Deep** — die KI plant Unterfragen, sucht breit, liest die besten Quellen und
  schreibt daraus einen **strukturierten, belegten Bericht** (im Studio gespeichert,
  inkl. der hochwertigen HTML-Ansicht). Das **Layout passt sich der Art der Frage an**:
  Produktempfehlungen als gerankte Liste mit Vergleichstabelle, ein Vergleich als
  Kriterien-Matrix, eine Anleitung Schritt-für-Schritt, ein Faktencheck als Belege
  dafür/dagegen mit Bewertung. Wo es den Inhalt klarer macht, bettet der Bericht
  **Grafiken** ein: Ablauf-/Zeitdiagramme (Mermaid), Balken-/Linien-/Tortendiagramme
  aus echten Zahlen der Quellen, Bilder aus den Quellen (inkl. Titelbild). Der Fortschritt (Planen → Suchen → Lesen → Schreiben) und das Budget (max. Anzahl
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

### Wo der Schutz überall greift (ab 9.343.0)

Bisher wirkte der Schutz nur im Chat-Turn selbst. Jetzt auch dort:

- **Geplante Aufgaben, Hintergrund-Recherchen und Delegationen** arbeiten in
  derselben geschützten Sicht wie dein Chat. Vorher sahen sie die Klardaten und
  konnten geschützte Namen ungefragt an Suchmaschinen schicken — das ist zu.
- **Alle Wege nach außen sind gegatet**, nicht nur die Web-Suche: E-Mail-Versand,
  Bildgenerierung und angebundene Fremd-Werkzeuge (MCP). Enthält ein Aufruf einen
  geschützten Wert oder einen Platzhalter, wird er **verweigert** statt gesendet.
- **Achtung Bildgenerierung:** `generate_image` schickt den Prompt **immer** an
  einen Cloud-Dienst — auch wenn dein Chat auf einem lokalen Modell läuft.
  Personenbezogene Prompts werden dort abgelehnt. Für Diagramme mit echten Namen
  nimm **Diagramm rendern** (läuft lokal, bekommt die echten Werte).
- **Anhang-Dateinamen werden anonymisiert wie jeder andere Text** (seit v9.394.0).
  Lädst du `KYC_Musterfrau_Ausweis.pdf` hoch, behält die Datei ihren echten Namen
  auf der Platte, aber gegenüber dem Cloud-Modell wird nur der Namensteil im
  Dateinamen unkenntlich gemacht — genau wie Text in der Nachricht. In deinen
  Antworten siehst du wieder den echten Namen, und der Agent kann die Datei ganz
  normal lesen. (Früher wurde die Datei intern in `att_01.pdf` umbenannt; das
  entfällt, weil der Originalname trotzdem über eine Begleitzeile mitging.)
- **E-Mail-Anhänge sind in anonymisierten Sitzungen gesperrt.** Die Datei auf der
  Platte enthält die echten Werte — sie zu versenden wäre ein Klartext-Leck an der
  Prüfung vorbei. Verschicke sie bewusst selbst, wenn du das willst.
- Übersetzungen, Wiki-Einträge, E-Mail- und Verlaufs-Abfragen geben keine
  Klardaten mehr an das Sprachmodell weiter.

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
- **STT-Modell**-Auswahl: welches Transkriptions-Modell die Spracherkennung
  übernimmt (nur Modelle mit der Fähigkeit „Transkription“, z. B. Whisper
  lokal/kostenlos oder Voxtral-mini in der Cloud). Vorausgewählt ist das in
  Einstellungen → Werkzeuge → transcribe_audio hinterlegte Standardmodell.
- Verlaufszeile hat eigene Herunterladen-Knöpfe pro Ausgabeformat.

### Live-Mikrofon-Tab
- Aufnahme klicken. Sprechen. Übersetzung erscheint während des Sprechens.
- **Modus**: Live-Untertitel vs. satzweise Stücke.
- **STT-Modell**-Auswahl wie im Audio-/Video-Tab (gilt pro Aufnahme; greift
  ab der nächsten gestarteten Aufnahme).
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
- **🎯 Ziel** (optional, Goal-Modus) — Nach jedem Durchgang prüft ein
  Prüf-Modell, ob das Ziel erreicht ist; wenn nicht, arbeitet die Aufgabe
  automatisch weiter (bis zur einstellbaren Obergrenze „Max. Iterationen“
  oder dem Timeout). Das Ergebnis vermerkt den Ausgang, z. B.
  „Ziel: erreicht nach 2 Iterationen“.

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
(z. B. „E-Mail entwerfen" → Mensch gibt frei → „via email_send senden").

Im Editor anlegen: Name, Beschreibung, geordnete Schritte mit optionalen
Datei-Uploads und `ask_user_for_file` / `ask_llm`-Blöcken. Aus der
Workflows-Ansicht starten; das **Ausführungen**-Panel zeigt den
Live-Status mit Freigeben-/Abbrechen-Knöpfen.

Während ein Workflow-Lauf läuft, ist das normale Eingabefeld ausgeblendet
(Chatten mitten im Lauf ist nicht vorgesehen). Im Chat-Verlauf zeigt eine
Statuszeile **„Workflow-Lauf in Bearbeitung …"** (bzw. „pausiert") mit direkt
daneben liegenden Knöpfen **Pause** / **Fortsetzen** und **Stopp** (bricht den
Lauf ab). Pause greift kooperativ am nächsten Schritt — ein gerade laufender
Teilschritt wird erst zu Ende geführt.

Während ein Arbeitsschritt läuft, erscheint sein **Fortschritt live** im
Verlauf — genau wie in einem normalen Chat: jeder Werkzeug-Aufruf als Zeile mit
Beschreibung (z. B. „Befehl ausführen: `cp …`", „Im Web suchen nach …",
„Datei lesen: …"), Haken/Spinner und Dauer; ein Klick öffnet das
Aktivitäts-Panel mit den vollen Parametern und dem Ergebnis. Denk-Schritte und
der entstehende Antworttext werden ebenfalls inline angezeigt. Die Statuszeile
nennt zudem, **welcher** Workflow-Schritt gerade läuft (die Instruktion des
Schritts). Nach Abschluss des Schritts bleibt diese Aktivität sichtbar — sie
verschwindet nicht. Erreicht ein Schritt sein Runden-/Zeitbudget, bricht der
Lauf nicht mehr mit „Keine Antwort" ab, sondern behält die Zwischenergebnisse
und weist per Hinweis darauf hin, dass das Ergebnis unvollständig sein kann.

Wartet der Lauf auf eine Datei (`ask_user_for_file`, z. B. das Ausweis-Foto
in der Ausweisprüfung), erscheint an derselben Stelle die **Upload-Karte** —
mit dem Aufforderungstext des Schritts, einem Datei-auswählen-Feld (respektiert
den Dateityp-Filter, etwa nur Bilder) und Drag-&-Drop. Drei Knöpfe:
**Abbrechen** stoppt den gesamten Lauf, **Zurücksetzen** löscht nur die
Dateiauswahl, **Hochladen** sendet die Datei — danach läuft der Workflow
weiter.

### Workflow von der KI erzeugen lassen (v9.290.0)

Statt einen Workflow von Hand zu schreiben, kann Brain-Agent ihn entwerfen —
aus einem gelungenen Chat, einem Plan-Dokument oder einer einfachen
Beschreibung. Vier Einstiegspunkte, alle münden im selben Ablauf:

1. **Composer im Chat** — der Workflow-Knopf (zwei verbundene Kästchen,
   in der Knopfreihe unter dem Eingabefeld neben dem Ziel-Knopf) erzeugt
   einen Workflow aus dem aktuellen Chat. Hat der Chat einen freigegebenen
   Ausführungsplan (Experten-Gremium), wird genau dieser Plan übernommen und
   das Modell, das die Arbeit ausgeführt hat, im Workflow festgeschrieben.
2. **Terminal-Chat**: `/workflow` (optional `/workflow <session_id>`) —
   Fortschritt erscheint als Terminalzeilen; ein fehlerfreier Entwurf wird
   direkt unter einem Vorschlagsnamen gespeichert.
3. **Artefakt-Ansicht** — auf Markdown-Dokumenten, die wie ein
   Ausführungsplan aussehen (Schritt-Überschriften), erscheint ein
   **Workflow**-Knopf: der Plan wird zur Methodik des neuen Workflows.
4. **Workflows-Ansicht → „Neu aus Beschreibung"** — den gewünschten Ablauf
   in eigenen Worten beschreiben, optional Markdown-/Textdateien als
   Kontext anhängen.

**Methode als Skill (v9.294.2):** Im Generieren-Dialog können Sie wählen, ob
die Vorgehensweise als **Skill** ausgelagert wird statt als Inline-Plan: „Neuen
Skill auslagern“ erzeugt zuerst einen Skill und lässt den Workflow ihn per
`agent_step skill="…"` referenzieren; „Vorhandenen Skill referenzieren“ bietet
passende bereits gespeicherte Skills zur Auswahl (semantisch gefunden). So lebt
die Methode an EINER Stelle (im Skill) und mehrere Workflows können sie nutzen —
ändert sich die Methode, genügt eine Skill-Bearbeitung.

Jeder Entwurf öffnet sich **zur Prüfung im Editor** (nichts wird ungefragt
gespeichert; nur der Terminal-Weg speichert fehlerfreie Entwürfe direkt).
Der Editor hat dafür zwei Reiter: **Flow** (das Skript) und **Plan** (die
Methodik als `plan.md` — im Lauf führt der `agent_step`-Schritt diesen Plan
mit Werkzeugen aus, und ein Prüf-Schritt kontrolliert den Report gegen den
Plan). Typisches Ergebnis: eine Fachkraft startet den Workflow, lädt die
angefragte Datei hoch (z. B. ein Ausweisbild) und erhält automatisch einen
Bericht in der Qualität des ursprünglichen Chats. Welches Modell die
Workflows entwirft, legen Admins unter **Einstellungen → Service-Modelle →
Workflow-Generator** fest.

### Skill aus einem Chat erstellen (v9.294.0)

Nah verwandt mit dem Workflow-Generator, aber das Ergebnis ist ein **Skill** —
eine wiederverwendbare Anleitung, die der Agent bei ähnlichen Aufgaben künftig
von selbst heranzieht (er lädt sie über `use_skill`). Der **Skill-Knopf**
(Doktorhut 🎓, in der Knopfreihe unter dem Eingabefeld neben dem
Workflow-Knopf) destilliert aus dem aktuellen Chat — oder aus dessen
freigegebenem Ausführungsplan (Experten-Gremium) — eine Anleitung mit Auslöser,
Voraussetzungen, nummerierten Schritten, Fallstricken und einem Beispiel. Es
ist **keine Abschrift** des Gesprächs, sondern die herausgelöste Methode.

Während der Erzeugung zeigt das Fenster eine **Fortschrittsanzeige** (9.294.4):
ein Balken plus eine Checkliste der vier Stufen — Quellmaterial sammeln, Skill
verfassen, Entwurf validieren, Fertigstellen — mit Häkchen für erledigte und
einem Spinner für die laufende Stufe; darunter das verwendete Modell und
etwaige Hinweise.

Der Entwurf öffnet sich **zur Prüfung**: Kurzname, Titel, Beschreibung und der
Skill-Inhalt sind editierbar. Vor dem Speichern legen Sie die **Sichtbarkeit**
fest — **privat** (nur Sie), **Team** oder **alle** —, genau wie beim Teilen
eines Chats. Ein gespeicherter Skill gehört Ihnen; über den Teilen-Dialog
lässt sich die Freigabe später ändern oder der Skill übertragen. Unterschied
zum Workflow: ein Workflow ist ein ausführbares Skript, ein Skill ist Wissen,
das der Agent beim Arbeiten berücksichtigt. Welches Modell die Skills entwirft,
legen Admins unter **Einstellungen → Service-Modelle → Skill-Generator** fest.

### Einen Lauf ansehen (v9.290.2)

Ein Workflow-Lauf öffnet sich wie eine normale Chat-Sitzung: Klick auf eine
Zeile im **Ausführungen**-Panel öffnet den Lauf in der Chat-Ansicht. Das
**Ergebnis des Laufs** — die Antwort, die der Agent (per `agent_step`)
erarbeitet hat, samt der erzeugten Dateien — erscheint im Hauptbereich als
**normale Chat-Nachricht** in gewohnter Darstellung (gleiche Schrift,
Farben, Abstände, Markdown wie in jedem Chat) und **aktualisiert sich
live**, solange der Lauf läuft. Nach dem Lauf schreibt man direkt darunter
weiter — Folgefragen an den Agenten funktionieren wie in jedem Chat.
(Technischer Hinweis: das Schritt-für-Schritt-Werkzeugprotokoll ist NICHT
mehr im Hauptbereich, sondern im **Protokoll**-Reiter.)

Die Detail-Informationen liegen in eigenen Reitern der **rechten
Seitenleiste** (öffnen über den Seitenleisten-Knopf oben rechts):

- **Statistik** — Agent, Modell, Start/Ende, Dauer, Kosten, Status,
  Ausführungs-ID sowie die Aktionen **Lauf abbrechen** (nur live),
  **Protokoll herunterladen** (Markdown), **In Chats speichern** und
  **← Workflows** (zurück zur Liste).
- **Quellcode** — der `.flow`-Quelltext des Workflows.
- **Protokoll** — das Schritt-für-Schritt-Ausführungsprotokoll (jeder
  Werkzeug-Aufruf mit Ein- und Ausgabe, Fehler, Rückgabewert).
- **Dateien** (der normale Artefakt-Reiter) — **alle** vom Lauf erzeugten
  Ausgabedateien, ansehen und herunterladen wie in jedem Chat. Das umfasst
  auch Dateien, die ein Schritt selbst per Skript geschrieben hat (z. B.
  Zwischenbilder oder Mess-Protokolle) — der gesamte Arbeitsordner des Laufs
  wird erfasst, nicht nur die direkt vom `.flow` geschriebenen Dateien
  (9.291.3). Die hochgeladenen **Eingabedateien** erscheinen — wie bei einem
  normalen Chat — im **Anhänge**-Reiter (ansehen/herunterladen; Bilder mit
  Vorschau) und zusätzlich als **Referenz** im Referenzen-Reiter (9.291.4).

Bricht ein Lauf **vorzeitig** ab (z. B. weil ein Anbieter ein Nutzungslimit /
Rate-Limit meldet), steht oben im Verlauf ein deutlicher Hinweis, und die bis
dahin erzeugten Teilergebnisse und Dateien bleiben gültig und nutzbar
(9.291.3).

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

### Jupyter-Notebooks (.ipynb, v9.358.0)

Analyse-Ergebnisse kann der Assistent als **Jupyter-Notebook** liefern —
Bericht (Markdown), Rechenweg (Code) und Ergebnisse (Tabellen, Diagramme)
in EINER Datei. Das Artefakt-Panel rendert die Zellen direkt: Text
formatiert, Code mit Syntax-Hervorhebung, eingebettete Diagramme als Bild,
Tabellen sicher in einem isolierten Rahmen. Jede Änderung erzeugt wie
gewohnt eine neue Version — ältere Stände bleiben über die Versions-Auswahl
einzeln aufrufbar, jede Version ist ein prüfbarer Stand. Notebooks in
Projekt-Ordnern werden wie andere Dokumente durchsuchbar gemacht (Inhalt
der Zellen landet im Projektwissen). Hinweis: die Zellen werden angezeigt,
nicht in der App ausgeführt.

### Nachweiskette (Provenance, v9.357.0)

Jede Zahl führt auf ihren Code zurück: hat ein **Python- oder R-Skript**
eine Datei erzeugt (Diagramm, CSV-Ergebnis, Auswertung), zeigt das
Artifact-Panel unter der Kopfzeile **Herkunfts-Chips** für die gewählte
Version:
- **Code** — das erzeugende Skript (z. B. `script_3.py`); ein Klick öffnet
  den Skript-Quelltext als eigenes Artefakt. So ist prüfbar, WIE eine Zahl
  berechnet wurde.
- **Env** — die Rechenumgebung (Python-/Paketversionen bzw. R-Version) zum
  Zeitpunkt der Erzeugung; vollständig per Tooltip.
- **Anfrage N** + **Zeitstempel** — welcher Chat-Turn die Version erzeugte
  und wann.

Dateien, die der Agent direkt schrieb (`write_file`) oder die per
Shell-Befehl entstanden, tragen bewusst KEINE Code-/Env-Chips — lieber
ehrlich leer als geraten. Ältere Versionen (vor v9.357.0) zeigen ebenfalls
keine Chips. Relevanz: Modellvalidierung/Prüfung (BCBS 239 / MaRisk
AT 4.3.2) — Ergebnisdatei, erzeugender Code, Datenstand-Zeitpunkt und
Environment hängen an einer Stelle zusammen.

### Persistente Analyse-Umgebung (Kernel, v9.359.0)

Bei Datenanalysen im Chat kann der Assistent eine **dauerhafte Python-
oder R-Arbeitsumgebung** („Kernel") nutzen: ein großer Datensatz wird
EINMAL geladen und bleibt für alle Folgefragen im Speicher — jede weitere
Auswertung antwortet in Sekundenbruchteilen, statt die Daten neu zu laden.
Sie müssen dafür nichts tun; der Assistent entscheidet selbst, wann sich
die dauerhafte Umgebung lohnt (typisch: „lade die Positionsdaten und dann
schauen wir uns verschiedene Auswertungen an").

- **Statusleiste**: läuft eine Umgebung, erscheint unten ein Badge
  **„Kernel · py/R · Speicher"**. Der Punkt daneben zeigt grün (bereit)
  bzw. pulsierend (rechnet gerade). Per Tooltip sehen Sie Laufzeit und
  Anzahl der Ausführungen.
- **Neustart-Knopf** (im Badge): verwirft alle geladenen Variablen und
  startet die Umgebung frisch — nützlich, wenn eine Analyse „hängen"
  bleibt oder Sie sauber neu beginnen möchten.
- **Diagramme** aus der Umgebung (z. B. matplotlib oder R-`plot()`)
  erscheinen automatisch als Datei im Artifacts-Panel — inklusive
  Nachweiskette (Herkunfts-Chip `kernel#N` + Environment, siehe oben).
- **Automatisches Aufräumen**: nach etwa 20 Minuten ohne Nutzung beendet
  sich die Umgebung selbst; die nächste Analyse startet transparent eine
  neue (geladene Daten müssen dann erneut geladen werden). Ein
  Server-Neustart beendet alle Umgebungen ebenfalls.
- **Grenzen**: pro Chat-Sitzung EINE Umgebung (Python ODER R, max. 3
  gleichzeitig auf dem Server); geplante Aufgaben und Hintergrund-Agenten
  nutzen bewusst weiterhin die einmaligen Skript-Werkzeuge.

### Design-Modus (HTML-Artefakte per Klick verfeinern, v9.351.0)

Auf **HTML-Artefakten** (One-Pager, Reports, Landing Pages) gibt es in der
Aktionsleiste des Artifact-Panels den Knopf **„Design"** (Stift-Symbol). Er
schaltet die Vorschau in den Design-Modus:

1. **Element anklicken** — beim Überfahren wird das Element blau umrandet,
   ein Klick öffnet eine Kommentar-Blase (mit dem CSS-Selektor des Elements
   als Referenz).
2. **Änderungswunsch notieren** — z. B. „Headline kürzer, maximal eine
   Zeile" → **Kommentar hinzufügen**. Der Wunsch erscheint als nummerierter
   Pin auf der Vorschau und als Chip in der Leiste unten. Beliebig viele
   Kommentare sammeln; einzelne per ✕ entfernen.
   - **Bild anhängen (v9.364.0)**: In der Kommentar-Blase gibt es den Knopf
     **„Bild anhängen"** — z. B. „Füge diesen Screenshot an dieser Stelle
     ein" plus das Bild selbst. Auch ein Bild ohne Text ist erlaubt (dann
     gilt „hier einfügen"). Das Bild erscheint als Mini-Vorschau in Blase
     und Chip; beim Anwenden wird es direkt in die HTML-Datei eingebettet
     (das Dokument bleibt selbständig — Ansicht und alle Exporte
     funktionieren ohne externe Dateien). Angehängte Bilder durchlaufen
     denselben Datenschutz-Scan wie normale Chat-Anhänge.
3. **„N Kommentare anwenden"** — alle Wünsche gehen als *eine* normale
   Chat-Nachricht an den Agenten. Er bearbeitet die Datei, und die Vorschau
   aktualisiert sich automatisch mit der neuen Version.

Wissenswertes:
- Der Design-Modus funktioniert nur auf der **aktuellen Version** des
  Artefakts (auf älteren Versionen erscheint ein Hinweis).
- Nach jeder Aktualisierung werden offene (nicht angewendete) Pins
  verworfen — die Vorschau hat sich geändert, die Anker wären unzuverlässig.
- Links im Entwurf sind im Design-Modus deaktiviert (Klick = kommentieren,
  nicht navigieren). Zum normalen Ansehen den Design-Modus einfach wieder
  ausschalten.
- Es zählt als normaler Chat-Turn (Verlauf, Kosten, Abbrechen wie üblich).

### Design-System pro Projekt (markenkonsistente Entwürfe, v9.352.0)

Auf der **Projekt-Seite** gibt es die Sektion **„Design-System"**: Farben
(hex + Rolle, z. B. „#0F3D68 · Primär"), Schrift für Überschriften und
Fließtext, Logo-URL, Tonalität und optional ein CSS-Snippet. Gespeichert
wird pro Projekt; leer = neutrale Gestaltung.

**Wann greift es?** Bei **Design-Turns** — deterministisch, ohne Raten:
1. Solange der **Design-Modus** auf einem HTML-Artefakt aktiv ist (also
   auch beim „Kommentare anwenden"), und/oder
2. wenn der **Paletten-Knopf** im Eingabefeld aktiviert ist (violett =
   an) — der explizite Einstieg, um einen *neuen* Entwurf gleich im
   Marken-Look erstellen zu lassen (z. B. „Erstelle einen One-Pager …").

Das Design-System wird dem Agenten dann als verbindliche Vorgabe zur
Nachricht mitgegeben (nur für diesen Turn; es landet nicht im Verlauf und
gilt nicht für normale Frage-Antwort-Turns im Projekt).

**Automatisch vorbefüllen:** „Aus Website generieren…" (Adresse der
Firmen-Website angeben — Farben/Schriften werden aus deren HTML/CSS
destilliert) oder „Aus CI-Dokument generieren…" (Styleguide/CI-PDF
hochladen). Das Ergebnis füllt nur das Formular — geprüft und übernommen
wird erst mit **Speichern**.

### Export: PDF, Word & PowerPoint (v9.353.0, DOCX seit v9.360.0)

Auf jedem **HTML-Artefakt** gibt es in der Aktionsleiste den Knopf
**„Export ▾"** mit vier Wegen:

- **HTML herunterladen** — die Datei selbst, selbständig lauffähig.
- **Als PDF exportieren** — druckgenau im echten Browser gerendert
  (Chromium). Ideal für One-Pager und Reports.
- **Als DOCX exportieren** — ein **echtes, bearbeitbares Word-Dokument**:
  Überschriften werden Word-Formatvorlagen, Tabellen bleiben Tabellen,
  Listen und Bilder kommen mit. Das visuelle Layout (Farben, Raster,
  Hero-Flächen) wird dabei bewusst vereinfacht — Word ist ein
  Fließtext-Format; wer es pixelgenau braucht, nimmt PDF.
- **Als PPTX exportieren** — nur für **Foliendecks**: jede
  `<section data-slide>` im Entwurf wird eine Folie. Die Folien sind
  pixelgenaue **Bild-Folien** — sie sehen in PowerPoint/Keynote exakt aus
  wie die Vorschau, sind dort aber **nicht als Text editierbar**.

Wissenswertes:
- Design-Entwürfe werden automatisch exportfähig angelegt (der Agent kennt
  die Folien-Konvention bei Design-Turns).
- Ist der Entwurf kein Deck (keine `data-slide`-Abschnitte), erklärt die
  PPTX-Fehlermeldung, wie man den Agenten das Deck anlegen lässt.
- PDF/PPTX brauchen den Render-Dienst (crawl4ai). Ist er auf diesem Server
  nicht eingerichtet, kommt eine klare Fehlermeldung — der HTML-Download
  funktioniert immer. DOCX braucht keinen Render-Dienst.
- **Auch PDF-Artefakte** haben den Export-Knopf (seit v9.361.0): dort gibt
  es genau einen Weg — **Als DOCX exportieren**, layout-treu konvertiert
  (Textboxen, Tabellen, Bilder, Spalten werden echte Word-Elemente). Bei
  gescannten (Bild-)PDFs schlägt die Konvertierung mit einem klaren Hinweis
  fehl — solche PDFs vorher per OCR durchsuchbar machen.

---

## Wiki

Seitenleiste → **Wiki**. Dein durchsuchbares, editierbares Wissens-Wiki — und
zugleich das Langzeit-Gedächtnis des Agenten: jede gespeicherte Seite wird in
MemPalace gespiegelt, sodass der Agent sie bei der Suche findet.

Für maximale Arbeitsfläche fährt die linke Hauptnavigation im Wiki automatisch
auf die schmale Icon-Leiste ein (wie im Code-Modus); beim Verlassen des Wikis
kehrt sie in ihren vorherigen Zustand zurück.

**Aufbau:**
- **Links** der Seitenbaum. Der obere Filter schaltet den Sichtbarkeitsbereich
  (**Alle** / **Meine** / **Team** / **Alle sichtbar**). Das **Gruppieren nach**-
  Menü ordnet die Seiten: **Manuell** (der editierbare Baum mit Unterseiten +
  Drag&Drop), **Thema**, **Projekt**, **Quelle** (Chat / Studio / …), **Erstellt
  von**, **Geändert von**. Jede Zeile zeigt ein Bereichs-Symbol, einen kleinen
  grünen Punkt (in MemPalace durchsuchbar) und ihre Tags.
- **Suche im Baum:** Das Suchfeld **„Seiten durchsuchen…"** filtert den Baum
  sofort nach freiem Text — es durchsucht **Titel, Tags UND den Seiteninhalt**
  (Volltext), sodass auch Seiten gefunden werden, deren Suchbegriff nur im Text
  steht. (Für sinngemäße/semantische Treffer über alle Wikis gibt es zusätzlich
  die globale Lupe im Seitenleisten-Kopf.)
- **Auf-/Zuklappen:** Seiten mit Unterseiten haben im *Manuell*-Baum ein
  Pfeil-Symbol zum Auf-/Zuklappen; der Baum startet **standardmäßig
  eingeklappt** (nur oberste Ebene). Öffnest du eine tief verschachtelte Seite,
  klappen ihre übergeordneten Ebenen automatisch auf.
- **Breite verstellbar:** Die Trennlinie zwischen Baum und Editor lässt sich
  ziehen, um die Baum-Spalte breiter/schmaler zu machen (die Breite bleibt
  gespeichert).
- **Tags & Filter:** Seiten bekommen beim Erstellen/Aktualisieren automatisch
  Themen-Tags (KI-vorgeschlagen); du kannst eigene Tags ergänzen oder entfernen
  (deine Tags bleiben erhalten). Die Tag-Leiste oben filtert den Baum; ein Klick
  auf ein Tag filtert ebenfalls.
- **Bearbeiten im Baum:** Beim Überfahren einer Zeile erscheinen **Umbenennen**
  und **Löschen**. Im Modus *Manuell* lassen sich Seiten per **Drag&Drop** unter
  eine andere Seite ziehen (verschachteln/umordnen).
- **Zur Quelle springen:** Aus einem Chat/Studio/… erzeugte Seiten zeigen unter
  dem Titel einen Link (z. B. „Zum Chat"), der das Ursprungsobjekt öffnet.
- **Rechts** der Editor mit zwei Ansichten, oben rechts über Symbol-Knöpfe
  umschaltbar: **Ansicht** (Auge — gerendertes Markdown) ↔ **Markdown** (Stift —
  Roh-Editor mit Zeilennummern, folgt dem hellen/dunklen Design). Titel oben ist
  direkt editierbar; die Werkzeugleiste daneben ist rein icon-basiert.

**Aktionen:** **+ Seite** legt eine neue Seite an (Bereich richtet sich nach dem
aktiven Filter; optional als Unterseite der offenen Seite). Der **Speichern**-
Knopf (Disketten-Symbol) schreibt eine neue Version. Der **Versionen**-Knopf
(Uhr-Symbol) zeigt den Verlauf — jede Bearbeitung ist eine unveränderliche
Version; **Ansehen** öffnet eine schreibgeschützte Vorschau, **Aktivieren** macht
eine alte Version wieder zur aktuellen (nur die aktuelle Version ist editierbar
und durchsuchbar). Der **Löschen**-Knopf (Papierkorb-Symbol) entfernt die Seite;
Unterseiten bleiben erhalten (rücken eine Ebene hoch). Alle Kopf-Knöpfe sind
Symbol-Knöpfe mit Tooltip beim Überfahren.

**Bereiche (Scopes):** *Meine* Seiten sehen nur du, *Team*-Seiten dein Team,
*globale* Seiten alle. Aus Chats, Studio-Ergebnissen, geplanten Aufgaben und
Workflows automatisch erzeugte Seiten tragen eine Quell-Markierung (↩) und
werden bei einer Änderung der Quelle als neue Version fortgeschrieben statt
doppelt angelegt.

**Erzeugen & Medien (Seitenkopf — alle als Symbol-Knöpfe mit Tooltip):**
- **Lautsprecher-Symbol** liest die Seite vor (Sprache wird automatisch erkannt).
- **Listen-Symbol (Zusammenfassung)** erzeugt per KI eine kompakte
  Zusammenfassung als Unterseite (optional inklusive aller Unterseiten).
- **Kopfhörer-Symbol (Podcast)** erzeugt eine zweistimmige Audio-Übersicht (MP3)
  als Unterseite.
- **Büroklammer-Symbol (Medien)** lädt Bild / Audio / Video hoch und fügt es in
  die Seite ein; in der Ansicht wird es als `<img>`/`<audio>`/`<video>`
  dargestellt.

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

> **Automatische Fachsuchen (ab 9.288.0):** Neben der allgemeinen Websuche kann
> der Assistent selbstständig gezielt suchen — nach **wissenschaftlichen
> Arbeiten** (arXiv, PubMed, Google Scholar), nach **Programmier-/Technikwissen**
> (Stack Overflow, MDN, GitHub), nach **Bildern** und nach **Nachrichten**. Er
> wählt die passende Suche je nach Frage selbst; man muss nichts umschalten.
> Unter **Einstellungen → Server → Websuche** lässt sich jede dieser Suchen
> einzeln ein-/ausschalten, und der Zustand der Suchmaschinen wird dort je
> Kategorie angezeigt (automatische Prüfung alle 4 Stunden, plus „Jetzt testen").

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

**YouTube & Audio-Links** (v9.307.0): Auch YouTube-Videos und direkte
Audio-Dateien (z. B. MP3-Podcasts) funktionieren als Quellen — der Ton wird
automatisch heruntergeladen und **lokal transkribiert**; der Assistent
arbeitet dann mit dem gesprochenen Inhalt. Das gilt überall, wo Web-Adressen
genutzt werden: im Chat, im Websuche-Korb und bei den Web-Adressen eines
Projekts (Limit: ~80 Minuten Audio pro Video).

(Anderer Mechanismus als die **Web-Adressen** eines Projekts, die dauerhaft
ins Projektgedächtnis eingespeist werden.)

---

## Quellen-Pinning (Projekt-Dokumente anheften, v9.305.0)

In **Projekt-Chats** erscheint im Composer ein **Pin-Symbol** 📌: Darüber
lassen sich einzelne **Projekt-Dokumente anpinnen** (hochgeladene Dateien,
Dateien aus Eingabe-Ordnern, eingelesene Web-Adressen). Angepinnte Dokumente
werden mit ihrem **Volltext in jede Anfrage dieses Chats** eingespeist — das
Modell muss sie nicht erst über die Projektsuche finden und arbeitet garantiert
mit dem ganzen Dokument. Das ist das Websuche-Korb-Prinzip für Projektdateien:

- **Pro Chat gespeichert** — die Pins bleiben über Neuladen/Gerätewechsel
  erhalten und gelten nur für diesen Chat. Das Pin-Symbol färbt sich, solange
  Pins aktiv sind.
- **Nichts landet im Verlauf** — der Volltext wird nur für die jeweilige
  Anfrage mitgegeben (wire-only) und nie in die Chat-Historie geschrieben;
  unter jeder Antwort zeigt eine Zeile „Angepinnte Quellen dieser Anfrage“,
  welche Dokumente einflossen.
- **Grenzen**: max. 12 Quellen pro Anfrage, je bis 60.000 Zeichen (Überhang
  wird ausgewiesen, nie still gekappt). Viele große Pins erhöhen Kosten und
  Antwortzeit — für breite Fragen über viele Dokumente bleibt die normale
  Projektsuche (MemPalace) der bessere Weg; Pinning ist für „arbeite genau
  mit diesen Unterlagen“.
- Anders als bei der Websuche werden **keine Werkzeuge gesperrt** — das Modell
  darf zusätzlich suchen.

---

## Datenquellen im Chat und Projekt (Datenquellen v2, 9.368–9.375)

Vom Administrator konfigurierte **externe Datenquellen** (PostgreSQL-,
MS-SQL-Server-Datenbanken und REST-APIs) kann der Assistent direkt abfragen —
aber nur dort, wo Sie sie ausdrücklich freigeben. In **1–2 Klicks**:

- **Im Projekt**: Projekt-Einstellungen → Sektion **„Datenquellen"** —
  Quelle anhaken, optional per Klick auf „Tabellen wählen…" auf einzelne
  Tabellen (bei REST-Quellen: Pfade) einschränken. Gilt für alle Chats des
  Projekts, auch in Code-Projekten.
- **Im projektlosen Chat**: rechtes Panel → Tab **„Datenquellen"**
  (Zylinder-Symbol) — gleiche Auswahl, gespeichert pro Unterhaltung
  (überlebt Neuladen und Gerätewechsel). In Projekt-Chats zeigt dieser Tab
  nur an, was im Projekt konfiguriert ist.

Ohne Freigabe verweigert der Assistent jede Abfrage mit einem Hinweis auf
genau diese beiden Orte — es gibt kein stilles „alle Quellen erlaubt". Eine
Tabellen-Einschränkung wird hart durchgesetzt (Abfragen auf andere Tabellen
werden abgelehnt); die Schema-Übersicht (`information_schema`) bleibt zur
Orientierung lesbar. Ob eine Quelle **read-only** oder **read/write** ist,
bestimmt der Administrator pro Quelle — auf einer read-only-Quelle werden
Schreibversuche immer abgelehnt.

Zwei Komfort-/Schutz-Merkmale sehen Sie dabei im Alltag:
- **Steckbrief** (📄-Symbol neben der Quelle, v9.374.0): hat der
  Administrator Nutzungswissen zur Quelle hinterlegt (was die Tabellen und
  Felder bedeuten, wie man korrekt abfragt), kennt der Assistent es
  automatisch — er findet ohne Erkundungs-Abfragen direkt die richtige
  Abfrage. Antworten kommen dadurch schneller und treffsicherer.
- **Datensparsamkeit** (v9.375.0): bei entsprechend konfigurierten Quellen
  erreichen Roh-Datenzeilen das Sprachmodell **gar nicht** — es sieht nur
  Spaltennamen und Zeilenzahlen, exportiert die Daten einmal als Datei und
  rechnet Auswertungen lokal auf dem Server. Ergebnis: auch Analysen über
  große, sensible Datenbestände ohne dass Massendaten in den Chat-Kontext
  fließen.

---

## Aktivität (Tool-Aufrufe) & Hintergrundaufgaben

Der **Aktivität**-Tab im rechten Panel zeigt **alle Tool-Aufrufe dieses Chats** an
einem Ort — sowohl die synchronen (während eines normalen Turns, z.B. `web_fetch`,
`read_document`) als auch die abgekoppelten **Hintergrundaufgaben**. Chronologisch
sortiert (neueste oben), in zwei Bereichen **Laufend** / **Abgeschlossen**. Jeder
Eintrag trägt seinen Typ (Tool-Name oder „Hintergrundaufgabe") und asynchrone
Einträge zusätzlich ein **Hintergrund**-Badge.

Außerdem erscheinen hier — im selben Karten-Stil — die **Turn-Steuerungs-Ereignisse**
des Chats: eine **eingefügte Klarstellung** (erst „Wartet auf das nächste
Rundenende“, nach dem Einfügen „In Runde N übernommen“) und die
**Goal-Modus-Aktivität** (bei aktivem Ziel während einer Antwort die *geplante*
Ziel-Prüfung als zukünftige Aufgabe, dann die laufende Prüfung, ihr Ergebnis und
jede zusätzliche Iteration mit der Judge-Anweisung). Die Ergebnis-Karte der
Ziel-Prüfung zeigt die Begründung des Prüf-Modells und bei „noch nicht
erreicht" zusätzlich die Anweisung, die der Assistent für die nächste
Iteration erhalten hat.

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

**Subagenten live zusehen (Code-Projekte):** In einem Code-Projekt öffnet sich
beim Start von Hintergrundaufgaben automatisch der **✦-Tab „Subagenten"** im
unteren Bereich — EIN Tab für alle: pro Aufgabe eine **Karte** mit Status-Punkt
(grün pulsierend = läuft), Titel, **ausführendem Modell**, Token-Zählern und
**Stopp**-Knopf. Die Karte zeigt eine kompakte Live-Zeile (aktuelles Werkzeug
bzw. letzter Text); ein Klick auf den Kopf klappt das **volle Live-Transcript**
auf (Anfrage, Antworttext, gedimmtes Nachdenken, Tool-Aufrufe mit ✓/✗). Der
Tab-Titel trägt einen **Zähler** und pulsiert, solange etwas läuft — so sehen
Sie auf einen Blick, dass noch gearbeitet wird, auch wenn der Chat selbst
schon geantwortet hat. Das **Schließen einer Karte oder des Tabs stoppt nichts**
— nur der Stopp-Knopf. Nach einem **Seiten-Reload** kommen die Karten wieder
(laufende live, fertige aus dem gespeicherten Verlauf). Zusätzlich erscheinen
laufende Subagenten in der **linken Chat-Liste als ✦-Zeilen unter ihrem Chat** —
beim Überfahren einer ✦-Zeile erscheint dort ein **Stopp-Symbol**, mit dem Sie
den einzelnen Subagenten direkt aus der Liste stoppen (das Teilergebnis bleibt
erhalten). Im Terminal-Chat zeigt die grüne Subagenten-Zeile am Log-Ende
zusätzlich **„alle stoppen"** — ein Klick bricht sämtliche laufenden
Subagenten dieses Chats auf einmal ab.
Ist die Aufgabe fertig, verarbeitet der Chat das Ergebnis automatisch in einem
Folge-Turn — ein offener Terminal-Chat springt dafür von selbst an.

**Stopp gilt für Turn + Subagenten:** Bricht man eine **laufende Antwort** ab
(Stopp-Knopf im Composer bzw. Esc im Terminal-Chat), werden die Subagenten, die
**genau diese Antwort** gestartet hat, mitgestoppt. Subagenten aus früheren
Antworten laufen weiter — sie haben ihre eigenen Stopp-Knöpfe.

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

**Wenn etwas schiefgeht, reagiert der Assistent selbst:** Scheitert eine
Teilaufgabe (API-Fehler, Zeitlimit — jede Aufgabe wird nach spätestens 1 Stunde
beendet —, oder eine leere Antwort), entscheidet das Modell bei der Auslieferung,
was sinnvoll ist: die Teilaufgabe **genau einmal neu starten** (bei Bedarf auf
einem anderen Modell), sie **direkt selbst erledigen** oder den Ausfall
transparent berichten und mit den vorhandenen Ergebnissen weiterarbeiten. Mehr
als ein Neustart pro Aufgabe ist technisch nicht möglich — Endlosschleifen sind
ausgeschlossen. Eine Aufgabe, die **Sie selbst gestoppt** haben, startet der
Assistent dagegen nie ungefragt neu: Er nutzt das Teilergebnis und fragt nach,
wenn das fehlende Ergebnis zwingend gebraucht wird.

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
- **Server** — **Auto-Routing**, Ports, Monitore für **Web Search
  (SearXNG)** und **crawl4ai** (Status/PID/Uptime/Health/Breaker + Neustart;
  SearXNG zusätzlich mit Pro-Engine-Tabelle und „Jetzt testen"; der
  Sidecar-Monitor wurde mit dem Sidecar selbst entfernt — die LLM-Schleife
  läuft seit 9.247.0 im Server-Prozess). Die
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
  Direkt darunter: **Experten-Gremium (MoA)** (9.268.0, Matrix seit 9.269.0,
  Beitrags-Modi + Umbenennung 9.271.0) — schaltet das
  „🧬 Experten-Gremium"-Modell im Verfasser frei und konfiguriert es über
  eine **Matrix „Modell × Aufgabentyp"**: Zeilen sind die aktivierten
  Modelle — Cloud UND lokal (lokale mit „[lokal]"-Kennzeichnung; seit
  9.289.1, vorher cloud-only), Spalten die Aufgabentypen des Klassifikators (Recherche,
  Analyse, Berichte, …). Pro Spalte haken Sie an, welche Modelle bei diesem
  Aufgabentyp antreten; eine Spalte ohne Häkchen heißt: für diesen Typ tritt
  kein Gremium an (die Anfrage verhält sich wie „Smart (Cloud)"). Die zweite
  Kopfzeile wählt je Spalte den **Beitrags-Modus**: „Antwort" (vollständiger
  Entwurf), „Ansatz" (nur Herangehensweise, das antwortende Modell führt
  aus — Voreinstellung bei Recherche/Orchestrierung/Agentisch) oder
  **„Plan-Delegation"** (9.284.0): der Orchestrator schreibt aus den
  Ansätzen EINEN Ausführungsplan, der Plan wird klassifiziert und ein
  günstigeres, dazu passendes Modell führt ihn mit den Tools aus (es wird
  für die Session gepinnt; der Plan-Aufruf erscheint im Kosten-Popover als
  „moa_planner"). Die Checkbox **„Plan-Delegation nur bei Web-Bezug
  (empfohlen)"** (9.284.2, Standard an) begrenzt die Delegation auf
  Anfragen, für die der Klassifikator Web-Recherche vorsieht — interne
  Dokument-/Wissensfragen führt der Orchestrator selbst aus (dort
  verschlechtert die Delegation die Antwortqualität nachweislich, bei
  Web-Recherchen senkt sie die Kosten um ~2/3). Das Modell,
  das die Anfrage gerade selbst beantwortet, wird automatisch ausgelassen.
  Die Checkbox **„Lokale Modelle als Plan-Executor zulassen (experimentell)"**
  (9.289.1, Standard aus) erlaubt, dass bei „Plan-Delegation" ein LOKALES
  Modell als ausführendes Modell gewählt wird — die automatische Wahl
  bevorzugt weiter Cloud, ein lokales Modell wählen Sie im Plan-Prüfungs-
  Dropdown des Chats. Standardmäßig aus, weil lokale Modelle Tool-Runden
  langsam abarbeiten und tool-schwächere Modelle mandatierte Recherchen
  überspringen können; für Tests gedacht.
  Die dritte
  Kopfzeile wählt je Spalte den **Orchestrator** (9.274.0): „Auto (Smart)" =
  das per Auto-Routing gewählte Modell führt die Beiträge zusammen
  (Standard), oder ein FESTES Modell, das für diesen Aufgabentyp immer die
  finale Antwort erstellt (es wird automatisch aus dem Gremium der Spalte
  ausgelassen, damit es sich nicht selbst berät).
  Dazu: max. Experten je Anfrage (1–5), max. Tokens je Beitrag und Timeout je
  Experte. Das Feld **„Ergebnis-Prüfung: max. Nachbesserungen"** (9.286.0,
  0–5, Standard 2) steuert die Post-Verifikation der Plan-Delegation: nach dem
  Executor-Lauf prüft der Orchestrator im interaktiven Chat, ob die Antwort
  Plan und Anfrage erfüllt, und fordert bis zu so vielen Nachbesserungen
  gezielt an (0 = nur protokollieren, ohne erneuten Versuch). Komplett leere
  Matrix oder deaktiviert = der 🧬-Eintrag verschwindet aus dem Verfasser.
- **Provider** — OpenAI-kompatible Provider hinzufügen/bearbeiten/testen
- **Nodes** — verteilte Compute-Peers
- **Datenquellen** (v9.363.0; Datenquellen v2 9.368–9.375) — externe
  Datenquellen für die Analyse-Tools `db_query`/`rest_query`: **PostgreSQL**,
  **MS SQL Server** (bank-erprobter ODBC-Driver-17-Weg) und **REST-APIs**
  (feste Base-URL — der Assistent erreicht ausschließlich Pfade darunter),
  komplett per GUI statt config.json: Quellen anlegen/bearbeiten/löschen
  (Name, Typ, DSN **oder** Env-Variable bzw. Base-URL + Auth, Timeouts; das
  Passwort/Secret wird nur maskiert angezeigt — beim Bearbeiten leer lassen
  heißt „unverändert"), wirksam **ohne Server-Neustart**. Pro Quelle:
  **Zugriffsmodus** read-only (Standard; nur SELECT, Verbindung zusätzlich
  schreibgeschützt) oder **read/write** (der Assistent darf gezielt
  schreiben — INSERT/UPDATE/DELETE, nie Schema-Änderungen; die Rechte des
  hinterlegten Datenbank-Kontos sind die letzte Instanz), **Kontext-Preview**
  (none/head/full — „none" hält Roh-Datenzeilen komplett aus dem
  Sprachmodell-Kontext, Analysen laufen dann über lokale Datei-Auswertung)
  und ein **Steckbrief** (Markdown-Nutzungswissen zur Quelle; der Knopf
  „Steckbrief generieren" liest das Live-Schema als kuratierbares Gerüst
  ein; für umfangreiche Doku lässt sich zusätzlich ein Quellen-Skill
  verknüpfen). Dazu die **Zugriffs-Steuerung**: ein globaler
  Ein/Ausschalter (aus = für alle gesperrt, auch Administratoren) und
  additive Freigaben nach **Benutzertyp** (Administratoren immer; Poweruser
  und Benutzer zuschaltbar), nach **Team** und nach **einzelnem Benutzer**.
  Ohne gespeicherte Policy dürfen nur Administratoren zugreifen. **Nutzbar**
  wird eine Quelle erst durch die Freigabe im Kontext: Projekt-Einstellungen
  → Datenquellen bzw. Right-Panel-Tab „Datenquellen" (siehe Abschnitt
  „Datenquellen im Chat und Projekt").
- **Modelle** — Pro-Modell-Konfiguration (warmup, thinking, profile, cost).
  Bei den Kosten gibt es neben „Kosten ein/aus ($/M)" das Feld **„Kosten
  cached ($/M)"** — der Preis für Tokens aus dem Prompt-Cache (leer = automatisch
  0,1× des Eingabepreises). Ein gesetzter Wert markiert das Modell zugleich als
  „cache-priced" (Auto-Routing friert Modell + Tools dann auf Turn 1 ein, damit
  der Provider-Cache trifft). Daneben das Dropdown **„Coding-Plan / Konto"**
  (9.281.0, seit 9.283.0 als Plan-Verknüpfung): Es verbindet das Modell mit
  einem Abrechnungskonto aus der Plan-Nutzung-Sektion. Ein **Flat-Plan**
  (Z.ai/Kimi Coding Plan, Mistral Vibe — oder „Flatrate ohne Plan-Objekt")
  verbucht jeden Aufruf mit **0 $ realen Kosten** (Quotas, Plan-Verbrauch);
  das gilt auch für die seiten-basierte **OCR**- und die zeichen-basierte
  **TTS**-Abrechnung. Ein **Credit-Konto** (API-Guthaben, z. B. Kilo) rechnet
  weiter real nach Token — der Verbrauch läuft gegen das hinterlegte
  Kontingent im Dashboard. Die Kostenfelder behalten in beiden Fällen den
  **API-Listenpreis**; daraus rechnen Statuszeile und Kostenaufstellung den
  hypothetischen „API-Listenpreis ohne Flatrate" samt Ersparnis. Der
  Cache-Freeze („Kosten cached") bleibt davon unberührt. Pro
  Modell über das ⚙-Icon: u.a. **Fan-out-Modell** — teilt dieses Chat-Modell im
  Chat eine Hintergrundaufgabe per Fan-out auf, laufen die Leaf-Tasks auf dem
  hier gewählten (meist günstigeren) Modell; leer = bleiben auf diesem Modell.
  **✨ Auto** klassifiziert stattdessen die Absicht jedes Leaf-Tasks und wählt
  je Task das passende Modell (gesteuert über Server → Auto-Routing).
  **Benchmark** (steuert das Ranking der ✨-Auto-Modellwahl; seit 9.275.0):
  Die **Fähigkeit (%)** je Aufgabentyp stammt aus **offiziellen Leaderboards**
  — Artificial Analysis (Indizes; braucht den kostenlosen API-Key im Feld
  oben im Tab) und LMArena (Kategorie-Elo, ohne Key) — als Perzentil der
  gesamten Leaderboard-Verteilung; die **Geschwindigkeit (tok/s)** wird beim
  Benchmark-Lauf weiterhin real auf der eigenen Umgebung gemessen — seit
  9.362.0 mit **einem einzigen repräsentativen Aufruf pro Modell** (Durchsatz
  hängt am Modell/Provider, nicht am Aufgabentyp), dessen Wert für alle
  Leaderboard-Zellen übernommen wird; der Lauf ist dadurch deutlich
  schneller. Jede Zelle zeigt ein Quellen-Badge (**AA** / **Arena** /
  **intern**); der Tooltip nennt das zugeordnete offizielle Modell und den
  Rohwert. Modelle ohne Leaderboard-Eintrag (z. B. lokale Modelle) werden wie
  früher intern per Prompt+Judge getestet. Trifft die automatische Zuordnung
  das falsche Leaderboard-Modell, pinnen die **„Zuordnung"**-Felder unter der
  Tabelle den exakten offiziellen Namen; die **Override**-Spalten schlagen
  weiterhin jede Messung und überleben neue Läufe.
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
  - **E-Mail-Konten (v9.365.0)**: Unter **Einstellungen → Tools → email**
    verwaltet der Admin die E-Mail-Konten des Agenten — mehrere Konten
    parallel, je Konto eine Karte mit Name (der Bezeichner, den der Agent im
    `account`-Parameter verwendet), Typ (**IMAP+SMTP**, **POP3+SMTP** oder
    **Exchange (EWS, On-Prem)**), E-Mail-Adresse, Benutzername (leer =
    Adresse ist der Login) und Passwort (maskiert). Bei IMAP füllt ein
    **Preset** (Gmail, Outlook.com, GMX, iCloud, web.de) Host/Port/
    Verschlüsselung vor — das Gmail-Preset braucht ein App-Passwort (2FA)
    und schaltet die volle Gmail-Suchsyntax frei. Exchange verbindet per
    EWS-Host + Benutzername/Passwort (On-Prem; Microsoft 365/Graph ist
    nicht abgedeckt); das Abschalten der Zertifikatsprüfung (Self-Signed)
    wirkt prozessweit und ist entsprechend markiert. **„Verbindung
    testen"** je Konto prüft Login bzw. EWS-Bind, ohne etwas zu senden.
    Ein Konto wird per Radio-Knopf zum **Standard-Konto** (gilt, wenn der
    Agent keins nennt). Das alte Gmail-Feld wandert beim ersten Start
    automatisch als Konto „gmail" in dieses Modell.
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
- **Fenstergröße anpassbar:** Am **oberen linken Eck** des Brainy-Fensters
  sitzt ein Anfasser — daran ziehen macht das Fenster breiter/höher (oder
  wieder kleiner). Die gewählte Größe wird **pro Nutzer gespeichert** und
  beim nächsten Öffnen wiederhergestellt. Auf schmalen Bildschirmen
  (Smartphone) füllt Brainy ohnehin die ganze Fläche; dort entfällt das
  Anpassen.
- **Modell-Beratung (9.314.0):** Brainy beantwortet Fragen zur Modellwahl
  aus der echten Live-Konfiguration — z. B. „Welches Modell fürs
  Programmieren?", „Was kostet mich Modell X?", „Welches ist am
  schnellsten?", „Geht das auch lokal, ohne Cloud?". Er vergleicht dafür
  die Fähigkeits-Bewertungen je Aufgabentyp, die gemessene Geschwindigkeit
  (Tokens/Sek.), die Preise und sagt dazu, ob ein Modell lokal läuft
  (kostenlos, Daten bleiben auf dem Gerät) oder ob ein Abo es bereits
  abdeckt. Auch Fragen zu Abrechnungskonten, eigenen Kostenlimits und
  dazu, welches Modell eine Hintergrund-Aufgabe nutzt, kann er belegen.
- Der Verlauf ist **pro Nutzer** (nicht pro Chat) und bleibt erhalten.
  Nachrichten werden zu Frage-Antwort-Paaren mit Zeitstempel gruppiert;
  bei mehr als ~10 Paaren greift adaptive Altersgruppierung (Heute /
  Gestern / Diese Woche / Diesen Monat / Monat / Jahr, einklappbar) mit
  „Ältere laden". Einzelne Paare oder ganze Gruppen lassen sich löschen.
- Admins konfigurieren Brainy unter **Einstellungen → Tools → Brainy**:
  Aktiviert-Schalter, Modell (Standard „Auto" = Server-Standardmodell),
  Tool-Runden (1–12) und den editierbaren System-Prompt. Hinweis: die
  Modell-Beratung braucht ein Modell, das Werkzeuge zuverlässig aufruft —
  mit `mistral-small` riet Brainy stattdessen und verwechselte Konten mit
  Modellen; seit 9.314.0 läuft er auf `deepseek-v4-flash`.

---

## FAQ

**F: Warum ist die Modellauswahl plötzlich auf lokale Modelle beschränkt?**
A: Der GDPR-Scanner hat PII im Entwurf oder Verlauf gefunden, und für diese
Kategorie ist „Server-Block" aktiv. Entweder die PII entfernen, auf ein
lokales Modell wechseln (Daten bleiben auf dem Gerät) oder — als Admin —
die Kategorie-Aktion unter Einstellungen → GDPR von `block` auf `warn`
ändern.

**F: Warum meldet der Assistent bei einer Web-Recherche „nicht prüfbar (Datenschutz)"?**
A: Der Chat arbeitet mit anonymisierten personenbezogenen Daten (Schild-Symbol
aktiv). Damit die echten Werte — Name, Geburtsdatum, Passnummer, Adresse —
nicht an externe Suchmaschinen gelangen, prüft Brain seit v9.334.0 jede
Web-Suchanfrage und jede abzurufende URL **vor dem Versand**: enthält sie
einen geschützten Wert der Sitzung (auch versteckt in einer Web-Adresse),
wird der Aufruf angehalten. Der Assistent weist die betroffene Prüfung dann
ehrlich als „nicht prüfbar (Datenschutz)" aus — er behauptet **nicht**
fälschlich „keine Treffer". Rein technische Suchen (Gerätemodelle, Normen,
Fehlermeldungen) laufen normal durch. Admins wählen das Verhalten unter
**Einstellungen → GDPR → Master-Schalter → „Websuche in anonymisierten
Chats"** — seit v9.386.0 mit **zwei** Optionen:
- **Suchen** (`allow`): Der echte Wert wird für die Anfrage an die
  Suchmaschine eingesetzt — genau so findet eine gewollte Personen- oder
  Firmen-Recherche (z. B. Bilder zu einer Person, KYC-Prüfung) etwas. Der
  Assistent selbst sieht den echten Wert dabei nie; die Treffer werden vor der
  Anzeige wieder anonymisiert. Diese Übersetzung gilt nur für Web-Suche und
  -Abruf — **E-Mail-Versand und Bildgenerierung senden geschützte Werte nie**,
  auch in diesem Modus nicht (das wäre Kontaktaufnahme mit der geschützten
  Person bzw. ein Abfluss an einen fremden Dienst).
- **Blockieren** (`refuse`, Standard): Kein geschützter Wert verlässt den
  Rechner; die betroffene Prüfung wird als „nicht prüfbar (Datenschutz)"
  ausgewiesen.

Die früheren Modi „Nachfragen" (`ask`) und „Web-Tools ausblenden"
(`block_group`) wurden in v9.386.0 entfernt; eine noch so eingestellte
config.json wird beim Laden automatisch auf „Blockieren" gesetzt.

**F: Findet ein anonymisierter Chat meine Projektdaten und Dateien noch?**
A: Ja — seit v9.336.0. Vorher liefen Suchen im Projektgedächtnis und
Dateizugriffe in anonymisierten Chats oft ins Leere, weil die Werkzeuge nur
die Ersatzwerte kannten (eine Suche nach dem Pseudonym findet keine Akte mit
dem echten Namen). Jetzt übersetzt der Server die Ersatzwerte beim
Werkzeugaufruf automatisch zurück, führt das Werkzeug auf den echten Daten
aus und anonymisiert das Ergebnis wieder, bevor es das Sprachmodell erreicht.
Auch Inhalte aus dem Projektgedächtnis und aus dem Web (Suchtreffer,
abgerufene Seiten, Websuche-Korb) werden seither vor der Übergabe an
Cloud-Modelle anonymisiert. Web-Suchanfragen selbst folgen dabei dem
Web-Egress-Modus (Suchen/Blockieren, siehe oben): nur im Modus „Suchen" wird
der echte Wert für die Anfrage eingesetzt, sonst nie.

**F: Warum ist im anonymisierten Chat dieselbe Person überall gleich benannt?**
A: Seit v9.337.0 arbeitet die Anonymisierung auf Personen-Ebene statt auf
Zeichenketten-Ebene. Früher bekam jede Schreibweise desselben Namens (Akte,
Ausweis, E-Mail-Adresse, Scan mit Lesefehlern) einen anderen Ersatzwert — das
Sprachmodell sah dadurch mehrere scheinbar verschiedene Personen und konnte
z. B. bei einer Dokumentenprüfung falsche Widersprüche „finden". Jetzt erhält
jede Person genau **eine** Ersatz-Identität, die in allen Schreibweisen
konsistent auftaucht (auch in der maschinenlesbaren Ausweiszone und in
E-Mail-Adressen); verschiedene echte Personen bekommen immer verschiedene
Ersatznamen. Passnummern und die Ausweiszone werden als stimmige Ersatzwerte
mit **gültigen Prüfziffern** erzeugt, und Datumsangaben verschieben sich um
einen konstanten Betrag, sodass Reihenfolgen, Gültigkeitsspannen und
Zeitabstände exakt erhalten bleiben. Analysen über mehrere Dokumente liefern
so dieselben Zusammenhänge wie ohne Anonymisierung — im Chat sehen Sie
weiterhin die echten Werte, nur das Cloud-Modell sieht die Ersatzwerte.

**F: Sind fotografierte Ausweise in anonymisierten Chats geschützt?**
A: Ja, seit v9.339.0 auch der automatisch erkannte Text. Wenn Sie ein Bild
anhängen, liest Brain den Inhalt lokal per OCR aus (damit auch Modelle ohne
Bildverständnis den Inhalt kennen). Dieser erkannte Text lief früher an der
Anonymisierung vorbei — Name, Passnummer und Geburtsdatum eines
fotografierten Ausweises konnten roh an das Cloud-Modell gehen. Jetzt wird
er wie Ihr getippter Text anonymisiert. Zusätzlich liest Brain die
maschinenlesbare Zone (MRZ) des Dokuments strukturiert und prüfziffern-
validiert aus. Seit v9.383.0 erscheinen Name, Passnummer und Geburtsdatum
aus dieser Zone bereits **im Entscheidungs-Dialog vor dem Senden** als
eigene Einträge („Name (MRZ, Ausweisdokument)" usw.) — Sie sehen also
vorab vollständig, was erkannt wurde, und können jeden Eintrag einzeln als
Falschtreffer markieren. Was Sie bestätigen, wird konsistent ersetzt (auch
schwer lesbare Schreibweisen aus schlechten Fotos und ungewöhnliche
Namensfolgen). Beachten Sie: die **Bild-Pixel selbst** sind nicht
anonymisierbar — wenn ein bildfähiges Cloud-Modell aktiv ist, sieht es das
Foto; nutzen Sie für Ausweisprüfungen im Zweifel ein lokales Modell oder
die `doc_checks`-Werkzeuge (serverseitige Prüfung, es verlassen nur
Prüf-Verdikte den Rechner).

**F: Wird „als Falschtreffer markiert" wirklich überall beachtet?**
A: Ja, seit v9.383.0 verlässlich. Die gesamte Erkennung läuft jetzt
**einmal, vor dem Dialog** (getippter Text, Anhänge und Ausweis-MRZ), und
der Chat wendet danach exakt Ihre Auswahl an, statt intern erneut zu
scannen. Ein als Falschtreffer markierter Wert bleibt damit überall im
Klartext — auch wenn er in einem Anhang steht, den der Assistent später im
Gespräch erneut liest, und auch in Folgenachrichten desselben Chats. Zuvor
konnte es passieren, dass ein markierter Wert trotzdem anonymisiert wurde,
weil eine interne Nach-Erkennung die Markierung nicht kannte.

**F: Was bedeutet „⚠️ N Werte konnten nicht zurückübersetzt werden"?**
A: In anonymisierten Chats ersetzt Brain die Ersatzwerte in der Antwort und
in geschriebenen Dateien automatisch wieder durch Ihre echten Daten. Das
funktioniert nur, wenn das Modell die Ersatzwerte **exakt so** wiedergibt,
wie es sie erhalten hat. Formuliert es einen Wert um — etwa das Ersatzdatum
„17.02.1947" als „17. Februar 1947", einen Namen als Initialen oder im
Genitiv —, kann die Rückübersetzung diese Stelle nicht mehr erkennen. Seit
v9.340.0 prüft Brain jede fertige Antwort und jede geschriebene Datei
darauf und warnt deutlich (Hinweisblock unter der Antwort bzw. Warnung an
der Datenschutz-Zeile), statt Ihnen unbemerkt Ersatzwerte in einem Bericht
zu liefern. Die gezeigten Werte in der Warnung sind immer die
**Ersatzwerte**, nie Ihre echten Daten. Betroffene Stellen im Text bitte
manuell prüfen oder die Antwort neu anfordern.

**F: Warum soll der Assistent in anonymisierten Chats kein PDF erzeugen?**
A: PDF-Dateien lassen sich nachträglich nicht zuverlässig verändern — die
automatische Rückübersetzung der Ersatzwerte kann sie deshalb nicht
bearbeiten. Ein PDF aus einem anonymisierten Chat enthielte also plausibel
aussehende, aber falsche Werte (Ersatz-Namen, Ersatz-Passnummern) ohne
Kennzeichnung. Seit v9.340.0 wird der Assistent angewiesen, Berichte in
solchen Chats als **HTML oder Markdown** zu erzeugen (beide werden
vollständig rückübersetzt); schreibt er dennoch ein PDF mit Ersatzwerten,
erscheint eine deutliche Warnung an der Datei und der Assistent erhält den
Hinweis, den Bericht als HTML/Markdown neu zu erzeugen. Ein PDF ohne
geschützte Werte (z. B. ein rein technischer Anhang) bleibt unbeanstandet.

**F: Wo stelle ich die Datenschutz-Haltung ein? (Projekt-Presets entfernt)**
A: Zentral, an genau einer Stelle: **Einstellungen → GDPR**. Die früheren
Projekt-Presets („KYC", „KYC lokal", „Screening", v9.341–9.347) wurden in
v9.348.0 entfernt — es gilt ein einziges Regelwerk, das in jedem Chat gleich
greift, mit und ohne Projekt, und nicht pro Projekt umgangen oder
abgeschwächt werden kann. Das Prinzip: Die **Regel** (pro Datenart, mit
Konfidenz-Schwellen) entscheidet, wann sie greift; die **Aktion** entscheidet,
was Sie dann tun können — bei „Warnen" dürfen Sie ignorieren oder fortfahren,
bei „Blockieren" bleibt nur Anonymisieren, das lokale Modell oder Abbrechen
(kein Klartext-Versand). Haben Sie in einem Chat einmal „Anonymisieren"
gewählt, bleibt der Chat dabei — ohne erneute Rückfrage; ein bewusster
Opt-out per Schild-Symbol im Eingabefeld ist weiterhin möglich, sofern die
Regel nicht auf „Blockieren" steht.

**F: Wie schütze ich Firmennamen (früher „Screening")?**
A: Über die **Organisations-Regel** in Einstellungen → GDPR (Regel
`organisation` auf „Warnen" oder „Blockieren" stellen). Dann werden
**Firmennamen** gegenüber dem Cloud-Modell durch Ersatznamen geschützt — über
alle Schreibweisen hinweg: „Wiener Privatbank SE", „Wiener Privatbank", die
GROSSSCHREIBUNG, in der Sanktionslisten geführt werden, und die Kurzform in
einer Web-Adresse gelten als ein und dieselbe Firma und bekommen denselben
Ersatznamen. Auch **Konzernstrukturen bleiben lesbar**: eine
Tochtergesellschaft ist auch im Ersatznamen als Tochter ihrer Mutter
erkennbar — ohne das ginge genau die Information verloren, um die es bei
einer UBO-Prüfung geht.

Der entscheidende Punkt: **Ihre Recherche funktioniert trotzdem.** Für die
Suchanfrage setzt Brain den echten Firmennamen automatisch wieder ein, ohne
ihn dem Sprachmodell zu zeigen. Adverse-Media-, Sanktions- und
Registerabgleich laufen also vollständig — das Cloud-Modell bekommt den
Firmennamen dabei nie zu sehen. (Bei Personennamen bleibt es dabei, dass Sie
eine Websuche einzeln freigeben müssen: eine Suche mit einem
Ersatz-Personennamen träfe eine echte fremde Person und würde Ihre Analyse
verfälschen.) Nicht ersetzt werden Behörden, Register und Prüflisten (OFAC,
Firmenbuch, Companies House, BaFin) — sie sind Ihr Prüfwerkzeug, nicht das
Prüfsubjekt.

**F: Bin ich auch ohne Projekt geschützt?**
A: Ja — der Schutz ist projekt-unabhängig. In einem Chat
**ohne Projekt** konnte früher ein Name oder eine Kontonummer in die allererste
Websuche geraten, bevor irgendein Schutz griff. Seit v9.344.0 gilt: Sobald der
Datenschutz-Scanner aktiv ist (Einstellungen → GDPR), prüft Brain **jede**
ausgehende Websuche — auch in Chats ohne Projekt — und verweigert bzw. fragt
nach, wenn personenbezogene Daten darin vorkommen. Die automatische
Ersetzung durch Ersatzwerte funktioniert unverändert wie bisher (per Dialog
bzw. Merken der Entscheidung); neu ist allein, dass der Weg nach draußen auch
ohne Projekt bewacht ist.

**F: Was bedeutet die Zeile „Datenschutz dieser Antwort: …" unter einer Antwort?**
A: In anonymisierten Chats zeigt diese Zeile (seit v9.341.0), was bei dieser
Antwort aus Datenschutzgründen anders lief als in einem ungeschützten Chat —
z. B. „Websuche 2× nicht ausgeführt (geschützte Werte)", „Websuche 1× mit
freigegebenen Werten", „Dokument-Prüfung serverseitig (3×)", „PDF-Erzeugung
abgelehnt" oder „1 Wert nicht rückübersetzbar". Sie hilft, eine
datenschutzbedingte Lücke von einem echten Prüfungsbefund zu unterscheiden:
Wenn dort steht, dass eine Websuche nicht ausgeführt wurde, heißt „kein
Web-Treffer erwähnt" eben **nicht** „im Web gibt es nichts". Die Zeile nennt
nur Anzahlen, nie die geschützten Werte selbst.

**F: Ein Werkzeug-Aufruf (z. B. Suche im Wissensspeicher) zeigt einen echten
Namen — ist das ein Leck?**
A: Nein, das ist gewollt und sicher (seit v9.387.0). Werkzeuge laufen **lokal
auf Ihrem Rechner** und arbeiten deshalb auf den **echten** Daten — anonymisiert
wird nur der Weg zum Sprachmodell. In einem geschützten Chat sieht das
Sprachmodell nur Pseudonyme (z. B. „Collins Kerry A"); bevor eine lokale Suche,
ein Dateizugriff oder ein Skript läuft, setzt Brain die echten Werte wieder ein
(„Stark Bonnie M"), sonst fände die Suche im Wissensspeicher nichts. Der Chat
zeigt Ihnen diesen **tatsächlich ausgeführten** Aufruf mit echten Werten.
(Das frühere Abzeichen „🔓 deanonymisiert" wurde in v9.391.2 entfernt — die
farbigen Markierungen mit Tooltip übernehmen diese Auskunft pro Wert.)
Das Ergebnis wird wieder pseudonymisiert, bevor
es zum Sprachmodell zurückgeht. Bei Werkzeugen, die Daten **nach außen** geben
(Websuche, E-Mail-Versand), gilt das nicht — die schützt Brain gesondert.
Sind zusätzlich die **Datenschutz-Details** eingeschaltet (Schalter im
Eingabefeld), werden geschützte Werte seit v9.391.0 **auch in den
Werkzeug-Aufrufen farblich markiert** — in der Aufruf-Zeile im Chat ebenso wie
im Aktivitäts-Panel (Titel, Parameter-Tabelle und Ergebnis-Box), genau wie in
Ihrer Frage und der Antwort. Der Tooltip an jeder Markierung nennt, welcher
Wert durch welches Pseudonym ersetzt wurde (bzw. dass ein Wert bewusst im
Klartext blieb).

**F: Ein erzeugter Report hat einen Pseudonym-Namen im Dateinamen — obwohl der
Inhalt echt ist. Kann man das beheben?**
A: Ja, das passiert automatisch (seit v9.390.0). Weil das Sprachmodell nur die
Pseudonyme sieht, benennt es eine erzeugte Datei manchmal nach dem Fake-Namen
(z. B. `..._logan_edwards.html`), während der **Inhalt** bereits in Echtwerte
zurückübersetzt ist. Brain benennt die Datei danach auf den **echten** Namen um
(`..._bonnie_stark.html`), sodass Name und Inhalt zusammenpassen — in der
Artefakt-Liste und beim Download sehen Sie den echten Namen. Damit spätere
Zugriffe des Modells nicht ins Leere laufen, bleibt der alte (Pseudonym-)Pfad
als Verweis bestehen; die Funktion bricht also nie. Seit v9.391.0 nennt auch
der **Antworttext** die Datei beim echten Namen: verwies die Antwort bisher
noch auf den Pseudonym-Dateinamen („📁 `..._logan_edwards.html`"), obwohl die
Datei längst umbenannt war, zeigen Text und Link jetzt direkt auf die echte
Datei.

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
Entscheidungen, offene Punkte). Während die Übergabe erstellt wird, zeigt ein
**Fortschritts-Fenster** den Status; danach siehst du eine **Vorschau** der
Übergabe und entscheidest selbst: **Übernehmen** öffnet einen **neuen Chat**
mit zwei angehängten Dokumenten — der kompakten Übergabe und dem
**vollständigen Verlauf** des Ursprungs-Chats; **Abbrechen** verwirft den
neuen Chat. Das Modell arbeitet aus der Übergabe und öffnet den vollen Verlauf
nur bei Bedarf — du machst nahtlos dort weiter, wo du aufgehört hast. Die
Übergabe wird außerdem **als Dokument (Artefakt) im ursprünglichen Chat
gespeichert** (`Übergabe-<Zeitstempel>.md`), unabhängig davon, ob du sie in
einen neuen Chat übernimmst.

**F: Was macht das Schild-Symbol (Datenschutz-Übersicht) im Eingabefeld?**
A: Es erscheint, sobald in deinem Chat personenbezogene Daten erkannt wurden
oder du dazu schon Entscheidungen getroffen hast. Ein **Klick** öffnet die
**Datenschutz-Übersicht** — ein großes Fenster, das **alle** erkannten Daten
des gesamten Chats zeigt: aus deinen Nachrichten, dem Verlauf und Anhängen,
**gruppiert nach Herkunft**. Pro Fund siehst du Kategorie, einen maskierten
Wert und den **Status** (offen · anonymisiert · im Klartext gesendet · lokal
gesendet · Falschtreffer). Mit **Suche** und **Status-Filter** findest du
gezielt einzelne Funde; per **Auswahl** (einzeln oder ganze Gruppe) kannst du
mehrere Funde auf einmal **als Falschtreffer markieren**, **als Klartext
akzeptieren** oder die **Entscheidung zurücksetzen** und mit **Änderungen
speichern** sichern. So erledigst du das Datenschutz-Thema in einem Durchgang,
statt jeden Fund einzeln beim Senden zu behandeln. Pro Fund kannst du den
**Verlauf** aufklappen — er zeigt **wer wann was** entschieden hat (mit Name und
Zeitpunkt); denselben Verlauf siehst du auch im Hinweis-Dialog vor dem Senden
bei bereits behandelten Funden, beide Dialoge sind gleich aufgebaut. Bei sehr
vielen Funden sind die Quellen (Nachrichten, Verlauf, einzelne Anhänge)
standardmäßig **eingeklappt** (mit Anzahl + Status-Vorschau), lange Listen
werden schrittweise nachgeladen. (Das eigentliche
**Anonymisieren** geschieht weiterhin im Hinweis-Dialog *vor dem Senden* — die
Übersicht zeigt den Anonymisierungs-Status und kann ihn zurücksetzen.) Das
Schild bleibt auch nach dem **Neuladen** des Chats erreichbar.

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
A: Das Modal vor dem Senden bietet zwei Wege plus Abbrechen:
- **Senden an Cloud-Modell** — anonymisiert die erkannten Werte, die Sie NICHT
  als Falschtreffer markiert haben, und sendet dann an das gewählte Cloud-Modell
  (PII durch Tokens ersetzt; eine Decrypt-Map bleibt für das Audit erhalten).
  Markieren Sie **alle** Funde als Falschtreffer, wird nichts ersetzt und die
  Nachricht geht unverändert an die Cloud. Bei **streng vertraulichem** oder
  klassifiziertem Inhalt ist dieser Knopf gesperrt (Cloud-Versand verboten).
- **Unverändert senden an lokales Modell** — sendet ohne Änderung an ein
  lokales Modell; die Daten verlassen das Gerät nicht. Auch für streng
  vertrauliche Dokumente verfügbar.
- **Abbrechen** — nichts wird gesendet; **Ihre eingegebene Nachricht bleibt
  erhalten** und Sie können sie im Eingabefeld weiter bearbeiten.

In der Spalte **Nachrichtentext** wird jeder Fund mit etwas **umgebendem Text**
angezeigt und der eigentliche Wert **farblich hervorgehoben** — so sehen Sie
sofort, an welcher Stelle Ihrer Nachricht er steht (der volle Kontext erscheint
als Tooltip beim Überfahren). Einzelne Funde können Sie im Dialog (oder in der
Datenschutz-Übersicht über das Schild-Symbol) als **Falschtreffer** markieren —
solche Werte bleiben im Klartext und werden nie anonymisiert.

**F: Kann ich nachträglich prüfen, ob die GDPR-Aktion funktioniert hat?**
A: Ja — im Modal vor dem Senden (Cloud-Modell / lokales Modell) gibt
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

## Excel-Dateien analysieren, bearbeiten und erstellen (v9.262.0/v9.263.0)

Excel-Aufgaben funktionieren jetzt mit **jedem** Modell zuverlässig — auch mit
kleinen lokalen. Der Agent schreibt dafür keinen Python-Code mehr, sondern
nutzt eingebaute Tabellen-Werkzeuge; die Daten selbst laufen dabei nie durch
das Sprachmodell (wichtig bei großen oder sensiblen Tabellen).

Was Sie einfach im Chat anfordern können (Datei per 📎 anhängen):

- **Verstehen**: „Was steht in dieser Excel?" — der Agent liefert eine
  Strukturübersicht (Blätter, Spalten, Datentypen, auffällige Verknüpfungen
  zwischen Blättern), ohne die Rohdaten in den Chat zu kippen.
- **Analysieren**: Filtern, Gruppieren, Summen, Verknüpfen mehrerer Blätter
  oder mehrerer Dateien („Vergleiche Datei A mit Datei B") — das Ergebnis
  erscheint als Tabelle im Chat; vollständige Ergebnisse werden auf Wunsch
  als CSV-Artifact gespeichert.
- **Erstellen**: „Erzeuge daraus eine kombinierte Excel" — die neue
  Arbeitsmappe kommt automatisch professionell formatiert (farbige
  Kopfzeile, fixierte Titelzeile, passende Spaltenbreiten, Zahlen-/
  Euro-/Datumsformate, optional Summenzeile, Diagramme und farbige
  Bedingungsregeln). Auch Master-Detail-Layouts (z. B. jede Order mit ihren
  Teilausführungen gruppiert darunter) sind ein Standardfall.
- **Bearbeiten**: Zeilen anhängen, berechnete Spalten (echte Excel-Formeln),
  Werte per Bedingung ändern, Blätter verwalten — die bestehende Formatierung
  bleibt erhalten.
- **Vergleichen** (v9.263.0): „Was hat sich zwischen Datei A und Datei B
  geändert?" — der Agent liefert einen Änderungsbericht (neue/entfernte/
  geänderte Zeilen mit Alt→Neu-Werten) und auf Wunsch die vollständige
  Änderungsliste als CSV.
- **Prüfen** (v9.263.0): eine Tiefenprüfung findet Duplikate, statistische
  Ausreißer, verwaiste Verknüpfungswerte (z. B. Buchungen ohne zugehörige
  Order) und zeigt, welche Blätter per Formel auf welche verweisen.
- **Vorlagen füllen** (v9.263.0): der Agent schreibt Daten in eine bestehende,
  fertig formatierte Excel-Vorlage (Corporate-Template) — nur die Daten ändern
  sich, Layout und Formeln der Vorlage bleiben unangetastet.

**Tabellen-Vorschau in der Oberfläche** (v9.263.0): Ein Klick auf eine
`.xlsx`-Datei im Datei-Baum (unterer Bereich, Code-Modus) öffnet jetzt eine
Tabellen-Ansicht direkt in der App — mit Reitern pro Tabellenblatt, fixierter
Kopfzeile und Zeilennummern (Rechtsklick → „extern öffnen" startet weiterhin
Excel). CSV-Dateien zeigen die gleiche Tabellen-Ansicht im „Ansicht"-Modus des
Editors. Auch im rechten Panel unter **Dateien** werden Excel-Artefakte jetzt
als Tabelle voranzeigt statt nur als Download-Karte. Komplexe Blätter werden
dabei genauso interpretiert wie vom Agenten selbst (mehrere Tabellenblöcke pro
Blatt, verbundene zweizeilige Überschriften).

**Neu in v9.264.0:**
- **In der Tabellen-Vorschau arbeiten**: Klick auf eine Spaltenüberschrift
  sortiert (nochmal: absteigend, drittes Mal: Originalreihenfolge), das
  Suchfeld filtert Zeilen, Spaltenbreiten lassen sich am Rand ziehen. Im
  unteren Bereich (Datei-Baum) sind Excel-Zellen per **Doppelklick direkt
  editierbar** — Enter speichert, Esc bricht ab; wurde die Datei
  zwischenzeitlich extern geändert, warnt die App statt zu überschreiben.
- **Pivot-Auswertungen**: „Erstelle eine Kreuztabelle Umsatz je Region und
  Monat" — der Agent baut echte Pivot-Blätter (Summe/Anzahl/Durchschnitt/
  Min/Max, mit Gesamtzeile).
- **Bessere Diagramme**: auch Flächen- und Punktdiagramme, gestapelte Balken
  und Kombi-Diagramme mit zweiter Y-Achse (z. B. Umsatz als Balken, Quote als
  Linie rechts).
- **Vergleich als markierte Excel**: der Datei-Vergleich liefert auf Wunsch
  eine Excel, in der geänderte Zellen gelb markiert sind (alter Wert als
  Kommentar), neue Zeilen grün und entfernte rot — ideal zum Durchsehen.
  Zusammengesetzte Schlüssel („Kunde + Datum") und Formel-Vergleich sind
  möglich.
- **Formeln sofort gerechnet**: neue oder geänderte Formeln können direkt
  beim Erstellen/Bearbeiten berechnet werden (nicht erst beim Öffnen in
  Excel), sodass Folgeauswertungen die Werte sehen.
- **Alte Formate**: `.xls`- und `.ods`-Dateien werden überall gelesen.
- **Projekt-Wissen**: für jede Excel in einem Projekt wird automatisch ein
  Struktur-Profil hinterlegt — Fragen wie „welche Datei hat die Spalte
  Kundennummer?" beantwortet der Agent aus dem Projektgedächtnis, ohne die
  Datei erst öffnen zu müssen.
- **JSON/XML übersichtlich**: JSON- und XML-Dateien erscheinen im rechten
  Panel (Anhänge und Dateien) als aufklappbarer Datenbaum — wie im unteren
  Editor-Bereich; der Quelltext bleibt über den Code-Umschalter erreichbar.

**Neu in v9.265.0:**
- **Zellen auch im rechten Panel bearbeiten**: Im Dateien-Tab (Artefakte)
  genügt jetzt ebenfalls ein Doppelklick auf eine Zelle — Enter speichert,
  Esc bricht ab. Bearbeitbar sind die vom Agenten erzeugten Dateien
  (neueste Version); Anhänge bleiben bewusst nur lesbar, damit sich die
  Eingaben des Agenten nicht unbemerkt ändern.
- **VBA-Ansicht für .xlsm**: Makrofähige Arbeitsmappen zeigen in der
  Tabellen-Vorschau (unten wie im Dateien-Tab) ihre VBA-Module als eigene
  ⚙-Reiter — Quellcode mit Syntax-Hervorhebung, exportierbar als `.bas`.
  Nur lesend: Makros werden nie ausgeführt, und VBA zurückzuschreiben
  erfordert prinzipbedingt Excel (Rechtsklick → „extern öffnen").

**Neu in v9.266.0:**
- **Rückgängig beim Zellen-Bearbeiten**: Jede gespeicherte Zellen-Änderung
  lässt sich über den „↩ Rückgängig"-Knopf in der Tabellen-Leiste
  zurücknehmen (mehrere Schritte, neueste zuerst).
- **Sehr große Exporte**: Tabellen mit mehr als 100.000 Zeilen schreibt der
  Agent jetzt in einem speicherschonenden Streaming-Verfahren — auch
  500.000+ Zeilen sind kein Problem (formatierte Kopfzeile, fixierte
  Titelzeile und Summenzeile bleiben; Zebra-Streifen und Zahlenformate
  entfallen in diesem Modus, was im Ergebnis vermerkt wird).
- **Formatierungs-Vergleich**: Der Datei-Vergleich findet auf Wunsch auch
  reine Format-Änderungen — Zellen, deren Wert gleich blieb, aber deren
  Fettung, Farbe oder Zahlenformat sich geändert hat.
- Für wiederkehrende Excel-Reports und tägliche Bestandsabgleiche gibt es
  fertige Rezepte (geplante Aufgaben) — fragen Sie Brainy nach
  „wiederkehrender Excel-Report".

**Tipp**: Für einen wiederkehrenden Abgleich daraus eine geplante Aufgabe
machen — Dateien anhängen, Prompt wie oben, Zeitplan `0 7 * * *`,
Tool-Profil `interactive`.

---

## Rezept: zwei Excel-Dateien vergleichen

1. Neuen Chat öffnen (jedes Modell geeignet — die Tabellen-Werkzeuge arbeiten
   deterministisch, siehe oben).
2. 📎 Beide `.xlsx`-Dateien anhängen.
3. Prompt:
   > Vergleiche `datei_a.xlsx` und `datei_b.xlsx`. Beide haben eine Spalte
   > `customer_id`. Liste die Zeilen, in denen sich der Wert von `amount`
   > zwischen den Dateien bei gleicher `customer_id` unterscheidet. Gib eine
   > CSV mit den Spalten `customer_id, amount_a, amount_b, delta` aus.
4. Der Agent verschafft sich per `xlsx_inspect` einen Überblick, verknüpft
   beide Dateien per `xlsx_query` (SQL-JOIN über beide Dateien in einem
   Aufruf) und speichert das volle Ergebnis als CSV-Artifact, herunterladbar
   im **Dateien**-Tab des rechten Panels.

---

## Rezept: tägliche E-Mail-Zusammenfassung einrichten

1. Seitenleiste → **Geplante Aufgaben** → ＋ neu.
2. Ausfüllen:
   - **Name**: `tägliche_inbox_zusammenfassung`
   - **Aufgabe**:
     > Nutze `email_search`, um ungelesene Nachrichten der letzten 24 h zu
     > finden. Für jeden Thread, der eine Antwort zu brauchen scheint,
     > liste Absender, Betreff und einen Ein-Satz-Grund. Überspringe
     > Newsletter und Benachrichtigungen. Gib eine Markdown-Liste aus.
   - **Zeitplan**: `0 8 * * *`
   - **Modell**: ein fähiges Modell (lokal ist gut)
   - **Tool-Profil**: `interactive` (braucht die E-Mail-Tools)
3. Speichern. **Jetzt ausführen** klicken zum Testen. Lauf-Detail prüfen.
4. Passt die Ausgabe, stehen lassen — feuert täglich um 08:00.

**Tipp**: Das Ergebnis an etwas Umsetzbares schicken — die Aufgabe so
ändern, dass sie eine Zusammenfassung per `email_send` an dich selbst
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
