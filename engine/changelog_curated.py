"""engine/changelog_curated.py — KURATIERTE Versionshistorie aus ENDUSER-SICHT.

Dies ist NICHT der technische `CHANGELOG` aus brain.py (der entwickler-facing ist:
Funktionsnamen, Dateipfade, "URSACHE/FIX"-Mechanik). Diese Liste hier ist die
schön formulierte, nutzenorientierte Versionshistorie, die im Web-UI erscheint,
wenn man in der linken Spalte auf die Brain-Agent-Version klickt — sie sagt dem
Nutzer (und dem Admin), WAS eine Version ihm bringt, nicht WIE sie gebaut wurde.

PFLEGE (stehende Regel, siehe CLAUDE.md "Kuratierte Versionshistorie pflegen"):
sie ist HANDGEPFLEGT — wie die brain-agent-guide-Skill, NICHT auto-derived. Bei
jedem Release mit enduser-/admin-RELEVANTER Änderung kommt VORNE ein neuer Eintrag
dazu (oder ein bestehender wird erweitert). Rein interne Releases (Performance,
unsichtbare Bugfixes, Refactors) bekommen KEINEN Eintrag. Beginnt bei 9.0.0 —
alles davor ist bewusst nicht aufgenommen.

Tonregeln:
  - Deutsch, förmliches "Sie" (passend zur UI). Fachbegriffe dürfen englisch bleiben.
  - NUTZEN statt Mechanik: "vor jeder Eingabe wird das beste Modell gewählt und die
    Werkzeuge passend reduziert → schneller, zuverlässiger, günstiger" — NICHT
    "ein LLM-Klassifikator erkennt die Aufgabenart".
  - title: 3-7 Wörter. body: 1-4 Sätze, was es dem Nutzer/Admin bringt.
  - audience: "user" (für jeden sichtbar/nutzbar) | "admin" (neue Einstellung/Admin-Funktion).
  - versions: alle technischen Versionen, die in diesem kuratierten Eintrag zusammengefasst sind.

Format pro Eintrag: dict mit version/date/title/body/audience/versions.
Reihenfolge: NEUESTE zuerst (so wird es auch angezeigt).
"""
from __future__ import annotations

CURATED_CHANGELOG: list[dict] = [
    {
        "version": "9.312.8",
        "date": "2026-07-11",
        "title": "Der Chat zeigt, dass Subagenten noch arbeiten",
        "body": "Startet ein Chat Subagenten, läuft der gewohnte Spinner "
                "weiter — mit dem Hinweis „✦ 3 Subagenten · arbeitet im "
                "Hintergrund“, im Terminal-Chat als eigene Zeile am Ende des "
                "Verlaufs. Ein Klick darauf öffnet die Live-Karten. Bisher sah "
                "der Chat fertig aus, obwohl im Hintergrund noch minutenlang "
                "gearbeitet wurde.",
        "audience": "user",
        "versions": ["9.312.8"],
    },
    {
        "version": "9.312.7",
        "date": "2026-07-11",
        "title": "Jeder Chat bekommt seinen eigenen Ausgabeordner",
        "body": "Was der Agent in einem Code-Projekt erzeugt — Hilfsskripte, "
                "Reports, Diagramme — liegt jetzt in einem Ordner pro Chat: "
                "`chats/<Titel>_<Datum>_<ID>/`. Läuft eine Aufgabe über mehrere "
                "Subagenten, bekommt jeder von ihnen darin ein eigenes "
                "Unterverzeichnis, sodass sie sich nicht mehr gegenseitig "
                "überschreiben können. Ihr Quellcode bleibt unberührt, und die "
                "erzeugten Dateien fließen nicht in die Code-Suche ein.",
        "audience": "user",
        "versions": ["9.312.7"],
    },
    {
        "version": "9.312.6",
        "date": "2026-07-11",
        "title": "Subagenten-Ansicht folgt dem Chat, mit Laufzeit",
        "body": "Der Subagenten-Tab zeigt jetzt immer die Aufgaben des Chats, "
                "in dem Sie gerade sind, und schaltet beim Wechsel automatisch "
                "um — vorher konnten sich nach einem Neuladen die Subagenten "
                "mehrerer Chats vermischen. Jede Karte zeigt außerdem, wie "
                "lange die Aufgabe schon läuft und wie viel Zeit ihr bis zum "
                "Abbruch bleibt; ist sie fertig, sehen Sie die Gesamtlaufzeit.",
        "audience": "user",
        "versions": ["9.312.6"],
    },
    {
        "version": "9.312.5",
        "date": "2026-07-11",
        "title": "Subagenten können jetzt zurückfragen",
        "body": "Braucht ein Subagent eine Entscheidung von Ihnen, erscheint "
                "seine Frage als Antwort-Box auf seiner Karte — mit den "
                "vorgeschlagenen Optionen und einem Freitextfeld. Chat-Liste "
                "und Statuszeile weisen mit „❓ Rückfrage“ darauf hin, damit "
                "Sie es auch bei geschlossenem Panel bemerken. Sobald Sie "
                "antworten, arbeitet der Subagent weiter. Bisher blieb eine "
                "solche Frage unsichtbar und die Aufgabe lief ins Leere.",
        "audience": "user",
        "versions": ["9.312.4", "9.312.5"],
    },
    {
        "version": "9.312.3",
        "date": "2026-07-11",
        "title": "Code-Projekte bleiben aufgeräumt",
        "body": "Hilfsskripte, Reports und Diagramme, die der Agent für seine "
                "Arbeit erzeugt, landen jetzt in einem Unterverzeichnis (etwa "
                "`brain/scripts/`) statt lose im Wurzelverzeichnis Ihres "
                "Projekts. Passende vorhandene Ordner werden bevorzugt "
                "wiederverwendet. Quellcode, den der Agent ändert oder "
                "ergänzt, bleibt selbstverständlich dort, wo Ihr Code liegt.",
        "audience": "user",
        "versions": ["9.312.3"],
    },
    {
        "version": "9.312.2",
        "date": "2026-07-11",
        "title": "Laufende Subagenten sind jetzt sichtbar",
        "body": "Startet ein Chat Subagenten, sehen Sie das jetzt sofort: Der "
                "Chat pulsiert grün und zeigt „✦ 3 Subagenten“, solange im "
                "Hintergrund gearbeitet wird — ein Klick darauf öffnet die "
                "Live-Karten. In der linken Chat-Liste erscheint derselbe "
                "Hinweis wie bei einer laufenden Antwort. Bisher wirkte der "
                "Chat nach dem Start der Subagenten still, obwohl sie noch "
                "minutenlang arbeiteten.",
        "audience": "user",
        "versions": ["9.312.2"],
    },
    {
        "version": "9.312.1",
        "date": "2026-07-11",
        "title": "Spickzettel-Regler schaltet jetzt wirklich ab",
        "body": "Der Spickzettel-Regler pro Modell (Einstellungen → Modelle) "
                "wirkt jetzt so, wie er beschriftet ist: Steht er auf „Aus“, "
                "bekommt das Modell die Denk-Werkzeuge nicht mehr angeboten. "
                "Bisher wurde nur die Aufforderung zum Nachdenken unterdrückt, "
                "die Werkzeuge selbst blieben sichtbar — starke Modelle griffen "
                "trotz „Aus“ danach und verbrauchten dafür Zeit und Budget. "
                "Modelle mit eingeschaltetem Spickzettel sind unverändert.",
        "audience": "admin",
        "versions": ["9.312.1"],
    },
    {
        "version": "9.312.0",
        "date": "2026-07-11",
        "title": "Subagenten übersichtlich: ein Hub statt vieler Tabs",
        "body": "Die Subagenten-Ansicht im Code-Modus ist jetzt ein einziger "
                "✦-Tab mit einer Karte pro Aufgabe — mit Status, ausführendem "
                "Modell, Live-Zeile und aufklappbarem Verlauf; der Tab zählt "
                "und pulsiert, solange gearbeitet wird. Laufende Subagenten "
                "erscheinen zusätzlich in der linken Chat-Liste unter ihrem "
                "Chat, Karten überleben einen Seiten-Reload, und sobald eine "
                "Aufgabe fertig ist, zeigt der Chat die Verarbeitung des "
                "Ergebnisses von selbst an.",
        "audience": "user",
        "versions": ["9.312.0"],
    },
    {
        "version": "9.311.0",
        "date": "2026-07-11",
        "title": "Code-Modus: sicherer bauen, gezielter suchen",
        "body": "Drei Verbesserungen für Code-Projekte: Der Assistent kann "
                "riskante Umbauten jetzt in einem isolierten Arbeitsbereich "
                "erledigen (eigene Kopie auf eigenem Branch — Sie prüfen den "
                "Diff und führen selbst zusammen, nie automatisch). Für "
                "strukturelle Code-Suchen und -Umbauten nutzt er eine "
                "syntaxbewusste Suche mit Vorschau vor jeder Änderung. Und "
                "Datei-Änderungen schlagen deutlich seltener fehl: fast "
                "passende Änderungsstellen mit Anführungszeichen- oder "
                "Einrückungs-Abweichungen werden sicher erkannt — bei "
                "Mehrdeutigkeit wird nachgefragt statt geraten.",
        "audience": "user",
        "versions": ["9.309.0", "9.310.0", "9.311.0"],
    },
    {
        "version": "9.308.0",
        "date": "2026-07-11",
        "title": "Subagenten live im Code-Modus zusehen",
        "body": "Wenn der Assistent in einem Code-Modus-Projekt eine "
                "Hintergrundaufgabe startet, öffnet sich unten automatisch ein "
                "eigenes Fenster, in dem Sie dem Subagenten live zusehen können — "
                "seine Antwort, seine Werkzeug-Aufrufe und sein Fortschritt "
                "erscheinen in Echtzeit, mit Stopp-Knopf direkt daneben. "
                "Zusätzlich zeigt das Hintergrundaufgaben-Panel rechts bei "
                "laufenden Aufgaben wieder den Live-Verlauf statt erst das "
                "Endergebnis.",
        "audience": "user",
        "versions": ["9.308.0"],
    },
    {
        "version": "9.307.0",
        "date": "2026-07-10",
        "title": "YouTube-Videos und Audio-Links als Quellen",
        "body": "Der Assistent kann jetzt mit YouTube-Links und direkten "
                "Audio-Dateien (z. B. MP3-Podcasts) arbeiten: Der Ton wird "
                "automatisch heruntergeladen und lokal transkribiert, und der "
                "Assistent antwortet auf Basis des gesprochenen Inhalts. Das "
                "funktioniert überall, wo Web-Adressen genutzt werden — im Chat, "
                "im Websuche-Korb und bei den Web-Adressen eines Projekts (ein "
                "verlinktes Video wird damit Teil des Projektwissens).",
        "audience": "user",
        "versions": ["9.307.0"],
    },
    {
        "version": "9.306.0",
        "date": "2026-07-10",
        "title": "Eine Suche über Chats, Wiki und Gedächtnis",
        "body": "Die Suche in der Seitenleiste (auch per Strg/Cmd+K) durchsucht "
                "jetzt alles auf einmal: Ihre Chats — einschließlich der "
                "Nachrichteninhalte, nicht mehr nur der Titel —, Ihre "
                "Wiki-Seiten (sinngemäße Treffer, nicht nur Wortgleichheit) und "
                "das Langzeit-Gedächtnis des Assistenten. Die Ergebnisse "
                "erscheinen gruppiert mit Vorschau; ein Klick springt direkt in "
                "den Chat oder auf die Wiki-Seite.",
        "audience": "user",
        "versions": ["9.306.0"],
    },
    {
        "version": "9.305.0",
        "date": "2026-07-10",
        "title": "Projekt-Dokumente im Chat anpinnen",
        "body": "In Projekt-Chats gibt es jetzt ein Pin-Symbol im Eingabefeld: "
                "Damit heften Sie ausgewählte Projekt-Dokumente an den Chat — "
                "ihr vollständiger Inhalt wird dann jeder Anfrage automatisch "
                "mitgegeben, statt dass der Assistent die Unterlagen erst suchen "
                "muss. Ideal, wenn eine Aufgabe genau mit bestimmten Dokumenten "
                "arbeiten soll. Die Pins bleiben pro Chat gespeichert, und unter "
                "jeder Antwort sehen Sie, welche Quellen eingeflossen sind.",
        "audience": "user",
        "versions": ["9.305.0"],
    },
    {
        "version": "9.304.0",
        "date": "2026-07-10",
        "title": "Podcasts mit eigenen Sprechern — auch auf Deutsch",
        "body": "Der Audio Overview im Projekt-Studio lässt sich jetzt gestalten: "
                "Wählen Sie die Sprache der Folge (oder lassen Sie sie automatisch "
                "erkennen — ein deutsches Projekt ergibt jetzt wirklich einen "
                "deutschen Podcast), die Zahl der Sprecher (1 bis 4: Monolog, "
                "Dialog, Gesprächsrunde oder Panel) und geben Sie jedem Sprecher "
                "auf Wunsch einen Namen, eine Persönlichkeit („skeptische "
                "Expertin“) und eine eigene Stimme — auch selbst geklonte Stimmen "
                "stehen zur Auswahl. Ein neues Feld „Publikum“ richtet die Folge "
                "auf Ihre Zielgruppe aus.",
        "audience": "user",
        "versions": ["9.304.0"],
    },
    {
        "version": "9.303.0",
        "date": "2026-07-10",
        "title": "Antworten per Klick ins Wiki übernehmen",
        "body": "Unter jeder Antwort des Agenten gibt es jetzt ein "
                "Lesezeichen-Symbol: Ein Klick speichert genau diese Antwort als "
                "eigene Wiki-Seite — sofort auffindbar, bearbeitbar und in "
                "Projekten automatisch dem Projekt zugeordnet. Speichern Sie "
                "dieselbe Antwort später erneut, wird die vorhandene Seite als "
                "neue Version aktualisiert statt doppelt angelegt.",
        "audience": "user",
        "versions": ["9.303.0"],
    },
    {
        "version": "9.302.0",
        "date": "2026-07-10",
        "title": "Eigene Studio-Vorlagen — auch pro Quelle",
        "body": "Im Studio-Tab eines Projekts können Sie jetzt eigene Vorlagen "
                "anlegen: Sie beschreiben einmal, was aus den Quellen extrahiert "
                "werden soll (z. B. „Literatur-Review: Fragestellung, Methodik, "
                "Kernergebnisse, Limitationen“), und wenden die Vorlage danach "
                "per Klick an — wie die eingebauten Karten Study Guide oder FAQ. "
                "Auf Wunsch läuft eine Vorlage einzeln über jedes Dokument des "
                "Projekts und legt dabei automatisch eine Wiki-Seite pro Quelle "
                "an: So entsteht aus einem Ordner voller Papiere mit einem Klick "
                "eine strukturierte, durchsuchbare Wissensbasis. Alle Ausgaben "
                "bleiben wie gewohnt belegt und zitiert.",
        "audience": "user",
        "versions": ["9.302.0"],
    },
    {
        "version": "9.301.0",
        "date": "2026-07-10",
        "title": "Transkriptions-Modell direkt im Übersetzen-Tab wählen",
        "body": "In den Übersetzungs-Bereichen mit Spracherkennung — Audio/Video "
                "und Live-Mikrofon — gibt es jetzt eine Auswahl „STT-Modell“ in "
                "der Werkzeugleiste. Dort wählen Sie pro Durchlauf, welches "
                "Transkriptions-Modell die Spracherkennung übernimmt: etwa das "
                "schnelle lokale Whisper Turbo für saubere Aufnahmen oder die "
                "Cloud-Transkription für schwierige Tonqualität. Vorausgewählt "
                "ist immer das vom Administrator hinterlegte Standardmodell — "
                "wer nichts ändert, bekommt das bisherige Verhalten.",
        "audience": "user",
        "versions": ["9.301.0"],
    },
    {
        "version": "9.300.0",
        "date": "2026-07-10",
        "title": "Schnellere lokale Transkription mit Whisper Turbo",
        "body": "Für die Audio-Transkription steht jetzt zusätzlich „Whisper "
                "large-v3-turbo“ zur Wahl (Einstellungen → Werkzeuge → "
                "transcribe_audio). Das Turbo-Modell liefert auf sauberen "
                "Aufnahmen praktisch dieselbe Qualität wie das große "
                "Whisper-Modell, transkribiert aber deutlich schneller — und "
                "läuft wie bisher komplett lokal und kostenlos. Für schwierige "
                "Aufnahmen (Telefonmitschnitte, starkes Rauschen) bleibt das "
                "bisherige Standard-Modell bzw. die Cloud-Transkription die "
                "robustere Wahl; der Standard wurde deshalb nicht umgestellt.",
        "audience": "admin",
        "versions": ["9.300.0"],
    },
    {
        "version": "9.298.0",
        "date": "2026-07-10",
        "title": "Kalibrier-Spickzettel gegen erfundene Antworten",
        "body": "Neue Option „Kalibriert“ im Spickzettel-Regler eines Modells "
                "(Einstellungen → Allgemein → Modelle): Vor der Antwort muss das "
                "Modell offenlegen, welche Aussagen wirklich in Ihren Dokumenten "
                "stehen, was nur abgeleitet oder geraten ist und wo Lücken sind — "
                "und sich daran halten. Fragen, die Ihre Richtlinien nicht "
                "beantworten, bekommen so ein ehrliches „dazu steht nichts in den "
                "Dokumenten“ statt einer plausibel klingenden Vermutung; in "
                "Messungen stieg die Qualität genau solcher Antworten deutlich. "
                "Empfohlen für Compliance- und Richtlinien-Auskünfte; für breite "
                "Zusammenfassungen über viele Dokumente bleibt die bisherige "
                "Einstellung die bessere Wahl.",
        "audience": "admin",
        "versions": ["9.298.0"],
    },
    {
        "version": "9.297.0",
        "date": "2026-07-09",
        "title": "Kosten für OCR, TTS und Transkription pro Modell",
        "body": "Die nach Einheit abgerechneten Dienste (Texterkennung nach "
                "Seiten, Sprachausgabe nach Zeichen, Transkription nach "
                "Audio-Minuten) haben jetzt eigene Kostenfelder direkt am Modell "
                "(Einstellungen → Allgemein → Modelle) statt einer globalen "
                "Pauschale. So können Sie pro Modell den korrekten Preis "
                "hinterlegen — etwa $0,003/Minute für die Cloud-Transkription, "
                "während die lokale Transkription (Whisper) mit $0 zu Buche "
                "schlägt. Neu wird die Transkription überhaupt in der "
                "Kostenübersicht erfasst (bisher fehlte sie ganz); jede "
                "Transkription erscheint nun korrekt in der "
                "Kostenaufschlüsselung.",
        "audience": "admin",
        "versions": ["9.297.0"],
    },
    {
        "version": "9.296.0",
        "date": "2026-07-09",
        "title": "Transkriptions-Modelle klar getrennt",
        "body": "Bei den Modell-Fähigkeiten (Einstellungen → Allgemein → "
                "Modelle) gibt es jetzt eine eigene Fähigkeit „Transkription“, "
                "getrennt von „Audio“. Hintergrund: Manche Modelle können "
                "gesprochenes Audio zwar verstehen und darauf antworten, aber "
                "nicht wortgetreu abschreiben — nur echte Transkriptions-Modelle "
                "(Whisper, Voxtral-mini) leisten das zuverlässig. Das Auswahlfeld "
                "für die Audio-Transkription (Einstellungen → Werkzeuge → "
                "transcribe_audio) zeigt deshalb ab sofort nur noch Modelle, die "
                "tatsächlich transkribieren können. So lässt sich kein Modell mehr "
                "auswählen, das anschließend bei der Transkription scheitert.",
        "audience": "admin",
        "versions": ["9.296.0"],
    },
    {
        "version": "9.295.0",
        "date": "2026-07-09",
        "title": "Spickzettel — bessere Antworten schwächerer Modelle",
        "body": "Schwächere lokale Modelle antworten bei komplexen Fragen "
                "manchmal vorschnell. Neu können Sie pro Modell einen "
                "„Spickzettel“ aktivieren (Einstellungen → Allgemein → Modelle): "
                "Das Modell hält dann vor der Antwort kurz inne, notiert seinen "
                "Gedankengang und prüft ihn gegen die gefundenen Informationen. "
                "In Tests hob das die Qualität bei mehrschrittigen "
                "Richtlinien-Fragen deutlich. Vier Stufen stehen zur Wahl: Aus, "
                "Einfach, Erweitert (ausführlicher, aber langsamer) und "
                "Automatisch — bei „Automatisch“ entscheidet das System pro "
                "Anfrage selbst, ob und wie viel Nachdenken sinnvoll ist. "
                "Empfohlen für lokale Modelle; bei Cloud-Modellen sparsam "
                "einsetzen, da jeder zusätzliche Denkschritt Zeit und Kosten "
                "verursacht.",
        "audience": "admin",
        "versions": ["9.295.0"],
    },
    {
        "version": "9.294.3",
        "date": "2026-07-08",
        "title": "Service-Modelle greifen sofort",
        "body": "Änderungen an den Service-Modellen (Einstellungen → Allgemein "
                "→ Service-Modelle) wirken jetzt sofort — ein Server-Neustart "
                "ist nicht mehr nötig. Bisher lief z. B. der Skill- oder "
                "Workflow-Generator nach einer Umstellung stillschweigend "
                "weiter auf dem alten Modell. Zusätzlich behoben: Das für den "
                "Goal-Modus eingestellte Prüf-Modell wurde bisher gar nicht "
                "verwendet; jetzt greift es wie konfiguriert. Ausnahme bleibt "
                "das Telegram-Modell, das weiterhin einen Neustart erfordert.",
        "audience": "admin",
        "versions": ["9.294.3"],
    },
    {
        "version": "9.294.2",
        "date": "2026-07-07",
        "title": "Workflows nutzen Ihre Skills",
        "body": "Workflows und Skills arbeiten jetzt zusammen. Beim Erzeugen "
                "eines Workflows aus einem Chat oder Plan können Sie die "
                "Vorgehensweise als Skill auslagern (neu anlegen) oder einen "
                "bereits vorhandenen, passenden Skill auswählen — der Workflow "
                "verweist dann darauf, statt die Methode zu kopieren. Der "
                "Vorteil: Die Anleitung lebt an einer Stelle; verbessern Sie "
                "den Skill, profitieren alle Workflows, die ihn nutzen, sofort. "
                "Passende vorhandene Skills werden dabei automatisch "
                "vorgeschlagen — auch bei anders formulierter oder "
                "fremdsprachiger Aufgabenbeschreibung.",
        "audience": "user",
        "versions": ["9.294.1", "9.294.2"],
    },
    {
        "version": "9.294.0",
        "date": "2026-07-07",
        "title": "Aus einem Chat einen Skill erstellen",
        "body": "Wenn ein Chat eine gelungene Vorgehensweise gezeigt hat, "
                "können Sie diese jetzt mit einem Klick als wiederverwendbaren "
                "„Skill“ sichern (neue Schaltfläche 🎓 in der Eingabezeile). "
                "Brain-Agent destilliert aus dem Gespräch — oder aus dem "
                "freigegebenen Plan eines Experten-Gremiums — eine klare "
                "Anleitung (Auslöser, Voraussetzungen, Schritte, Fallstricke, "
                "Beispiel), die der Agent bei ähnlichen Aufgaben künftig "
                "eigenständig heranzieht. Der Entwurf wird Ihnen zunächst zur "
                "Prüfung vorgelegt — während der Erzeugung sehen Sie den "
                "Fortschritt als Checkliste mit Balken —; Sie passen ihn bei Bedarf an und legen fest, "
                "ob der Skill privat bleibt oder mit Ihrem Team bzw. allen "
                "geteilt wird — genau wie bei einem Chat. So wächst mit jeder "
                "guten Arbeit eine persönliche, teilbare Wissensbasis.",
        "audience": "user",
        "versions": ["9.294.0", "9.294.4"],
    },
    {
        "version": "9.293.2",
        "date": "2026-07-07",
        "title": "Bilder an Text-Modelle: exakt statt vage",
        "body": "Wenn Sie ein Bild an ein Modell anhängen, das Bilder nicht "
                "direkt „sehen“ kann, wird der Bildinhalt jetzt zuerst exakt und "
                "ohne KI-Aufruf aufbereitet: enthaltener Text wird per "
                "Texterkennung ausgelesen, QR- und Barcodes werden dekodiert, "
                "und sachliche Bildmerkmale (Größe, Aufnahmedatum, Ort, "
                "Farben, Anzahl erkannter Gesichter) werden ergänzt. So erhält "
                "das Modell bei Belegen, Rechnungen oder Screenshots verlässliche "
                "Fakten statt einer ungefähren Beschreibung — schneller, "
                "kostenlos und ohne Cloud. Nur wenn ein Bild keinerlei Text "
                "enthält (etwa ein reines Foto), wird zusätzlich wie bisher eine "
                "Bildbeschreibung erzeugt.",
        "audience": "user",
        "versions": ["9.293.2"],
    },
    {
        "version": "9.293.1",
        "date": "2026-07-07",
        "title": "Text aus Scans und Fotos zuverlässig auslesen",
        "body": "Brain-Agent kann Text jetzt direkt und verlässlich aus "
                "gescannten Bildern, Fotos und eingescannten PDFs herauslesen — "
                "etwa Rechnungen, Belege, Formulare oder Screenshots. Bisher "
                "„betrachtete“ das Modell ein angehängtes Bild nur und tippte "
                "Zahlen von Hand ab, was zu Lesefehlern führen konnte. Die neue "
                "Texterkennung arbeitet stattdessen exakt und wiederholbar: Sie "
                "können den kompletten Text extrahieren, gezielt einzelne Felder "
                "(z. B. Rechnungsnummer und Betrag) herausziehen, Tabellen als "
                "Datei gewinnen (die sich anschließend wie eine Excel-Tabelle "
                "weiterverarbeiten lässt) oder nur einen bestimmten Ausschnitt "
                "einer Seite lesen. Die Erkennung läuft vollständig lokal auf dem "
                "Server, ohne Cloud-Dienst.",
        "audience": "user",
        "versions": ["9.293.1"],
    },
    {
        "version": "9.293.0",
        "date": "2026-07-07",
        "title": "Kimi im Experten-Gremium wieder zuverlässig",
        "body": "Im Experten-Gremium (Mixture-of-Agents) steuert Kimi jetzt "
                "wieder verlässlich einen vollständigen Beitrag bei. Zuvor "
                "konnte Kimis Beitrag leer bleiben und fiel dann aus der "
                "Abstimmung heraus. Ursache war, dass Kimi über die "
                "Direktanbindung stets ausführlich „mitdenkt“ und das knappe "
                "Antwortbudget dadurch vollständig aufgebraucht wurde. Für "
                "Provider lässt sich nun das Anbindungsprotokoll einstellen "
                "(Provider-Verwaltung → „Wire-API“); für Kimi ist es so gesetzt, "
                "dass sich das Mitdenken sauber abschalten lässt und Werkzeug-"
                "aufrufe zugleich funktionieren — schneller und günstiger, ohne "
                "leere Beiträge.",
        "audience": "admin",
        "versions": ["9.293.0"],
    },
    {
        "version": "9.292.0",
        "date": "2026-07-07",
        "title": "GLM und Kimi: schneller und zuverlässiger angebunden",
        "body": "Die Modelle GLM und Kimi werden jetzt direkt bei ihren "
                "jeweiligen Anbietern angesprochen statt über einen "
                "zwischengeschalteten Dienst. Zuvor konnte GLM sporadisch mit "
                "einer Auslastungsmeldung („Rate Limit\") abbrechen, obwohl das "
                "GLM-Konto selbst gar nicht ausgelastet war — die Drosselung "
                "entstand erst auf dem Zwischenweg. Mit der Direktanbindung "
                "entfällt dieser Engpass; beide Modelle antworten zuverlässiger "
                "und profitieren vom anbietereigenen Zwischenspeicher (günstiger "
                "und schneller bei fortlaufenden Unterhaltungen). Kimi läuft "
                "dabei nun auf der aktuellen Version „K2.7 Code\" und wird in "
                "der Modellauswahl entsprechend angezeigt.",
        "audience": "admin",
        "versions": ["9.292.0"],
    },
    {
        "version": "9.291.3",
        "date": "2026-07-07",
        "title": "Workflow-Läufe: alle Dateien sichtbar, klarer bei Abbruch",
        "body": "Beim Öffnen eines Workflow-Laufs erscheinen jetzt zuverlässig "
                "ALLE Dateien, die der Lauf erzeugt hat, im Dateien-Reiter — "
                "auch solche, die ein Schritt selbst per Skript geschrieben hat "
                "(etwa Zwischenbilder oder Mess-Protokolle einer Analyse), und "
                "Bilder werden als Bild angezeigt statt als Rohdaten. Zuvor "
                "konnten einzelne im Bericht genannte Dateien fehlen. Die "
                "hochgeladenen Eingabedateien erscheinen jetzt — wie bei einem "
                "normalen Chat — im Anhänge-Reiter und lassen sich dort ansehen "
                "und herunterladen. Bricht ein Lauf vorzeitig ab — etwa weil "
                "ein Anbieter ein Nutzungslimit meldet —, weist ein deutlicher "
                "Hinweis oben im Verlauf darauf hin und stellt klar, dass die "
                "bis dahin erzeugten Teilergebnisse und Dateien gültig und "
                "nutzbar sind.",
        "audience": "user",
        "versions": ["9.291.3", "9.291.4"],
    },
    {
        "version": "9.291.0",
        "date": "2026-07-07",
        "title": "Bild-Anhänge funktionieren mit jedem Modell",
        "body": "Ein Bild-Anhang führt jetzt auch dann zu einem sinnvollen "
                "Ergebnis, wenn das antwortende Modell selbst keine Bilder "
                "sehen kann. Bislang konnte das — etwa nach einer Plan-"
                "Delegation im Experten-Gremium oder bei bewusster Wahl eines "
                "textbasierten Modells — zu einem Fehler führen. Ab sofort "
                "beschreibt in solchen Fällen automatisch ein bildfähiges "
                "Modell den Anhang, und das gewählte Modell arbeitet mit "
                "dieser Beschreibung weiter. Bildfähige Modelle sehen das Bild "
                "unverändert direkt. Die Vorauswahl passender Modelle bleibt "
                "erhalten — wählen Sie jedoch bewusst ein nicht-bildfähiges "
                "Modell, wird diese Wahl nun respektiert statt stillschweigend "
                "übergangen.",
        "audience": "user",
        "versions": ["9.291.0"],
    },
    {
        "version": "9.290.2",
        "date": "2026-07-07",
        "title": "Workflow-Läufe wie ein Chat",
        "body": "Ein Workflow-Lauf öffnet sich jetzt wie eine gewohnte "
                "Chat-Sitzung: Das Ergebnis des Laufs — die Antwort, die der "
                "Agent erarbeitet hat — erscheint im Hauptbereich als "
                "normale Chat-Nachricht in gewohnter Darstellung und "
                "aktualisiert sich live, während der Lauf arbeitet. Direkt "
                "darunter schreiben Sie wie in jedem Chat weiter. Die vom "
                "Lauf erzeugten Dateien liegen im Dateien-Reiter und lassen "
                "sich dort ansehen und herunterladen wie in jedem Chat. Die "
                "Detail-Ansichten sind in eigene Reiter der rechten "
                "Seitenleiste gewandert: Statistik (Modell, Dauer, Kosten, "
                "Status und die Aktionen zum Abbrechen, Herunterladen und "
                "Speichern), Quellcode und Protokoll.",
        "audience": "user",
        "versions": ["9.290.1", "9.290.2"],
    },
    {
        "version": "9.290.0",
        "date": "2026-07-06",
        "title": "Workflows aus Chats erzeugen",
        "body": "Ein gelungener Chat lässt sich jetzt per Klick in einen "
                "wiederverwendbaren Workflow verwandeln: Brain-Agent übernimmt "
                "den dort erarbeiteten Ausführungsplan und baut daraus einen "
                "Workflow, der dieselbe Arbeitsweise auf neue Eingaben "
                "anwendet — aus einer einmaligen Ausweisprüfung wird so eine "
                "Vorlage, bei der Kollegen nur noch das nächste Passbild "
                "hochladen und automatisch einen gleichwertigen Prüfbericht "
                "samt Qualitätskontrolle erhalten. Vier Wege führen dorthin: "
                "der neue Knopf in der Statusleiste des Chats, der Befehl "
                "/workflow im Terminal-Chat, der „Workflow“-Knopf auf "
                "Plan-Dokumenten in der Artefakt-Ansicht sowie „Neu aus "
                "Beschreibung“ im Workflows-Bereich, wo Sie den gewünschten "
                "Ablauf in eigenen Worten beschreiben (auf Wunsch mit "
                "beigelegten Markdown-Notizen). Jeder Entwurf wird vor dem "
                "Speichern im Editor zur Prüfung vorgelegt; der Plan selbst "
                "bleibt als eigener „Plan“-Reiter lesbar und anpassbar. Admins "
                "wählen unter Service-Modelle, welches Modell die Workflows "
                "entwirft.",
        "audience": "user",
        "versions": ["9.290.0"],
    },
    {
        "version": "9.289.2",
        "date": "2026-07-06",
        "title": "Experten-Gremium: Bild-Anhänge zuverlässig",
        "body": "Wenn Sie im Experten-Gremium eine Anfrage mit einem "
                "Bild-Anhang stellen und die Ausführung an ein günstigeres "
                "Modell delegiert wird, wird nun garantiert nur ein Modell "
                "gewählt, das Bilder auch verarbeiten kann. Zuvor konnte die "
                "Anfrage an ein reines Text-Modell geraten und scheitern. Die "
                "Auswahl gleicht die Dateitypen des Anhangs jetzt gegen die "
                "Fähigkeiten der Modelle ab.",
        "audience": "admin",
        "versions": ["9.289.2"],
    },
    {
        "version": "9.289.1",
        "date": "2026-07-05",
        "title": "Lokale Modelle im Experten-Gremium konfigurierbar",
        "body": "Die Gremium-Matrix in den Servereinstellungen zeigt jetzt "
                "auch lokale Modelle (gekennzeichnet mit „[lokal]“) und "
                "speichert sie zuverlässig — bisher fielen lokale Modelle "
                "beim Speichern der Matrix still heraus. Zusätzlich lässt sich "
                "über einen neuen Schalter erlauben, dass bei der "
                "Plan-Delegation ein lokales Modell die Ausführung übernimmt. "
                "Diese Option ist standardmäßig aus und für Tests gedacht: "
                "lokale Modelle arbeiten langsamer und überspringen "
                "gelegentlich vorgesehene Recherche-Schritte.",
        "audience": "admin",
        "versions": ["9.289.1"],
    },
    {
        "version": "9.288.0",
        "date": "2026-07-05",
        "title": "Gezielte Suche nach Wissenschaft, Technik, Bildern und Nachrichten",
        "body": "Zusätzlich zur allgemeinen Websuche kann der Assistent jetzt "
                "gezielt in passenden Quellen suchen: nach wissenschaftlichen "
                "Arbeiten (u. a. arXiv, PubMed, Google Scholar), nach "
                "Programmier- und Technikwissen (u. a. Stack Overflow, MDN, "
                "GitHub), nach Bildern sowie nach aktuellen Nachrichten. Der "
                "Assistent wählt die passende Suche selbst — Sie erhalten so "
                "relevantere Treffer für Fachfragen, Recherchen und Bildersuchen. "
                "In den Einstellungen unter Server → Websuche lässt sich jede "
                "dieser Suchen einzeln ein- oder ausschalten; der Zustand der "
                "dahinterliegenden Suchdienste wird dort je Kategorie angezeigt "
                "und alle vier Stunden automatisch geprüft.",
        "audience": "user",
        "versions": ["9.288.0"],
    },
    {
        "version": "9.287.2",
        "date": "2026-07-05",
        "title": "Websuche liefert wieder relevante Treffer",
        "body": "Die eingebaute Websuche hatte zuletzt teils völlig "
                "themenfremde Ergebnisse geliefert, weil sich die "
                "dahinterliegenden Suchdienste verändert hatten. Der "
                "Suchdienst-Pool wurde neu vermessen und bereinigt — "
                "Suchanfragen finden jetzt wieder zuverlässig die richtigen "
                "Seiten (z. B. das passende Profil oder die offizielle "
                "Museumsseite als Top-Treffer).",
        "audience": "user",
        "versions": ["9.287.2"],
    },
    {
        "version": "9.287.0",
        "date": "2026-07-05",
        "title": "HTML-Berichte mit echtem Titelbild",
        "body": "HTML-Berichte im Magazin-Layout beginnen jetzt — wie die "
                "Deep-Research-Berichte — nach Möglichkeit mit einem echten, "
                "thematisch passenden Titelbild statt eines abstrakten "
                "Farbbanners: Der Agent wählt ein Bild aus seinen "
                "Recherche-Quellen, oder es wird automatisch das Vorschaubild "
                "einer im Bericht zitierten oder während der Recherche "
                "besuchten Quelle übernommen. Nur wenn kein Bild gefunden "
                "wird, erscheint weiterhin der generierte Banner.",
        "audience": "user",
        "versions": ["9.287.0", "9.287.1"],
    },
    {
        "version": "9.286.0",
        "date": "2026-07-05",
        "title": "Experten-Gremium: bessere Pläne und geprüfte Ergebnisse",
        "body": "Das Experten-Gremium prüft seine Arbeit jetzt in zwei "
                "Stufen. Erstens: Findet der Orchestrator die Vorschläge der "
                "Experten für den Ausführungsplan zu schwach, lässt er genau "
                "die betreffenden Experten gezielt nachbessern — mit einer "
                "konkreten Begründung, was gefehlt hat — und plant dann neu. "
                "Zweitens: Nachdem das ausführende Modell geantwortet hat, "
                "beurteilt der Orchestrator im Chat, ob die Antwort den Plan "
                "und Ihre Anfrage wirklich erfüllt; falls nicht, lässt er das "
                "Modell gezielt nachbessern (bis zu zwei Durchläufe). So "
                "landen unvollständige Ergebnisse seltener bei Ihnen. Beide "
                "Prüfungen sind als eigene Karten im Chat nachvollziehbar; die "
                "Zahl der Nachbesserungen ist in den Server-Einstellungen "
                "wählbar (0 = nur protokollieren). Jede Entscheidung — "
                "Plan-Freigabe, Rückfrage, Executor-Wechsel und das Ergebnis "
                "der Ergebnis-Prüfung — erscheint als eigene 🧬-Karte im Chat "
                "und im Aktivität-Tab, jeweils mit Begründung. Beim Wählen des "
                "Ausführungs-Modells im Plan-Review sind ungeeignete Modelle "
                "ausgegraut (weiterhin wählbar, aber mit Warnung); das "
                "empfohlene Modell steht oben.",
        "audience": "user",
        "versions": ["9.286.0", "9.286.1", "9.286.2"],
    },
    {
        "version": "9.285.0",
        "date": "2026-07-05",
        "title": "Ausführungspläne vor dem Start prüfen und steuern",
        "body": "Wenn das Experten-Gremium einen Ausführungsplan delegiert, "
                "können Sie ihn im Chat und im Terminal-Chat jetzt VOR der "
                "Ausführung prüfen: den Plan direkt bearbeiten, dem "
                "Orchestrator Rückfragen oder Änderungswünsche geben (er legt "
                "daraufhin einen überarbeiteten Plan vor), das ausführende "
                "Modell wechseln — und erst mit Ihrer Freigabe startet die "
                "Arbeit. Automatische Abläufe (geplante Aufgaben, "
                "Hintergrund-Anfragen) bleiben ohne Zwischenstopp: dort "
                "beurteilt der Orchestrator selbst, ob sein Plan trägt, und "
                "führt nur dann aus. Jeder geprüfte Planstand — Entwurf, "
                "Überarbeitungen, Ihre freigegebene Fassung — wird zudem als "
                "versioniertes Artefakt der Sitzung abgelegt "
                "(ausfuehrungsplan.md, Versionsverlauf im Artefakte-Panel).",
        "audience": "user",
        "versions": ["9.285.0", "9.285.1"],
    },
    {
        "version": "9.284.0",
        "date": "2026-07-04",
        "title": "Experten-Gremium: Plan-Delegation an günstigere Modelle",
        "body": "Das Experten-Gremium kann pro Aufgabentyp jetzt delegieren: "
                "Das stärkste Modell fasst die Experten-Ansätze in einen "
                "konkreten Ausführungsplan zusammen, und ein günstigeres, "
                "automatisch zum Plan passendes Modell erledigt die "
                "eigentliche Werkzeug-Arbeit — das teure Modell denkt einmal, "
                "die vielen Recherche-Schritte laufen zum kleinen Tarif. Das "
                "gewählte Ausführungs-Modell bleibt für den Chat gepinnt, und "
                "der Plan ist im Chat als eigene aufklappbare Karte "
                "„Plan-Orchestrator“ einsehbar. Aktivierbar in den "
                "Server-Einstellungen über den neuen Beitrags-Modus "
                "„Plan-Delegation“ in der Gremium-Matrix.",
        "audience": "admin",
        "versions": ["9.284.0", "9.284.1", "9.284.2"],
    },
    {
        "version": "9.283.0",
        "date": "2026-07-04",
        "title": "Coding-Pläne und API-Guthaben im Blick",
        "body": "Die Plan-Nutzung zeigt jetzt Ihre Abo-Pläne und API-Guthaben "
                "als eigene Übersicht: pro Plan der Monatspreis und farbige "
                "Auslastungsbalken für jedes Zeitfenster (5 Stunden, Woche, "
                "Monat), bei Guthaben-Konten der verbrauchte und noch "
                "verfügbare Betrag seit der letzten Aufladung. Pläne legen "
                "Sie selbst an und verknüpfen sie pro Modell in den "
                "Modell-Einstellungen — angezeigt wird nur, was wirklich in "
                "Benutzung ist. Da die Anbieter keine Abfrage-Schnittstelle "
                "bieten, sind die Werte aus der eigenen Nutzung geschätzt und "
                "lassen sich mit einem Klick gegen das echte "
                "Anbieter-Dashboard kalibrieren.",
        "audience": "user",
        "versions": ["9.283.0", "9.283.1", "9.283.2", "9.283.3", "9.283.4", "9.283.5", "9.283.6"],
    },
    {
        "version": "9.281.0",
        "date": "2026-07-04",
        "title": "Ehrliche Kosten: Flatrate, Listenpreis und Cache-Ersparnis",
        "body": "Modelle, die über einen Coding-Plan oder eine Flatrate laufen, "
                "lassen sich jetzt pro Modell so kennzeichnen — sie werden mit "
                "0 $ verrechnet, und überall sehen Sie zusätzlich, was die "
                "Nutzung zum API-Listenpreis gekostet hätte: in der Statuszeile, "
                "im Terminal-Chat und in der Kostenaufstellung, die nun "
                "verrechnete Kosten, API-Kosten, Flatrate-Ersparnis und "
                "Caching-Ersparnis getrennt ausweist — als übersichtliche "
                "Tabelle mit festen Spalten; der Sitzungs-Inspektor zeigt "
                "dieselben Kennzahlen für die ganze Sitzung und pro Anfrage "
                "in derselben Reihenfolge. Außerdem: Ist eine "
                "gesuchte Information nicht in Ihren Quellen vorhanden, sagt "
                "der Assistent das jetzt als ersten Satz klar heraus, statt es "
                "in einer langen Antwort zu verstecken; Provider-Fehler (z. B. "
                "aufgebrauchtes Guthaben) erscheinen als sichtbare Meldung "
                "statt als leere Antwort; und beim Wiederverbinden mit einem "
                "laufenden Gespräch erscheinen Experten- und Datenschutz-Karten "
                "nicht mehr doppelt.",
        "audience": "user",
        "versions": ["9.281.0", "9.281.1", "9.282.0", "9.282.1"],
    },
    {
        "version": "9.280.0",
        "date": "2026-07-04",
        "title": "Terminal-Chat: Cache-Anzeige, helles Design, echte Reihenfolge",
        "body": "Die Statuszeile des Terminal-Chats in Code-Projekten zeigt "
                "jetzt auch die Prompt-Cache-Treffer (⚡ mit Trefferquote) und "
                "verlässliche Kosten — auch nach dem erneuten Öffnen eines "
                "früheren Verlaufs. Im hellen Design erscheint der "
                "Terminal-Chat nun hell statt immer dunkel; im dunklen Design "
                "bleibt der gewohnte CLI-Look erhalten. Außerdem erscheinen "
                "Denkschritte und Werkzeugaufrufe jetzt in der tatsächlichen "
                "Reihenfolge der Ausführung statt gruppiert.",
        "audience": "user",
        "versions": ["9.280.0"],
    },
    {
        "version": "9.279.0",
        "date": "2026-07-04",
        "title": "Neue Modelle erscheinen nicht mehr ungefragt",
        "body": "Wenn ein Anbieter neue Modelle in seinen Katalog aufnimmt, "
                "tauchten diese bisher automatisch aktiviert in der "
                "Modellauswahl auf — bei großen Katalogen wurde die Liste "
                "unübersichtlich. Ein neuer Schalter im Provider-Tab "
                "(„Neu entdeckte Modelle automatisch aktivieren“) legt jetzt "
                "fest, ob neue Modelle sofort nutzbar sind oder zunächst "
                "deaktiviert angelegt werden und bewusst freigeschaltet "
                "werden müssen.",
        "audience": "admin",
        "versions": ["9.279.0"],
    },
    {
        "version": "9.278.0",
        "date": "2026-07-04",
        "title": "Direktverbindung zu allen Cloud-Anbietern",
        "body": "Alle Cloud-Modelle (GLM, Kimi, DeepSeek, Mistral) kommunizieren "
                "jetzt ohne Zwischenstation direkt mit ihren Anbietern. Das "
                "entfernt eine ganze Fehlerquelle aus jedem Gespräch — weniger "
                "Verbindungsabbrüche, geringere Latenz und mehr gleichzeitige "
                "Anfragen möglich. Die Preisvorteile durch zwischengespeicherte "
                "Eingaben bleiben vollständig erhalten. Bestehende "
                "Unterhaltungen laufen unverändert weiter.",
        "audience": "admin",
        "versions": ["9.278.0"],
    },
    {
        "version": "9.277.2",
        "date": "2026-07-04",
        "title": "Websuche funktioniert auch nach einer Begrüßung",
        "body": "Begann eine Unterhaltung mit einer einfachen Begrüßung, blieb "
                "der Assistent bei einigen Modellen für den Rest der "
                "Unterhaltung auf ein Minimal-Werkzeugset festgelegt — eine "
                "spätere Frage nach z. B. dem Wetter wurde dann ohne "
                "Internetrecherche beantwortet. Jetzt wird jede Eingabe neu "
                "eingeschätzt und fehlende Werkzeuge (etwa die Websuche) "
                "werden automatisch nachgeladen. Außerdem erscheint das "
                "Klassifikations-Symbol unter der Antwort nun bei jeder "
                "Antwort, nicht nur bei der ersten.",
        "audience": "user",
        "versions": ["9.277.2"],
    },
    {
        "version": "9.277.1",
        "date": "2026-07-04",
        "title": "Thinking-Schalter wirkt bei allen Modellen",
        "body": "Einige Modelle (u. a. GLM, Kimi und DeepSeek) haben bisher "
                "auch dann „nachgedacht“, wenn das Thinking im Eingabefeld "
                "ausgeschaltet war — der Schalter wurde schlicht ignoriert. "
                "Jetzt gilt Ihre Einstellung verlässlich: Thinking aus heißt "
                "aus. Antworten dieser Modelle kommen ohne unerwünschten "
                "Denkblock, schneller und günstiger.",
        "audience": "user",
        "versions": ["9.277.1"],
    },
    {
        "version": "9.277.0",
        "date": "2026-07-03",
        "title": "Abgerissene Antworten setzen sich selbst fort",
        "body": "Wenn die Verbindung zum KI-Anbieter mitten in einer Antwort "
                "abreißt, setzt der Assistent die Antwort jetzt automatisch an "
                "genau der Abbruchstelle fort — das manuelle „weiter“/„continue“ "
                "entfällt. Kurze Netzwerk-Aussetzer beim Verbindungsaufbau "
                "werden automatisch erneut versucht, echte Anbieter-Fehler "
                "klar angezeigt statt als leere Antwort versteckt. Zusätzlich "
                "findet der Assistent nachgeladene Werkzeuge jetzt zuverlässig: "
                "Einmal entdeckt, bleiben sie für die ganze Unterhaltung "
                "nutzbar — auch bei Modellen, die ihre Werkzeugliste streng "
                "auslegen.",
        "audience": "user",
        "versions": ["9.277.0"],
    },
    {
        "version": "9.276.2",
        "date": "2026-07-03",
        "title": "Chat erkennt das Antwort-Ende zuverlässig",
        "body": "Bisher konnte es passieren, dass eine Antwort im Chat scheinbar "
                "nie fertig wurde — der Ladeindikator lief weiter, obwohl der "
                "Server längst fertig war, und erst ein Neuladen zeigte das "
                "Ergebnis. Ursache war eine unterbrochene Verbindung (z. B. "
                "nach Standby oder Netzwechsel), die der Browser nicht als tot "
                "erkennen konnte. Jetzt schließt der Server den Datenstrom nach "
                "jeder Antwort sauber, und der Browser erkennt eine tote "
                "Verbindung nach spätestens 45 Sekunden selbst — die Antwort "
                "erscheint dann automatisch, ohne Neuladen.",
        "audience": "user",
        "versions": ["9.276.2"],
    },
    {
        "version": "9.276.0",
        "date": "2026-07-03",
        "title": "Auto-Modellwahl: beste Intelligenz fürs Geld",
        "body": "Die ✨ Auto-Modellwahl wählt jetzt gezielt das Modell mit dem "
                "besten Verhältnis aus Fähigkeit und Preis: Unter den Modellen, "
                "die nahezu so leistungsfähig sind wie das beste verfügbare, "
                "gewinnt das günstigste — ein Spitzenmodell zum Bruchteil des "
                "Preises schlägt damit sowohl das teuerste als auch ein "
                "deutlich schwächeres, gleich billiges Modell. Bei besonders "
                "anspruchsvollen Aufgaben wird der Kreis automatisch enger "
                "gezogen (nur Top-Modelle), bei einfachen weiter — das senkt "
                "die Kosten, ohne Qualität zu opfern.",
        "audience": "admin",
        "versions": ["9.276.0"],
    },
    {
        "version": "9.275.0",
        "date": "2026-07-03",
        "title": "Modell-Benchmarks aus offiziellen Leaderboards",
        "body": "Die Fähigkeits-Bewertung der Modelle (Grundlage der ✨ "
                "Auto-Modellwahl) stammt jetzt aus offiziellen, laufend "
                "gepflegten Quellen — Artificial Analysis und LMArena — statt "
                "aus dem eigenen Mini-Test: objektiver, breiter abgestützt und "
                "immer auf aktuellem Stand. Die Geschwindigkeit wird weiterhin "
                "auf Ihrer eigenen Umgebung real gemessen. Im Modelle-Tab sehen "
                "Sie pro Wert die Quelle; ein optionaler (kostenloser) "
                "Artificial-Analysis-API-Key erweitert die Abdeckung, und "
                "Modelle ohne Leaderboard-Eintrag (z. B. lokale Modelle) werden "
                "wie bisher intern getestet.",
        "audience": "admin",
        "versions": ["9.275.0", "9.275.1"],
    },
    {
        "version": "9.274.1",
        "date": "2026-07-03",
        "title": "Aufgeräumt: Sidecar-Bereich entfernt",
        "body": "In den Allgemeinen Einstellungen unter „Server“ gibt es den "
                "Bereich „Sidecar“ nicht mehr — der zugehörige Hilfsprozess "
                "wurde bereits vor einigen Versionen durch eine schnellere, "
                "direkt integrierte Lösung ersetzt; der verbliebene Monitor "
                "samt Neustart-Schaltfläche war funktionslos. Die Monitore für "
                "Websuche (SearXNG) und Web-Rendering (crawl4ai) bleiben "
                "unverändert erhalten.",
        "audience": "admin",
        "versions": ["9.274.1"],
    },
    {
        "version": "9.274.0",
        "date": "2026-07-03",
        "title": "Experten-Gremium: Orchestrator pro Aufgabentyp wählbar",
        "body": "In der Gremium-Matrix (Einstellungen → Server) können Sie jetzt "
                "je Aufgabentyp festlegen, welches Modell die Experten-Beiträge "
                "zur finalen Antwort zusammenführt: „Auto (Smart)“ überlässt die "
                "Wahl wie bisher dem intelligenten Routing, oder Sie pinnen ein "
                "festes Modell — etwa Ihr stärkstes Modell für Recherche-Fragen. "
                "Das fest gewählte Modell wird automatisch aus dem Gremium der "
                "Spalte ausgelassen, damit es sich nicht selbst berät.",
        "audience": "admin",
        "versions": ["9.274.0"],
    },
    {
        "version": "9.273.0",
        "date": "2026-07-03",
        "title": "Benchmark-Läufe zeigen jetzt Live-Fortschritt",
        "body": "Wenn Sie in den Allgemeinen Einstellungen unter „Modelle“ einen "
                "Benchmark starten, sehen Sie jetzt direkt neben den Schaltflächen "
                "einen Fortschrittsbalken mit dem gerade getesteten Modell und "
                "Aufgabentyp — statt wie bisher minutenlang ohne Rückmeldung zu "
                "warten. Die Anzeige überlebt auch einen Tab-Wechsel oder das "
                "erneute Öffnen der Einstellungen: Läuft noch ein Benchmark, wird "
                "der Fortschritt automatisch wieder angezeigt.",
        "audience": "admin",
        "versions": ["9.273.0"],
    },
    {
        "version": "9.272.0",
        "date": "2026-07-03",
        "title": "Keine erfundenen Quellen, keine unnötigen Verweigerungen",
        "body": "Zwei ärgerliche Verhaltensmuster sind behoben: Der Assistent "
                "verweigerte gelegentlich Antworten auf allgemeine Fachfragen "
                "(„keine Quellen abrufbar“), und er schmückte Antworten mit "
                "Quellenangaben, deren Dokumente oder Zitate es gar nicht gab. "
                "Jetzt gilt: Fragen zu Ihren Dokumenten, Richtlinien und "
                "aktuellen Daten werden weiterhin nur mit echten Belegen "
                "beantwortet — allgemeines Fachwissen beantwortet der Assistent "
                "aber direkt und kennzeichnet es als solches, statt zu "
                "verweigern. Quellenangaben ohne tatsächlich abgerufene Quelle "
                "werden zusätzlich automatisch entfernt und transparent "
                "ausgewiesen.",
        "audience": "user",
        "versions": ["9.272.0"],
    },
    {
        "version": "9.271.0",
        "date": "2026-07-03",
        "title": "Experten-Gremium: Antwort oder Ansatz",
        "body": "Das MoA-Modell heißt jetzt „🧬 Experten-Gremium“ — und die "
                "Experten können pro Aufgabentyp zwei Arten von Beiträgen "
                "liefern: eine vollständige Entwurfs-Antwort (stark bei Wissens- "
                "und Urteilsfragen) oder nur einen Lösungsansatz — Schritte, "
                "Quellen, Fallstricke —, den das antwortende Modell dann mit "
                "seinen Werkzeugen ausführt (stark bei Recherchen, wo die "
                "Experten selbst nicht ins Web können). Den Modus wählen "
                "Administratoren je Aufgabentyp direkt in der Gremium-Matrix "
                "(Einstellungen → Server); für Recherche und Orchestrierung ist "
                "„Ansatz“ voreingestellt.",
        "audience": "user",
        "versions": ["9.271.0"],
    },
    {
        "version": "9.270.0",
        "date": "2026-07-03",
        "title": "MoA-Entwürfe einsehbar",
        "body": "Die 🧬-Karten der MoA-Referenzmodelle lassen sich jetzt "
                "aufklappen: Ein Klick zeigt den vollständigen Antwort-Entwurf, "
                "den das jeweilige Modell beigesteuert hat. Dieselben Karten "
                "erscheinen zusätzlich im Aktivität-Tab des rechten Panels — "
                "ebenfalls mit dem Entwurfstext. Außerdem behoben: Nach dem "
                "Neuladen eines Chats mit Datenschutz- oder MoA-Karten fehlte "
                "im Aktivität-Tab die Liste der ausgeführten Werkzeuge.",
        "audience": "user",
        "versions": ["9.270.0"],
    },
    {
        "version": "9.269.0",
        "date": "2026-07-02",
        "title": "MoA: Matrix „Modell × Aufgabentyp“",
        "body": "Die MoA-Konfiguration (Einstellungen → Server) ist jetzt eine "
                "übersichtliche Matrix: Sie haken pro Aufgabentyp (Recherche, "
                "Analyse, Berichte, …) genau die Modelle an, die dort Entwürfe "
                "liefern sollen. Eine Spalte ohne Häkchen bedeutet: für diesen "
                "Aufgabentyp läuft kein Fan-out — die Anfrage verhält sich wie "
                "eine normale Smart-Anfrage. Die bisherige getrennte Pflege von "
                "Modell-Pool und Aufgabentypen-Liste entfällt.",
        "audience": "admin",
        "versions": ["9.269.0"],
    },
    {
        "version": "9.268.0",
        "date": "2026-07-02",
        "title": "Neues Modell: 🧬 MoA (Smart)",
        "body": "In der Modellauswahl gibt es jetzt „🧬 MoA (Smart)“ (Mixture of "
                "Agents): Bei geeigneten Aufgaben — etwa Recherchen, Analysen oder "
                "Berichten — entwerfen mehrere Modelle gleichzeitig je einen "
                "Antwort-Vorschlag, und das automatisch gewählte Smart-Modell "
                "prüft die Entwürfe, gleicht Widersprüche ab und formuliert daraus "
                "die finale Antwort. Den Fortschritt der Entwürfe sehen Sie live "
                "als Karten im Chat. Bei Aufgaben, wo mehrere Meinungen nichts "
                "bringen (z. B. Programmierung oder Mathematik), läuft die Anfrage "
                "automatisch als normale Smart-Anfrage — ohne Zusatzkosten. "
                "Administratoren legen unter Einstellungen → Server fest, welche "
                "Modelle Entwürfe liefern dürfen und bei welchen Aufgabentypen "
                "sich der Mehraufwand lohnt.",
        "audience": "user",
        "versions": ["9.268.0", "9.269.1"],
    },
    {
        "version": "9.267.0",
        "date": "2026-07-02",
        "title": "Ziel-Prüfung erklärt ihre Entscheidung",
        "body": "Im Goal-Modus zeigt die Ziel-Prüfungs-Karte im Aktivität-Tab "
                "des rechten Panels jetzt, WARUM das Ziel noch nicht erreicht "
                "ist — und welche konkrete Anweisung der Assistent für den "
                "nächsten Durchlauf erhalten hat. Auch bei erreichtem oder "
                "abgebrochenem Ziel sehen Sie die Begründung des Prüf-Modells "
                "direkt in der Karte.",
        "audience": "user",
        "versions": ["9.267.0"],
    },
    {
        "version": "9.266.0",
        "date": "2026-07-02",
        "title": "Riesige Excel-Exporte, Format-Vergleich, Rückgängig",
        "body": "Excel-Exporte mit hunderttausenden Zeilen schreibt der Agent "
                "jetzt in einem speicherschonenden Streaming-Verfahren — auch "
                "500.000+ Zeilen sind kein Problem (Kopfzeile, fixierte "
                "Titelzeile und Summenzeile bleiben erhalten). Der "
                "Datei-Vergleich findet auf Wunsch auch reine "
                "Formatierungs-Änderungen: Zellen, deren Wert gleich blieb, "
                "deren Fettung, Farbe oder Zahlenformat sich aber geändert "
                "hat. Und beim Bearbeiten von Zellen in der Tabellen-Vorschau "
                "gibt es jetzt einen Rückgängig-Knopf, der die letzten "
                "Änderungen Schritt für Schritt zurücknimmt.",
        "audience": "user",
        "versions": ["9.266.0"],
    },
    {
        "version": "9.265.0",
        "date": "2026-07-02",
        "title": "VBA-Ansicht und Zellen-Bearbeitung im Panel",
        "body": "Makrofähige Excel-Dateien (.xlsm) zeigen in der "
                "Tabellen-Vorschau jetzt zusätzlich den VBA-Quellcode ihrer "
                "Makros — als eigene Reiter neben den Tabellenblättern, mit "
                "Syntax-Hervorhebung und Export als .bas-Datei (nur lesend; "
                "Makros werden nie ausgeführt, und das Zurückschreiben von "
                "VBA erfordert prinzipbedingt Excel). Außerdem können Sie "
                "Zellen jetzt auch direkt im rechten Panel unter „Dateien“ "
                "per Doppelklick bearbeiten — nicht mehr nur im unteren "
                "Arbeitsbereich.",
        "audience": "user",
        "versions": ["9.265.0"],
    },
    {
        "version": "9.264.0",
        "date": "2026-07-02",
        "title": "Excel-Vollausbau: bearbeiten, vergleichen, auswerten",
        "body": "Die Excel-Tabellenansicht in der App kann jetzt sortieren, "
                "suchen und — im unteren Arbeitsbereich — Zellen per "
                "Doppelklick direkt bearbeiten. Der Datei-Vergleich liefert "
                "auf Wunsch eine markierte Excel (geänderte Zellen gelb mit "
                "altem Wert als Kommentar, Neues grün, Entferntes rot), auch "
                "mit zusammengesetzten Schlüsseln und Formel-Vergleich. Der "
                "Agent erstellt echte Pivot-Auswertungen und bessere "
                "Diagramme (gestapelt, Punkt/Fläche, zweite Y-Achse), rechnet "
                "Formeln auf Wunsch sofort durch und liest auch alte .xls- "
                "und .ods-Dateien. In Projekten merkt sich Brain die Struktur "
                "jeder Excel automatisch — Fragen wie „welche Datei hat die "
                "Spalte Kundennummer?“ beantwortet es aus dem Gedächtnis. "
                "JSON- und XML-Dateien erscheinen im rechten Panel jetzt als "
                "aufklappbarer Datenbaum.",
        "audience": "user",
        "versions": ["9.264.0"],
    },
    {
        "version": "9.263.0",
        "date": "2026-07-02",
        "title": "Excel-Vorschau, -Vergleich und -Tiefenprüfung",
        "body": "Excel-Dateien öffnen sich jetzt direkt in der Oberfläche als "
                "Tabelle — mit Reitern pro Tabellenblatt, im Datei-Baum wie im "
                "Dateien-Panel; auch CSV-Dateien zeigen diese Ansicht. Der "
                "Agent kann außerdem zwei Dateien vergleichen (was wurde "
                "hinzugefügt, entfernt, geändert — auf Wunsch als komplette "
                "Änderungsliste), eine Tiefenprüfung durchführen (Duplikate, "
                "Ausreißer, verwaiste Verknüpfungen, Formel-Abhängigkeiten) "
                "und Daten in bestehende, fertig formatierte Excel-Vorlagen "
                "einfüllen, ohne deren Layout anzutasten. Komplexe Blätter "
                "mit mehreren Tabellen oder verbundenen Überschriften werden "
                "dabei korrekt erkannt, und Zwischenergebnisse lassen sich "
                "über mehrere Schritte hinweg weiterverwenden.",
        "audience": "user",
        "versions": ["9.263.0"],
    },
    {
        "version": "9.262.0",
        "date": "2026-07-02",
        "title": "Excel zuverlässig analysieren und erstellen",
        "body": "Excel-Aufgaben funktionieren jetzt mit jedem Modell zuverlässig — "
                "auch mit den kleinen lokalen. Der Agent versteht die Struktur "
                "einer angehängten Arbeitsmappe, verknüpft und wertet "
                "Tabellenblätter oder mehrere Dateien aus und erstellt daraus "
                "fertig formatierte neue Excel-Dateien (farbige Kopfzeile, "
                "fixierte Titelzeile, Zahlen- und Euro-Formate, Summenzeilen, "
                "Diagramme, auch gruppierte Master-Detail-Ansichten). Bestehende "
                "Dateien lassen sich gezielt erweitern, ohne dass ihre "
                "Formatierung verloren geht. Die Tabellendaten selbst bleiben "
                "dabei im System und laufen nicht durch das Sprachmodell — das "
                "macht die Ergebnisse reproduzierbar und schont bei großen "
                "Dateien die Kosten.",
        "audience": "user",
        "versions": ["9.262.0"],
    },
    {
        "version": "9.260.0",
        "date": "2026-07-02",
        "title": "HTML-Berichte automatisch im Magazin-Layout",
        "body": "Wenn Sie einen Bericht als HTML erstellen lassen, erhalten Sie "
                "jetzt automatisch das hochwertige, redaktionelle Layout — mit "
                "Titelkopf, Inhaltsverzeichnis am Rand, hell/dunkel-Umschaltung "
                "und druckfertiger Gestaltung, so wie es die Tiefenrecherche "
                "verwendet. Bisher entstand dieses schöne Layout nur, wenn Sie "
                "ausdrücklich einen „schönen“ Bericht anforderten; eine einfache "
                "Bitte wie „erstelle einen HTML-Report“ ergab noch das schlichtere "
                "Dokumentformat. Das ist jetzt der Standard für jeden HTML-Bericht.",
        "audience": "user",
        "versions": ["9.260.0"],
    },
    {
        "version": "9.259.0",
        "date": "2026-07-02",
        "title": "Saubere HTML-Berichte",
        "body": "Als HTML erzeugte Berichte werden jetzt durchgängig sauber "
                "formatiert. Bisher konnten in längeren Dokumenten vereinzelt "
                "Roh-Textzeichen sichtbar bleiben — etwa „---“-Striche zwischen "
                "Abschnitten, „>“ vor Hinweiszeilen, „-“ vor Aufzählungspunkten "
                "oder Sternchen im Fenstertitel. Diese werden nun korrekt in "
                "Trennlinien, hervorgehobene Zitatblöcke und echte Listen "
                "umgesetzt, und eine irrtümlich vorangestellte Kopfzeile wird "
                "entfernt. Ihre Berichte sehen damit so aus, wie sie gemeint sind.",
        "audience": "user",
        "versions": ["9.259.0"],
    },
    {
        "version": "9.258.0",
        "date": "2026-07-02",
        "title": "Zwischenfragen-Tab und sichtbare Turn-Aktivität",
        "body": "Nebenfragen (btw) haben jetzt einen eigenen Tab „Zwischenfragen“ "
                "im rechten Panel — mit eigenem Eingabefeld und Frage-Antwort-"
                "Verlauf, ohne die laufende Antwort zu stören und ohne den "
                "Chat-Verlauf zu verändern. Eingefügte Klarstellungen und der "
                "Goal-Modus zeigen ihre Aktivität jetzt als Karten im "
                "Aktivität-Tab: Sie sehen dort, ob eine Klarstellung noch wartet "
                "oder schon übernommen wurde, und beim Ziel die geplante "
                "Prüfung, das Prüfergebnis und jede zusätzliche Iteration.",
        "audience": "user",
        "versions": ["9.258.0"],
    },
    {
        "version": "9.257.0",
        "date": "2026-07-02",
        "title": "Klarere Kosten- und Fortschrittsanzeigen",
        "body": "Drei Verbesserungen der Transparenz. Deep-Research- und "
                "Studio-Berichte weisen in ihren Metadaten jetzt aus, wie viele "
                "Tokens aus dem Prompt-Cache bedient wurden (⚡ gecacht — diese "
                "kosten nur rund ein Zehntel); Admins können den Cache-Preis "
                "pro Modell unter Einstellungen → Modelle im neuen Feld "
                "„Kosten cached“ hinterlegen. Die Projekt-Synchronisierung "
                "zeigt beim Einlesen zusätzlich an, wie viele Dokumente bereits "
                "unverändert aktuell sind (z. B. „4/145 Dokumente · 258 "
                "unverändert“) — die Zahl liest sich nicht mehr, als wäre das "
                "der ganze Projektbestand. Außerdem wirkt der Befehl /caveman "
                "im Terminal-Chat jetzt tatsächlich auf die Sitzung (er wurde "
                "bisher nicht gespeichert).",
        "audience": "user",
        "versions": ["9.257.0"],
    },
    {
        "version": "9.256.0",
        "date": "2026-07-02",
        "title": "Goal-Modus: Ziel setzen, Agent arbeitet bis zur Erfüllung",
        "body": "Geben Sie einem Chat, dem Terminal-Chat oder einer geplanten "
                "Aufgabe ein Ziel — z. B. „Der Bericht enthält alle fünf "
                "Abschnitte und jede Zahl ist belegt.“ Nach jeder Antwort wird "
                "automatisch geprüft, ob das Ziel erreicht ist; wenn nicht, "
                "arbeitet der Assistent selbstständig weiter und verbessert das "
                "Ergebnis, bis es passt (mit einstellbarer Obergrenze an "
                "Durchläufen). Im Chat setzen Sie das Ziel über den neuen "
                "Zielscheiben-Knopf im Eingabefeld, im Terminal-Chat per "
                "/goal, bei geplanten Aufgaben direkt im Aufgaben-Editor. "
                "Aktive Ziele sind jederzeit sichtbar: farbiger Knopf im "
                "Eingabefeld, 🎯-Markierung in der Chat-Liste, Fortschritt "
                "(„Iteration 2/5“) in der Statusleiste — und jeder automatische "
                "Zwischenschritt bleibt als eigene Nachricht nachvollziehbar. "
                "Admins wählen unter Service-Modelle das Prüf-Modell und legen "
                "in den Eingabefeld-Standards die Obergrenze fest oder schalten "
                "den Goal-Modus ab.",
        "audience": "user",
        "versions": ["9.256.0"],
    },
    {
        "version": "9.255.0",
        "date": "2026-07-01",
        "title": "Klareres Deep-Research-Symbol + ruhigerer Arbeits-Spinner",
        "body": "Zwei kleine Verbesserungen im Chat. Das Symbol für die "
                "Tiefen-Recherche ist jetzt ein Wissenschaftler (Figur mit "
                "Kolben) statt des bisherigen Mikroskops — auf einen Blick "
                "erkennbar, wofür der Knopf steht. Außerdem steht die "
                "„arbeitet…“-Anzeige während einer Antwort nun direkt hinter dem "
                "gerade entstehenden Text bzw. den Denk-Schritten, statt lose am "
                "unteren Rand — so ist besser sichtbar, woran gerade gearbeitet "
                "wird.",
        "audience": "user",
        "versions": ["9.255.0"],
    },
    {
        "version": "9.254.0",
        "date": "2026-07-01",
        "title": "Laufende Antworten steuern: Warteschlange, Pause, Zwischenfragen",
        "body": "Sie müssen eine laufende Antwort nicht mehr abwarten oder "
                "abbrechen, um weiterzumachen. Vier neue Möglichkeiten — im "
                "normalen Chat wie im Terminal-Chat: (1) Schreiben Sie einfach "
                "weiter, während geantwortet wird — Ihre Nachrichten reihen sich "
                "in eine Warteschlange ein und werden automatisch nacheinander "
                "gesendet, sobald die aktuelle Antwort fertig ist; die "
                "Warteschlange lässt sich bearbeiten, umsortieren und leeren. "
                "(2) Pausieren Sie die Antwort und setzen Sie sie später fort — "
                "sauber am nächsten Schritt, ohne etwas zu verlieren. (3) Stellen "
                "Sie mit „btw“ eine Zwischenfrage: Ein Klick auf den btw-Knopf "
                "öffnet eine kleine Sprechblase direkt am Knopf, in der die Frage "
                "beantwortet wird, ohne die laufende Antwort zu stören — sie weiß "
                "sogar, was der Agent gerade tut, sodass Sie „Was machst du "
                "gerade?“ oder „Wie lange dauert das noch?“ fragen können. "
                "(4) Schieben Sie mitten in eine laufende Antwort eine "
                "Klarstellung nach, die im nächsten Schritt berücksichtigt wird. "
                "So kommen Sie schneller und gezielter zu besseren Antworten.",
        "audience": "user",
        "versions": ["9.254.0", "9.254.1"],
    },
    {
        "version": "9.253.0",
        "date": "2026-07-01",
        "title": "Terminal-Chat im echten CLI-Look",
        "body": "Der Terminal-Chat im Code-Modus ist jetzt im Stil eines echten "
                "Terminals gestaltet — durchgehend eine Monospace-Schrift in "
                "einheitlicher Größe, ruhige Terminalfarben und eine klare "
                "Struktur allein über Farbe statt Fettdruck. Das liest sich wie "
                "die Kommandozeile und hebt sich bewusst vom normalen Chat ab.",
        "audience": "user",
        "versions": ["9.253.0"],
    },
    {
        "version": "9.252.0",
        "date": "2026-07-01",
        "title": "Laufende Chats auf einen Blick",
        "body": "Chats, in denen gerade eine Antwort erstellt wird, tragen jetzt "
                "eine grüne „läuft\"-Markierung — sowohl in der Seitenleiste als "
                "auch in den Projekt-Chatlisten. So sehen Sie sofort, wo eine "
                "Antwort im Entstehen ist, ohne den jeweiligen Chat erst öffnen "
                "zu müssen.",
        "audience": "user",
        "versions": ["9.252.0"],
    },
    {
        "version": "9.251.0",
        "date": "2026-07-01",
        "title": "Chatliste nach Aktivität sortiert",
        "body": "Die Liste Ihrer Chats richtet sich jetzt danach, wann zuletzt "
                "etwas GESCHRIEBEN wurde — nicht mehr danach, wann Sie einen Chat "
                "zuletzt nur geöffnet haben. Ein Chat, den Sie lediglich zum "
                "Nachlesen aufrufen, rutscht dadurch nicht mehr an die Spitze; "
                "die Reihenfolge bleibt stabil und spiegelt Ihre tatsächliche "
                "Arbeit wider.",
        "audience": "user",
        "versions": ["9.251.0"],
    },
    {
        "version": "9.249.0",
        "date": "2026-07-01",
        "title": "HTML-Berichte im Magazin-Layout",
        "body": "Lassen Sie sich einen Bericht als HTML im edlen Magazin-Layout "
                "erstellen — dasselbe hochwertige Design wie bei der Deep-"
                "Recherche: warme Farbwelt, große Anfangsinitiale, elegante "
                "Überschriften, ein mitlaufendes Inhaltsverzeichnis am Rand, "
                "heller und dunkler Modus und druckfertig. Bitten Sie einfach um "
                "einen „schönen HTML-Report“; das bisherige, an Word/PDF "
                "angelehnte HTML-Format bleibt für alle anderen Fälle erhalten. "
                "Das Layout wurde zudem verfeinert: ein automatisch erzeugtes "
                "Titelbild, farbige Kennzahl-Kacheln für Kernwerte, ein auch im "
                "schmalen Vorschaufenster erreichbares Inhaltsverzeichnis und "
                "eine saubere, vollständige Umsetzung aller Textauszeichnungen "
                "(verschachtelte Listen, Zitate, Tabellen u. a.) — gleichwertig "
                "zur Word- und PDF-Ausgabe. Quellenangaben im Text erscheinen "
                "jetzt als kompakte nummerierte Verweise mit einem "
                "Quellenverzeichnis am Ende, statt als lange Klammern mitten im "
                "Satz.",
        "audience": "user",
        "versions": ["9.249.0", "9.249.1", "9.250.0"],
    },
    {
        "version": "9.246.0",
        "date": "2026-07-01",
        "title": "Cache-Ersparnis immer im Blick",
        "body": "Die Prompt-Cache-Nutzung wird jetzt durchgängig angezeigt — auch "
                "wenn (noch) nichts aus dem Cache kam: in der Statusleiste, bei "
                "jeder einzelnen Antwort und im Sitzungs-Inspektor sehen Sie die "
                "gecachten Tokens samt Trefferquote in Prozent und der daraus "
                "resultierenden Ersparnis in Euro. So ist auf einen Blick "
                "erkennbar, wie stark eine Unterhaltung vom günstigeren Cache-"
                "Tarif profitiert.",
        "audience": "user",
        "versions": ["9.246.0", "9.247.0", "9.247.1", "9.247.2", "9.248.0", "9.248.1", "9.248.2"],
    },
    {
        "version": "9.245.0",
        "date": "2026-06-30",
        "title": "Günstigere Antworten durch Prompt-Cache",
        "body": "Wiederkehrende Anfragen werden jetzt spürbar günstiger: Sobald "
                "ein Modell mit Cache-Tarif genutzt wird, hält Brain die Anfrage "
                "über die ganze Unterhaltung stabil, sodass der Anbieter den "
                "wiederholten Teil aus seinem Cache bedient — diese Tokens kosten "
                "nur einen Bruchteil (rund ein Zehntel) frischer Eingabe-Tokens. "
                "In der Statusleiste und bei jeder Antwort sehen Sie nun, wie "
                "viele Tokens aus dem Cache kamen (Symbol ⚡), und die "
                "Kostenübersicht weist die Cache-Treffer gesondert aus, damit die "
                "Ersparnis sichtbar wird. Für Modelle ohne Cache-Tarif bleibt "
                "alles wie bisher.",
        "audience": "user",
        "versions": ["9.245.0"],
    },
    {
        "version": "9.242.0",
        "date": "2026-06-30",
        "title": "Code-Editor: XML & JSON auf- und zuklappen",
        "body": "XML- und JSON-Dateien lassen sich im Code-Editor jetzt wie eine "
                "Baumstruktur erkunden. In der Ansicht sehen Sie die Daten als "
                "aufklappbaren Baum — Objekte, Listen und Werte mit einem Klick "
                "ein- und ausklappen, inklusive „Alles auf-/zuklappen“. Im "
                "Bearbeiten-Modus zeigen kleine Pfeile am linken Rand jede "
                "Verschachtelung, sodass Sie einzelne Abschnitte einklappen und "
                "lange Dateien übersichtlich halten können. Beides gilt für "
                "XML, SVG und JSON (auch JSON-Lines und GeoJSON). ShowCase-Dateien "
                "(.dbq) erhalten zusätzlich eine eigene Baum-Ansicht für ihre "
                "XML-Struktur.",
        "audience": "user",
        "versions": ["9.242.0", "9.242.1"],
    },
    {
        "version": "9.241.0",
        "date": "2026-06-30",
        "title": "Code-Modus: Symbole für SQL-Dateien",
        "body": "SQL- und ShowCase-Dateien zeigen jetzt ihre Bestandteile direkt "
                "im Datei-Baum: Klappen Sie eine Datei auf, sehen Sie die von ihr "
                "verwendeten Tabellen, die definierten Prozeduren und Views, "
                "etwaige CTEs sowie angesprochene Linked-Server — jeweils mit "
                "Sprung an die richtige Stelle. So erkennen Sie auf einen Blick, "
                "welche Datenquellen eine Abfrage anfasst, ohne den ganzen Code "
                "zu lesen. Diese Dateien gelten damit als vollständig indexiert.",
        "audience": "user",
        "versions": ["9.241.0"],
    },
    {
        "version": "9.240.0",
        "date": "2026-06-30",
        "title": "Code-Editor: SQL-Farbhervorhebung & ShowCase-Doppelansicht",
        "body": "SQL-Dateien werden im Code-Editor jetzt farblich hervorgehoben "
                "(Schlüsselwörter, Zeichenketten, Kommentare). ShowCase-Dateien "
                "(.dbq) — XML-Hüllen um eine SQL-Abfrage — lassen sich in zwei "
                "Ansichten öffnen und bearbeiten: „SQL (extrahiert)“ zeigt allein "
                "die Abfrage mit SQL-Hervorhebung, „XML-Quelle“ die vollständige "
                "Datei. Sie können in beiden Ansichten Änderungen vornehmen; beim "
                "Speichern wird die bearbeitete SQL automatisch wieder korrekt in "
                "die XML-Datei eingesetzt. Lange SQL-Zeilen werden im Editor "
                "automatisch umgebrochen, statt aus dem Bild zu laufen.",
        "audience": "user",
        "versions": ["9.240.0", "9.240.1"],
    },
    {
        "version": "9.239.0",
        "date": "2026-06-30",
        "title": "Code-Modus: SQL- und ShowCase-Dateien korrekt indexiert",
        "body": "Der Index-Status im Code-Dateibaum ist jetzt für SQL-Projekte "
                "verlässlich. Reine SQL-Skripte wurden bisher fälschlich als "
                "„nicht indexiert“ markiert, obwohl ihr Inhalt durchsuchbar war — "
                "sie erhalten nun einen eigenen Status „indexiert, ohne Symbole“, "
                "sodass ein echter Index-Fehler klar erkennbar bleibt. Zusätzlich "
                "wird das in ShowCase-Dateien (.dbq) eingebettete SQL automatisch "
                "ausgelesen und mitindexiert — diese Auswertungen sind damit "
                "erstmals durchsuchbar und werden im Dateibaum als indexiert "
                "angezeigt.",
        "audience": "admin",
        "versions": ["9.239.0"],
    },
    {
        "version": "9.238.0",
        "date": "2026-06-30",
        "title": "Code-Modus: Dateibaum, Symbole & Terminal-Chat verbessert",
        "body": "Der untere Code-Bereich wurde rundum verbessert: Im Dateibaum "
                "zeigt der Maus-Tooltip jetzt Größe und Änderungsdatum, und Sie "
                "sortieren die Dateien nach Art, Name, Datum oder Größe (die Wahl "
                "wird je Projekt gemerkt). Die Symbole eines Projekts stecken nun "
                "direkt im Dateibaum — klappen Sie eine Datei auf, sehen Sie ihre "
                "Klassen, Funktionen und Methoden; ein Suchfeld findet zugleich "
                "Dateien und Symbole. Im Terminal-Chat sitzt die Eingabe jetzt "
                "wie in einer Kommandozeile direkt unter der letzten Antwort, Text "
                "im Verlauf lässt sich markieren und kopieren, und Sie laden den "
                "Chatverlauf (oder die Terminal-Ausgabe) jederzeit als "
                "Markdown-Datei herunter.",
        "audience": "user",
        "versions": ["9.238.0"],
    },
    {
        "version": "9.237.1",
        "date": "2026-06-29",
        "title": "Code-Editor: R-Syntaxhervorhebung",
        "body": "R-Dateien (.R) werden im Code-Editor jetzt mit "
                "Syntaxhervorhebung angezeigt — Schlüsselwörter, Funktionen, "
                "Zeichenketten und Kommentare sind farblich abgesetzt, genau "
                "wie bei den übrigen Programmiersprachen.",
        "audience": "user",
        "versions": ["9.237.1"],
    },
    {
        "version": "9.237.0",
        "date": "2026-06-29",
        "title": "Code-Modus: anpassbare Arbeitsanweisung",
        "body": "Für Code-Projekte gibt es jetzt eine editierbare Arbeitsanweisung, "
                "die der Assistent bei jeder Code-Aufgabe befolgt — unabhängig von "
                "der Programmiersprache. Standardmäßig sorgt sie dafür, dass der "
                "Assistent zuerst den bereits indexierten Projekt-Code heranzieht "
                "statt zu raten, bei Änderungen die genutzten Abhängigkeiten und "
                "globalen Variablen benennt, stets vollständigen lauffähigen Code "
                "liefert und sich an den Projektstil hält. Administratoren können "
                "diesen Text unter Einstellungen → Tools frei anpassen oder leeren.",
        "audience": "admin",
        "versions": ["9.237.0"],
    },
    {
        "version": "9.236.0",
        "date": "2026-06-29",
        "title": "Code-Bereich: R-Auswertungen",
        "body": "Enthält Ihr Code-Projekt R-Skripte (.R), bietet der Knopf "
                "'Auswertungen' jetzt zusätzlich R-Analysen: alle Funktionen "
                "mit Aufruf-Häufigkeit (inklusive Warnung, wenn dieselbe "
                "Funktion mehrfach in verschiedenen Dateien definiert ist), den "
                "Daten-Fluss (welches Skript welche Dateien liest und "
                "schreibt), die Skript-Abhängigkeiten über source() sowie eine "
                "Übersicht zu Funktionsgröße und Nutzung globaler Variablen "
                "(ein Hinweis auf Wartungs- und Refactoring-Risiken). Pfad und "
                "Zeile sind anklickbar und springen direkt zur Stelle.",
        "audience": "user",
        "versions": ["9.236.0"],
    },
    {
        "version": "9.235.0",
        "date": "2026-06-29",
        "title": "Code-Bereich: SQL-Auswertungen",
        "body": "Enthält Ihr Code-Projekt SQL-Dateien (.sql oder .dbq), bietet "
                "der Knopf 'Auswertungen' jetzt zusätzlich SQL-Analysen: die "
                "meistgenutzten Tabellen über alle Abfragen (zentrale "
                "Datenquellen), die komplexesten Abfragen (nach Anzahl der "
                "Verknüpfungen — gute Review-Kandidaten), die Zugriffe auf "
                "externe Datenbanken (Linked Server) sowie ein Inventar aller "
                "Prozeduren und Views. Pfad und Zeile sind anklickbar und "
                "springen direkt zur Stelle. Funktioniert auch mit "
                "umfangreichen, gemischten SQL-Sammlungen.",
        "audience": "user",
        "versions": ["9.235.0"],
    },
    {
        "version": "9.234.2",
        "date": "2026-06-29",
        "title": "Terminal: Symbole & Schrift korrigiert",
        "body": "Im eingebauten Terminal werden Programme mit grafischen "
                "Oberflächen (z. B. Systemmonitore wie htop) jetzt korrekt "
                "dargestellt — Rahmen, Balken und Symbole erscheinen sauber "
                "statt als wirres Zeichengewirr. Außerdem nutzen Terminal und "
                "Terminal-Chat jetzt eine gut lesbare Terminal-Schrift (die "
                "Standard-Schrift Ihres Systems), die alle Sonderzeichen "
                "zuverlässig anzeigt.",
        "audience": "user",
        "versions": ["9.234.2"],
    },
    {
        "version": "9.234.0",
        "date": "2026-06-29",
        "title": "Code-Bereich: Symbol-Übersicht & Auswertungen",
        "body": "Unter dem Datei-Baum gibt es jetzt ein Panel 'Symbole': es "
                "zeigt alle Klassen, Methoden, Funktionen und Variablen des "
                "Projekts auf einen Blick, nach Datei gruppiert und durchsuchbar. "
                "Ein Klick springt direkt zur Definition; über das Pfeil-Symbol "
                "sehen Sie zu jedem Eintrag die Aufrufer und Verwendungsstellen — "
                "ebenfalls anklickbar. Neu ist außerdem der Knopf "
                "'Auswertungen': fertige Analysen Ihres Codes (komplexeste "
                "Funktionen, meistgenutzte Funktionen, größte Dateien, "
                "Klassenhierarchie und mehr) mit übersichtlich aufbereiteten "
                "Ergebnissen, aus denen Sie direkt in den Code springen können.",
        "audience": "user",
        "versions": ["9.234.0", "9.234.1"],
    },
    {
        "version": "9.233.0",
        "date": "2026-06-29",
        "title": "Code-Bereich: Fenster frei anordnen",
        "body": "Den Arbeitsbereich im Code-Modus teilen Sie jetzt frei per "
                "Ziehen auf — die feste Layout-Auswahl entfällt. Ziehen Sie "
                "einen Tab an den linken, rechten, oberen oder unteren Rand "
                "eines Bereichs, teilt er sich in diese Richtung; ziehen Sie "
                "ihn in die Mitte, wandert er einfach dorthin. Schließen Sie "
                "das letzte Fenster eines Bereichs, wird der Platz automatisch "
                "wieder freigegeben. Ihre Aufteilung wird pro Projekt gemerkt "
                "und beim nächsten Öffnen wiederhergestellt — beim Öffnen wird "
                "Ihr gespeicherter Arbeitsbereich wiederhergestellt (ein neues "
                "Terminal entsteht nur, wenn er ganz leer ist). Verlassen Sie "
                "das Projekt bzw. den Chat, aus dem der Bereich geöffnet wurde, "
                "schließt er sich automatisch.",
        "audience": "user",
        "versions": ["9.233.0", "9.233.1", "9.233.2"],
    },
    {
        "version": "9.232.0",
        "date": "2026-06-28",
        "title": "Code-Bereich: bessere Datei-Liste & Aufteilung",
        "body": "Mehrere Verbesserungen im Code-Bereich: Lange Dateinamen im "
                "Datei-Baum werden nun in voller Breite angezeigt statt unnötig "
                "abgeschnitten. Datei-Baum und Terminal-Chats teilen sich die "
                "linke Spalte standardmäßig zur Hälfte, und Sie können das "
                "Verhältnis per Ziehen anpassen — die Einstellung wird pro "
                "Projekt gemerkt. Im Terminal-Chat steht die Statuszeile jetzt "
                "unter dem Eingabefeld, das bei mehrzeiligen Eingaben "
                "automatisch mitwächst.",
        "audience": "user",
        "versions": ["9.232.0"],
    },
    {
        "version": "9.231.0",
        "date": "2026-06-28",
        "title": "Code-Projekte: Terminal direkt öffnen",
        "body": "In der Ansicht eines Code-Projekts gibt es jetzt oben einen "
                "„Terminal“-Knopf, der den Terminal- und Editor-Arbeitsbereich "
                "sofort im Vollbild öffnet — ohne dass Sie erst einen Chat "
                "starten müssen.",
        "audience": "user",
        "versions": ["9.231.0"],
    },
    {
        "version": "9.230.0",
        "date": "2026-06-28",
        "title": "Terminal-Chat: Abbrechen, schnellere Befehle, Aufräumen",
        "body": "Mehrere Verbesserungen im Terminal-Chat: Eine laufende Antwort "
                "lässt sich jederzeit mit der Esc-Taste oder „/cancel“ "
                "abbrechen — auch wenn der Cursor nicht im Eingabefeld steht. "
                "Befehle ohne weitere Optionen (z. B. /help) werden nun direkt "
                "mit einem Enter ausgeführt, und ein im Menü gewähltes Modell "
                "oder eine Denkstufe greift sofort. Außerdem erkennt der Chat "
                "das Ende einer Antwort jetzt zuverlässig (kein hängender "
                "„läuft…“-Hinweis mehr). In der Liste der Terminal-Chats können "
                "Sie einzelne Chats über das ✕ löschen oder alle auf einmal "
                "entfernen.",
        "audience": "user",
        "versions": ["9.230.0"],
    },
    {
        "version": "9.229.0",
        "date": "2026-06-28",
        "title": "Terminal-Chat: Befehls-Auswahl beim Tippen von „/“",
        "body": "Sobald Sie im Terminal-Chat ein „/“ eingeben, erscheint eine "
                "Auswahlliste der verfügbaren Befehle mit kurzer Beschreibung. "
                "Bei Befehlen mit Optionen — etwa /model oder /think — wird "
                "anschließend direkt die Liste der möglichen Werte angeboten "
                "(beim Modell die tatsächlich verfügbaren Modelle). Mit den "
                "Pfeiltasten wählen Sie aus, mit Enter oder Tab übernehmen Sie — "
                "so müssen Sie sich Befehle und Werte nicht merken.",
        "audience": "user",
        "versions": ["9.229.0"],
    },
    {
        "version": "9.228.0",
        "date": "2026-06-28",
        "title": "Dateibaum: Umbenennen, Löschen, Ordner, Verschieben",
        "body": "Der Dateibaum im Arbeitsverzeichnis lässt sich jetzt direkt "
                "bearbeiten: Per Rechtsklick benennen Sie Dateien und Ordner um "
                "oder löschen sie, legen neue Ordner und Dateien an; gelöschte "
                "Elemente wandern in einen Papierkorb (.brain-trash) und sind "
                "wiederherstellbar, statt unwiderruflich entfernt zu werden. "
                "Dateien und Ordner lassen sich außerdem per Ziehen in einen "
                "anderen Ordner verschieben. Neue Datei und neuer Ordner sind "
                "auch über die Knöpfe oben im Baum erreichbar.",
        "audience": "user",
        "versions": ["9.228.0"],
    },
    {
        "version": "9.227.0",
        "date": "2026-06-28",
        "title": "Code-Bereich: sinnvolle Standard-Platzierung + Live-Dateibaum",
        "body": "Beim Arbeiten mit geteiltem Bereich öffnen sich neue Inhalte "
                "jetzt automatisch dort, wo es Sinn ergibt: Quelldateien oben "
                "links, andere Dateien (HTML, Markdown …) oben rechts, Terminal "
                "und Terminal-Chat unten — passend zum gewählten Layout, mit "
                "sinnvollem Ausweichen, wenn eine Position im aktuellen Layout "
                "fehlt. Wer einen Bereich gezielt anspricht (über die "
                "Knöpfe direkt am Bereich), öffnet weiterhin genau dort. "
                "Außerdem aktualisiert sich der Dateibaum im Arbeitsverzeichnis "
                "nun von selbst, wenn Dateien hinzukommen oder gelöscht werden — "
                "etwa durch einen Terminal-Befehl, den „!“-Shell-Befehl oder ein "
                "externes Programm.",
        "audience": "user",
        "versions": ["9.227.0"],
    },
    {
        "version": "9.226.0",
        "date": "2026-06-28",
        "title": "Terminal-Chat: Shell-Befehle mit „!“",
        "body": "Im Terminal-Chat führen Sie mit einem vorangestellten "
                "Ausrufezeichen einen Befehl direkt in der Shell des "
                "Arbeitsverzeichnisses aus — etwa „! python "
                "wettervorhersage.py --region=München“ oder „! ls *.md“. Die "
                "Ausgabe erscheint sofort im Chat, ohne dass eine Anfrage an "
                "das Sprachmodell nötig ist. So wechseln Sie nahtlos zwischen "
                "Fragen an den Assistenten und schnellen Befehlen, ohne den "
                "Bereich zu verlassen.",
        "audience": "user",
        "versions": ["9.226.0"],
    },
    {
        "version": "9.225.0",
        "date": "2026-06-28",
        "title": "Code-Projekte: Chat direkt im Terminal-Bereich",
        "body": "In Code-Projekten gibt es jetzt einen Terminal-Chat: eine "
                "schlanke, terminalartige Chat-Oberfläche direkt im unteren "
                "Bereich, neben Terminal und Editor. Sie eignet sich als "
                "vollwertiger Ersatz für die normale Chat-Ansicht beim Arbeiten "
                "am Code — mit live gestreamten Antworten, sichtbaren "
                "Werkzeugaufrufen, einer Statuszeile (Modell, Denkstufe, Token, "
                "Kosten, Kontext) und Schrägstrich-Befehlen wie /model, /think, "
                "/clear, /lcm, /sync oder /init. Sie lässt sich wie jeder andere "
                "Bereich aufteilen — etwa links der Editor, rechts der Chat — und "
                "maximieren. Diese Chats werden pro Projekt gespeichert und in "
                "einer eigenen Liste „Terminal-Chats“ unter dem Datei-Baum "
                "geführt; sie erscheinen bewusst nicht in der normalen Chat-Liste, "
                "sondern bleiben dem Code-Bereich vorbehalten.",
        "audience": "user",
        "versions": ["9.225.0"],
    },
    {
        "version": "9.224.0",
        "date": "2026-06-28",
        "title": "Editor: Auto-Aktualisierung + externes Öffnen",
        "body": "Im Editor geöffnete Dateien aktualisieren sich jetzt automatisch, "
                "wenn sie sich auf der Platte ändern — etwa weil der Assistent, das "
                "Terminal oder ein externes Programm sie bearbeitet hat. Haben Sie "
                "selbst ungespeicherte Änderungen offen, wird nichts überschrieben; "
                "der Tab markiert stattdessen den Konflikt. Außerdem lassen sich "
                "Dateien, die sich nicht sinnvoll im Editor bearbeiten lassen "
                "(Word, Excel, PowerPoint, PDF, Medien), im jeweiligen externen "
                "Programm öffnen — per Klick im Datei-Baum oder über das "
                "Rechtsklick-Menü.",
        "audience": "user",
        "versions": ["9.224.0"],
    },
    {
        "version": "9.223.0",
        "date": "2026-06-28",
        "title": "Code-Projekte: geteilter Arbeitsbereich",
        "body": "Der untere Bereich mit Terminal und Editor lässt sich jetzt "
                "aufteilen — ideal, um zwei Dateien nebeneinander zu vergleichen "
                "oder oben den Editor und unten das Terminal zu sehen. Über den "
                "Aufteilungs-Knopf wählen Sie zwischen einzeln, links/rechts, "
                "oben/unten oder „L/R + unten“. Jeder Teilbereich hat eigene "
                "Tabs; Tabs lassen sich per Ziehen von einem Bereich in einen "
                "anderen verschieben, und die Größen sind verstellbar. Aufteilung, "
                "Größen und geöffnete Dateien werden pro Projekt gemerkt.",
        "audience": "user",
        "versions": ["9.223.0"],
    },
    {
        "version": "9.222.0",
        "date": "2026-06-28",
        "title": "Code-Editor: Vorschau gerenderter Dateien",
        "body": "In der Nur-Lesen-Ansicht des Code-Editors werden darstellbare "
                "Dateien jetzt gerendert statt als Quelltext gezeigt: HTML- und "
                "SVG-Dateien erscheinen als fertige Seite bzw. Grafik, Markdown als "
                "formatierter Text. Zum Bearbeiten wechseln Sie wie gewohnt in den "
                "Bearbeiten-Modus und sehen den Quelltext. Code-Dateien bleiben "
                "unverändert.",
        "audience": "user",
        "versions": ["9.222.0"],
    },
    {
        "version": "9.221.0",
        "date": "2026-06-28",
        "title": "Code-Projekte: Dateibaum neben dem Editor",
        "body": "Der Dateibaum Ihres Arbeitsverzeichnisses sitzt jetzt direkt links "
                "neben Terminal und Editor — im selben dunklen Look wie der Editor, "
                "ein- und ausblendbar und in der Breite verstellbar. Jede Datei zeigt "
                "auf einen Blick ihren Zustand: die Farbe des Namens spiegelt den "
                "Git-Status (geändert, neu, gelöscht …), ein Punkt markiert im Editor "
                "geöffnete Dateien mit ungespeicherten Änderungen, und die gerade "
                "bearbeitete Datei ist hervorgehoben. Mit zwei Knöpfen klappen Sie alle "
                "Ordner auf oder zu; ein optionaler Ein-Editor-Modus lässt jeden "
                "Klick die aktuelle Datei ersetzen, statt immer neue Tabs zu öffnen. "
                "Layout und geöffnete Dateien werden pro Projekt gemerkt. Der "
                "Code-Editor wurde überarbeitet: Ansicht und Bearbeiten sehen "
                "identisch aus (gleiche Zeilennummern und Farben, nur im "
                "Bearbeiten-Modus blinkt ein Cursor), der gesamte untere Bereich "
                "inklusive Terminal und Editor folgt jetzt dem Hell-/Dunkel-Modus, "
                "unten zeigt eine Statuszeile Größe, Zeilenzahl und Änderungsdatum, "
                "die Cursor-Position wird je Datei gemerkt, und der Tab zeigt „*“ "
                "bei ungespeicherten Änderungen. In Code-Projekten zeigt das rechte "
                "Panel außerdem nur noch die passenden Bereiche (Artefakte und "
                "Web-Adressen sind dort ausgeblendet).",
        "audience": "user",
        "versions": ["9.221.0"],
    },
    {
        "version": "9.220.0",
        "date": "2026-06-27",
        "title": "Code-Editor: Cypher-Abfragen (Power-User)",
        "body": "Für technische Auswertungen bietet der Code-Editor jetzt eine "
                "Cypher-Suchleiste (Knopf „Cypher“). Damit lassen sich gezielte "
                "Fragen an den Code-Index stellen — etwa „die komplexesten Methoden“, "
                "„alle Klassen mit Datei“ oder „Funktionen ohne Tests“. Fertige "
                "Beispiel-Abfragen stehen auf Knopfdruck bereit; das Ergebnis "
                "erscheint als Tabelle, und ein Klick auf einen Dateipfad öffnet die "
                "Datei direkt im Editor.",
        "audience": "admin",
        "versions": ["9.220.0"],
    },
    {
        "version": "9.219.0",
        "date": "2026-06-27",
        "title": "Code-Editor: Symbole finden und verstehen",
        "body": "Der Code-Editor kennt jetzt alle Symbole Ihres Projekts und macht sie "
                "direkt nutzbar. Mit Cmd/Strg+P (oder dem Knopf „Symbole“) öffnen Sie "
                "eine Schnellsuche über alle Funktionen, Methoden und Klassen und "
                "springen mit einem Klick an die richtige Stelle. Ein Rechtsklick auf "
                "ein Symbol bietet „Gehe zu Definition“ und „Wer ruft das auf?“. Beim "
                "Tippen schlägt der Editor passende Symbole vor (Strg-/Cmd-Leertaste), "
                "und wenn Sie mit der Maus über ein Symbol fahren, sehen Sie dessen "
                "Signatur, Beschreibung und wie oft es aufgerufen wird — alles aus dem "
                "Code-Index, ohne Wartezeit.",
        "audience": "user",
        "versions": ["9.219.0"],
    },
    {
        "version": "9.218.0",
        "date": "2026-06-27",
        "title": "Code-Projekte: integrierter Code-Editor",
        "body": "Im unteren Bereich von Code-Projekten können Sie Dateien jetzt nicht "
                "nur ansehen, sondern direkt bearbeiten: Ein Klick auf eine Datei "
                "öffnet sie als Tab mit Syntaxhervorhebung — umschaltbar zwischen "
                "formatierter Ansicht und Bearbeiten-Modus, mit Speichern und "
                "Herunterladen. Sie können neue Dateien anlegen, mehrere Editor- und "
                "Terminal-Tabs nebeneinander offen halten, Tabs gebündelt schließen "
                "(Rechtsklick) und den Bereich auf volle Höhe maximieren. Welche "
                "Dateien geöffnet sind, wird pro Projekt gemerkt — auch "
                "gerätübergreifend.",
        "audience": "user",
        "versions": ["9.218.0"],
    },
    {
        "version": "9.217.0",
        "date": "2026-06-27",
        "title": "Code-Projekte: integriertes Terminal",
        "body": "Code-Projekte haben jetzt ein vollwertiges Terminal direkt in der "
                "Oberfläche — am unteren Rand auf- und zuklappbar. Es öffnet "
                "automatisch im Arbeitsverzeichnis des Projekts und bleibt darauf "
                "beschränkt (ein Wechsel aus dem Projektordner heraus wird "
                "verhindert). Sie können mehrere Terminal-Tabs gleichzeitig "
                "betreiben, und die laufenden Sitzungen sind sowohl in der "
                "Projektansicht als auch im Projekt-Chat dieselben — Sie setzen "
                "also nahtlos dort fort, wo Sie waren.",
        "audience": "user",
        "versions": ["9.217.0"],
    },
    {
        "version": "9.216.0",
        "date": "2026-06-27",
        "title": "Code-Projekte: Dateivorschau & Downloads",
        "body": "In Code-Projekten können Sie jetzt direkt im Dateibaum auf eine Datei "
                "klicken, um sie in einer Vorschau anzusehen — mit übersichtlicher "
                "Syntaxhervorhebung und einem Umschalter zur unformatierten Roh"
                "ansicht; Markdown und Bilder werden passend dargestellt. Jede Datei "
                "lässt sich einzeln herunterladen, und über den Download-Knopf am "
                "Dateibaum erhalten Sie das gesamte Arbeitsverzeichnis als ZIP-Archiv "
                "(ohne technische Hilfsordner wie .git).",
        "audience": "user",
        "versions": ["9.216.0"],
    },
    {
        "version": "9.215.0",
        "date": "2026-06-27",
        "title": "Code-Projekte: stärkere Code-Suche & Index-Verwaltung",
        "body": "Code-Projekte verstehen jetzt deutlich größere Codebasen: Der Assistent "
                "findet Funktionen, Aufrufer und Zusammenhänge zuverlässig über eine "
                "automatisch aktualisierte Code-Indexierung — auch in sehr großen "
                "Projekten, in denen einfaches Durchsuchen bisher scheiterte. In der "
                "Projektansicht sehen Sie pro Datei den Index-Zustand (indexiert, veraltet, "
                "nicht indexiert) und können den Index mit einem Klick aktualisieren, neu "
                "aufbauen, den Code-Graphen ansehen oder den Verlauf prüfen — analog zur "
                "Wissens-Verwaltung normaler Projekte. Der Index hält sich bei Datei"
                "änderungen selbst aktuell; die Projektnotiz (BRAIN.md) konzentriert sich "
                "dadurch auf das Dauerhafte (Zweck, Konventionen) statt auf schnell "
                "veraltende Struktur.",
        "audience": "admin",
        "versions": ["9.214.0", "9.215.0"],
    },
    {
        "version": "9.213.3",
        "date": "2026-06-27",
        "title": "Klarere Denk-Anzeige & ruhigeres Mitlaufen",
        "body": "Die Gedankengänge des Assistenten sind im Chat jetzt deutlich besser "
                "erkennbar: Jeder Denkabschnitt ist mit einer eigenen Leiste und der "
                "Markierung „Denken“ vom eigentlichen Antworttext und den Werkzeug-Aufrufen "
                "abgesetzt, und aufeinanderfolgende Denkschritte werden zu einem "
                "übersichtlichen Block zusammengefasst statt vieler einzelner Zeilen. "
                "Außerdem läuft die Ansicht beim Schreiben einer Antwort ruhiger mit: Es "
                "wird nur dann automatisch nach unten gescrollt, wenn Sie sich ohnehin am "
                "Ende befinden — haben Sie nach oben gescrollt, um etwas nachzulesen, bleibt "
                "Ihre Position erhalten, und das frühere Hoch-/Runter-Springen bei längeren "
                "Antworten entfällt.",
        "audience": "user",
        "versions": ["9.213.3"],
    },
    {
        "version": "9.213.0",
        "date": "2026-06-26",
        "title": "Deep Research: Grafiken & Projektwissen",
        "body": "Die Deep-Research-Berichte enthalten jetzt, wo es den Inhalt klarer macht, "
                "automatisch Grafiken: Ablauf- und Zeitdiagramme, Balken-/Linien-/Tortendiagramme "
                "aus den im Bericht genannten Zahlen sowie passende Bilder aus den Quellen (inkl. "
                "eines Titelbilds) — direkt in die hochwertige HTML-Ansicht eingebettet. In einem "
                "Projekt-Chat bezieht die Recherche außerdem das gesamte Projektwissen mit ein "
                "(die im Projektgedächtnis abgelegten Quellen und Zusammenhänge), genau wie ein "
                "normaler Projekt-Chat — Anhänge und der bisherige Gesprächsverlauf fließen "
                "ohnehin ein. Ist die freie Websuche für den Chat unterbunden, weist ein Hinweis "
                "darauf hin, dass Deep Research erst mit aktivierter Websuche voll sinnvoll ist.",
        "audience": "user",
        "versions": ["9.213.0"],
    },
    {
        "version": "9.212.0",
        "date": "2026-06-26",
        "title": "Deep Research im normalen Chat",
        "body": "Die tiefe Recherche steht Ihnen jetzt auch im normalen Chat zur Verfügung — "
                "nicht mehr nur im Projektbereich. Über den neuen Schalter (Mikroskop-Symbol) "
                "unten im Eingabefeld aktivieren Sie sie: Ist der Schalter an, recherchiert die "
                "KI zu Ihrer nächsten Frage gründlich im Web (zerlegt sie in Teilfragen, sucht, "
                "liest die besten Quellen und schreibt einen belegten Bericht); ist er aus, "
                "läuft alles wie ein gewöhnlicher Chat. Das Ergebnis erscheint als hochwertiger "
                "HTML-Bericht im Artefakte-Bereich rechts (mit Quellenliste, druck- und teilbar) "
                "und liegt zusätzlich als Markdown bei. Der Schalter ist ausgegraut, solange kein "
                "Suchanbieter eingerichtet ist.",
        "audience": "user",
        "versions": ["9.212.0"],
    },
    {
        "version": "9.211.0",
        "date": "2026-06-26",
        "title": "Berichte als hochwertige HTML-Dokumente",
        "body": "Recherche-Berichte und Studio-Ausgaben erscheinen jetzt zusätzlich als "
                "professionell gestaltetes, eigenständiges HTML-Dokument im redaktionellen "
                "Magazin-Stil — mit gut lesbarer Typografie, automatischem Inhaltsverzeichnis, "
                "Quellenliste und heller/dunkler Darstellung. Sie öffnen den Bericht direkt im "
                "Studio in dieser ansprechenden Ansicht und können ihn per Klick herunterladen "
                "oder als PDF drucken — ideal zum Teilen. Bei der Deep Research wird das Layout "
                "zudem an die Art der Frage angepasst: Produktempfehlungen kommen als gerankte "
                "Liste mit Vergleichstabelle, ein Vergleich als übersichtliche Kriterien-Matrix, "
                "eine Anleitung als Schritt-für-Schritt und ein Faktencheck als Gegenüberstellung "
                "von Belegen mit klarer Bewertung. Das bisherige Markdown bleibt als Quelle "
                "erhalten; die HTML-Ansicht ist die neue, schönere Standarddarstellung.",
        "audience": "user",
        "versions": ["9.211.0"],
    },
    {
        "version": "9.209.0",
        "date": "2026-06-26",
        "title": "Berichte: Diagramme & Inhaltsverzeichnis",
        "body": "Diagramme in Word- und PDF-Berichten werden jetzt korrekt als Bild dargestellt: "
                "Ein im Text beschriebenes Ablauf- oder Gantt-Diagramm (Mermaid) wird automatisch "
                "in eine saubere, an das Corporate-Design angepasste Grafik umgewandelt und "
                "eingebettet — statt wie bisher als Roh-Text zu erscheinen. Außerdem füllt sich "
                "das Inhaltsverzeichnis in Word jetzt mit den richtigen Seitenzahlen, wenn das "
                "Dokument geöffnet wird (zuvor wurde fälschlich überall Seite 1 angezeigt), und "
                "die Abstände zwischen den Fußzeilenzeilen sind kompakter.",
        "audience": "user",
        "versions": ["9.209.0"],
    },
    {
        "version": "9.208.0",
        "date": "2026-06-26",
        "title": "Berichte: Fußzeile, Klassifizierung & KI-Hinweis",
        "body": "Erzeugte Word- und PDF-Berichte tragen in der Fußzeile jetzt automatisch drei "
                "saubere, jeweils eigene Zeilen: eine inhaltsbasierte Klassifizierung "
                "(Öffentlich / Intern / Vertraulich / Streng vertraulich, automatisch aus dem "
                "Dokumentinhalt abgeleitet), einen Transparenzhinweis gemäß EU-AI-Act, dass das "
                "Dokument mit Unterstützung künstlicher Intelligenz erstellt wurde, sowie die "
                "Seitenzahl im Format „Seite - N“ in derselben Schrift wie der übrige Fußzeilentext. "
                "Diese Angaben müssen nicht mehr von Hand eingetragen werden — die manuelle "
                "Fußzeile bleibt für eigene Inhalte frei. Außerdem überlappt ein Logo in der "
                "Kopfzeile nicht mehr die erste Überschrift.",
        "audience": "user",
        "versions": ["9.208.0"],
    },
    {
        "version": "9.207.0",
        "date": "2026-06-26",
        "title": "Word- und PDF-Berichte: sauberes Layout",
        "body": "Erzeugte Word- und PDF-Dokumente sehen jetzt deutlich professioneller aus. "
                "Das Inhaltsverzeichnis ist sofort mit echten Einträgen und Seitenzahlen gefüllt "
                "(bisher blieb es leer, bis man es manuell mit F9 aktualisierte). Überschriften und "
                "Tabellenzeilen brechen nicht mehr ungünstig über den Seitenrand um — eine "
                "Überschrift bleibt bei ihrem Text, eine Tabellenzeile bricht nicht mitten durch, und "
                "lange Tabellen wiederholen ihre Kopfzeile auf jeder Seite. Tabellenspalten werden "
                "nun nach ihrem Inhalt bemessen, sodass schmale Spalten nicht mehr jedes Wort "
                "einzeln umbrechen. Und am Dokumentende lässt sich eine Versionshistorie als Tabelle "
                "anführen, die automatisch auf einer eigenen Seite beginnt.",
        "audience": "user",
        "versions": ["9.207.0"],
    },
    {
        "version": "9.206.0",
        "date": "2026-06-25",
        "title": "Lokale Modelle ohne unnötige Wartezeit",
        "body": "Lokale Modelle stehen jetzt so schnell wie möglich bereit: Sie werden im "
                "Hintergrund vorgewärmt und einmal vorgewärmt auch warm gehalten, ohne dazwischen "
                "grundlos neu aufzuheizen. Bisher konnte ein bereits warmes Modell mitten in der "
                "Sitzung erneut aufwärmen — etwa beim Wechsel zwischen einem normalen Chat und einem "
                "Projekt-Chat auf demselben lokalen Modell — was eine kurze, unnötige Verzögerung "
                "verursachte. Das ist behoben: Es wird nur noch dann aufgewärmt, wenn es wirklich "
                "nötig ist. Spürbar ist das vor allem an einem schnelleren ersten Antwort-Beginn.",
        "audience": "user",
        "versions": ["9.206.0", "9.206.1"],
    },
    # ── 9.2xx — Datenschutz-Prüfung serverseitig ──
    {
        "version": "9.205.2",
        "date": "2026-06-25",
        "title": "Lokales Modell: keine Anonymisierung, keine Markierung",
        "body": "Wenn Sie ein lokales Modell wählen, werden Ihre Daten nicht mehr anonymisiert — auch "
                "dann nicht, wenn der Chat zuvor über ein Cloud-Modell lief und dort anonymisiert wurde. "
                "Ein lokales Modell verarbeitet alles auf Ihrem Gerät; die Daten verlassen es nicht, "
                "daher ist eine Anonymisierung weder nötig noch sinnvoll. Passend dazu blendet der "
                "Chat-Verlauf die Datenschutz-Markierungen aus, solange ein lokales Modell ausgewählt "
                "ist (es gibt nichts zu kennzeichnen). Wechseln Sie zurück zu einem Cloud-Modell, "
                "erscheinen die Markierungen wieder.",
        "audience": "user",
        "versions": ["9.205.2", "9.205.3", "9.205.4"],
    },
    {
        "version": "9.205.4",
        "date": "2026-06-25",
        "title": "Anzeige, wenn der Dienst noch hochfährt",
        "body": "Direkt nach einem Neustart braucht der Dienst einige Sekunden, bis alle Modelle "
                "geladen sind. Diese Phase ist jetzt sichtbar: der Verbindungspunkt in der Statuszeile "
                "leuchtet währenddessen gelb mit dem Hinweis „Server wird bereit …“ und wechselt erst "
                "auf grün „verbunden“, wenn alles geladen ist. Senden Sie in dieser kurzen Phase eine "
                "Nachricht, werden Sie mit einem kurzen Hinweis gebeten, einen Moment zu warten — so "
                "ist sichergestellt, dass die Datenschutz-Prüfung mit den korrekten Modell-"
                "Informationen arbeitet.",
        "audience": "user",
        "versions": ["9.205.4"],
    },
    {
        "version": "9.205.0",
        "date": "2026-06-25",
        "title": "Anhänge: Datenschutz-Prüfung mit Fortschritt beim Senden",
        "body": "Angehängte Dokumente werden für die Datenschutz-Prüfung jetzt beim Absenden geprüft — "
                "gemeinsam mit Ihrer Nachricht und unter einem einzigen Fortschritts-Fenster mit "
                "Abbrechen-Schaltfläche. Da die aufwändige Analyse (Texterkennung großer Dokumente) bei "
                "den Anhängen liegt, sehen Sie den Fortschritt nun dort, wo er tatsächlich anfällt "
                "(„Anhang wird geprüft …“), und können eine lange Prüfung jederzeit abbrechen. Die "
                "erkannten Daten werden anschließend wie gewohnt im Hinweis-Dialog vor dem Senden zur "
                "Entscheidung vorgelegt.",
        "audience": "user",
        "versions": ["9.205.0"],
    },
    {
        "version": "9.204.8",
        "date": "2026-06-25",
        "title": "Anonymisierte Werte überall im Verlauf markiert",
        "body": "Wenn Sie eine Angabe anonymisieren, die mehrfach im Chat vorkommt, wird sie jetzt in "
                "jedem betroffenen Beitrag farblich markiert — in Ihren Fragen wie in den Antworten —, "
                "nicht mehr nur an der Stelle, an der Sie sie anonymisiert haben. Das entspricht dem, was "
                "tatsächlich geschützt an das Sprachmodell übermittelt wird: Der Wert geht in allen "
                "betroffenen Beiträgen pseudonymisiert hinaus, und die Markierung im Verlauf zeigt das nun "
                "durchgängig an.",
        "audience": "user",
        "versions": ["9.204.8"],
    },
    {
        "version": "9.204.6",
        "date": "2026-06-25",
        "title": "Datenschutz-Übersicht zeigt verlässlich Ihre Entscheidungen",
        "body": "Die Datenschutz-Übersicht (Schild-Symbol) zeigt jetzt genau einen Eintrag pro Angabe, "
                "zu der Sie eine Entscheidung getroffen haben — mit aktuellem Status und vollständigem "
                "Verlauf (wer, wann, was). Zuvor konnten einzelne Angaben doppelt erscheinen — einmal mit "
                "Status und einmal fälschlich als „offen“ —, weil die Ansicht den Chat zusätzlich live "
                "durchsuchte und denselben Wert in leicht abweichender Schreibweise nicht zuordnen konnte. "
                "Die Übersicht liest die Einträge nun direkt aus den gespeicherten Entscheidungen, sodass "
                "keine Dubletten und keine irreführenden „offenen“ Einträge mehr entstehen. Die Werte "
                "werden im Klartext angezeigt (es ist Ihr eigener Chat) statt verdeckt, und Einträge aus "
                "dem Chat-Text erscheinen in einer einzigen Gruppe statt mehreren. Neue, noch nicht "
                "behandelte personenbezogene Daten werden weiterhin wie gewohnt im Hinweis-Dialog vor "
                "dem Senden abgefragt.",
        "audience": "user",
        "versions": ["9.204.6", "9.204.7"],
    },
    {
        "version": "9.204.0",
        "date": "2026-06-25",
        "title": "Datenschutz-Übersicht: Verlauf und einheitliches Design",
        "body": "Die Datenschutz-Übersicht (Schild-Symbol im Eingabefeld) sieht jetzt genauso aus wie "
                "der Hinweis-Dialog vor dem Senden — gleiches Design, gleiche Bedienung, sodass Sie "
                "sich sofort zurechtfinden. Zu jedem erkannten Datum sehen Sie nun den vollständigen "
                "Entscheidungs-Verlauf: WER WANN WAS entschieden hat (anonymisiert, im Klartext "
                "gesendet, als Falschtreffer markiert oder zurückgesetzt) — sowohl in der Übersicht "
                "als auch im Hinweis-Dialog. Für Chats mit sehr vielen Funden sind die Quellen "
                "(Nachrichten, Verlauf, einzelne Anhänge) jetzt standardmäßig eingeklappt und zeigen "
                "pro Gruppe die Anzahl und eine Status-Vorschau; lange Listen werden schrittweise "
                "nachgeladen, damit alles übersichtlich und schnell bedienbar bleibt.",
        "audience": "user",
        "versions": ["9.204.0", "9.204.1", "9.204.2", "9.204.3"],
    },
    {
        "version": "9.203.0",
        "date": "2026-06-25",
        "title": "Datenschutz-Übersicht: alles auf einen Blick erledigen",
        "body": "Das Bearbeiten erkannter personenbezogener Daten ist jetzt deutlich komfortabler — "
                "gerade in Chats mit sehr vielen Funden. Das Hinweis-Fenster vor dem Senden ist größer "
                "und übersichtlicher, mit nach Quelle gruppierten Funden. Neu ist eine große "
                "Datenschutz-Übersicht, die Sie über das Schild-Symbol im Eingabefeld öffnen: Sie zeigt "
                "ALLE erkannten Daten des gesamten Chats — aus Ihren Nachrichten, dem Verlauf und "
                "Anhängen — gruppiert nach Herkunft, mit Suche, Filter und dem jeweiligen Status "
                "(offen, anonymisiert, im Klartext gesendet, als Falschtreffer markiert). Mehrere Funde "
                "lassen sich auf einmal als Falschtreffer markieren, akzeptieren oder zurücksetzen, "
                "sodass Sie das Thema in einem Durchgang abschließen können. Außerdem behoben: das "
                "Schild-Symbol bleibt nach dem Neuladen eines Chats zuverlässig erreichbar.",
        "audience": "user",
        "versions": ["9.203.0", "9.203.1"],
    },
    {
        "version": "9.202.0",
        "date": "2026-06-24",
        "title": "Übergabe mit Vorschau und Ablage im Ursprungs-Chat",
        "body": "Beim Erstellen einer Übergabe in einen neuen Chat sehen Sie jetzt zuerst eine "
                "Vorschau der Zusammenfassung und entscheiden selbst, ob Sie sie übernehmen oder "
                "verwerfen — statt dass der neue Chat sofort und ungeprüft geöffnet wird. Während die "
                "Übergabe erstellt wird, zeigt ein Fortschritts-Hinweis den Status an. Die fertige "
                "Übergabe wird zudem automatisch als Dokument im ursprünglichen Chat abgelegt, sodass "
                "Sie jederzeit darauf zurückgreifen können.",
        "audience": "user",
        "versions": ["9.202.0"],
    },
    {
        "version": "9.201.0",
        "date": "2026-06-24",
        "title": "Anonymisierte Daten bleiben dauerhaft geschützt",
        "body": "Ein einmal anonymisierter Wert (z. B. eine E-Mail-Adresse) bleibt jetzt in jedem "
                "weiteren Verlauf der Unterhaltung geschützt — auch dann, wenn Sie in einem späteren "
                "Schritt für eine andere Angabe „Trotzdem senden“ wählen. Zuvor konnte ein früh "
                "anonymisierter Wert ab einem solchen Schritt wieder im Klartext an das Cloud-Modell "
                "gelangen. Das gilt gleichermaßen für Daten in Anhängen. Die Verlaufs-Übersicht zeigt "
                "außerdem wieder alle erkannten Angaben über die gesamte Unterhaltung, nicht nur die "
                "zuletzt entschiedene. Zudem behoben: ein wiederhergestellter Wert innerhalb eines "
                "Quellenverweises in der Antwort wird jetzt korrekt dargestellt (zuvor konnte dort "
                "fehlerhafter Code sichtbar werden).",
        "audience": "user",
        "versions": ["9.201.0", "9.201.1"],
    },
    {
        "version": "9.200.0",
        "date": "2026-06-24",
        "title": "Datenschutz-Prüfung mit Fortschritt und Abbrechen",
        "body": "Die Erkennung personenbezogener Daten läuft jetzt vollständig auf dem Server — eine "
                "einheitliche, zuverlässige Prüfung statt zwei parallelen. Vor dem Senden erscheint bei "
                "längeren Prüfungen ein Fortschritts-Hinweis mit Abbrechen-Schaltfläche, sodass eine "
                "aufwändige Prüfung (z. B. großer Text oder Anhang) Sie nie ohne Rückmeldung warten "
                "lässt und jederzeit abgebrochen werden kann.",
        "audience": "user",
        "versions": ["9.200.0"],
    },
    # ── 9.19x — Dokument-Erzeugung, KI-Projektanweisungen, PII-Präzision ──
    {
        "version": "9.199.0",
        "date": "2026-06-24",
        "title": "Datenschutz-Markierung zuverlässiger, Ansicht pro Chat gemerkt",
        "body": "Personenbezogene Daten, die Sie bewusst im Klartext gesendet haben (z. B. „Trotzdem "
                "senden“ über ein Cloud-Modell), werden jetzt zuverlässig im Chat farblich markiert — "
                "auch nach dem Neuladen der Seite. Der Schalter „Datenschutz-Details sichtbar“ steuert "
                "dabei einheitlich alle Markierungen: ist er aus, bleibt der Chat unmarkiert. Zusätzlich "
                "merkt sich jeder Chat einzeln, ob die Details ein- oder ausgeblendet sind, und stellt "
                "diese Ansicht beim Wiederöffnen wieder her. Die Datenschutz-Hinweise im Aktivitäts-"
                "bereich sind außerdem auf eine kompakte Titelzeile reduziert — ohne aufklappbare "
                "Detail-Tabelle.",
        "audience": "user",
        "versions": ["9.199.0", "9.199.1", "9.200.1", "9.200.2"],
    },
    {
        "version": "9.198.0",
        "date": "2026-06-24",
        "title": "Datenschutz im Chat-Verlauf sichtbar",
        "body": "Personenbezogene Daten sind jetzt direkt im Chat-Verlauf farblich markiert: anonymisierte "
                "Werte in Gelb (mit Anzeige des verwendeten Pseudonyms, z. B. „a@b.de → person1@…“), und "
                "Werte, die Sie bewusst im Klartext gesendet oder als Falschtreffer eingestuft haben, in Rot "
                "mit dem Hinweis „nicht anonymisiert“ — so sehen Sie auf einen Blick, was geschützt wurde und "
                "was nicht. Der Tooltip am Schild-Symbol zeigt nun zu jedem Treffer Wert, Konfidenz und "
                "Ausgang im Detail. Der frühere Knopf zum Zurücksetzen der Datenschutz-Wahl entfällt — die "
                "Prüfung läuft automatisch und fragt nur bei wirklich neuen Funden nach.",
        "audience": "user",
        "versions": ["9.198.0"],
    },
    {
        "version": "9.197.0",
        "date": "2026-06-24",
        "title": "Datenschutz-Hinweis nur noch bei neuen Funden",
        "body": "Der Hinweis vor dem Senden erscheint jetzt nur noch, wenn wirklich NEUE personenbezogene "
                "Treffer dabei sind, die Sie noch nicht geprüft haben — bereits geprüfte Werte lösen ihn "
                "nicht erneut aus. Im Dialog sind die Treffer übersichtlich in „bereits gesehen“ (fixiert) "
                "und „neu“ (zu prüfen) sowie nach Nachrichtentext und Anhang gegliedert; bei vielen Treffern "
                "lassen sich die Bereiche auf- und zuklappen und gesammelt als Falschtreffer markieren. "
                "Auch Treffer in Anhängen (Dokumente, Tabellen) werden jetzt einzeln mit vollem Wert und "
                "Konfidenz angezeigt und sind einzeln bewertbar. Die Verlaufsanzeige zeigt statt der Rohwerte, "
                "wie viele Treffer bestätigt bzw. als Falschtreffer eingestuft wurden.",
        "audience": "user",
        "versions": ["9.197.0"],
    },
    {
        "version": "9.196.0",
        "date": "2026-06-24",
        "title": "Datenschutz-Dialog: Treffer einzeln prüfen",
        "body": "Der Hinweis vor dem Senden zeigt jetzt zu jedem erkannten personenbezogenen Treffer den "
                "vollständigen Wert und eine Konfidenz-Bewertung — und Sie können jeden Treffer einzeln als "
                "Falschtreffer markieren. Als falsch markierte Werte werden nicht anonymisiert und für den "
                "restlichen Chat gemerkt, sodass Sie nicht erneut gefragt werden; nur neu hinzugekommene "
                "Treffer lösen den Dialog wieder aus. Ihre Entscheidungen werden gespeichert und lassen sich "
                "auswerten. Die Modellauswahl ist außerdem nicht mehr durch erkannte Daten eingeschränkt — "
                "Sie wählen das Modell frei, der Dialog regelt Anonymisieren bzw. lokale Verarbeitung. "
                "(Die Sperre für streng vertraulich klassifizierte Dokumente bleibt unverändert bestehen.)",
        "audience": "user",
        "versions": ["9.196.0"],
    },
    {
        "version": "9.195.0",
        "date": "2026-06-24",
        "title": "Datenschutz-Prüfung mit Konfidenz-Schwellen",
        "body": "Die Datenschutz-Prüfung entscheidet jetzt feiner, wann sie eingreift: Jeder gefundene "
                "personenbezogene Treffer erhält eine Konfidenz-Bewertung, und zwei einstellbare "
                "Schwellenwerte teilen das Ergebnis in drei Bereiche. Unsichere Funde mit niedriger "
                "Konfidenz werden ignoriert und stören den Arbeitsfluss nicht mehr; im mittleren Bereich "
                "wird nachgefragt, was geschehen soll (ignorieren, anonymisieren oder lokal verarbeiten); "
                "nur bei hoher Konfidenz greift automatisch die für die jeweilige Regel hinterlegte Aktion. "
                "Wie oft derselbe Treffer in einem Dokument vorkommt, fließt dabei in die Bewertung ein — "
                "mehrfaches Auftreten erhöht die Konfidenz. Die bisherige globale „Hart-Blockieren“-Option "
                "entfällt; die Schwellenwerte ersetzen sie und sind im DSGVO-Tab einstellbar.",
        "audience": "admin",
        "versions": ["9.194.0", "9.195.0"],
    },
    {
        "version": "9.193.0",
        "date": "2026-06-24",
        "title": "Treffsicherere Erkennung personenbezogener Daten",
        "body": "Die Datenschutz-Prüfung erkennt Personennamen, Organisationen und Anschriften jetzt "
                "deutlich treffsicherer: Statt jedes großgeschriebene Wort zu markieren, wird ein Name nur "
                "noch dann als personenbezogen gewertet, wenn echte Personen-Hinweise vorliegen (z. B. eine "
                "Anrede wie „Herr/Frau/Dr.“ oder ein klarer Vor- und Nachname). Häufige Fehlalarme auf "
                "deutschen Fachbegriffen („Datenschutzvorfall“, „Benutzerkennwörter“) und auf internen "
                "Abkürzungen („ARL“, „DSG“, „DSGVO“) entfallen, während echte Produkt- und Systemnamen "
                "weiterhin erkannt werden. Auch Anschriften zählen nur noch dann, wenn sie konkret "
                "identifizieren (mit Hausnummer oder Postleitzahl) — eine bloße Stadt oder ein Land allein "
                "löst keinen Treffer mehr aus. Die Trefferquote auf echten personenbezogenen Daten bleibt "
                "dabei erhalten.",
        "audience": "admin",
        "versions": ["9.193.0", "9.193.1"],
    },
    {
        "version": "9.190.0",
        "date": "2026-06-23",
        "title": "Deutlich schönere Word- & PDF-Dokumente",
        "body": "Erzeugte Word-, PDF- und PowerPoint-Dateien sehen jetzt durchgängig professionell aus: "
                "Deckblatt, Inhaltsverzeichnis, saubere Überschriften, farbcodierte Risiko-Badges und "
                "echte Tabellen statt Roh-Markdown. Listen, Zitate, Code und Links werden korrekt "
                "dargestellt, und der Assistent kann auf Wunsch das Aussehen einer angehängten "
                "Vorlagendatei übernehmen, statt einen festen Stil aufzudrängen.",
        "audience": "user",
        "versions": ["9.190.0", "9.191.0", "9.192.0", "9.192.2"],
    },
    {
        "version": "9.189.0",
        "date": "2026-06-22",
        "title": "Projektanweisungen von der KI erstellen lassen",
        "body": "Im Anweisungen-Dialog eines Projekts gibt es jetzt den Modus „Anweisung mit KI erstellen“: "
                "Sie beschreiben kurz Ziel und Ergebnis, der Assistent liest die hinterlegten "
                "Referenzdateien und das Projektwissen, recherchiert bei Bedarf im Web und verfasst daraus "
                "eine vollständige, gut strukturierte Projektanweisung. Das Ergebnis landet zum Prüfen im "
                "Editor — gespeichert wird erst nach Ihrer Freigabe.",
        "audience": "user",
        "versions": ["9.189.0"],
    },
    {
        "version": "9.182.0",
        "date": "2026-06-22",
        "title": "Chats automatisch archivieren & aufräumen",
        "body": "Auf Wunsch räumt sich die Chatliste selbst auf: Lange nicht geöffnete, rein private und nicht "
                "gemerkte Chats werden nach einer einstellbaren Frist archiviert und später endgültig "
                "gelöscht. Gemerkte, geteilte oder favorisierte Chats bleiben unberührt. Die Funktion ist "
                "standardmäßig aus und wird vom Admin in den Allgemeinen Einstellungen unter „Bereinigung“ "
                "aktiviert und eingestellt.",
        "audience": "admin",
        "versions": ["9.182.0"],
    },
    {
        "version": "9.181.0",
        "date": "2026-06-22",
        "title": "Denk-Stufe pro Chat & Eingabefeld-Standards",
        "body": "Die Denk-Stufe (Aus/Niedrig/Mittel/Hoch) merkt sich jetzt jeder Chat einzeln — öffnen Sie "
                "einen Chat erneut, ist seine eigene Einstellung wieder da, statt von anderen Chats "
                "überschrieben zu werden. Zusätzlich lassen sich Standard-Denkstufe und -Antwortstil für "
                "neue Chats festlegen (pro Nutzer in den Benutzereinstellungen, global durch den Admin).",
        "audience": "user",
        "versions": ["9.181.0"],
    },
    {
        "version": "9.179.0",
        "date": "2026-06-21",
        "title": "Mehr Webseiten erfolgreich abrufen",
        "body": "Der Web-Abruf kommt jetzt auch an Seiten heran, die hinter einem Bot-Schutz (z. B. "
                "Cloudflare-Prüfung) liegen — eine zusätzliche Abruf-Stufe löst solche Prüfungen "
                "automatisch und holt den echten Seiteninhalt, wo vorher nur eine leere Hülle ankam.",
        "audience": "user",
        "versions": ["9.179.0"],
    },
    {
        "version": "9.178.0",
        "date": "2026-06-21",
        "title": "Automatische Kontext-Verdichtung & Chat-Übergabe",
        "body": "Wird ein Gespräch sehr lang, kann der Verlauf jetzt automatisch verlustfrei verdichtet werden, "
                "damit nichts wegen Platzmangel abbricht — ein Badge zeigt, wie viel eingespart wurde, "
                "und ältere Teile werden bei Bedarf wieder entfaltet (vom Admin pro Modell aktivierbar). "
                "Neu in jedem Chat: ein „Übergabe“-Knopf erzeugt eine saubere Zusammenfassung des bisherigen "
                "Stands plus den vollständigen Verlauf, mit der Sie nahtlos in einem frischen Chat weiterarbeiten.",
        "audience": "user",
        "versions": ["9.178.0"],
    },
    {
        "version": "9.164.0",
        "date": "2026-06-19",
        "title": "Schnellere, treffsichere Anfrage-Vorbereitung",
        "body": "Vor jeder Eingabe analysiert die App automatisch, welches Modell am besten passt und welche "
                "Werkzeuge wirklich gebraucht werden, und reduziert den Rest — das macht Antworten "
                "zuverlässiger und schneller und hält die Kosten niedrig. Admins können jetzt für diese "
                "Analyse, die Chat-Zusammenfassung, das Wiki, das Nutzerprofil und viele weitere "
                "Hintergrund-Dienste jeweils ein eigenes Modell wählen — alle gebündelt im Panel "
                "„Service-Modelle“, jeder Dienst getrennt und ohne Seiteneffekte aufeinander.",
        "audience": "admin",
        "versions": ["9.164.0", "9.165.0", "9.166.0", "9.167.0", "9.168.0", "9.169.0",
                     "9.170.0", "9.171.0", "9.173.0", "9.174.0", "9.175.0"],
    },
    {
        "version": "9.161.0",
        "date": "2026-06-18",
        "title": "Code-Projekte: direkt im Code-Ordner arbeiten",
        "body": "Neben den gewohnten Gedächtnis-Projekten gibt es jetzt Code-Projekte: Sie wählen einen "
                "Arbeitsordner (meist Ihren Quellcode), und der Assistent liest, bearbeitet und erstellt "
                "Dateien direkt dort. Ein Befehl „init“ lässt ihn das Verzeichnis erkunden und eine "
                "Projektnotiz (BRAIN.md) als Gedächtnis anlegen. Ein eigener Arbeitsverzeichnis-Tab im "
                "Chat zeigt den Dateibaum mit Live-Aktualisierung und Inline-Vorschau jeder Datei.",
        "audience": "user",
        "versions": ["9.161.0", "9.161.1", "9.161.2", "9.161.3", "9.162.0", "9.163.0"],
    },
    {
        "version": "9.159.0",
        "date": "2026-06-18",
        "title": "Begleitdateien & Quellgruppen für Projekte",
        "body": "Projekten lassen sich jetzt Begleitdateien beilegen (Styleguide, Vorlage, Begriffsliste) — "
                "der Assistent liest sie wie Anhänge bei Bedarf, ohne sie ins Projektgedächtnis aufzunehmen. "
                "Beim Import ganzer Ordner werden die Quellen automatisch in virtuelle Gruppen einsortiert "
                "(z. B. ein Ordner je Kunde), sodass die App Inhalte sauber dem richtigen Kunden zuordnet "
                "und nicht vermischt. Import läuft mit Fortschrittsanzeige, Abbrechen-Möglichkeit und "
                "klarer Fehlerliste.",
        "audience": "user",
        "versions": ["9.158.0", "9.159.0", "9.160.0", "9.160.4", "9.160.5", "9.160.6", "9.157.0"],
    },
    {
        "version": "9.156.0",
        "date": "2026-06-17",
        "title": "Live-Fortschritt bei Werkzeug-Aufrufen",
        "body": "Werkzeug-Aufrufe zeigen jetzt direkt im Chat, was gerade passiert (z. B. „Seite 3 von 12“ "
                "beim Auslesen eines PDFs oder „Seite wird gerendert“ beim Web-Abruf), statt scheinbar "
                "stillzustehen. PDFs werden zudem zuverlässiger ausgelesen — selbst tabellenlastige "
                "Dateien, die früher ins Leere liefen, liefern jetzt sauberen Text.",
        "audience": "user",
        "versions": ["9.156.0"],
    },
    {
        "version": "9.150.0",
        "date": "2026-06-16",
        "title": "Dokument-Stile bequem als Formular",
        "body": "Das Aussehen erzeugter Dokumente (Schriften, Farben, Größen, Kopf-/Fußzeile, Logo) richten "
                "Sie jetzt in einem komfortablen Formular-Editor mit Farbwähler und Live-Vorschau ein — "
                "kein YAML-Tippen mehr. Lässt das Modell beim Erstellen den Stil weg, wird automatisch ein "
                "Standard-Stil (oder der des Projekts) angewendet, sodass Reports immer einheitlich "
                "professionell aussehen. Dokumente können jetzt auch als gestyltes .html erzeugt werden.",
        "audience": "user",
        "versions": ["9.148.0", "9.149.0", "9.150.0", "9.151.0", "9.152.0", "9.153.0", "9.154.0"],
    },
    {
        "version": "9.146.0",
        "date": "2026-06-16",
        "title": "Echte, scharfe Diagramme",
        "body": "Der Assistent erstellt jetzt echte Diagramme (Organigramme, Flussdiagramme, Zeitleisten, "
                "Gantt, Tortendiagramme) mit gestochen scharfer, lesbarer Beschriftung — direkt im Chat "
                "angezeigt und einbettbar in Reports und Präsentationen, statt verschwommener "
                "KI-Bilder. Die Diagramme übernehmen automatisch die Farben und Schriften Ihres "
                "Dokument-Stils.",
        "audience": "user",
        "versions": ["9.146.0", "9.147.0", "9.154.5", "9.154.7"],
    },
    {
        "version": "9.144.0",
        "date": "2026-06-16",
        "title": "Quellenangaben auch in Tabellen & Fazit",
        "body": "Die Quellen-Disziplin gilt jetzt auch für Tabellenzeilen mit Zahlen und für "
                "zusammenfassende Sätze (z. B. ein Fazit), nicht nur für Aufzählungspunkte — "
                "Antworten zu Bilanzen und Berichten sind dadurch lückenlos belegt.",
        "audience": "user",
        "versions": ["9.144.0"],
    },
    {
        "version": "9.142.0",
        "date": "2026-06-16",
        "title": "Bessere PDF- & Excel-Verarbeitung",
        "body": "PDFs werden sauberer ausgelesen — Tabellen aus Finanzberichten bleiben als echte Tabellen "
                "erhalten. Zusätzlich versteht die App jetzt makrofähige und binäre Excel-Dateien "
                "(.xlsm/.xlsb) und kann sogar den Code hinterlegter Makros lesen (ohne ihn auszuführen). "
                "Admins wählen die Auslese-Engine pro Dateityp in den Einstellungen.",
        "audience": "user",
        "versions": ["9.142.0", "9.143.0"],
    },
    {
        "version": "9.140.0",
        "date": "2026-06-16",
        "title": "Verlinkte Dokumente auf Projekt-Webseiten finden",
        "body": "Verweist eine im Projekt hinterlegte Webseite auf herunterladbare Dokumente (z. B. eine "
                "Veröffentlichungsseite mit Berichts-PDFs), spürt die App diese Links auf und schlägt sie "
                "Ihnen zur Auswahl vor — Sie entscheiden per Häkchen, was ins Projekt aufgenommen wird. "
                "Web-Adressen im Quellbaum lassen sich jetzt außerdem direkt in einem neuen Tab öffnen.",
        "audience": "user",
        "versions": ["9.140.0"],
    },

    # ── 9.10x–9.13x — Wiki, Chat-Anzeige, Einstellungen ──
    {
        "version": "9.133.0",
        "date": "2026-06-15",
        "title": "Lebendigere Chat-Anzeige",
        "body": "Während der Assistent antwortet, sehen Sie jetzt direkt im Chat eine lebendige Statuszeile "
                "mit wechselnden Tätigkeits-Wörtern und dem aktiven Modell. Denkschritte und "
                "Werkzeug-Aufrufe erscheinen fortlaufend im Gesprächsfluss, statt in einem separaten "
                "Bereich versteckt zu sein.",
        "audience": "user",
        "versions": ["9.117.0", "9.133.0"],
    },
    {
        "version": "9.128.0",
        "date": "2026-06-15",
        "title": "Echte Vorschauen in der Artefakt-Übersicht",
        "body": "Die Artefakt-Übersicht zeigt für nahezu jeden Dateityp eine echte Vorschau direkt auf der "
                "Karte: Bild-Thumbnails, abspielbare Audio-/Video-Player, gerenderte Markdown- und "
                "HTML-Seiten sowie farbig hervorgehobenen Code — ohne die Karte erst öffnen zu müssen. "
                "Erzeugte Bilder bekommen sprechende Dateinamen aus dem Bildmotiv.",
        "audience": "user",
        "versions": ["9.128.0", "9.129.0"],
    },
    {
        "version": "9.119.0",
        "date": "2026-06-14",
        "title": "Übersichtlichere Einstellungen",
        "body": "Die Einstellungsdialoge sind aufgeräumter: Lange Erklärtexte sind hinter ein „?“-Symbol "
                "gewandert und erscheinen bei Bedarf als Hinweis, die Schriften sind einheitlich und "
                "Rollen auf Deutsch beschriftet. Der Chat hat zudem mehr Zeilenabstand für bessere "
                "Lesbarkeit.",
        "audience": "user",
        "versions": ["9.119.0"],
    },
    {
        "version": "9.118.0",
        "date": "2026-06-14",
        "title": "Wissensgraph ohne KI erstellen",
        "body": "Admins können die Wissensgraph-Erstellung jetzt wahlweise rein regelbasiert und lokal "
                "laufen lassen — ohne KI-Aufruf, nichts verlässt den Rechner — getrennt einstellbar für "
                "Projekte und Wiki, global wie auch pro Projekt.",
        "audience": "admin",
        "versions": ["9.118.0"],
    },
    {
        "version": "9.102.0",
        "date": "2026-06-13",
        "title": "Das Wiki: Ihr editierbares Gedächtnis",
        "body": "Brandneu: ein sichtbares, editierbares Wiki, das zugleich das Langzeitgedächtnis des "
                "Assistenten ist — mit eigenem Bereich, Seitenbaum, komfortablem Editor, Versionshistorie "
                "und Wiederherstellen alter Stände. Inhalte fließen automatisch hinein: gemerkte Chats "
                "werden zu sauberen Themenseiten zusammengefasst, Berichte und Ihr Aktivitätsprofil landen "
                "als Seiten, geplante Aufgaben können Ergebnisse als neue Version ablegen. Seiten lassen "
                "sich mit Tags ordnen, gruppieren, verschachteln und um Bild/Audio/Video ergänzen — je "
                "Seite gibt es Zusammenfassung, Podcast und Vorlesen.",
        "audience": "user",
        "versions": ["9.102.0", "9.103.0", "9.104.0", "9.105.0", "9.106.0", "9.108.0",
                     "9.111.0", "9.112.0", "9.113.0", "9.114.0", "9.115.0", "9.116.0"],
    },
    {
        "version": "9.101.4",
        "date": "2026-06-11",
        "title": "Werkzeuge pro Anwendungsfall steuern",
        "body": "Admins können den Status jedes Werkzeugs (aktiv/inaktiv/zurückgestellt) jetzt getrennt pro "
                "Kanal festlegen — Chat, Transformation, Memory, Research und Brainy — statt nur global. "
                "Zusätzlich lässt sich die Beschreibung jedes Werkzeugs anpassen (mit Zurücksetzen auf den "
                "Standard). Der Klassifikations-Inspektor zeigt pro Anfrage genau, welche Werkzeuge "
                "tatsächlich bereitgestellt wurden und welche Recherche-Disziplin galt.",
        "audience": "admin",
        "versions": ["9.101.4", "9.101.6", "9.101.10"],
    },

    # ── 9.8x–9.9x — Smart-Modi, Datenschutz, Kosten, Audio ──
    {
        "version": "9.98.0",
        "date": "2026-06-09",
        "title": "Zwei Smart-Modi: Cloud & Lokal",
        "body": "Der automatische Modus „✨ Auto“ ist jetzt aufgeteilt in „✨ Smart (Cloud)“ und "
                "„✨ Smart (Lokal)“ — Sie wählen, ob das automatisch passende Modell aus der Cloud oder "
                "von einem lokalen Modell kommt, und die Wahl bleibt pro Sitzung erhalten. Die "
                "automatische Werkzeug-Optimierung ist nun ein eigener, pro Agent abschaltbarer Schalter.",
        "audience": "user",
        "versions": ["9.98.0", "9.99.0"],
    },
    {
        "version": "9.96.0",
        "date": "2026-06-08",
        "title": "Datenschutz-Prüfer mit Markierungen",
        "body": "Ein neuer Dokument-Prüfer markiert DSGVO- und Vertraulichkeits-Funde farbig direkt im Text, "
                "mit Vor-/Zurück-Navigator durch alle Funde, Erklärung je Fund, Einzelfall-Übersteuerung "
                "und umkehrbarer Anonymisierung. Erreichbar in der Daten-Ansicht, im Projekt-Quellbaum "
                "(Rechtsklick) und an Anhängen; ein anonymisiertes Export-Exemplar lässt sich erzeugen — "
                "die Originaldatei bleibt stets unverändert.",
        "audience": "user",
        "versions": ["9.96.0"],
    },
    {
        "version": "9.94.0",
        "date": "2026-06-08",
        "title": "Datenschutz-Rückfrage nach dem Senden",
        "body": "Optional fragt die App nach einer Anfrage, bei der ein Datenschutz-Schritt ausgelöst wurde "
                "(Anonymisieren oder Wechsel auf ein lokales Modell), ob das Ergebnis gepasst hat — und "
                "führt dieselbe Anfrage auf Wunsch mit einer anderen Methode erneut aus. Die Wahl wird für "
                "Folgeanfragen gemerkt; aktivierbar im Datenschutz-Dialog vor dem Senden.",
        "audience": "user",
        "versions": ["9.94.0"],
    },
    {
        "version": "9.91.0",
        "date": "2026-06-07",
        "title": "Datenschutz-Feinschliff & Service-Modelle",
        "body": "Die rund 70 Erkennungsregeln für personenbezogene Daten wurden überarbeitet, um Fehlalarme "
                "deutlich zu reduzieren, und Admins haben bei Funden in Hintergrund-Aufgaben eine neue "
                "Option „Überspringen“. Der Projekt-Quellbaum zeigt pro Datei ein farbiges "
                "Wissensgraph-Badge, und das neue Panel „Service-Modelle“ bündelt alle Dienst-Modelle an "
                "einem Ort — mit klarer Fehlermeldung statt erfundener Standardwerte.",
        "audience": "admin",
        "versions": ["9.91.0", "9.92.0", "9.93.0"],
    },
    {
        "version": "9.89.0",
        "date": "2026-06-06",
        "title": "Detaillierte Kostenaufschlüsselung",
        "body": "Die Plan-Nutzungs-Anzeige in der Statusleiste zeigt jetzt eine Kostenaufschlüsselung nach "
                "Anwendungsfall und Modell — mit wählbarem Zeitfenster, anteiligen Balken und aufklappbarem "
                "Modell-Detail. Jeder Modellaufruf wird lückenlos erfasst (auch kostenlose und lokale), "
                "sodass die Übersicht ein vollständiges Bild der Ausgaben liefert.",
        "audience": "user",
        "versions": ["9.89.0", "9.90.0"],
    },
    {
        "version": "9.83.0",
        "date": "2026-06-06",
        "title": "Audio Overview: Podcast & Vorlesen",
        "body": "Lassen Sie sich Ihre Projektquellen oder einen Chatverlauf als natürlich klingenden "
                "Zwei-Stimmen-Podcast vorlesen — zwei Hosts diskutieren den Inhalt in einer echten "
                "Unterhaltung, als fertige Audiodatei mit Player. Ein Vorlese-Knopf liest jede Antwort laut "
                "vor. Die Ausgabe ist mehrsprachig (passende Stimme automatisch erkannt), und Admins können "
                "eigene Stimmen klonen.",
        "audience": "user",
        "versions": ["9.83.0", "9.84.0", "9.85.0", "9.85.1", "9.87.0", "9.88.0"],
    },
    {
        "version": "9.82.0",
        "date": "2026-06-06",
        "title": "Prompt-Verfeinerung in zwei Stufen",
        "body": "Neben dem bewährten Ein-Klick-Polish gibt es jetzt eine optionale Engineer-Stufe, die Ihren "
                "Prompt strukturiert umbaut und am Kontext der App (Modell, Werkzeuge, Projektanweisungen) "
                "ausrichtet — ohne Details zu erfinden. Bei zu vagen Eingaben stellt sie eine gezielte "
                "Rückfrage statt zu raten. Wählbar über den Polish/Engineer-Umschalter.",
        "audience": "user",
        "versions": ["9.82.0"],
    },

    # ── 9.6x–9.8x — Research, Studio, Projektansicht, Citations ──
    {
        "version": "9.78.0",
        "date": "2026-06-05",
        "title": "Aufgeräumte Projektansicht mit Quellen-Baum",
        "body": "Alle Projektquellen (Anweisungen, Dateien, Ordner, Web-Adressen) erscheinen jetzt als ein "
                "gemeinsamer, ein- und ausklappbarer Baum. Quellen lassen sich per Drag-and-drop und "
                "Mehrfachauswahl in eigene virtuelle Ordner gruppieren (bis zu drei Ebenen tief), und ein "
                "Farbpunkt zeigt je Element den Speicher-Status. Die Projekt-Tabs sind jetzt "
                "platzsparende Symbol-Tabs.",
        "audience": "user",
        "versions": ["9.75.1", "9.75.2", "9.78.0", "9.79.0", "9.80.0"],
    },
    {
        "version": "9.67.0",
        "date": "2026-06-03",
        "title": "Anklickbare Quellenangaben",
        "body": "Sobald eine Antwort auf Dokumenten, Web-Quellen oder Gedächtnis beruht, belegt der Assistent "
                "seine Aussagen automatisch in jedem Chat — ohne Schalter. Belege erscheinen als "
                "nummerierte Ziffern [1][2] plus Quellen-Legende; ein Klick öffnet die Quelle und hebt die "
                "zitierte Stelle hervor. Nicht wörtlich auffindbare Zitate werden mit Warnhinweis "
                "gekennzeichnet.",
        "audience": "user",
        "versions": ["9.67.0", "9.68.0"],
    },
    {
        "version": "9.65.0",
        "date": "2026-06-03",
        "title": "Deep & Fast Research mit Studio",
        "body": "Ein neuer Research-Tab bietet zwei Modi: Fast liefert eine schnelle Trefferliste zum "
                "Übernehmen, Deep erzeugt einen belegten Bericht plus geprüfte Quellenliste — mit "
                "Live-Fortschritt, sofortigem Abbruch und Kosten-Ausweis. Begleitend das neue Studio: über "
                "Vorlagen-Karten (Study Guide, Briefing, FAQ, Timeline) generieren Sie aus Ihren "
                "Projektquellen belegte Deliverables zum Öffnen, Umbenennen, Neu-Generieren, Herunterladen "
                "oder Löschen.",
        "audience": "user",
        "versions": ["9.63.0", "9.64.0", "9.65.0", "9.66.0", "9.73.0", "9.73.1", "9.74.0",
                     "9.75.0", "9.77.0", "9.81.0", "9.81.1"],
    },
    {
        "version": "9.54.0",
        "date": "2026-05-30",
        "title": "Bessere Web-Abrufe für Fachartikel",
        "body": "Der Web-Abruf erkennt wissenschaftliche Seiten (arXiv, bioRxiv, PubMed u. a.) und holt direkt "
                "das PDF statt der Hülle. Ein neuer Kurzfassungs-Modus holt nur einen knappen Überblick — "
                "praktisch zum schnellen Relevanz-Prüfen, aktivierbar in der Websuche.",
        "audience": "user",
        "versions": ["9.54.0"],
    },
    {
        "version": "9.53.0",
        "date": "2026-05-30",
        "title": "Intelligentere automatische Modellwahl",
        "body": "Die automatische Modellwahl im ✨-Modus versteht Anfragen jetzt deutlich besser und erkennt "
                "Aufgabenart, Komplexität und benötigte Werkzeuge zuverlässiger. Admins können Modelle per "
                "Benchmark nach Fähigkeit und Geschwindigkeit bewerten lassen, sodass die Wahl auf "
                "gemessenen Werten beruht, und zwischen Schlüsselwort-, KI- und Hybrid-Erkennung wählen. "
                "Ein Kompass-Symbol zeigt nach jeder Anfrage transparent, wie entschieden wurde.",
        "audience": "admin",
        "versions": ["9.53.0", "9.55.0", "9.56.0", "9.57.0", "9.58.0", "9.59.0"],
    },
    {
        "version": "9.52.0",
        "date": "2026-05-30",
        "title": "Feinschliff in der Chat-Ansicht",
        "body": "Mehrere kleine Verbesserungen im Gesprächsverlauf: auf- und zuklappbare Bereiche öffnen und "
                "schließen jetzt sanft animiert, ein langer Druck auf eine Anfrage klappt alle gemeinsam "
                "auf oder zu, jede Antwort zeigt in ihrer Statuszeile den Startzeitpunkt, und technische "
                "Hinweise sind aus dem Verlauf in den Sitzungs-Inspektor gewandert — so bleibt der Chat "
                "übersichtlich.",
        "audience": "user",
        "versions": ["9.9.14", "9.43.1", "9.49.2", "9.52.0"],
    },

    # ── 9.4x–9.5x — Hintergrundaufgaben, Live-Anzeige, Feedback ──
    {
        "version": "9.45.0",
        "date": "2026-05-28",
        "title": "Hintergrundaufgaben & Aktivitäts-Panel",
        "body": "Lange, aufwändige Recherchen lassen sich jetzt als Hintergrundaufgabe abkoppeln: Sie geben "
                "den Auftrag, der Chat bleibt sofort frei, Sie arbeiten weiter — und sobald die Aufgabe "
                "fertig ist, fließt ihr Ergebnis automatisch in Ihren nächsten Beitrag ein. Eine Pille oben "
                "zeigt laufende Aufgaben, ein Panel listet Status und Tool-Aufrufe live (auch parallele "
                "Aufgaben als Gruppe), erlaubt Stoppen, das Abbrechen einzelner Schritte und den Blick ins "
                "vollständige Transkript.",
        "audience": "user",
        "versions": ["9.45.0", "9.45.3", "9.46.0", "9.46.6", "9.47.0", "9.47.1", "9.48.0",
                     "9.49.0", "9.50.0", "9.50.1", "9.50.2", "9.51.5", "9.51.6", "9.51.7",
                     "9.51.8", "9.51.9"],
    },
    {
        "version": "9.44.0",
        "date": "2026-05-28",
        "title": "MemPalace-Dashboard für Admins",
        "body": "Admins erhalten unter Einstellungen → MemPalace ein visuelles Dashboard, um das Gedächtnis "
                "der App direkt zu durchstöbern und zu kuratieren — Einträge, Bereiche, Verknüpfungen und "
                "Wissensgraph-Fakten lassen sich ansehen, durchsuchen, hinzufügen und löschen.",
        "audience": "admin",
        "versions": ["9.44.0"],
    },
    {
        "version": "9.43.0",
        "date": "2026-05-27",
        "title": "Feedback per Daumen mit Antwort-Dialog",
        "body": "Sie können jede Antwort und jedes Ergebnis mit 👍 oder 👎 bewerten und optional kommentieren "
                "— quer durch Chat, Projekt-Chat, Workflows, Brainy, geplante Aufgaben, Übersetzungen und "
                "Klassifizierungen. Aus der Bewertung wird ein kleiner Dialog: Nutzer und Admin tauschen "
                "kurze Nachrichten aus, ein pulsierender Punkt zeigt ungelesene Antworten. Übersetzungen "
                "lassen sich einzeln als Favorit markieren.",
        "audience": "user",
        "versions": ["9.41.0", "9.41.1", "9.41.2", "9.43.0"],
    },
    {
        "version": "9.39.0",
        "date": "2026-05-27",
        "title": "Präzisere & sparsamere Antworten aus Dokumenten",
        "body": "Zieht die App ein Dokument aus dem Gedächtnis heran, liest sie jetzt gezielt nur die wirklich "
                "relevanten Stellen (kleine Dokumente weiterhin komplett) und sortiert die Treffer nach "
                "echter Relevanz neu. Das macht Antworten genauer und beugt Fehlern aus halben "
                "Textausschnitten vor.",
        "audience": "user",
        "versions": ["9.38.0", "9.39.0", "9.40.0", "9.34.0"],
    },
    {
        "version": "9.31.0",
        "date": "2026-05-26",
        "title": "Projekt-Setting: Websuche unterbinden",
        "body": "Projekte haben ein neues Setting „Websuche unterbinden“: Ist es aktiv, sind die Web-Werkzeuge "
                "für Chats und geplante Aufgaben dieses Projekts hart gesperrt — die App antwortet dann "
                "ausschließlich aus dem Projektgedächtnis und den hinterlegten Dokumenten.",
        "audience": "admin",
        "versions": ["9.31.0"],
    },
    {
        "version": "9.29.0",
        "date": "2026-05-26",
        "title": "Geplante Aufgaben im Projektkontext",
        "body": "Geplante Aufgaben lassen sich jetzt an ein Projekt binden und laufen dann mit dessen "
                "Anweisungen, Projektgedächtnis und Recherche-Modus — wie ein Projekt-Chat. Im Projekt gibt "
                "es einen Tab „Geplante Aufgaben“ mit Anlegen, Pausieren, Sofort-Ausführen, Verlauf und "
                "Kosten pro Lauf.",
        "audience": "user",
        "versions": ["9.29.0", "9.32.0"],
    },

    # ── 9.1x–9.2x — Brainy, deutsche UI, Websuche, Auto-Modell ──
    {
        "version": "9.21.0",
        "date": "2026-05-25",
        "title": "Brainy — der Helpdesk-Bot 🧠",
        "body": "Brainy ist ein freundlicher Helpdesk-Bot, erreichbar über einen schwebenden Button in jeder "
                "Ansicht. Er kennt die App, Ihre aktuelle Sitzung und Ihren Kontext (z. B. in welchem "
                "Projekt Sie sind) und gibt konkrete Tipps — auch während die Hauptantwort noch entsteht. "
                "Er führt eine durchgehende Unterhaltung mit Verlauf und beantwortet selbst Detailfragen "
                "quellenbasiert, statt zu raten. Admins konfigurieren Modell und Persönlichkeit unter "
                "Einstellungen → Tools → Brainy.",
        "audience": "user",
        "versions": ["9.21.0", "9.21.1", "9.21.2", "9.23.0", "9.24.0", "9.26.0", "9.27.0",
                     "9.28.0", "9.40.0"],
    },
    {
        "version": "9.20.0",
        "date": "2026-05-25",
        "title": "Komplett deutschsprachige Oberfläche",
        "body": "Die gesamte Bedienoberfläche ist jetzt auf Deutsch (förmliches „Sie“) — Buttons, "
                "Überschriften, Tabs, Platzhalter, Tooltips, Meldungen und Dialoge. Etablierte Fachbegriffe "
                "(Agent, Workflow, Token, Provider, MCP, Knowledge Graph u. a.) bleiben bewusst englisch. "
                "Die Projekteinstellungen haben zusätzlich einen einklappbaren Hilfe-Bereich mit "
                "verständlichen Erklärungen.",
        "audience": "user",
        "versions": ["9.19.1", "9.20.0"],
    },
    {
        "version": "9.13.0",
        "date": "2026-05-24",
        "title": "Websuche: kuratierte Recherche & Projekt-Webadressen",
        "body": "Die App bringt eine eigene, selbst gehostete Websuche mit (kein externer Dienst nötig). Über "
                "den Websuche-Tab suchen Sie selbst, markieren die nützlichen Treffer in einem Korb (per "
                "Häkchen, URL-Eingabe oder Drag&Drop) — und die Antwort arbeitet dann strikt nur aus diesen "
                "ausgewählten Quellen, statt frei im Netz zu suchen (per Schaltfläche bei Bedarf "
                "aufhebbar). JavaScript-lastige Seiten werden korrekt gerendert, und Projekte können feste "
                "Web-Adressen hinterlegen, die laufend ins Projektwissen aufgenommen werden.",
        "audience": "user",
        "versions": ["9.13.0", "9.14.0", "9.16.0", "9.17.0", "9.19.0"],
    },
    {
        "version": "9.11.0",
        "date": "2026-05-22",
        "title": "Mitlaufende Chat-Zusammenfassung",
        "body": "Die kurze Zusammenfassung über jedem Chat wird jetzt nach jedem Beitrag aktualisiert und "
                "deckt alle besprochenen Themen ab, nicht nur das erste — so bleibt der Überblick über den "
                "gesamten Gesprächsverlauf aktuell.",
        "audience": "user",
        "versions": ["9.11.0"],
    },
    {
        "version": "9.9.0",
        "date": "2026-05-20",
        "title": "Automatische Modellwahl pro Anfrage",
        "body": "Im Modell-Menü gibt es „✨ Auto“: Die App wählt für jede einzelne Anfrage automatisch ein "
                "passendes Modell — schnelle Aufgaben gehen an ein günstiges/lokales Modell, Code- und "
                "Analyse-Fragen an ein stärkeres Modell, Anhänge werden berücksichtigt. Ein Tooltip zeigt "
                "das gewählte Modell und den Grund.",
        "audience": "user",
        "versions": ["9.9.0", "9.9.1"],
    },

    # ── 9.0x — Datenschutz-Fundament, Anhänge ──
    {
        "version": "9.6.0",
        "date": "2026-05-18",
        "title": "Datenschutz: DSGVO/PII & Dokumentenklassifizierung",
        "body": "Die Datenschutzprüfung erkennt jetzt zusätzlich Personennamen, Adressen und Organisationen "
                "und markiert wiederhergestellte Werte in Anfrage und Antwort. Neu hinzu kommt eine "
                "Dokumenten-Klassifizierung: Anhänge werden auf ihre Sensibilität (öffentlich/intern/"
                "vertraulich/streng vertraulich) geprüft — erkannt aus Markierungen UND aus dem Inhalt. Je "
                "nach Stufe wird gewarnt, automatisch auf ein lokales Modell umgeschaltet oder das Senden "
                "blockiert. Ein Daten-Bereich erlaubt das Scannen von Dateien, Ordnern und Projekten mit "
                "CSV-Export.",
        "audience": "user",
        "versions": ["9.3.0", "9.4.0", "9.5.0", "9.6.0", "9.7.0", "9.8.0"],
    },
    {
        "version": "9.2.0",
        "date": "2026-05-18",
        "title": "Anhänge im Seitenpanel verwalten",
        "body": "Chat-Anhänge lassen sich jetzt im rechten Panel ansehen und verwalten — mit derselben "
                "Vorschau wie bei Artefakten: Bilder, PDFs und Text-/Code-/Markdown-Dateien werden direkt "
                "angezeigt, plus Kopieren, Herunterladen und Rohansicht je Datei.",
        "audience": "user",
        "versions": ["9.1.0", "9.2.0"],
    },
    {
        "version": "9.0.0",
        "date": "2026-05-17",
        "title": "Bildanhänge & Datenschutz-Schalter",
        "body": "Angehängte Bilder kann der Assistent jetzt nicht nur sehen, sondern auch als Datei "
                "bearbeiten (z. B. Größe ändern, Hintergrund entfernen), und Ergebnisse von Befehlen "
                "tauchen zuverlässig im Artefakte-Panel auf. Ein neuer Schalter im Eingabebereich steuert, "
                "ob Datenschutz-Details (gelbe Markierungen, aufklappbarer Hinweis) sichtbar sind — "
                "standardmäßig aus, Sie sehen nur die Statistik und blenden Details bei Bedarf ein.",
        "audience": "user",
        "versions": ["9.0.0", "9.0.2"],
    },
]
