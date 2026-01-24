# PDF-Upload Feature - Implementierungszusammenfassung

## Status: Abgeschlossen

**Commit:** `bae2e47` - Add PDF upload feature for multimodal chat  
**Änderungen:** 7 Dateien, +428/-62 Zeilen

## Übersicht

Das PDF-Upload Feature wurde vollständig implementiert. Benutzer können nun PDF-Dateien an Nachrichten anhängen, damit Claude den Inhalt analysieren kann.

## Implementierte Komponenten

### 1. Datenmodell (Models/)

**APIModels.swift** - Neue Typen für multimodale API-Requests:
- `MessageContent` - Enum für Text oder multimodale Inhalte
- `ContentItem` - Enum für Text- oder Dokument-Elemente
- `DocumentContent` / `DocumentSource` - Structs für Base64-kodierte PDFs

**Message.swift** - Erweitert um PDF-Unterstützung:
- `PDFAttachment` Struct mit: `id`, `filename`, `data`, `pageCount`, `formattedFileSize`
- `attachments: [PDFAttachment]` Feld in `Message`
- `toAPIMessage()` Methode für API-Konvertierung

### 2. Service-Schicht (Services/)

**ClaudeAPIService.swift** - Aktualisiert für multimodale Nachrichten:
- Neue Fehlerfälle: `pdfTooLarge(Int)`, `invalidPDF`
- `maxPDFSize` Konstante (32 MB)
- `sendMessageStream(messages:)` ersetzt `sendMessageStream(content:)`
- Automatische PDF-Größenvalidierung

### 3. ViewModel (ViewModels/)

**ChatViewModel.swift** - PDF-Zustandsverwaltung:
- `@Published var pendingAttachments: [PDFAttachment]`
- `@Published var showFileImporter: Bool`
- `addPDFAttachment(from: URL)` - Lesen, Validieren, Hinzufügen
- `removePendingAttachment(_:)` - Entfernen aus Liste
- Aktualisierte `sendMessage()` mit Attachment-Unterstützung

### 4. UI-Komponenten (Views/)

**InputBar.swift** - Erweiterte Eingabeleiste:
- Büroklammer-Button für PDF-Auswahl
- Horizontale Scroll-Leiste für pending Attachments
- `AttachmentChip` Komponente (Dateiname, Größe, Seitenanzahl, X-Button)
- `canSend` erlaubt PDF-only Nachrichten

**MessageBubble.swift** - Attachment-Anzeige:
- `PDFAttachmentBadge` Komponente
- Badges werden über dem Nachrichtentext angezeigt

**ContentView.swift** - FileImporter-Integration:
- `.fileImporter()` Modifier für PDF-Auswahl
- Unterstützt Mehrfachauswahl
- Neue Bindings an InputBar

## Validierung

| Prüfung | Implementierung |
|---------|-----------------|
| PDF Magic Bytes | `%PDF` Header-Check |
| Dateigröße | Max. 32 MB (Anthropic Limit) |
| Security-Scoped Access | `startAccessingSecurityScopedResource()` |
| Seitenanzahl | PDFKit `PDFDocument.pageCount` |

## API-Format

Nachrichten mit PDF werden als multimodales Content-Array gesendet:

```json
{
  "role": "user",
  "content": [
    {
      "type": "document",
      "source": {
        "type": "base64",
        "media_type": "application/pdf",
        "data": "<base64-encoded-pdf>"
      }
    },
    {
      "type": "text",
      "text": "Analysiere dieses Dokument"
    }
  ]
}
```

## Fehlermeldungen

| Fehler | Meldung |
|--------|---------|
| Zu groß | "PDF too large (XMB). Maximum size is 32MB." |
| Ungültig | "Invalid PDF file" |
| Kein Zugriff | "Cannot access file" |
| Lesefehler | "Failed to read PDF: ..." |

## Verwendung

1. **PDF anhängen:** Klick auf Büroklammer-Icon (📎)
2. **PDF auswählen:** Dateiauswahl-Dialog öffnet sich
3. **Vorschau:** Attachment erscheint in Leiste unter Eingabefeld
4. **Entfernen:** X-Button auf Attachment-Chip
5. **Senden:** ⌘+Return oder Senden-Button

## Text-Extraktions-Modus (Fallback)

Für APIs die keine nativen PDF-Uploads unterstützen (z.B. MiniMax), kann die Text-Extraktion aktiviert werden.

### Einstellung

In `AppSettings.swift`:
```swift
@AppStorage("extractPDFText") var extractPDFText: Bool = false
```

Toggle in Einstellungen unter "PDF-Verarbeitung".

### Funktionsweise

Wenn aktiviert, wird `PDFAttachment.extractText()` verwendet:

```swift
func extractText() -> String {
    guard let pdfDocument = PDFDocument(data: data) else {
        return "[PDF konnte nicht gelesen werden]"
    }
    // Extrahiert Text Seite für Seite
    for pageIndex in 0..<pdfDocument.pageCount {
        if let page = pdfDocument.page(at: pageIndex) {
            extractedText += page.string ?? ""
        }
    }
    return extractedText
}
```

### Ausgabeformat (Text-Modus)

```
=== Dokument: beispiel.pdf (5 Seiten) ===
--- Seite 1 ---
[Extrahierter Text von Seite 1]

--- Seite 2 ---
[Extrahierter Text von Seite 2]
...

=== Anfrage ===
[Benutzeranfrage]
```

### Einschränkungen Text-Modus

- Formatierung geht verloren (Tabellen, Spalten, etc.)
- Bilder im PDF werden ignoriert
- Gescannte PDFs ohne OCR liefern keinen Text

## Hinweise

- PDFs werden als `Data` gespeichert (nicht als URL), da Security-Scoped URLs ablaufen
- Nachrichten können nur PDF ohne Text enthalten (Standard-Prompt wird hinzugefügt)
- Mehrere PDFs pro Nachricht werden unterstützt
- Text-Extraktion ist Fallback für nicht-Anthropic APIs
