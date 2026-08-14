// spotlight-qa — Core-Spotlight-RAG: Stufe 1 findet Dokumente über das native
// SpotlightSearchTool (Datei-Index-Ranking), Stufe 2 liest die Companion-
// Markdowns der Treffer und beantwortet die Frage aus dem ECHTEN Inhalt in
// einer zweiten AFM-Session (das Tool selbst liefert dem Modell nur Metadaten
// — textContent fließt aus dem Datei-Index nicht mit, gemessen 566 Zeichen).
// Usage: spotlight-qa "<Frage>" [corpus-dir]   → JSON auf stdout
import Foundation
import CoreSpotlight
import FoundationModels

@main
struct SpotlightQA {
    static func main() async {
        let args = CommandLine.arguments
        guard args.count >= 2 else {
            FileHandle.standardError.write("usage: spotlight-qa \"<frage>\" [corpus-dir]\n".data(using: .utf8)!)
            exit(2)
        }
        let question = args[1]
        let corpus = args.count >= 3 ? args[2]
            : NSString(string: "~/spotlight-eval/corpus").expandingTildeInPath

        // ── Stufe 1: Dokument-Discovery via SpotlightSearchTool ─────────────
        var fileSource = FileSource(fetchAttributes: [.title, .path, .contentURL, .displayName])
        fileSource.scopes = [URL(fileURLWithPath: corpus, isDirectory: true)]
        // NB Beta-Befund: scopes wird vom Tool NICHT respektiert (Treffer aus
        // fremden Home-Verzeichnissen beobachtet) — deshalb unten der harte
        // Korpus-Filter über die Companion-Map; 8 Slots gegen Junk-Verdrängung.
        fileSource.maximumResultCount = 8

        var config = SpotlightSearchTool.Configuration(sources: [.files(fileSource)])
        config.guide = SpotlightSearchTool.Guide(
            level: .focused(.documents), format: .compact)
        let tool = SpotlightSearchTool(configuration: config)

        let model = SystemLanguageModel(guardrails: .permissiveContentTransformations)
        let searchSession = LanguageModelSession(model: model, tools: [tool], instructions: """
            Finde die für die Frage relevanten Dokumente. Du MUSST das \
            Spotlight-Suchwerkzeug aufrufen. Suche mit 1-2 Kernbegriffen (alle \
            Begriffe müssen im Dokument vorkommen — weniger ist mehr); bei 0 \
            Treffern suche SOFORT erneut mit nur EINEM Begriff. Antworte danach \
            nur mit den gefundenen Dateinamen.
            """)

        // Treffer-Pfade aus dem Reply-Stream einsammeln
        final class ReplyBox: @unchecked Sendable {
            var paths: [String] = []
            var debug: [String] = []
        }
        let box = ReplyBox()
        let collector = Task {
            for await reply in tool.searchResults {
                var items: [CoreSpotlight.SearchableItem] = []
                switch reply.content {
                case .items(let arr): items = arr
                case .scoredItems(let arr): items = arr.map { $0.item }
                default:
                    box.debug.append("reply: " + String(describing: reply.content).prefix(120).description)
                }
                for si in items {
                    let a = si.item.attributeSet
                    // path/contentURL sind im Datei-Index-Reply nil (gemessen)
                    // — displayName trägt den Dateinamen, das reicht fürs
                    // Companion-Mapping.
                    if let d = a.displayName { box.paths.append(d) }
                }
            }
        }

        let t0 = Date()
        var searchNote = ""
        do {
            let r = try await searchSession.respond(to: question)
            searchNote = r.content
        } catch { searchNote = "search-error: \(error)" }
        try? await Task.sleep(nanoseconds: 300_000_000)
        collector.cancel()

        var toolCalls: [[String: String]] = []
        for entry in searchSession.transcript {
            if case .toolCalls(let calls) = entry {
                for c in calls {
                    toolCalls.append(["tool": c.toolName,
                                      "args": String(describing: c.arguments).prefix(200).description])
                }
            }
        }

        // ── Companion-Auflösung: PDF-Treffer → .brain-extracted/<name>.pdf.md
        var mdByBase: [String: String] = [:]  // "<datei>.pdf" → companion-Pfad
        if let en = FileManager.default.enumerator(atPath: corpus) {
            for case let rel as String in en where rel.hasSuffix(".md") {
                let base = (rel as NSString).lastPathComponent
                    .replacingOccurrences(of: ".md", with: "")
                mdByBase[base] = corpus + "/" + rel
            }
        }
        // Deterministische Zweitquelle: mdfind -onlyin corpus pro Frage-
        // Begriff (derselbe Spotlight-Index, aber zuverlässig gescoped — das
        // native Tool ignoriert scopes und sucht launisch). Ranking: Dokumente
        // nach Anzahl getroffener Begriffe.
        let stopEarly: Set<String> = ["welche", "werden", "müssen", "wie", "wird",
                                      "sind", "eine", "einen", "gilt", "oft", "wer",
                                      "was", "ist", "von", "bei", "für", "und",
                                      "oder", "nicht", "auch", "wurde", "durch"]
        let qTerms = question.lowercased()
            .components(separatedBy: CharacterSet.alphanumerics.inverted)
            .filter { $0.count > 3 && !stopEarly.contains($0) }
        var hitCount: [String: Int] = [:]  // pfad → #begriffe
        for term in qTerms.prefix(6) {
            let proc = Process()
            proc.executableURL = URL(fileURLWithPath: "/usr/bin/mdfind")
            proc.arguments = ["-onlyin", corpus, term]
            let pipe = Pipe()
            proc.standardOutput = pipe
            proc.standardError = Pipe()
            try? proc.run()
            let data = pipe.fileHandleForReading.readDataToEndOfFile()
            proc.waitUntilExit()
            for line in (String(data: data, encoding: .utf8) ?? "").split(separator: "\n") {
                let p = String(line)
                guard !p.contains("/.brain-extracted/") else { continue }  // Companions nicht doppelt
                hitCount[p, default: 0] += 1
            }
        }
        let mdfindRanked = hitCount.sorted { ($0.value, $0.key) > ($1.value, $1.key) }
            .map { ($0.key as NSString).lastPathComponent }

        // Kandidaten-Union: Tool-Treffer zuerst (modell-gewählte Query),
        // dann mdfind-Ranking. HART auf den Korpus gefiltert (mdByBase
        // enthält nur Korpus-Companions; Home-Index-Fremdtreffer fallen raus).
        var docTexts: [(name: String, text: String)] = []
        var seen = Set<String>()
        for p in box.paths + mdfindRanked {
            let base = (p as NSString).lastPathComponent
            let name = base.hasSuffix(".md")
                ? base.replacingOccurrences(of: ".md", with: "") : base
            guard let mp = mdByBase[name], !seen.contains(name) else { continue }
            seen.insert(name)
            if let t = try? String(contentsOfFile: mp, encoding: .utf8) {
                docTexts.append((name, t))
            }
            if docTexts.count >= 3 { break }
        }

        // ── Kontext-Fenster: Absätze nach Frage-Begriffen ranken, je Doc kappen
        let stop: Set<String> = ["welche", "werden", "müssen", "wie", "wird", "sind",
                                 "eine", "einen", "der", "die", "das", "und", "für",
                                 "gilt", "oft", "wer", "was", "ist", "in", "von", "bei"]
        let terms = question.lowercased()
            .components(separatedBy: CharacterSet.alphanumerics.inverted)
            .filter { $0.count > 3 && !stop.contains($0) }
        func windows(_ text: String, budget: Int) -> String {
            let paras = text.components(separatedBy: "\n\n")
            let scored = paras.enumerated().map { (i, p) -> (Int, Int, String) in
                let lp = p.lowercased()
                let s = terms.reduce(0) { $0 + (lp.contains($1) ? 1 : 0) }
                return (s, i, p)
            }.sorted { ($0.0, -$0.1) > ($1.0, -$1.1) }
            var out: [(Int, String)] = []
            var used = 0
            for (s, i, p) in scored where s > 0 || used == 0 {
                if used + p.count > budget { continue }
                out.append((i, p)); used += p.count
                if used > budget - 200 { break }
            }
            return out.sorted { $0.0 < $1.0 }.map { $0.1 }.joined(separator: "\n\n")
        }
        let totalBudget = Int(ProcessInfo.processInfo.environment["CTX_BUDGET"] ?? "") ?? 7000
        let perDoc = docTexts.isEmpty ? 0 : totalBudget / docTexts.count
        var context = ""
        for (name, text) in docTexts {
            context += "=== \(name) ===\n" + windows(text, budget: perDoc) + "\n\n"
        }

        // --retrieve-only: nur Discovery+Kontext liefern (Antwortmodell
        // extern austauschbar — 12B-Arm des Evals).
        if ProcessInfo.processInfo.environment["RETRIEVE_ONLY"] == "1" {
            let ro: [String: Any] = [
                "docs": docTexts.map { $0.name },
                "context": context,
                "tool_calls": toolCalls,
                "duration_s": Double(Int(Date().timeIntervalSince(t0) * 10)) / 10,
            ]
            if let data = try? JSONSerialization.data(withJSONObject: ro),
               let s = String(data: data, encoding: .utf8) { print(s) }
            return
        }

        // ── Stufe 2: Antwort aus dem echten Inhalt ──────────────────────────
        var answer = ""
        var errorText: String? = nil
        if context.isEmpty {
            answer = "Die Dokumente enthalten keine Antwort auf diese Frage (keine relevanten Treffer im Regelwerk)."
        } else {
            let answerSession = LanguageModelSession(model: model, instructions: """
                Beantworte die Frage präzise auf Deutsch, NUR auf Basis der \
                Dokumentauszüge. Belege JEDE Aussage in der Form \
                [Quelle: <Dateiname> — "wörtliches Zitat aus dem Auszug"]. \
                Erfinde keine Werte: was nicht im Auszug steht, ist "nicht \
                spezifiziert". Steht die Antwort gar nicht in den Auszügen, \
                sage das ausdrücklich.
                """)
            do {
                let r = try await answerSession.respond(to: "Frage: \(question)\n\nDokumentauszüge:\n\(context)")
                answer = r.content
            } catch { errorText = "\(error)" }
        }
        let dur = Date().timeIntervalSince(t0)

        var out: [String: Any] = [
            "answer": answer,
            "search_note": searchNote,
            "tool_calls": toolCalls,
            "docs": docTexts.map { $0.name },
            "context_chars": context.count,
            "debug": box.debug,
            "duration_s": Double(Int(dur * 10)) / 10,
        ]
        if let e = errorText { out["error"] = e }
        if let data = try? JSONSerialization.data(withJSONObject: out, options: [.prettyPrinted]),
           let s = String(data: data, encoding: .utf8) {
            print(s)
        } else {
            print("{\"error\": \"json serialization failed\"}")
        }
    }
}
