// apple_ocr_cli.swift — Dokument-OCR über Apples Vision-Framework
// (RecognizeDocumentsRequest, macOS 26+): Bild rein, Markdown raus.
// Absätze als Fließtext, Tabellen als Markdown-Tabellen, Listen als Listen.
// Wird vom apple_ocr_server.py pro Request aufgerufen (Prozess-Spawn ist bei
// OCR-Laufzeiten unkritisch). Läuft auf ANE/CPU — keine GPU-Last.
//
// Build (M4, Prebuilt-Modul-Cache — siehe speech_stream_server.swift):
//   swiftc -O apple_ocr_cli.swift -o apple_ocr_cli
// Nutzung: apple_ocr_cli <bilddatei> [--lang de-DE,en-US]

import Foundation
import Vision

guard CommandLine.arguments.count >= 2 else {
    FileHandle.standardError.write("usage: apple_ocr_cli <image> [--lang de-DE,en-US]\n".data(using: .utf8)!)
    exit(2)
}
let imagePath = CommandLine.arguments[1]

func cellText(_ container: DocumentObservation.Container) -> String {
    container.text.transcript
        .replacingOccurrences(of: "\n", with: " ")
        .replacingOccurrences(of: "|", with: "\\|")
        .trimmingCharacters(in: .whitespaces)
}

func markdown(from document: DocumentObservation.Container) -> String {
    var out: [String] = []
    for paragraph in document.paragraphs {
        let t = paragraph.transcript.trimmingCharacters(in: .whitespacesAndNewlines)
        if !t.isEmpty { out.append(t) }
    }
    for table in document.tables {
        var lines: [String] = []
        for (i, row) in table.rows.enumerated() {
            let cells = row.map { cellText($0.content) }
            lines.append("| " + cells.joined(separator: " | ") + " |")
            if i == 0 {
                lines.append("|" + Array(repeating: " --- |", count: cells.count).joined())
            }
        }
        if !lines.isEmpty { out.append(lines.joined(separator: "\n")) }
    }
    for list in document.lists {
        let items = list.items.map { "- " + $0.itemString.trimmingCharacters(in: .whitespacesAndNewlines) }
        if !items.isEmpty { out.append(items.joined(separator: "\n")) }
    }
    if out.isEmpty {
        // Fallback: reiner Transkript-Text, falls keine Struktur erkannt wurde.
        let t = document.text.transcript.trimmingCharacters(in: .whitespacesAndNewlines)
        if !t.isEmpty { out.append(t) }
    }
    return out.joined(separator: "\n\n")
}

let semaphore = DispatchSemaphore(value: 0)
Task {
    do {
        // Sprache: automatische Erkennung (recognitionLanguages existiert nur
        // auf RecognizeTextRequest, nicht auf der Document-Variante).
        let request = RecognizeDocumentsRequest()
        let url = URL(fileURLWithPath: imagePath)
        let observations = try await request.perform(on: url)
        var parts: [String] = []
        for observation in observations {
            parts.append(markdown(from: observation.document))
        }
        print(parts.joined(separator: "\n\n---\n\n"))
        exit(0)
    } catch {
        FileHandle.standardError.write("ERROR: \(error)\n".data(using: .utf8)!)
        exit(1)
    }
}
semaphore.wait()
