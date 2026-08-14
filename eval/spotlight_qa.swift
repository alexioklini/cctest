// spotlight-qa — Core-Spotlight-RAG, alles Apple/ml-explore-Standard:
// Stufe 1 Dokument-Discovery (natives SpotlightSearchTool + mdfind-Zweitquelle),
// Stufe 2 Antwort aus Companion-Markdown-Auszügen — beides in EINER
// LanguageModelSession-Familie mit austauschbarem Backend (MODEL-Env):
//   afm | coreai:<bundle-pfad> | mlx:<hf-repo-id>
// Modi:  spotlight-qa "<Frage>" [corpus]           (Einzelfrage)
//        spotlight-qa --serve [corpus]             (Modell RESIDENT; Fragen
//          zeilenweise via stdin, je Antwort EINE Zeile "@@RESULT@@{json}")
import Foundation
import CoreSpotlight
import FoundationModels
import CoreAILanguageModels
import HuggingFace
import MLXFoundationModels
import MLXHuggingFace
import MLXLLM
import MLXLMCommon
import Tokenizers

@available(macOS 27.0, *)
func makeModel(_ spec: String) async throws -> any FoundationModels.LanguageModel {
    if spec.hasPrefix("coreai:") {
        let path = String(spec.dropFirst("coreai:".count))
        let url = URL(fileURLWithPath: NSString(string: path).expandingTildeInPath,
                      isDirectory: true)
        return try await CoreAILanguageModel(resourcesAt: url)
    }
    if spec.hasPrefix("mlx:") {
        let id = String(spec.dropFirst("mlx:".count))
        let cfg = ModelConfiguration(id: id)
        return MLXLanguageModel(
            configuration: cfg,
            capabilities: [],
            weightsLocation: { rid in
                let cache = HubCache.default
                guard let repo = Repo.ID(rawValue: rid) else { return cache.cacheDirectory }
                if let commit = cache.resolveRevision(repo: repo, kind: .model, ref: "main"),
                   let snapshot = try? cache.snapshotPath(repo: repo, kind: .model, commitHash: commit) {
                    return snapshot
                }
                return cache.repoDirectory(repo: repo, kind: .model)
            },
            load: { configuration, progressHandler in
                try await loadModelContainer(
                    from: #hubDownloader(),
                    using: #huggingFaceTokenizerLoader(),
                    configuration: configuration,
                    progressHandler: progressHandler)
            })
    }
    return SystemLanguageModel(guardrails: .permissiveContentTransformations)
}

@main
struct SpotlightQA {
    static func main() async {
        guard #available(macOS 27.0, *) else {
            print("{\"error\": \"braucht macOS 27\"}"); exit(3)
        }
        await run()
    }

    @available(macOS 27.0, *)
    static func run() async {
        let args = CommandLine.arguments
        guard args.count >= 2 else {
            FileHandle.standardError.write("usage: [MODEL=…] spotlight-qa \"<frage>\"|--serve [corpus-dir]\n".data(using: .utf8)!)
            exit(2)
        }
        let serve = (args[1] == "--serve")
        let corpus = args.count >= 3 ? args[2]
            : NSString(string: "~/spotlight-eval/corpus").expandingTildeInPath
        let modelSpec = ProcessInfo.processInfo.environment["MODEL"] ?? "afm"

        let model: any FoundationModels.LanguageModel
        do {
            model = try await makeModel(modelSpec)
        } catch {
            print("{\"error\": \"model-load: \(String(describing: error).replacingOccurrences(of: "\"", with: "'"))\"}")
            exit(1)
        }

        // Companion-Map einmal bauen (serve: bleibt über alle Fragen stehen).
        var mdByBase: [String: String] = [:]
        if let en = FileManager.default.enumerator(atPath: corpus) {
            var mdRels: [String] = []
            while let rel = en.nextObject() as? String {
                if rel.hasSuffix(".md") { mdRels.append(rel) }
            }
            for rel in mdRels {
                let base = (rel as NSString).lastPathComponent
                    .replacingOccurrences(of: ".md", with: "")
                mdByBase[base] = corpus + "/" + rel
            }
        }

        if serve {
            FileHandle.standardError.write("bereit (\(modelSpec))\n".data(using: .utf8)!)
            while let line = readLine(strippingNewline: true) {
                let q = line.trimmingCharacters(in: .whitespaces)
                if q.isEmpty { continue }
                if q == "@@QUIT@@" { break }
                let out = await answerOne(question: q, corpus: corpus,
                                          model: model, mdByBase: mdByBase)
                emitResult(out, marker: true)
            }
        } else {
            let out = await answerOne(question: args[1], corpus: corpus,
                                      model: model, mdByBase: mdByBase)
            emitResult(out, marker: false)
        }
    }

    @available(macOS 27.0, *)
    static func emitResult(_ out: [String: Any], marker: Bool) {
        let opts: JSONSerialization.WritingOptions = marker ? [] : [.prettyPrinted]
        if let data = try? JSONSerialization.data(withJSONObject: out, options: opts),
           let s = String(data: data, encoding: .utf8) {
            print((marker ? "@@RESULT@@" : "") + s)
        } else {
            print((marker ? "@@RESULT@@" : "") + "{\"error\": \"json serialization failed\"}")
        }
        fflush(stdout)
    }

    @available(macOS 27.0, *)
    static func answerOne(question: String, corpus: String,
                          model: any FoundationModels.LanguageModel,
                          mdByBase: [String: String]) async -> [String: Any] {
        // ── Stufe 1: Discovery via natives SpotlightSearchTool ──────────────
        var fileSource = FileSource(fetchAttributes: [.title, .path, .contentURL, .displayName])
        fileSource.scopes = [URL(fileURLWithPath: corpus, isDirectory: true)]
        // Beta-Befund: scopes wird NICHT respektiert → harter Korpus-Filter
        // unten über die Companion-Map; 8 Slots gegen Junk-Verdrängung.
        fileSource.maximumResultCount = 8
        var config = SpotlightSearchTool.Configuration(sources: [.files(fileSource)])
        config.guide = SpotlightSearchTool.Guide(
            level: .focused(.documents), format: .compact)
        let tool = SpotlightSearchTool(configuration: config)

        let searchSession = LanguageModelSession(model: model, tools: [tool], instructions: """
            Finde die für die Frage relevanten Dokumente. Du MUSST das \
            Spotlight-Suchwerkzeug aufrufen. Suche mit 1-2 Kernbegriffen (alle \
            Begriffe müssen im Dokument vorkommen — weniger ist mehr); bei 0 \
            Treffern suche SOFORT erneut mit nur EINEM Begriff. Antworte danach \
            nur mit den gefundenen Dateinamen.
            """)

        final class ReplyBox: @unchecked Sendable { var paths: [String] = [] }
        let box = ReplyBox()
        let collector = Task {
            for await reply in tool.searchResults {
                var items: [CoreSpotlight.SearchableItem] = []
                switch reply.content {
                case .items(let arr): items = arr
                case .scoredItems(let arr): items = arr.map { $0.item }
                default: break
                }
                for si in items {
                    // path/contentURL sind im Datei-Index-Reply nil (gemessen)
                    // — displayName trägt den Dateinamen.
                    if let d = si.item.attributeSet.displayName { box.paths.append(d) }
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

        // Deterministische Zweitquelle: mdfind -onlyin corpus pro Frage-Begriff
        // (scopes-Bug + UND-Verknüpfung der Tool-Keywords).
        let stopEarly: Set<String> = ["welche", "werden", "müssen", "wie", "wird",
                                      "sind", "eine", "einen", "gilt", "oft", "wer",
                                      "was", "ist", "von", "bei", "für", "und",
                                      "oder", "nicht", "auch", "wurde", "durch"]
        let qTerms = question.lowercased()
            .components(separatedBy: CharacterSet.alphanumerics.inverted)
            .filter { $0.count > 3 && !stopEarly.contains($0) }
        var hitCount: [String: Int] = [:]
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
                guard !p.contains("/.brain-extracted/") else { continue }
                hitCount[p, default: 0] += 1
            }
        }
        let mdfindRanked = hitCount.sorted { ($0.value, $0.key) > ($1.value, $1.key) }
            .map { ($0.key as NSString).lastPathComponent }

        // Kandidaten-Union, HART auf den Korpus gefiltert (Companion-Map).
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

        // Kontext-Fenster: Absätze nach Frage-Begriffen ranken, je Doc kappen.
        func windows(_ text: String, budget: Int) -> String {
            let paras = text.components(separatedBy: "\n\n")
            let scored = paras.enumerated().map { (i, p) -> (Int, Int, String) in
                let lp = p.lowercased()
                let s = qTerms.reduce(0) { $0 + (lp.contains($1) ? 1 : 0) }
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

        if ProcessInfo.processInfo.environment["RETRIEVE_ONLY"] == "1" {
            return [
                "docs": docTexts.map { $0.name },
                "context": context,
                "tool_calls": toolCalls,
                "duration_s": Double(Int(Date().timeIntervalSince(t0) * 10)) / 10,
            ]
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
            "duration_s": Double(Int(dur * 10)) / 10,
        ]
        if let e = errorText { out["error"] = e }
        return out
    }
}
