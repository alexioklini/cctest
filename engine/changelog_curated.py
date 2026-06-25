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
    # ── 9.2xx — Datenschutz-Prüfung serverseitig ──
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
